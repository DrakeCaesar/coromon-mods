"""What the dex is still missing, and which zones are worth walking to go and get it.

THE USER'S RULE, which is what this file exists to implement: "each evolutionary line has 1 to 3
coromon, at 3 potential levels, 4th if we count crimsonite variants. those we need to condense
internally into 4 coromon groups per evolutionary line - if we have at least one of the evolutions of
the 4 groups caught - it counts as having that kind of coromon already captured, because transition
between evolution levels is trivial, basically if we are missing all 3 of a potential level - that
counts as missing and if we have one - that means caught. based on this data I would like to have a
list of spawn zones ordered by how many coromon we don't have yet captured that spawn there".

So the UNIT IS (EVOLUTIONARY LINE, KIND) - `Group` - and a group is caught when ANY of its stages is
caught in that category. A line makes up to four groups:

    standard    "A"           the line's stages, catching any one of them in category A
    potent      "B"           ... in category B
    perfect     "C"           ... in category C
    crimsonite  "crimsonite"  the line's crimsonite FORMS, which is a skin unlock per FAMILY

A CRIMSONITE SLOT IS COUNTED AS THE CRIMSONITE GROUP ONLY, which is the conservative reading: the form
is a Coromon of its own with no dex entry (see `dex.crimsonite_forms`), and whether catching one also
stamps the ordinary entry's category is not something the archive settled - the catch code was chased
to `playerStats:setMonsterOwned` without reaching the call site. It changes at most a handful of
zones, all of them water areas that spawn the ordinary forms of those same lines anyway.

Nothing here touches Qt or reads the disk: the save arrives as `owned` / `skins` (what
`savefile.dex_record()` returns), so the model can be built and printed without a window - see
`report()`.
"""

import dex
import encounters

# THE SAVE'S OWN CATEGORY LETTERS, in the game's order, plus the crimsonite skin - see
# `savefile.categories` and `savefile.has_skin`.
KINDS = ("A", "B", "C", dex.CRIMSONITE)

# WHAT THE POTENTIAL CATEGORIES ARE CALLED, the game's own words for them (`playerStats` and the dex
# screen both say Standard / Potent / Perfect - see `savefile.AREAS`-style tables in the Database tab).
KIND_NAMES = {"A": "standard", "B": "potent", "C": "perfect", dex.CRIMSONITE: "crimsonite"}


def _owns(owned, uid, kind):
    """Whether the save records that UID in that category.

    The same test as `savefile.categories`, which reads `MONSTERS_OWNED[uid][category]` and upper-cases
    the letter because the payload is not consistent about case. Written out here rather than imported
    so this module stays independent of the save file, and `ui_smoke` asserts the two agree.
    """
    entry = (owned or {}).get(uid)
    if not isinstance(entry, dict):
        return False
    return any(str(key).upper() == kind and bool(value) for key, value in entry.items())


class Group:
    """One evolutionary line's worth of ONE kind - the unit the dex is judged in.

    `members` are the Coromon that carry it: the line's stages for a potential category, and the
    line's crimsonite forms for the crimsonite kind (which is keyed by FAMILY in the save, so every
    stage of the line shares the one key).
    """

    def __init__(self, family, kind, members, caught, name=None):
        self.family = family
        self.kind = kind
        self.members = list(members)
        self.caught = caught
        # THE LINE'S OWN NAME, passed in rather than taken from `members`: a crimsonite group's members
        # are the FORMS, whose names are "Crimsonite Fiddly", and a checklist that calls the line
        # "Crimsonite Fiddly" reads as a different Coromon instead of the crimsonite kind of Fiddly.
        self._name = name or self.members[0].name

    def __repr__(self):
        return "<%s %s>" % ("caught" if self.caught else "MISSING", self.label)

    @property
    def key(self):
        return (self.family, self.kind)

    @property
    def name(self):
        """The LINE's name, from its base form - "Buzzlet", not "Volcadon"."""
        return self._name

    @property
    def label(self):
        return "%s %s" % (self.name, KIND_NAMES[self.kind])

    @property
    def uids(self):
        return [mon.uid for mon in self.members]

    @property
    def skin(self):
        """The `dex.where` skin to ask for: the form's, or None for an ordinary stage."""
        return dex.CRIMSONITE if self.kind == dex.CRIMSONITE else None

    def caught_in(self, owned, skins):
        """Whether the save fills this group - ANY stage counts, which is the user's rule."""
        if self.kind == dex.CRIMSONITE:
            return ("%s|%s" % (self.family, dex.CRIMSONITE)) in (skins or ())
        return any(_owns(owned, uid, self.kind) for uid in self.uids)


def groups(owned=None, skins=None, lines=None):
    """Every group the dex can be filled in, in dex order: the whole checklist, caught and missing.

    THE ORDER IS THE GAME'S OWN - `dex.lines()` is the database screen's row order - so the checklist
    reads like the dex rather than like a list of gaps.
    """
    out = []
    for family, stages in (lines or dex.lines()):
        line_name = stages[0].name
        for kind in ("A", "B", "C"):
            group = Group(family, kind, stages, False, line_name)
            group.caught = group.caught_in(owned, skins)
            out.append(group)
        forms = [form for form in (dex.crimsonite_of(mon.uid) for mon in stages) if form]
        if forms:
            group = Group(family, dex.CRIMSONITE, forms, False, line_name)
            group.caught = group.caught_in(owned, skins)
            out.append(group)
    return out


