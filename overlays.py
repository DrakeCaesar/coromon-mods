#!/usr/bin/env python3
"""
overlays.py - the live in-game helpers: the battle Potential readout, the markers over the
map's collectable objects, and the overworld zoom.

Run it with no arguments. Everything it does comes from `overlays.toml`, which sits next to
this file:

    python overlays.py

It makes the game match that file - it tears down whatever was installed last time and
installs whatever is switched on there - and then prints what it installed, the current
state of each feature, and what is around you. Turning a feature off is setting its
`enabled` to false and running again; there is nothing to remember on the command line.

It does not need the game to be up, and it does not stop when the game does. Start it
first and it waits for the game to appear; leave it running and closing and restarting the
game costs nothing - it re-attaches and installs everything again by itself, and re-reads
`overlays.toml` for the new session on the way. Ctrl+C stops it, and the game keeps
whatever is on screen until it is closed.

Only ONE instance may run at a time, and a second one refuses to start rather than join in.
That is not tidiness: a run installs its own copy of the code on every launch, so two of
them race to tear each other down, and an error thrown inside an install leaves the game
frozen on it. The run that is already going is named when you try.

The settings file documents itself: it is generated from the settings each module declares,
and if it goes missing it is written back out from those defaults. So the place to look for
what can be set is the file itself.

The code:

    ingame/config.py      the settings file: its schema, its defaults, reading it back
    ingame/core.py        the hook-up to the running game, the Lua every feature shares,
                          and the harness that composes them into one chunk
    ingame/potential.py   the battle Potential readout
    ingame/items.py       the markers over the map's collectable objects
    ingame/zoom.py        the overworld zoom keys
    ingame/__init__.py    the feature list, and the interface each feature implements

`core` knows nothing about what the features are - it asks each module for its settings, its
Lua, its summary line and its teardown. So the features can be worked on, installed and
removed independently, and adding another is one module plus one line in `ingame/__init__`.

This is where the old standalone `battle_potential.py`, `hidden_items.py` and `map_zoom.py`
ended up, folded in and then deleted.

What each feature does, and the notes behind it, is documented at the top of its module.

Requires frida (`pip install frida`) and Python 3.11 or newer for tomllib. The game can be
running, or not yet, or closed and started again - the wait and the re-attach are in
`ingame/core.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ingame import FEATURES, core  # noqa: E402

if __name__ == "__main__":
    lock = core.claim_single_instance()
    if lock is None:
        sys.exit(3)
    try:
        sys.exit(core.main(FEATURES))
    finally:
        core.release_single_instance(lock)
