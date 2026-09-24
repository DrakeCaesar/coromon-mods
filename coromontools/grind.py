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
  not by XP per battle;
* a crimsonite spawn is a Coromon of its own, listed under its own name with the share that slot
  really has. One zone can spawn both the ordinary and the crimsonite form of a species, and
  their shares are counted apart - together they are the zone's 100% (see `Zone.slots`).
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSpinBox, QSplitter,
                               QVBoxLayout, QWidget)

import threading

import dex

from . import mapnames, mapview, maptiles
from .config import (LEVEL_DEFAULT, MIN_SHARE_DEFAULT, ONLY_XP_DEFAULT, ON_TOP_DEFAULT,
                     SCALES_SPAN, STATES_ONLY_DEFAULT, XP_MARGIN)
from .mapview import ZoneMap
from .table import PAYLOAD, Column, DataTable
from .widgets import FittedPane, mono_text, note

import items

# THE ZONE RANKING'S STORY-STATES TICK: what it says, and the two tooltips - the count cannot be in
# the first one, because the map files it is counted from are read on a thread of their own (see
# `_read_states`).
STORY_STATES_TIP_INITIAL = (
    "only the zones whose map draws different tiles in another story moment\n"
    "(the `base#condition` layers the map's own stepper flips between) - counting them now")
STORY_STATES_TIP = (
    "only the zones whose map draws different tiles in another story moment\n"
    "(the `base#condition` layers the map's own stepper flips between) - %d of the %d areas "
    "have a map like that")

try:
    import savefile
except ImportError:          # the button then explains itself instead of crashing
    savefile = None

# THE GEMS THAT MOVE XP, in the order the user asked for them, and the reason they are columns at
# all: the Smart Gem pays the Coromon that FIGHTS, and the two lazy gems are the only way a Coromon
# you are NOT fighting with earns anything - the game hands XP to the sprites that FACED the enemy
# (`classes.battle.rules.afterMonsterSpritesFaintedXp`), so a benched Coromon without one gets zero.
# The multipliers are READ OUT OF THE GAME'S OWN ITEM CLASSES (`items.xp_multiplier`): 1.1 for the
# Smart Gem, 0.5 for the Sloth Gem, 0.2 for the Lazy Gem.
GEMS = (
    ("smart", "smart gem", "HOLD_EXTRA_XP"),
    ("sloth", "sloth gem", "HOLD_LAZY_XP_PREMIUM"),
    ("lazy", "lazy gem", "HOLD_LAZY_XP"),
)

NOTES = ("Shares are the game's own encounter weights, normalised. XP PER FIGHT IS THE GAME'S OWN "
         "REWARD, not an estimate: `dex.xp_reward` is the formula out of the Lua - the Coromon's "
         "place in its evolution line x the mean of its base stats x its level / 2 - averaged over "
         "the encounters the zone can roll, a group counted member by member (a party of two is two "
         "Coromon defeated). Three things it is NOT: a wild Potent or Perfect Coromon is worth 2x or "
         "4x (a difficulty setting), a squad splitting the reward is not in a per-fight figure, and "
         "the mean of the base stats is the one step that calls a function the shipped archive does "
         "not contain (`getMutatedBaseStats`), so read the numbers as exact to within a constant. "
         "The reward follows the monster's level, so a zone's rank does not depend on yours, and how "
         "often the zone rolls a fight at all (`stepsWithEncounter`, 1-4 per encounter) is NOT "
         "weighed in - this column is per fight, not per step. An encounter the game cannot roll at "
         "all (a LEVEL RANGE THAT IS EMPTY: Pyramid F6 ships a 2725-27 typo) is left out and says "
         "so, rather than adding a level-2725 reward to the zone. The three GEM COLUMNS are the same "
         "fight with each XP gem on the holder: the Smart Gem's is for the Coromon that FIGHTS (1.1x), "
         "the Sloth (0.5x) and Lazy (0.2x) gems pay one that DID NOT - nothing else does, and they "
         "are how you level a Coromon you are not fighting with. All four are ONE Coromon facing the "
         "whole fight (the solo case); two Coromon facing the same enemy SPLIT that enemy's reward.")

MAP_NOTES = ("Zones are read from the map: a marker's own tile plus the connected tiles of its "
             "tileset, which is exact for grass (one tile, one map cell). A marker that names no "
             "layer - water, caves - has its CELLS as the shape instead, and those are merged into "
             "blocks and edged. Every block is named; the selected zone is the one with the white "
             "outline.")