def family_of(uids=None):
    """{uid: family UID} for every dex entry, so a spawn can be traced back to its line.

    Built from `dex.lines()` rather than from the monsters file, because the LINES are what decide
    which Coromon this tab counts at all: the seven families entirely inside the 900+ placeholder
    block are not in any line (`dex.monsters` leaves them out), and neither is Xena, so a spawn of one
    of those is not part of the checklist - see `unlined_slots`.
    """
    index = {}
    for family, stages in dex.lines():
        for mon in stages:
            index[mon.uid] = family
    return index


def groups_by_zone(zone_list, all_groups):
    """`{zone name: [group, ...]}` - the groups each zone can fill, caught ones included.

    A zone's ORDINARY slot for a species offers all three potential categories, because the category
    of a wild Coromon is a roll (`monsterUtility:rollPotential`) and not a property of the slot: any
    walk in that grass can turn up that line as a standard, a potent or a perfect. A CRIMSONITE slot
    offers that line's crimsonite group only - see the module docstring.
    """
    by_key = {group.key: group for group in all_groups}
    families = family_of()
    out = {}
    for zone in zone_list:
        seen = []
        for uid, crimsonite in ([(uid, False) for uid in zone.monsters] +
                                [(uid, True) for uid in zone.crimsonite]):
            family = families.get(uid)
            if family is None:                          # a spawn that is in no dex line
                continue
            kinds = (dex.CRIMSONITE,) if crimsonite else ("A", "B", "C")
            for kind in kinds:
                group = by_key.get((family, kind))
                if group is not None and group not in seen:
                    seen.append(group)
        out[zone.name] = seen
    return out


def unlined_slots(zone_list):
    """[uid, ...] - the spawn slots that belong to no dex line, so nothing here can be filled by them.

    A number to keep an eye on rather than a silent `continue`: if a game update adds encounters for
    the placeholder families, the tab would under-report and this is what says so.
    """
    families = family_of()
    out = set()
    for zone in zone_list:
        for uid in list(zone.monsters) + list(zone.crimsonite):
            if uid not in families:
                out.add(uid)
    return sorted(out)


def zones(zone_list, all_groups):
    """The zones to grind, MOST MISSING GROUPS FIRST - the list the user asked for.

    Each row is `{"zone", "groups", "missing"}`: every group the zone can fill, and the subset of them
    the save does not have yet. Sorted by how many are missing, then by how many the zone offers at
    all (a zone with four missing out of four is a better target than four out of twelve), then by name
    so the order is stable.
    """
    by_zone = groups_by_zone(zone_list, all_groups)
    rows = []
    for zone in zone_list:
        hosted = by_zone.get(zone.name, [])
        if not hosted:
            continue
        rows.append({"zone": zone, "groups": hosted,
                     "missing": [group for group in hosted if not group.caught]})
    rows.sort(key=lambda row: (-len(row["missing"]), -len(row["groups"]), row["zone"].name))
    return rows


def odds_in(zone, group):
    """`(min level, max level, share %)` for that group in that zone, or None when it spawns there not.

    THE LINE'S SLOTS ADDED UP, because either stage is the same group: meeting a Buzzlet OR a Volcadon
    in that grass fills it. Levels are the span over those slots, and the share is their sum - "the
    odds of meeting something of that line here", which is the number that decides whether the trip is
    worth it. `share` can exceed 100% for a line whose several stages all spawn in one zone, exactly as
    the per-species view does (see `Zone.encounters` for why the per-ENCOUNTER shares are the ones that
    add up to 100%) - so it is a likelihood to compare between zones, not a percentage of one roll.
    """
    hits = [hit for uid in group.uids for hit in dex.where(uid, group.skin)
            if hit[0].name == zone.name]
    if not hits:
        return None
    return (min(hit[1] for hit in hits), max(hit[2] for hit in hits),
            sum(hit[3] for hit in hits))


def load(owned=None, skins=None):
    """`(all_groups, zone_rows)` from a save record - the model the tab draws, with no Qt in it."""
    zone_list = _all_zones()
    all_groups = groups(owned, skins)
    return all_groups, zones(zone_list, all_groups)


def _all_zones():
    zones_raw, species = encounters.load()
    return encounters.all_zones(zones_raw, species)


def report(owned=None, skins=None):
    """The tab's numbers as text, for `python -m coromontools --selftest` and for a quick look."""
    all_groups, rows = load(owned, skins)
    missing = [group for group in all_groups if not group.caught]
    lines = ["groups: %d, missing %d, in %d lines still short"
             % (len(all_groups), len(missing), len({group.family for group in missing})),
             "zones that can fill something: %d, of which %d are missing something"
             % (len(rows), sum(1 for row in rows if row["missing"]))]
    for row in rows[:6]:
        lines.append("  %-22s %-22s %d missing of %d"
                     % (row["zone"].name, row["zone"].map_file,
                        len(row["missing"]), len(row["groups"])))
        for group in row["missing"][:3]:
            odds = odds_in(row["zone"], group)
            lines.append("      %-20s %-11s %s" % (
                group.label, "L%s-%s" % odds[:2] if odds else "no slots", 
                "%.1f%% of the slots here" % odds[2] if odds else ""))
    unlined = unlined_slots(_all_zones())
    if unlined:
        lines.append("spawn slots in no dex line (not counted): %d %s" % (len(unlined), unlined[:4]))
    return "\n".join(lines)
