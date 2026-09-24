"""App-wide policy for the grind window.

Constants that describe what the tool decides, not what the user chose - the user's own
choices live in `state.py`. Kept here so a tuning change is one edit in one file.
"""

# Defaults for the values the user can change; the saved state overrides them.
LEVEL_DEFAULT = 50
MIN_SHARE_DEFAULT = 0.0
ONLY_XP_DEFAULT = True
ON_TOP_DEFAULT = True
# THE ZONE RANKING'S "with story states" TICK, off by default: it is a way to FIND the maps whose
# tiles change with the story, not a way to rank them.
STATES_ONLY_DEFAULT = False

# A zone whose species span this many levels ("Swurmy L1-99 in the titan temple") ranks high
# on the weighted-level average and is not a grind spot, so it gets flagged.
SCALES_SPAN = 30

# "still gives XP" hides zones whose highest monster is this far below the squad level.
XP_MARGIN = 3

# A nudge, in TEXELS, applied to the sprite inside every dex icon: the one knob for "the sprite
# sits a pixel low" and similar. (0, 0) is the game's own composition - the atlas cell at its
# authored position, with the type frame at 3.5 texels, the centre of the cell - and it is ONE
# NUMBER FOR THE WHOLE GRID on purpose: per-sprite nudging was tried (from each sprite's own art
# bounding box) and it moved every Coromon by a different amount, because the box is the body plus
# whatever tail, wing or glow that one happens to have. Negative values move the sprite up/left.
#
# WHY -3 IS ALSO THE GAME'S OWN NUMBER, and not just an eye adjustment: the game puts the 24 texel
# sprite and the 17 texel type frame in the same 24 texel container and aligns them bottom-right to
# bottom-right (`magnet:bottomRight(frame, padding, padding, container)` in
# `classes.interface.MonsterAvatar`), so the frame sits 7 texels into the sprite - and on a cell
# whose frame is at 4, 7 - 4 = 3 texels up and left IS this nudge.
ICON_NUDGE = (-3, -3)

# HOW MUCH SLACK THE CANVAS GIVES THE SPRITE BEYOND THE CELL, in TEXELS, on the side the sprite
# actually hangs over - the top and left, for a negative nudge.
# A negative nudge hangs the sprite off the cell's top-left corner, and the canvas used to be the
# cell exactly, so that corner was CUT OFF - which is what showed up on the larger Coromon: measured
# across the shipped sheet, the art reaches within 1 to 2 texels of the cell's top-left corner for
# 51 of the 134 dex cells (and within 3 for far more), while a small Coromon sits inside its cell
# and never touched the edge. The game never meets the clip because there the sprite IS the
# container; here the frame lives in a cell that is smaller than the offset needs, so the canvas
# gets slack instead of the sprite losing pixels.
# THE SLACK IS ON ONE SIDE ONLY, and that is not an accident: it is the overhang, so it is on the
# side the overhang is. Padding all four sides (what this did first) keeps the composition centred
# in the canvas but adds 3 texels of empty canvas to EVERY icon - 9 columns x 15 px of air nothing
# draws in, which is most of why the grid did not fit the window. The content does not move: the
# sprite is still at the cell's own offset inside it, so this only takes away room nobody used.
ICON_PAD = (abs(ICON_NUDGE[0]), abs(ICON_NUDGE[1]))

# Where the CAUGHT badge sits, in TEXELS added to the icon's own top-right corner. Texels, not
# pixels, so the badge stays on the sprite's scaling grid (the user's correction: "I meant 2px
# before scaled"); (0, 0) is flush with the corner.
ICON_BADGE_SHIFT = (2, 0)

# THE ENTRY PLATE behind every icon: the game's own rounded-rect sprite (`dex.PLATE_SPRITE`), drawn
# `PLATE_TEXELS` square. 19 puts ONE TEXEL OF PLATE all round the 17 texel frame, which is what the
# game's own entry measures - template-matching the frame sprite into a screenshot puts the frame at
# x 285..369, y 135..219 and the plate's coloured pixels at x 280..374, y 130..224. `PLATE_OFFSET`
# centres it on the frame, which `dex.FRAME_TEXELS` puts 4 texels into the 24 texel cell.
PLATE_TEXELS = 19
PLATE_OFFSET = 3
# The plate's fill, measured off the game's own entry (it composites the white mask into #3C3C4F
# there). An entry the player has NOT discovered uses the game's DISABLED box: the same shape in
# black.
PLATE_FILL = "#3C3C4F"
PLATE_UNKNOWN_FILL = "#000000"
# Where the "?" of a never-seen entry sits, in TEXELS from the FRAME's top-left corner: the game
# anchors it to the entry box (`magnet:bottomRight(questionMark, 6.0, 0.0, container)`) and draws
# it in `outline_10_bold` - the same font as the number - in the entry's own text colour. Measured
# against the game's own screen, its glyph ink lands 4.5 texels in from the frame's left edge and
# 4 texels down from its top (x 628..650 for a frame box at x 605..689); this is the bottom-right
# corner of the text box that puts it there.
QUESTION_MARK = (9.5, 11.0)
# Texels between the plate's right edge and the number's right edge, from the game's own placement
# (`magnetRightToLeftAware:bottomRight(text, 2, 0, parent)`).
NUMBER_GAP = 2

