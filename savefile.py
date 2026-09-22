#!/usr/bin/env python3
"""
savefile.py - which areas you have actually been to, read straight from your save.

The grind tool can only rank areas you have, and ticking 69 boxes by hand is tedious.
The game itself knows which maps you have set foot in, so lend it from there.

Where the save is
-----------------
Not in the game folder, and not under a "Coromon" folder in your user profile: Solar2D
keeps its store in its own preferences database,

    %LOCALAPPDATA%\\TRAGsoft\\Coromon\\.system\\CoronaPreferences.sqlite

with one `saveslot_self_<n>` row per save slot plus a `saveslot_self_<n>_auto` row for the
autosave. Each row is JSON: `versionId`, `metadata` (including a `dateTime`) and
`encryptedData`.

The obfuscation
---------------
`encryptedData` is base64 of the save's JSON XORed against a repeating 332-byte keystream,
offset by one byte. The keystream is not a literal in `resource.car` and is not stored in the
preferences, so it was recovered from the running game instead: the module
`classes.modules.encryptionHelper` has an `encrypt` that is deterministic and preserves
length, so XORing its output for a known input of 4096 'A's against that input leaves the
keystream. `KEY` below is the result for this installation. That makes it machine-specific -
if the tool reports it cannot read the save, the key has to be re-derived on the machine that
wrote it. `OFFSET` is not a fudge factor: the same probe shows the payload sits one byte into
the buffer, so the keystream is applied one byte out of phase.

What is read
------------
The decrypted save has 37 top-level keys; the one that matters here is

    stats.VISITED_MAPS     {"desertRoute_4": 6, "pyramidArea": 5, ...}

a map name mapped to the number of times you have been there. Names in the encounter data do
not always match the save exactly (`electricCave_f1` vs `electricCave_f1_A`), so matching is
by exact name first and then by prefix in either direction.

Not everything in a save maps to an encounter area, so `unmatched()` reports the visits that
no area claims - useful for spotting a mapping the matching rule missed.

`monster_record()` READS THE DEX instead, out of the same decrypted save:

    stats.MONSTERS_OWNED   {"GHOST_CAT_1": {"A": True, "B": True, "C": True}, ...}
    stats.MONSTERS_SEEN    {"GHOST_CAT_1": {"A": True}, ...}

a Coromon UID mapped to the potential categories you have it in - `A` standard, `B` potent,
`C` perfect. That is exactly what the game's own database screen shows: three tabs, one per
category, each entry caught (`MONSTERS_OWNED`), seen-but-not-caught (`MONSTERS_SEEN`) or
unknown. The game's own accessors are `playerStats:hasOwnedMonster(uid, category)` and
`hasSeenMonster`, and the database screen is `classes.interface.screens.monsterDatabaseScreen`.
"""

import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile

PREFS_DB = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "TRAGsoft", "Coromon", ".system", "CoronaPreferences.sqlite",
)

# the XOR keystream, recovered from the running game (see module docstring)
KEY = base64.b64decode(
    "cD1UazhPZjNKYXhFXHNAV247Umk2TWQxSF92Q1pxPlVsOVBnNEtieUZddEFYbzxTajdOZTJJYHdEW3I/"
    "Vm06UWg1TGMwR151QllwPVRrOE9mM0pheEVcc0BXbjtSaTZNZDFIX3ZDWnE+VWw5UGc0S2J5Rl10QVhv"
    "PFNqN05lMklgd0Rbcj9WbTpRaDVMYzBHXnVCWXA9VGs4T2YzSmF4RVxzQFduO1JpNk1kMUhfdkNacT5V"
    "bDlQZzRLYnlGXXRBWG88U2o3TmUySWB3RFtyP1ZtOlFoNUxjMEdedUJZcD1UazhPZjNKYXhFXHNAV247"
    "Umk2TWQxSF92Q1pxPlVsOVBnNEtieUZddEFYbzxTajdOZTJJYHdEW3I/Vm06UWg1TGMwR151QllwPVRr"
    "OE9mM0pheEVcc0BXbjtSaTZNZDFIX3ZDWnE+VWw5UFk="
)
OFFSET = 331


def _decrypt(blob):
    """The save's JSON, from the base64-decoded `encryptedData`."""
    key = KEY
    return bytes(b ^ key[(i + OFFSET) % len(key)] for i, b in enumerate(blob))


def _read_rows():
    """Every `saveslot_self_*` row, decrypted, newest first.

    Returns a list of (dateTime, slot key, save dict). The game keeps the database open, so
    it is copied out first rather than risking a lock.
    """
    if not os.path.exists(PREFS_DB):
        raise FileNotFoundError("%s does not exist - has the game ever saved?" % PREFS_DB)
    tmp = os.path.join(tempfile.gettempdir(), "coromon-prefs-copy.sqlite")
    shutil.copy2(PREFS_DB, tmp)
    con = sqlite3.connect("file:%s?mode=ro" % tmp.replace("\\", "/"), uri=True)
    try:
        rows = con.execute(
            "select key, value from preference where key like 'saveslot_self_%'").fetchall()
    finally:
        con.close()

    out = []
    for key, value in rows:
        try:
            outer = json.loads(str(value))
            blob = base64.b64decode(outer["encryptedData"])
            obj = json.loads(_decrypt(blob).decode("utf-8", "replace"))
        except Exception:
            continue                     # empty slot, or a format we cannot read
        when = (outer.get("metadata") or {}).get("dateTime") or 0
        out.append((when, key, obj))
    out.sort(key=lambda r: -r[0])
    return out


