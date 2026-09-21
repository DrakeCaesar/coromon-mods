"""Path constants for the Coromon grind tools.

The repo layout is::

    <game>/                          the Coromon install
        Resources/data/json/         the game's own data, read by the data modules
        coromon-tools/
            encounters_gui.py        entry script
            coromontools/            this package

Only the two paths this package itself needs live here. Every data module
(`encounters`, `skills`, `dex`, `encounter_zones`) already computes the path to the
files it reads from its own location, so nothing here has to know about them.
"""

import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(PACKAGE_DIR)
GAME_DIR = os.path.dirname(TOOLS_DIR)

APP_NAME = "Coromon - grind & skills"

# Where the Tk version kept its state. Read once by `state.py` so the tick list and the
# filters survive the move to Qt, and never written again.
LEGACY_STATE = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "coromon-grind.json"
)
