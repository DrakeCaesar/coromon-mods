"""_gen_mapnames.py - regenerate `coromontools/mapnames.py` from the game itself.

    python _gen_mapnames.py

The GUI shows areas by prettifying the map FILE name (`volcanoCave_bf3` -> "Volcano Cave Bf 3"),
which is not a name the game has anywhere: the game calls that place **Mount Muspel** and says so
in its own map-name popup, built from `localise('world.map.volcanoCave.name')`. Two sources in
`Resources/resource.car` hold that knowledge, one half each:

  * **which key a map file belongs to**: every map module (`classes.maps.<region>.<mapFile>`)
    calls `setMapName('<key>')` from its own create function. It is the only place the tie is
    written down - the encounter data only has the internal zone token (`VOLCANOCAVE_BF3`) and the
    map bundles only have that same token.
  * **the key's name**: `classes.language.world_en-us` is a table of `world.map.<key>.name` ->
    the English string ("Mount Muspel", and "Berg Muspel"/"Monte Muspel" in the other languages).

So this reads those two and writes a plain Python table. Run it after a game update; nothing at
runtime reads the 238 MB archive, which is why the table is generated rather than looked up.
"""

import contextlib
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import car_extract as ce            # noqa: E402
import luadis                       # noqa: E402
import qr_luadata                   # noqa: E402
from coromontools import paths      # noqa: E402

CAR = os.path.join(paths.GAME_DIR, "Resources", "resource.car")
OUT = os.path.join(paths.PACKAGE_DIR, "mapnames.py")

SET_MAP_NAME = re.compile(r"LOADK\s+A=\d+\s+Bx=\d+ '([A-Za-z_]+)'")

# The archive's entry names carry the extension (`classes.maps.iceCave.frozenCave_1.lu`), so the
# map file is the last segment of the path WITHOUT it. Taking the last dot-segment outright gives
# every map the name "lu", which collapses the whole table into one entry - and it looks like a
# successful run, so the scan count below is reported as well.
LOCALISATION_MODULE = "classes.language.world_en-us.lu"


def _map_file_of(entry_name):
    """`classes.maps.iceCave.frozenCave_1.lu` -> `frozenCave_1`."""
    stem = entry_name[:-3] if entry_name.endswith(".lu") else entry_name
    return stem.rsplit(".", 1)[-1]


def _key_of_map(data):
    """The argument of the module's own `setMapName(...)` call, or None."""
    root, _info = luadis.load_data(data)
    walk = luadis.walk(root)
    for _path, proto in walk:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            luadis.disasm(proto, walk)
        lines = buf.getvalue().splitlines()
        for i, line in enumerate(lines):
            if "'setMapName'" not in line or i + 1 >= len(lines):
                continue
            # the argument is loaded by the instruction after the call setup (the game builds it
            # as `worldHelper:getWorldMapData():setMapName('frozenCave')`)
            match = SET_MAP_NAME.search(lines[i + 1])
            if match:
                return match.group(1)
    return None


def keys_by_map(car):
    """{map file: localisation key} from every map module in the archive."""
    out = {}
    scanned = skipped = 0
    for typ, off, name in ce.parse_toc(car):
        if not name.startswith("classes.maps.") or "#" in name:
            continue
        scanned += 1
        map_file = _map_file_of(name)
        _etype, _usize, _csize, data = ce.read_entry(car, typ, off)
        if not data or data[:4] != b"\x1bLua":
            skipped += 1
            continue
        key = _key_of_map(data)
        if key:
            out[map_file] = key
        else:
            skipped += 1
    return out, scanned, skipped


def names_by_key(car):
    """{key: name} from the game's own English world localisation."""
    for typ, off, name in ce.parse_toc(car):
        if name != LOCALISATION_MODULE:
            continue
        _etype, _usize, _csize, data = ce.read_entry(car, typ, off)
        result = qr_luadata.run_data(data)
        table = result[0] if isinstance(result, list) and result else result
        out = {}
        for key, value in dict(table).items():
            if (key.startswith("world.map.") and key.endswith(".name")
                    and isinstance(value, str)):
                out[key[len("world.map."):-len(".name")]] = value
        return out
    return {}


HEADER = '''"""Area names as the game shows them - GENERATED, do not edit by hand.

Regenerate with:

    python _gen_mapnames.py

`KEY_BY_MAP` is read from each map module's own `setMapName(...)` call and `NAME_BY_KEY` from
`classes.language.world_en-us`, both inside `Resources/resource.car`. `area()` is what the tabs
call; it mirrors the in-game overlay's label (`coromon-tools/ingame/area.py`) so the window and
the game agree on what a place is called.

The point of the table: the GUI used to label areas by prettifying the map FILE name, so
`volcanoCave_bf3` read "Volcano Cave Bf 3" - a name that appears nowhere in the game (the string
"Vulcano" does not exist in it at all) - while the game calls that place "Mount Muspel" and shows
that in its own popup on entering.
"""

from .text import pretty

# {map file: localisation key}, from each map module's `setMapName(...)`  (%d entries)
KEY_BY_MAP = {
%s}

# {key: English name}, from `world.map.<key>.name` in the game's world localisation  (%d entries)
NAME_BY_KEY = {
%s}


def area(map_file):
    """The area as the game names it: ``volcanoCave_bf3`` -> "Mount Muspel BF3".

    Three cases, and the third is why this is not a plain lookup:

      * the file starts with its own key -> the name plus the file's tail, which is the floor:
        ``volcanoCave_bf3`` -> "Mount Muspel BF3";
      * the file IS the key -> the name alone;
      * the file is named after something else (``library_f1`` inside ``amishTown``) -> the tail is
        not part of the name, so the name comes first and the file identifies it in brackets:
        "Amish Town (Library F1)". Two zones the game calls the same thing stay distinguishable,
        which a list of tickable areas needs.
    """
    key = KEY_BY_MAP.get(map_file)
    name = NAME_BY_KEY.get(key) if key else None
    # A placeholder is not a name: the debug maps localise to "?" (`world.map.unknown.name`), and
    # "? (Developer Home Area)" reads worse than the file name does.
    if not name or name in ("?", "???"):
        return pretty(map_file)              # unmapped map: the old behaviour, not an error
    if key == map_file:
        return name
    prefix = key + "_"
    if map_file.startswith(prefix):
        tail = map_file[len(prefix):]
        if tail:
            return "%%s %%s" %% (name, tail.replace("_", " ").upper())
    return "%%s (%%s)" %% (name, pretty(map_file))
'''


def _table(pairs, width=88):
    lines = []
    for key, value in sorted(pairs.items()):
        lines.append('    %-28s %s,' % ('"%s":' % key, '"%s"' % value))
    return "\n".join(lines)


def main():
    if not os.path.exists(CAR):
        print("cannot find the archive at", CAR)
        return 2
    keys, scanned, skipped = keys_by_map(CAR)
    names = names_by_key(CAR)
    print("map modules scanned:  %d   (%d with a key, %d without)"
          % (scanned, len(keys), skipped))
    print("localised names:      %d" % len(names))
    unmapped = sorted(k for k in keys if keys[k] not in names)
    print("keys with no English name: %d %s" % (len(unmapped), unmapped[:6]))
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(HEADER % (len(keys), _table(keys), len(names), _table(names)))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
