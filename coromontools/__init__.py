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
* ``coromon``    - the Coromon tab
* ``database_tab`` - the Database tab: caught / seen / unknown, per potential category
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

from . import state                                                    # noqa: E402
from .config import ON_TOP_DEFAULT                                     # noqa: E402
from .coromon import CoromonTab                                        # noqa: E402
from .database_tab import DatabaseTab                                  # noqa: E402
from .grind import GrindTab, rank, zone_rows                           # noqa: E402
from .skills_tab import SkillsTab                                      # noqa: E402
from .text import pretty                                               # noqa: E402
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

        self.tabs = QTabWidget()
        self.grind = GrindTab(zones, prefs)
        self.coromon = CoromonTab()
        self.database = DatabaseTab(prefs)
        self.skills = SkillsTab(skill_list, prefs)
        self.tabs.addTab(self.grind, "  Where to grind  ")
        self.tabs.addTab(self.coromon, "  Coromon  ")
        self.tabs.addTab(self.database, "  Database  ")
        self.tabs.addTab(self.skills, "  Skills  ")
        self.setCentralWidget(self.tabs)

        self.grind.onTopToggled.connect(self.set_on_top)
        self.coromon.zoneChosen.connect(self.show_zone_on_map)
        self.database.zoneChosen.connect(self.show_zone_on_map)
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

        Both tabs that list wild locations emit this - the Coromon tab's list and the Database tab's
        footer - so one handler serves both and they cannot drift apart.
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
    # The Database tab's own numbers, without opening the tab: what the save records per category.
    try:
        import savefile
        slot, owned, seen = savefile.monster_record()
        for cat, label in (("A", "Standard"), ("B", "Potent"), ("C", "Perfect")):
            caught = sum(1 for m in species if cat in savefile.categories(m.uid, owned))
            met = sum(1 for m in species if cat in savefile.categories(m.uid, seen))
            print("  %-9s caught %3d  seen %3d  of %d" % (label, caught, met, len(species)))
        print("  dex record from", slot)
    except Exception as exc:                    # noqa: BLE001 - a missing save is not a crash
        print("  dex record unavailable: %s: %s" % (type(exc).__name__, exc))
    for skill in skills.shown(skill_list, "poison")[:2]:
        print("  ", "  ".join("%s=%s" % (key, value) for key, value in skills.row(skill).items()))
        print("    ", skills.resolve(skill.get("description"), skill))
    print("settings file:", state.settings().fileName())
    for zone in rank(all_zones, 64, only_xp=True)[:6]:
        rows = zone_rows(zone)
        print("  %-22s %-18s expLvl %5.2f  %s" % (
            zone.name, pretty(zone.map_file), zone.average_level,
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
