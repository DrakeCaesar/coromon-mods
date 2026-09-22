"""_run_lua.py - run a .lua file in the game and write the reply to a UTF-8 text file.

Throwaway helper. Two reasons it exists rather than a shell redirect: PowerShell's `>`
writes UTF-16, which nothing else here can read, and the terminal truncates long output.

    python _run_lua.py <script.lua> [out.txt] [--process coromon.exe]

`Bridge.eval` returns a dict of {"out", "err", "state"}, and `Bridge.wait()` is NOT a
"wait until ready" call - it blocks until the game goes away, which is what a first version
of this got wrong. There is nothing to wait for: attach, give it a moment, eval.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coromon_lua import Bridge  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1
    path = args[0]
    out = args[1] if len(args) > 1 else "_lua_out.txt"
    process = "coromon.exe"
    if "--process" in sys.argv:
        process = sys.argv[sys.argv.index("--process") + 1]

    with open(path, encoding="utf-8") as fh:
        source = fh.read()

    bridge = Bridge(process)
    time.sleep(0.2)
    status = bridge.status() or {}
    print("[bridge] state=%s hook_hits=%s" % (status.get("state"), status.get("hooks")))
    result = bridge.eval(source, timeout=60.0)
    text = result.get("out")
    if text is None:
        text = "ERROR: %s" % (result.get("err"),)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %s  (%d chars, %d lines)"
          % (out, len(text), text.count("\n") + 1), flush=True)
    bridge.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
