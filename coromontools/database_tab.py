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
3 x 3 x 51 cells is not a grid to rebuild on a keystroke. That is what RELOAD SAVE is for: it reads
the save again and refills the same cells (`reload_save`), which is the whole update path there is.
THE SAVE IS READ WHEN THE TAB IS FIRST SHOWN, not when the window is built, and again only when
the button is pressed - so a save made while the window is open is picked up by pressing it.
WHERE THE SAVE CAME FROM is reported on the top row, immediately LEFT of the button that re-reads
it, and the FOOTER is for the picked Coromon: `select` puts its wild locations there as the same
sortable list the Coromon tab shows, and picking a row (a DOUBLE click, as in that tab) shows the
area on the map. Potential is not a factor in that - the three category columns are three copies of
one species, and it can only be captured where it can be captured.
THE TOP ROW IS ALWAYS ONE LINE. Nothing on it wraps: what does not fit is ELIDED (see
`_elide_labels`), because a wrapped row is two lines and a clipped row loses letters mid-word.
"""

import time

import dex
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import icons
from .config import (ICON_ZOOM, ICON_ZOOM_KEY, ICON_ZOOM_MAX, ICON_ZOOM_MIN, STATE_CAUGHT,
                     STATE_ELSEWHERE, STATE_SEEN, STATE_UNKNOWN)
from .mapview import ZoneMap
from .table import PAYLOAD, Column, DataTable
from .text import pretty

try:
    import savefile
except ImportError:                                    # pragma: no cover - reported in the tab
    savefile = None

# The game's own categories, in the game's own order, with the names the game gives them.
CATEGORIES = (("A", "Standard"), ("B", "Potent"), ("C", "Perfect"))

# The preferences key the icon scale is kept under, and how wide the two buttons that change it
# are - small on purpose: they sit in the corner of a row that is already tight (see `_elide_labels`).
ZOOM_BUTTON_WIDTH = 22

# What the footer's own heading says before anything has been picked.
HINT = "Click a Coromon, then one of its locations, to see the area on the map."

# AND HERE IS THAT MAP: the first tab's own widget (`ZoneMap`), in the right half of the footer.
# Its own choice of words for having nothing to draw - the first tab's default would tell this tab
# to go and look at the first tab.
MAP_EMPTY = "pick one of its locations"

# HOW TALL THE FOOTER'S MAP IS, in pixels - THE KNOB FOR "A BIGGER MAP". `ZoneMap` picks the scale
# that fits the room it is given (`min(width / map_tiles_w, height / map_tiles_h)`, never below 1:1
# and never above `config.MAP_MAX_SCALE`), so for anything but a very wide map the HEIGHT it gets is
# exactly how big the map comes out. Its own floor is 180; the user: "make the footer taller so we
# can fit a bigger map". The location list beside it grows with it, which is how the 14-location
# Coromon fit without scrolling.
FOOTER_MAP_HEIGHT = 300

# THE LOCATION LIST IS THE COROMON TAB'S, columns and all: it is the same question ("where can I
# catch this one?"), asked from the grid instead of from the list, so it gets the same answers in
# the same shape - and a row carries its Zone (`PAYLOAD`) so picking one can show it on the map.
LOCATION_COLUMNS = (
    Column("area", "Area", 165, "w"),
    Column("zone", "Zone", 165, "w"),
    Column("levels", "levels", 85, "e"),
    Column("share", "share", 65, "e"),
)

# HOW WIDE THE SEARCH FIELD IS ALLOWED TO GET. It used to stretch across the whole row, which was
# fine while it had the row to itself - but the tally, the save report and the Reload button all
# belong on that row too (the user: "make the search bar shorter, so we can fit the save info to the
# left of the load save button instead, so it's compact"), so it is capped - and the cap is about
# 25 characters, which is all a filter needs: every pixel the field does not take is a pixel the
# tally and the save report keep.
SEARCH_WIDTH = 170
# ... and how narrow it may be squeezed before the window refuses to shrink further. Without a
# floor the field collapses to a sliver on a narrow window; with one, what gives instead is the two
# text labels beside it, which ELIDE rather than wrap (see `_elide_labels`).
SEARCH_MIN_WIDTH = 120

# What separates the three category tallies - a dot and a single space, not the four spaces this
# used to be: the same information 15 characters narrower reads as three columns still, because
# the dot is the visible break the eye needs ("reduce the gap between columns there").
TALLY_GAP = " \u00b7 "

# AND THE SAME IDEA IN THE GRID: the width of the spacer COLUMN between the three category groups.
# The column has to exist (see `_build` - without it the three lists drift to three different
# pitches), but it does not have to be wide: with the grid's own spacing either side, this leaves
# the gaps between the groups twice the gaps inside them, which is as much as the eye needs ("reduce
# the gap between the groups of 3 coromon in an evolutionary line so it's all more compact
# horizontally"). The category columns themselves keep their width - they are sized to the icon.
GROUP_GAP = 8

# HOW MUCH AIR EACH GRID COLUMN KEEPS AROUND ITS ICON, in pixels, on top of the icon's own width.
# A column's minimum width is the icon plus this, so it is what separates two neighbouring Coromon
# along with the grid's 5 px spacing - and it was 16, which is 24 px of air between two icons. The
# icons are 160 px wide and the captions under them are about 70, so this is padding, not room
# anything needs: less of it is what makes the whole grid narrower.
GRID_AIR = 8


def line_stages(lines):
    """How many stage columns a line needs: the longest line in the data, never less than one."""
    return max([len(stages) for _family, stages in lines] or [1])


def clamp_zoom(zoom):
    """`zoom` as a whole number inside `config.ICON_ZOOM_MIN`..`ICON_ZOOM_MAX`.

    THE SAVED VALUE IS CLAMPED ON THE WAY IN, not trusted: an old settings file, or one edited by
    hand, can hold a 0 or a 40, and a 0 would divide the whole grid's geometry by nothing.
    """
    try:
        return max(ICON_ZOOM_MIN, min(ICON_ZOOM_MAX, int(zoom)))
    except (TypeError, ValueError):
        return ICON_ZOOM


class DatabaseTab(QWidget):
    """The database: a row per evolutionary line, a column group per potential category."""

    zoneChosen = Signal(object)      # a Zone the map should show - the Coromon tab's own signal
    zoomChanged = Signal(int)        # the icon scale, for the other tab that draws dex icons

    def __init__(self, prefs, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.lines = dex.lines()
        self.stages = line_stages(self.lines)
        self.owned = {}                  # uid -> {category: True}
        self.seen = {}
        self.slot = None                 # the save slot the record came from (the game's key)
        self.saved = None                # ... and its timestamp, as the game wrote it
        self.slots = 0                   # how many slots record a dex at all
        self.error = None
        self.zoom = clamp_zoom(prefs.get(ICON_ZOOM_KEY, ICON_ZOOM))
        self._counts_text = ""           # the tally as it was composed, before any eliding
        self._report_text = ""           # ... and the save report, likewise
        self.top_row = None              # the find row, kept for `_elide_labels`
        self.cells = {}                  # (line index, category index, stage) -> (icon, caption)
        self.click_target = {}           # a cell widget or one of its labels -> Species
        self.rules = {}                  # line index -> the rule drawn under it
        self._built = False
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 6)

        # ONE LINE: find the Coromon, how the record stands, the save it came from, and the button
        # that reads it again - in that order, so the report sits immediately LEFT of the button and
        # nothing needs a line of its own.
        #
        # THE TWO TEXT LABELS ARE THE ELASTIC ONES, and neither wraps: their size policy is Ignored
        # so they may be narrower than their text, they share the row's spare width in proportion to
        # how much text each carries, and whatever does not fit is elided (`_elide_labels`). Wrapping
        # is what the row did before and it made two lines out of it; clipping would cut a letter in
        # half. Their share is also why there is no stretch of its own here - the labels fill it.
        top = QHBoxLayout()
        top.addWidget(QLabel("find"))
        self.search = QLineEdit()
        self.search.setToolTip("hide lines whose Coromon do not match")
        self.search.textChanged.connect(self._filter)
        self.search.setMaximumWidth(SEARCH_WIDTH)
        self.search.setMinimumWidth(SEARCH_MIN_WIDTH)
        top.addWidget(self.search)
        self.counts = QLabel("")
        self.counts.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.counts, 1)
        # WHICH SAVE, right where it is read, and right-aligned so at a wide window it sits against
        # the button rather than floating in the middle of its half of the row.
        self.saved_label = QLabel("")
        self.saved_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.saved_label, 1)
        # READ THE SAVE AGAIN. The tab reads it when it is first shown and never again on its own,
        # so this is how a save made while the window is open gets in - and it is also the way to
        # check which slot the tab is showing, which the report beside it then says out loud.
        self.reload_button = QPushButton("Reload save")
        self.reload_button.setToolTip("read the save again - the newest slot that records a dex")
        self.reload_button.clicked.connect(self.reload_save)
        top.addWidget(self.reload_button)
        # THE ICON SCALE, in the corner past the button: two small buttons and nothing else, because
        # the number is visible in the icons themselves. The value is the app's own state, kept in
        # the preferences like the window's position (see `set_zoom`), so it survives a restart.
        # THE PLAIN CHARACTERS, on the user's word: a typographic minus (U+2212) centres better but
        # is not the character anybody expects to see on a "minus" button.
        self.zoom_out = QPushButton("-")
        self.zoom_in = QPushButton("+")
        for button, tip, delta in ((self.zoom_out, "smaller Coromon icons", -1),
                                   (self.zoom_in, "bigger Coromon icons", 1)):
            # AS TALL AS THE BUTTON BESIDE THEM, and only as wide as a "+" needs: zeroing the theme's
            # padding (below) takes the height down to the glyph's own 14 px, so the height is taken
            # from the Reload button's own hint rather than guessed.
            button.setFixedSize(ZOOM_BUTTON_WIDTH, self.reload_button.sizeHint().height())
            # THE THEME PADS A BUTTON 12 px A SIDE, which is wider than these two buttons are: Qt
            # then has no content rect left and draws an EMPTY button - the user's screenshot shows
            # two blank squares where the - and + should be (22 px wide against a 38 px size hint).
            # Zeroing the padding is what lets a button this small carry a glyph at all; the rest of
            # the theme's button style (fill, border, hover) still applies, because a widget's own
            # stylesheet only outranks it for the properties it names.
            button.setStyleSheet("padding: 0;")
            button.setToolTip(tip)
            button.clicked.connect(lambda *_, step=delta: self.set_zoom(self.zoom + step))
            top.addWidget(button)
        self.top_row = top
        outer.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.grid = QGridLayout(self.holder)
        self.grid.setContentsMargins(6, 6, 6, 6)
        # COMPACT ON PURPOSE: the spacing between two neighbouring columns, on top of whatever slack
        # each column already has around its icon - which is `GRID_AIR` in `_build_cells`, not 16 as
        # it was. Between the category groups the spacer column adds `GROUP_GAP` on top of this.
        self.grid.setHorizontalSpacing(5)
        self.grid.setVerticalSpacing(2)
        # ONE SPACER COLUMN BETWEEN THE GROUPS, plus a trailing one that takes the slack. Without
        # them the grid hands the window's spare width to the columns unevenly and the three lists
        # end up at three different pitches; with them every content column keeps its own minimum
        # and the only thing that stretches is the empty column on the right.
        self.gap = 1
        self.stride = self.stages + self.gap
        self.columns = len(CATEGORIES) * self.stride
        self.grid.setColumnStretch(self.columns, 1)
        self.scroll.setWidget(self.holder)
        outer.addWidget(self.scroll, 1)

        # THE FOOTER IS THE LOCATION LIST ON THE LEFT AND THE MAP ON THE RIGHT, which is what the
        # user meant by "show the map on the right with the area": the area is drawn HERE, in the
        # footer, not by jumping to the first tab - so picking a row costs nothing and can be done
        # for every row in turn. The footer's height is what the map needs (it will not go below
        # 240x180); the list fills it, which comes to about nine of its rows.
        # No prose down here any more - the user: "remove all that useless text in the footer".
        self.footer = QLabel(HINT)
        self.footer.setWordWrap(True)
        outer.addWidget(self.footer)

        self.locations = DataTable(LOCATION_COLUMNS, sort_key="area")
        self.locations.selectionChangedTo.connect(self.show_location)
        self.locations.doubleClicked.connect(lambda *_: self._jump())

        right = QVBoxLayout()
        right.setContentsMargins(8, 0, 0, 0)
        self.map_head = QLabel("")
        self.map_head.setWordWrap(True)
        right.addWidget(self.map_head)
        self.legend_row = QWidget()
        self.legend_box = QHBoxLayout(self.legend_row)
        self.legend_box.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.legend_row)
        self.map = ZoneMap(empty=MAP_EMPTY)
        self.map.setMinimumHeight(FOOTER_MAP_HEIGHT)
        right.addWidget(self.map, 1)

        footer = QHBoxLayout()
        footer.addWidget(self.locations, 1)
        footer.addLayout(right, 1)
        outer.addLayout(footer)
        self.show_location(None)      # the empty map says what to do, from the start

        self._build_header()
        self._build_cells()
        self._apply_zoom()       # the columns and the cells, at the saved scale

    def _elide_labels(self):
        """Fit the tally and the save report to the room they were given, with an ellipsis.

        THE TOP ROW IS ONE LINE BY CONSTRUCTION (see `_build`): the two text labels are Ignored by
        the layout, so their width comes from the row rather than from their own text, and this is
        what puts a shortened version of each in them. Nothing wraps and nothing is cut mid-letter -
        and nothing is lost either, because the whole text goes in the label's tooltip.
        """
        if self.top_row is None:
            return
        pairs = ((self.counts, self._counts_text), (self.saved_label, self._report_text))
        # THE SHARE OF THE ROW COMES FIRST. The stretch factors decide how the row's spare width is
        # split, so they have to be settled before anything is measured against the result - and the
        # layout has to be let run, or the labels are measured at the width they had during the last
        # pass and the elided text comes out shorter than the room it actually has. Eliding does not
        # feed back into any of it: an Ignored size policy means a label's width comes from the row,
        # not from the text this function just shortened.
        for label, text in pairs:
            index = self.top_row.indexOf(label)
            if index >= 0:
                self.top_row.setStretch(index, max(1, len(text) // 8))
        self.layout().activate()
        for label, text in pairs:
            metrics = label.fontMetrics()
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                       max(0, label.width() - 2))
            label.setText(shown)
            # THE TOOLTIP IS THE WAY BACK TO THE WHOLE TEXT, and only worth having when something
            # was actually cut - a tooltip repeating what is already on screen is noise.
            label.setToolTip(text if shown != text else "")

    def resizeEvent(self, event):
        """Re-fit the top row: the room its labels have changed, so what they say has to."""
        super().resizeEvent(event)
        self._elide_labels()

    def _apply_zoom(self):
        """Put the grid's COLUMNS and CELLS at `self.zoom`.

        They have to move together: a cell is a fixed-size label sitting in a fixed-width column,
        so a new scale means resizing both. There is nothing to rebuild - the widgets stay, only
        their sizes change - but there are two places to keep in step, which is why they are done
        in one function, called by `_build` (after the cells exist) and by `set_zoom`.
        """
        size = icons.icon_size(self.zoom)
        for column in range(self.columns):
            if column % self.stride < self.stages:
                self.grid.setColumnMinimumWidth(column, size[0] + GRID_AIR)
            else:
                self.grid.setColumnMinimumWidth(column, GROUP_GAP)
        for picture, _caption in self.cells.values():
            picture.setFixedSize(QSize(size[0], size[1]))

    def set_zoom(self, zoom):
        """Change the size of every dex icon, and REMEMBER it.

        The scale is part of the app's own state, like the window's position: it is a user decision
        (`state.Prefs`, written on the spot), not a constant to re-edit and restart for. The other
        tab that draws these icons is told through `zoomChanged`, because it composes its own - one
        scale for the whole window is the point of the knob.
        """
        zoom = clamp_zoom(zoom)
        if zoom == self.zoom:
            return
        self.zoom = zoom
        self.prefs.set(ICON_ZOOM_KEY, zoom)
        self._apply_zoom()
        self.apply_states()        # the icons themselves, at the new size
        self.zoomChanged.emit(zoom)

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
        """One cell per (line, category, stage), created once and filled by `apply_states`.

        The icons' size is NOT set here - `_apply_zoom` does that for every cell at once, which is
        also what a scale change goes through.
        """
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
                    picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    box.addWidget(picture, 0, Qt.AlignmentFlag.AlignHCenter)

                    # THE NAME ONLY - the number is already IN the icon, drawn in the game's own
                    # font and the entry's own state colour (see `icons.icon_pixmap`), so a
                    # "#35 Buzzlet" caption said it twice under every one of the 117 icons.
                    caption = QLabel(mon.name)
                    caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    caption.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                    box.addWidget(caption)

                    # A CLICK PICKS IT, and the hand cursor says so: a QLabel has no selection state
                    # to use instead, so this is the tab's own picking (see `eventFilter`). The cell,
                    # the icon and the caption all count as the same target.
                    for widget in (cell, picture, caption):
                        widget.installEventFilter(self)
                        widget.setCursor(Qt.CursorShape.PointingHandCursor)
                        self.click_target[widget] = mon

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
                # THE NEWEST SLOT THAT RECORDS A COROMON, which is what `monster_record` has always
                # picked - the autosave counts as a slot like any other, so if the newest thing the
                # game wrote is the autosave, that is what this reads. The slot and its time are
                # kept so the top-right label can say which one it was instead of leaving it a guess.
                found = savefile.dex_slots()
                self.slots = len(found)
                if found:
                    self.saved, self.slot, self.owned, self.seen = found[0]
                else:
                    self.saved = None
                    self.slot, self.owned, self.seen = savefile.monster_record()
                self.error = None
            except Exception as exc:                   # noqa: BLE001 - reported, not hidden
                self.error = "%s: %s" % (type(exc).__name__, exc)

        known = {mon.uid for _family, stages in self.lines for mon in stages}
        stray = sorted(u for u in set(self.owned) | set(self.seen) if u not in known)
        # Terse on purpose: this corner says which slot the record came from and when, and nothing
        # else - the grid below already shows what is in it (and its tally is in the find row).
        # Terse on purpose: this corner says which slot the record came from and when, and nothing
        # else - the grid below already shows what is in it (and its tally is in the find row).
        # "newest of 2" is the answer to "does it read the most recent slot", so it stays; the
        # word "saved" and "slots" do not pay for their width.
        report = ["save: %s" % (self.error or self.slot)]
        if self.saved:
            report[0] += " \u00b7 newest of %d \u00b7 %s" % (
                self.slots, time.strftime("%Y-%m-%d %H:%M", time.localtime(self.saved)))
        if stray:
            report.append("\u00b7 %d recorded Coromon are not in the dex: %s"
                          % (len(stray), ", ".join(stray[:6])))
        self._report_text = "   ".join(report)
        self._elide_labels()

    def select(self, mon):
        """List where that Coromon can be CAPTURED, in the footer.

        POTENTIAL IS NOT A FACTOR, which is the point of the footer being the whole story: the three
        category columns are three pictures of ONE species, and a species' wild locations do not
        depend on the category you happened to catch it in. So the same Coromon lists the same
        places whichever of its three cells was clicked.

        The list is the Coromon tab's (`dex.where`, most likely first, then sorted here by area) with
        the same fields - area, the zone's own name, level range and encounter share - and each row
        carries its Zone, so `show_location` can draw it and `_jump` can hand it to the first tab. A
        species you cannot meet in the grass says so instead, and the list is emptied rather than
        left showing the previous Coromon's - which also clears the map, because `set_rows` tells
        the selection there is nothing to show.
        """
        found = dex.where(mon.uid, mon.skin)
        if not found:
            self.footer.setText("%s: no wild encounters - an evolution, a starter or a gift"
                                % mon.name)
            self.locations.set_rows([])
            return
        self.footer.setText("%s   %d location(s)" % (mon.name, len(found)))
        self.locations.set_rows([{
            "area": pretty(zone.map_file),
            "zone": zone.name,
            "levels": "L%s-%s" % (low, high),
            "share": "%.1f%%" % share,
            PAYLOAD: zone,
        } for zone, low, high, share, _battles in found])

    def show_location(self, zone):
        """Draw that location in the footer's map half - what PICKING A ROW does.

        Selecting is enough here, unlike the first tab's map, because this map is right there: the
        selection moves, the picture follows, and nothing is lost by looking at every row in turn.
        The legend comes from the map's own plan (it is the widget that knows which patches and
        which colours it drew, and which zone is the solid one).

        NO CAPTION WHEN THE MAP ANSWERS: the list beside it already names the area and the zone, so
        the map's own line - "7 patch(es), 238 tiles at (59,35), ..." - is only worth showing when
        the map could NOT answer, which is what `set_zone` returning False means ("no map file",
        "not marked on the map of...", or nothing picked yet).
        """
        drew = self.map.set_zone(zone)
        self.map_head.setText("" if drew else self.map.headline)
        self.map_head.setVisible(not drew)
        while self.legend_box.count():
            item = self.legend_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for (letter, colour, selected) in self.map.legend:
            chip = QLabel(letter)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedWidth(22)
            # the selected zone is the solid one - the same rule the map draws by
            chip.setStyleSheet("background: %s; color: #ffffff; border: %s;"
                               % (colour, "1px solid #ffffff" if selected else "none"))
            self.legend_box.addWidget(chip)
        self.legend_box.addStretch(1)

    def _jump(self):
        """Hand the double-clicked location to the first tab, where the grinder can use it.

        The map above already answers "where is it", so this is the step beyond it: DOUBLE CLICK
        means "take me there", exactly as in the Coromon tab (see `zoneChosen`).
        """
        zone = self.locations.current_payload()
        if zone is not None:
            self.zoneChosen.emit(zone)

    def eventFilter(self, obj, event):
        """A click on a cell picks its Coromon for the footer (`select`)."""
        if event.type() == QEvent.Type.MouseButtonPress and obj in self.click_target:
            self.select(self.click_target[obj])
            return True      # handled: the labels would ignore it, and it would come back to us
        return super().eventFilter(obj, event)

    def reload_save(self):
        """The Reload save button: read the save again and refill every cell from it.

        NOTHING IS REBUILT - the cells, their sizes and the scroll position all stay, and only the
        pixmaps and captions change (`apply_states`) - because the grid is the same grid whatever
        the save says. `_built` is set so that showing the tab again does not read the file a third
        time on top of this.
        """
        self._built = True
        self.reload()
        self.apply_states()

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
                    pixmap = icons.icon_pixmap(mon, self.zoom, category,
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
        self._counts_text = TALLY_GAP.join(
            "%s %d caught / %d seen" % (label, tally[category]["caught"], tally[category]["seen"])
            for category, label in CATEGORIES)
        self._elide_labels()

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
