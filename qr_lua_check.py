"""qr_lua_check.py - offline parse check for the reload chunks.

Imports the live chunk module the same way `ingame/reload.py` does, then parses every chunk it
composes. Uses the lexer/parser directly with a collecting error listener so a failure reports
line:column, instead of `luaparser.ast.parse`, which bails with `syntax errors: ...` and no detail.

  python qr_lua_check.py
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "abandoned"))
sys.path.insert(0, BASE)

# A `__NAME__` left behind in a composed chunk. It is VALID Lua - an ordinary identifier - so a
# chunk that still contains one parses happily and then reads a global that does not exist, which
# is nil. See the note in check().
PLACEHOLDER = re.compile(r"\b__[A-Z][A-Z0-9_]*__\b")

from antlr4 import CommonTokenStream, InputStream, Token  # noqa: E402
from antlr4.error.ErrorListener import ErrorListener  # noqa: E402
from luaparser import ast  # noqa: E402
from luaparser.ast import LuaLexer, LuaParser  # noqa: E402

# The base class of every AST node, used to walk children without descending into Tokens.
NODE = ast.Node


# ---------------------------------------------------------------------------
# THE LOCAL-VARIABLE BUDGET, which is a PER-FUNCTION limit of 200 in Lua 5.1 and is counted the way
# the compiler counts it: EVERY declaration in the function, not the locals active at one time.
#
# A `do ... end` block releases its registers at the end of the block, but the names it declared
# still count against the function they are in - so wrapping a section in `do ... end` buys nothing.
# This is how the whole install broke (2026-09-23): 146 top-level declarations plus every do-block
# local in all 16 sections, which does not fail until the game tries to load the chunk:
#
#     luaL_loadstring failed: function at line 1 has more than 200 local variables
#
# A function body is a budget of its own, which is why every feature is composed inside one
# (`core._feature_fn`). This check exists so the next feature cannot spend the budget silently.
LOCAL_LIMIT = 200        # Lua 5.1's MAXVARS
LOCAL_HEADROOM = 25      # fail this far below the limit rather than exactly at it
FUNC_NODE_NAMES = ("Function", "AnonymousFunction", "Method", "LocalFunction")


def _children(node):
    """The child AST NODES of one node.

    FILTERED TO `ast.Node` ON PURPOSE, and it is not a micro-optimisation: a generic walk over
    every attribute with a `__dict__` also descends into the Tokens every node carries, which took
    the check from under a second to 35 on the composed install chunk (measured) - and the counts
    are the same either way, because a Token is never a declaration. `Token` is not a `Node`
    subclass, so this filters exactly the right things out.
    """
    for _name, val in vars(node).items():
        if isinstance(val, (list, tuple)):
            for item in val:
                if isinstance(item, NODE):
                    yield item
        elif isinstance(val, NODE):
            yield val


def local_budgets(text):
    """[(label, count)] for the chunk and every function inside it.

    ITERATIVE, WITH A VISITED SET, and both are needed: the AST nodes carry back-references, so a
    naive recursive walk recurses until Python's stack gives out (measured: RecursionError on any
    real chunk). The visited set also stops a shared node being counted twice.
    """
    tree = ast.parse(text)
    out = []
    seen = set()

    def is_func(n):
        return type(n).__name__ in FUNC_NODE_NAMES

    def label_of(fn):
        return getattr(getattr(fn, "name", None), "id", None) or "anonymous"

    chunk = [0]
    out.append(("<chunk>", chunk))
    work = [(tree, chunk)]                     # each entry is one function's budget
    while work:
        node, counter = work.pop()
        stack = [node]
        while stack:
            cur = stack.pop()
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            kind = type(cur).__name__
            if kind == "LocalAssign":
                counter[0] += len(cur.targets)
            elif kind == "LocalFunction":
                counter[0] += 1
            elif kind == "ForNum":
                counter[0] += 1
            elif kind == "ForIn":
                counter[0] += len(cur.targets)
            for kid in _children(cur):
                if is_func(kid):
                    if id(kid) in seen:
                        continue
                    seen.add(id(kid))
                    # `local function f` DECLARES A NAME IN THE ENCLOSING FUNCTION as well as
                    # starting one of its own - and since the function node is queued rather than
                    # pushed, this is the only place that name can be counted. Missing it made the
                    # chunk read 12 instead of 29, i.e. the check would have passed a chunk that
                    # was over. (`function a.b()` declares nothing local, so it is not counted.)
                    if type(kid).__name__ == "LocalFunction":
                        counter[0] += 1
                    sub = [len(getattr(kid, "args", None) or [])]
                    out.append((label_of(kid), sub))
                    # The BODY is queued, not the function node: the node itself is marked seen
                    # here, so a budget that walked the node would skip it and measure nothing.
                    body = getattr(kid, "body", None)
                    if body is not None:
                        work.append((body, sub))
                else:
                    stack.append(kid)
    return [(label, c[0]) for label, c in out]


def compose_shared(core):
    """The part of every composed chunk that is NOT a feature: the shared preamble, the host and
    the driver. Its local budget is the chunk's own, because each feature is a function."""
    return core.LUA + "\n" + core.HOST + "\n" + core.DRIVER


