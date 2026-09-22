"""Offscreen smoke test for the grind window.

Checks that the data reaches the widgets: the four tabs, a filled ranking, a map with a plan, the
full dex with icons, the game's own database grid, the skill table and its description, sorting,
filtering, the cross-tab jump, and the window's saved geometry. Runs on Qt's `offscreen` platform,
so it needs no display and nothing running.

    python ui_smoke.py

It uses its OWN QSettings scope. That is not tidiness: the window saves every user decision the
moment it is made - tick list, filters, sort orders, tab - so a test pointed at the real
settings would quietly rewrite them, which is exactly what the first version of this script did.
"""

import os
import sys

# offscreen unless the geometry check was asked for, and this has to be decided BEFORE the
# environment is touched: the platform plugin is chosen when QApplication is built, so setting
# it here would pin even the windowed test to offscreen and it would silently test nothing
if "--geometry" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QSettings, QSize, Qt                    # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

import encounters                                           # noqa: E402
import skills                                               # noqa: E402

import coromontools.state as state                          # noqa: E402
from coromontools import MainWindow, font, icons             # noqa: E402
from coromontools import config                              # noqa: E402
from coromontools.theme import apply_theme                  # noqa: E402

SMOKE_ORG, SMOKE_APP = "CoromonGrindSmoke", "smoke"

FAILURES = []