# THE NUMBERS COME FIRST, AND THAT IS A DELIBERATE ORDER. This window is used BESIDE a fullscreen
# game, so it is small: measured on the shipped window state it opens at 1040x560, which leaves this
# table a 428 px viewport. With Area and Zone in front, that showed two names and "exp level" and
# pushed every XP column off the right edge - which is exactly how the user came to ask "it's not
# showing the extra columns for exp anywhere still, am I overlooking them?": they were all past the
# scrollbar, the base column included. Zone (140) + the four XP columns (70 each = 280) = 420 px, so
# the fight's worth AND the three gems are readable with no scrolling at all at that width, and the
# descriptive columns follow for anyone who drags the window wider. The full area name is not lost -
# selecting a row prints it, with the whole spawn list, in the pane below.
COLUMNS = (
    Column("zone", "Zone", 140, "w"),
    # THE NUMBER THE TAB EXISTS FOR, and the column it opens sorted by: what one fight is worth here.
    Column("xp", "xp / fight", 70, "e", desc_first=True),
    # ... AND WHAT THE SAME FIGHT IS WORTH WITH EACH XP GEM ON THE HOLDER - see `GEMS`.
) + tuple(
    Column(key, heading, 70, "e", desc_first=True) for key, heading, _uid in GEMS
) + (
    Column("area", "Area", 190, "w"),
    Column("explvl", "exp level", 80, "e", desc_first=True),
    Column("best", "most common", 240, "w", desc_first=True),
    Column("flags", "flags", 150, "w"),
)


def gem_factors():
    """`{column key: multiplier}` for the XP gems, read from the game - None when it cannot be read.

    A gem the archive has no class for is reported as None rather than 1.0, so its column stays
    blank instead of pretending the gem does nothing.
    """
    return {key: items.xp_multiplier(uid) for key, _heading, uid in GEMS}


def zone_rows(zone, min_share=0.0):
    """The spawns of a zone, most common first.

    A crimsonite form is in here like every other spawn, under its own name ("Crimsonite
    <species>") and with its own share - see `Zone.slots`. It is what the zone really spawns
    there, so it belongs in the pane you read before walking to a spot.
    """
    return [row for row in sorted(zone.slots(), key=lambda rec: -rec["share"])
            if row["share"] >= min_share]


def encounter_lines(zone, min_share=0.0, xp_of=None):
    """One aligned line per ENCOUNTER of `zone`, most likely first - the body of a spawns pane.

    ONE FORMATTER FOR TWO TABS: this tab draws it under its map and the Database tab under its location
    list, answering the same question ("what is in this grass?") in the same columns - which is why the
    alignment is padded with spaces and neither pane wraps.

    THE ROWS ARE ENCOUNTERS, NOT SPECIES, and that is the change the user asked for: a percentage
    belongs to the fight the game rolls, so a triple battle is ONE line naming its party ("Armadon + 2
    Armado") at the odds of meeting it. Listing the members separately reported every one of them at
    the whole group's share and counted a repeated member twice, which is how a zone's shares came out
    summing past 100%. See `encounters.Zone.encounters`.

    The heading belongs to the caller: this tab names the zone and marks a water zone, the Database tab
    does not, because the selected row directly above the pane already names it.

    `xp_of` adds WHAT A FIGHT IS WORTH, and the gems with it: the ranking's whole claim is XP per
    fight, so a zone's own pane has to show where that number comes from - and the gems are the same
    fight with one on the holder, which is the other half of "where should I grind". Four aligned
    number columns follow the share, named by a HEADER ROW of their own (the numbers are bare so they
    stay narrow, and a column nobody can name is unreadable):

             name              level    share   fight   smart   sloth    lazy
        Chonktoad              L55-60   20.0%    4406    4847    2203     881

    `fight` is the whole fight (every body in the party), and `smart` / `sloth` / `lazy` are that
    figure with each XP gem on the holder - the Smart Gem for a Coromon that FIGHTS, the other two for
    one that does not (see `GEMS`). A group is worth every body, exactly as the column that sums them
    does. An entry whose LEVEL RANGE IS EMPTY (the game's own typo - see `Zone.rollable_encounters`)
    gets no figure at all, because there is no level to reward: it says so instead, and the zone's
    total leaves it out.
    """
    rows = [row for row in zone.encounters() if row["share"] >= min_share]
    width = max([14] + [len(row["name"]) + 1 for row in rows])
    out = []
    if xp_of is not None:
        # THE COLUMNS ARE NAMED ONCE, which is what lets the rows carry bare numbers: six number
        # columns with " xp" after each would be half again as wide, and this pane is narrow (it sits
        # in the map column of a window that is kept beside the game).
        out.append("  %-*s %-7s %6s %6s %6s %6s %6s"
                   % (width, "", "level", "share", "fight", "smart", "sloth", "lazy"))
    gems = gem_factors() if xp_of is not None else {}
    for row in rows:
        line = "  %-*s L%-3s-%-3s %5.1f%%" % (width, row["name"], row["min"], row["max"],
                                              row["share"])
        if xp_of is not None:
            if row["rollable"]:
                fight = zone.encounter_xp(row, xp_of)
                line += " %6d" % fight
                # ... AND THE SAME FIGHT WITH EACH GEM ON THE HOLDER. A gem whose multiplier could not
                # be read prints "-" rather than a figure computed from a guess, exactly as the table's
                # column goes blank - and the two must agree, they are one reading of one item class.
                for key, _heading, _uid in GEMS:
                    factor = gems.get(key)
                    line += (" %6d" % (fight * factor)) if factor else " %6s" % "-"
            else:
                # NO LEVEL, NO REWARD: four blanks and the reason, rather than a figure from a level
                # the game cannot roll (see `Zone.rollable_encounters`).
                line += " %6s %6s %6s %6s  (empty level range)" % ("-", "-", "-", "-")
        out.append(line)
    return out


