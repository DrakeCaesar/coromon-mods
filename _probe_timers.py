"""_probe_timers.py - install the stopwatch probe, let it run, then print its report.

Throwaway, like _run_lua.py, but two evals with real time in between: the point is to measure the
PERIODIC cost of each feature, which needs a window. `_run_lua.py` holds one script and one eval.

    python _probe_timers.py [_probe_timers.lua] [seconds]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coromon_lua import Bridge  # noqa: E402


def main():
    script = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "_probe_timers.lua"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

    with open(script, encoding="utf-8") as fh:
        install = fh.read()

    bridge = Bridge("coromon.exe")
    time.sleep(0.2)
    status = bridge.status() or {}
    print("[bridge] state=%s hook_hits=%s" % (status.get("state"), status.get("hooks")))

    res = bridge.eval(install, timeout=60.0)
    print("[install] %s" % (res.get("out") or res.get("err")))

    print("[wait] measuring for %.0f s - leave the game in the overworld and do not touch it" % seconds)
    # Poll instead of a long sleep: an idle Frida session was seen to go stale over a 12 s gap
    # (it reported "the game closed" while the process was still very much alive), and polling
    # also means the report is only ever read from a session that is known to be working.
    started = time.time()
    out = ""
    while time.time() - started < seconds:
        time.sleep(2.0)
        rep = bridge.eval("return _G.__tt and _G.__tt.report() or 'probe not installed'",
                          timeout=30.0)
        if rep.get("err"):
            print("[error] %s" % rep["err"])
            break
        out = rep.get("out") or out
    print(out)

    rm = bridge.eval("return _G.__tt_remove and _G.__tt_remove() or 'no remover'", timeout=30.0)
    print("[cleanup] %s" % (rm.get("out") or rm.get("err")))


if __name__ == "__main__":
    main()
