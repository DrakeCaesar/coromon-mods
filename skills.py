#!/usr/bin/env python3
"""
skills.py - every skill in the game, in the columns the wiki's skill table uses, plus the
full description.

Reads the game's own data. No game, no memory reading, nothing needed running:

    Resources/data/json/skills.json     258 skills, with types and integers already typed

The JSON is used rather than the CSV beside it purely because it arrives typed - `power` is
`60` and `makesContact` is `true` - so nothing here has to re-parse "true"/"" out of strings.

THE COLUMNS. These are the wiki's, and each maps to one field:

    Name     name                    SP       energyCost      Ctgry.   category
    Type     type                    Power    power           Contact  makesContact
    Acc.     accuracy                Target   targetMode

Two of them are words in the data and numbers on the page. `category` is one of
`physicalDamage` / `specialDamage` / `status`; `targetMode` is one of
`enemyTargetInBattle` / `enemyTeamInBattle` / `self` / `selfTarget` / `enemyTargetRandom` /
`allInBattle` / `none`, and is not otherwise explained anywhere, so both are mapped here with
the wiki's own wording.

`power` and `accuracy` are absent on status skills and are shown as "-", which is what the
wiki does. SP is NOT blanked when it is 0: Agility Training really does cost 0.

THE DESCRIPTIONS, and why there is a table of words in this file. A description is written
with bracketed placeholders rather than plain nouns, because the game colours and localises
them at draw time:

    "Bite with acid teeth, lowering the target's [stat.defense]."

`[stat.defense]` is a localisation key, and the game looks it up as `global.stat.defense`.
The English values were read out of the game's own string table in `Resources/resource.car`
(the archive holds every language, one contiguous block each; English is the block holding
`global.hp` -> `<color TEXT_HP>HP</color>`), and the wording below is copied from there - so
"Defense" and "Sp. Attack", not a guess at "Special Attack". The archive is 238 MB and is
scanned for these once, by hand; they are hard-coded here rather than re-read at start-up.

There is a second kind of placeholder, braced rather than bracketed, and it is the skill's own
field rather than a word: 57 descriptions read "Has a {sideEffectChance}% chance to ...", and
every one of those 57 carries a numeric `sideEffectChance` (and no skill carries the number
without the brace), so it is substituted from the record and the sentence gets its percentage.

Only 30 tokens appear anywhere in the skill descriptions, which is what makes that
reasonable. Anything outside the table falls back to a readable form of the token itself
(`type.electric` -> "Electric"), and `[kind UID]` forms (`item`, `monster`, ...) - which no
skill uses - fall back to a prettified UID.

Usage:

    python skills.py                       every skill, in the wiki's columns
    python skills.py --type poison         one type
    python skills.py --search acid         name or UID contains
    python skills.py --detail ACID_BITE    one skill, with its full description
"""

import argparse
import json
import os
import re
import sys

DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Resources", "data", "json",
)

# The wiki's columns, in order: (key, heading, width, anchor). Shared with the GUI so the
# table and the text listing can never drift apart.
COLUMNS = (
    ("name", "Name", 190, "w"),
    ("type", "Type", 92, "w"),
    ("sp", "SP", 44, "e"),
    ("power", "Power", 56, "e"),
    ("acc", "Acc.", 50, "e"),
    ("ctgry", "Ctgry.", 78, "w"),
    ("contact", "Contact", 64, "center"),
    ("target", "Target", 82, "w"),
)

CATEGORIES = {
    "physicalDamage": "Physical",
    "specialDamage": "Special",
    "status": "Status",
}

TARGETS = {
    "enemyTargetInBattle": "Single",
    "enemyTeamInBattle": "Multiple",
    "enemyTargetRandom": "Random",
    "allInBattle": "All",
    "self": "Self",
    "selfTarget": "Self",
    "none": "-",
}

