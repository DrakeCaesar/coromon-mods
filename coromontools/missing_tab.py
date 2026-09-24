"""The Missing tab: which zones can still fill the dex, most missing groups first.

THE COMPANION TO THE DATABASE TAB. The grid there answers "what is in my dex"; this answers the next
question - "so where do I go next". The user: "based on this data I would like to have a list of spawn
zones ordered by how many coromon we don't have yet captured that spawn there - so we can identify the
best zone to grind next to capture the missing coromon to fill out the database".

The grouping rule and the counting live in the tools-root module `missing.py`, which reads nothing but
the game's data and the save record handed to it - this file is the window over that model: a ranking
on top, and, for the row that is selected, exactly WHICH groups are still missing there and how likely
each one is.

Two deliberate differences from the Database tab:

  * the ranking's numbers are counts of GROUPS, not of Coromon, because one group is one thing left to
    do: catching a stage-1 Potent finishes the line's potent group (`missing.Group`).
  * this tab has no side columns, so the table and the pane below it get the whole window width - which
    is why the pane here can print a label, a kind, a level span and a share on one line at all.
"""

import time

import missing as missing_data                 # the tools-root model (see the module docstring)

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSplitter, QVBoxLayout,
                               QWidget)

from . import mapnames
from . import mapview
from .mapview import ZoneMap
from .table import PAYLOAD, Column, DataTable
from .widgets import FittedPane, mono_text, note

try:
    import savefile
except ImportError:                             # reported in the top row instead of a crash
    savefile = None

# "to catch" is the number the whole tab exists for and the column it opens sorted by, and it counts
# LINES: a line missing its potent and its perfect group is one thing to go and get (the user: "it
# should rather count as 1 as one member"), while the missing-kinds column still says how many
# individual catches those lines add up to. "of" is how many lines the zone can fill at all, so a row
# reads "3 of 20".
COLUMNS = (
    Column("area", "Area", 200, "w"),
    Column("zone", "Zone", 160, "w"),
    Column("lines", "to catch", 80, "e", desc_first=True),
    Column("of", "of", 60, "e"),
    # THE BEST SINGLE CHANCE AMONG THE MISSING ONES, so two zones with the same count can be told
    # apart: twelve groups at 2% each is a worse target than eight at 30%.
    Column("easiest", "easiest", 80, "e", desc_first=True),
    # HOW MANY OF THE MISSING LINES ARE SHORT OF EACH KIND, so it reads in the same unit as "to catch"
    # ("lines short of standard 2, potent 5, perfect 5") - a line short of three kinds appears in three
    # of these counts, which is what makes it a count of CATCHES waiting rather than of lines.
    Column("kinds", "lines short of", 230, "w"),
    Column("levels", "levels", 90, "e"),
)

# SHORT KIND LABELS, for the rank column and the pane: the full words are in `missing.KIND_NAMES`.
SHORT = {"A": "standard", "B": "potent", "C": "perfect", "crimsonite": "crimsonite"}

# WHAT THE MAP SAYS WHEN NOTHING IS PICKED, in this tab's own words (the other tabs have their own:
# "pick a zone on the first tab", "pick one of its locations").
MAP_EMPTY = "pick a zone above"

RULE = ("A LINE IS COUNTED HERE WHENEVER ANY OF ITS MEMBERS IS MISSING, and it counts once - a line "
        "short of two potential categories is still one slot to go and find. A kind is missing until "
        "EVERY stage of the line has it: catching a base form and evolving it does not fill the evolved "
        "form's own entry. A line's crimsonite group is its skin unlock. An ordinary spawn can turn up "
        "any of the three potential categories; a crimsonite spawn fills the crimsonite group only.")


def _row(text, width):
    """One left-justified field of the pane's little table, plus its two-space gutter."""
    return "%-*s  " % (width, text)