# ---- THE FOUR DEX STATES, with the game's own colours and darkening -------------------------
# Every value here is READ OUT of the game, not chosen, so the window can be checked against it:
#   * the number colours are `classes.constants.colors` - `TEXT_LIGHTBLUE`, `TEXT_DISABLED` and
#     `UI_TEXT_RED_DISABLED`, picked in `monsterDatabaseScreen` by
#     hasOwnedMonster(uid, category) / hasSeenMonster(uid, category) / hasSeenMonster(uid).
#     The "?" of a never-seen entry is drawn in the same colour as its number
#     (`questionMarkColor = <the text colour>`) in `outline_10_bold`.
#   * the darkening is what the screen passes to `MonsterAvatar` as `darkenedMonsterAlpha` and
#     `darkenedMonsterSaturation`. The avatar keeps a copy of itself duotoned BLACK
#     (`fillEffectHelper:setDuotone(x, 0, 0, 0)`) and draws it over the sprite AND its frame at
#     that alpha, so `alpha` is how black the entry goes and `saturation` how much colour survives.
#     An entry that is neither selected nor hovered gets the `darkened` pair, one you hover or
#     select gets the non-darkened one (which is why hovering brings an entry back to colour).
STATE_CAUGHT = "caught"          # owned in this potential category
STATE_SEEN = "seen"              # met in this one, not caught
STATE_ELSEWHERE = "elsewhere"    # met in ANOTHER potential category - you know it exists
STATE_UNKNOWN = "unknown"        # never met at all
STATES = (STATE_CAUGHT, STATE_SEEN, STATE_ELSEWHERE, STATE_UNKNOWN)

STATE_NUMBERS = {
    STATE_CAUGHT: "#00F6FF",      # colors.TEXT_LIGHTBLUE        rgb(0, 246, 255)
    STATE_SEEN: "#878787",        # colors.TEXT_DISABLED         rgb(135, 135, 135)
    STATE_ELSEWHERE: "#615553",   # colors.UI_TEXT_RED_DISABLED  rgb(97, 85, 83)
    STATE_UNKNOWN: "#615553",
}
# (black copy's alpha, saturation) - measured against the screen's own options:
#   owned/seen......... darkenedMonsterAlpha 0.1, darkenedMonsterSaturation 0.5 for an OWNED entry
#                        (1.0 when hovered), and no saturation change for a seen one
#   seen elsewhere..... darkenedMonsterAlpha 0.9, darkenedMonsterSaturation 0.2
#                        (0.85 when hovered)
#   never seen......... the sprite is drawn as a black silhouette (the game duotones the real
#                        sprite black) with the game's UNKNOWN frame and the "?" on top.
STATE_DARKENING = {
    STATE_CAUGHT: (0.1, 0.5),
    STATE_SEEN: (0.1, 1.0),
    STATE_ELSEWHERE: (0.9, 0.2),
    STATE_UNKNOWN: (1.0, 0.0),
}

# Draw the dex number in the bottom-right of every icon, exactly as the game's database draws it:
# the game's OWN FONT (glyphs read out of the shipped 512x1024 atlas - see `font.py`), in the
# colour of the entry's state. Species with no dex number (the titans and a couple of others) get
# none - there is no number.
ICON_NUMBERS = True

# Dex icons are 24 px atlas cells that the game draws small. The window shows them at 5x, which
# is a whole-number nearest-neighbour scale - the only kind that keeps pixel art crisp - and at
# that size the type frame, the sprite and the caught badge are all legible without squinting.
# It is also what the grid's column widths and the list's icon size are computed from, so this one
# number decides how big every dex icon in the window is.
# THIS IS THE DEFAULT, NOT THE SETTING: the Database tab has a pair of small -/+ buttons for it and
# the choice is kept in the app's own preferences ("icon_zoom"), like the window's position - see
# `DatabaseTab.set_zoom`, and `ICON_ZOOM_MIN`/`ICON_ZOOM_MAX` below for how far it goes.
ICON_ZOOM = 5

# How far the -/+ buttons go. 1 would be a 24 px icon (the size the game itself draws, and too
# small to read a bird from a bat), and past 8 a single grid column is wider than the window.
ICON_ZOOM_MIN = 2
ICON_ZOOM_MAX = 8

# The preference the icon scale is kept under - the app's own state, like the window's position,
# because it is a user decision rather than a constant to re-edit and restart for.
ICON_ZOOM_KEY = "icon_zoom"

