"""_install.py - install the current source into the running game, once, with no waiting loop.

`overlays.py` does this on attach and then sits there watching for launches; for testing a change
that is already in the files that is one step too many. This is the same install chunk
(`core.install_code`) pushed through the same bridge, so the running code is exactly the code the
tool would install. Throwaway.

    python _install.py            # install and print the summary
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
    code = core.install_code(FEATURES, cfg)
    bridge = Bridge("coromon.exe")
    time.sleep(0.2)
    status = bridge.status() or {}
    print("[bridge] state=%s hook_hits=%s" % (status.get("state"), status.get("hooks")))
    result = bridge.eval(code, timeout=90.0)
    if result.get("err"):
        print("ERROR: %s" % (result.get("err"),))
    out = result.get("out") or ""
    with open("_install_out.txt", "w", encoding="utf-8") as fh:
        fh.write(out)
    print(out)
    bridge.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
