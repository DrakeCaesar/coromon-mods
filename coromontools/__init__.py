"""Coromon grind tools: a Qt window over the game's own encounter, Coromon and skill data.

Modules are split by responsibility:

* ``paths``      - where the game, the tools and the old state file are
* ``config``     - app-wide policy (filters, icon zoom, map scale, alpha)
* ``text``       - the game's internal names, as readable ones
* ``theme``      - the dark palette and the Qt stylesheet
* ``state``      - what the window remembers, and how it is written
* ``widgets``    - the small shared widgets (note labels, monospace panes)
* ``table``      - the reusable sortable table (QTableView + model)
* ``icons``      - dex icons, composed in memory with Qt
* ``mapview``    - the zone map, which patch of grass each zone is
* ``grind``      - the "Where to grind" tab
* ``database_tab`` - the Database tab: the game's own grid, caught / seen / unknown per potential
                   category, and where the picked Coromon can be caught * ``missing_tab`` - the Missing tab: the zones that can still fill the dex, most missing groups first
* ``potential_tab`` - the Potential tab: the wild Potential roll, level by level, under the game's
                   settings
* ``items_tab``  - the Items tab: every item, with the stats and the sprites the game's Lua holds
* ``skills_tab`` - the Skills tab

Run it with the entry script beside this package (``encounters_gui.py``), or
``python -m coromontools`` from inside the tools folder. ``--selftest`` builds the data and
prints the ranking without opening a window.
"""

import argparse
import os
import sys

from .paths import APP_NAME, TOOLS_DIR

if TOOLS_DIR not in sys.path:
    # the data modules (encounters, skills, dex, encounter_zones, savefile) are plain modules in
    # the folder above, not a package, so that folder has to be importable - and this has to
    # happen before the first of them is imported
    sys.path.insert(0, TOOLS_DIR)

from PySide6.QtCore import Qt                                          # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget     # noqa: E402

import dex                                                             # noqa: E402
import encounters                                                      # noqa: E402
import skills                                                          # noqa: E402

from . import mapnames, state                                               # noqa: E402
from . import mapview                                                       # noqa: E402
from .config import MAP_ZOOM_DEFAULT, MAP_ZOOM_KEY, ON_TOP_DEFAULT   # noqa: E402
from .database_tab import DatabaseTab                                  # noqa: E402
from .grind import GrindTab, rank, zone_rows                           # noqa: E402
from .items_tab import ItemsTab                                        # noqa: E402
from .missing_tab import MissingTab                                    # noqa: E402
from .potential_tab import PotentialTab                                # noqa: E402
from .skills_tab import SkillsTab                                      # noqa: E402
from .theme import apply_theme                                         # noqa: E402

GEOMETRY_WIDTH, GEOMETRY_HEIGHT = 1420, 760
MIN_WIDTH, MIN_HEIGHT = 1040, 560


