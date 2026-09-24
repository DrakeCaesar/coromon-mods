#!/usr/bin/env python3
"""
potential.py - how a wild Coromon's Potential is rolled, and what the game's speed settings do to it.

READ OUT OF THE GAME, not modelled. `classes.modules.utilities.monsterUtility.lu` rolls it like this:

    local distribution = potentialDistributionByAmountOfSpeedUpSettings[
          (gameSettings:getTimescaleForBattle()    > 1 and 1 or 0)
        + (gameSettings:getTimescaleForOverworld() > 1 and 1 or 0)
        + (gameSettings:shouldShowEncounterAnimations() and 0 or 1)]

    return array.findMax(array.constructByFunction(
        battleEffectUtility:mutateAmountOfPotentialRolls(),
        function() return table.rollKeyFromTotal(distribution) end))

so the Potential is **the highest of N weighted picks**. One pick walks one of the four weight tables
below (`table.rollKeyFromTotal` rolls `math.random(total)` and subtracts weights until the roll runs
out - the key it returns IS the potential, 1..21), and N is 1 normally and 3 while a Potent Scent is
active. The four tables are the 21-entry literals in `monsterUtility.lu` lines 117-120, keyed 0..3.

THE THREE FLAGS, exactly as the game tests them:
  * "Battle speed multiplier" and "Game speed multiplier" count when they are ABOVE x1 - `> 1` is the
    whole test, so x1.5 and x2 are worth the same one point.
  * "Show encounter animations" counts when it is OFF (`shouldShowEncounterAnimations` is false).
  * "Show weather animations" is a DIFFERENT setting and is not read here at all.

A SPEED-UP NEVER TOUCHES THE TAIL: the weights for 17..21 are identical in all four tables, and each
speed-up doubles the weight of low potentials instead - which dilutes the good ones. From table 0 to
table 3 the potent rate falls from 2.880% to 2.014% (about 30% fewer), and the perfect weight stays 1
while the total grows from 3194 to 4567.

THE POTENT SCENT is `classes.items.SCENT_ADD_POTENTIAL_ROLL`: `mutateAmountOfPotentialRolls(value)` is
`value + 2` (and `mutateAmountOfMonsterRolls(value)` is `value + 1`), for 6 minutes. With N = 3 the
potent rate goes from 2.382% to 6.98% under the settings this was written for (two speed-ups), so the
scent is worth about three times as many potent Coromon.

NOTHING ELSE IS AN INPUT: not the zone, not the species, not the level, not the weather, and not the
difficulty. A zone decides WHICH Coromon you meet (a shuffle bag - see `encounters.py`), never how good
it is. The one exception anywhere in the game is a HAND-PLACED monster on a map, whose potential can be
authored as a Tiled property (`spawnables/monster.lu`) - a random encounter never is.

The window over this is `coromontools/potential_tab.py`; this module is only the arithmetic, so it can
be printed, tested and imported without Qt.
"""

# The keys the game stores the three flags under (see `classes.modules.gameSettings`).
BATTLE_KEY = "TIMESCALE_FOR_BATTLE"
OVERWORLD_KEY = "TIMESCALE_FOR_OVERWORLD"
ANIMATIONS_KEY = "SHOW_ENCOUNTER_ANIMATIONS"
# The game's own fallback when the key was never written: `settings.X == nil and true or X`, i.e.
# animations are ON by default, and both multipliers fall back to 1.0.
ANIMATIONS_DEFAULT = True
SPEED_DEFAULT = 1.0
# What the settings screen offers, in the order it offers it ("x1 / x1.5 / x2"). Only the first is
# free: everything above it costs one flag.
SPEEDS = (1.0, 1.5, 2.0)

# THE FOUR TABLES, indexed by how many of the three flags are on. Weight per potential 1..21.
TABLES = {
    0: (1, 4, 8, 16, 64, 128, 192, 256, 320, 384, 448, 384, 320, 256, 192, 128, 40, 32, 16, 4, 1),
    1: (2, 8, 16, 32, 128, 256, 192, 256, 320, 384, 448, 384, 320, 256, 192, 128, 40, 32, 16, 4, 1),
    2: (2, 8, 16, 32, 128, 256, 384, 512, 320, 384, 448, 384, 320, 256, 192, 128, 40, 32, 16, 4, 1),
    3: (2, 8, 16, 32, 128, 256, 384, 512, 640, 768, 448, 384, 320, 256, 192, 128, 40, 32, 16, 4, 1),
}
LEVELS = tuple(range(1, len(TABLES[0]) + 1))
MAX_SPEED_UPS = max(TABLES)

# THE GAME'S OWN CATEGORIES, with the letters its data uses: `monsterUtility:
# getPotentialCategoryForPotential` returns "A" up to 16, "B" up to 20 and "C" for 21, and the window
# writes them as Standard / Potent / Perfect everywhere else.
KINDS = (("A", "Standard", 1, 16), ("B", "Potent", 17, 20), ("C", "Perfect", 21, 21))
KIND_NAMES = {letter: name for letter, name, _low, _high in KINDS}

# WHAT A PICK IS AND HOW MANY THERE ARE: `battleEffectUtility:mutateAmountOfPotentialRolls()` starts
# at 1 (`mutateValue(effects, key, 1.0)`) and the scent's own `mutateAmountOfPotentialRolls` body is
# `value + 2`.
BASE_ROLLS = 1
SCENT_ROLLS = 2
SCENT_ITEM = "SCENT_ADD_POTENTIAL_ROLL"
SCENT_MINUTES = 6