def console(text):
    """`text` in the console's own encoding, so a Coromon's name cannot kill the run.

    This console is cp1250, which has no "ø" in it - printing Vørst's name raised
    UnicodeEncodeError from inside the harness, and the run died on the DIAGNOSIS rather than on
    the check. Only the printout is replaced; the check itself still compares the real strings.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text).encode(encoding, "replace").decode(encoding, "replace")


def check(name, condition, detail=""):
    print("%-4s %s%s" % ("ok" if condition else "FAIL", console(name),
                         ("   [%s]" % console(detail)) if detail else ""))
    if not condition:
        FAILURES.append(name)


def pump(app, times=3):
    for _ in range(times):
        app.processEvents()


def main():
    if "--geometry" in sys.argv:
        return geometry_test()

    # every write the window makes goes to a scratch scope - see the module docstring
    state.settings = lambda: QSettings(SMOKE_ORG, SMOKE_APP)
    QSettings(SMOKE_ORG, SMOKE_APP).clear()

    app = QApplication(sys.argv)
    apply_theme(app)

    prefs = state.Prefs.load()
    zones, species = encounters.load()
    all_zones = encounters.all_zones(zones, species)
    skill_list = skills.load()

    window = MainWindow(all_zones, skill_list, prefs)
    state.restore_geometry(window, 1420, 760)
    window.show()
    pump(app)

    # ---------------------------------------------------------------- window
    check("four tabs", window.tabs.count() == 4, window.tabs.count())
    check("window titled", bool(window.windowTitle()), window.windowTitle())
    check("min size kept", window.minimumWidth() == 1040 and window.minimumHeight() == 560)

    # ---------------------------------------------------------------- grind tab
    grind = window.grind
    check("areas listed", grind.area_list.count() == len(grind.maps), grind.area_list.count())
    # the tick list is imported from the Tk version's state file the first time, which is the
    # point of the migration - so the expectation is "what was saved", not "everything"
    saved = prefs.get("available")
    expected = len(saved) if isinstance(saved, list) else len(grind.maps)
    check("tick list restored", len(grind.available) == expected,
          "%d of %d ticked, saved list had %d" % (len(grind.available), len(grind.maps),
                                                 expected))
    rows = grind.table.model_.rowCount()
    check("ranking filled", rows > 50, "%d rows at squad level %d" % (rows, grind.level.value()))
    check("a zone is selected", grind.table.current_row() is not None)
    check("species pane filled", len(grind.species.toPlainText()) > 20)
    check("map headline", bool(grind.map.headline), grind.map.headline)
    check("map has a plan", grind.map._plan is not None)
    if grind.map._plan:
        plan = grind.map._plan
        check("plan has ground runs", len(plan["ground"]) > 0, len(plan["ground"]))
        check("plan has a selected patch", any(p["selected"] for p in plan["patches"]))
        check("plan draws no colourless patch", all("colour" in p for p in plan["patches"]))
        check("legend lists the zones", len(grind.map.legend) > 0, len(grind.map.legend))

    # sorting: the numeric column opens high-first, then flips, then comes back
    key, desc = grind.table.sort_state
    check("ranking sorts by exp level, descending", key == "explvl" and desc is True)
    top_before = grind.table.model_.rows[0]["explvl"]
    grind.table._header_clicked(0)
    key, desc = grind.table.sort_state
    check("clicking Area re-sorts ascending", key == "area" and desc is False)
    check("area sort is alphabetical",
          grind.table.model_.rows[0]["area"] <= grind.table.model_.rows[-1]["area"])
    grind.table._header_clicked(2)
    key, desc = grind.table.sort_state
    check("exp level opens descending again", key == "explvl" and desc is True)
    check("first row unchanged by the round trip",
          grind.table.model_.rows[0]["explvl"] == top_before,
          "%s vs %s" % (grind.table.model_.rows[0]["explvl"], top_before))

    # filters: an invariant rather than a count, because the filter may legitimately remove
    # nothing at a low squad level and the check still has to mean something
    grind.only_xp.setChecked(True)
    pump(app)
    with_xp = grind.table.model_.rowCount()
    grind.only_xp.setChecked(False)
    pump(app)
    without_xp = grind.table.model_.rowCount()
    check("'still gives XP' can only remove zones", with_xp <= without_xp,
          "%d with the filter, %d without" % (with_xp, without_xp))

    # untick everything -> the hint, not a blank pane
    grind.set_all(False)
    pump(app)
    check("empty ranking says why", "Nothing is ticked" in grind.species.toPlainText(),
          grind.species.toPlainText().splitlines()[:1])
    check("empty ranking has no rows", grind.table.model_.rowCount() == 0)
    grind.set_all(True)
    pump(app)
    check("re-ticking refills the ranking", grind.table.model_.rowCount() > 0)

    # the area filter hides rows rather than rebuilding the list, so the scroll position is kept
    grind.search.setText("harbor")
    pump(app)
    hidden = sum(1 for i in range(grind.area_list.count()) if grind.area_list.item(i).isHidden())
    check("area filter hides non-matches", hidden == grind.area_list.count() - 1,
          "%d hidden of %d" % (hidden, grind.area_list.count()))
    grind.search.setText("")
    pump(app)

    # ---------------------------------------------------------------- coromon tab
    window.tabs.setCurrentIndex(1)
    pump(app, 5)
    coromon = window.coromon
    check("dex filled on first open", coromon.list.count() == len(coromon.monsters),
          "%d of %d" % (coromon.list.count(), len(coromon.monsters)))
    with_icon = sum(1 for mon in coromon.monsters if icons.icon_image(mon) is not None)
    # EVERY ONE of them: the entry that used to be missing artwork was `NORMAL_SPINNER`, which the
    # game does not have (`dex.UNUSED_UIDS`), so the count is now the whole list rather than one shy.
    check("every Coromon has an icon", with_icon == len(coromon.monsters),
          "%d of %d" % (with_icon, len(coromon.monsters)))
    # The dex number is drawn with the GAME's font, read out of resource.car at runtime - so this
    # is the check that the reading still works (see `coromontools/font.py`).
    check("the game's number font reads", font.available())
    # ... and that an icon still comes out at the size the tabs lay out for.
    check("icon canvas is the cell plus the badge overhang",
          icons.icon_pixmap(coromon.monsters[0], config.ICON_ZOOM).size() ==
          QSize(*icons.icon_size(config.ICON_ZOOM)),
          "%s" % (icons.icon_size(config.ICON_ZOOM),))
    # The row label is the NAME only - the dex number is drawn inside the icon (the game's own
    # font, see `icons.icon_pixmap`) - so the order is checked through the row's own data.
    first = coromon.list.item(0)
    check("first dex row is the #1 Coromon",
          coromon.monsters[0].number == 1
          and first.data(Qt.ItemDataRole.UserRole) == coromon.monsters[0].uid,
          first.text())
    # THE SEVEN WITH NO DEX NUMBER have no number to sort by, so their order is READ OUT of the
    # game's own database screen (`dex.unnumbered_order`). This is the check that the reading still
    # works, and it is written as the order itself rather than as a comparison with `dex`, so a
    # change in either shows up here: Fusebox, then the six titans.
    tail = [coromon.list.item(row).text()
            for row in range(coromon.list.count() - 7, coromon.list.count())]
    check("the numberless Coromon are in the game's order",
          tail == ["Fusebox", "Voltgar", "Illuginn", "Sart", "Hozai", "Vørst", "Chalchiu"],
          tail)

    buzzlet = next((mon for mon in coromon.monsters if mon.name == "Buzzlet"), None)
    check("Buzzlet is in the dex", buzzlet is not None)
    if buzzlet is not None:
        coromon.list.setCurrentItem(coromon.rows[buzzlet.uid])
        pump(app)
        check("Buzzlet has locations", coromon.locations.model_.rowCount() > 0,
              coromon.locations.model_.rowCount())
        check("locations header names it", "Buzzlet" in coromon.head.text(), coromon.head.text())
        zone = coromon.locations.current_payload()
        check("a location carries its zone", zone is not None)
        coromon.zoneChosen.emit(zone)
        pump(app, 3)
        check("jump switched to the grind tab", window.tabs.currentIndex() == 0)
        check("jump drew that zone", grind.map.zone is zone,
              "%s vs %s" % (getattr(grind.map.zone, "name", None), zone.name))

    coromon.search.setText("zzz-nothing")
    pump(app)
    check("dex filter matches nothing", coromon.count_label.text().startswith("0 of"),
          coromon.count_label.text())
    coromon.search.setText("")
    pump(app)

    # ---------------------------------------------------------------- skills tab
    window.tabs.setCurrentIndex(2)
    pump(app, 3)
    skills_tab = window.skills
    # the filters are saved state, so clear them before counting: a run after a filtered one
    # would otherwise start narrowed and the count would be the previous run's answer
    skills_tab.type_box.setCurrentIndex(0)
    skills_tab.search.setText("")
    pump(app)
    check("every skill listed", skills_tab.table.model_.rowCount() == len(skill_list),
          "%d of %d" % (skills_tab.table.model_.rowCount(), len(skill_list)))
    check("a skill is selected", skills_tab.table.current_payload() is not None)
    check("description filled", len(skills_tab.detail.toPlainText()) > 40)
    check("type box lists the types",
          skills_tab.type_box.count() == len(skills.types(skill_list)) + 1,
          skills_tab.type_box.count())

    skills_tab.type_box.setCurrentIndex(skills_tab.type_box.findText("poison"))
    pump(app)
    filtered = skills_tab.table.model_.rowCount()
    check("type filter narrows the list", 0 < filtered < len(skill_list), filtered)
    check("filtered list is all poison",
          all("Poison" in skills_tab.table.model_.rows[i]["type"] for i in range(filtered)))

    skills_tab.table._header_clicked(3)                  # Power
    key, desc = skills_tab.table.sort_state
    check("Power opens descending", key == "power" and desc is True)
    check("top power is a real number", skills_tab.table.model_.rows[0]["power"] not in ("", "-"),
          skills_tab.table.model_.rows[0]["power"])
    skills_tab.table._header_clicked(3)                  # flip
    key, desc = skills_tab.table.sort_state
    check("Power flips to ascending", key == "power" and desc is False)
    check("ascending puts the blanks last",
          skills_tab.table.model_.rows[-1]["power"] in ("-", ""),
          skills_tab.table.model_.rows[-1]["power"])

    # ---------------------------------------------------------------- saved state
    check("tab index saved", prefs.get("tab") == 2, prefs.get("tab"))
    check("skill filters saved", prefs.get("skill_type") == "poison", prefs.get("skill_type"))
    check("prefs readable back", state.load_prefs().get("skill_type") == "poison")
    check("nothing written to the real settings",
          not QSettings(state.ORG, state.APP_NAME).contains("prefs")
          or state.settings().fileName() != QSettings(state.ORG, state.APP_NAME).fileName())

    window.close()
    pump(app)
    check("geometry saved on close", bool(state.settings().value("window/geometry", "")))
    # the round trip itself is NOT asserted here. The offscreen plugin keeps no real frame
    # geometry - measured: a window shown at 1420x760 saves a 66-byte blob that restores to
    # 1040x760, its minimum width - so this platform cannot answer the question. Run
    # `python ui_smoke.py --geometry` for it, on real windows.
    print("note  geometry round trip checked separately: python ui_smoke.py --geometry")

    print("\n%d failure(s)" % len(FAILURES))
    for name in FAILURES:
        print("  -", name)
    return 1 if FAILURES else 0


def geometry_test():
    """Does the window come back where it was - maximized included, and un-maximizing intact?

    Needs a real platform plugin: the offscreen one cannot round-trip a geometry. Four windows
    are created and closed in well under a second, so they flash rather than sit there.

    This is the behaviour the tkinter version got wrong: a maximized window reported the screen
    size as its own, so the size it came back to was not the size it was left at.
    """
    app = QApplication(sys.argv)
    apply_theme(app)
    state.settings = lambda: QSettings(SMOKE_ORG, SMOKE_APP)
    QSettings(SMOKE_ORG, SMOKE_APP).clear()

    zones, species = encounters.load()
    all_zones = encounters.all_zones(zones, species)
    skill_list = skills.load()

    def opened():
        window = MainWindow(all_zones, skill_list, state.Prefs.load())
        state.restore_geometry(window, 1420, 760)
        window.show()
        pump(app, 6)
        return window

    first = opened()
    first.resize(1234, 777)
    first.move(140, 110)
    pump(app, 6)
    placed = (first.width(), first.height())
    first.close()
    pump(app, 3)

    second = opened()
    check("size restored", (second.width(), second.height()) == placed,
          "%dx%d vs %dx%d" % (second.width(), second.height(), placed[0], placed[1]))
    check("position restored", abs(second.x() - 140) <= 40 and abs(second.y() - 110) <= 40,
          "%d,%d vs 140,110" % (second.x(), second.y()))

    second.showMaximized()
    pump(app, 8)
    check("window really is maximized", second.isMaximized())
    second.close()
    pump(app, 3)

    third = opened()
    check("comes back maximized", third.isMaximized(), third.isMaximized())
    if third.isMaximized():
        third.showNormal()
        pump(app, 8)
        check("un-maximizing returns to the size it had before",
              (third.width(), third.height()) == placed,
              "%dx%d vs %dx%d" % (third.width(), third.height(), placed[0], placed[1]))
    third.close()
    pump(app, 3)

    QSettings(SMOKE_ORG, SMOKE_APP).clear()
    print("\n%d failure(s)" % len(FAILURES))
    for name in FAILURES:
        print("  -", name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
