#!/usr/bin/env python3
"""encounters_gui.py - the Coromon grind window.

A thin entry script: this is the path that gets run, and it is the path the tkinter version
lived at, so nothing already open in a shell or a shortcut has to change. The window itself is
the `coromontools` package beside this file, split by responsibility - see
`coromontools/__init__.py` for the map of it.

    python encounters_gui.py             open the window
    python encounters_gui.py --selftest  build the data and print the ranking, no window

The tkinter implementation is kept at `abandoned/encounters_gui_tk.py`. It is worth keeping
because the two do not share their icon code: `dex.build_icon` still draws the PNG set that
`python dex.py --icons` writes, while the window composes its own in memory with Qt.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coromontools import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