class MainWindow(QMainWindow):
    """The three tabs, plus the two things that belong to the window itself: its position and
    whether it stays above the game."""

    def __init__(self, zones, skill_list, prefs):
        super().__init__()
        self.prefs = prefs
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        # THE MAP ZOOM IS ONE VALUE FOR THE WINDOW, so it is restored here and not by a tab: three
        # tabs draw a map, and each of them applying the same saved number would be three places to
        # get it wrong. It is set BEFORE the tabs are built, because each map's zoom bar reads the
        # value when it shows itself.
        mapview.set_zoom(prefs.get(MAP_ZOOM_KEY, MAP_ZOOM_DEFAULT))

        # FIVE TABS, and they are the questions: where to grind, what the save records (with where
        # each Coromon can be caught), WHAT IS STILL MISSING AND WHERE TO GET IT - the Database tab's
        # companion, sitting right after it - what the items are (with the stats only the game's Lua
        # has), and what the skills do. The Potential tab was added last (it describes the wild roll
        # itself rather than a list of things to go and do).
        # The Coromon tab USED to sit between the first two with a flat list of every Coromon and its
        # locations - the user: "get rid of the coromon tab, since the database tab superseeds it". It
        # was a second view of the same data: the Database grid holds every dex entry, its crimsonite
        # forms included, and clicking a cell lists that Coromon's locations (see `database_tab.py`),
        # so the list was a copy - and the copy that could not show what the save records.
        self.tabs = QTabWidget()
        self.grind = GrindTab(zones, prefs)
        self.database = DatabaseTab(prefs)
        self.missing = MissingTab(prefs)
        self.items = ItemsTab()
        self.skills = SkillsTab(skill_list, prefs)
        self.potential = PotentialTab(prefs)
        self.tabs.addTab(self.grind, "  Where to grind  ")
        self.tabs.addTab(self.database, "  Database  ")
        self.tabs.addTab(self.missing, "  Missing  ")
        self.tabs.addTab(self.items, "  Items  ")
        self.tabs.addTab(self.skills, "  Skills  ")
        # APPENDED, never inserted: a saved `prefs["tab"]` index has to keep meaning the tab it
        # meant, which is why the new one goes at the end (the Potential tab is a reference page
        # about the wild roll, so it has no natural place among the data tabs).
        self.tabs.addTab(self.potential, "  Potential  ")
        self.setCentralWidget(self.tabs)

        self.grind.onTopToggled.connect(self.set_on_top)
        # ONE TAB PICKS A WILD LOCATION NOW, and it is the Database tab's location list: the ranking
        # it can hand a zone to is the first tab, so the gesture crosses tabs and the window is the
        # one that owns the switch (see `show_zone_on_map`).
        self.database.zoneChosen.connect(self.show_zone_on_map)
        # ... AND THE SAME GESTURE IN THE MISSING TAB: its whole point is choosing where to go next,
        # so a double-clicked row goes to the ranking that can show the spot on the map.
        self.missing.zoneChosen.connect(self.show_zone_on_map)
        self.tabs.currentChanged.connect(self._tab_changed)

        saved = prefs.get("tab")
        if isinstance(saved, int) and 0 <= saved < self.tabs.count():
            self.tabs.setCurrentIndex(saved)

        self.set_on_top(bool(prefs.get("on_top", ON_TOP_DEFAULT)), save=False)

    def set_on_top(self, on, save=True):
        """Keep the window above the game, or stop.

        Applied here rather than in the tab because the flag belongs to the top-level window and
        a child widget cannot restack its parent. Qt wants the window shown again after a flag
        change, which is why this re-shows it: the position and size are kept by Qt itself, so
        it does not jump.
        """
        if save:
            self.prefs.set("on_top", on)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if self.isVisible():
            self.show()

    def show_zone_on_map(self, zone):
        """Show a location picked in another tab: the first tab, with that zone selected.

        The Database tab's location list emits this - it is the only place a wild location is picked
        now that the Coromon tab is gone - and the ranking it is being handed to lives in the first
        tab, so this is the window's own switch rather than anything a tab can do to its sibling.
        """
        self.tabs.setCurrentIndex(0)
        self.grind.show_zone_from_elsewhere(zone)

    def _tab_changed(self, index):
        self.prefs.set("tab", index)

    def closeEvent(self, event):
        """Store the window before it goes: position, size and maximized state in one value."""
        state.save_geometry(self)
        super().closeEvent(event)


