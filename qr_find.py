"""qr_find.py - find where a name is used in a disassembled Coromon .lu file.

Runs the working disassembler over every proto and reports the protos whose
listing mentions one of the given names, with the matching instruction lines.

Usage:
  python qr_find.py <file.lu> <name> [<name2> ...] [--context N]
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import luadis


def main():
    args = [a for a in sys.argv[1:]]
    context = 1
    if '--context' in args:
        i = args.index('--context')
        context = int(args[i + 1])
        del args[i:i + 2]
    if len(args) < 2:
        print(__doc__)
        return 1
    path, names = args[0], args[1:]

    root, _info = luadis.load(path)
    flat = luadis.walk(root)
    hits = 0
    for p, pr in flat:
        text = luadis.disasm(pr, flat, show_lines=True)
        if not text:
            continue
        lines = text.splitlines()
        for n in names:
            for i, ln in enumerate(lines):
                if ("'" + n + "'") not in ln and ('"' + n + '"') not in ln:
                    continue
                hits += 1
                print("--- %s  in proto %s  (lines %d-%d)" % (
                    n, str(p), pr.linedefined, pr.lastlinedefined))
                lo = max(0, i - context)
                hi = min(len(lines), i + context + 1)
                for j in range(lo, hi):
                    print(("  >> " if j == i else "     ") + lines[j])
                print()
    if not hits:
        print("no mention of %s" % ", ".join(names))
    return 0


if __name__ == '__main__':
    sys.exit(main())