# `[token]` -> the English words the game itself shows, read from resource.car. Keys are the
# localisation keys minus their leading `global.`, which is exactly how the game builds them.
PLACEHOLDERS = {
    # stats. `hp` and `sp` have no long form in the table (the value is empty) - the game
    # shows the `.short` one, which is the same "HP"/"SP".
    "hp": "HP",
    "sp": "SP",
    "stat.hp": "HP",
    "stat.sp": "SP",
    "stat.attack": "Attack",
    "stat.defense": "Defense",
    "stat.specialAttack": "Sp. Attack",
    "stat.specialDefense": "Sp. Defense",
    "stat.speed": "Speed",
    "stat.evasion": "Evasion",
    "stat.accuracy": "Accuracy",
    "stat.criticalHitChance": "Critical Hit Chance",
    # conditions
    "condition.burn": "Burn",
    "condition.poison": "Poison",
    "condition.freeze": "Freeze",
    "condition.shock": "Shock",
    "condition.hazy": "Hazy",
    "condition.drowsy": "Drowsy",
    "condition.cursed": "Cursed",
    # everything else that turns up in a skill description
    "squad": "Squad",
    "fruit.singular": "Fruit",
    "fruit.plural": "Fruits",
    "currency.gold": "Gold",
    "trait.singular": "Trait",
    "trait.plural": "Traits",
    "weather.singular": "Weather Condition",
    "weather.plural": "Weather Conditions",
    "weather.heatwave": "Heatwave",
    "weather.snow": "Snow",
    "weather.rain": "Rain",
    "weather.sandstorm": "Sandstorm",
    "weather.twilight": "Twilight",
    "region": "Velua",
    "region.possessive": "Velua's",
}

_MARKUP = re.compile(r"\[([^\]]+)\]")
_BRACE = re.compile(r"\{([^}]+)\}")
_INITIALISMS = {"hp": "HP", "sp": "SP", "xp": "XP"}


def load():
    with open(os.path.join(DATA, "skills.json"), encoding="utf-8") as fh:
        return json.load(fh)


def prettify(name):
    """camelCase / snake_case / SCREAMING_SNAKE -> "Camel Case", keeping HP and SP intact."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name)).replace("_", " ")
    words = []
    for word in re.sub(r"\s+", " ", text).strip().split(" "):
        low = word.lower()
        if low in _INITIALISMS:
            words.append(_INITIALISMS[low])
        elif word:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def resolve(text, skill=None):
    """A description with its placeholders turned into the words the game shows.

    `[tokens]` are localisation keys. `{tokens}` are the skill's own fields, filled in from
    `skill` when one is passed; with no record to read, the token is left visible rather than
    silently dropped - a description reading "a % chance" would be worse than an obvious one.
    """
    if not text:
        return ""
    if skill:
        def brace(match):
            value = skill.get(match.group(1))
            if value is None or value == "":
                return match.group(0)
            return str(value)

        text = _BRACE.sub(brace, text)

    def one(match):
        token = match.group(1)
        if token in PLACEHOLDERS:
            return PLACEHOLDERS[token]
        if token.startswith("type."):
            # Type names are a fixed word list in the game rather than localisation entries,
            # and each is just its own name capitalised - the wiki prints the same words.
            return prettify(token.split(".", 1)[1])
        if " " in token:
            # `[item SOME_UID]` / `[monster SOME_UID]` and friends: the game substitutes the
            # item's or monster's name here. No skill description uses one, so rather than
            # pretend, the UID is shown readably.
            return prettify(token.split(" ", 1)[1])
        return prettify(token.split(".")[-1])

    return _MARKUP.sub(one, text)


def type_name(value):
    return prettify(value)


def category_name(value):
    return CATEGORIES.get(value, prettify(value))


def target_name(value):
    return TARGETS.get(value, prettify(value))


def blank(value):
    """A number the wiki prints as "-" when the skill does not have one."""
    if value is None or value == "" or value == 0:
        return "-"
    return str(value)


def row(skill):
    """The wiki's columns for one skill, as display strings."""
    return {
        "name": skill.get("name") or skill.get("UID", ""),
        "type": type_name(skill.get("type")),
        "sp": str(skill.get("energyCost", "")),
        "power": blank(skill.get("power")),
        "acc": blank(skill.get("accuracy")),
        "ctgry": category_name(skill.get("category")),
        "contact": "yes" if skill.get("makesContact") else "no",
        "target": target_name(skill.get("targetMode")),
    }


