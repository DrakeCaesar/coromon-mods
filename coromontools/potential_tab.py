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
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

import potential                            # the tools-root model (see the module docstring)

from .table import Column, DataTable

# What each row shows. "1 pick" is kept beside the current chance so the scent's own effect is
# readable without toggling it: with no scent the two columns are identical, which is the honest way
# to show that the scent changes nothing else.
COLUMNS = (
    Column("level", "potential", 80, "e"),
    Column("kind", "category", 100, "w"),
    # THE GAME'S OWN NUMBER, so the table can be checked against `monsterUtility.lu`: the four tables
    # differ only in these weights, and the tail (17-21) is the same in all four.
    Column("weight", "weight", 80, "e"),
    Column("one", "1 pick", 90, "e"),
    Column("chance", "chance", 90, "e", desc_first=True),
    # The same chance drawn to scale, which is what makes the shape (and the tail) readable at a
    # glance: a speed-up fattens the low end, the scent widens the top.
    Column("bar", "distribution", 300, "w"),
)

# How many blocks a full bar is. The widest level (potential 11 or so) fills it.
BAR = 26

# THE MECHANIC IN ONE SENTENCE, and it is a TOOLTIP rather than a note under the table: all 21
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
          "INPUT: not the zone, not the species, not the level.")


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
        # the tab - every pixel of margin here is a level that would otherwise scroll.
        outer.setContentsMargins(8, 6, 8, 4)
        outer.setSpacing(4)

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
        outer.addWidget(self.table, 1)

    @staticmethod
    def _elastic(label, align):
        """A label that takes the width the row can spare instead of asking for its own text."""
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        return label

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
                # NEVER ZERO BLOCKS for a level the table gives weight to: a bar that vanishes would
                # read as "impossible", and every level here is possible - just unlikely.
                "bar": "\u2588" * max(1, int(round(now / widest * BAR))) if now else "",
                "one": potential.percent(single),
                "chance": potential.percent(now),
            })
        self.table.set_rows(rows)

        kinds = potential.kind_chances(count, picks)
        summary = ("%d speed-up(s) \u00b7 the game's %d-weight table \u00b7 %s"
                   % (count, potential.total(count),
                      "%d picks (Potent Scent)" % picks if scented else "1 pick"))
        odds = ("  ".join("%s %s" % (potential.KIND_NAMES[letter], potential.percent(kinds[letter]))
                          for letter, _name, _low, _high in potential.KINDS)
                + "  \u00b7  %s" % potential.one_in(kinds["C"]))
        for label, text in ((self.summary, summary), (self.odds, odds)):
            label.setText(text)
            label.setToolTip("%s\n\n%s" % (text, DETAIL))