class MissingTab(QWidget):
    """Spawn zones ordered by how many groups they can still fill."""
    zoneChosen = Signal(object)                 # a Zone, handed to the first tab on a double click

    def __init__(self, prefs, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.error = None
        self.groups_ = []
        self.rows = []
        self._built = False
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 6)

        top = QHBoxLayout()
        top.addWidget(QLabel("missing"))
        # THE ELASTIC LABEL, like the Database tab's: it is allowed to be narrower than its text (so
        # the row never wraps) and its tooltip carries the whole sentence.
        self.summary = QLabel("")
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.summary, 1)
        self.saved_label = QLabel("")
        self.saved_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.saved_label, 1)
        # READ THE SAVE AGAIN: the tab reads it when it is first shown and never again on its own, so
        # this is how a catch made while the window is open gets in - and it is also the answer to
        # "which slot is this showing", which the label beside it then says out loud.
        self.reload_button = QPushButton("Reload save")
        self.reload_button.setToolTip("read the save again - the newest slot that records a dex")
        self.reload_button.clicked.connect(self.reload_save)
        top.addWidget(self.reload_button)
        outer.addLayout(top)

        self.table = DataTable(COLUMNS, sort_key="lines", sort_desc=True)
        self.table.selectionChangedTo.connect(self.show_zone)
        self.table.doubleClicked.connect(self._jump)

        # TWO COLUMNS: the ranking and what is left in the picked zone on the left, THE MAP on the
        # right - the user: "the right half of the missing tab should also show the map with the zone
        # highlighted like the other tabs". It is the same `ZoneMap` the other two tabs draw, so the
        # picked zone is the solid patch here exactly as it is there, and the legend under it comes
        # from the map's own plan.
        left = QWidget()
        box = QVBoxLayout(left)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.table, 3)
        box.addWidget(note(RULE, wrap=900))
        # NOTHING WRAPS IN THE PANE: its columns are aligned with spaces (see `widgets.mono_text`).
        self.detail = mono_text(wrap=False)
        self.detail.setToolTip("the lines still short in the picked zone, and how likely each is")
        box.addWidget(self.detail, 2)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(left)
        self.split.addWidget(self._build_map())
        # THE TABLE'S COLUMNS ARE THE WIDTH OF THE LEFT COLUMN and the map takes the rest: the 900:500
        # this used to be given is a RATIO - it came out about 660 px at the size this window runs at
        # - and a ratio is off by however much the last column then stretched to fill. See
        # `widgets.FittedPane`.
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setChildrenCollapsible(False)
        self.pane_fit = FittedPane(self.split, 0, self.table, elastic=1)
        outer.addWidget(self.split, 1)

    def _build_map(self):
        """The map panel: its caption (only when it could not draw) and the legend under it."""
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        self.map = ZoneMap(empty=MAP_EMPTY)
        self.map_head = QLabel("")
        self.map_head.setWordWrap(True)
        self.legend_row = QWidget()
        self.legend_box = QHBoxLayout(self.legend_row)
        self.legend_box.setContentsMargins(0, 0, 0, 0)
        # ... WITH THE ZOOM CONTROLS every map panel wears (`mapview.head_row`)
        box.addWidget(mapview.head_row(self.map_head, self.prefs))
        box.addWidget(self.legend_row)
        box.addWidget(self.map, 1)
        return panel

    # ------------------------------------------------------------------ the save
    def showEvent(self, event):
        """Read the save the first time the tab is opened, not when the window is built."""
        super().showEvent(event)
        if self._built:
            return
        self._built = True
        self.reload_save()

    def reload_save(self):
        """Re-read the dex record and rebuild the ranking. A failure is a message, not an exception."""
        owned = skins = None
        if savefile is None:
            self.error = "savefile.py is missing"
        else:
            try:
                self.slot, self.saved, self.slots, owned, _seen, skins = savefile.dex_record()
                self.error = None
            except Exception as exc:            # noqa: BLE001 - reported, not hidden
                self.error = "%s: %s" % (type(exc).__name__, exc)
        self.groups_, self.rows = missing_data.load(owned, skins)
        self._fill()
        report = ["save: %s" % (self.error or self.slot)]
        if self.saved:
            report.append("newest of %d" % self.slots)
            report.append(time.strftime("%Y-%m-%d %H:%M", time.localtime(self.saved)))
        text = " \u00b7 ".join(report)
        self.saved_label.setText(text)
        self.saved_label.setToolTip(text)

    # ------------------------------------------------------------------ the ranking
    def _fill(self):
        """Turn the model into table rows, and say in the top row how the dex stands."""
        rows = []
        self._members = {}
        for row in self.rows:
            zone = row["zone"]
            # THE MISSING COROMON OF THIS ZONE, worked out once per zone (`missing.members_missing`):
            # the row needs their level span and best odds, and the pane below prints them - and the
            # two must be the same numbers, so they come from one reading.
            members = missing_data.members_missing(zone, row["missing"])
            self._members[zone.name] = members
            hits = [entry["odds"] for entry in members if entry["odds"]]
            rows.append({
                "area": mapnames.area(zone.map_file),
                "zone": zone.name,
                "lines": "%d" % len(row["missing_lines"]),
                "of": "%d" % len(row["hosted_lines"]),
                "easiest": "%.1f%%" % max(hit[2] for hit in hits) if hits else "-",
                "kinds": self._kinds(row["missing"]),
                "levels": ("L%s-%s" % (min(hit[0] for hit in hits), max(hit[1] for hit in hits))
                           if hits else "-"),
                PAYLOAD: zone,
            })
        self.table.set_rows(rows)

        still_short = len({group.family for group in self.groups_ if not group.caught})
        left = sum(1 for group in self.groups_ if not group.caught)
        helpful = sum(1 for row in self.rows if row["missing"])
        # Terse on purpose, and the tooltip carries the same sentence in full for the width the row
        # cannot spare (the two labels are the elastic ones, as in the Database tab).
        text = ("%d line(s) short, %d group(s) left \u00b7 %d zone(s) can help"
                % (still_short, left, helpful))
        self.summary.setText(text)
        self.summary.setToolTip(text)

    @staticmethod
    def _kinds(groups):
        """The missing groups per kind, short and in a fixed order - "potent 4 \u00b7 perfect 2"."""
        counts = []
        for kind in missing_data.KINDS:
            hits = sum(1 for group in groups if group.kind == kind)
            if hits:
                counts.append("%s %d" % (SHORT[kind], hits))
        return " \u00b7 ".join(counts)

    # ------------------------------------------------------------------ one zone
    def show_zone(self, zone):
        """Point the pane AND the map at the row - or say nothing is left in it.

        BOTH from here, the same way the Database tab's list fills its own two panes below: this is the
        one place that knows the picked zone, so the picture and the words cannot disagree. NO CAPTION
        WHEN THE MAP ANSWERS - the selected row already names the area and the zone, so the map's own
        line ("7 patch(es), 238 tiles at ...") is shown only when it could NOT draw, which is what
        `set_zone` returning False means. The legend comes from the map's plan.
        """
        self.detail.setPlainText(self.detail_text(zone))
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

    def detail_text(self, zone):
        """The zone's headline, then ONE ROW PER COROMON still missing here - see `members_missing`."""
        if zone is None:
            return ""
        row = next((r for r in self.rows if r["zone"].name == zone.name), None)
        if row is None:
            return ""
        missing = row["missing"]
        members = self._members.get(zone.name, [])
        lines = ["%s  (%s)   %d line(s) short \u00b7 %d Coromon to catch"
                 % (zone.name, mapnames.area(zone.map_file), len(row["missing_lines"]), len(members)),
                 ""]
        if not members:
            lines.append("  every Coromon of the lines this zone can fill is already caught.")
            return "\n".join(lines)
        # ONE ROW PER MISSING COROMON, which is what the user asked for after seeing a SWAMP_3_A row
        # that named only the line: "it doesn't list Fibio, which I don't have, but it spawns there",
        # and then "I said that if we are missing a member of line - it should still be listed". The
        # LINE is named on every row too (it is the counted slot, and the thing you evolve within), and
        # the `how` column says whether this Coromon itself spawns here or arrives by evolving one that
        # does - which is why a member with no slot of its own is listed rather than hidden.
        width = max(len(entry["member"].name) for entry in members)
        line_width = max(len(entry["line"]) for entry in members)
        needs = [" · ".join(SHORT[kind] for kind in entry["kinds"]) for entry in members]
        need_width = max(len(text) for text in needs)
        lines.append("  %s%s%s%s  %s" % (
            _row("Coromon", width), _row("needs", need_width), _row("line", line_width),
            _row("how", 11), "levels      share"))
        for entry, text in zip(members, needs):
            hit = entry["odds"]
            lines.append("  %s%s%s%s  %s" % (
                _row(entry["member"].name, width), _row(text, need_width),
                _row(entry["line"], line_width),
                _row("spawns here" if hit else "evolve", 11),
                "%-8s  %5s" % ("L%s-%s" % (hit[0], hit[1]) if hit else "-",
                               "%.1f%%" % hit[2] if hit else "-")))
        return "\n".join(lines)

    def _jump(self, index):
        """A double click hands the zone to the first tab, as the Database tab's list does."""
        if index.isValid():
            payload = self.table.current_payload()
            if payload is not None:
                self.zoneChosen.emit(payload)
