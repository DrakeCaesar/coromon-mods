#!/usr/bin/env python3
"""
qr_luadata.py - run a Coromon `.lu` DATA module and print the table it returns.

WHY THIS IS HERE
----------------
The game's generated data modules are plain bytecode that builds one big table:
`fonts.outline_10_bold_00.lu` is the image sheet for the outline_10_bold font - a glyph name,
its rect in the atlas and its advance
(`fonts.glyphIdByCharacter_pixelArt.lu` is the same idea for the id lookup).

Reading those tables matters because they are ASSET METADATA, not code: the glyph rectangles
cannot be recovered from the atlas PNG (the packing is irregular), and a name like
`Kroeger_10_bold_0x1a0` does not say where on the sheet that glyph lives. Coromon's own UI gets
this data by `require`-ing the module, so the only faithful way to get it outside the game is to
run the same chunk.

There is no Lua on this machine and the game must not be needed to read its own assets, so this
is a small Lua 5.1 interpreter: enough of the VM to execute generated data modules (which are
straight-line table construction, plus the occasional `table.insert` loop).

Usage:
  python qr_luadata.py <file.lu>                  summary of the returned table
  python qr_luadata.py <file.lu> --json out.json  dump it as JSON
  python qr_luadata.py <file.lu> --at frames      summarise one key (repeatable)
  python qr_luadata.py <file.lu> --lua            print it as a Lua literal
"""
import argparse
import json
import sys

import luadis

# ---------------------------------------------------------------- values


class Table(dict):
    """A Lua table. Dict keys are used as-is; the array part is normal 1..n ints.

    `mark` is Lua's array-part length indicator, which SETLIST needs when its C operand is 0
    ("append after the last element"). Keeping it explicit is what makes `start=0` correct.
    """

    __slots__ = ("mark",)

    def __init__(self):
        super().__init__()
        self.mark = 0


class StubResult(Table):
    """What a stubbed engine call returns.

    Callable on purpose: in this game a colour is not data but a FUNCTION (`colors.TYPE_NORMAL`
    is called to get the colour, and `Color.darkenedRgbFloat(0.5, colours.TYPE_NORMAL())` takes
    one), so a stub that returns a plain table dies with "attempt to call a non-function".
    Also answers any missing key with a fresh recorder, so a stub handles helpers that were not
    enumerated up front (Color.rgbTable, .withAlpha, ... - the colour class alone has a dozen).
    """

    def __missing__(self, key):
        def recorder(*args, _key=key):
            out = StubResult()
            out["fn"] = _key
            out["args"] = list(args)
            return out
        return recorder

    def __call__(self, *args):
        out = StubResult()
        out["call"] = self.get("call", self.get("fn"))
        out["args"] = list(args)
        return out


def truthy(v):
    return v is not None and v is not False


def norm(key):
    if isinstance(key, float) and key.is_integer():
        return int(key)
    return key


class StubValue(Table):
    """A stand-in for a value only the engine can build (a `Color.rgb(...)` result).

    It RECORDS the call that produced it (`__call__`/`args`), so a style table's colours stay
    readable, and it is itself callable: some modules take a colour value and call it later,
    which would otherwise stop the module right before the table we want.
    """

    def __call__(self, *args):
        rec = StubValue()
        rec["__call__"] = "?"
        rec["args"] = list(args)
        return rec


def to_python(v):
    if isinstance(v, Table):
        keys = list(v.keys())
        if keys and all(isinstance(k, int) and k >= 1 for k in keys):
            keys.sort()
            if keys == list(range(1, len(keys) + 1)):
                return [to_python(v[k]) for k in keys]
        return {str(k): to_python(v[k]) for k in sorted(v, key=lambda k: str(k))}
    return v


# ---------------------------------------------------------------- stdlib
# Only what generated data modules touch. Anything else raises, which is the point: a missing
# function shows up as an error naming it, not as silently wrong data.


class LuaError(Exception):
    pass


# Lua 5.1 flushes a table constructor's array part in blocks of this many fields, which is
# what SETLIST's C operand counts.
LFIELDS_PER_FLUSH = 50


def lua_tonumber(v):
    if isinstance(v, bool) or v is None:
        raise LuaError("attempt to perform arithmetic on a non-number")
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            raise LuaError("attempt to perform arithmetic on a string")
    return v


