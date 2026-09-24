#!/usr/bin/env python3
"""encounter_zones.py - WHICH grass is "Grass B".

The wiki lists encounter rates per area as "Grass A / Grass B / ..." and never says which patch of
grass each one is. The game does know: the maps carry `grassArea` objects whose properties name a
`zoneUID`, and those names ARE the wiki's letters - `AMISHROUTE_A`, `AMISHROUTE_B`, `SWAMP_2_WATER`.
So this reads the maps, works out which tiles each zone covers, and draws it.

HOW A MARKER BECOMES A PATCH. A marker is a single tile inside a patch of grass, and one zone
usually has several markers (one per patch). The patch is taken to be the 4-connected run of tiles
belonging to the SAME TILESET as the tile the marker sits on - the grass tileset for grass, the water
tileset for water. That rule is a RECONSTRUCTION, not the game's own code, and it is checked two
ways: it produces contiguous patches of a sensible size, and a zone's patches come out disjoint and
spread across the route exactly where distinct grass areas are. It is exact for the grass zones,
which are what the question is about, because grass.json is a 16 px tileset on a 16 px grid, so one
tile is one map cell.

WHICH TILE THAT IS takes three steps, because a marker's own cell is often not on its grass at all
(`marker_tile`, `off_layer`, `nearest_tile`):

  1. the tile on the layer the marker's `tileLayer` property names - and ONLY IF it is on the zone's
     own terrain (see below). The property is an author's note and it is sometimes stale;
  2. otherwise the nearest layer of the same FAMILY (`level 1` -> `level 2`, never `floor`) that has
     a tile there - `oasisCave_3`'s (35,12) says "level 1" while its grass is one layer up;
  3. otherwise the nearest cell within `NEIGHBOUR_RADIUS` that holds the zone's own terrain, because
     markers are placed ON a patch and the cell under them can be the rock in the middle of it -
     `dojoGrounds` (27,64) sits on a `rockWalls` cell inside a 74-cell grass region, and (7,9) sits
     one tile ABOVE its 11-cell region.

THE ZONE'S OWN TERRAIN is the tileset the most of its markers sit on (`_own_tilesets`), which is what
keeps a stray marker off the rock: `dojoGrounds` A has six markers on `grass` and one on `rockWalls`,
so the rock one is looked for elsewhere - before this rule that single marker drew a five-cell patch
of rock as if it were grass (the user: "in dojo grounds, one area is marked at the rocks, not the
grass"). A zone whose markers name no tile layer at all (every `_WATER` zone) has no terrain to look
for and keeps its bare cells.

WHERE IT DOES NOT APPLY, and the output says so rather than guessing:
  * a tileset whose tiles are bigger than the map grid (48 px trees over 16 px cells): one tile
    spans several cells, so a run of same-tileset cells is not a region.
  * water and cave markers, which carry NO `tileLayer` property at all, and often sit on a tile that
    is empty in every layer. Those are kept as bare CELLS, which is not a failure: a water zone marks
    its area with hundreds of them (`SWAMP_3_WATER` 409, `WATERROUTE_4_WATER` 170), so the cells ARE
    the shape - `zone_blocks` merges them, together with whatever patches resolved, into the blocks the
    window draws (170 cells -> two blocks, 409 -> 41), CORNER-TOUCHING CELLS INCLUDED so that two
    patches meeting at a corner are one block with one name on it, and `block_outline` gives each
    block the border that follows it.

    python encounter_zones.py                 # writes encounter_zones.html
    python encounter_zones.py ICE             # only areas whose name contains ICE

The output is one self-contained page: a map per area with the patches coloured and lettered, and
the encounter table for each zone under it.
"""

import collections
import glob
import html
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(BASE), "Resources")
ZONES_JSON = os.path.join(RES, "data", "json", "encounterZones.json")
MAPS = os.path.join(RES, "maps")
OUT = os.path.join(BASE, "encounter_zones.html")

TILE_PX = 6          # drawn size of one map cell
PAD = 6

