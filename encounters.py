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
         "monsters": [{"monsterUID": "NORMAL_BEE_1", "minLevel": 3, "maxLevel": 6,
                       "crimsonite": false}, ...]},
        ...]}

An encounter holding two or three monsters is a double/triple battle, not a split rate.

CRIMSONITE SPAWNS. Every monster slot carries a `crimsonite` flag, and a slot with it set is a
DIFFERENT Coromon: same UID, but its crimsonite sprite and type, with its own skills (the game has a
whole `battle.skills.crimsonite` family and a `crimsoniteAura` battle rule) and its own catch
milestones (`MonsterSpriteSkinMilestone.CATCH_CRIMSONITE_ELECTRIC_FIREFLY` - "Catch a Coromon from
the [type.crimsonite] [monster ELECTRIC_FIREFLY_1] line"). Those flags are read into
`Zone.crimsonite`, kept apart from `Zone.monsters` so neither form's share is counted with the
other's - 11 Coromon spawn this way, across six lines, all in the water areas.

THE SHARES. A species' share of a zone is the sum of the `stepsWithEncounter` weights of the
encounters holding it, divided by the sum of all the weights in that zone.

That denominator is measured, not chosen, and the mechanism behind it is now read out of the
game's own bytecode (2026-09-26). A zone builds a SHUFFLE BAG of exactly
`stepsUntilSeenAllEncounters` entries: each encounter contributes `stepsWithEncounter` copies,
and everything left over is filled with `NO_ENCOUNTER`:

    for each encounter:  bag:addAmountOfObject(encounter.stepsWithEncounter, encounter)
    then:                bag:addAmountOfObject(stepsUntilSeenAllEncounters - <sum of weights>, NO_ENCOUNTER)

A draw takes a random entry and REMOVES it, refilling the bag only once it is empty
(`classes.modules.Shufflebag`; the zone wrapper is in `classes.lists.EncounterZoneList`). So a
species' share OF ENCOUNTERS is its weight over the sum of weights - what this tool prints - while
dividing by `stepsUntilSeenAllEncounters` gives its chance PER ROLL. The `NO_ENCOUNTER` filler is
what reconciles the two magnitudes: the weights sum to 4..95 while the field holds 10 to 4000, and
`sum(weights) <= stepsUntilSeenAllEncounters` holds in all 101 zones - never a negative pad.

WHAT A ROLL IS depends on the zone, because it is not always a step:

  * LAND. The grass TILES are the zone objects - `grassArea` objects in the map, each carrying a
    `zoneUID` - and they are an `abstractActionTileZoneArea`, so the roll fires on the player's
    grid move into/onto them. One bag entry per grass tile entered, multiplied by
    `battleEffectUtility:mutateAmountOfMonsterRolls` (1 normally; lures raise it). Turning in
    place, standing still and pacing outside the zone roll nothing. That tile's own
    `activatedModule` then applies: `repel` blocks the encounter outright, `attract` raises the
    rolled monster(s) by `math.random(4, 6)` levels.
  * WATER. `fishingZoneArea` objects are NOT action-tile areas, so walking on water rolls nothing
    at all. The surfboard's action button finds the `fishingZoneArea` on the player's tile and
    casts once: ONE bag entry per cast, and a `NO_ENCOUNTER` draw is what the game shows as the
    "no bite" dialogue. The water zones' high figures here are therefore per CAST, not per step.

What is NOT verified: nothing about the weights now - the draw is read out of the code and the
zone data is consistent with it. The earlier remark about the wiki still stands: its "Grass A
(South)" groups areas differently from the game's per-map A/B/C/D zones, so there is no
zone-by-zone comparison to make.

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
        self.monsters = {}          # uid -> rec, the ORDINARY spawns
        self.crimsonite = {}        # uid -> rec, the crimsonite form of a species
        self.battles = 1
        for enc in raw.get("encounters", []):
            weight = enc.get("stepsWithEncounter", 0)
            party = enc.get("monsters", [])
            self.battles = max(self.battles, len(party))
            for mon in party:
                uid = mon.get("monsterUID")
                # A CRIMSONITE SLOT IS A DIFFERENT COROMON, not more of the ordinary one. Every
                # monster slot carries the flag, and the SAME uid can spawn both ways in the same
                # zone - so counting the two together both overstates the ordinary form's share and
                # loses where the crimsonite one comes from. The DENOMINATOR does not change: a
                # crimsonite encounter is one of the zone's encounters like any other, and both
                # shares are still read against the whole table.
                form = self.crimsonite if mon.get("crimsonite") else self.monsters
                rec = form.setdefault(
                    uid, {"share": 0.0, "min": 10 ** 6, "max": 0, "battles": 1}
                )
                rec["share"] += 100.0 * weight / total
                rec["min"] = min(rec["min"], mon.get("minLevel", 0))
                rec["max"] = max(rec["max"], mon.get("maxLevel", 0))
                rec["battles"] = max(rec["battles"], len(party))

    @property
    def water(self):
        return self.name.endswith("_WATER")

    def rollable_encounters(self):
        """The encounters the game can actually roll a level for, weighted by their own share.

        THE GAME ROLLS `math.random(minLevel, maxLevel)` - `classes.lists.EncounterZoneList.lu`,
        line 115, which builds the `Monster` right afterwards with that number as its `level` - and
        LUA REFUSES AN EMPTY INTERVAL, so an entry whose `minLevel` is above its `maxLevel` can never
        produce a fight. PYRAMID_F6 ships exactly that: `GHOST_OCTO_1 minLevel 2725, maxLevel 27`,
        where every other Pyramid floor uses 25-27, so it is a typo in the game's own data and not a
        level-2725 Coromon. Believing it produced the nonsense the user spotted - "Squidly
        L2725-27 14.3% 32237 xp", which made that floor the best grind in the game.

        Such an entry is left out of the level and XP expectations and the rest are renormalised
        over their own shares, because a fight the game cannot start cannot take a share of the
        fights the player gets. `Zone.encounters()` still lists it, with the game's own numbers.

        The weights are FRACTIONS (0..1) of the fights you will meet, and they no longer count a
        group's members separately - the same unit `encounters()` fixed the percentages for.
        """
        rows = [row for row in self.encounters() if row["rollable"]]
        total = sum(row["share"] for row in rows) or 1.0
        return [(row, row["share"] / total) for row in rows]

    @property
    def average_level(self):
        """Expected level for ONE encounter, weighted by the chance of meeting it - the ranking.

        PER ENCOUNTER, like the shares: the expectation is `sum(share x the party's mean level)` over
        the encounters that can be rolled. Counting each SPECIES instead added up shares past 100%
        for a group (measured on WATERROUTE_4: 74.20 that way against 54.33 here) and reported an
        expected level above anything the zone spawns.
        """
        return sum(weight * self.encounter_level(row)
                   for row, weight in self.rollable_encounters())

    @staticmethod
    def encounter_level(row):
        """A party's mean level, one count per BODY - two Silquill are two Coromon."""
        bodies = sum(member["count"] for member in row["members"]) or 1
        return sum(member["count"] * (member["min"] + member["max"]) / 2.0
                   for member in row["members"]) / bodies

    def slots(self):
        """Every spawn in the zone, as `{"name", "crimsonite", "share", "min", "max", ...}`.

        A crimsonite slot is named the way the game names it - "Crimsonite <species>", the string
        its own catch milestone prints - because it is a Coromon of its own. Nothing is merged:
        both the ordinary and the crimsonite form of one species can appear here, each with the
        share it really has.
        """
        out = [dict(rec, name=self.species.get(uid, uid), crimsonite=False)
               for uid, rec in self.monsters.items()]
        out += [dict(rec, name="Crimsonite " + self.species.get(uid, uid), crimsonite=True)
                for uid, rec in self.crimsonite.items()]
        return out

    def encounters(self):
        """Every ENCOUNTER the zone can roll - one row per entry, most likely first.

        THE UNIT THE GAME ROLLS, which is why this exists beside `slots`. An entry is one encounter
        with a party, so its share is the chance of meeting THAT FIGHT, and the party is the answer to
        "what am I actually up against". The per-species view cannot say either: it counts a species
        once PER PARTY MEMBER (a `[Skuldra, Skelatops, Skuldra]` triple battle gives Skuldra twice the
        entry's share - measured, the shares of DESERTROUTE_3_SPECIAL sum to 106.9%), and it reports
        every member of a group at the whole group's share, which reads as several separate spawns at
        the same odds. The user, on exactly that: "it says tripple battle, in this case it's Armadon
        and 2 Armodo, but it doesn't say that, so the percentages are misleading".

        `name` is the COMPOSITION - "Armadon + 2 Armado", duplicates counted, in party order - because
        that is what the percentage belongs to. `min`/`max` span the whole party, and `members` keeps
        the per-member detail (`name`, `count`, `min`, `max`, `crimsonite`).
        """
        total = self.weight_total or 1
        out = []
        for enc in self.raw.get("encounters", []):
            party = enc.get("monsters", [])
            if not party:
                continue
            seen, order = {}, []
            for mon in party:
                key = (mon.get("monsterUID"), bool(mon.get("crimsonite")))
                if key not in seen:
                    seen[key] = {"uid": key[0], "crimsonite": key[1], "count": 0,
                                 "min": 10 ** 6, "max": 0}
                    order.append(key)
                rec = seen[key]
                rec["count"] += 1
                rec["min"] = min(rec["min"], mon.get("minLevel", 0))
                rec["max"] = max(rec["max"], mon.get("maxLevel", 0))
            members = [seen[key] for key in order]
            for rec in members:
                name = self.species.get(rec["uid"], rec["uid"])
                rec["name"] = ("Crimsonite " + name) if rec["crimsonite"] else name
            out.append({
                "name": " + ".join("%d %s" % (rec["count"], rec["name"])
                                    if rec["count"] > 1 else rec["name"]
                                    for rec in members),
                "members": members,
                "share": 100.0 * enc.get("stepsWithEncounter", 0) / total,
                "min": min(rec["min"] for rec in members),
                "max": max(rec["max"] for rec in members),
                "battles": len(party),
                "crimsonite": all(rec["crimsonite"] for rec in members),
                # EVERY MEMBER's range has to be rollable, not just the party's span: a reversed
                # range on one member of a group would be hidden by `min`/`max` over the others.
                "rollable": all(rec["min"] <= rec["max"] for rec in members),
            })
        return sorted(out, key=lambda rec: -rec["share"])

    def xp_per_encounter(self, xp_of):
        """Expected XP for ONE encounter in this zone, given `xp_of(uid, level)`.

        THE NUMBER THIS WHOLE TAB EXISTS TO RANK BY, and it belongs to the ENCOUNTER for the same
        reason its share does: one fight is what the player gets, so the expectation is
        `sum over encounters (share x the party's total XP)`. A group counts EVERY member (a party of
        two Skuldra is two Skuldra defeated), and each member's level is the middle of its own range,
        which is the same reading `average_level` uses.

        `xp_of` is passed in rather than imported so this module stays data-only - the formula lives in
        `dex.xp_reward`, read out of the game's own `Monster:calculateXpReward`.
        """
        return sum(weight * self.encounter_xp(row, xp_of)
                   for row, weight in self.rollable_encounters())

    def xp_spread(self, xp_of):
        """`(expected, best, encounters)` for one fight in this zone - the pane's headline.

        THE EXPECTATION ALONE READS LIKE A LIE, which is what the user ran into: WATERROUTE_4's five
        encounters are worth 4406 / 6101 / 4431 / 4775 / 4485 xp, so the zone's own 4840 is LOWER
        than three of the five numbers printed underneath it. It is the average, weighted by the
        chance of meeting each fight (all five are 20% here, so it is the plain mean as well), and
        the best fight in that grass is worth 6101 - both of which a reader is entitled to see.
        """
        rows = self.rollable_encounters()
        expectation = sum(weight * self.encounter_xp(row, xp_of) for row, weight in rows)
        best = max((self.encounter_xp(row, xp_of) for row, _weight in rows), default=0.0)
        return expectation, best, len(rows)

    def encounter_xp(self, row, xp_of):
        """One encounter's party's total XP, given `xp_of(uid, level)` - see `xp_per_encounter`.

        Every member counts once per body: a party of two is two Coromon defeated, so a group is worth
        more than its leader even though it fills one line of the listing.
        """
        xp = 0.0
        for member in row["members"]:
            middle = (member["min"] + member["max"]) / 2.0
            xp += (xp_of(member["uid"], middle) or 0) * member["count"]
        return xp

    def listing(self, min_share=0.0):
        """Every spawn as a text row, most common first (see `slots`)."""
        rows = [r for r in sorted(self.slots(), key=lambda r: -r["share"])
                if r["share"] >= min_share]
        width = max([14] + [len(r["name"]) + 1 for r in rows])
        out = []
        for rec in rows:
            tag = {1: "", 2: "  double", 3: "  triple"}.get(rec["battles"], "")
            if rec["crimsonite"]:
                tag += "  crimsonite"
            out.append("%-*s L%-3s-%-3s %5.1f%%%s" % (
                width, rec["name"], rec["min"], rec["max"], rec["share"], tag))
        return out


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