def _t_insert(t, *rest):
    if len(rest) == 1:
        t[t.mark + 1] = rest[0]
        t.mark += 1
    elif len(rest) == 2:
        pos, value = int(rest[0]), rest[1]
        n = t.mark
        for i in range(n, pos - 1, -1):
            t[i + 1] = t.get(i)
        t[pos] = value
        t.mark = n + 1
    else:
        raise LuaError("wrong number of arguments to 'insert'")
    return None


def _t_remove(t, pos=None):
    n = t.mark
    if pos is None:
        pos = n
    pos = int(pos)
    if n == 0 or pos > n:
        return None
    value = t.get(pos)
    for i in range(pos, n):
        t[i] = t.get(i + 1)
    t.pop(n, None)
    t.mark = n - 1
    return value


def _t_concat(t, sep="", i=1, j=None):
    j = t.mark if j is None else int(j)
    return str(sep).join("" if t.get(k) is None else str(t[k]) for k in range(int(i), j + 1))


def _string_format(fmt, *args):
    out, ai = [], 0
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        j = i + 1
        while j < len(fmt) and fmt[j] in "-+ #0123456789.":
            j += 1
        if j >= len(fmt):
            raise LuaError("invalid format")
        spec = fmt[i:j + 1]
        val = args[ai]
        ai += 1
        conv = spec[-1]
        if conv in "diouxX":
            out.append(spec % int(lua_tonumber(val)))
        elif conv in "eEfgG":
            out.append(spec % float(lua_tonumber(val)))
        elif conv == "s":
            out.append(spec % str(val))
        elif conv == "c":
            out.append(chr(int(lua_tonumber(val))))
        elif conv == "%":
            out.append("%")
            ai -= 1
        else:
            raise LuaError("invalid option '%" + conv + "' to 'format'")
        i = j + 1
    return "".join(out)


def _string_sub(s, i, j=-1):
    s = str(s)
    i, j = int(i), int(j)
    n = len(s)
    if i < 0:
        i = max(n + i + 1, 1)
    elif i == 0:
        i = 1
    if j < 0:
        j = n + j + 1
    elif j == 0:
        j = 0
    return s[i - 1:j] if i <= j else ""


def _string_rep(s, n, sep=""):
    return str(sep).join([str(s)] * int(n))


def _string_gsub(s, pat, repl, n=None):
    import re
    count = 0

    def sub(match):
        nonlocal count
        count += 1
        if callable(repl):
            return str(repl(match.group(0)))
        if isinstance(repl, Table):
            key = match.group(1) if match.groups() else match.group(0)
            v = repl.get(key, repl.get(norm(key)))
            return match.group(0) if v is None else str(v)
        return str(repl).replace("%0", match.group(0)).replace("%1", match.group(1) or "")

    # Lua patterns are not regexes; translate the few that appear in data modules.
    rx = pat.replace("%d", r"\d").replace("%s", r"\s").replace("%a", r"[A-Za-z]")
    rx = rx.replace("%%", "%").replace("(", r"(")
    out = re.sub(rx, sub, str(s), count=0 if n is None else int(n))
    return out, count


def _inputhelper(*_a):
    raise LuaError("inputHelper is game-only")


def _t_merge(dst, *srcs):
    for src in srcs:
        for k, v in src.items():
            if dst.get(k) is None:
                dst[k] = v
    return dst


def _reverse_key_value(t):
    out = Table()
    for k, v in t.items():
        out[v] = k
    return out


def _pairs(t):
    keys = sorted(t.keys(), key=lambda k: (isinstance(k, str), k))
    it = iter(keys)
    last = [None]

    def step(_, _k):
        k = next(it, None)
        if k is None:
            return None
        last[0] = k
        return [k, t[k]]

    return step, t, None


def _ipairs(t):
    n = 0

    def step(_, i):
        nonlocal n
        n = (i or 0) + 1
        v = t.get(n)
        if v is None:
            return None
        return [n, v]

    return step, t, 0


