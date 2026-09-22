"""The Coromon tab: every Coromon in dex order, and where each one appears.

The order is the game's own `number`, not the order of the data file: the wiki, the dex screen
and any "where do I find #87" answer all use that number, so sorting by it is the point rather
than reading the JSON as it comes.

CRIMSONITE FORMS ARE IN THIS LIST, each immediately after the species it is a form of - the same
number, the same family, a different Coromon (see `dex.crimsonite_forms`). They are what the game
spawns in some areas instead of the ordinary Coromon, in their own places, so "where can I catch a
crimsonite Firefly" is a question this list has to answer like any other.

The list is built the first time the tab is opened rather than at startup, and unlike the Tk
version there is no cost left to defer: icons are composed in memory by Qt (`icons.py`) instead
of being cut into 117 files on disk, so the first open is one atlas decode.
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QSplitter, QVBoxLayout, QWidget)

import dex

from . import icons, mapnames
from .config import ICON_ZOOM
from .table import PAYLOAD, Column, DataTable
from .widgets import note

NOTES = ("Wild encounters only - evolutions, starters and gift Coromon have no locations. "
         "Shares are the zone's own encounter weights. A crimsonite form is a Coromon of its "
         "own, listed after the species it is a form of, with the places that spawn IT. "
         "Double-click a location to show it on the map.")

COLUMNS = (
    Column("area", "Area", 165, "w"),
    Column("zone", "Zone", 165, "w"),
    Column("levels", "levels", 85, "e"),
    Column("share", "share", 65, "e"),
)


class CoromonTab(QWidget):
    """The list of Coromon, and the wild locations of whichever one is selected."""

    zoneChosen = Signal(object)      # a Zone the map should show

    def __init__(self, parent=None):
        super().__init__(parent)
        # THE CRIMSONITE FORMS ARE IN THIS LIST (not in the Database grid, which is the game's own
        # dex and has no such entries): they are separate Coromon with their own encounters, and
        # this is the tab that answers "where do I catch this one". They arrive beside the species
        # they are a form of, sharing its dex number - see `dex.crimsonite_forms`.
        self.monsters = dex.monsters(with_crimsonite=True)
        self.zoom = ICON_ZOOM      # the window hands over the saved scale - see `set_zoom`
        self.rows = {}               # uid -> the list item, for filtering
        self.pick = {}               # uid -> Species
        self.zone_of_row = {}        # row index -> Zone
        self._filled = False
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_list())
        splitter.addWidget(self._build_locations())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 620])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 6)
        outer.addWidget(splitter)

    def _build_list(self):
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 8, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel("find"))
        self.search = QLineEdit()
        self.search.textChanged.connect(self.apply_filter)
        top.addWidget(self.search, 1)
        self.count_label = QLabel("")
        top.addWidget(self.count_label)
        box.addLayout(top)

        self.list = QListWidget()
        # `icons.icon_size`, not `dex.CELL * ICON_ZOOM`: the composed icon carries the game's own
        # entry plate and any badge overhang, so the cell it is drawn in is that much bigger.
        self._size_list()
        self.list.currentItemChanged.connect(lambda *_: self._show_locations())
        box.addWidget(self.list, 1)
        return panel

    def _size_list(self):
        """Point the list's row height at the current icon scale."""
        width, height = icons.icon_size(self.zoom)
        self.list.setIconSize(QSize(width, height))

    def _build_locations(self):
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)

        self.head = QLabel("")
        self.head.setWordWrap(True)
        box.addWidget(self.head)

        self.locations = DataTable(COLUMNS, sort_key="area")
        self.locations.doubleClicked.connect(lambda *_: self._jump())
        box.addWidget(self.locations, 1)
        box.addWidget(note(NOTES, wrap=520))
        return panel

    # ------------------------------------------------------------------ contents
    def showEvent(self, event):
        """Fill the list the first time the tab is opened, not when the window is built."""
        super().showEvent(event)
        if not self._filled:
            self._filled = True
            self.fill()

    def fill(self):
        for mon in self.monsters:
            # THE NAME ONLY: the dex number is drawn INSIDE the icon (in the game's own font - see
            # `icons.icon_pixmap`), so the row label no longer repeats it. A Coromon with no dex
            # number (the titans) is now just its name, rather than a "#-- " prefix. A crimsonite
            # form is named for what it is - "Crimsonite Firefly", the game's own phrasing
            # (`[type.crimsonite] [monster ...]` in its catch milestones).
            label = mon.name
            item = QListWidgetItem(label)
            # THE ROW IS KEYED BY `mon.key`, not by the UID: a crimsonite form shares its UID with
            # the species it is a form of, so keying by UID would make the two rows overwrite each
            # other in `rows` and `pick`.
            item.setData(Qt.ItemDataRole.UserRole, mon.key)
            pixmap = icons.icon_pixmap(mon, self.zoom)
            if pixmap is not None:
                # no icon at all for a Coromon the atlas does not list: the row is then text,
                # which is what it was in the Tk version too - see icons.icon_image
                item.setIcon(pixmap)
            self.list.addItem(item)
            self.rows[mon.key] = item
            self.pick[mon.key] = mon
        # QListWidget has no "fill without emitting", so the selection is set once at the end
        self.apply_filter()
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._show_locations()

    def set_zoom(self, zoom):
        """Draw the list's icons at that scale, and remember it.

        The Database tab owns the -/+ buttons (its grid is where the size is visible), so this is
        the half of that knob that lives here: the same `config.ICON_ZOOM_KEY` in the same `Prefs`,
        one scale for the whole window. Called before the list is first filled and it is simply the
        scale `fill` will use; called after, it re-points the icons it already made.
        """
        zoom = int(zoom)
        if zoom == self.zoom:
            return
        self.zoom = zoom
        self._size_list()
        for key, item in self.rows.items():
            pixmap = icons.icon_pixmap(self.pick[key], self.zoom)
            if pixmap is not None:
                item.setIcon(pixmap)

    def apply_filter(self):
        """Hide rows that do not match, rather than rebuilding the list on each keystroke.

        The row label is the name only (the number is in the icon), so the dex NUMBER is matched
        separately - typing "35" or "#35" still finds Buzzlet, and a bare "#" matches nothing.
        """
        needle = self.search.text().strip().lower()
        number = needle.lstrip("#")
        shown = 0
        for key, item in self.rows.items():
            mon = self.pick.get(key)
            match = (not needle or needle in item.text().lower()
                     or needle in (key or "").lower()
                     or (number and mon is not None and mon.number
                         and number in str(mon.number)))
            item.setHidden(not match)
            shown += 1 if match else 0
        self.count_label.setText("%d of %d" % (shown, len(self.monsters)))
        if shown and self.list.currentItem() is not None and self.list.currentItem().isHidden():
            for row in range(self.list.count()):
                if not self.list.item(row).isHidden():
                    self.list.setCurrentRow(row)
                    break

    def _show_locations(self):
        item = self.list.currentItem()
        mon = self.pick.get(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None
        if mon is None:
            self.head.setText("")
            self.locations.set_rows([])
            return
        found = dex.where(mon.uid, mon.skin)
        self.head.setText("%s   %s   %d location(s)%s" % (
            mon.name, mon.family or "-", len(found),
            "" if found else "   - no wild encounters: an evolution, a starter or a gift"))
        self.locations.set_rows([{
            "area": mapnames.area(zone.map_file),
            "zone": zone.name,
            "levels": "L%s-%s" % (low, high),
            "share": "%.1f%%" % share,
            PAYLOAD: zone,
        } for (zone, low, high, share, _battles) in found])

    def _jump(self):
        """Ask for the double-clicked location to be shown on the map."""
        zone = self.locations.current_payload()
        if zone is not None:
            self.zoneChosen.emit(zone)
