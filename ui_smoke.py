"""Offscreen smoke test for the grind window.

Checks that the data reaches the widgets: the three tabs, a filled ranking, a map with a plan, the
game's own database grid and the dex icons in it, the skill table and its description, sorting,
filtering, the cross-tab jump, and the window's saved geometry. Runs on Qt's `offscreen` platform,
so it needs no display and nothing running.

    python ui_smoke.py

It uses its OWN QSettings scope. That is not tidiness: the window saves every user decision the
moment it is made - tick list, filters, sort orders, tab - so a test pointed at the real
settings would quietly rewrite them, which is exactly what the first version of this script did.
"""

import os
import re
import sys

# offscreen unless the geometry check was asked for, and this has to be decided BEFORE the
# environment is touched: the platform plugin is chosen when QApplication is built, so setting
# it here would pin even the windowed test to offscreen and it would silently test nothing
if "--geometry" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QSettings, QSize, Qt                    # noqa: E402
from PySide6.QtTest import QTest                                  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel        # noqa: E402

import encounters                                           # noqa: E402
import skills                                               # noqa: E402

import dex                                                  # noqa: E402
import savefile                                             # noqa: E402

import coromontools.state as state                          # noqa: E402
from coromontools import MainWindow, font, icons             # noqa: E402
from coromontools import config                              # noqa: E402
from coromontools.database_tab import CATEGORIES, SKIN_COLUMN  # noqa: E402
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
    # THE COROMON TAB IS GONE - the Database tab supersedes it (see `coromontools/__init__.py`), so
    # the tabs are the three questions there are, in that order.
    titles = [window.tabs.tabText(index).strip() for index in range(window.tabs.count())]
    check("three tabs, in that order",
          titles == ["Where to grind", "Database", "Skills"], titles)
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

    # CRIMSONITE SPAWNS ARE IN THE LISTINGS. The encounter data marks a slot as the crimsonite form
    # (`encounters.Zone.crimsonite`), and that form is a Coromon of its own - so a zone that spawns
    # one says so, under the name the game gives it and with the share that slot really has. Its
    # share is counted APART from the ordinary form's but the two still add up to the zone's 100%,
    # which is why the expected level per encounter - the one number the ranking is sorted by -
    # counts both: you meet a crimsonite while walking like anything else.
    mixed_zone = next((z for z in all_zones if z.crimsonite), None)
    check("a zone spawns a crimsonite form", mixed_zone is not None,
          getattr(mixed_zone, "name", None))
    if mixed_zone is not None:
        keep_zone, keep_share = grind.selected_zone, grind.min_share.value()
        # the split loses nothing and merges nothing: one slot in the game's table is one form
        raw_pairs = {(m.get("monsterUID"), bool(m.get("crimsonite")))
                     for e in mixed_zone.raw["encounters"] for m in e.get("monsters", [])}
        check("every slot in the zone's table becomes one of the two forms, nothing merged",
              len(raw_pairs) == len(mixed_zone.monsters) + len(mixed_zone.crimsonite),
              "%d slot(s) -> %d ordinary + %d crimsonite"
              % (len(raw_pairs), len(mixed_zone.monsters), len(mixed_zone.crimsonite)))
        ordinary_level = sum(r["share"] / 100.0 * (r["min"] + r["max"]) / 2.0
                             for r in mixed_zone.monsters.values())
        check("the expected level counts both forms",
              abs(mixed_zone.average_level -
                  sum(r["share"] / 100.0 * (r["min"] + r["max"]) / 2.0
                      for r in mixed_zone.slots())) < 1e-9
              and mixed_zone.average_level > ordinary_level,
              "%.2f (%.2f without the crimsonite slots)"
              % (mixed_zone.average_level, ordinary_level))
        grind.min_share.setValue(0)
        grind.show_zone(mixed_zone)
        pump(app)
        names = ["Crimsonite " + mixed_zone.species.get(uid, uid)
                 for uid in mixed_zone.crimsonite]
        text = grind.species.toPlainText()
        check("the zone's species pane names the crimsonite form",
              all(name in text for name in names)
              and text.count("crimsonite") >= len(names), names)
        grind.show_zone(keep_zone)
        grind.min_share.setValue(keep_share)
        pump(app)

    # ---------------------------------------------------------------- database tab
    # THE GRID IS THE ONLY DEX VIEW NOW. The Coromon tab used to list every Coromon beside its
    # locations; the user: "get rid of the coromon tab, since the database tab superseeds it". The
    # grid already holds every dex entry and clicking a cell lists that Coromon's locations in the
    # right column, so what that tab's checks were checking is checked here, on the grid.
    # The tab reads the save when it is first SHOWN and never on its own again, so the Reload button
    # is the only way a save made while the window is open gets in - both paths, then.
    window.tabs.setCurrentIndex(1)
    pump(app, 5)
    database = window.database

    # THE LAYOUT: two columns with a slider between them, and the right column split again into the
    # locations OVER the map - the user's own instruction, and the reason the grid gets its full
    # height back ("no footer needed").
    check("there is no footer any more", not hasattr(database, "footer"))
    check("the grid and the right column are the two children of one slider",
          database.split.orientation() == Qt.Orientation.Horizontal
          and database.split.count() == 2
          and database.split.widget(0) is database.scroll
          and database.split.widget(1) is database.head.parentWidget(),
          "%d child(ren)" % database.split.count())
    check("neither column can be dragged shut", not database.split.childrenCollapsible())
    check("the right column is the location list OVER the map",
          database.right_split.orientation() == Qt.Orientation.Vertical
          and database.right_split.count() == 2
          and database.right_split.widget(0) is database.locations
          and database.right_split.widget(1).isAncestorOf(database.map),
          "%d child(ren)" % database.right_split.count())
    # AND THE MAP IS DIRECTLY UNDER THE LIST'S LAST ROW, not half the column: the list gets its own
    # content height back and the map takes the rest - the user: "the map section should be directly
    # below the last line in the list, so we can fit a taller map if needed".
    database.select(database.lines[0][1][0])
    pump(app, 3)
    sizes = database.right_split.sizes()
    check("the list is only as tall as its own rows",
          abs(sizes[0] - database.locations.content_height()) <= 2,
          "list %d px, %d row(s) want %d"
          % (sizes[0], database.locations.model_.rowCount(),
             database.locations.content_height()))
    check("the map takes the rest of the column", sizes[1] > sizes[0] * 2,
          "list %d, map %d" % tuple(sizes))

    # ... AND THE GRID IS THE WHOLE DEX: one cell per entry per category, plus one per crimsonite
    # form (which has no dex entry of its own - see `dex.crimsonite_forms`).
    entries = sum(len(line) for _family, line in database.lines)
    check("the grid holds every dex entry in every category, and each form once",
          len(database.cells) == entries * len(CATEGORIES) + len(dex.crimsonite_forms()),
          "%d cell(s) = %d entries x %d categories + %d form(s)"
          % (len(database.cells), entries, len(CATEGORIES), len(dex.crimsonite_forms())))
    with_icon = sum(1 for mon in dex.monsters(with_crimsonite=True)
                    if icons.icon_image(mon) is not None)
    # EVERY ONE of them: the entry that used to be missing artwork was `NORMAL_SPINNER`, which the
    # game does not have (`dex.UNUSED_UIDS`), so the count is now the whole list rather than one shy.
    check("every Coromon has an icon", with_icon == len(dex.monsters(with_crimsonite=True)),
          "%d of %d" % (with_icon, len(dex.monsters(with_crimsonite=True))))
    # The dex number is drawn with the GAME's font, read out of resource.car at runtime - so this
    # is the check that the reading still works (see `coromontools/font.py`).
    check("the game's number font reads", font.available())
    # ... and that an icon still comes out at the size the grid lays out for - the cell, the side the
    # sprite overhangs and the badge, which is what `icon_size` adds up.
    check("icon canvas is the cell plus the overhangs the grid sizes for",
          icons.icon_pixmap(dex.monsters()[0], config.ICON_ZOOM).size() ==
          QSize(*icons.icon_size(config.ICON_ZOOM)),
          "%s" % (icons.icon_size(config.ICON_ZOOM),))
    # THE CAPTIONS CARRY THE NAME ONLY - the dex number is drawn INSIDE the icon, in the game's own
    # font and the entry's state colour (see `icons.icon_pixmap`) - so a caption repeating it is
    # checked against here rather than trusted to stay gone.
    numbered = [caption.text() for _pic, caption in database.cells.values()
                if "#" in caption.text()]
    check("no caption repeats the number the icon draws", not numbered, numbered)
    check("the first row starts at the #1 Coromon",
          database.lines[0][1][0].number == 1,
          "%s (#%s)" % (database.lines[0][1][0].name, database.lines[0][1][0].number))
    # THE SEVEN WITH NO DEX NUMBER have no number to sort by, so their order is READ OUT of the
    # game's own database screen (`dex.unnumbered_order`) and they are the last rows of the grid.
    # This is the check that the reading still works, and it is written as the order itself rather
    # than as a comparison with `dex`, so a change in either shows up here: Fusebox, then the six
    # titans.
    tail = [database.lines[index][1][0].name
            for index in range(len(database.lines) - 7, len(database.lines))]
    check("the numberless Coromon are the last rows, in the game's order",
          tail == ["Fusebox", "Voltgar", "Illuginn", "Sart", "Hozai", "Vørst", "Chalchiu"],
          tail)

    # CRIMSONITE FORMS ARE COROMON OF THEIR OWN, and the cell they have in the fourth group is where
    # they are picked: clicking it has to list the places the FORM spawns - its own encounters, not
    # the species' - because the encounter data says which slot is which
    # (`encounters.Zone.crimsonite`).
    plain = next((m for m in dex.monsters(with_crimsonite=True)
                  if m.uid == "ELECTRIC_FIREFLY_1" and not m.skin), None)
    form = next((m for m in dex.monsters(with_crimsonite=True)
                 if m.uid == "ELECTRIC_FIREFLY_1" and m.skin), None)
    check("a crimsonite form is a Coromon of its own",
          plain is not None and form is not None and form.key != plain.key
          and form.name.startswith("Crimsonite "),
          "%s / %s" % (getattr(plain, "name", None), getattr(form, "name", None)))
    if form is not None and plain is not None:
        form_icon = icons.icon_pixmap(form, config.ICON_ZOOM)
        plain_icon = icons.icon_pixmap(plain, config.ICON_ZOOM)
        check("... and its own crimsonite sprite",
              form_icon is not None and plain_icon is not None
              and form_icon.toImage() != plain_icon.toImage(), form.name)
        form_cell = next((cell for key, cell in database.cells.items()
                          if key[1] == SKIN_COLUMN
                          and database.click_target[cell[0]].key == form.key), None)
        check("... with a cell of its own in the crimsonite group", form_cell is not None)
        if form_cell is not None:
            QTest.mouseClick(form_cell[0], Qt.MouseButton.LeftButton)
            pump(app, 2)
            rows = database.locations.model_.rows
            check("... that lists where the FORM spawns, not the species",
                  bool(rows) and all(row["zone"].startswith("WATER") for row in rows),
                  [row["zone"] for row in rows])
            check("... and names the form in the heading",
                  database.head.text().startswith("Crimsonite "), database.head.text())

    # THE FIND BOX HIDES WHOLE LINES, because a row is the unit here: a species that matches shows
    # the LINE it is on and nothing else, so Buzzlet's row is three stages wide and comes up in all
    # three category groups. The dex NUMBER still matches even though no caption shows it any more,
    # because "35" or "#35" pasted out of a wiki is how a Coromon is usually looked up.
    line = next((stages for _family, stages in database.lines if stages[0].name == "Buzzlet"), [])
    names = sorted(stage.name for stage in line * len(CATEGORIES))
    database.search.setText("zzz-nothing")
    pump(app)
    shown = [caption.text() for _pic, caption in database.cells.values()
             if caption.parentWidget().isVisible()]
    check("a find that matches nothing hides every line", not shown, shown)
    for needle in ("buzzlet", "35"):
        database.search.setText(needle)
        pump(app)
        shown = sorted(caption.text() for _pic, caption in database.cells.values()
                       if caption.parentWidget().isVisible())
        check("the line for %r is the only one shown" % needle, shown == names, shown)
    database.search.setText("")
    pump(app)

    if database.error is None:
        # WHICH slot is stated rather than implied: it is the newest that records a dex and the
        # autosave is a slot like any other, so "newest of 2" plus the timestamp IS the answer.
        # ELIDED TEXT IS STILL THERE in the tooltip - see `_elide_labels` - which is where this asks
        # when the test's own rendering is too narrow for the whole report.
        report = database.saved_label.toolTip() or database.saved_label.text()
        check("the save report names the newest slot and when it was saved",
              bool(database.slot) and "newest of" in report
              and re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d", report) is not None,
              report)
    else:
        print("note database tab could not read the save: %s" % database.error)
    check("the heading says nothing about the save", "save:" not in database.head.text(),
          database.head.text()[:60])
    # ONE COMPACT LINE: the search field is capped rather than stretching, and the save report sits
    # on the same line as the button that re-reads the file, immediately to its left. A wrapped row
    # would show up here as a label taller than the button.
    button, report = database.reload_button.geometry(), database.saved_label.geometry()
    row = [database.search, database.counts, database.saved_label, database.reload_button]
    heights = [widget.geometry().height() for widget in row]
    check("the search field is capped, not stretched",
          database.search.maximumWidth() < 300, database.search.maximumWidth())
    check("the top row is a single line", max(heights) - min(heights) <= 8, heights)
    check("the save report is on the button's row, to its left",
          report.right() <= button.left() and abs(report.center().y() - button.center().y()) <= 20,
          "report %s, button %s" % (report, button))
    check("the three tallies are split by a dot, not a wide gap",
          (database.counts.toolTip() or database.counts.text()).count(" \u00b7 ") == 2,
          database.counts.toolTip() or database.counts.text())

    # THE LIST LISTS WHERE A COROMON CAN BE CAPTURED, and potential is not a factor: the three
    # category columns are three pictures of ONE species, so a click in any of them must list the
    # same places - the same areas, zones, levels and shares.
    buzz = [key for key, (_pic, caption) in database.cells.items() if caption.text() == "Buzzlet"]
    answers = []
    for key in buzz:
        QTest.mouseClick(database.cells[key][0], Qt.MouseButton.LeftButton)
        pump(app, 2)
        answers.append((database.head.text(),
                        tuple((row["area"], row["zone"], row["levels"], row["share"])
                              for row in database.locations.model_.rows)))
    check("Buzzlet has a cell in each category column", len(buzz) == 3, len(buzz))
    # "Woodlow Harbor" is the game's own name for the `harbor` map (`mapnames.area`, generated
    # from the map's `setMapName` plus `world.map.<key>.name`), not the file name prettified - the
    # area column used to read "Harbor" and the whole point of the table is that it reads what the
    # game shows.
    check("a click lists where it can be captured, whatever the category",
          len(set(answers)) == 1 and answers[0][0].startswith("Buzzlet")
          and ("Woodlow Harbor", "HARBOR_A", "L7-12", "44.4%") in answers[0][1],
          "%s -> %s" % (answers[0][0], answers[0][1][:2]))

    # THE MAP IS UNDER THE LIST: picking a row draws that area there, without leaving the tab - and
    # that is what the slider between the two halves is for.
    database.locations.selectRow(1)
    pump(app, 3)
    picked = database.locations.current_payload()
    check("picking a row draws that area on the map below",
          picked is not None and database.map.zone is picked
          and not database.map_head.isVisible(),
          "%s | %r" % (getattr(picked, "name", None), database.map_head.text()))
    # ... and a DOUBLE click is the step beyond it: hand that area to the first tab, which is where
    # the grinder can use it (the tab's own gesture, through the signal the window handles).
    database._jump()
    pump(app, 3)
    check("a double-clicked location opens on the first tab's map",
          window.tabs.currentIndex() == 0 and grind.map.zone is picked,
          "%s vs %s" % (getattr(grind.map.zone, "name", None), getattr(picked, "name", None)))
    window.tabs.setCurrentIndex(1)
    pump(app, 3)

    # THE CRIMSONITE SECTION - a fourth column group after Perfect, the user's own suggestion: the
    # skins the game SPAWNS IN THE WILD, which is the crimsonite one and nothing else (every monster
    # slot in the encounter data carries one skin flag, and the 38 other skins a save can unlock are
    # cosmetic - see `dex.crimsonite_forms`). It is NOT a potential category: the game has no
    # crimsonite dex entry, so the cells are filled from the save's SKIN UNLOCKS instead
    # (`savefile.has_skin`), and each sits in the stage column of the Coromon it is a form of.
    heads = {label.text(): label for label in database.holder.findChildren(QLabel)}
    check("the crimsonite section is titled, after the Perfect group",
          "Crimsonite" in heads and "Perfect" in heads
          and heads["Crimsonite"].x() > heads["Perfect"].x(),
          "Crimsonite x=%s, Perfect x=%s" % (
              heads["Crimsonite"].x() if "Crimsonite" in heads else None,
              heads["Perfect"].x() if "Perfect" in heads else None))
    skin_cells = {key: cell for key, cell in database.cells.items() if key[1] == SKIN_COLUMN}
    module_forms = dex.crimsonite_forms()
    check("every crimsonite form has a cell, and no other cell has one",
          len(skin_cells) == len(module_forms)
          and {database.click_target[cell[0]].key for cell in skin_cells.values()}
          == {form.key for form in module_forms},
          "%d cell(s) for %d form(s)" % (len(skin_cells), len(module_forms)))
    check("a crimsonite cell sits in the stage column of the Coromon it is a form of",
          all(database.click_target[cell[0]].uid == database.lines[index][1][stage].uid
              for (index, _group, stage), cell in skin_cells.items()),
          sorted(key for key in skin_cells))
    check("a line that spawns no crimsonite form has no cell in the section",
          all((buzz[0][0], SKIN_COLUMN, stage) not in database.cells
              for stage in range(database.stages)),
          "Buzzlet's line")
    # THE STATE COMES FROM THE SAVE'S OWN SKIN UNLOCKS, so the check is the CONTRAST between an
    # unlocked form (drawn like a caught dex entry, with the badge) and one no save unlocked (dulled
    # like a seen one) rather than a count that a different save would fail.
    try:
        _slot, _when, _slots, _owned, _seen, skins = savefile.dex_record()
    except Exception as exc:                                  # noqa: BLE001 - note, not a failure
        skins = set()
        print("note the crimsonite skin unlocks could not be read: %s" % exc)
    met, unmet = [], []
    for (index, _group, _stage), cell in skin_cells.items():
        family = database.lines[index][0]
        (met if savefile.has_skin(skins, family, dex.CRIMSONITE) else unmet).append(cell[0])
    if met and unmet:
        check("a crimsonite form is dull until the save has that skin unlocked",
              met[0].pixmap().toImage() != unmet[0].pixmap().toImage()
              and "crimsonite skin unlocked" in met[0].toolTip()
              and "crimsonite skin not unlocked yet" in unmet[0].toolTip(),
              "%d unlocked, %d not" % (len(met), len(unmet)))
        QTest.mouseClick(met[0], Qt.MouseButton.LeftButton)
        pump(app, 2)
        rows = database.locations.model_.rows
        check("a crimsonite cell lists its own locations in the list",
              bool(rows) and database.head.text().startswith("Crimsonite "),
              "%s -> %s" % (database.head.text(), [row["zone"] for row in rows]))
    else:
        print("note the save has all or none of the crimsonite skins unlocked, so the two "
              "drawings could not be contrasted")

    # THE -/+ BUTTONS in the corner: the icon scale is the app's own state (like the window's
    # position, and one scale for the whole window), and it resizes the grid it is pressed on.
    start = database.zoom
    size = database.cells[(0, 0, 0)][0].size()
    column = database.grid.columnMinimumWidth(0)
    database.zoom_in.click()
    pump(app, 5)
    check("+ makes every icon bigger - and its column with it - and remembers it",
          database.zoom == start + 1
          and database.cells[(0, 0, 0)][0].size() != size
          and database.grid.columnMinimumWidth(0) != column
          and prefs.get(config.ICON_ZOOM_KEY) == database.zoom,
          "zoom %d -> %d, cell %s -> %s, column %s -> %s"
          % (start, database.zoom, size, database.cells[(0, 0, 0)][0].size(), column,
             database.grid.columnMinimumWidth(0)))
    database.zoom_out.click()
    pump(app, 5)
    check("- puts it back", database.zoom == start and database.grid.columnMinimumWidth(0) == column,
          "%d, cell %s" % (database.zoom, database.cells[(0, 0, 0)][0].size()))
    for _ in range(config.ICON_ZOOM_MIN + config.ICON_ZOOM_MAX):
        database.zoom_out.click()
    pump(app, 3)
    check("the scale stops at the bottom", database.zoom == config.ICON_ZOOM_MIN, database.zoom)
    for _ in range(20):
        database.zoom_in.click()
    pump(app, 3)
    check("the scale stops at the top", database.zoom == config.ICON_ZOOM_MAX, database.zoom)
    for _ in range(database.zoom - start):
        database.zoom_out.click()
    pump(app, 3)

    # THE -/+ BUTTONS MUST ACTUALLY SHOW THEIR GLYPH: the theme pads a button 12 px a side, and on a
    # 22 px button that leaves no content rect at all, so Qt draws an EMPTY square - which is exactly
    # what the user's screenshot showed. Counting the colours in the button's own grab catches it:
    # fill + border is 2 colours, and any glyph on top adds more.
    for label, button in (("-", database.zoom_out), ("+", database.zoom_in)):
        grab = button.grab().toImage()
        ink = len({grab.pixelColor(x, y).getRgb()
                   for y in range(grab.height()) for x in range(grab.width())})
        check("the %s zoom button draws its glyph" % label, ink > 2, "%d colour(s)" % ink)

    report, tally = database.saved_label.text(), database.counts.text()
    database.reload_button.click()
    pump(app, 5)
    check("Reload save re-reads the same record",
          database.saved_label.text() == report and database.counts.text() == tally,
          "%s" % database.saved_label.text())

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