def _declared_names(line):
    """How many local variables one `local ...` line declares."""
    body = line[len("local "):]
    if body.startswith("function"):
        return 1
    body = body.split("=")[0].strip()
    if not body:
        return 0
    return len([p for p in body.split(",") if p.strip()])


def check_shape(features, cfg, text):
    """The composition, checked WITHOUT parsing - the one thing the piece-wise budget check cannot
    see.

    The pieces add up to the composed chunk's budget ONLY while every feature is inside its own
    function (core._feature_fn). If a future edit puts a feature's Lua back at the chunk level, each
    piece would still measure small while the real chunk went over 200 - so this counts the chunk's
    OWN declarations directly, by tracking the wrapper's delimiters.

    That works without a parser because core emits a marker comment around every feature block
    (`hud:feature:begin` / `hud:feature:end`), so anything OUTSIDE those markers is the chunk level
    by construction. The wrapper's own `(function()` / `end)()` lines cannot be used for this - the
    first version tried, and several features' summary expressions are themselves
    `(function() ... end)()`, which threw the nesting off by two.
    """
    inside, top, functions = False, 0, 0
    for line in text.split("\n"):
        if line.startswith("-- hud:feature:begin"):
            inside, functions = True, functions + 1
            continue
        if line.startswith("-- hud:feature:end"):
            inside = False
            continue
        if not inside and line.startswith("local "):
            top += _declared_names(line)

    enabled = sum(1 for f in features if cfg[f.NAME]["enabled"])
    limit = 70
    problems = []
    if top > limit:
        problems.append(
            "  %d local(s) at the chunk level of the composed chunk (over %d): a feature's Lua is\n"
            "  probably no longer inside its own function (see core._feature_fn)" % (top, limit))
    if functions < enabled:
        problems.append(
            "  only %d feature block(s) for %d enabled feature(s)" % (functions, enabled))
    if problems:
        print("FAIL  %-18s (%d chars)" % ("composed shape", len(text)))
        for line in problems:
            print(line)
        return False
    print("ok    %-18s (%d chars, %d local(s) at chunk level, %d feature block(s))"
          % ("composed shape", len(text), top, functions))
    return True


