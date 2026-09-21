"""qr_peek.py - inspect protos of a disassembled Coromon .lu file.

Replaces the ad-hoc `python - <<'PYEOF'` heredocs, which broke the shell when the
body was not sent.  Every selector is optional; they AND together.

Usage:
  python qr_peek.py <file.lu> --list 230 345          span table (linedefined in range)
  python qr_peek.py <file.lu> --at 246                protos whose span CONTAINS line 246
  python qr_peek.py <file.lu> --lines 246 268         protos whose span IS exactly 246-268
  python qr_peek.py <file.lu> --under 0 6             only descendants of proto (0,6)
  python qr_peek.py <file.lu> --under 0 6 --at 246    ... and containing line 246
  python qr_peek.py <file.lu> --at 246 --no-lines     skip the per-instruction line comments

Options:
  --skip N   drop the first N instructions of each matched proto (like `sed -n` on disasm)
  --max N    stop after N matched protos
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import luadis


def _take(args, flag):
    if flag not in args:
        return None
    i = args.index(flag) + 1
    out = []
    while i < len(args) and not args[i].startswith('--'):
        out.append(args[i])
        i += 1
    return out or None


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith('--'):
        print(__doc__)
        return 1
    path, args = args[0], args[1:]

    at = [int(x) for x in (_take(args, '--at') or [])]
    lines = _take(args, '--lines')
    span = (int(lines[0]), int(lines[1])) if lines else None
    under = _take(args, '--under')
    under = tuple(int(x) for x in under) if under else None
    table = _take(args, '--list')
    table = (int(table[0]), int(table[1])) if table else None
    skip = int((_take(args, '--skip') or [0])[0])
    maxn = int((_take(args, '--max') or [0])[0])
    show_lines = '--no-lines' not in args

    root, _info = luadis.load(path)
    flat = luadis.walk(root)

    if table is not None:
        for p, pr in flat:
            if table[0] <= pr.linedefined <= table[1]:
                print("  proto %-14s lines %4d-%4d params=%d ups=%s" % (
                    str(p), pr.linedefined, pr.lastlinedefined, pr.numparams,
                    pr.upvalue_names))
        return 0

    got = 0
    for p, pr in flat:
        if under is not None and tuple(p[:len(under)]) != under:
            continue
        if span is not None and (pr.linedefined, pr.lastlinedefined) != span:
            continue
        if at and not any(pr.linedefined <= x <= pr.lastlinedefined for x in at):
            continue
        got += 1
        if maxn and got > maxn:
            break
        print("=" * 74)
        print("proto %s  lines %d-%d  params=%d maxstack=%d ups=%s" % (
            str(p), pr.linedefined, pr.lastlinedefined, pr.numparams,
            pr.maxstack, pr.upvalue_names))
        text = luadis.disasm(pr, flat, show_lines=show_lines)
        for ln in (text or "(disasm returned nothing)").splitlines():
            # honour --skip by dropping instructions below it
            marker = ln.lstrip().startswith('[')
            if skip and marker:
                try:
                    pc = int(ln.split('[', 1)[1].split(']', 1)[0])
                except ValueError:
                    pc = 0
                if pc < skip:
                    continue
            print(ln)

    if not got:
        print("no proto matched")
    return 0


if __name__ == '__main__':
    sys.exit(main())
