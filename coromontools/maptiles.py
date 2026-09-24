"""The map's own tiles, composited into one picture - the ground the zones are drawn on.

WHERE THE PICTURE COMES FROM. The game's maps are Tiled JSON (`Resources/maps/**/*.json`), each with
its tile layers and a list of tilesets that point at PNG sheets (`Resources/maps/all/tilesets/...`).
`encounter_zones.py` already reads those files for the ZONE shapes; this reads the same files for the
TILES, so the map view can show the map instead of a flat ground colour. Nothing here invents
geometry: a tile is blitted at its own cell, which is the whole renderer.

WHICH LAYERS ARE DRAWN, and why not all of them. Measured on `amishRoute` (97x70), the layer tree is

    floor/          belowFloor 1, floor, aboveFloor 1, (aboveFloor 2#MESCHER_REALM - hidden)
    aboveFloor/     aboveFloorGenerated|level 1, |level 5
    levels/         level 1 .. level 5, trees
    abovePlayer/    abovePlayerGenerated|level 1..5, |trees
    worldOverlay, aboveWorldOverlay

* the FLOOR group is the base the map is built on, so it is always drawn;
* a `level*` layer is where the walkable ground is - every zone marker's own `tileLayer` names one of
  them ("level 1" on `amishRoute`), which is the reason they are the layers the zones can be found in;
* the `abovePlayer*` layers are the parts drawn OVER the player (tree tops, roofs) and
  `worldOverlay` is the pause menu's own dimming overlay. Drawing those buries the ground the whole
  tab is asking about, so they are skipped.

A tile may be BIGGER than the map's grid (48x48 trees on a 16x16 grid); Tiled draws such a tile with
its BOTTOM-LEFT corner at the cell's bottom-left, which is what `_tile_target` reproduces - without it
every tree would sit a cell and a half too high.

The result is cached per map file: it is a few thousand blits, and the map view redraws on every
selection and every resize.
"""

import json
import os

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap

import encounter_zones as ez

# The layer-name prefixes that are drawn, in the game's own order (the order of the map file decides,
# these only decide WHICH layers are considered - see the module docstring for the measurements).
DRAWN_PREFIXES = ("belowFloor", "floor", "aboveFloor", "level")

# map file -> QPixmap, because a picture is thousands of blits and the pane repaints on every resize
_PICTURE_CACHE = {}
# tileset image path -> QImage, shared by every map that uses the sheet
_SHEET_CACHE = {}


def layer_names(m):
    """The tile layers to draw, in the map's own order."""
    chosen = []
    for name, layer in _walk(m.get("layers", [])):
        if layer.get("type") != "tilelayer" or layer.get("visible", True) is False:
            continue
        if name.startswith(DRAWN_PREFIXES):
            chosen.append(name)
    return chosen


def _walk(layers):
    """(name, layer) for every leaf layer in the tree, groups included."""
    for layer in layers:
        if layer.get("type") == "group":
            yield from _walk(layer.get("layers", []))
        else:
            yield layer.get("name", ""), layer


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


def picture(map_file):
    """The composited map as a QPixmap, or None when the map or its sheets cannot be read.

    Cached, and cached as a PIXMAP rather than as a QImage: this is drawn once per paint per tab.
    """
    if map_file in _PICTURE_CACHE:
        return _PICTURE_CACHE[map_file]
    _PICTURE_CACHE[map_file] = _build(map_file)
    return _PICTURE_CACHE[map_file]


def _build(map_file):
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
    drawn = [layers[name] for name in layer_names(m) if name in layers]
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