def make_sandbox(requires=None, globals_values=None):
    """requires: {module name: path to its extracted .lu} - resolved by require().

    Game modules pull in siblings (`require('classes.constants.colors')`), and a style table is
    only readable once those resolve, so a module can be run with its dependencies wired up.
    globals_values: {global name: already-computed value} for names the engine injects before
    requiring anything (`Enum`, set up in the game's boot script, is one - without it
    UIContainerStyle will not even load). Values are resolved by the caller, never here: doing it
    here makes the nested sandbox inject the same global again and recurse forever.
    """
    requires = requires or {}
    globals_values = globals_values or {}
    cache = {}

    def _require(name):
        if name in cache:
            return cache[name]
        target = requires.get(name)
        if target is None:
            raise LuaError("require('%s') - add --require %s=<file.lu>" % (name, name))
        value = run_module(target, requires, globals_values)[0]
        cache[name] = value
        return value

    import math as _math
    math_t = Table()
    for name in ("floor", "ceil", "fabs", "sqrt", "pi"):
        math_t[name] = getattr(_math, name)
    math_t["huge"] = float("inf")
    math_t["abs"] = abs
    math_t["min"] = min
    math_t["max"] = max
    math_t["pow"] = lambda a, b: float(a) ** float(b)
    math_t["random"] = lambda *a: 0.5
    math_t["randomseed"] = lambda *a: None

    string_t = Table()
    string_t["format"] = _string_format
    string_t["sub"] = _string_sub
    string_t["rep"] = _string_rep
    string_t["gsub"] = _string_gsub
    string_t["lower"] = lambda s: str(s).lower()
    string_t["upper"] = lambda s: str(s).upper()
    string_t["len"] = lambda s: len(str(s))
    string_t["char"] = lambda *a: "".join(chr(int(x)) for x in a)
    string_t["byte"] = lambda s, i=1: ord(str(s)[int(i) - 1]) if str(s) else None
    string_t["find"] = lambda s, p, *_a: (str(s).find(str(p)) + 1 or None)
    string_t["match"] = lambda s, p: None

    table_t = Table()
    table_t["insert"] = _t_insert
    table_t["remove"] = _t_remove
    table_t["concat"] = _t_concat
    table_t["merge"] = _t_merge
    table_t["sort"] = lambda t, *a: sorted(t.keys())
    table_t["reverseKeyValue"] = _reverse_key_value

    g = Table()
    g["table"] = table_t
    g["string"] = string_t
    g["math"] = math_t
    g["pairs"] = _pairs
    g["ipairs"] = _ipairs
    g["next"] = lambda t, k=None: None
    g["tostring"] = lambda v: "nil" if v is None else str(v)
    g["tonumber"] = lambda v: lua_tonumber(v)
    g["type"] = lambda v: ("nil" if v is None else "boolean" if isinstance(v, bool)
                           else "number" if isinstance(v, (int, float))
                           else "string" if isinstance(v, str) else "table")
    g["print"] = lambda *a: sys.stderr.write(" ".join(str(x) for x in a) + "\n")
    g["pcall"] = lambda fn, *a: [True] + ([fn(*a)] if True else [])
    g["error"] = lambda *a: (_ for _ in ()).throw(LuaError(str(a[0]) if a else "error"))
    g["unpack"] = lambda t, i=1, j=None: [t[k] for k in range(int(i),
                                                              (t.mark if j is None else int(j)) + 1)]
    g["select"] = lambda n, *a: len(a) if n == "#" else list(a)[int(n) - 1:]
    g["setmetatable"] = lambda t, mt: t
    g["rawget"] = lambda t, k: t.get(k)
    g["rawset"] = lambda t, k, v: t.__setitem__(k, v)
    g["require"] = _require
    g["requireOnce"] = _require
    for name, val in globals_values.items():
        g[name] = val
    g["_G"] = g
    return g


# ---------------------------------------------------------------- VM

ARITH = {12: lambda a, b: a + b, 13: lambda a, b: a - b, 14: lambda a, b: a * b,
         15: lambda a, b: a / b, 16: lambda a, b: a % b, 17: lambda a, b: a ** b}

OPNAMES = luadis.OPNAMES


class Frame:
    __slots__ = ("r", "pc", "proto", "upvals", "varargs")

    def __init__(self, proto, upvals, varargs=()):
        self.proto = proto
        self.r = [None] * (max(proto.maxstack, 8) + 8)
        self.pc = 0
        self.upvals = upvals
        self.varargs = list(varargs)


