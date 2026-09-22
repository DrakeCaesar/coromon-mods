"""_status.py - print the tool's full status report for the game that is running.

`overlays.py --status` cannot be used while the watcher is running (the single-instance lock is
exactly for that, since two installers racing freeze the game). This does not install anything and
does not take the lock - it attaches, evaluates the same status chunk `overlays.py --status` would
use, and detaches. Safe to run alongside the watcher.

    python _status.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coromon_lua import Bridge  # noqa: E402
from ingame import FEATURES, core  # noqa: E402


def main():
    cfg = core.read_config(FEATURES)
    if cfg is None:
        print("could not read the config")
        return 1
    bridge = Bridge("coromon.exe")
    time.sleep(0.2)
    result = bridge.eval(core.status_code(FEATURES, cfg), timeout=60.0)
    out = result.get("out")
    if out is None:
        out = "ERROR: %s" % (result.get("err"),)
    print(out)
    bridge.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
