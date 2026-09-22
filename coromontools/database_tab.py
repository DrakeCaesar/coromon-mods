"""The Database tab: one evolutionary line per row, and the three potential categories as three
columns of complete lists.

WHY ROWS ARE FAMILIES. The game's own database draws a line per row - `currentMonsterFamilyUID`
and `getGridBoxIndexByData` in `classes.interface.screens.monsterDatabaseScreen` - and
`dex.lines()` reproduces that from `families.json`: each family's `evolutionObjects`, ordered by
`atLevel`, so a row reads base form to final form left to right. Every one of the 117 dex entries
belongs to a family and the longest line is three stages, so the grid is 51 rows of up to three
icons each.

WHY THREE COLUMN GROUPS. Caught / seen / unknown is PER CATEGORY, not per Coromon: a Coromon caught
as a Potent and later as a Perfect is caught in both, and the game counts them separately
(`playerStats:hasOwnedMonster(uid, category)`). Showing the three side by side is what makes that
visible at a glance - the same line three times, and where the ticks are missing.

THE STATES, taken from the game rather than invented - `monsterDatabaseScreen` picks between FOUR,
by testing hasOwnedMonster(uid, category), then hasSeenMonster(uid, category), then
hasSeenMonster(uid) (any category):
  * caught     - owned in this category: the species' own sprite in its own type frame, the game's
                 `icon_caught` badge in the corner, and the number in `TEXT_LIGHTBLUE` (cyan)
  * seen       - met in this one, not caught: NO badge, the number in `TEXT_DISABLED` (grey), and
                 the sprite and frame darkened by the game's own `darkenedMonsterAlpha`
  * elsewhere  - not met in this category but met in another, so you know it exists: the same
                 icon darkened much harder (`darkenedMonsterAlpha` 0.9 with saturation 0.2, which
                 is what turns it into a silhouette) and the number in `UI_TEXT_RED_DISABLED`
  * unknown    - never met: the game's own `UNKNOWN.png` black square where the frame would be,
                 the sprite as a black silhouette, a "?" in the same font as the numbers, and the
                 entry's disabled (black) plate
Every colour and every darkening value lives in `config.STATE_NUMBERS` / `config.STATE_DARKENING`
with the game's line of code next to it, so this can be checked against the game rather than
believed.

WHERE THE DATA COMES FROM: the save's own dex record, `stats.MONSTERS_OWNED` and
`stats.MONSTERS_SEEN` (see `savefile.monster_record`).

ONE PASS, BUILT ONCE. Every cell is created a single time and then filled: a state change here
means re-reading a save, not switching a filter, so there is no cheaper update to design for - and
3 x 3 x 52 cells is not a grid to rebuild on a keystroke.
"""

import dex
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import icons
from .config import (ICON_ZOOM, STATE_CAUGHT, STATE_ELSEWHERE, STATE_SEEN, STATE_UNKNOWN)
from .text import pretty
from .widgets import note

try:
    import savefile
except ImportError:                                    # pragma: no cover - reported in the tab
    savefile = None

# The game's own categories, in the game's own order, with the names the game gives them.
CATEGORIES = (("A", "Standard"), ("B", "Potent"), ("C", "Perfect"))

NOTES = ("One row per evolutionary line, left to right in its evolution order, with the three "
         "potential categories side by side - so a line is complete in a column when every icon "
         "there carries the caught badge. Caught comes from your save's own dex record, and a "
         "Coromon counts as caught in the category you actually caught it in.")


def line_stages(lines):
    """How many stage columns a line needs: the longest line in the data, never less than one."""
    return max([len(stages) for _family, stages in lines] or [1])