def selftest():
    """Build the data and print the ranking without opening a window."""
    zones, species = encounters.load()
    all_zones = encounters.all_zones(zones, species)
    print("zones:", len(all_zones), "species:", len(species))
    skill_list = skills.load()
    print("skills:", len(skill_list), "in", len(skills.types(skill_list)), "types")
    species = dex.monsters()
    print("dex entries:", len(species), "in", len(dex.lines()), "evolutionary lines")
    # Not dex entries - the game has none for them - but Coromon the window lists and the map can
    # point at, so the reading of them is worth a line here: see `dex.crimsonite_forms`.
    forms = dex.crimsonite_forms()
    print("crimsonite forms:", len(forms), "in", len({m.family for m in forms}), "lines,",
          sum(len(dex.where(m.uid, m.skin)) for m in forms), "encounters between them")
    # The Database tab's own numbers, without opening the tab: what the save records per category.
    try:
        import savefile
        slot, _when, slots, owned, seen, skins = savefile.dex_record()
        for cat, label in (("A", "Standard"), ("B", "Potent"), ("C", "Perfect")):
            caught = sum(1 for m in species if cat in savefile.categories(m.uid, owned))
            met = sum(1 for m in species if cat in savefile.categories(m.uid, seen))
            print("  %-9s caught %3d  seen %3d  of %d" % (label, caught, met, len(species)))
        print("  dex record from", slot, "(newest of %d)" % slots)
        # THE SKIN UNLOCKS OF THE SAME SLOT, which is where the Database tab's crimsonite section
        # gets its states from: one key per family, so a line has the skin or it does not.
        lines_hit = {f.family for f in forms if savefile.has_skin(skins, f.family, dex.CRIMSONITE)}
        print("  skin unlocks recorded: %d, crimsonite lines among them: %d of %d"
              % (len(skins), len(lines_hit), len({f.family for f in forms})))
        # THE MISSING TAB'S OWN NUMBERS, from the same record: the groups that are left and the zones
        # that can still fill them (see `missing.py`).
        import missing
        print("  " + missing.report(owned, skins).replace("\n", "\n  "))
    except Exception as exc:                    # noqa: BLE001 - a missing save is not a crash
        print("  dex record unavailable: %s: %s" % (type(exc).__name__, exc))
    for skill in skills.shown(skill_list, "poison")[:2]:
        print("  ", "  ".join("%s=%s" % (key, value) for key, value in skills.row(skill).items()))
        print("    ", skills.resolve(skill.get("description"), skill))
    print("settings file:", state.settings().fileName())
    # THE WILD POTENTIAL ROLL, as the game is configured right now (see `potential.py`): the three
    # settings flags the roll reads, and what they add up to per potential level.
    import potential
    battle, overworld, animations = potential.read_settings()
    count = potential.speed_ups(battle, overworld, animations)
    print("potential: the game has battle x%s, game x%s, encounter animations %s"
          % (battle, overworld, "on" if animations else "off"))
    for scent in (False, True):
        picks = potential.rolls(scent)
        kinds = potential.kind_chances(count, picks)
        print("  %d pick(s)%s: %s  \u00b7  perfect %s"
              % (picks, " (Potent Scent)" if scent else "",
                 "  ".join("%s %s" % (potential.KIND_NAMES[letter], potential.percent(kinds[letter]))
                           for letter, _name, _low, _high in potential.KINDS),
                 potential.one_in(kinds["C"])))
    for zone in rank(all_zones, 64, only_xp=True)[:6]:
        rows = zone_rows(zone)
        print("  %-22s %-18s expLvl %5.2f  %s" % (
            zone.name, mapnames.area(zone.map_file), zone.average_level,
            ", ".join("%s L%s-%s %.0f%%" % (row["name"], row["min"], row["max"], row["share"])
                      for row in rows[:3])))
    return 0


def main(argv=None):
    """Open the window, or answer --selftest without one."""
    parser = argparse.ArgumentParser(
        description="Pick the areas you have; it ranks where to grind.")
    parser.add_argument("--selftest", action="store_true",
                        help="build the data and print the ranking without opening a window")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()

    app = QApplication(sys.argv)
    # QSettings is pinned to the same organisation and application name here as in state.py, so
    # where the settings live does not depend on this being set first
    apply_theme(app)

    prefs = state.Prefs.load()
    zones, species = encounters.load()
    window = MainWindow(encounters.all_zones(zones, species), skills.load(), prefs)
    state.restore_geometry(window, GEOMETRY_WIDTH, GEOMETRY_HEIGHT)
    window.show()
    return app.exec()
