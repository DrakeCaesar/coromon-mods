"""What the dex is still missing, and which zones are worth walking to go and get it.

THE USER'S RULE, after one revision (the earlier half is quoted because it is what the current rule
was changed FROM):

    "each evolutionary line has 1 to 3 coromon, at 3 potential levels, 4th if we count crimsonite
     variants. those we need to condense internally into 4 coromon groups per evolutionary line ...
     based on this data I would like to have a list of spawn zones ordered by how many coromon we
     don't have yet captured that spawn there"

     - then: "if we are missing any member of a line - that counts as one slot discard what I said
     about evolutions being trivial to get"

So a line makes up to four groups, and each is CAUGHT only when nothing of that kind is missing:

    standard    "A"           every stage of the line owned in category A
    potent      "B"           ... in category B
    perfect     "C"           ... in category C
    crimsonite  "crimsonite"  the line's crimsonite forms, which is a skin unlock per FAMILY

An earlier version of this took "any one stage is enough, because the evolution between them is
trivial" - that is the part the user discarded. `zones()` then counts LINES, not kinds: a line missing
two kinds is one slot to go and find, which is why the ranking and the pane are per line and the kinds
are named on the line's own row.

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
        """Whether the save fills this group - EVERY stage of the line, no evolution shortcut.

        THE USER CHANGED THIS RULE: "if we are missing any member of a line - that counts as one slot
        discard what I said about evolutions being trivial to get". So a potential-category group is
        filled only when EVERY stage of the line has that category: catching a stage-1 Buzzlet as a
        Potent and evolving it used to be enough, and it is not any more - the evolved form's own
        Potent entry is a member that has to exist.

        This is now the same reading as the Database tab's "hide complete" filter
        (`database_tab._complete`, which asks it per stage and per category) - the two tabs agree on
        what a finished line is, with the one difference that the crimsonite skin is a group of its
        own here and is not part of completeness there.
        """
        if self.kind == dex.CRIMSONITE:
            return ("%s|%s" % (self.family, dex.CRIMSONITE)) in (skins or ())
        return all(_owns(owned, uid, self.kind) for uid in self.uids)


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


def _lines(groups):
    """The distinct evolutionary LINES the groups belong to, in the order they were met.

    THE COUNTING RULE THE USER CHANGED: a line missing both its potent and its perfect group is ONE
    thing to go and get, not two - "right now we count 1 potent missing and 1 perfect missing etc of
    the same evolutionary line as 2 missing - weight 2, it should rather count as 1 as one member".
    The same grass grows it either way, so the trip is one; how many CATCHES it takes is what the
    groups count for, and that is still visible per kind in the tab.
    """
    seen = []
    for group in groups:
        if group.family not in seen:
            seen.append(group.family)
    return seen


def zones(zone_list, all_groups):
    """The zones to grind, MOST MISSING LINES FIRST - the list the user asked for.

    Each row is `{"zone", "groups", "missing", "missing_lines", "hosted_lines"}`: every group the zone
    can fill, the subset of them the save does not have, and the LINES those groups belong to - which
    is what the ranking is ordered by, with the group count as the tie-break (more catches waiting
    there is more to do there) and the name last so the order is stable.
    """
    by_zone = groups_by_zone(zone_list, all_groups)
    rows = []
    for zone in zone_list:
        hosted = by_zone.get(zone.name, [])
        if not hosted:
            continue
        missing = [group for group in hosted if not group.caught]
        rows.append({"zone": zone, "groups": hosted, "missing": missing,
                     "missing_lines": _lines(missing), "hosted_lines": _lines(hosted)})
    rows.sort(key=lambda row: (-len(row["missing_lines"]), -len(row["missing"]),
                               -len(row["hosted_lines"]), row["zone"].name))
    return rows


def lines_missing(zone, groups):
    """The missing groups FOLDED PER LINE - `[{"name", "kinds", "odds"}]`, best odds first.

    THE UNIT THE USER COUNTS IN, and therefore the unit the pane prints: "standard, potent and perfect
    kyreptil should count as 1 group ... only if we were missing both potent kyreptil and kyraptor does
    that count as one missing slot". A line short of three kinds is ONE row naming all three, and
    `odds` is the best way into that line here - the potential kinds share the same slots, while a
    crimsonite group has its own, so when that is the only thing missing its own odds are what shows.
    """
    folded = []
    for group in groups:
        hit = odds_in(zone, group)
        for entry in folded:
            if entry["family"] == group.family:
                entry["kinds"].append(group.kind)
                if hit:
                    entry["hits"].append(hit)
                break
        else:
            folded.append({"family": group.family, "name": group.name, "kinds": [group.kind],
                           "hits": [hit] if hit else []})
    for entry in folded:
        entry["kinds"].sort(key=KINDS.index)
        entry["odds"] = ((min(hit[0] for hit in entry["hits"]),
                          max(hit[1] for hit in entry["hits"]),
                          max(hit[2] for hit in entry["hits"])) if entry["hits"] else None)
    folded.sort(key=lambda entry: -(entry["odds"][2] if entry["odds"] else 0))
    return folded


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
        lines.append("  %-22s %-22s %d line(s), %d group(s) missing of %d"
                     % (row["zone"].name, row["zone"].map_file,
                        len(row["missing_lines"]), len(row["missing"]), len(row["groups"])))
        for entry in lines_missing(row["zone"], row["missing"])[:3]:
            lines.append("      %-16s %-30s %s" % (
                entry["name"], " / ".join(KIND_NAMES[kind] for kind in entry["kinds"]),
                "L%s-%s  %.1f%% of the slots here" % entry["odds"] if entry["odds"] else "no slot"))
    unlined = unlined_slots(_all_zones())
    if unlined:
        lines.append("spawn slots in no dex line (not counted): %d %s" % (len(unlined), unlined[:4]))
    return "\n".join(lines)