def execute(g, frame, depth=0):
    """Run one proto. Returns the list of results."""
    if depth > 60:
        raise LuaError("call depth exceeded")
    pr = frame.proto
    code = pr.code
    r = frame.r
    while frame.pc < len(code):
        ins = code[frame.pc]
        op = ins & 0x3F
        A = (ins >> 6) & 0xFF
        C = (ins >> 14) & 0x1FF
        B = (ins >> 23) & 0x1FF
        Bx = (ins >> 14) & 0x3FFFF
        sBx = Bx - 131071
        frame.pc += 1

        def rk_(x, A=A):
            return pr.k[x - 256] if x >= 256 else r[x]

        if op == 0:      # MOVE
            r[A] = r[B]
        elif op == 1:    # LOADK
            r[A] = pr.k[Bx]
        elif op == 2:    # LOADBOOL
            r[A] = bool(B)
            if C:
                frame.pc += 1
        elif op == 3:    # LOADNIL
            for i in range(A, A + B + 1):
                r[i] = None
        elif op == 4:    # GETUPVAL
            r[A] = frame.upvals[B][0]
        elif op == 5:    # GETGLOBAL
            name = pr.k[Bx]
            if name not in g:
                raise LuaError("undefined global '%s'" % name)
            r[A] = g[name]
        elif op == 6:    # GETTABLE
            t = rk_(B)
            k = norm(rk_(C))
            if t is None:
                raise LuaError("attempt to index a nil value")
            if isinstance(t, StubResult):
                r[A] = t[k]        # __missing__ gives a recorder for unknown helpers
            else:
                r[A] = t.get(k)
        elif op == 7:    # SETGLOBAL
            g[pr.k[Bx]] = r[A]
        elif op == 8:    # SETUPVAL
            frame.upvals[B][0] = r[A]
        elif op == 9:    # SETTABLE  R(A)[RK(B)] = RK(C)
            t = r[A]
            if t is None:
                raise LuaError("attempt to index a nil value")
            key = norm(rk_(B))
            t[key] = rk_(C)
            if isinstance(t, Table) and isinstance(key, int) and key > t.mark:
                t.mark = key
        elif op == 10:   # NEWTABLE
            r[A] = Table()
        elif op == 11:   # SELF
            t = rk_(B)
            r[A + 1] = t
            if isinstance(t, StubResult):
                r[A] = t[norm(rk_(C))]
            else:
                r[A] = t.get(norm(rk_(C)))
        elif op in ARITH:
            r[A] = ARITH[op](lua_tonumber(rk_(B)), lua_tonumber(rk_(C)))
        elif op == 18:   # UNM
            r[A] = -lua_tonumber(r[B])
        elif op == 19:   # NOT
            r[A] = not truthy(r[B])
        elif op == 20:   # LEN
            v = r[B]
            r[A] = v.mark if isinstance(v, Table) else len(str(v))
        elif op == 21:   # CONCAT
            r[A] = "".join("" if r[i] is None else str(r[i]) for i in range(B, C + 1))
        elif op == 22 and False:
            pass
        elif op == 22:   # JMP
            frame.pc += sBx
        elif op in (23, 24, 25):   # EQ / LT / LE
            a, b = rk_(B), rk_(C)
            if op == 23:
                ok = (a == b) if not (isinstance(a, Table) or isinstance(b, Table)) else (a is b)
            elif op == 24:
                ok = a < b
            else:
                ok = a <= b
            if ok != bool(A):
                frame.pc += 1
        elif op == 26:   # TEST
            if truthy(r[A]) != bool(C):
                frame.pc += 1
        elif op == 27:   # TESTSET
            if truthy(r[B]) == bool(C):
                r[A] = r[B]
            else:
                frame.pc += 1
        elif op == 28:   # CALL
            nargs = B - 1
            nres = C - 1
            fn = r[A]
            args = r[A + 1:A + 1 + (nargs if nargs >= 0 else len(r) - A - 1)]
            res = call(g, fn, args, frame, depth,
                       "proto %d-%d pc %d" % (pr.linedefined, pr.lastlinedefined, frame.pc - 1))
            if nres >= 0:
                for i in range(nres):
                    r[A + i] = res[i] if i < len(res) else None
            else:
                for i, v in enumerate(res):
                    r[A + i] = v
        elif op == 29:   # TAILCALL
            nargs = B - 1
            args = r[A + 1:A + 1 + nargs]
            return call(g, r[A], args, frame, depth)
        elif op == 30:   # RETURN
            n = B - 1
            if n < 0:
                n = len(r) - A
                while n > 0 and r[A + n - 1] is None:
                    n -= 1
            return r[A:A + n]
        elif op == 31:   # FORLOOP
            step = r[A + 2]
            idx = r[A] + step
            if (step > 0 and idx <= r[A + 1]) or (step <= 0 and idx >= r[A + 1]):
                r[A] = idx
                r[A + 3] = idx
                frame.pc += sBx
        elif op == 32:   # FORPREP
            r[A] = lua_tonumber(r[A]) - lua_tonumber(r[A + 2])
            frame.pc += sBx
        elif op == 33:   # TFORLOOP
            fn, state, ctl = r[A], r[A + 1], r[A + 2]
            res = call(g, fn, [state, ctl], frame, depth)
            if not res or res[0] is None:
                frame.pc += 1
            else:
                for i, v in enumerate(res[:C]):
                    r[A + 3 + i] = v
        elif op == 34:   # SETLIST  R(A)[(C-1)*50 + i] = R(A+i)
            t = r[A]
            n = B
            if n == 0:
                n = len(r) - A - 1
            # C is a BLOCK number, not an index: the array is flushed 50 slots at a time, so
            # C=8 means slots 351..(350+n). Writing at index C instead silently overlaps every
            # batch onto the first 50 slots - which is exactly how this module first came out
            # with 56 glyphs instead of the 375 it defines.
            start = 0 if C == 0 else (C - 1) * LFIELDS_PER_FLUSH
            if start == 0:
                start = t.mark
            for i in range(1, n + 1):
                t[start + i] = r[A + i]
            if start + n > t.mark:
                t.mark = start + n
        elif op == 35:   # CLOSE
            pass
        elif op == 36:   # CLOSURE
            child = pr.protos[Bx]
            upvals = []
            for _ in range(child.nups):
                pseudo = code[frame.pc]
                frame.pc += 1
                pop = pseudo & 0x3F
                pB = (pseudo >> 23) & 0x1FF
                if pop == 0:      # MOVE: parent register
                    cell = [frame.r[pB]]
                    upvals.append(cell)
                elif pop == 4:    # GETUPVAL: parent upvalue
                    upvals.append(frame.upvals[pB])
                else:
                    raise LuaError("bad CLOSURE pseudo-op %d" % pop)
            r[A] = Closure(child, upvals)
        elif op == 37:   # VARARG
            n = B - 1
            vals = frame.varargs
            if n < 0:
                n = len(vals)
            for i in range(n):
                r[A + i] = vals[i] if i < len(vals) else None
        else:
            raise LuaError("unhandled opcode %s (%d) at proto %d-%d pc %d" % (
                OPNAMES[op] if op < len(OPNAMES) else "?", op, pr.linedefined,
                pr.lastlinedefined, frame.pc - 1))
    return []