class DatabaseTab(QWidget):
    """The database: a row per evolutionary line, a column group per potential category."""

    def __init__(self, prefs, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.lines = dex.lines()
        self.stages = line_stages(self.lines)
        self.owned = {}                  # uid -> {category: True}
        self.seen = {}
        self.slot = None
        self.error = None
        self.cells = {}                  # (line index, category index, stage) -> (icon, caption)
        self.rules = {}                  # line index -> the rule drawn under it
        self._built = False
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 6)

        top = QHBoxLayout()
        top.addWidget(QLabel("find"))
        self.search = QLineEdit()
        self.search.setToolTip("hide lines whose Coromon do not match")
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        self.counts = QLabel("")
        top.addWidget(self.counts)
        outer.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.grid = QGridLayout(self.holder)
        self.grid.setContentsMargins(6, 6, 6, 6)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(2)
        # ONE SPACER COLUMN BETWEEN THE GROUPS, plus a trailing one that takes the slack. Without
        # them the grid hands the window's spare width to the columns unevenly and the three lists
        # end up at three different pitches; with them every content column keeps its own minimum
        # and the only thing that stretches is the empty column on the right.
        self.gap = 1
        self.stride = self.stages + self.gap
        self.columns = len(CATEGORIES) * self.stride
        for column in range(self.columns):
            if column % self.stride < self.stages:
                self.grid.setColumnMinimumWidth(column, icons.icon_size(ICON_ZOOM)[0] + 16)
            else:
                self.grid.setColumnMinimumWidth(column, 18)
        self.grid.setColumnStretch(self.columns, 1)
        self.scroll.setWidget(self.holder)
        outer.addWidget(self.scroll, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        outer.addWidget(note(NOTES, wrap=1000))

        self._build_header()
        self._build_cells()

    def _build_header(self):
        """The three column names, each over its own group of stage columns."""
        for index, (_category, label) in enumerate(CATEGORIES):
            head = QLabel(label)
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            self.grid.addWidget(head, 0, index * self.stride, 1, self.stages)
        self.grid.addWidget(self._rule(False), 1, 0, 1, self.columns)

    @staticmethod
    def _rule(enabled):
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFrameShadow(QFrame.Shadow.Sunken)
        rule.setEnabled(enabled)
        return rule

    def _build_cells(self):
        """One cell per (line, category, stage), created once and filled by `apply_states`."""
        size = icons.icon_size(ICON_ZOOM)
        for index, (_family, stages) in enumerate(self.lines):
            row = 2 + index * 2
            for cat_index in range(len(CATEGORIES)):
                for stage, mon in enumerate(stages):
                    column = cat_index * self.stride + stage
                    cell = QWidget()
                    box = QVBoxLayout(cell)
                    box.setContentsMargins(1, 1, 1, 1)
                    box.setSpacing(1)

                    picture = QLabel()
                    picture.setFixedSize(QSize(size[0], size[1]))
                    picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    box.addWidget(picture, 0, Qt.AlignmentFlag.AlignHCenter)

                    # THE NAME ONLY - the number is already IN the icon, drawn in the game's own
                    # font and the entry's own state colour (see `icons.icon_pixmap`), so a
                    # "#35 Buzzlet" caption said it twice under every one of the 117 icons.
                    caption = QLabel(mon.name)
                    caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    caption.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                    box.addWidget(caption)

                    self.grid.addWidget(cell, row, column)
                    self.cells[(index, cat_index, stage)] = (picture, caption)
            # A rule under each line, so 51 rows of nine icons stay readable as rows.
            self.rules[index] = self._rule(False)
            self.grid.addWidget(self.rules[index], row + 1, 0, 1, self.columns)

    # ------------------------------------------------------------------ data
    def showEvent(self, event):
        """Read the save the first time the tab is opened, not when the window is built."""
        super().showEvent(event)
        if self._built:
            return
        self._built = True
        self.reload()
        self.apply_states()

    def reload(self):
        """Read the dex record out of the save. Failure is a message, not an exception."""
        if savefile is None:
            self.error = "savefile.py is missing"
        else:
            try:
                self.slot, self.owned, self.seen = savefile.monster_record()
                self.error = None
            except Exception as exc:                   # noqa: BLE001 - reported, not hidden
                self.error = "%s: %s" % (type(exc).__name__, exc)

        known = {mon.uid for _family, stages in self.lines for mon in stages}
        stray = sorted(u for u in set(self.owned) | set(self.seen) if u not in known)
        parts = ["save: %s" % (self.error or self.slot)]
        if stray:
            parts.append("%d recorded Coromon are not in the dex: %s"
                         % (len(stray), ", ".join(stray[:6])))
        parts.append("%d evolutionary lines, %d Coromon." % (len(self.lines), len(known)))
        self.status.setText("  ".join(parts))

    def state_of(self, mon, category):
        """Which of the game's four dex states this Coromon is in, in that potential category.

        The game tests three things, in this order (`monsterDatabaseScreen`):
        `hasOwnedMonster(uid, category)`, `hasSeenMonster(uid, category)` and `hasSeenMonster(uid)`
        - the last one is ANY category, and it is what separates a species you have never met from
        one you have met in another potential category and therefore know exists.
        """
        if savefile is None:
            return STATE_UNKNOWN
        if category in savefile.categories(mon.uid, self.owned):
            return STATE_CAUGHT
        if category in savefile.categories(mon.uid, self.seen):
            return STATE_SEEN
        if savefile.categories(mon.uid, self.seen):
            return STATE_ELSEWHERE
        return STATE_UNKNOWN

    # ------------------------------------------------------------------ contents
    def apply_states(self):
        """Fill every cell from the record: the icon for its state, the name when it is caught."""
        tally = {category: {"caught": 0, "seen": 0} for category, _label in CATEGORIES}
        for index, (_family, stages) in enumerate(self.lines):
            for cat_index, (category, label) in enumerate(CATEGORIES):
                for stage, mon in enumerate(stages):
                    picture, caption = self.cells[(index, cat_index, stage)]
                    state = self.state_of(mon, category)
                    # THE CATEGORY IS THE SPRITE, not a tint: `B` and `C` are the game's own
                    # potent and perfect pictures, so the three columns show three different
                    # drawings of the same Coromon rather than the same one three times. THE STATE
                    # is the game's too: its own darkening, its own number colour, and for a
                    # species you have never met its own black frame and "?" - see
                    # `config.STATES`, where every one of those values is written down with the
                    # line of the game's code it came from.
                    pixmap = icons.icon_pixmap(mon, ICON_ZOOM, category,
                                               caught=(state == STATE_CAUGHT), state=state)
                    if state in tally[category]:
                        tally[category][state] += 1
                    if pixmap is None:
                        picture.clear()
                    else:
                        picture.setPixmap(pixmap)
                    caption.setEnabled(state != STATE_UNKNOWN)
                    tip = "%s\n%s\ntype %s\n%s\n%s stage %d of %d" % (
                        mon.name,
                        "number %s" % mon.number if mon.number else "no dex number",
                        pretty(dex.primary_type(mon) or "?"),
                        {STATE_CAUGHT: "caught",
                         STATE_SEEN: "seen, not caught",
                         STATE_ELSEWHERE: "not met as %s - met in another category" % label.lower(),
                         STATE_UNKNOWN: "not seen yet"}[state],
                        label, stage + 1, len(stages))
                    picture.setToolTip(tip)
                    caption.setToolTip(tip)
        self.counts.setText("    ".join(
            "%s %d caught / %d seen" % (label, tally[category]["caught"], tally[category]["seen"])
            for category, label in CATEGORIES))

    def _filter(self, _text=None):
        """Hide whole LINES that do not match: a row is the unit here, not a Coromon.

        THE DEX NUMBER IS STILL SEARCHED even though the caption no longer shows it: the number is
        how a Coromon is usually looked up ("35", or "#35" pasted from a wiki), and the caption
        dropping it is a reason to keep matching it here, not to lose it. A bare "#" matches
        nothing, so the prefix cannot turn into a wildcard.
        """
        needle = self.search.text().strip().lower()
        number = needle.lstrip("#")
        for index, (_family, stages) in enumerate(self.lines):
            hit = not needle or any(
                needle in mon.name.lower()
                or (number and mon.number and number in str(mon.number))
                for mon in stages)
            for cat_index in range(len(CATEGORIES)):
                for stage in range(self.stages):
                    cell = self.cells.get((index, cat_index, stage))
                    if cell is not None:
                        cell[0].parentWidget().setVisible(hit)
            rule = self.rules.get(index)
            if rule is not None:
                rule.setVisible(hit)
