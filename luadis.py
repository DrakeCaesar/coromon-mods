#!/usr/bin/env python3
"""
Minimal Lua 5.1 bytecode reader / disassembler for Solar2D `.lu` modules.

Usage:
    python luadis.py <file.lu> [--strings] [--proto N] [--all]
"""
import argparse
import struct
import sys

OPNAMES = """MOVE LOADK LOADBOOL LOADNIL GETUPVAL GETGLOBAL GETTABLE SETGLOBAL
SETUPVAL SETTABLE NEWTABLE SELF ADD SUB MUL DIV MOD POW UNM NOT LEN CONCAT JMP
EQ LT LE TEST TESTSET CALL TAILCALL RETURN FORLOOP FORPREP TFORLOOP SETLIST CLOSE
CLOSURE VARARG""".split()

# opcode -> (A,B,C) / (A,Bx) / (A,sBx)
ARITH = {12, 13, 14, 15, 16, 17, 18, 19, 20, 21}
AB = {0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37}


class Reader:
    def __init__(self, data, intsize, sizesize, instrsize, numsize, little=True, integral=0):
        self.d = data
        self.p = 0
        self.ints = intsize
        self.szs = sizesize
        self.instrs = instrsize
        self.nums = numsize
        self.integral = integral
        self.e = "little" if little else "big"
        self.se = "<" if little else ">"

    def byte(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def nbytes(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def int(self):
        v = int.from_bytes(self.nbytes(self.ints), self.e, signed=True)
        return v

    def size(self):
        return int.from_bytes(self.nbytes(self.szs), self.e, signed=False)

    def instr(self):
        return int.from_bytes(self.nbytes(self.instrs), self.e, signed=False)

    def number(self):
        if self.integral:
            return int.from_bytes(self.nbytes(self.nums), self.e, signed=True)
        if self.nums == 8:
            return struct.unpack(self.se + "d", self.nbytes(8))[0]
        return struct.unpack(self.se + "f", self.nbytes(4))[0]

    def string(self):
        n = self.size()
        if n == 0:
            return None
        s = self.nbytes(n)[:-1]
        return s.decode("utf-8", "replace")


class Proto:
    def __init__(self):
        self.source = None
        self.linedefined = 0
        self.lastlinedefined = 0
        self.nups = 0
        self.numparams = 0
        self.is_vararg = 0
        self.maxstack = 0
        self.code = []
        self.k = []          # constants
        self.protos = []
        self.lineinfo = []
        self.locvars = []
        self.upvalue_names = []


def load(path):
    data = open(path, "rb").read()
    assert data[:4] == b"\x1bLua", "not lua bytecode"
    fmt = data[4]  # version
    endian = data[6]
    intsize, sizesize, instrsize, numsize, integral = data[7], data[8], data[9], data[10], data[11]
    r = Reader(data, intsize, sizesize, instrsize, numsize, endian == 1, integral)
    r.p = 12

    def read_proto(reader, top=False):
        pr = Proto()
        pr.source = reader.string()
        pr.linedefined = reader.int()
        pr.lastlinedefined = reader.int()
        pr.nups = reader.byte()
        pr.numparams = reader.byte()
        pr.is_vararg = reader.byte()
        pr.maxstack = reader.byte()
        n = reader.int()
        pr.code = [reader.instr() for _ in range(n)]
        n = reader.int()
        for _ in range(n):
            t = reader.byte()
            if t == 0:
                pr.k.append(None)
            elif t == 1:
                pr.k.append(bool(reader.byte()))
            elif t == 3:
                pr.k.append(reader.number())
            elif t == 4:
                pr.k.append(reader.string())
            else:
                raise SystemExit("bad constant type %d" % t)
        n = reader.int()
        pr.protos = [read_proto(reader) for _ in range(n)]
        # debug
        n = reader.int()
        pr.lineinfo = [reader.int() for _ in range(n)]
        n = reader.int()
        for _ in range(n):
            pr.locvars.append((reader.string(), reader.int(), reader.int()))
        n = reader.int()
        pr.upvalue_names = [reader.string() for _ in range(n)]
        return pr

    root = read_proto(r)
    return root, dict(version=fmt, endian=endian, intsize=intsize, sizesize=sizesize,
                      instrsize=instrsize, numsize=numsize, integral=integral, size=len(data))


def walk(pr, path=(0,), out=None):
    out = out if out is not None else []
    out.append((path, pr))
    for i, sub in enumerate(pr.protos):
        walk(sub, path + (i,), out)
    return out


def kval(pr, i):
    if i < len(pr.k):
        v = pr.k[i]
        if isinstance(v, str):
            return repr(v)
        return repr(v)
    return "k%d?" % i


def disasm(pr, protos, show_lines=True):
    print(f"# proto lines {pr.linedefined}-{pr.lastlinedefined} params={pr.numparams} "
          f"vararg={pr.is_vararg} maxstack={pr.maxstack} nups={pr.nups} upvals={pr.upvalue_names}")
    for pc, ins in enumerate(pr.code):
        op = ins & 0x3F
        A = (ins >> 6) & 0xFF
        C = (ins >> 14) & 0x1FF
        B = (ins >> 23) & 0x1FF
        Bx = (ins >> 14) & 0x3FFFF
        sBx = Bx - 131071
        name = OPNAMES[op] if op < len(OPNAMES) else "OP%d" % op
        extra = ""
        if name in ("LOADK", "GETGLOBAL", "SETGLOBAL", "CLOSURE"):
            extra = f" Bx={Bx} {kval(pr, Bx) if name != 'CLOSURE' else 'proto %d' % Bx}"
        elif name in ("JMP", "FORLOOP", "FORPREP"):
            extra = f" sBx={sBx} -> {pc + 1 + sBx}"
        elif name in ("EQ", "LT", "LE", "TEST", "TESTSET"):
            extra = f" B={B}"
        elif name in ("GETTABLE", "SETTABLE", "SELF"):
            extra = f" B={B} C={C}" + (f" k={kval(pr, C)}" if C >= 256 else "") + \
                    (f" k={kval(pr, B)}" if 256 <= B < 512 else "")
        elif name == "NEWTABLE":
            extra = f" B={B} C={C}"
        elif name == "CALL":
            extra = f" nargs={B - 1} nres={C - 1}"
        elif name == "RETURN":
            extra = f" n={B - 1}"
        elif name == "LOADBOOL":
            extra = f" B={B} skip={C}"
        elif name in ("GETUPVAL", "SETUPVAL"):
            extra = f" B={B} ({pr.upvalue_names[B] if B < len(pr.upvalue_names) else '?'})"
        elif name == "SETLIST":
            extra = f" B={B} C={C}"
        line = f"  [{pc:4d}] {name:9s} A={A:<4d}{extra}"
        if show_lines and pc < len(pr.lineinfo):
            line += f"   ; L{pr.lineinfo[pc]}"
        print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--strings", action="store_true")
    ap.add_argument("--globals", action="store_true")
    ap.add_argument("--proto", type=int, default=None, help="index into flattened proto list")
    ap.add_argument("--tree", action="store_true")
    ap.add_argument("--consts", action="store_true",
                    help="print every constant with its index (numbers included). "
                         "In Lua 5.1 an RK operand of C>=256 refers to k[C-256].")
    args = ap.parse_args()

    root, info = load(args.file)
    protos = walk(root)
    if args.tree:
        print(info)
        for path, pr in protos:
            print(f"proto {path} lines {pr.linedefined}-{pr.lastlinedefined} nups={pr.nups} "
                  f"upvals={pr.upvalue_names} nconst={len(pr.k)}")
    if args.strings:
        seen = []
        for path, pr in protos:
            for k in pr.k:
                if isinstance(k, str):
                    seen.append((path, k))
        for path, s in seen:
            print(f"{'.'.join(map(str, path))}\t{s}")
    if args.globals:
        for path, pr in protos:
            g = []
            for ins in pr.code:
                op = ins & 0x3F
                if op in (5, 7):  # GETGLOBAL / SETGLOBAL
                    Bx = (ins >> 14) & 0x3FFFF
                    g.append((OPNAMES[op], pr.k[Bx] if Bx < len(pr.k) else "?"))
            if g:
                print(f"proto {path} ({pr.linedefined}-{pr.lastlinedefined}):")
                for kind, nm in g:
                    print(f"   {kind} {nm}")
    if args.proto is not None:
        path, pr = protos[args.proto]
        print("=== proto", path)
        disasm(pr, protos)
    if args.consts:
        for path, pr in protos:
            tag = ".".join(map(str, path))
            for i, k in enumerate(pr.k):
                print(f"{tag}\tk[{i}]\t{k!r}")
    if not (args.strings or args.globals or args.tree or args.proto is not None
            or args.consts):
        print(info)
        print("protos:", len(protos))
        for path, pr in protos:
            print(f"  {path} lines {pr.linedefined}-{pr.lastlinedefined}")


if __name__ == "__main__":
    main()
