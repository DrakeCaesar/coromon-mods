"""The map's own tiles, composited into one picture - the ground the zones are drawn on.

WHERE THE PICTURE COMES FROM. The game's maps are Tiled JSON (`Resources/maps/**/*.json`), each with
its tile layers and a list of tilesets that point at PNG sheets (`Resources/maps/all/tilesets/...`).
`encounter_zones.py` already reads those files for the ZONE shapes; this reads the same files for the
TILES, so the map view can show the map instead of a flat ground colour. Nothing here invents
geometry: a tile is blitted at its own cell, which is the whole renderer.

WHICH LAYERS ARE DRAWN, and why not all of them. The layer tree is the same shape on every map,
though the layers inside it differ (measured over the 69 maps the zones name):

    floor/          belowFloor 1, floor, floor#whileCRIMSONITE_PHASE_1, aboveFloor 1..3, aboveFloor2
    aboveFloor/     aboveFloorGenerated|level 1..5
    levels/         level 1 .. level 6, trees, belowInnerWalls, innerWalls, outerWalls, mountain 1/2
    objectLayers/   area, interactObjects, locations, characters, spriteLayer (object layers)
    abovePlayer/    abovePlayerGenerated|level 1..5, |trees, |outerWalls
    mapExtensions/  the story's own extension objects
    worldOverlay, aboveWorldOverlay, worldRain/SnowOverlay

* THE TERRAIN IS THE THREE GROUPS `floor`, `aboveFloor` and `levels`, so their leaves are what is
drawn - including the ones whose names say nothing about the terrain: the `trees`, `outerWalls`,
`innerWalls` and `mountain 1/2` layers live INSIDE `levels`;
* everything else is not terrain: the `abovePlayer` layers are the parts drawn OVER the player (tree
tops, roofs) and the `world*` layers are the pause menu's dimming and the weather. Drawing either
buries the ground the tab is asking about, so they are left out;
* a layer is picked by the GROUP it sits in, NOT by the name it starts with. A NAME ALLOW-LIST
(`floor*`, `level*`) is what was here first, and it silently left out every layer the game does not
name that way - which is why "some sprites are still missing from some of the maps" (the user): no
trees on the nineteen maps with a `trees` layer, no walls on the ten with `outerWalls`/`innerWalls`,
no mountains on the two with `mountain 1`;
* A HIDDEN LAYER IS NOT DRAWN IN THE STATE THE MAP IS SAVED IN, and neither is a layer inside a
hidden GROUP. The flag is Tiled authoring state, but the state it records IS the one the map is
drawn in, and the layers next to a base layer are ALTERNATIVES for other states of the story:
`harbor`'s hidden `aboveFloor 2#MESCHER_REALM` (88 tiles of the ghost realm) put long purple lines
across the sand and electricTown's hidden `rainDrops` (960) put white drops all over Donar Island -
neither is in the game (the user: "some maps like woodland harbor, have those strange lines on the
ground, that are not shown in the game ... and donor island also has those strange white dots").
MEASURED over the 69 maps: the hidden layers draw 4781 cells and only 40 of them are cells no visible
layer covers - so the saved state removes 4781 cells of content the game never shows and leaves 40
cells of background, which is what the game has there too;
* ... BUT THEY ARE THE ALTERNATIVES THE STORY SWITCHES BETWEEN, so they are not thrown away: a layer
named `base#condition` is kept as a VARIANT (`variants`, 28 conditions over 23 of the 69 maps, up to
nine on iceTown) and `layer_names(m, "whileEVACUATION")` draws Ice Town as it looks while the town
is evacuated - one version PER BASE NAME, the chosen condition where it exists and the saved state
everywhere else (the user: "how about we keep them, with buttons in the top to flip between them").
The `saved` variant (the default, `variant=None`) is the visible set;
* AND A FEW LAYERS ARE NOT DRAWN IN ANY OF THE STATES - `config.MAP_LAYER_BLACKLIST`, matched by own
name or base name: the rain tiles are weather rather than terrain, and the `world*` overlays are the
pause menu's dimming.

A tile may be BIGGER than the map's grid (48x48 trees on a 16x16 grid); Tiled draws such a tile with
its BOTTOM-LEFT corner at the cell's bottom-left, which is what `_tile_target` reproduces - without it
every tree would sit a cell and a half too high.

The result is cached per map file: it is a few thousand blits, and the map view redraws on every
selection and every resize.
"""

