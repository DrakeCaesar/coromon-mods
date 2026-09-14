#!/usr/bin/env python3
"""
Solar2D resource.car extractor.

Format (little endian):
   0x00  magic  "rac\x01"
   0x04  u32    version (1)
   0x08  u32    ?? (toc_end-ish)
   0x0C  u32    ?? 
   0x10  TOC entries until first entry data offset:
        entry: u32 type, u32 offset, u32 namelen, char name[namelen] (padded to 4)
   types: 1 = plain resource, 2 = lua bytecode (12-byte prefix then std LuaQ chunk)
"""
import argparse
import os
import struct
import sys

MAGIC = b"rac\x01"


def parse_toc(path):
    with open(path, "rb") as f:
        head = f.read(16)
        if head[:4] != MAGIC:
            raise SystemExit(f"not a rac archive: {head[:4]!r}")
        f.seek(16)
        entries = []
        while True:
            pos = f.tell()
            hdr = f.read(12)
            if len(hdr) < 12:
                break
            typ, off, namelen = struct.unpack("<III", hdr)
            if namelen == 0 or namelen > 4096:
                break
            name = f.read(namelen)
            if len(name) < namelen or not name.isascii():
                break
            entries.append((typ, off, name.decode("ascii", "replace")))
            # name is NUL-terminated then padded to a 4-byte boundary
            pad = (-(pos + 12 + namelen + 1)) % 4
            f.read(1 + pad)
        return entries


def read_entry(path, typ, off):
    with open(path, "rb") as f:
        f.seek(off)
        hdr = f.read(12)
        etype, usize, csize = struct.unpack("<III", hdr)
        data = f.read(csize)
        return etype, usize, csize, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("car")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--filter", default=None)
    ap.add_argument("--extract", default=None, help="output dir")
    ap.add_argument("--grep-bytes", default=None, help="only extract entries whose name matches")
    args = ap.parse_args()

    entries = parse_toc(args.car)
    print("entries:", len(entries), file=sys.stderr)
    for typ, off, name in entries[:5]:
        print("  sample:", typ, hex(off), name, file=sys.stderr)

    if args.list:
        for typ, off, name in entries:
            if args.filter and args.filter.lower() not in name.lower():
                continue
            print(f"{typ}\t{off:#x}\t{name}")
        return

    if args.extract:
        os.makedirs(args.extract, exist_ok=True)
        n = 0
        for typ, off, name in entries:
            if args.grep_bytes and args.grep_bytes.lower() not in name.lower():
                continue
            try:
                _, _, _, data = read_entry(args.car, typ, off)
            except Exception as e:  # noqa
                print("fail", name, e)
                continue
            out = os.path.join(args.extract, name.replace("/", os.sep))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(data)
            n += 1
        print("extracted", n)


if __name__ == "__main__":
    main()