def rank(zones, level, min_share=0.0, only_xp=True):
    """Zones worth walking to for a squad of `level`, best first.

    "Still gives XP" hides a zone whose highest monster is more than XP_MARGIN levels below the
    squad: those are the ones that stop being worth the walk, and a list sorted by level alone
    would put a zone you out-level at the top forever.
    """
    picked = []
    for zone in zones:
        top = max((row["max"] for row in zone.slots()), default=0)
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
        # WHICH AREAS HAVE STORY STATES, filled in by `_read_states` - on its own thread, because it
        # reads every map file (see `states_of`).
        self.states = {}
        self._reader = None
        self.area_items = {}
        self.selected_zone = None
        # set while the tick list is being written to programmatically, so that "All", the save
        # import and the initial fill do not each re-rank the whole table once per checkbox
        self._syncing = False

        self._build()
        self.apply_filter()
        self.refresh()
        # THE MAP FILES ARE READ AFTER THE WINDOW IS BUILT, on their own thread: 69 maps are 36 MB, and
        # the first read of a session takes 1.9 s (measured cold, 0.02 s warm) - paid while the window
        # was being built it took its start from 0.8 s to 2.7 s, and on a thread but DURING the build
        # it still cost 0.3 s of interpreter contention. Nothing waits for it: the ranking is whole
        # until the answer is in, and comes back through `refresh` when it lands. The main thread ASKS
        # whether it is done rather than being signalled by it, so a window closed while the maps are
        # still being read cannot be touched by a dying thread.
        self._watch = QTimer(self)
        self._watch.setInterval(250)
        self._watch.timeout.connect(self._states_landed)
        QTimer.singleShot(0, self._start_reading)

    # ------------------------------------------------------------------ the areas' story states
    def _start_reading(self):
        """Start the map reader, once."""
        if self._reader is not None:
            return
        self._reader = threading.Thread(target=self._read_states, name="area-states", daemon=True)
        self._reader.start()
        self._watch.start()

    def _read_states(self):
        """Which areas draw something else in another story moment - the answer the filter needs.

        `maptiles.has_states` reads the map's bytes rather than parsing them, because parsing all 69
        would also pin 36 MB of JSON in `map_parts`' cache for the rest of the session.
        """
        self.states.update({map_file: maptiles.has_states(map_file) for map_file in self.maps})

    def states_ready(self):
        """Whether the reader has answered for every area yet."""
        if self._reader is None:
            return False
        return not self._reader.is_alive()

    def states_of(self, map_file):
        """Whether this area's map draws different tiles in another story moment.

        FALSE WHILE THE READER IS STILL GOING, which is why the ranking only filters once
        `states_ready` says so - a half-read answer would hide zones with no reason given.
        """
        return self.states.get(map_file, False)

    def _states_landed(self):
        """The reader is done: the tick can say how many areas have a map like that, and the ranking
        it was waiting to filter is rebuilt with it."""
        if not self.states_ready():
            return
        self._watch.stop()
        self.states_tick.setToolTip(STORY_STATES_TIP % (sum(self.states.values()), len(self.maps)))
        if self.states_tick.isChecked():
            self.refresh()

    # ------------------------------------------------------------------ layout
    def _build(self):
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_areas())
        splitter.addWidget(self._build_ranking())
        splitter.addWidget(self._build_map())
        # THE RANKING PANE IS THE TABLE'S OWN WIDTH and the map takes everything else: the ranking used
        # to be given a share of the window (660 of 1330), which is not a width anything in it asks
        # for - it is a ratio, and a ratio moves every time the window does. The fit also means the
        # separator lands just past the last column instead of inside it (see `widgets.FittedPane`).
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        self.pane_fit = FittedPane(splitter, 1, self.table, elastic=2)

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
            item = QListWidgetItem(mapnames.area(map_file))
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

        controls.addWidget(QLabel("hide encounters under %"))
        self.min_share = QSpinBox()
        self.min_share.setRange(0, 100)
        self.min_share.setValue(int(float(self.prefs.get("min_share", MIN_SHARE_DEFAULT))))
        self.min_share.valueChanged.connect(self._filters_changed)
        controls.addWidget(self.min_share)

        # ... AND THE ZONES THAT DRAW SOMETHING ELSE IN ANOTHER STORY MOMENT, which is the only way to
        # find them: the states are a property of the MAP (`base#condition` layers, see the map
        # pane's stepper), nothing in the ranking shows them, and 23 of the 69 areas have maps like
        # that. It filters THE TABLE ONLY - no area's tick is touched, so narrowing the ranking cannot
        # untick anything, and an empty result says why instead of looking broken (`_show_empty_hint`).
        self.states_tick = QCheckBox("with story states")
        self.states_tick.setToolTip(STORY_STATES_TIP_INITIAL)
        self.states_tick.setChecked(bool(self.prefs.get("with_states", STATES_ONLY_DEFAULT)))
        self.states_tick.toggled.connect(self._filters_changed)
        controls.addWidget(self.states_tick)

        controls.addStretch(1)
        self.on_top = QCheckBox("keep window on top")
        self.on_top.setChecked(bool(self.prefs.get("on_top", ON_TOP_DEFAULT)))
        # the window flag belongs to the window, so the tab reports the choice and the main
        # window applies it - a child widget cannot restack its own top-level
        self.on_top.toggled.connect(self.onTopToggled.emit)
        controls.addWidget(self.on_top)
        box.addLayout(controls)

        self.table = DataTable(COLUMNS, sort_key="xp", sort_desc=True)
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
        # THE CAPTION ROW, which every map panel wears: the map's own line, the -/+ zoom buttons and
        # the story-state stepper (`mapview.head_row`), so the three tabs that draw a map are laid out
        # the same way. The stepper is handed to the map, which fills it with the states THAT map has.
        head, self.variant_bar = mapview.head_row(self.map_head, self.prefs)
        box.addWidget(head)

        self.legend_row = QWidget()
        self.legend_box = QHBoxLayout(self.legend_row)
        self.legend_box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.legend_row)

        self.map = ZoneMap()
        self.map.set_variant_bar(self.variant_bar)
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
        self.prefs.set("with_states", self.states_tick.isChecked())
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
        # THE STORY-STATES FILTER, which is about WHERE a zone is rather than how good it is. It waits
        # for the map reader (`states_ready`) instead of blocking on it: until the answer is in the
        # table is left whole, and `_states_landed` comes back through here when it arrives.
        if self.states_tick.isChecked() and self.states_ready():
            picked = [zone for zone in picked if self.states_of(zone.map_file)]
        min_share = self.min_share_value()
        # read once for the whole table: the multipliers come out of the archive, which is cached a
        # uid at a time but is not something to ask for on every row
        factors = gem_factors()
        rows = []
        for zone in picked:
            species = zone_rows(zone, min_share)
            best = species[0] if species else None
            # ONE FIGHT's worth of XP, which is what the ranking is about - see `Zone.xp_per_encounter`
            fight = zone.xp_per_encounter(dex.xp_reward)
            row = {
                "area": mapnames.area(zone.map_file),
                "zone": zone.name,
                "explvl": "%.2f" % zone.average_level,
                "xp": "%.0f" % fight,
                "best": ("%s L%s-%s  %.0f%%" % (best["name"], best["min"], best["max"],
                                                best["share"]) if best else ""),
                "flags": " ".join(self._flags(zone, species)),
                PAYLOAD: zone,
            }
            # THE SAME FIGHT WITH EACH GEM ON THE HOLDER (see `GEMS`): the holder's own earnings, not
            # the zone's. A gem whose multiplier could not be read leaves its cell blank rather than
            # claiming the gem changes nothing.
            for key, factor in factors.items():
                row[key] = "%.0f" % (fight * factor) if factor else ""
            rows.append(row)
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

        REVERSED LEVELS IS THE GAME'S OWN TYPO, not a reading of ours, and it is flagged because it
        is the one thing here that can put a zone at the top of the ranking on its own: PYRAMID_F6
        ships `GHOST_OCTO_1 minLevel 2725, maxLevel 27`, and the game rolls a wild level with
        `math.random(minLevel, maxLevel)`, which LUA REFUSES for an empty interval - so that entry
        can never produce a fight. Its row says so, its level and its XP are left out of the zone's
        numbers (`Zone.rollable_encounters`), and the flag is what tells you why the zone's total
        does not cover all of its spawns.
        """
        flags = []
        if zone.water:
            flags.append("water")
        spawns = zone.slots()
        top = max((rec["max"] for rec in spawns), default=0)
        bottom = min((rec["min"] for rec in spawns), default=0)
        if top - bottom >= SCALES_SPAN:
            flags.append("scales")
        if any(rec["min"] > rec["max"] for rec in spawns):
            flags.append("odd levels")
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
        elif self.states_tick.isChecked() and not any(self.states_of(zone.map_file)
                                                      for zone in reachable):
            text = ("No zone in the ticked areas draws anything else in another story "
                    "moment.\n\nThe story states are a property of the map - "
                    "%d of the %d areas have a map with them - and unticking \"with story "
                    "states\" ranks every zone again."
                    % (sum(self.states.values()), len(self.maps)))
        else:
            best = max(reachable, key=lambda zone: max(
                (rec["max"] for rec in zone.slots()), default=0))
            top = max((rec["max"] for rec in best.slots()), default=0)
            text = ("No zone here still gives XP at squad level %d.\n\nThe highest monster in "
                    "the ticked areas is L%d, in %s (%s). Untick \"still gives XP\" to rank "
                    "them anyway, or reach further areas."
                    % (self.level.value(), top, best.name, mapnames.area(best.map_file)))
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
        """The selected zone: its name, WHAT A FIGHT IS WORTH THERE, and then every fight it rolls.

        The headline says the number is an AVERAGE over the fights below and names the best of them,
        because on its own it looks wrong: WATERROUTE_4 reads 4840 while three of its five fights are
        worth more than that (6101, 4775, 4485) - the user: "weird it still shows the data as
        WATERROUTE_4 ... 4840 xp / fight ... 6101 xp". It is the average, weighted by the odds of
        meeting each fight, and a zone's best fight is worth knowing on its own.
        """
        expected, best, count = zone.xp_spread(dex.xp_reward)
        lines = ["%s  (%s)%s" % (zone.name, mapnames.area(zone.map_file),
                                 "   water" if zone.water else ""),
                 "%.0f xp / fight   (average of %d encounter%s, best %.0f)"
                 % (expected, count, "" if count == 1 else "s", best),
                 ""]
        lines.extend(encounter_lines(zone, self.min_share_value(), xp_of=dex.xp_reward))
        return "\n".join(lines)

    def _show_map_head(self):
        self.map_head.setText(self.map.headline)
        # the chips come from the map's own plan, so the legend says exactly what the map drew
        mapview.fill_legend(self.legend_box, self.map.legend,
                            "same colour as the tool's map page; the selected zone "
                            "wears the white outline")

    def show_zone_from_elsewhere(self, zone):
        """Show a zone the Coromon tab asked about: select it if it is listed, always draw it."""
        if zone is not None:
            self.table.select_payload(zone)
        self.show_zone(zone)