class Closure:
    def __init__(self, proto, upvals):
        self.proto = proto
        self.upvals = upvals


def call(g, fn, args, parent, depth, where=None):
    if isinstance(fn, Closure):
        frame = Frame(fn.proto, fn.upvals, args[fn.proto.numparams:] if fn.proto.is_vararg else ())
        for i in range(fn.proto.numparams):
            frame.r[i + 1] = args[i] if i < len(args) else None
        if fn.proto.is_vararg:
            frame.varargs = args[fn.proto.numparams:]
        return execute(g, frame, depth + 1)
    if callable(fn):
        out = fn(*args)
        return [] if out is None else (out if isinstance(out, list) else [out])
    where = ""
    if parent is not None:
        where = " (proto %d-%d, pc %d)" % (parent.proto.linedefined,
                                           parent.proto.lastlinedefined, parent.pc - 1)
    raise LuaError("attempt to call a non-function (%r)%s" % (fn, where))


def run_module(path, requires=None, globals_values=None, tolerant=False):
    """Run a module and return its results.

    tolerant=True returns the module's root table even if it raised part way through, which is
    how a table of constants is still read when its last few entries need an engine value.
    """
    with open(path, "rb") as f:
        return run_data(f.read(), requires, globals_values, tolerant)


def run_data(data, requires=None, globals_values=None, tolerant=False):
    """Run a chunk that is already in memory - a module read straight out of resource.car.

    This is what lets the app read a game data module without unpacking the archive to disk
    first (see `coromontools/font.py`, which needs the font's glyph table).
    """
    root, _info = luadis.load_data(data)
    frame = Frame(root, [])
    if root.is_vararg:
        frame.varargs = []
    try:
        return execute(make_sandbox(requires, globals_values), frame)
    except LuaError as exc:
        if not tolerant:
            raise
        sys.stderr.write("partial: %s\n" % exc)
        return [frame.r[0]]


