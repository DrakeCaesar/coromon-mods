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

WHERE IT DOES NOT APPLY, and the output says so rather than guessing:
  * a tileset whose tiles are bigger than the map grid (48 px trees over 16 px cells): one tile
    spans several cells, so a run of same-tileset cells is not a region.
  * water and cave markers, which carry NO `tileLayer` property at all, and often sit on a tile that
    is empty in every layer. Those are reported as unplaced.

    python encounter_zones.py                 # writes encounter_zones.html
    python encounter_zones.py ICE             # only areas whose name contains ICE

The output is one self-contained page: a map per area with the patches coloured and lettered, and
the encounter table for each zone under it.
"""

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


# ==============================================================================================
# READING THE MAPS
# ==============================================================================================
def find_map_file(name):
    hits = glob.glob(os.path.join(MAPS, "**", name + ".json"), recursive=True)
    return hits[0] if hits else None


def walk_layers(layers):
    for l in layers:
        if l.get("type") == "group":
            yield from walk_layers(l.get("layers", []))
        else:
            yield l


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
    """(map dict, {layer name: layer}, tileset ranges as dicts with lo/hi/name/tw/th)."""
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
    return m, layers, sets


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


def zone_patches(path):
    """(map, layers, {zone: {"patches": [set(tiles)], "unplaced": [...], "note": str|None}})"""
    m, marks = markers(path)
    _, layers, sets = map_parts(path)
    out = {}
    for zone, seeds in marks.items():
        entry = {"patches": [], "unplaced": [], "note": None}
        for (tx, ty, layer_name) in seeds:
            layer = layers.get(layer_name) if layer_name else None
            if layer is None:
                entry["unplaced"].append((tx, ty, "no tileLayer named"))
                continue
            gid = layer["data"][ty * m["width"] + tx] if 0 <= tx < m["width"] \
                and 0 <= ty < m["height"] else 0
            ts = next((s for s in sets if s["lo"] <= gid < s["hi"]), None)
            if ts is None:
                entry["unplaced"].append((tx, ty, "empty tile on %s" % layer_name))
                continue
            if ts["tw"] != m["tilewidth"] or ts["th"] != m["tileheight"]:
                entry["note"] = ("%s tiles are %sx%s on a %sx%s grid, so a region cannot be read "
                                 "off the grid" % (ts["name"], ts["tw"], ts["th"],
                                                   m["tilewidth"], m["tileheight"]))
                entry["unplaced"].append((tx, ty, "multi-cell tile"))
                continue
            tiles = patch_tiles(layers, m, (tx, ty, layer_name), ts)
            if tiles:
                entry["patches"].append(tiles)
            else:
                entry["unplaced"].append((tx, ty, "not on %s" % ts["name"]))
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
