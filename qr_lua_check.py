"""qr_lua_check.py - offline parse check for the reload chunks.

Imports the live chunk module the same way `ingame/reload.py` does, then parses every chunk it
composes. Uses the lexer/parser directly with a collecting error listener so a failure reports
line:column, instead of `luaparser.ast.parse`, which bails with `syntax errors: ...` and no detail.

  python qr_lua_check.py
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "abandoned"))
sys.path.insert(0, BASE)

from antlr4 import CommonTokenStream, InputStream, Token  # noqa: E402
from antlr4.error.ErrorListener import ErrorListener  # noqa: E402
from luaparser.ast import LuaLexer, LuaParser  # noqa: E402


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

    if errs:
        print("FAIL  %-18s (%d chars)" % (name, len(text)))
        for line in errs[:12]:
            print(line)
        return False
    print("ok    %-18s (%d chars)" % (name, len(text)))
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
        try:
            # Entries are (key, default, comment) in most modules and carry a fourth field in
            # others, so index rather than unpack.
            text = section({entry[0]: entry[1] for entry in settings})
        except Exception as exc:  # noqa: BLE001
            print("SKIP  %-18s (cfg: %r)" % (label, exc))
            continue
        if isinstance(text, str) and text:
            good &= check(label, text)
        else:
            print("skip  %-18s (nothing to install)" % label)

    print()
    print("all chunks parse" if good else "SOMETHING DID NOT PARSE")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
