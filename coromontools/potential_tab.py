"""The Potential tab: what the game's own settings do to a wild Coromon's Potential.

THE MODEL IS `potential.py` IN THE TOOLS ROOT - the four weight tables lifted out of
`monsterUtility.lu`, the flag count that picks one, and the best-of-N roll. This file is only the
window over it.

WHY THE TAB EXISTS - the user: "could you add another tab to the gui with checkboxes for potent
scent, battle animations, overworld and battle speed settings that would show the real values for
each potential level". So the controls are exactly the inputs the game's roll reads:

  * the two speed multipliers, TICKS rather than pickers - the game tests `> 1` and nothing else, so
    x1.5 and x2 are worth the same one flag and a three-way control would offer a choice that does
    not exist (the user: "make the checkboxes like speed > 1, no need for a dropdown if the effect
    change is bianry"); which multiplier the game really has is in the tooltip
  * "Show encounter animations" (the game's own wording) - it costs one flag while it is OFF
  * the Potent Scent - not a setting at all: it is the item (`SCENT_ADD_POTENTIAL_ROLL`) that runs
    for six minutes and takes the best of THREE picks instead of one

and the table below prints, for every potential level, the game's own weight, the chance of landing
on exactly that level and the distribution drawn to scale. Nothing about a zone or a species is an
input, so this one tab answers the whole question "why did I get so many potent ones there".

IT OPENS ON THE PLAYER'S REAL SETTINGS: `potential.read_settings()` reads them out of the game's own
preferences (the same database the save comes from, a different row), and "From the game" puts them
back after the controls have been played with. Nothing here is SAVED - the controls are a what-if, and
a remembered copy could quietly disagree with the game it is supposed to describe.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

import potential                            # the tools-root model (see the module docstring)

from .table import COLUMN_PAD, Column, DataTable

# What each row shows. "1 pick" is kept beside the current chance so the scent's own effect is
# readable without toggling it: with no scent the two columns are identical, which is the honest way
# to show that the scent changes nothing else. "fraction" is the same chance as an EXACT fraction,
# the way the wiki writes its odds tables: `320/3194` for one pick of level 9, and under the scent a
# nine-digit numerator over `total ** 3` (39826498375 for the 3415-weight table).
COLUMNS = (
    Column("level", "potential", 78, "e"),
    Column("kind", "category", 88, "w"),
    # THE GAME'S OWN NUMBER, so the table can be checked against `monsterUtility.lu`: the four tables
    # differ only in these weights, and the tail (17-21) is the same in all four.
    Column("weight", "weight", 74, "e"),
    Column("one", "1 pick", 78, "e"),
    Column("chance", "chance", 78, "e", desc_first=True),
    Column("fraction", "fraction", 190, "e"),
    # THE SHAPE OF THE TABLE, DRAWN TO SCALE (`table.BarDelegate`): the value is the level's own
    # chance over the widest one, so the bar is proportional to the very number printed beside it. It
    # is a number rather than a row of block characters, which is what made this column a staircase of
    # six lengths instead of a distribution.
    Column("bar", "distribution", 170, "w", bar=True),
)

# THE POTENTIFLATOR, the second half of the same question: not "what will I meet" but "what does the
# machine turn this into". It has ONE ROW PER POTENTIAL TOO (1..20), which is why the two tables stand
# side by side - they line up - and why neither costs the other any height. THIS ONE COMES FIRST, the
# order the user picked.
# `draws / weight` is the wiki's own rendering: how many full rolls the machine takes the best of,
# over the weight of potential 21. It is the EXPECTED NUMBER OF HITS rather than the chance, so it
# reads a hair high (60/3194 = 1.879%, where the real chance is 1.861%); the last column is exact,
# and both are there so the wiki's numbers can be recognised and checked.
REROLL_COLUMNS = (
    # THE FIRST HEADING NAMES THE TABLE, which is how the second table says what it is without a
    # caption above it (15 px of caption is 15 px off the last row of a 21-row table at 1040x560).
    Column("from", "Potentiflator", 112, "e"),
    Column("draws", "draws", 62, "e"),
    Column("fraction", "draws / weight", 118, "e"),
    # "to perfect" rather than "chance to perfect": the heading beside it already names the table.
    Column("chance", "to perfect", 100, "e", desc_first=True),
)

# How many blocks a full bar was, back when the distribution column was written in block characters.
# The column is PAINTED now (`table.BarDelegate`) so a level's bar is exactly its share of the peak -
# this is gone: see the bar column in COLUMNS.

# THE COLUMNS THAT HOLD THE NUMBERS THE SWITCHES MOVE, and so are pinned to their WIDEST case. The
# user: "so that the table column there does not change so much when flipping the checkboxes, could you
# make it so it's always the width it would show when the longest fractions are calculated? that would
# be the everything on, but animations off" - everything on is all THREE flags (the 4567-weight table)
# plus the scent's three picks, which is the longest numerator over the longest denominator. Without
# this the wild table's fraction column jumps between 320/3194 and 5409692416/32584025384, and the
# table (and the one beside it, which sits after it) shifts sideways with every tick.
PINNED = ("weight", "one", "chance", "fraction")
REROLL_PINNED = ("chance",)

# THE MECHANIC IN ONE SENTENCE, and it is a TOOLTIP rather than a note under the tables: all 21
# levels have to be visible at the window's real size (1040x560, where the user runs this beside the
# game) and a wrapped note costs exactly the two rows that would then scroll - the perfect row among
# them. The numbers above already say what the settings do; this says why.
RULE = ("Potential is the HIGHEST of N weighted picks from the game's own table: N is 1, or 3 while "
        "a Potent Scent is running.")

# ... and the long version, which is the tooltip of the two numbers it explains.
DETAIL = (RULE + "  The three flags are the settings screen's \"Battle speed multiplier\", \"Game "
          "speed multiplier\" - anything above x1 counts, so x1.5 and x2 are the same - and \"Show "
          "encounter animations\", which counts while it is OFF. \"Show weather animations\" is a "
          "different setting and is not read at all. A speed-up never changes the weights of 17-21: "
          "it doubles the weight of low potentials, which dilutes the good ones. NOTHING ELSE IS AN "
          "INPUT: not the zone, not the species, not the level.  THE FRACTIONS ARE THE SAME NUMBERS "
          "AS THE PERCENTAGES: an exact numerator over `total ** picks`, i.e. 3194 with one pick and "
          "39826498375 under a scent on the 3415-weight table.")

# THE POTENTIFLATOR'S OWN RULE, on its own table: the four controls above drive THIS one too, because
# the machine does not roll anything of its own - it rolls `rollPotential()` again and again.
REROLL_RULE = ("Hand the Coromon to the Potentiflator and it comes back as the BEST of "
               "`draws` full rolls of exactly the table above, plus its own potential - so it can "
               "never come out lower. Potentials 20 and 21 are 21 already: no roll at all.")
REROLL_DETAIL = (REROLL_RULE + "  The draws are the game's own `amountOfRerollsByPotentialValue` "
                 "(3 at potential 1, one more per level to 19 at 17, then 30 at 18 and 60 at 19), "
                 "and the value is decided AT THE HANDOVER - the 1000 steps afterwards only collect "
                 "it, so a Potent Scent has to be running when you hand it over, not while you walk. "
                 "`draws / weight` is the wiki's own fraction (the expected number of hits, which "
                 "reads slightly high); `chance to perfect` is the exact chance, "
                 "1 - (1 - 1/total) ** draws per pick, so 1.861% where the wiki prints 1.879%.")


class PotentialTab(QWidget):
    """The real odds of each Potential level, under the settings the player picks here."""

    def __init__(self, prefs=None, parent=None):
        super().__init__(parent)
        # `prefs` is accepted and deliberately unused: this tab remembers nothing, because every
        # value on it is a description of the game's current state rather than a choice of the user's.
        self._build()
        self.from_game()

    # ------------------------------------------------------------------ layout
    def _build(self):
        outer = QVBoxLayout(self)
        # TIGHT ON PURPOSE: 21 rows at 20 px plus the header is 445, and at 1040x560 that is most of
        # the tab - every pixel of margin here is a level that would otherwise scroll. The two
        # captions moved that budget by a couple of pixels, so the margins and the spacing are the
        # levers that were left (see the "all 21 levels fit" check in `ui_smoke`).
        outer.setContentsMargins(8, 0, 8, 0)
        outer.setSpacing(0)

        controls = QHBoxLayout()
        self.scent = QCheckBox("Potent Scent")
        self.scent.setToolTip(
            "%s: %d potential picks instead of %d, and one more monster roll, for %d minutes.\n"
            "Not read from your save - tick it to see what a scent would do."
            % (potential.SCENT_ITEM, potential.rolls(True), potential.rolls(False),
               potential.SCENT_MINUTES))
        self.scent.toggled.connect(self._changed)
        controls.addWidget(self.scent)

        controls.addSpacing(12)
        # A TICK EACH, NOT A PICKER: the game's test is `> 1`, so x1.5 and x2 are worth exactly the
        # same one flag and a three-way control would offer a choice that does not exist (the user:
        # "make the checkboxes like speed > 1, no need for a dropdown if the effective change is
        # bianry"). Which multiplier the game really has is said in the tooltip instead - it is the
        # only thing the exact value is good for.
        self.battle = QCheckBox("battle speed > x1")
        self.overworld = QCheckBox("game speed > x1")
        for tick, what in ((self.battle, "Battle speed multiplier"),
                           (self.overworld, "Game speed multiplier")):
            tick.setToolTip("%s.\nAnything ABOVE x1 costs one speed-up flag, so x1.5 and x2 are the "
                            "same here." % what)
            tick.toggled.connect(self._changed)
        controls.addWidget(self.battle)
        controls.addSpacing(12)
        controls.addWidget(self.overworld)

        controls.addSpacing(12)
        self.animations = QCheckBox("encounter animations")
        self.animations.setToolTip(
            "The settings screen's \"Show encounter animations\".\n"
            "It costs a speed-up flag while it is OFF, so untick it only if you want the faster\n"
            "encounters: it is worth about 6.5% of your potent rate.\n"
            "\"Show weather animations\" is a different setting and is not read at all.")
        self.animations.toggled.connect(self._changed)
        controls.addWidget(self.animations)

        controls.addStretch(1)
        self.reload_button = QPushButton("From the game")
        self.reload_button.setToolTip("put the controls back on the settings the game really has")
        self.reload_button.clicked.connect(self.from_game)
        controls.addWidget(self.reload_button)
        outer.addLayout(controls)

        # TWO ELASTIC LABELS, the pattern the Database and Missing tabs use: they are allowed to be
        # narrower than their text so the row never wraps, and each carries the whole sentence as a
        # tooltip. Left: which table and how many picks. Right: the odds that matter.
        head = QHBoxLayout()
        self.summary = self._elastic(QLabel(""), Qt.AlignmentFlag.AlignLeft)
        head.addWidget(self.summary, 1)
        self.odds = self._elastic(QLabel(""), Qt.AlignmentFlag.AlignRight)
        head.addWidget(self.odds, 1)
        outer.addLayout(head)

        self.table = DataTable(COLUMNS, sort_key="level")
        self.table.setToolTip(DETAIL)
        self.reroll_table = DataTable(REROLL_COLUMNS, sort_key="from", sort_desc=False)
        self.reroll_table.setToolTip(REROLL_DETAIL)
        self._pin_widths()

        # THE POTENTIFLATOR'S TABLE COMES FIRST, with no separator between it and the wild table:
        # the user - "the potentiflator table can go right after to the first table to the right, no
        # need to add a separator, cause it pushes it way too much to the side", then "switch the
        # order of the tables, so potentinflator goes first". So they are two widgets in a row with the
        # slack AFTER both of them rather than between them, each as wide as its own columns ask
        # (`table.DataTable.sizeHint`). Narrower than that and a table scrolls itself, which is what a
        # table is for.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self.reroll_table)
        row.addWidget(self.table)
        row.addStretch(1)
        outer.addLayout(row, 1)

    @staticmethod
    def _elastic(label, align):
        """A label that takes the width the row can spare instead of asking for its own text."""
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        return label

    # ------------------------------------------------------------------ the columns' widths
    def _pin_widths(self):
        """Give the number columns ONE width: the widest value they can ever hold.

        MEASURED with the table's own font, over the values themselves rather than guessed from a
        character count (this is a proportional font, so the longest string is not always the widest
        one): the widest WEIGHT is the biggest step of the 4567 table, the widest odds are the smallest
        ones - `0.00000%` is longer than `16.60%` - and the widest fraction is the scent's, a ten digit
        numerator over `total ** 3`. The heading is measured too ("fraction" is wider than `320/3194`
        would be on its own), so nothing is ever elided at any setting.
        """
        counts = sorted(potential.TABLES)
        pickses = (1, potential.rolls(True))
        widest = {
            "weight": ["%d" % potential.weights(count)[level - 1]
                       for count in counts for level in potential.LEVELS],
            "one": [potential.percent(potential.single(count, level))
                    for count in counts for level in potential.LEVELS],
            "chance": [potential.percent(potential.chances(count, picks)[level - 1])
                       for count in counts for picks in pickses for level in potential.LEVELS],
            "fraction": ["%d/%d" % potential.fraction(count, picks, level)
                         for count in counts for picks in pickses for level in potential.LEVELS],
        }
        reroll = [potential.percent(potential.reroll_chance(level, count, scent))
                  for level in range(1, potential.REROLL_CERTAIN + 1)
                  for count in counts for scent in (False, True)]
        for table, columns in ((self.table, widest),
                               (self.reroll_table, {"chance": reroll})):
            metrics = QFontMetrics(table.font())
            for index, column in enumerate(table.model_.columns):
                if column.key not in columns:
                    continue
                need = max(metrics.horizontalAdvance(text) for text in columns[column.key])
                need = max(need, metrics.horizontalAdvance(column.heading),
                           table.horizontalHeader().sectionSizeHint(index))
                column.width = need + COLUMN_PAD
                column.fixed = True

    # ------------------------------------------------------------------ the controls
    def from_game(self):
        """Put the controls back on the settings the GAME has, then redraw.

        A multiplier only has two states worth showing, so the tick is `value > 1` and the exact
        value goes in the tooltip - which is also the only place it can be read off now that the
        control is binary. The scent is unticked and stays a what-if: it is an item used for six
        minutes rather than a setting, so "what the game has" says nothing about it. The widgets are
        set with their own signals quiet, so all of it redraws the table once.
        """
        battle, overworld, animations = potential.read_settings()
        for tick, value, what in ((self.battle, battle, "Battle speed multiplier"),
                                  (self.overworld, overworld, "Game speed multiplier")):
            tick.blockSignals(True)
            tick.setChecked(bool(value > 1))
            tick.setToolTip("%s - your game has x%g.\nAnything ABOVE x1 costs one speed-up flag, so "
                            "x1.5 and x2 are the same here." % (what, value))
            tick.blockSignals(False)
        for tick, value in ((self.animations, animations), (self.scent, False)):
            tick.blockSignals(True)
            tick.setChecked(bool(value))
            tick.blockSignals(False)
        self._fill()

    def configuration(self):
        """`(speed-ups, scent)` as the controls have them - the one place that reads the controls.

        The two multipliers are passed as the tick's own value, where 1 means x1 (off) and 2 means
        "above x1" (on) - which is all `potential.speed_ups` can tell apart anyway.
        """
        count = potential.speed_ups(2 if self.battle.isChecked() else 1,
                                    2 if self.overworld.isChecked() else 1,
                                    self.animations.isChecked())
        return count, self.scent.isChecked()

    def _changed(self, *_args):
        self._fill()

    # ------------------------------------------------------------------ the numbers
    def _fill(self):
        """Recompute the whole table for the current controls, and say what they add up to."""
        count, scented = self.configuration()
        picks = potential.rolls(scented)
        chance = potential.chances(count, picks)
        one = potential.chances(count, 1)
        table = potential.weights(count)
        widest = max(chance)

        rows = []
        for level, now, single in zip(potential.LEVELS, chance, one):
            rows.append({
                "level": "%d" % level,
                "kind": potential.KIND_NAMES[potential.level_kind(level)],
                "weight": "%d" % table[level - 1],
                # A FRACTION OF THE BAR, painted by `table.BarDelegate` - no rounding to whole
                # characters, so a level at 59% of the peak draws a bar at exactly 59% of the column.
                "bar": now / widest if widest else 0.0,
                "one": potential.percent(single),
                "chance": potential.percent(now),
                "fraction": "%d/%d" % potential.fraction(count, picks, level),
            })
        self.table.set_rows(rows)
        # THE SAME SWITCHES DRIVE THE SECOND TABLE: the machine rolls this very distribution, so a
        # speed-up and the scent move both tables at once - which is the point of showing them
        # together.
        self.reroll_table.set_rows(potential.reroll_rows(count, scented))

        kinds = potential.kind_chances(count, picks)
        summary = ("%d speed-up(s) \u00b7 the game's %d-weight table \u00b7 %s"
                   % (count, potential.total(count),
                      "%d picks (Potent Scent)" % picks if scented else "1 pick"))
        odds = ("  ".join("%s %s" % (potential.KIND_NAMES[letter], potential.percent(kinds[letter]))
                          for letter, _name, _low, _high in potential.KINDS)
                + "  \u00b7  %s" % potential.one_in(kinds["C"])
                # ... and the machine's own answer for a Coromon at 19, which is what a grinder hands
                # over: the row is in the table, this is what makes it readable without hunting.
                + "  \u00b7  to perfect from 19: %s"
                % potential.percent(potential.reroll_chance(19, count, scented)))
        for label, text in ((self.summary, summary), (self.odds, odds)):
            label.setText(text)
            label.setToolTip("%s\n\n%s" % (text, DETAIL))