def facts(skill):
    """The things the table has no column for, as `label: value` lines.

    Kept out of the table on purpose - the wiki's eight columns are already at the width of
    the window - and shown beside the description instead.
    """
    out = []
    strikes = skill.get("strikes") or []
    counts = sorted({s for s in strikes if isinstance(s, int)})
    if len(counts) > 1:
        out.append(("strikes", " or ".join(str(c) for c in counts)))
    elif counts:
        out.append(("strikes", str(counts[0])))
    priority = skill.get("priority")
    if priority and priority != "other":
        out.append(("priority", prettify(priority)))
    subtypes = skill.get("subtypes") or []
    if subtypes:
        out.append(("subtypes", ", ".join(prettify(s) for s in subtypes)))
    if skill.get("requiresCharge"):
        out.append(("charge", "must be charged before use"))
    if skill.get("isSkillFlash"):
        out.append(("skill flash", "only a Coromon in the right family can learn this"))
    if skill.get("disabledInRandomizer"):
        out.append(("randomizer", "excluded from randomizer runs"))
    return out


def matches(skill, needle):
    needle = needle.strip().lower()
    if not needle:
        return True
    return needle in (skill.get("name") or "").lower() or needle in (skill.get("UID") or "").lower()


def shown(skills, type_filter=None, search=None):
    """The skills to list, sorted by name, after the two filters the CLI and GUI share."""
    picked = [s for s in skills if matches(s, search or "")]
    if type_filter and type_filter.lower() != "all":
        picked = [s for s in picked if (s.get("type") or "").lower() == type_filter.lower()]
    return sorted(picked, key=lambda s: (s.get("name") or "").lower())


def types(skills):
    """Every type present in the data, in the order they first appear."""
    seen = []
    for s in skills:
        t = s.get("type")
        if t and t not in seen:
            seen.append(t)
    return seen


def describe(skill):
    """The full detail pane: description, then the short one, then the facts."""
    out = ["%s  (%s)" % (skill.get("name", ""), skill.get("UID", ""))]
    head = row(skill)
    out.append("%s - %s, %s SP, power %s, accuracy %s, contact %s, target %s"
               % (head["type"], head["ctgry"], head["sp"], head["power"], head["acc"],
                  head["contact"], head["target"]))
    out.append("")
    out.append(resolve(skill.get("description"), skill))
    short = resolve(skill.get("shortDescription"), skill)
    if short and short != resolve(skill.get("description"), skill):
        out.append("")
        out.append("In battle: " + short)
    for field, label in (("descriptionWithReducedRNG", "reduced randomness"),
                         ("shortDescriptionWithReducedRNG", "reduced randomness, in battle")):
        text = resolve(skill.get(field), skill)
        if text:
            out.append("")
            out.append("%s: %s" % (label, text))
    extra = facts(skill)
    if extra:
        out.append("")
        for label, value in extra:
            out.append("%s: %s" % (label, value))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Every skill, in the wiki's columns.")
    ap.add_argument("--type", help="only this type (poison, electric, ...)")
    ap.add_argument("--search", help="name or UID contains this")
    ap.add_argument("--detail", metavar="UID", help="one skill, with its full description")
    args = ap.parse_args(argv)

    skills = load()
    if args.detail:
        needle = args.detail.strip().lower()
        hit = [s for s in skills
               if needle in (s.get("UID") or "").lower() or needle in (s.get("name") or "").lower()]
        if not hit:
            print("no skill matches %r" % args.detail, file=sys.stderr)
            return 1
        for skill in hit:
            print(describe(skill))
            print()
        return 0

    picked = shown(skills, args.type, args.search)
    widths = {"name": 26, "type": 11, "sp": 4, "power": 6, "acc": 5, "ctgry": 9,
              "contact": 8, "target": 9}
    print("  ".join(h.ljust(widths[k]) for k, h, _, _ in COLUMNS).rstrip())
    for skill in picked:
        cells = row(skill)
        print("  ".join(cells[k].ljust(widths[k]) for k, _, _, _ in COLUMNS).rstrip())
    print()
    print("%d skill(s)" % len(picked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
