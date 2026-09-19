#!/usr/bin/env python3
"""
encounters.py - where every Coromon spawns, with its levels and its share of the zone.

Reads the game's own data. No game, no memory reading, nothing needed running:

    Resources/data/json/encounterZones.json    69 maps -> 101 zones -> encounters -> monsters
    Resources/data/json/monsters.json          UID -> display name

The shape of a zone, verbatim:

    {"mapFile": "amishRoute", "name": "AMISHROUTE_A", "stepsUntilSeenAllEncounters": 100,
     "encounters": [
        {"name": "Encounter 3", "stepsWithEncounter": 1,
         "monsters": [{"monsterUID": "NORMAL_BEE_1", "minLevel": 3, "maxLevel": 6}, ...]},
        ...]}

An encounter holding two or three monsters is a double/triple battle, not a split rate.

THE SHARES. A species' share of a zone is the sum of the `stepsWithEncounter` weights of the
encounters holding it, divided by the sum of all the weights in that zone.

That denominator is measured, not chosen. The obvious first guess is to divide by the zone's
own `stepsUntilSeenAllEncounters`, and it is wrong: measured across all 101 zones the weights
sum to between 4 and 95, while those fields hold 10 to 4000 (10, 12, 14, ... 200, 300, 4000),
and the two are equal in exactly 0 of the 101. Dividing by them makes every share too small by
a factor that differs from zone to zone.

What is NOT verified: that the game draws proportionally to those weights. The encounter roll
was not reachable from the running game - the zone data is not held by any loaded module - so
read the shares as relative weights. This is also why the official wiki's percentages can't be
used to check it: for a zone whose weights are percentage-like the numbers agree, but the
wiki's "Grass A (South)" groups areas differently from the game's per-map A/B/C/D zones, so
there is no zone-by-zone comparison to make.

Usage:

    python encounters.py                      every zone, grouped by map
    python encounters.py --grind 64           best spots for a level 64 squad, ranked
    python encounters.py --map waterTown      one map
    python encounters.py --species Buzzlet    every zone it appears in
    python encounters.py --min-share 10       only species that are at least this common

Nothing here knows about XP: the monster data has no XP yield and no level curve, so a good
spot is ranked by the level of what you meet (rate-weighted) rather than by XP per battle.
"""

import argparse
import collections
import json
import os
import sys

DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Resources", "data", "json",
)


def load():
    with open(os.path.join(DATA, "encounterZones.json"), encoding="utf-8") as fh:
        zones = json.load(fh)
    with open(os.path.join(DATA, "monsters.json"), encoding="utf-8") as fh:
        species = {m["UID"]: m["name"] for m in json.load(fh)}
    return zones, species


class Zone:
    """One zone's encounters, with the shares worked out."""

    def __init__(self, map_file, raw, species):
        self.map_file = map_file
        self.raw = raw
        self.name = raw.get("name", "?")
        self.species = species
        total = sum(e.get("stepsWithEncounter", 0) for e in raw.get("encounters", [])) or 1
        self.weight_total = total
        self.monsters = {}
        self.battles = 1
        for enc in raw.get("encounters", []):
            weight = enc.get("stepsWithEncounter", 0)
            party = enc.get("monsters", [])
            self.battles = max(self.battles, len(party))
            for mon in party:
                uid = mon.get("monsterUID")
                rec = self.monsters.setdefault(
                    uid, {"share": 0.0, "min": 10 ** 6, "max": 0, "battles": 1}
                )
                rec["share"] += 100.0 * weight / total
                rec["min"] = min(rec["min"], mon.get("minLevel", 0))
                rec["max"] = max(rec["max"], mon.get("maxLevel", 0))
                rec["battles"] = max(rec["battles"], len(party))

    @property
    def water(self):
        return self.name.endswith("_WATER")

    @property
    def average_level(self):
        """Expected level per encounter, weighted by share - the grind ranking."""
        return sum(r["share"] / 100.0 * (r["min"] + r["max"]) / 2.0 for r in self.monsters.values())

    def listing(self, min_share=0.0):
        rows = []
        for uid, rec in sorted(self.monsters.items(), key=lambda kv: -kv[1]["share"]):
            if rec["share"] < min_share:
                continue
            tag = {1: "", 2: "  double", 3: "  triple"}.get(rec["battles"], "")
            rows.append("%-14s L%-3s-%-3s %5.1f%%%s" % (
                self.species.get(uid, uid), rec["min"], rec["max"], rec["share"], tag))
        return rows


def all_zones(zones, species):
    out = []
    for map_file, raw_zones in zones.items():
        for raw in raw_zones:
            out.append(Zone(map_file, raw, species))
    return out


def show_zones(zs, min_share=0.0, header=True):
    for z in sorted(zs, key=lambda z: (z.map_file, z.name)):
        if header:
            print("%-22s %-16s%s" % (z.name, z.map_file, "   (water)" if z.water else ""))
        for row in z.listing(min_share):
            print("    " + row)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", help="only this map file (e.g. waterTown)")
    ap.add_argument("--species", help="only zones where this species appears (name or UID)")
    ap.add_argument("--min-share", type=float, default=0.0, help="hide species below this %%")
    ap.add_argument("--grind", type=float, metavar="LEVEL",
                    help="rank zones for a squad of this level instead of listing them")
    ap.add_argument("--top", type=int, default=12, help="how many zones --grind shows")
    args = ap.parse_args(argv)

    zones, species = load()
    zs = all_zones(zones, species)
    if args.map:
        # substring, so --map waterTown also catches waterTown_titanTemple
        want = args.map.lower()
        zs = [z for z in zs if want in z.map_file.lower() or want in z.name.lower()]
    if args.species:
        want = args.species.lower()
        zs = [z for z in zs if any(
            want == uid.lower() or want == species.get(uid, "").lower() for uid in z.monsters)]

    if not zs:
        print("no zone matched", file=sys.stderr)
        return 1

    if args.grind:
        level = args.grind
        # worth fighting = still reaches the squad's level; ranked by rate-weighted level
        worth = [z for z in zs if max(r["max"] for r in z.monsters.values()) >= level - 3]
        worth.sort(key=lambda z: -z.average_level)
        print("best spots for a level %g squad (ranked by rate-weighted monster level)\n" % level)
        for z in worth[: args.top]:
            tag = "   (water)" if z.water else ""
            print("%-22s %-16s expLvl %5.2f%s" % (z.name, z.map_file, z.average_level, tag))
            for row in z.listing(args.min_share):
                print("    " + row)
            print()
        if not worth:
            print("nothing in range - the data tops out lower than that")
        return 0

    show_zones(zs, args.min_share)
    return 0


if __name__ == "__main__":
    sys.exit(main())