def _visited_in(obj):
    """The VISITED_MAPS dict from a decrypted save, wherever it is nested.

    It currently lives at `settings.VISITED_MAPS`, but it is found by name rather than by
    path: the save has 37 top-level keys and its shape changes between data versions.
    """
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).upper() == "VISITED_MAPS" and isinstance(value, dict):
                    return value
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(x for x in node if isinstance(x, (dict, list)))
    return {}


def visited():
    """(slot name, {map name: visits}) for the most recent slot that records visits."""
    newest = None
    for _, key, obj in _read_rows():
        found = _visited_in(obj)
        if found:
            return key, found
        if newest is None:
            newest = (key, {})
    if newest is None:
        raise ValueError("no save slot could be decoded - the keystream may be from another machine")
    return newest


# The dex record, keyed by Coromon UID and then by potential category: A standard, B potent,
# C perfect. Those three are the game's own database tabs, and the value is True for every
# category you have that Coromon in.
OWNED_KEY = "MONSTERS_OWNED"
SEEN_KEY = "MONSTERS_SEEN"


def _record_in(obj, name):
    """One of those dicts from a decrypted save, wherever it is nested.

    Found by NAME rather than by path, exactly like VISITED_MAPS: `stats.MONSTERS_OWNED` is where
    it lives today, and the save's shape changes between data versions.
    """
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).upper() == name and isinstance(value, dict):
                    return value
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(x for x in node if isinstance(x, (dict, list)))
    return {}


def monster_record():
    """(slot name, owned, seen) for the most recent slot that records either.

    Both are {coromon uid: {category: True}}; see the module docstring. Measured on a real save:
    `MONSTERS_OWNED` held 53 UIDs and `MONSTERS_SEEN` 115 of the 117 dex entries (every owned one
    of them also seen), which is the "seen / caught" the database screen counts for itself.
    """
    newest = None
    for _, key, obj in _read_rows():
        owned = _record_in(obj, OWNED_KEY)
        seen = _record_in(obj, SEEN_KEY)
        if owned or seen:
            return key, owned, seen
        if newest is None:
            newest = (key, {}, {})
    if newest is None:
        raise ValueError("no save slot could be decoded - the keystream may be from another machine")
    return newest


def categories(uid, record):
    """The categories a UID appears in, as a set - "A", "B", "C". Empty when it does not."""
    entry = record.get(uid)
    if not isinstance(entry, dict):
        return set()
    return {str(key).upper() for key, value in entry.items() if value}


def areas(names, seen=None):
    """The subset of `names` the save says you have visited, sorted.

    Exact matches first, then a name is accepted when a visited map extends it (the save has
    `electricCave_f1_A` where the encounter data has `electricCave_f1`).
    """
    if seen is None:
        _, seen = visited()
    lower = {k.lower(): k for k in seen}
    picked = []
    for name in names:
        low = name.lower()
        if low in lower:
            picked.append(name)
            continue
        if any(k.startswith(low) for k in lower):
            picked.append(name)
    return sorted(picked)


def unmatched(names):
    """Visited maps that no area name accounts for."""
    _, seen = visited()
    lower = [n.lower() for n in names]
    out = []
    for key in seen:
        low = key.lower()
        if any(low == n or low.startswith(n) for n in lower):
            continue
        out.append(key)
    return sorted(out)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    print("save database: %s" % PREFS_DB)
    try:
        for when, key, obj in _read_rows():
            maps = _visited_in(obj)
            print("  %-24s %-18s %4d visited maps  dataVersion=%s"
                  % (key, obj.get("currentPlayerMapName"), len(maps),
                     obj.get("saveslotDataVersion")))
    except Exception as exc:
        print("could not read the save: %s: %s" % (type(exc).__name__, exc))
        return 1

    try:
        slot, seen = visited()
        print("\nnewest slot with visit data: %s (%d maps)" % (slot, len(seen)))
    except Exception as exc:
        print("\n%s" % exc)
        return 1

    try:
        import encounters
        zone_maps = sorted({z.map_file for z in encounters.all_zones(*encounters.load())})
    except Exception:
        zone_maps = []
    if zone_maps:
        hit = areas(zone_maps)
        print("of the %d encounter areas, %d are visited:" % (len(zone_maps), len(hit)))
        print("  " + ", ".join(hit))
        rest = unmatched(zone_maps)
        if rest:
            print("visited but not an encounter area (%d): %s" % (len(rest), ", ".join(rest[:25])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