# HOW FAR A MARKER MAY SIT FROM THE PATCH IT NAMES, in cells. Markers are placed ON a patch, but not
# always on a tile OF it: `dojoGrounds` (27,64) is a marker in the middle of a 74-cell grass region
# standing on the rock inside it, and (7,9) is one tile above its own 11-cell region. Measured over
# the 69 maps the zones name, every marker that needed this was within two cells of its patch.
NEIGHBOUR_RADIUS = 3


# ==============================================================================================
# READING THE MAPS
# ==============================================================================================
def find_map_file(name):
    """The map's JSON, by name - cached, because the glob walks 83 MB of map paths."""
    if name not in _PATH_CACHE:
        hits = glob.glob(os.path.join(MAPS, "**", name + ".json"), recursive=True)
        _PATH_CACHE[name] = hits[0] if hits else None
    return _PATH_CACHE[name]


def walk_layers(layers):
    for l in layers:
        if l.get("type") == "group":
            yield from walk_layers(l.get("layers", []))
        else:
            yield l


# Both of these read a file that does not change while the window is open, and the GUI reads a map
# from several places (see `map_parts`), so the answers are kept.
_PATH_CACHE = {}      # map name -> path or None
_PARTS_CACHE = {}     # map path -> (map, layers, tilesets)


def resolve_tileset(map_path, source):
    """The file for a tileset the map references.

    BY NAME, NOT BY PATH, when the path does not resolve: some maps still point at
    `../all/templateTilesets/`, a folder that has since been renamed to `templates/`. The gid ranges
    only need the ORDER of the tilesets, which the map itself gives, so a tileset that cannot be
    found is not fatal - it is recorded with unknown tile size, which marks its zones as
    unreadable rather than silently guessing at their shape.
    """
    direct = os.path.normpath(os.path.join(os.path.dirname(map_path), source))
    if os.path.exists(direct):
        return direct
    name = os.path.basename(source)
    for folder in ("templates", "externalTilesets", "tilesets", "objects"):
        cand = os.path.join(MAPS, "all", folder, name)
        if os.path.exists(cand):
            return cand
    hits = glob.glob(os.path.join(MAPS, "**", name), recursive=True)
    return hits[0] if hits else None


def map_parts(path):
    """(map dict, {layer name: layer}, tileset ranges) - CACHED per path.

    The GUI asks for a map several times over - `markers` reads it, `zone_patches` reads it again,
    `maptiles` reads it once more for the tiles - and each read parses a megabyte of JSON for the same
    answer. Nothing in here is mutated by any caller, so one read is enough.
    """
    if path in _PARTS_CACHE:
        return _PARTS_CACHE[path]
    m = json.load(open(path, encoding="utf-8"))
    layers = {l["name"]: l for l in walk_layers(m.get("layers", [])) if l.get("type") == "tilelayer"}
    first = [t["firstgid"] for t in m.get("tilesets", [])] + [1 << 30]
    sets = []
    for i, t in enumerate(m.get("tilesets", [])):
        src = os.path.basename(t.get("source", ""))
        ts = {}
        ts_path = resolve_tileset(path, t.get("source", ""))
        if ts_path:
            try:
                ts = json.load(open(ts_path, encoding="utf-8"))
            except (OSError, ValueError):
                ts = {}
        sets.append({"lo": t["firstgid"], "hi": first[i + 1],
                     "name": src.replace(".json", ""),
                     "tw": ts.get("tilewidth"), "th": ts.get("tileheight")})
    _PARTS_CACHE[path] = (m, layers, sets)
    return _PARTS_CACHE[path]


