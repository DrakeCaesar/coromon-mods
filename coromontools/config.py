"""App-wide policy for the grind window.

Constants that describe what the tool decides, not what the user chose - the user's own
choices live in `state.py`. Kept here so a tuning change is one edit in one file.
"""

# Defaults for the values the user can change; the saved state overrides them.
LEVEL_DEFAULT = 50
MIN_SHARE_DEFAULT = 0.0
ONLY_XP_DEFAULT = True
ON_TOP_DEFAULT = True

# A zone whose species span this many levels ("Swurmy L1-99 in the titan temple") ranks high
# on the weighted-level average and is not a grind spot, so it gets flagged.
SCALES_SPAN = 30

# "still gives XP" hides zones whose highest monster is this far below the squad level.
XP_MARGIN = 3

# Dex icons are 24 px atlas cells that the game draws small. A list row shows them at 2x,
# which is a whole-number nearest-neighbour scale - the only kind that keeps pixel art crisp.
ICON_ZOOM = 2

# A map is fitted to the pane, but never blown up past this: a small map filling a maximised
# window is all block, and the patches stop being readable.
MAP_MAX_SCALE = 8.0

# How many patch origins the map's headline names before it gives up and says "...". One map
# marks 137 patches, and a label listing all of them is a paragraph of coordinates.
SPOTS_SHOWN = 6

# How much of a patch's colour survives when it is not the selected zone. The selected zone is
# drawn solid, and the rest are faded so it can be picked out of six - the Tk version had no
# alpha and had to stipple them instead.
PATCH_ALPHA = 150