import json
import os
import re

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap

import encounter_zones as ez

from .config import MAP_LAYER_BLACKLIST

# THE GROUPS WHOSE LAYERS ARE TERRAIN, by the name the map's own tree gives them. The order of the
# map file decides what is drawn over what; these only decide which layers are considered.
DRAWN_GROUPS = ("floor", "aboveFloor", "levels")

# (map file, variant) -> QPixmap, because a picture is thousands of blits and the pane repaints on
# every resize
_PICTURE_CACHE = {}
# tileset image path -> QImage, shared by every map that uses the sheet
_SHEET_CACHE = {}
# map path -> whether its FILE names a story state, for `has_states`
_STATE_FILE_CACHE = {}
# a layer name in the Tiled JSON: `"name": "base#condition"`. Only the NAME has to be read - the
# question is whether the file has any at all.
_STATE_NAME = re.compile(rb'"name"\s*:\s*"([^"]*)#([^"]*)"')


def has_states(map_file):
    """Whether this map carries story states, read off the FILE rather than parsed into it.

    THE AREA LIST IS FILTERED BY THIS, which has to answer for all 69 maps at once. Parsing them
    costs 1.0 s of CPU - and `encounter_zones.map_parts` CACHES what it parses, so it would also pin
    all 36 MB of JSON in memory for the rest of the session. Reading the bytes for a
    `"name": "...#..."` (which is all a layer name is) answers the same thing in 0.02 s with warm
    page cache and keeps 69 booleans, and `ui_smoke` checks the two agree on every shipped map.
    `variants()` is still the reader of WHAT the states ARE - this only says yes or no.
    """
    path = ez.find_map_file(map_file)
    if not path:
        return False
    if path not in _STATE_FILE_CACHE:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            _STATE_FILE_CACHE[path] = False
            return False
        found = False
        for match in _STATE_NAME.finditer(data):
            if not _blacklisted(match.group(1).decode("utf-8", "replace")):
                found = True
                break
        _STATE_FILE_CACHE[path] = found
    return _STATE_FILE_CACHE[path]


def variants(m):
    """The story conditions this map has alternative layers for, sorted - the states to flip between."""
    found = set()
    for groups, name, layer, _hidden in _walk(m.get("layers", [])):
        if layer.get("type") != "tilelayer" or not _is_terrain(groups) or _blacklisted(name):
            continue
        _base, _, condition = name.partition("#")
        if condition:
            found.add(condition)
    return sorted(found)


def layer_names(m, variant=None):
    """The tile layers to draw for `variant`, in the map's own order.

    A layer is a candidate when it sits in one of `DRAWN_GROUPS` (at any depth, whatever it is
    called) and is not blacklisted. Candidates are then taken ONE VERSION PER BASE NAME - the part
    before any `#` is what the versions of a layer share - which is what makes a variant a STATE
    rather than another stack of tiles: choosing `whileEVACUATION` draws Ice Town's evacuated floors
    INSTEAD of the plain ones, and the chosen condition where it exists, the saved state everywhere
    else. `variant=None` is the map as saved, i.e. what is visible in it.
    """
    chosen = set()
    versions = {}
    order = []
    for groups, name, layer, hidden in _walk(m.get("layers", [])):
        if layer.get("type") != "tilelayer" or not _is_terrain(groups) or _blacklisted(name):
            continue
        base = name.partition("#")[0]
        if base not in versions:
            versions[base] = []
            order.append(base)
        versions[base].append((name, hidden))
    for base in order:
        wanted = base + "#" + variant if variant else None
        names = [name for (name, _hidden) in versions[base]]
        if wanted in names:
            chosen.add(wanted)
        else:
            chosen.update(name for (name, hidden) in versions[base] if not hidden)
    return [name for groups, name, layer, _h in _walk(m.get("layers", [])) if name in chosen]


def _is_terrain(groups):
    return any(group in DRAWN_GROUPS for group in groups)


def _blacklisted(name):
    return name in MAP_LAYER_BLACKLIST or name.partition("#")[0] in MAP_LAYER_BLACKLIST


def _walk(layers, groups=(), hidden=False):
    """`(ancestor group names, name, layer, hidden)` for every leaf in the tree."""
    for layer in layers:
        off = hidden or layer.get("visible", True) is False
        if layer.get("type") == "group":
            yield from _walk(layer.get("layers", []), groups + (layer.get("name", ""),), off)
        else:
            yield groups, layer.get("name", ""), layer, off