def markers(path):
    """{zone name: [(tileX, tileY, tileLayer or None)]} from the grassArea objects."""
    m, layers, _ = map_parts(path)
    tw = m["tilewidth"]
    out = {}
    for l in walk_layers(m.get("layers", [])):
        if l.get("type") != "objectgroup":
            continue
        for o in l.get("objects", []):
            props = {p.get("name"): p.get("value") for p in o.get("properties") or []}
            if "zoneUID" not in props:
                continue
            out.setdefault(props["zoneUID"], []).append(
                (int(o.get("x", 0)) // tw, int(o.get("y", 0)) // tw, props.get("tileLayer")))
    return m, out


def patch_tiles(layers, m, seed, tileset):
    """The connected tiles of that tileset, starting at seed. Empty when the seed is not on one."""
    w, h = m["width"], m["height"]
    data = layers[seed[2]]["data"]
    lo, hi = tileset["lo"], tileset["hi"]

    def inside(x, y):
        return 0 <= x < w and 0 <= y < h and lo <= data[y * w + x] < hi

    if not inside(seed[0], seed[1]):
        return set()
    seen, stack = set(), [(seed[0], seed[1])]
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or not inside(x, y):
            continue
        seen.add((x, y))
        stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    return seen


def family_word(name):
    """The first word of a layer's name: `level 1` and `level 2` are one family, `floor` is not."""
    return (name or "").split(" ")[0].split("|")[0]


def tileset_for(sets, gid):
    """The tileset `gid` belongs to, or None when no range covers it."""
    return next((s for s in sets if s["lo"] <= gid < s["hi"]), None)


def marker_tile(layers, m, sets, tx, ty, names, allowed=None):
    """`(layer name, gid)` for the first of `names` that holds a tile at that cell, or `(None, 0)`.

    `allowed` is a set of TILESET NAMES: a tile whose tileset is not in it is skipped, which is what
    keeps a marker off the rock it happens to stand on. An empty `allowed` means "anything goes",
    which is what a zone with no known terrain needs.
    """
    if not 0 <= tx < m["width"] or not 0 <= ty < m["height"]:
        return None, 0
    for name in names:
        layer = layers.get(name)
        if layer is None:
            continue
        gid = layer["data"][ty * m["width"] + tx]
        if not gid:
            continue
        tileset = tileset_for(sets, gid)
        if allowed and (tileset is None or tileset["name"] not in allowed):
            continue
        return name, gid
    return None, 0


def off_layer(order, layer_name):
    """The layers to fall back on for a marker on `layer_name`: its family, NEAREST FIRST.

    Nearest by the map's own draw order, below before above, and the family is the first word of the
    name - so a `level 1` marker looks at `level 2`, `level 3`, ... and never at `floor` or `trees`.
    """
    family = family_word(layer_name)
    names = [name for name in order if name != layer_name and family_word(name) == family]
    if layer_name in order:
        home = order.index(layer_name)
        names.sort(key=lambda name: (abs(order.index(name) - home), order.index(name)))
    return names


def nearest_tile(layers, m, sets, tx, ty, names, wanted, radius=NEIGHBOUR_RADIUS):
    """The nearest cell within `radius` holding the zone's own terrain on one of `names`.

    Returns `(x, y, layer name, gid)` or None, searching ring by ring out from the marker so the
    closest tile wins, and top-left first within a ring so the answer never wanders between runs.
    """
    for distance in range(1, radius + 1):
        ring = sorted((tx + dx, ty + dy)
                      for dx in range(-distance, distance + 1)
                      for dy in range(-distance, distance + 1)
                      if max(abs(dx), abs(dy)) == distance)
        for (x, y) in ring:
            name, gid = marker_tile(layers, m, sets, x, y, names, allowed=wanted)
            if gid:
                return x, y, name, gid
    return None


def own_tilesets(seeds, layers, m, sets):
    """The tilesets a zone is really on: those the MOST of its markers sit on.

    BY COUNT, not by "all of them": `dojoGrounds` A marks six patches on the `grass` tileset and one
    on `rockWalls`, and that one marker is the one whose cell is the rock inside a grass patch. Taking
    every named tileset as the zone's terrain would let it keep drawing five cells of rock as grass
    (the user: "in dojo grounds, one area is marked at the rocks, not the grass"). A tie keeps every
    tileset that tied, so a zone split evenly between two kinds of ground is left alone.
    """
    counted = collections.Counter()
    for (tx, ty, layer_name) in seeds:
        if not layer_name:
            continue
        layer = layers.get(layer_name)
        if layer is None:
            continue
        tileset = tileset_for(sets, layer["data"][ty * m["width"] + tx])
        if tileset is not None:
            counted[tileset["name"]] += 1
    if not counted:
        return set()
    most = max(counted.values())
    return {name for name, count in counted.items() if count == most}


def zone_patches(path):
    """(map, layers, {zone: {"patches": [set(tiles)], "unplaced": [...], "note": str|None}})"""
    m, marks = markers(path)
    _, layers, sets = map_parts(path)
    order = [l["name"] for l in walk_layers(m.get("layers", [])) if l.get("type") == "tilelayer"]
    out = {}
    for zone, seeds in marks.items():
        entry = {"patches": [], "unplaced": [], "note": None}
        own = own_tilesets(seeds, layers, m, sets)
        for (tx, ty, layer_name) in seeds:
            if not layer_name:
                entry["unplaced"].append((tx, ty, "no tileLayer named"))
                continue
            cell = (tx, ty)
            name, gid = marker_tile(layers, m, sets, tx, ty, [layer_name], allowed=own)
            if not gid:
                # ... the family layers, and then the cells AROUND the marker (see `nearest_tile`)
                family = off_layer(order, layer_name)
                name, gid = marker_tile(layers, m, sets, tx, ty, family, allowed=own)
                if not gid and own:
                    # THE MARKER'S OWN LAYER IS SEARCHED TOO, and first: the rock it may be standing
                    # on is a cell INSIDE the patch, so the grass is one cell away ON THE SAME LAYER -
                    # `dojoGrounds` (27,64) is inside a 74-cell level-1 grass region.
                    near = nearest_tile(layers, m, sets, tx, ty,
                                        [layer_name] + family, own)
                    if near is not None:
                        tx, ty, name, gid = near
            if not gid:
                why = "empty tile on %s" % layer_name if cell == (tx, ty) \
                    else "on other terrain"
                entry["unplaced"].append((cell[0], cell[1], why))
                continue
            ts = tileset_for(sets, gid)
            if ts is None:
                entry["unplaced"].append((cell[0], cell[1], "empty tile on %s" % name))
                continue
            if ts["tw"] != m["tilewidth"] or ts["th"] != m["tileheight"]:
                entry["note"] = ("%s tiles are %sx%s on a %sx%s grid, so a region cannot be read "
                                 "off the grid" % (ts["name"], ts["tw"], ts["th"],
                                                   m["tilewidth"], m["tileheight"]))
                entry["unplaced"].append((cell[0], cell[1], "multi-cell tile"))
                continue
            tiles = patch_tiles(layers, m, (tx, ty, name), ts)
            if tiles and set(tiles) not in [set(p) for p in entry["patches"]]:
                entry["patches"].append(tiles)
            elif not tiles:
                entry["unplaced"].append((cell[0], cell[1], "not on %s" % ts["name"]))
        out[zone] = entry
    return m, layers, out


# ==============================================================================================
# DRAWING
# ==============================================================================================
# One colour per LETTER, so B is the same colour in every area - the page is meant to be skimmed
# area by area and a colour that means something different each time is worse than no colour.
COLOURS = {
    "A": "#3fa34d", "B": "#e07b39", "C": "#3d7dd8", "D": "#a356d0",
    "WATER": "#39b7c4", "SPECIAL": "#c8443f",
}


def colour_for(zone):
    for key, colour in COLOURS.items():
        if zone.endswith("_" + key) or zone == key:
            return colour
    return "#8a8a8a"


def span_path(rows_of_x, scale):
    """One SVG path for a set of cells, built from horizontal runs.

    RUNS, NOT CELLS, and it is not a micro-optimisation: drawn one `<rect>` per cell the first
    version of this page came to 110 MB, because a grass region is thousands of cells and every map
    has several. As runs it is one `<path>` per zone, and the page is small enough to open.
    """
    d = []
    for (y, x0, x1) in runs_by_row(rows_of_x):
        w = (x1 - x0) * scale
        d.append("M%g %gh%gv%gh-%gz" % (x0 * scale, y * scale, w, scale, w))
    return "".join(d)


def runs_by_row(cells_per_row):
    """[(y, x0, x1)] - horizontal runs in a {y: [x, ...]} map.

    Shared with the GUI, which draws the same shapes on a Tk canvas: a leaky second copy of "how a
    cell set becomes rectangles" is how the page and the window end up disagreeing about a patch.
    """
    out = []
    for y in sorted(cells_per_row):
        xs = sorted(cells_per_row[y])
        start = prev = xs[0]
        for x in xs[1:] + [None]:
            if x is not None and x == prev + 1:
                prev = x
                continue
            out.append((y, start, prev + 1))
            if x is not None:
                start = prev = x
    return out


def cells_by_row(tiles):
    rows = {}
    for (x, y) in tiles:
        rows.setdefault(y, []).append(x)
    return rows


def components(cells, diagonal=False):
    """The connected groups of a cell set - one BLOCK per group.

    WHY IT IS NEEDED, measured on the shipped maps: a zone whose markers name no tile layer marks its
    area with MANY cells - `SWAMP_3_WATER` has 409 of them, `WATERROUTE_4_WATER` 170 - and those cells
    ARE the shape. Drawn one at a time they are a field of dots; merged they are the body of water the
    player means by "the water zone" (409 cells -> 41 blocks, 170 -> 2, `DESERTTOWN_WATER`'s 96 -> 1).

    `diagonal` also joins groups that only TOUCH BY A CORNER, which is how the window draws them: two
    patches of the same zone meeting at a corner are one area to the player, and each of them would
    otherwise carry its own copy of the zone's name (the user: "if groups touch by corner - we could
    consider that as a single group as well, to reduce the number of labels").
    """
    steps = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    if diagonal:
        steps += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    todo = set(cells)
    out = []
    while todo:
        seed = todo.pop()
        block, stack = {seed}, [seed]
        while stack:
            x, y = stack.pop()
            for step in steps:
                neighbour = (x + step[0], y + step[1])
                if neighbour in todo:
                    todo.discard(neighbour)
                    block.add(neighbour)
                    stack.append(neighbour)
        out.append(block)
    return out


def zone_blocks(entry):
    """The merged blocks a zone is drawn as, biggest first - its patches and its bare cells together.

    A patch is already a connected run of the zone's own tileset (`patch_tiles`), and a marker that
    never resolved to one is a cell of the shape itself, so the union of the two split into CONNECTED
    (corner-touching included) groups is the zone as it should be drawn: `waterRoute_4`'s water becomes
    two blocks (151 and 19 cells) where its markers alone were 170 loose cells, and its route eight
    where they were 340.
    """
    cells = set()
    for tiles in entry["patches"]:
        cells |= set(tiles)
    cells |= {(x, y) for (x, y, _why) in entry["unplaced"]}
    return sorted(components(cells, diagonal=True), key=len, reverse=True)
    cells = set()
    for tiles in entry["patches"]:
        cells |= set(tiles)
    cells |= {(x, y) for (x, y, _why) in entry["unplaced"]}
    return sorted(components(cells), key=len, reverse=True)


def block_outline(block):
    """The sides of a block that face OUT, as segments in cells - the merged border.

    Per CELL SIDE, not per cell and not per bounding box: a side whose neighbour belongs to the same
    block is left out, so the border follows an L-shaped or holed region exactly and two adjacent cells
    share one line instead of drawing two. Returns `(horizontal, vertical)`, each a list of
    `(x0, y, x1)` / `(x, y0, y1)`.
    """
    horizontal, vertical = [], []
    for (x, y) in block:
        if (x, y - 1) not in block:
            horizontal.append((x, y, x + 1))
        if (x, y + 1) not in block:
            horizontal.append((x, y + 1, x + 1))
        if (x - 1, y) not in block:
            vertical.append((x, y, y + 1))
        if (x + 1, y) not in block:
            vertical.append((x + 1, y, y + 1))
    return horizontal, vertical


def terrain_cells(m, layers):
    """Every filled cell of every `level*` layer - the map's footprint, as a set of (x, y)."""
    w, h = m["width"], m["height"]
    cells = set()
    for name, l in layers.items():
        if not name.startswith("level"):
            continue
        data = l["data"]
        for y in range(h):
            base = y * w
            for x in range(w):
                if data[base + x]:
                    cells.add((x, y))
    return cells


_MAP_CACHE = {}


def zone_map(map_file):
    """(map, layers, zones) for a map NAME ("amishRoute"), or None - cached by name.

    The GUI calls this on every selection change, and re-reading a megabyte of JSON to redraw a
    rectangle would make selecting rows feel broken.
    """
    if map_file not in _MAP_CACHE:
        path = find_map_file(map_file)
        _MAP_CACHE[map_file] = zone_patches(path) if path else None
    return _MAP_CACHE[map_file]


def svg_for(m, layers, zones, width_limit=TILE_PX * 220):
    w, h = m["width"], m["height"]
    scale = TILE_PX
    while w * scale > width_limit and scale > 1:
        scale -= 1
    W, H = w * scale, h * scale
    parts = ['<svg viewBox="0 0 %d %d" width="%d" height="%d" '
             'xmlns="http://www.w3.org/2000/svg" style="background:#15171a">' % (W, H, W, H)]

    # the ground: every filled cell of every level layer, as one path
    parts.append('<path fill="#2a2f36" d="%s"/>'
                 % span_path(cells_by_row(terrain_cells(m, layers)), scale))

    for zone, entry in sorted(zones.items()):
        colour = colour_for(zone)
        letter = zone.rsplit("_", 1)[-1]
        every = set()
        for tiles in entry["patches"]:
            every |= tiles
        if every:
            parts.append('<path fill="%s" opacity="0.75" d="%s"/>'
                         % (colour, span_path(cells_by_row(every), scale)))
        # a letter on every patch, so a zone that owns several of them is still identifiable, and a
        # bigger one on the first patch of each zone
        for i, tiles in enumerate(entry["patches"]):
            xs = [t[0] for t in tiles]
            ys = [t[1] for t in tiles]
            cx = (min(xs) + max(xs) + 1) / 2 * scale
            cy = (min(ys) + max(ys) + 1) / 2 * scale
            size = max(9, scale * (2.4 if i == 0 else 1.6))
            parts.append('<text x="%.0f" y="%.0f" font-family="monospace" font-size="%.0f" '
                         'font-weight="bold" fill="#fff" stroke="#000" stroke-width="0.6" '
                         'text-anchor="middle" dominant-baseline="middle">%s</text>'
                         % (cx, cy, size, html.escape(letter)))
        for (x, y, why) in entry["unplaced"]:
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="%s" '
                         'stroke-width="2"><title>%s</title></rect>'
                         % (x * scale, y * scale, scale, scale, colour, html.escape(why)))
    parts.append("</svg>")
    return "".join(parts)


# ==============================================================================================
# THE TABLES
# ==============================================================================================
def zone_rows(entry):
    """The encounter table for one zone, as (share, steps, monsters)."""
    encs = entry.get("encounters") or []
    total = sum(e.get("stepsWithEncounter") or 0 for e in encs) or 1
    rows = []
    for e in encs:
        monsters = []
        for mon in e.get("monsters") or []:
            lvl = "%s-%s" % (mon.get("minLevel"), mon.get("maxLevel")) \
                if mon.get("minLevel") != mon.get("maxLevel") else str(mon.get("minLevel"))
            monsters.append("%s L%s%s" % (mon.get("monsterUID"), lvl,
                                          " (crimsonite)" if mon.get("crimsonite") else ""))
        share = 100.0 * (e.get("stepsWithEncounter") or 0) / total
        each = share / len(monsters) if monsters else 0.0
        rows.append((share, each, e.get("stepsWithEncounter"), e.get("name"), monsters))
    rows.sort(reverse=True)
    return rows


def main():
    contains = sys.argv[1].upper() if len(sys.argv) > 1 else None
    data = json.load(open(ZONES_JSON, encoding="utf-8"))

    sections, index = [], []
    for area in sorted(data):
        zone_list = data[area]
        if not zone_list:
            continue
        map_file = zone_list[0].get("mapFile") or area
        path = find_map_file(map_file)
        if path is None:
            continue
        if contains and contains not in area.upper():
            continue
        m, layers, zones = zone_patches(path)
        drawn = {z: v for z, v in zones.items() if v["patches"] or v["unplaced"]}
        index.append((area, path, len(drawn)))

        rows = []
        for z in zone_list:
            name = z["name"]
            entry = zones.get(name, {"patches": [], "unplaced": [], "note": None})
            colour = colour_for(name)
            placed = sum(len(p) for p in entry["patches"])
            spots = ", ".join("(%d,%d)" % (p[0], p[1]) for tiles in entry["patches"]
                              for p in [(min(t[0] for t in tiles), min(t[1] for t in tiles))])
            table = "".join(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%.1f%%</td><td>%.1f%%</td></tr>"
                % (html.escape(e_name or "-"), steps, "<br>".join(html.escape(x) for x in mons),
                   share, each)
                for (share, each, steps, e_name, mons) in zone_rows(z))
            rows.append(
                "<h3 style=\"color:%s\">%s <span class=sub>%d patch(es), %d tiles%s</span></h3>"
                "%s%s<table class=t><tr><th>slot</th><th>weight</th><th>monsters</th>"
                "<th>slot share</th><th>each</th></tr>%s</table>"
                % (colour, html.escape(name), len(entry["patches"]), placed,
                   (" - at " + spots) if spots else "",
                   ("<p class=warn>not placed: %s</p>" % html.escape(
                       "; ".join("(%d,%d) %s" % (tx, ty, why)
                                 for tx, ty, why in entry["unplaced"]))
                    if entry["unplaced"] else ""),
                   ("<p class=warn>%s</p>" % html.escape(entry["note"])) if entry["note"] else "",
                   table or "<tr><td colspan=6>-</td></tr>"))

        sections.append(
            "<section><h2 id=\"%s\">%s <span class=sub>%s</span></h2>%s%s</section>"
            % (html.escape(area), html.escape(area), html.escape(map_file),
               svg_for(m, layers, drawn), "".join(rows)))

    page = """<!doctype html><meta charset="utf-8"><title>Coromon encounter zones</title>
<style>
 body{background:#101216;color:#ddd;font:13px/1.5 system-ui,sans-serif;margin:0;padding:16px}
 h1{font-size:18px} h2{font-size:15px;border-bottom:1px solid #333;padding-top:14px;margin-bottom:6px}
 h3{font-size:13px;margin:16px 0 4px}
 .sub{color:#888;font-weight:400;font-size:12px}
 .warn{color:#e0a33a;margin:4px 0}
 table.t{border-collapse:collapse;margin:4px 0 0}
 table.t th,table.t td{border:1px solid #2c3138;padding:2px 6px;text-align:left}
 table.t th{background:#191d23;color:#aaa;font-weight:600}
 nav a{color:#7fb2ff;margin-right:10px;text-decoration:none}
</style>
<h1>Coromon encounter zones - which grass is which</h1>
<p class=sub>The letter in each coloured patch is the zone's own name (the wiki's &quot;Grass B&quot;
is the game's <code>_B</code>). Patch shapes are read from the map tiles; see the tool for how.</p>
<nav>%s</nav>%s
""" % ("".join('<a href="#%s">%s</a>' % (html.escape(a), html.escape(a)) for a, _, _ in index),
       "".join(sections))

    open(OUT, "w", encoding="utf-8").write(page)
    print("wrote %s - %d areas, %.1f KB" % (OUT, len(index), os.path.getsize(OUT) / 1024.0))
    for area, path, n in index:
        print("   %-22s %-46s %d zones" % (area, os.path.relpath(path, RES), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