def _lua_quote(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def ingame_check(chunks):
    """THE AUTHORITATIVE CHECK: ask the GAME's own Lua 5.1 to `loadstring` the chunk.

    WHY THIS EXISTS, and why the parse checks are not enough. `luaparser` is a 5.x grammar and it
    ACCEPTED a wrapper (`(function() ... end)()` at statement start) that the game's Lua 5.1 refuses
    with `ambiguous syntax (function call x new statement) near '('` - so an offline parse can pass a
    chunk the game will not load, and the failure arrives as "every feature is off". This same call
    also enforces the 200-local limit, since that is raised while the chunk is being compiled.

    The chunk travels as a FILE rather than as text inside the eval, because the install chunk is
    ~390 KB and the game can read an absolute path (`io.open` on one works - reload-probe.txt uses
    it). Nothing is installed by this: `loadstring` compiles and throws the result away.
    """
    import tempfile

    sys.path.insert(0, BASE)
    from coromon_lua import Bridge  # noqa: E402

    good = True
    try:
        bridge = Bridge("coromon.exe")
    except Exception as exc:  # noqa: BLE001
        print("SKIP  --ingame (%r)" % (exc,))
        return True
    try:
        for name, text in chunks:
            path = os.path.join(tempfile.gettempdir(), "hudchk_%s.lua" % name)
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            code = (
                "local fh = io.open(%s, 'rb')\n"
                "if not fh then return 'cannot open the temp file' end\n"
                "local text = fh:read('*a')\n"
                "fh:close()\n"
                "if type(text) ~= 'string' then return 'read nothing' end\n"
                "local f, e = loadstring(text, '@%s')\n"
                "if f then return 'ok' end\n"
                "return tostring(e)\n" % (_lua_quote(path), name)
            )
            result = bridge.eval(code, timeout=180.0) or {}
            out = (result.get("out") or result.get("err") or "no answer")
            ok = out.strip() == "ok"
            good &= ok
            print("%s  %-14s %s" % ("ok  " if ok else "FAIL", name, "" if ok else out.strip()))
    finally:
        try:
            bridge.detach()
        except Exception:  # noqa: BLE001
            pass
    return good


def check(name, text):
    errs = []

    class Collect(ErrorListener):
        def syntaxError(self, recognizer, offending, line, column, msg, e):
            errs.append("  line %d:%d  %s" % (line, column + 1, msg))

    lex = LuaLexer(InputStream(text))
    lex.removeErrorListeners()
    lex.addErrorListener(Collect())
    stream = CommonTokenStream(lex, channel=Token.DEFAULT_CHANNEL)
    parser = LuaParser(stream)
    parser.removeErrorListeners()
    parser.addErrorListener(Collect())
    try:
        parser.start_()
    except Exception as exc:  # noqa: BLE001 - report anything the parser throws
        errs.append("  threw: %r" % (exc,))

    # UNSUBSTITUTED PLACEHOLDERS, and this is the check that was missing. `__WALK__` is a perfectly
    # legal Lua identifier, so a chunk that still contains one PARSES - and then reads a global that
    # does not exist, which is nil. autoroll shipped with `local WALK_AFTER_HIT = __WALK__` for
    # exactly that reason: the substitution was never added, the parse check passed, and the flag
    # silently became falsy. Nothing but reading the composed text can catch this.
    leftover = sorted(set(PLACEHOLDER.findall(text)))
    if leftover:
        errs.append("  unsubstituted placeholder(s): %s" % ", ".join(leftover))

    # The per-function local budget, see above. Reported on the ok line as well, because a chunk
    # that is quietly at 190 has to be visible before it is at 201.
    worst = ("?", 0)
    if not errs:
        try:
            budgets = local_budgets(text)
        except Exception as exc:  # noqa: BLE001 - the parse above already succeeded
            errs.append("  local budget check threw: %r" % (exc,))
        else:
            if budgets:
                worst = max(budgets, key=lambda kv: kv[1])
            over = [(lab, n) for lab, n in budgets if n > LOCAL_LIMIT - LOCAL_HEADROOM]
            if over:
                over.sort(key=lambda kv: -kv[1])
                errs.append(
                    "  local budget over %d in: %s" % (
                        LOCAL_LIMIT - LOCAL_HEADROOM,
                        ", ".join("%s=%d" % kv for kv in over[:6])))

    if errs:
        print("FAIL  %-18s (%d chars)" % (name, len(text)))
        for line in errs[:12]:
            print(line)
        return False
    print("ok    %-18s (%d chars, max locals %d in %s)"
          % (name, len(text), worst[1], worst[0]))
    return True


def main():
    import quick_reload as qr

    print("flags: MENU_CLOSE_VIA_GAME=%s MENU_ROUND_TRIP=%s USE_ROUND_TRIP=%s "
          "CANCEL_MENU_ENTRANCE=%s"
          % (qr.MENU_CLOSE_VIA_GAME, qr.MENU_ROUND_TRIP, qr.USE_ROUND_TRIP,
             qr.CANCEL_MENU_ENTRANCE))
    print()

    good = True
    for name in ("PREFLIGHT", "TRANSITION_CANCEL", "TEARDOWN_BASE", "TEARDOWN_MENU",
                 "TEARDOWN_GUARD", "TEARDOWN", "RELOAD_BODY", "BODY"):
        good &= check(name, getattr(qr, name))

    # The other route, composed by hand so BOTH stay checkable whichever way the flags are set: the
    # round trip with the title screen, which is what `MENU_CLOSE_VIA_GAME = False` falls back to.
    good &= check("BODY (round trip)",
                  "_G.__qrMenuRoundTrip = true\n_G.__qrCancelEntrance = true\n"
                  + qr.TEARDOWN_BASE + qr.TEARDOWN_MENU + qr.RELOAD_BODY)

    # The feature's own install chunk. It is a much larger Lua string than anything above and it is
    # where the trigger, the watcher and the teardown pipeline live, so it needs the same check.
    try:
        sys.path.insert(0, os.path.join(BASE, "ingame"))
        import reload as rl
    except Exception as exc:  # noqa: BLE001
        print("SKIP  reload.py (%r)" % (exc,))
    else:
        for name in ("lua", "section"):
            fn = getattr(rl, name, None)
            if fn is None:
                continue
            # `section` reads the feature settings (enabled/key), so it needs a cfg shaped like the
            # one overlays.py would hand it; the plain {} is enough for the others.
            text = None
            for cfg in ({"enabled": True, "key": ["rightJoystickButton"]}, {}):
                try:
                    text = fn(cfg)
                    break
                except Exception as exc:  # noqa: BLE001
                    last = exc
            if text is None:
                print("SKIP  reload.%-9s (needs a cfg: %r)" % (name, last))
                continue
            if isinstance(text, str):
                good &= check("reload.%s" % name, text)
            else:
                print("SKIP  reload.%-9s (returned %s)" % (name, type(text).__name__))

    # EVERY FEATURE MODULE, not just reload. Each one has SETTINGS (key, default, comment) and a
    # section(cfg) that returns the Lua it would install, so the default cfg is derivable and the
    # section is checkable without the game. This is the check that would have caught a syntax slip
    # in any of the per-frame features, which are the ones a mistake here costs the most.
    import glob
    import importlib

    for path in sorted(glob.glob(os.path.join(BASE, "ingame", "*.py"))):
        mod = os.path.basename(path)[:-3]
        if mod.startswith("_") or mod in ("core", "config"):
            continue
        label = "ingame." + mod
        try:
            module = importlib.import_module("ingame." + mod)
        except Exception as exc:  # noqa: BLE001
            print("SKIP  %-18s (import: %r)" % (label, exc))
            continue
        settings = getattr(module, "SETTINGS", None)
        section = getattr(module, "section", None)
        if not settings or not callable(section):
            continue
        base_cfg = {entry[0]: entry[1] for entry in settings}
        # A feature that SHIPS SWITCHED OFF still has to compile - it is installed the moment
        # someone ticks it, and a `section()` that returns "" for the default would otherwise never
        # be checked at all. So the default is tried first, then the same cfg with enabled forced.
        candidates = [base_cfg]
        if base_cfg.get("enabled") is False:
            forced = dict(base_cfg)
            forced["enabled"] = True
            candidates.append(forced)
        text = None
        for cand in candidates:
            try:
                text = section(cand)
            except Exception as exc:  # noqa: BLE001
                print("SKIP  %-18s (cfg: %r)" % (label, exc))
                text = None
                break
            if text:
                break
        if isinstance(text, str) and text:
            good &= check(label, text)
        else:
            print("skip  %-18s (nothing to install)" % label)

    # THE COMPOSED CHUNKS. Parsing one of these is SLOW - antlr takes ~35 s on the 386 KB install
    # chunk (measured), which is far too slow for a check to run after every edit - so they are not
    # parsed by default. What the budget really is comes out of the PIECES instead, which are
    # checked above and below and parse in a fraction of a second each:
    #
    #     chunk budget  =  LUA + HOST + DRIVER          (the shared preamble, checked below)
    #     feature budget =  that feature's lua()+section (checked above, one per feature)
    #
    # and they add up because every feature is composed inside its own function. So the default run
    # measures the same numbers the composed chunk would, and only the COMPOSITION itself is left
    # unverified - which the shape smoke test below covers without parsing anything. `--composed`
    # does the real thing when it is worth the minute.
    from ingame import FEATURES, core  # noqa: E402 - imported here so the module works standalone
    cfg = core.read_config(FEATURES, warn=lambda m: None)
    if cfg is None:
        print("SKIP  composed chunks (overlays.toml not readable)")
        good = False
    else:
        good &= check("shared preamble", compose_shared(core))
        good &= check_shape(FEATURES, cfg, core.install_code(FEATURES, cfg))
        if "--composed" in sys.argv[1:]:
            print("      --composed: parsing the three composed chunks, this takes ~1 min")
            good &= check("install_code", core.install_code(FEATURES, cfg))
            good &= check("status_code", core.status_code(FEATURES, cfg))
            good &= check("report_code", core.report_code(FEATURES, cfg))
        if "--ingame" in sys.argv[1:]:
            print("      --ingame: asking the game's own Lua 5.1 to loadstring them")
            good &= ingame_check([
                ("install_code", core.install_code(FEATURES, cfg)),
                ("status_code", core.status_code(FEATURES, cfg)),
                ("report_code", core.report_code(FEATURES, cfg)),
            ])

    print()
    print("all chunks parse" if good else "SOMETHING DID NOT PARSE")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