def speed_ups(battle=SPEED_DEFAULT, overworld=SPEED_DEFAULT, animations=ANIMATIONS_DEFAULT):
    """How many of the three flags are on - the key into `TABLES`, 0..3.

    A missing value falls back to the game's own default (x1, x1, animations on), so a save or a
    settings blob that does not carry the key reads as "nothing sped up" rather than as a flag - and
    `None` means the same thing to all three, which is the only reading that cannot double-count a
    setting the game itself would treat as unset.
    """
    battle = battle if battle else SPEED_DEFAULT
    overworld = overworld if overworld else SPEED_DEFAULT
    if animations is None:
        animations = ANIMATIONS_DEFAULT
    return ((1 if battle > 1 else 0)
            + (1 if overworld > 1 else 0)
            + (0 if animations else 1))


def weights(count):
    """The game's weight for each potential 1..21, from the table that many flags pick."""
    return TABLES[max(0, min(MAX_SPEED_UPS, int(count)))]


def total(count):
    """The table's total weight - the number the pick rolls against, and its '1 full circle'."""
    return sum(weights(count))


def rolls(scent=False):
    """How many picks the roll takes the best of: 1 normally, 3 while a Potent Scent is running."""
    return BASE_ROLLS + (SCENT_ROLLS if scent else 0)


def single(count, level):
    """The chance ONE pick lands on `level`: the game's weight over the table's total."""
    table = weights(count)
    return table[level - 1] / float(sum(table))


def chances(count, picks=1):
    """The chance the FINAL potential is exactly each level, as a 21-long list.

    The best of `picks` picks over the table: P(max <= v) is F(v) ** picks, so
    P(max == v) = F(v) ** picks - F(v-1) ** picks. This is why more picks widen the tail without
    moving a single weight - the table is the same, only the best of several draws shows.
    """
    table = weights(count)
    tot = float(sum(table))
    out, below, previous = [], 0.0, 0.0
    for weight in table:
        below += weight / tot
        out.append(below ** picks - previous ** picks)
        previous = below
    return out


def kind_chances(count, picks=1):
    """`{letter: chance}` for the three categories - they sum to 1.

    From the same cumulative distribution as `chances`, which is also why the three add up exactly:
    Standard is F(16), Potent is F(20) - F(16) and Perfect the rest.
    """
    table = weights(count)
    tot = float(sum(table))
    up_to = {16: sum(table[:16]) / tot, 20: sum(table[:20]) / tot}
    return {"A": up_to[16] ** picks,
            "B": up_to[20] ** picks - up_to[16] ** picks,
            "C": 1.0 - up_to[20] ** picks}


def level_kind(level):
    """The category letter a potential belongs to (`getPotentialCategoryForPotential`)."""
    for letter, _name, low, high in KINDS:
        if low <= level <= high:
            return letter
    return KINDS[-1][0]


def percent(value):
    """A probability as a percentage, with the digits the row is worth.

    Three shapes on purpose: the body of the table is a few per cent (two decimals), the potent tail
    is under one (three), and the perfect weight is ~0.03% (five) - one format would either invent
    precision for the middle or throw the tail away.
    """
    pct = 100.0 * value
    if pct >= 1:
        return "%.2f%%" % pct
    if pct >= 0.01:
        return "%.3f%%" % pct
    return "%.5f%%" % pct


def one_in(value):
    """`"1 in 1,288"` - the same chance said the way a grinder asks for it."""
    if not value:
        return "-"
    return "1 in {:,}".format(int(round(1.0 / value)))


def read_settings():
    """The three flags as THE GAME has them right now: `(battle, overworld, encounter_animations)`.

    Read out of the game's own preferences (`savefile.global_settings` - the same database the save
    comes from, a different row), because the roll is a function of those three and nothing else: the
    tab opens on what the game will really do, and its controls are then a what-if. Anything that
    cannot be read falls back to the game's own defaults.
    """
    settings = {}
    try:
        import savefile
        settings = savefile.global_settings()
    except Exception:                       # noqa: BLE001 - no save, no settings blob, no crash
        settings = {}
    animations = settings.get(ANIMATIONS_KEY)
    if animations is None:
        animations = ANIMATIONS_DEFAULT
    return (settings.get(BATTLE_KEY) or SPEED_DEFAULT,
            settings.get(OVERWORLD_KEY) or SPEED_DEFAULT,
            bool(animations))


def report(count, scent=False):
    """The table as text: one line per potential with its weight and its chance, plus the header.

    Used by `python -m coromontools --selftest` (`potential.report(...)`), so the numbers can be read
    without opening a window - and so the arithmetic has one canonical rendering.
    """
    picks = rolls(scent)
    diff = chances(count, picks)
    base = chances(count, 1)
    lines = ["potential table: %d speed-up(s), %d-weight table, %d pick(s)%s"
             % (count, total(count), picks, " (Potent Scent)" if scent else "")]
    lines.append("  level  category  weight     1 pick    %s"
                 % ("1 pick" if picks == 1 else "%d picks" % picks))
    for level, chance, one in zip(LEVELS, diff, base):
        lines.append("  %5d  %-8s  %6d   %8s  %8s"
                     % (level, KIND_NAMES[level_kind(level)], weights(count)[level - 1],
                        percent(one), percent(chance)))
    kinds = kind_chances(count, picks)
    lines.append("  " + "  ".join(
        "%s %s%s" % (KIND_NAMES[letter], percent(kinds[letter]),
                     # "1 in N" only where it reads as odds: a 93% Standard is not "1 in 1.08"
                     " (%s)" % one_in(kinds[letter]) if kinds[letter] < 0.05 else "")
        for letter, _name, _low, _high in KINDS))
    return "\n".join(lines)