# ---------------------------------------------------------------- output


def lua_literal(v, indent=0):
    pad = "  " * indent
    if isinstance(v, Table) or isinstance(v, dict):
        keys = list(v.keys())
        numeric = [k for k in keys if isinstance(k, int) and k >= 1]
        if numeric and len(numeric) == len(keys) and sorted(numeric) == list(range(1, len(keys) + 1)):
            if not keys:
                return "{}"
            inner = ",\n".join(pad + "  " + lua_literal(v[k], indent + 1) for k in sorted(keys))
            return "{\n" + inner + "\n" + pad + "}"
        if not keys:
            return "{}"
        parts = []
        for k in sorted(keys, key=lambda k: str(k)):
            key = k if isinstance(k, str) and k.isidentifier() else "[%s]" % lua_literal(k)
            parts.append("%s  %s = %s" % (pad, key, lua_literal(v[k], indent + 1)))
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    if isinstance(v, str):
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "nil"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return repr(v)


def summarise(v, path=""):
    if isinstance(v, dict):
        keys = list(v.keys())
        print("%s{table, %d entries}" % (path or "", len(keys)))
        for k in sorted(keys, key=lambda k: str(k))[:12]:
            sub = v[k]
            kind = type(sub).__name__
            if isinstance(sub, dict):
                kind = "table(%d)" % len(sub)
            print("  %s%s = %s" % (path, k, kind))
    else:
        print("%s%r" % (path, v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--json", default=None)
    ap.add_argument("--lua", action="store_true")
    ap.add_argument("--at", action="append", default=[])
    ap.add_argument("--call", default=None,
                    help="call a function in the returned table (e.g. roundedRect_foo) "
                         "and summarise/print what it returns")
    ap.add_argument("--arg", action="append", default=[],
                    help="string argument for --call (repeatable)")
    ap.add_argument("--require", action="append", default=[],
                    help="name=file.lu, made available to the module's require()")
    ap.add_argument("--global", dest="globals_", action="append", default=[],
                    help="Name=file.lu, pre-loaded as a global (e.g. Enum=...)")
    ap.add_argument("--stub", action="append", default=[],
                    help="Name=fn1,fn2 - install engine globals the game's boot script builds "
                         "(e.g. Color=rgb,rgbFloat,rgba). Each fn records its arguments, so the "
                         "values a style table is built from stay visible.")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--tolerant", action="store_true",
                    help="print whatever the module built before it needed an engine-only value")
    args = ap.parse_args()

    requires = {}
    for spec in args.require:
        name, _, target = spec.partition("=")
        requires[name] = target

    globals_values = {}
    for spec in args.stub:
        name, _, fns = spec.partition("=")
        table = StubResult()
        for fn_name in [f for f in fns.split(",") if f]:
            def recorder(*a, _n=fn_name):
                rec = StubResult()
                rec["fn"] = _n
                rec["args"] = list(a)
                return rec
            table[fn_name] = recorder
        globals_values[name] = table
    for spec in getattr(args, "globals_", []):
        name, _, target = spec.partition("=")
        globals_values[name] = run_module(target, requires, globals_values)[0]

    sandbox = make_sandbox(requires, globals_values)
    value = run_module(args.file, requires, globals_values, args.tolerant)[0]

    if args.call:
        fn = value.get(args.call) if isinstance(value, dict) else None
        if fn is None:
            raise SystemExit("no function %r in the module's return value" % args.call)
        value = call(sandbox, fn, list(args.arg), None, 0)
        value = value[0] if value else None

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(to_python(value), f, indent=1)
        print("wrote", args.json)

    for key in args.at:
        cur = value
        for part in key.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part, cur.get(norm(part)))
            else:
                cur = None
        print("=== %s" % key)
        if isinstance(cur, dict) and len(cur) > args.limit:
            for k in sorted(cur.keys(), key=lambda k: str(k))[:args.limit]:
                print("  %s = %s" % (k, lua_literal(cur[k])))
            print("  ... %d more" % (len(cur) - args.limit))
        else:
            print(lua_literal(cur, 1) if isinstance(cur, dict) else repr(cur))

    if args.lua:
        print(lua_literal(value))
    elif not args.at and not args.json:
        summarise(value)


if __name__ == "__main__":
    main()