def _sheet(path):
    """The tileset's image, loaded once per file."""
    if path not in _SHEET_CACHE:
        image = QImage(path)
        _SHEET_CACHE[path] = None if image.isNull() else image
    return _SHEET_CACHE[path]


def _tile_source(tileset, sheet, gid):
    """`(sheet, source rect)` for one gid, or None when that tile cannot be drawn."""
    local = gid - tileset["lo"]
    columns = tileset.get("columns") or 0
    if columns <= 0 or local < 0:
        return None
    col, row = local % columns, local // columns
    rect = QRect(col * tileset["tw"], row * tileset["th"], tileset["tw"], tileset["th"])
    if not sheet.rect().contains(rect):
        return None
    return sheet, rect


def _tile_target(m, tileset, x, y):
    """Where a tile goes, in the picture's own pixels - bottom-left aligned for an oversized tile."""
    tw, th = m["tilewidth"], m["tileheight"]
    return QRect(x * tw, (y + 1) * th - (tileset["th"] or th), tileset["tw"] or tw,
                 tileset["th"] or th)


def picture(map_file, variant=None):
    """The composited map as a QPixmap, or None when the map or its sheets cannot be read.

    Cached PER (map, variant), and cached as a PIXMAP rather than as a QImage: this is drawn once per
    paint per tab, and flipping the state has to be instant when it flips back.
    """
    key = (map_file, variant)
    if key not in _PICTURE_CACHE:
        _PICTURE_CACHE[key] = _build(map_file, variant)
    return _PICTURE_CACHE[key]


def _build(map_file, variant=None):
    path = ez.find_map_file(map_file)
    if not path:
        return None
    try:
        m, layers, sets = ez.map_parts(path)
    except (OSError, ValueError, KeyError):
        return None
    # the tilesets' own metadata (columns, tile size) is not in the map's reference - it is in the
    # tileset file, which `map_parts` already resolved
    sets = [_with_columns(path, s) for s in sets]
    drawn = [layers[name] for name in layer_names(m, variant) if name in layers]
    if not drawn or "width" not in m:
        return None
    image = QImage(m["width"] * m["tilewidth"], m["height"] * m["tileheight"],
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        for layer in drawn:
            data = layer.get("data") or []
            for y in range(m["height"]):
                row = y * m["width"]
                for x in range(m["width"]):
                    gid = data[row + x] if row + x < len(data) else 0
                    if not gid:
                        continue
                    tileset = _for_gid(sets, gid)
                    if tileset is None or not tileset["tw"] or not tileset["th"]:
                        continue
                    sheet = _sheet(tileset["path"])
                    if sheet is None:
                        continue
                    found = _tile_source(tileset, sheet, gid)
                    if found is None:
                        continue
                    source, rect = found, found[1]
                    painter.drawImage(_tile_target(m, tileset, x, y), source[0], rect)
    finally:
        painter.end()
    return QPixmap.fromImage(image)


def _for_gid(sets, gid):
    for tileset in sets:
        if tileset["lo"] <= gid < tileset["hi"]:
            return tileset
    return None


def _with_columns(path, tileset):
    """That tileset's sheet path, column count and tile size, read from the tileset's OWN file.

    THE MAP HOLDS ONLY A REFERENCE, and the columns and the image path live in the file it points at -
    while the reference itself cannot be trusted: some maps still say `../all/templateTilesets/`, a
    folder that has since been renamed, which is why `encounter_zones.resolve_tileset` (which knows
    the current folders) is the resolver used here. `map_parts` keeps only the reference's basename,
    so that basename plus `.json` is what is resolved.
    """
    source = ez.resolve_tileset(path, (tileset.get("name") or "") + ".json")
    meta = {}
    if source:
        try:
            meta = json.load(open(source, encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    image = meta.get("image") or ""
    sheet = os.path.normpath(os.path.join(os.path.dirname(source), image)) \
        if source and image else ""
    out = dict(tileset)
    out["path"] = sheet if sheet and os.path.exists(sheet) else ""
    out["columns"] = meta.get("columns")
    out["tw"] = meta.get("tilewidth") or tileset.get("tw")
    out["th"] = meta.get("tileheight") or tileset.get("th")
    return out
