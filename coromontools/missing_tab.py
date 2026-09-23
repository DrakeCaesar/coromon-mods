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
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import mapnames
from .table import PAYLOAD, Column, DataTable
from .widgets import mono_text, note

try:
    import savefile
except ImportError:                             # reported in the top row instead of a crash
    savefile = None

# "to catch" is the number the whole tab exists for and the column it opens sorted by; "of" is how
# many groups the zone can fill at all, so a row reads "4 of 12" - four left, out of twelve the zone
# offers - and the kinds column says which of the four kinds those four are.
COLUMNS = (
    Column("area", "Area", 200, "w"),
    Column("zone", "Zone", 160, "w"),
    Column("missing", "to catch", 80, "e", desc_first=True),
    Column("of", "of", 60, "e"),
    # THE BEST SINGLE CHANCE AMONG THE MISSING ONES, so two zones with the same count can be told
    # apart: twelve groups at 2% each is a worse target than eight at 30%.
    Column("easiest", "easiest", 80, "e", desc_first=True),
    Column("kinds", "missing kinds", 250, "w"),
    Column("levels", "levels", 90, "e"),
)

# SHORT KIND LABELS, for the rank column and the pane: the full words are in `missing.KIND_NAMES`.
SHORT = {"A": "standard", "B": "potent", "C": "perfect", "crimsonite": "crimsonite"}

RULE = ("A LINE COUNTS AS CAUGHT when ANY ONE of its stages is caught in that potential category - it "
        "evolves into the rest - and its crimsonite group when that line's crimsonite skin is "
        "unlocked. So a line is worth up to four groups and only a group with NOTHING caught counts as "
        "missing. An ordinary spawn can turn up any of the three potential categories; a crimsonite "
        "spawn fills the crimsonite group only.")


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

        self.table = DataTable(COLUMNS, sort_key="missing", sort_desc=True)
        self.table.selectionChangedTo.connect(self.show_zone)
        self.table.doubleClicked.connect(self._jump)
        outer.addWidget(self.table, 3)

        outer.addWidget(note(RULE, wrap=900))
        # NOTHING WRAPS IN THE PANE: the columns are aligned with spaces (see `widgets.mono_text`), and
        # this pane has the whole window width because the tab has no side columns.
        self.detail = mono_text(wrap=False)
        outer.addWidget(self.detail, 2)

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
        self._odds = {}
        for row in self.rows:
            zone = row["zone"]
            # THE ODDS ARE WORKED OUT ONCE PER ZONE and kept: the row needs the level span and the
            # best chance, and the pane below prints every one of them - three passes over the same
            # `dex.where` lookups for no reason otherwise.
            odds = [(group, missing_data.odds_in(zone, group)) for group in row["missing"]]
            self._odds[zone.name] = odds
            spans = [hit for _group, hit in odds if hit]
            rows.append({
                "area": mapnames.area(zone.map_file),
                "zone": zone.name,
                "missing": "%d" % len(row["missing"]),
                "of": "%d" % len(row["groups"]),
                "easiest": "%.1f%%" % max(hit[2] for hit in spans) if spans else "-",
                "kinds": self._kinds(row["missing"]),
                "levels": ("L%s-%s" % (min(hit[0] for hit in spans), max(hit[1] for hit in spans))
                           if spans else "-"),
                PAYLOAD: zone,
            })
        self.table.set_rows(rows)

        still_short = len({group.family for group in self.groups_ if not group.caught})
        left = sum(1 for group in self.groups_ if not group.caught)
        helpful = sum(1 for row in self.rows if row["missing"])
        # Terse on purpose, and the tooltip carries the same sentence in full for the width the row
        # cannot spare (the two labels are the elastic ones, as in the Database tab).
        text = ("%d group(s) left in %d line(s) \u00b7 %d zone(s) can help"
                % (left, still_short, helpful))
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
        """Name the row's groups one by one, most likely first - or say nothing is left in it."""
        self.detail.setPlainText(self.detail_text(zone))

    def detail_text(self, zone):
        if zone is None:
            return ""
        row = next((r for r in self.rows if r["zone"].name == zone.name), None)
        if row is None:
            return ""
        missing = row["missing"]
        lines = ["%s  (%s)   %d of %d groups missing"
                 % (zone.name, mapnames.area(zone.map_file), len(missing), len(row["groups"])), ""]
        if not missing:
            lines.append("  every group this zone can fill is already caught.")
            return "\n".join(lines)
        # MOST LIKELY FIRST: the share is summed over the line's slots here, so the top line is the
        # group that is both missing and easiest to run into.
        odds = sorted(self._odds.get(zone.name, []), key=lambda pair: -(pair[1][2] if pair[1] else 0))
        width = max(len(pair[0].name) for pair in odds)
        for group, hit in odds:
            lines.append("  %-*s  %-11s %s   %s" % (
                width, group.name, SHORT[group.kind],
                "L%s-%s" % (hit[0], hit[1]) if hit else "        ",
                "%.1f%% of the slots here" % hit[2] if hit else "no wild slot"))
        return "\n".join(lines)

    def _jump(self, index):
        """A double click hands the zone to the first tab, as the Database tab's list does."""
        if index.isValid():
            payload = self.table.current_payload()
            if payload is not None:
                self.zoneChosen.emit(payload)