# ... and the one the Database tab's "hide complete" checkbox is kept under, for the same reason: it is
# how that tab is being read at the time, not something to re-tick on every start.
HIDE_COMPLETE_KEY = "hide_complete"

# HOW BIG A ZONE'S NAME IS DRAWN ON THE MAP: a multiple of one cell, and a ceiling in pixels. The
# size follows the zoom so the name stays legible when the map is small, but left uncapped it grew
# past the block it names - MEASURED at 26 px against 12 px cells on a 53x56 map (the user: "the font
# is way too big"). The selected zone's is one step bigger, which is how it is told apart on the map.
LABEL_PX = 0.85
SELECTED_LABEL_PX = 1.15
LABEL_MAX_PX = 13
SELECTED_LABEL_MAX_PX = 17

# ... AND THE LEGEND CHIP, the small square under the map panel that names the same zones. A chip is
# sized to its own TEXT, because the labels are not all one letter: every water zone ends in "WATER"
# and the event zones in "SPECIAL", and a fixed 22 px square with the window's own font cuts both of
# them off - the user: "in the legend, labels like WATER don't fit the small square at the top".
CHIP_FONT_PX = 11
CHIP_MIN_WIDTH = 20
CHIP_HEIGHT = 17

# HOW THE MAP PICTURE IS SCALED. The picture is the game's own tiles at 16 px a cell and the pane
# draws it at `_map_scale` px a cell - usually BELOW 1:1 (a 97x70 map is 1552 px wide against a ~430 px
# column), and there nearest neighbour tears the tile grid into moire. Zoomed IN past 1:1 the two swap
# over: the game itself draws its map with integer nearest neighbour, which is what keeps pixel art
# crisp. So it is the user's choice, ticked on the map panel and remembered like the dex scale.
MAP_SMOOTH_KEY = "map_smooth"
MAP_SMOOTH_DEFAULT = True
# LAYERS THAT ARE NEVER DRAWN, by their own name or by their BASE name (the part before any `#`, so
# one entry hides every state of it). Only the rain needs one today - `rainDrops` is 960 drop tiles
# inside the `levels` group on Donar Island, drawn as white dots all over the town when it is not
# raining (the user: "donor island also has those strange white dots"). The `world*` overlays are
# outside the terrain groups already; they are named here so that one list answers "what is not drawn".
MAP_LAYER_BLACKLIST = ("rainDrops", "worldRain", "worldRainOverlay", "worldSnowOverlay",
                       "worldOverlay", "aboveWorldOverlay")

# A map is FITTED to the pane, but the fit itself is never blown up past this: a small map filling a
# maximised window is all block, and the patches stop being readable.
#
# THE CAP IS ON THE FIT, NOT ON THE ZOOM (see `mapview._map_scale`). Capping the finished scale seems
# the same and is not: a map that fits at 2.1 px a cell reached this ceiling by x4, so x4, x5 and x6 all
# drew exactly the same picture while the label kept counting - the user: "the scale buttons don't do
# anything".
MAP_MAX_SCALE = 8.0

# How many patch origins the map's headline names before it gives up and says "...". One map
# marks 137 patches, and a label listing all of them is a paragraph of coordinates.
SPOTS_SHOWN = 6

# How much of a patch's colour survives when it is not the selected zone. The selected zone is
# drawn solid, and the rest are faded so it can be picked out of six - the Tk version had no
# alpha and had to stipple them instead.
#
# EVERY BLOCK IS TRANSLUCENT NOW, the selected one included - the user: "some of the blocks show as
# opaque, they should all be semi transparent with solid edges around the zones". A zone's identity is
# its EDGE, so the fill only has to tint the map's own tiles - which is also why this is lighter than
# it was when the ground behind it was a flat dark colour.
PATCH_ALPHA = 110
# ... AND THE EDGE IS AN OPAQUE COLOUR, because the map's tiles are behind the zones now: a translucent
# edge on top of grass or water is not an edge. The SELECTED zone's edge is white and a little wider -
# the same white border its legend chip wears - so "which one am I looking at" is answered without one
# zone's fill differing from the rest.
EDGE_WIDTH = 2.0
SELECTED_EDGE = "#ffffff"
SELECTED_EDGE_WIDTH = 3.0

# THE MAP VIEW ZOOMS, and the value belongs to the whole window: the map is drawn in three tabs, and a
# zoom that meant something different in each would be a bug rather than a feature. x1 is "fitted to the
# pane", which is what it always was, and the buttons scale around that; above x1 the pane scrolls.
MAP_ZOOM_KEY = "map_zoom"
MAP_ZOOM_DEFAULT = 1
# THE STEPS THE BUTTONS WALK, and WHOLE NUMBERS ONLY: the map is pixel art on a 16 px grid, so a factor
# of x1.25 magnifies a tile by a fraction and no label can honestly say by how much - the user: "they
# should be integer scaling only". Six of them, because past x6 the pane shows a couple of rooms.
MAP_ZOOM_STEPS = (1, 2, 3, 4, 5, 6)
