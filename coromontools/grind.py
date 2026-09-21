"""The first tab: the areas you have, and where to grind among them.

Three columns, because it is really one question asked in three steps: which areas can I
reach, what does that rank, and WHERE is the zone the ranking just recommended. The map sits
beside the ranking rather than on a tab of its own - the question only comes up while looking
at the ranking - and the species of the selected zone sit under the map, because they are what
the map is being read for.

The three caveats about the numbers, all of them also in `encounters.py`:

* a species' share is its `stepsWithEncounter` weight over the zone's total weight, because
  those weights do NOT sum to the zone's own `stepsUntilSeenAllEncounters` (measured: equal in
  0 of 101 zones), so dividing by that field would be wrong;
* whether the game draws proportionally to those weights is NOT verified - treat the shares as
  relative weights;
* the monster data carries no XP yield and no level curve, so the ranking is by monster level,
  not by XP per battle.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSpinBox, QSplitter,
                               QVBoxLayout, QWidget)

from .config import (LEVEL_DEFAULT, MIN_SHARE_DEFAULT, ONLY_XP_DEFAULT, ON_TOP_DEFAULT,
                     SCALES_SPAN, XP_MARGIN)
from .mapview import ZoneMap
from .table import PAYLOAD, Column, DataTable
from .text import pretty
from .widgets import mono_text, note

try:
    import savefile
except ImportError:          # the button then explains itself instead of crashing
    savefile = None

NOTES = ("Shares are the game's own encounter weights, normalised - whether the game draws "
         "proportionally to them is unverified. The monster data has no XP yield, so zones are "
         "ranked by how high the monsters are, not by XP per battle.")

MAP_NOTES = ("Patches are read from the map tiles: the marker's tile, plus the connected tiles "
             "of the same tileset - exact for grass, whose tiles are one map cell. Water and "
             "cave markers name no layer and show as small outlined squares.")

COLUMNS = (
    Column("area", "Area", 190, "w"),
    Column("zone", "Zone", 170, "w"),
    Column("explvl", "exp level", 80, "e", desc_first=True),
    Column("best", "most common", 240, "w", desc_first=True),
    Column("flags", "flags", 150, "w"),
)


def zone_rows(zone, min_share=0.0):
    """The species of a zone, most common first."""
    rows = []
    for uid, rec in sorted(zone.monsters.items(), key=lambda kv: -kv[1]["share"]):
        if rec["share"] < min_share:
            continue
        rows.append({
            "name": zone.species.get(uid, uid),
            "min": rec["min"], "max": rec["max"], "share": rec["share"],
            "battles": rec["battles"],
        })
    return rows


def rank(zones, level, min_share=0.0, only_xp=True):
    """Zones worth walking to for a squad of `level`, best first.

    "Still gives XP" hides a zone whose highest monster is more than XP_MARGIN levels below the
    squad: those are the ones that stop being worth the walk, and a list sorted by level alone
    would put a zone you out-level at the top forever.
    """
    picked = []
    for zone in zones:
        top = max((row["max"] for row in zone.monsters.values()), default=0)
        if only_xp and level and top < level - XP_MARGIN:
            continue
        picked.append(zone)
    picked.sort(key=lambda zone: -zone.average_level)
    return picked


class GrindTab(QWidget):
    """The tick list, the ranking it produces, and the map of the selected row."""

    onTopToggled = Signal(bool)

    def __init__(self, zones, prefs, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.by_map = {}
        for zone in zones:
            self.by_map.setdefault(zone.map_file, []).append(zone)
        self.maps = sorted(self.by_map)

        saved = prefs.get("available")
        self.available = set(saved) if isinstance(saved, list) else set(self.maps)
        self.area_items = {}
        self.selected_zone = None
        # set while the tick list is being written to programmatically, so that "All", the save
        # import and the initial fill do not each re-rank the whole table once per checkbox
        self._syncing = False

        self._build()
        self.apply_filter()
        self.refresh()

    # ------------------------------------------------------------------ layout
    def _build(self):
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_areas())
        splitter.addWidget(self._build_ranking())
        splitter.addWidget(self._build_map())
        # the ranking gets the room; the picker is a fixed-ish list and the map only has to be
        # big enough to tell six patches apart
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([250, 660, 420])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 6)
        outer.addWidget(splitter)

    def _build_areas(self):
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 8, 0)

        box.addWidget(QLabel("Areas you have"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter areas")
        self.search.textChanged.connect(self.apply_filter)
        box.addWidget(self.search)

        row = QHBoxLayout()
        for label, on in (("All", True), ("None", False)):
            button = QPushButton(label)
            button.setFixedWidth(60)
            button.clicked.connect(lambda _checked=False, state=on: self.set_all(state))
            row.addWidget(button)
        self.count_label = QLabel("")
        row.addWidget(self.count_label)
        row.addStretch(1)
        box.addLayout(row)

        self.visited_button = QPushButton("What I've visited")
        self.visited_button.clicked.connect(self.tick_visited)
        box.addWidget(self.visited_button)
        self.save_label = note("")
        box.addWidget(self.save_label)

        # a checkable list rather than a canvas of checkbuttons with a scrollbar bolted on: the
        # Tk version had to keep a scrollregion in step with its contents by hand, and this is
        # the widget that already is a scrolling column of ticks
        self.area_list = QListWidget()
        for map_file in self.maps:
            item = QListWidgetItem(pretty(map_file))
            item.setData(Qt.ItemDataRole.UserRole, map_file)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if map_file in self.available
                               else Qt.CheckState.Unchecked)
            self.area_list.addItem(item)
            self.area_items[map_file] = item
        # connected only once the list is filled, so that filling it cannot fire a refresh
        # before the ranking it would refresh exists
        self.area_list.itemChanged.connect(self._area_changed)
        box.addWidget(self.area_list, 1)
        return panel

    def _build_ranking(self):
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 8, 0)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("squad level"))
        self.level = QSpinBox()
        self.level.setRange(1, 100)
        self.level.setValue(int(self.prefs.get("level", LEVEL_DEFAULT)))
        self.level.valueChanged.connect(self._filters_changed)
        controls.addWidget(self.level)

        self.only_xp = QCheckBox("still gives XP")
        self.only_xp.setChecked(bool(self.prefs.get("only_xp", ONLY_XP_DEFAULT)))
        self.only_xp.toggled.connect(self._filters_changed)
        controls.addWidget(self.only_xp)

        controls.addWidget(QLabel("hide species under %"))
        self.min_share = QSpinBox()
        self.min_share.setRange(0, 100)
        self.min_share.setValue(int(float(self.prefs.get("min_share", MIN_SHARE_DEFAULT))))
        self.min_share.valueChanged.connect(self._filters_changed)
        controls.addWidget(self.min_share)

        controls.addStretch(1)
        self.on_top = QCheckBox("keep window on top")
        self.on_top.setChecked(bool(self.prefs.get("on_top", ON_TOP_DEFAULT)))
        # the window flag belongs to the window, so the tab reports the choice and the main
        # window applies it - a child widget cannot restack its own top-level
        self.on_top.toggled.connect(self.onTopToggled.emit)
        controls.addWidget(self.on_top)
        box.addLayout(controls)

        self.table = DataTable(COLUMNS, sort_key="explvl", sort_desc=True)
        self.table.selectionChangedTo.connect(self.show_zone)
        box.addWidget(self.table, 1)
        box.addWidget(note(NOTES, wrap=700))
        return panel

    def _build_map(self):
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)

        self.map_head = QLabel("")
        self.map_head.setWordWrap(True)
        box.addWidget(self.map_head)

        self.legend_row = QWidget()
        self.legend_box = QHBoxLayout(self.legend_row)
        self.legend_box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.legend_row)

        self.map = ZoneMap()
        box.addWidget(self.map, 3)

        box.addWidget(note(MAP_NOTES, wrap=420))

        self.species = mono_text(wrap=False)
        box.addWidget(self.species, 2)
        return panel

    # ------------------------------------------------------------------ area list
    def apply_filter(self):
        """Show only the areas matching the filter, without rebuilding the list.

        The Tk version destroyed and recreated every checkbox on each keystroke, which threw
        the scroll position away and is why it had to reset it to the top by hand. Hiding the
        rows keeps both.
        """
        needle = self.search.text().strip().lower()
        shown = 0
        for map_file, item in self.area_items.items():
            match = not needle or needle in item.text().lower() or needle in map_file.lower()
            item.setHidden(not match)
            shown += 1 if match else 0
        if shown:
            self.count_label.setText("%d of %d ticked" % (len(self.available), len(self.maps)))
        else:
            self.count_label.setText("(nothing matches)")

    def _area_changed(self, item):
        if self._syncing:
            return
        map_file = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.available.add(map_file)
        else:
            self.available.discard(map_file)
        self.count_label.setText("%d of %d ticked" % (len(self.available), len(self.maps)))
        self._save_available()
        self.refresh()

    def _save_available(self):
        self.prefs.set("available", sorted(self.available))

    def _set_checked(self, map_file, on):
        """Tick a row programmatically. The callers set `available` and refresh themselves.

        Guarded by `_syncing`, because QListWidget has no "set them all" call: sixty-nine
        individual changes would otherwise be sixty-nine full re-rankings of the table.
        """
        item = self.area_items[map_file]
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        if item.checkState() != state:
            self._syncing = True
            item.setCheckState(state)
            self._syncing = False

    def set_all(self, on):
        for map_file in self.area_items:
            self._set_checked(map_file, on)
            (self.available.add if on else self.available.discard)(map_file)
        self._save_available()
        self.refresh()

    def tick_visited(self):
        """Tick exactly the areas the save says were visited, and nothing else.

        Saves ticking 69 boxes down to the handful you actually have - the save records every
        map walked through, in `settings.VISITED_MAPS` (see savefile).
        """
        if savefile is None:
            self.save_label.setText("save: savefile.py is missing")
            return
        try:
            slot, seen = savefile.visited()
            hit = savefile.areas(self.maps, seen)
        except Exception as exc:                       # noqa: BLE001 - reported, not hidden
            self.save_label.setText("save: %s: %s" % (type(exc).__name__, exc))
            return
        self.available = set(hit)
        for map_file in self.area_items:
            self._set_checked(map_file, map_file in self.available)
        self._save_available()
        self.refresh()
        self.save_label.setText("%d of %d areas, from %s (%d maps visited)"
                               % (len(hit), len(self.maps), slot, len(seen)))

    # ------------------------------------------------------------------ ranking
    def _filters_changed(self, _value=None):
        self.prefs.set("level", self.level.value())
        self.prefs.set("min_share", float(self.min_share.value()))
        self.prefs.set("only_xp", self.only_xp.isChecked())
        self.refresh()

    def reachable(self):
        return [zone for map_file, zones in self.by_map.items()
                if map_file in self.available for zone in zones]

    def min_share_value(self):
        return float(self.min_share.value())

    def refresh(self):
        """Rebuild the ranking from the ticked areas and the filters."""
        picked = rank(self.reachable(), self.level.value(), self.min_share_value(),
                      self.only_xp.isChecked())
        min_share = self.min_share_value()
        rows = []
        for zone in picked:
            species = zone_rows(zone, min_share)
            best = species[0] if species else None
            rows.append({
                "area": pretty(zone.map_file),
                "zone": zone.name,
                "explvl": "%.2f" % zone.average_level,
                "best": ("%s L%s-%s  %.0f%%" % (best["name"], best["min"], best["max"],
                                                best["share"]) if best else ""),
                "flags": " ".join(self._flags(zone, species)),
                PAYLOAD: zone,
            })
        self.table.set_rows(rows, keep_row=self.selected_zone)
        # re-shown explicitly: filtering the species list or ticking a new area can change what
        # the SAME selected zone is, and a selection that did not move emits nothing
        self.show_zone(self.table.current_payload())
        if not rows:
            self._show_empty_hint()

    def _flags(self, zone, species):
        """What the numbers do not say: water, a huge level span, or more than one at once.

        A zone labelled "multi" is one where a single encounter can be a double or triple
        battle, which changes what the walk is worth without changing any number in the row.
        """
        flags = []
        if zone.water:
            flags.append("water")
        top = max((rec["max"] for rec in zone.monsters.values()), default=0)
        bottom = min((rec["min"] for rec in zone.monsters.values()), default=0)
        if top - bottom >= SCALES_SPAN:
            flags.append("scales")
        if any(row["battles"] >= 2 for row in species):
            flags.append("multi")
        return flags

    def _show_empty_hint(self):
        """Say what the filters removed, rather than leaving the ranking blank.

        A tick list covering only early areas plus the "still gives XP" filter is a common
        combination that yields nothing at all, and an empty table reads like a bug.
        """
        reachable = self.reachable()
        if not reachable:
            text = ("Nothing is ticked, so there is nothing to rank.\n\nTick an area on the "
                    "left, or press \"What I've visited\".")
        else:
            best = max(reachable, key=lambda zone: max(
                (rec["max"] for rec in zone.monsters.values()), default=0))
            top = max((rec["max"] for rec in best.monsters.values()), default=0)
            text = ("No zone here still gives XP at squad level %d.\n\nThe highest monster in "
                    "the ticked areas is L%d, in %s (%s). Untick \"still gives XP\" to rank "
                    "them anyway, or reach further areas."
                    % (self.level.value(), top, best.name, pretty(best.map_file)))
        self.species.setPlainText(text)

    # ------------------------------------------------------------------ selection
    def show_zone(self, zone):
        """Point the species pane and the map at a zone - or at nothing at all."""
        self.selected_zone = zone
        if zone is None:
            self.map.set_zone(None)
            self._show_map_head()
            return
        self.species.setPlainText(self._species_text(zone))
        self.map.set_zone(zone)
        self._show_map_head()

    def _species_text(self, zone):
        lines = ["%s  (%s)%s" % (zone.name, pretty(zone.map_file),
                                 "   water" if zone.water else ""), ""]
        for row in zone_rows(zone, self.min_share_value()):
            tag = {1: "", 2: "   double battle", 3: "   triple battle"}.get(row["battles"], "")
            lines.append("  %-14s L%-3s-%-3s  %5.1f%%%s" % (
                row["name"], row["min"], row["max"], row["share"], tag))
        return "\n".join(lines)

    def _show_map_head(self):
        self.map_head.setText(self.map.headline)
        while self.legend_box.count():
            item = self.legend_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for (letter, colour, selected) in self.map.legend:
            chip = QLabel(letter)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedWidth(22)
            # the selected zone is the solid one, so the legend shows which chip that is - the
            # same rule the map draws by, which is why the legend comes from the map's own plan
            chip.setStyleSheet("background: %s; color: #ffffff; border: %s;"
                               % (colour, "1px solid #ffffff" if selected else "none"))
            chip.setToolTip("the selected zone" if selected else "")
            self.legend_box.addWidget(chip)
        self.legend_box.addWidget(note("same colour as the tool's map page; the selected zone "
                                      "is the solid one"), 1)

    def show_zone_from_elsewhere(self, zone):
        """Show a zone the Coromon tab asked about: select it if it is listed, always draw it."""
        if zone is not None:
            self.table.select_payload(zone)
        self.show_zone(zone)
