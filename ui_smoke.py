"""Offscreen smoke test for the grind window.

Checks that the data reaches the widgets: the six tabs, a filled ranking, a map with a plan, the
game's own database grid and the dex icons in it, the item catalogue with the stats the game's Lua
holds, the skill table and its description, the wild Potential roll under the game's own settings,
sorting, filtering, the cross-tab jump, and the window's saved geometry. Runs on Qt's `offscreen`
platform, so it needs no display and nothing running.

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
from PySide6.QtGui import QFontMetrics                   # noqa: E402
from PySide6.QtTest import QTest                                  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel        # noqa: E402

import encounters                                           # noqa: E402
import skills                                               # noqa: E402

import dex                                                  # noqa: E402
import items as item_data                                   # noqa: E402
import missing as missing_data                              # noqa: E402
import potential                                            # noqa: E402
import savefile                                             # noqa: E402

import coromontools.state as state                          # noqa: E402
from coromontools import MainWindow, font, icons, mapnames as mn   # noqa: E402
from coromontools import maptiles                              # noqa: E402
from coromontools import config                              # noqa: E402
from coromontools.database_tab import CATEGORIES, SKIN_COLUMN  # noqa: E402
from coromontools.grind import encounter_lines                # noqa: E402
from coromontools import mapview                              # noqa: E402
from coromontools import missing_tab                          # noqa: E402
from coromontools import potential_tab                        # noqa: E402
from coromontools import table as table_mod                  # noqa: E402
from coromontools import theme                                # noqa: E402
from coromontools.table import ICON, PAYLOAD, sort_rows            # noqa: E402
from coromontools.theme import apply_theme                  # noqa: E402
from coromontools.widgets import mono_height                # noqa: E402

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
    # THE COROMON TAB IS GONE - the Database tab supersedes it (see `coromontools/__init__.py`) - and
    # the Missing tab joined the set after the Database one, which it is the companion of. The
    # Potential tab is APPENDED (see `coromontools/__init__.py`), so every saved tab index still means
    # what it meant.
    titles = [window.tabs.tabText(index).strip() for index in range(window.tabs.count())]
    check("six tabs, in that order",
          titles == ["Where to grind", "Database", "Missing", "Items", "Skills", "Potential"], titles)
    check("window titled", bool(window.windowTitle()), window.windowTitle())
    check("min size kept", window.minimumWidth() == 1040 and window.minimumHeight() == 560)

    # ---------------------------------------------------------------- grind tab
    grind = window.grind
    # WHICH COLUMN TO CLICK IS LOOKED UP BY KEY, never by position: the ranking's columns are ordered
    # for a small window (the numbers first), so a `_header_clicked(3)` that used to mean "x p / fight"
    # now means something else - and a stale index makes a sort check pass while testing nothing.
    col = lambda key: [c.key for c in grind.table.model_.columns].index(key)     # noqa: E731

    def pane_rows(text):
        """`[(name, share, [fight, smart, sloth, lazy])]` - the priced lines of a spawns pane.

        READ BY COLUMNS, NOT BY A SUFFIX. Every priced line ends with the four numbers, and the two
        tokens before them are the level range ("L55 -60") and the share; a party name may contain
        spaces, so the name is whatever is left in front. The heading row and an entry the game cannot
        roll (whose columns are "-") fail the `isdigit` test and drop out - which is the point.
        """
        out = []
        for line in text.split("\n"):
            parts = line.split()
            if len(parts) > 7 and parts[-1].isdigit():
                out.append((" ".join(parts[:-7]), parts[-5], [int(v) for v in parts[-4:]]))
        return out
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
        # THE ZONES ARE BLOCKS OF CELLS, each with the sides that face out of its own block. EVERY FILL
        # IS TRANSLUCENT, the selected zone's included - the user: "some of the blocks show as opaque,
        # they should all be semi transparent with solid edges around the zones" - and the selected one
        # is told apart by a WHITE edge instead.
        check("every zone fill is translucent, and every edge opaque",
              all(p["fill"].alpha() == config.PATCH_ALPHA and p["edge"].alpha() == 255
                  for p in plan["patches"]),
              sorted({p["fill"].alpha() for p in plan["patches"]}))
        selected = next(p for p in plan["patches"] if p["selected"])
        check("... with the selected zone's edge white, and no zone's fill set apart",
              selected["edge"].name() == config.SELECTED_EDGE
              and selected["fill"].alpha() == plan["patches"][0]["fill"].alpha(),
              selected["edge"].name())
        # ... AND EVERY NAME IS WRITTEN ON A CELL OF ITS OWN BLOCK - the user: "currently we put the
        # labels in the center of a group - but that could easily be outside of the group - we need to
        # still put them into the group". The middle of a block's BOX is not in the block when the shape
        # is an L, a ring, or two patches that only touch at a corner.
        inside = []
        named = []
        for patch in plan["patches"]:
            for block in patch["blocks"]:
                cells = {(x, y) for (y, x0, x1) in block["runs"] for x in range(x0, x1)}
                for (cx, cy, text, _big) in block["labels"]:
                    inside.append((int(cx), int(cy)) in cells)
                    named.append((len(cells), bool(block["labels"])))
        check("... whose blocks carry fills, merged outlines and a name each",
              all(block["runs"] and (block["outline"][0] or block["outline"][1])
                  for patch in plan["patches"] for block in patch["blocks"])
              and all(inside),
              "%d name(s), all on their own block: %s" % (len(inside), all(inside)))
        # A LONE MARKER CELL IS NOT NAMED: 72 of them over the shipped maps, each one a name as wide as
        # the text over a single tile - the user: "to reduce the number of labels". The BIGGEST block of
        # a zone is named whatever its size, so a zone can never come out nameless on the map.
        check("... and a lone marker cell is named only when it is the zone's biggest block",
              all(bool(block["labels"]) == (index == 0 or block["cells"] > 1)
                  for patch in plan["patches"]
                  for index, block in enumerate(patch["blocks"])),
              "%d block(s) besides a zone's first: %s"
              % (sum(len(patch["blocks"]) - 1 for patch in plan["patches"]),
                 [block["cells"] for patch in plan["patches"] for block in patch["blocks"]][:8]))
        check("... and the map's own tiles are the ground when they could be read",
              plan["picture"] is not None
              and plan["picture"].width() == plan["size"][0] * 16,
              None if plan["picture"] is None else
              "%dx%d px" % (plan["picture"].width(), plan["picture"].height()))
        check("legend lists the zones", len(grind.map.legend) > 0, len(grind.map.legend))

    # THE MAP VIEW ZOOMS, AND THE PANE SCROLLS. The value is the window's (`mapview.set_zoom`), so the
    # same pad appears over all three maps, all of them move together, and the selected zone is scrolled
    # into view - which is what makes a zoomed map useful rather than a corner of tiles.
    map_bar = grind.findChild(mapview.MapZoomBar)
    check("the map panel carries its own -/+ zoom pad", map_bar is not None
          and map_bar.zoom_in.text() == "+" and map_bar.zoom_out.text() == "-",
          None if map_bar is None else "%s ... %s" % (map_bar.zoom_out.text(), map_bar.zoom_in.text()))
    # A BUTTON IS AS TALL AS A BUTTON. Measuring the row's own height while it was still empty gave 5 px,
    # and the user saw it: the two controls stood "only as tall as the chars" beside a `fit` that had a
    # real height. Every button of the window's theme is padding + one line of text, and these wear the
    # same vertical padding - so the pad matches its neighbours instead of the glyphs.
    theme_button = next(widget for widget in window.findChildren(type(map_bar.fit_button))
                        if widget not in (map_bar.zoom_in, map_bar.zoom_out, map_bar.fit_button)
                        and widget.isVisible())
    check("... whose buttons are a button's height, like every other button in the window",
          map_bar.zoom_in.height() == map_bar.fit_button.height()
          and map_bar.zoom_in.height() >= theme_button.height()
          and map_bar.zoom_in.width() == map_bar.zoom_out.width(),
          "-%d/%d  +%d  fit%d  vs %s=%d" % (
              map_bar.zoom_out.width(), map_bar.zoom_out.height(), map_bar.zoom_in.height(),
              map_bar.fit_button.height(), theme_button.text()[:8], theme_button.height()))
    # ... AND THE STEPPER'S TWO BUTTONS ARE THE SAME BUTTON, which is why they are built the same way:
    # their own padding, a fixed width, and their height taken from the max of the two hints. A label
    # whose text changes length is NOT allowed to shuffle them, so it keeps a fixed width too.
    check("the stepper's < and > are the pad's buttons, and its label keeps its room",
          grind.variant_bar.back.width() == map_bar.zoom_in.width() == mapview.ZOOM_BUTTON_WIDTH
          and grind.variant_bar.back.height() == grind.variant_bar.forward.height()
          == map_bar.zoom_in.height()
          and grind.variant_bar.states_label.minimumWidth() == mapview.VARIANT_LABEL_WIDTH,
          "%dx%d, label >= %d px" % (grind.variant_bar.back.width(),
                                     grind.variant_bar.back.height(),
                                     grind.variant_bar.states_label.minimumWidth()))
    fit_scale = grind.map._scale
    check("... which opens fitted to the pane, with nothing to scroll",
          mapview.zoom() == config.MAP_ZOOM_DEFAULT
          and grind.map.horizontalScrollBar().maximum() == 0
          and grind.map.verticalScrollBar().maximum() == 0,
          "x%g, scale %.2f" % (mapview.zoom(), fit_scale))
    map_bar.zoom_in.click()
    pump(app, 3)
    check("... and a + press scales the map up and gives the pane scrollbars",
          mapview.zoom() > config.MAP_ZOOM_DEFAULT and grind.map._scale > fit_scale
          and (grind.map.verticalScrollBar().maximum() > 0
               or grind.map.horizontalScrollBar().maximum() > 0),
          "x%g, scale %.2f -> %.2f, ranges %d/%d" % (
              mapview.zoom(), fit_scale, grind.map._scale,
              grind.map.horizontalScrollBar().maximum(),
              grind.map.verticalScrollBar().maximum()))
    # THE ZOOM BELONGS TO THE WINDOW: the Missing tab's map is a different widget with its own pad, and
    # its pad reads the same factor because there is only one value.
    missing_bar = window.missing.findChild(mapview.MapZoomBar)
    check("... and every map in the window follows it",
          missing_bar is not None and missing_bar.value.text() == map_bar.value.text()
          and missing_bar.zoom_in.isEnabled() == map_bar.zoom_in.isEnabled(),
          "%s vs %s" % (map_bar.value.text(),
                         None if missing_bar is None else missing_bar.value.text()))
    # EVERY STEP IS A WHOLE NUMBER, AND EVERY STEP MOVES THE MAP. The ladder used to run 1, 1.25, 1.75,
    # 2.5, 4, 6 while the finished scale was clamped at `MAP_MAX_SCALE`, so from x4 up the pane drew the
    # SAME picture while the label kept counting - the buttons were dead over half their range, which is
    # what the user reported. Measured off a live map: one distinct drawn scale per step, growing.
    scales = []
    for step in config.MAP_ZOOM_STEPS:
        mapview.set_zoom(step)
        pump(app, 1)
        scales.append(grind.map._scale)
    check("... every step is a whole number, and each one draws the map bigger",
          all(float(step).is_integer() for step in config.MAP_ZOOM_STEPS)
          and len(set(scales)) == len(scales) and scales == sorted(scales),
          "%s -> %s" % (list(config.MAP_ZOOM_STEPS), [round(value, 2) for value in scales]))
    # ... AND THE VIEW STAYS WHERE IT IS, WHATEVER IS PICKED. It used to scroll the picked zone into
    # sight, which reads well once and is wrong when you are comparing rows: the map jumps under the
    # cursor on every click - the user: "it looks like it auto scrolls the maps to focus on the
    # selected area - that should not happen". Zoomed in past x1 (which is the state here, and the only
    # state in which scrolling is possible at all), scrolled to a corner, the scroll must come back
    # UNCHANGED.
    grind.map.centre_on((0.99, 0.99))
    pump(app, 2)
    corner = (grind.map.horizontalScrollBar().value(), grind.map.verticalScrollBar().value())
    grind.show_zone_from_elsewhere(grind.table.current_payload())
    pump(app, 3)
    check("... and picking a zone does not move the view",
          (grind.map.horizontalScrollBar().value(), grind.map.verticalScrollBar().value()) == corner
          and corner[1] > 0,
          "scroll %s -> %s" % (corner, (grind.map.horizontalScrollBar().value(),
                                        grind.map.verticalScrollBar().value())))
    # THE SCALING FILTER IS A TICK ON EVERY MAP PANEL AND A REMEMBERED VALUE, like the zoom: the map
    # picture is drawn far smaller than its own pixels here, where smooth is the only readable choice,
    # and past 1:1 nearest neighbour ("in game it's scaled integer nearest neighbour") is the crisp one.
    filter_tick = grind.findChild(mapview.MapFilterCheck)
    check("every map panel carries a smooth-scaling tick",
          filter_tick is not None and filter_tick.text() == "smooth"
          and filter_tick.isChecked() == mapview.smooth()
          and window.missing.findChild(mapview.MapFilterCheck) is not None,
          None if filter_tick is None else "%r, checked %s" % (filter_tick.text(),
                                                               filter_tick.isChecked()))
    smooth_shot = grind.map.grab().toImage()
    filter_tick.setChecked(False)
    pump(app, 3)
    nearest_shot = grind.map.grab().toImage()
    check("... and unticking it really resamples the map",
          not mapview.smooth() and prefs.get(config.MAP_SMOOTH_KEY) is False
          and smooth_shot != nearest_shot
          and nearest_shot.size() == smooth_shot.size(),
          "smooth %s, pref %s, pictures %s"
          % (mapview.smooth(), prefs.get(config.MAP_SMOOTH_KEY),
             "differ" if smooth_shot != nearest_shot else "identical"))
    filter_tick.setChecked(True)
    pump(app, 3)
    check("... and ticking it back gives the smooth picture again",
          mapview.smooth() and prefs.get(config.MAP_SMOOTH_KEY) is True
          and grind.map.grab().toImage() == smooth_shot,
          "smooth %s" % mapview.smooth())
    map_bar.fit_button.click()
    pump(app, 3)
    check("'fit' takes it back to x1, and the range closes up again",
          mapview.zoom() == config.MAP_ZOOM_DEFAULT
          and grind.map._scale == fit_scale
          and grind.map.horizontalScrollBar().maximum() == 0,
          "x%g, scale %.2f" % (mapview.zoom(), grind.map._scale))
    map_bar.zoom_in.click()
    pump(app, 2)
    check("... and the factor is remembered for the next start",
          prefs.get(config.MAP_ZOOM_KEY) == mapview.zoom(), prefs.get(config.MAP_ZOOM_KEY))
    map_bar.fit_button.click()
    pump(app, 2)

    # THE STORY-STATE STEPPER. The states are the game's own condition names, and the control is two
    # buttons around one label - `<  as saved  >` - because a row of buttons cannot hold them:
    # `iceTown` carries NINE states and the longest name in the game is 32 characters
    # (`beforeOrWhileRESTORE_EQUILIBRIUM`). One press walks to the next state of the map on screen and
    # repaints every map in the window in it; `as saved` is the list's first entry and the map as the
    # file has it.
    ice_zone = encounters.Zone("iceTown", {"name": "ICETOWN"}, [])
    plain_zone = encounters.Zone("waterRoute_4", {"name": "WATERROUTE_4_WATER"}, [])
    import encounter_zones as ez
    kept_zone = grind.map.zone
    grind.map.set_zone(ice_zone)
    # ZOOMED IN PAST x1, which is the only state in which a view can move at all - the check below is
    # that flipping a state does NOT move it.
    mapview.set_zoom(3)
    pump(app, 3)
    check("the stepper appears on a map that has story states",
          grind.variant_bar is not None and grind.variant_bar.isVisible()
          and grind.variant_bar.states[0] is None and len(grind.variant_bar.states) == 10,
          None if grind.variant_bar is None else "%d entry(ies), first %r"
          % (len(grind.variant_bar.states), grind.variant_bar.states[0]))
    check("... and the label says which state, with the long name in the tooltip",
          grind.variant_bar.states_label.text() == mapview.VARIANT_SAVED
          and mapview.short_state("beforeOrWhileRESTORE_EQUILIBRIUM") == "RESTORE_EQUILIB\u2026"
          and mapview.short_state("whileEVACUATION") == "EVACUATION"
          and "whileEVACUATION" in grind.variant_bar.toolTip(),
          "%r, %r" % (grind.variant_bar.states_label.text(),
                      mapview.short_state("beforeOrWhileRESTORE_EQUILIBRIUM")))
    grind.map.centre_on((0.99, 0.99))
    pump(app, 2)
    held = (grind.map.horizontalScrollBar().value(), grind.map.verticalScrollBar().value())
    saved_picture = grind.map._plan["picture"]
    saved_shot = grind.map.grab().toImage()
    grind.variant_bar.forward.click()
    pump(app, 3)
    flipped = grind.map._plan["picture"]
    check("one press flips the map to another state",
          mapview.variant() == "afterFESTIVAL_DELIVER_CARROT"
          and flipped is maptiles.picture("iceTown", "afterFESTIVAL_DELIVER_CARROT")
          and flipped is not saved_picture
          and grind.map.grab().toImage() != saved_shot
          and grind.variant_bar.states_label.text() == "FESTIVAL_DELIVE\u2026",
          "%r -> %r" % (None, mapview.variant()))
    check("... which does not move the view either",
          (grind.map.horizontalScrollBar().value(), grind.map.verticalScrollBar().value()) == held
          and held[1] > 0,
          "scroll %s -> %s" % (held, (grind.map.horizontalScrollBar().value(),
                                      grind.map.verticalScrollBar().value())))
    walked = [mapview.variant()]
    for _press in range(len(grind.variant_bar.states) - 1):
        grind.variant_bar.forward.click()
        walked.append(mapview.variant())
    check("... and the buttons cycle every state and then 'as saved'",
          walked[-1] is None and len(set(walked)) == len(walked)
          and set(walked) == set(grind.variant_bar.states),
          "%d step(s) %s" % (len(walked), walked[:3]))
    check("... and going back to 'as saved' draws the saved map again",
          grind.map._plan["picture"] is saved_picture,
          None if grind.map._plan["picture"] is saved_picture else "a different picture")
    grind.map.set_zone(plain_zone)
    pump(app, 2)
    check("the stepper hides on a map with no story states",
          not maptiles.variants(ez.map_parts(ez.find_map_file("waterRoute_4"))[0])
          and not grind.variant_bar.isVisible(),
          "%d state(s) left in the control" % (len(grind.variant_bar.states) - 1))
    grind.map.set_zone(kept_zone)
    mapview.set_variant(None)
    mapview.set_zoom(config.MAP_ZOOM_DEFAULT)
    pump(app, 3)

    # THE MERGING ITSELF, measured off the shipped maps: a water zone records its shape as bare marker
    # cells (its markers name no tile layer), and merging is what turns them into blocks -
    # `WATERROUTE_4_WATER`'s 170 cells are TWO blocks (151 + 19), where cell by cell they were a field
    # of dots. The borders are counted the same way, on shapes whose answer is arithmetic.
    _m, _layers, zone_data = ez.zone_map("waterRoute_4")
    water = zone_data["WATERROUTE_4_WATER"]
    water_blocks = ez.zone_blocks(water)
    check("a water zone's bare marker cells are merged into a few blocks",
          len(water["unplaced"]) > 100 and 1 <= len(water_blocks) <= 4
          and sum(len(block) for block in water_blocks)
          == len({(x, y) for (x, y, _why) in water["unplaced"]}),
          "%d cell(s) -> %d block(s) %s" % (len(water["unplaced"]), len(water_blocks),
                                            [len(block) for block in water_blocks]))
    pair = ez.block_outline({(4, 4), (5, 4)})
    check("... two adjacent cells share one border instead of drawing two",
          sorted(pair[0]) == [(4, 4, 5), (4, 5, 5), (5, 4, 6), (5, 5, 6)]
          and sorted(pair[1]) == [(4, 4, 5), (6, 4, 5)],
          "%d horizontal + %d vertical segment(s)" % (len(pair[0]), len(pair[1])))
    # CORNER-TOUCHING GROUPS ARE ONE GROUP - the user: "if groups touch by corner - we could consider
    # that as a single group as well, to reduce the number of labels". MEASURED on the shipped maps:
    # `oasisCave_3`'s water zone is 367 cells in ONE block where it was three, and one-cell blocks over
    # every zone went from 128 to 72.
    check("... and groups that only touch at a corner are one group",
          len(ez.components({(0, 0), (1, 1)})) == 2
          and len(ez.components({(0, 0), (1, 1)}, diagonal=True)) == 1
          and len(ez.components({(0, 0), (1, 1), (2, 0)}, diagonal=True)) == 1,
          "4-connected: %d, corner-connected: %d"
          % (len(ez.components({(0, 0), (1, 1)})),
             len(ez.components({(0, 0), (1, 1)}, diagonal=True))))
    _m3, _l3, cave3 = ez.zone_map("oasisCave_3")
    cave_water = ez.zone_blocks(cave3["OASISCAVE_3_WATER"])
    check("... which is what turns a scattered water shape into one block",
          len(cave_water) == 1 and len(cave_water[0]) == 367,
          "%d cell(s) -> %d block(s)"
          % (sum(len(b) for b in cave_water), len(cave_water)))
    square = ez.block_outline({(4, 4), (5, 4), (4, 5), (5, 5)})
    check("... and a 2x2 block has no line through the middle of it",
          len(square[0]) == 4 and len(square[1]) == 4
          and not any(segment[0] == 5 for segment in square[1])
          and not any(segment[1] == 5 for segment in square[0]),
          "%d horizontal + %d vertical segment(s)" % (len(square[0]), len(square[1])))
    check("the tiles are composited once and then reused",
          maptiles.picture("amishRoute") is maptiles.picture("amishRoute"))

    # A MARKER WHOSE OWN CELL IS NOT ON ITS GRASS STILL FINDS THE PATCH IT NAMES. The tile under a
    # marker comes from its `tileLayer` layer, and failing that from the nearest layer of the same
    # family, and failing THAT from the cells around it - because a marker is placed ON a patch and is
    # often standing on something else inside or beside it. This is the map the user reported: on
    # Vermeer Grotto 3 the grass at the top came out as the single tile the marker sits on, because
    # (35,12) says "level 1" while its grass is one layer up, (20,29) says "level 2" while its grass is
    # one layer down, and (33,12) is one cell to the RIGHT of its 43-cell region.
    _m3, _l3, cave3 = ez.zone_map("oasisCave_3")
    cave_grass = cave3["OASISCAVE_3"]
    patched = set().union(*cave_grass["patches"])
    # A HIDDEN LAYER IS NOT PART OF THE MAP THE GAME DRAWS, and the two the user caught say what
    # happens when one is: `harbor`'s hidden `aboveFloor 2#MESCHER_REALM` (88 tiles of the ghost
    # realm) drew long purple lines across the sand - "some maps like woodland harbor, have those
    # strange lines on the ground, that are not shown in the game" - and electricTown's hidden
    # `rainDrops` (960 drops) put white dots all over Donar Island. The flag is Tiled authoring state,
    # but the state it records is the one the map is drawn in; the `#whileEVACUATION`, `#whileChristmas`
    # and `#MESCHER_REALM` layers beside a base layer are ALTERNATIVES for other states.
    # MEASURED over the 69 maps: the hidden layers draw 4781 cells, of which only 40 are cells no
    # visible layer covers - so honouring the flag drops 4781 cells of content the game never shows.
    for map_name, hidden_name in (("harbor", "aboveFloor 2#MESCHER_REALM"),
                                  ("electricTown", "rainDrops"),
                                  ("electricTown", "level 3#whileChristmas")):
        drawn_here = maptiles.layer_names(ez.map_parts(ez.find_map_file(map_name))[0])
        check("%s does not draw its hidden %s layer" % (map_name, hidden_name),
              hidden_name not in drawn_here, "%d layer(s) drawn" % len(drawn_here))
    offenders, with_hidden = [], 0
    for map_name in grind.maps:
        map_d, _l, _s = ez.map_parts(ez.find_map_file(map_name))
        drawn_here = set(maptiles.layer_names(map_d))
        hidden = {name for _g, name, layer, off in maptiles._walk(map_d.get("layers") or [])
                  if layer.get("type") == "tilelayer" and off}
        with_hidden += 1 if hidden else 0
        if drawn_here & hidden:
            offenders.append(map_name)
    check("... and no map in the window draws a hidden layer at all",
          not offenders and with_hidden > 0,
          "%d map(s) have hidden layers, %d of them drawn: %s"
          % (with_hidden, len(offenders), offenders[:4]))

    # THE ALTERNATIVES ARE NOT THROWN AWAY, THEY ARE KEPT AS STATES - which is the other half of the
    # hidden-layer question: those layers are the map as ANOTHER STORY MOMENT draws it, and the window
    # flips between them instead of quietly dropping them (the user: "how about we keep them, with
    # buttons in the top to flip between them, and just add a blacklist for the ones to hide - so we
    # can see each variant separately"). A state is named by the condition after the `#`, it is the
    # state of THIS map (a state a map does not have leaves it as saved), and choosing one draws that
    # version of a layer INSTEAD of the plain one - not on top of it.
    ice_map = ez.map_parts(ez.find_map_file("iceTown"))[0]
    check("a map's story states are read off its own layers",
          len(maptiles.variants(ice_map)) == 9
          and "whileEVACUATION" in maptiles.variants(ice_map)
          and maptiles.variants(ez.map_parts(ez.find_map_file("amishRoute"))[0])
          == ["MESCHER_REALM"],
          "iceTown has %d state(s), amishRoute %s"
          % (len(maptiles.variants(ice_map)),
             maptiles.variants(ez.map_parts(ez.find_map_file("amishRoute"))[0])))
    with_states = [name for name in grind.maps
                   if maptiles.variants(ez.map_parts(ez.find_map_file(name))[0])]
    check("... on 23 of the 69 maps, and the rest are drawn as saved",
          len(with_states) == 23 and len(grind.maps) == 69,
          "%d of %d map(s): %s" % (len(with_states), len(grind.maps), with_states[:4]))
    harbor_map = ez.map_parts(ez.find_map_file("harbor"))[0]
    as_saved = maptiles.layer_names(harbor_map)
    as_mescher = maptiles.layer_names(harbor_map, "MESCHER_REALM")
    check("choosing a state draws that state's version of a layer instead of the plain one",
          "aboveFloor 2#MESCHER_REALM" not in as_saved
          and "aboveFloor 2#MESCHER_REALM" in as_mescher
          and "aboveFloor 2" not in as_mescher,
          "%d layer(s) as saved -> %d in MESCHER_REALM" % (len(as_saved), len(as_mescher)))
    # AND THE STATE REPLACES WHAT IT HAS, AND NOTHING ELSE. Worked out from the map file itself: a
    # state draws its own version of every base layer it has one for, and for the rest the layers the
    # map has visible - `level 4` and `level 4#whileAnniversary` are BOTH visible on electricTown, so
    # both are drawn, while a hidden `rainDrops` is drawn in no state at all.
    wrong_states, unseen = [], 0
    for map_name in with_states:
        map_d = ez.map_parts(ez.find_map_file(map_name))[0]
        seen = {}
        for _g, name, layer, off in maptiles._walk(map_d.get("layers") or []):
            if layer.get("type") == "tilelayer" and maptiles._is_terrain(_g) \
                    and not maptiles._blacklisted(name):
                seen.setdefault(name.partition("#")[0], []).append((name, off))
        for picked in maptiles.variants(map_d):
            unseen += 1
            expected = set()
            for base, versions in seen.items():
                names = [name for (name, _off) in versions]
                if base + "#" + picked in names:
                    expected.add(base + "#" + picked)
                else:
                    expected.update(name for (name, off) in versions if not off)
            if set(maptiles.layer_names(map_d, picked)) != expected:
                wrong_states.append((map_name, picked,
                                     sorted(set(maptiles.layer_names(map_d, picked)) ^ expected)))
    check("... one state's version of a layer, and the saved layers everywhere else",
          not wrong_states and unseen == 51,
          "%d state(s) checked, %d wrong: %s" % (unseen, len(wrong_states), wrong_states[:2]))
    # THE BLACKLIST IS FOR THE LAYERS THAT ARE NOT PART OF THE MAP AT ALL, matched by own name or by
    # base name: `rainDrops` is 960 white dots the game draws as WEATHER, and the `worldRain*` /
    # `worldOverlay` group are the ones the user reported as white dots on Donar Island.
    check("the weather layers are blacklisted, whichever state they are in",
          all(maptiles._blacklisted(name) for name in config.MAP_LAYER_BLACKLIST)
          and maptiles._blacklisted("rainDrops#whileChristmas")
          and not maptiles._blacklisted("level 1"),
          ", ".join(config.MAP_LAYER_BLACKLIST))
    stray = []
    for map_name in grind.maps:
        map_d = ez.map_parts(ez.find_map_file(map_name))[0]
        here = [name for name in maptiles.layer_names(map_d)
                if name.partition("#")[0] in config.MAP_LAYER_BLACKLIST]
        if here:
            stray.append((map_name, here))
    check("... and no map draws one in any state",
          not stray, "%d map(s): %s" % (len(stray), stray[:2]))
    check("a state is asked for by name, and the map is cached per (map, state)",
          maptiles.picture("harbor", "MESCHER_REALM")
          is maptiles.picture("harbor", "MESCHER_REALM")
          and maptiles.picture("harbor", "MESCHER_REALM") is not maptiles.picture("harbor")
          and maptiles.picture("harbor", "notMESCHER_REALM") is not maptiles.picture("harbor"))
    check("a marker whose own cell is not on its grass still finds the patch",
          not cave_grass["unplaced"]
          and sorted(len(p) for p in cave_grass["patches"]) == [12, 24, 29, 30, 36, 43, 44, 46]
          and {(35, 12), (20, 29)} <= patched,
          "%d patch(es) %s, %d marker(s) left over %s"
          % (len(cave_grass["patches"]), sorted(len(p) for p in cave_grass["patches"]),
             len(cave_grass["unplaced"]),
             [why for (_x, _y, why) in cave_grass["unplaced"]]))
    # ... AND THE WHOLE GRASS OF A MAP IS MARKED BY ONE ZONE OR ANOTHER, which is the check that says
    # no area was left out: every cell of the grass tileset on a `level*` layer has to be inside some
    # zone's patch (or be one of its bare marker cells), and no patch may sit anywhere else - a zone
    # that marks the ROCK is the other half of the same complaint: "in dojo grounds, one area is
    # marked at the rocks, not the grass". `dojoGrounds` is the map that has both: (27,64) stands on a
    # `rockWalls` cell in the middle of a 74-cell grass region, and its zone also has six markers
    # plainly on grass, so the rock is not what the zone is made of.
    def grass_of(map_name):
        """{(x, y)} where any `level*` layer holds a tile of the grass tilesets."""
        map_d, map_layers, map_sets = ez.map_parts(ez.find_map_file(map_name))
        ranges = [(s["lo"], s["hi"]) for s in map_sets if s["name"] in ("grass", "grass_burned")]
        cells = set()
        for layer_name, layer in map_layers.items():
            if not layer_name.startswith("level"):
                continue
            data = layer["data"]
            for y in range(map_d["height"]):
                for x in range(map_d["width"]):
                    gid = data[y * map_d["width"] + x]
                    if any(lo <= gid < hi for (lo, hi) in ranges):
                        cells.add((x, y))
        return cells

    for map_name, zones_on_it in (("oasisCave_3", ["OASISCAVE_3"]),
                                  ("dojoGrounds", ["DOJOGROUNDS_A", "DOJOGROUNDS_B"])):
        grass = grass_of(map_name)
        marked = set()
        _mm, _ll, entries = ez.zone_map(map_name)
        for zone_name in zones_on_it:
            entry = entries[zone_name]
            if entry["patches"]:
                marked |= set().union(*entry["patches"])
            marked |= {(x, y) for (x, y, _why) in entry["unplaced"]}
        left = grass - marked
        check("every grass cell of %s is marked, and nothing but grass is" % map_name,
              not left and not (marked - grass),
              "%d grass cell(s), %d marked, %d left over, %d wrong "
              "(patches %s)" % (len(grass), len(marked), len(left), len(marked - grass),
                                [sorted((len(p) for p in entries[z]["patches"]), reverse=True)
                                 for z in zones_on_it]))

    # ... AND IT IS THE GRASS IT FOUND, NOT THE ROCK UNDER IT: `floor` is a different family of layer
    # names, so a `level 1` marker never falls back to the wall tileset the floor is drawn from.
    _m4, layers4, sets4 = ez.map_parts(ez.find_map_file("oasisCave_3"))
    grass_set = next(s for s in sets4 if s["name"] == "grass")
    level2 = layers4["level 2"]["data"]
    found = next(tiles for tiles in cave_grass["patches"] if (35, 12) in tiles)
    check("... and what it found is the grass, not the rock the map is built on",
          all(grass_set["lo"] <= level2[y * _m4["width"] + x] < grass_set["hi"]
              for (x, y) in found),
          "%d cell(s), every one a grass tile of `level 2`" % len(found))

    # A LEGEND CHIP IS WIDE ENOUGH FOR ITS OWN LABEL. "WATER" is five letters and "SPECIAL" seven,
    # against a fixed 22 px square wearing the window's own font - which cut both of them off (the
    # user: "in the legend, labels like WATER don't fit the small square at the top"). The chip is
    # measured from its text now, and the font is the small print's own size.
    keep_zone = grind.table.current_payload()
    water_zone = next((row.get(PAYLOAD) for row in grind.table.model_.rows
                       if row.get(PAYLOAD) is not None
                       and row[PAYLOAD].name.endswith("_WATER")), None)
    grind.show_zone(water_zone)
    pump(app, 3)
    water_chip = next((chip for chip in grind.legend_row.findChildren(QLabel)
                       if chip.text() == "WATER"), None)
    check("a legend chip fits the label in it",
          water_chip is not None
          and water_chip.width() >= water_chip.fontMetrics().horizontalAdvance(water_chip.text()) + 2
          and water_chip.height() == config.CHIP_HEIGHT
          and water_chip.font().pixelSize() == config.CHIP_FONT_PX,
          None if water_chip is None else "%s in %dx%d px, text %d px, font %d"
          % (water_chip.text(), water_chip.width(), water_chip.height(),
             water_chip.fontMetrics().horizontalAdvance(water_chip.text()),
             water_chip.font().pixelSize()))
    grind.show_zone(keep_zone)
    pump(app, 2)

    # EVERY TERRAIN LAYER IS DRAWN, WHATEVER IT IS CALLED. The layers used to be picked by a NAME
    # allow-list (`floor*`, `level*`, `aboveFloor*`), which silently left out every layer the game does
    # not name that way - the user: "some sprites are still missing from some of the maps". MEASURED
    # OFF THE SHIPPED MAPS with that rule: iceMountain drew 5597 of its 11900 cells (its `mountain 1`
    # and `mountain 2` and `trees` layers were never considered), icePeak 3139 of 6794, pyramid_f4 1625
    # of 4500 (no `innerWalls`, no `outerWalls`), templeDungeon_03 427 of 744 - 13588 cells missing
    # across 14 of the 69 maps, and none at all on the other 55. They are picked by the GROUP they sit
    # in now (`maptiles.DRAWN_GROUPS`), and every one of those maps fills every cell it has.
    mountain_map, _layers, _sets = ez.map_parts(ez.find_map_file("iceMountain"))
    drawn = maptiles.layer_names(mountain_map)
    check("a map is drawn from every terrain layer it has, called what it may",
          all(name in drawn for name in ("mountain 1", "mountain 2", "trees", "level 1"))
          and not any(name.startswith("abovePlayer") for name in drawn),
          "%d layer(s): %s" % (len(drawn), ", ".join(drawn[:4])))
    image = maptiles.picture("iceMountain").toImage()
    step_x, step_y = mountain_map["tilewidth"], mountain_map["tileheight"]
    empty = [(x, y) for y in range(mountain_map["height"]) for x in range(mountain_map["width"])
             if not image.pixelColor(x * step_x + step_x // 2, y * step_y + step_y // 2).alpha()]
    check("... and every cell of that map comes out filled",
          not empty, "%d empty cell(s) of %d" % (len(empty), mountain_map["width"] * mountain_map["height"]))

    # sorting: the numeric column opens high-first, then flips, then comes back
    # IT OPENS ON XP PER FIGHT NOW, because that is the question this tab exists to answer - the user:
    # "the where to grind tab is basically meant to tell me where to get the most XP per encounter, but
    # I am not sure if the values are accurate, actually it doens't even show the xp".
    key, desc = grind.table.sort_state
    check("ranking opens on xp per fight, biggest first", key == "xp" and desc is True)
    xps = [float(row["xp"]) for row in grind.table.model_.rows]
    check("... ranked as a number, biggest first", xps == sorted(xps, reverse=True), xps[:4])
    check("... and every zone has one", all(row["xp"] for row in grind.table.model_.rows))
    top_before = grind.table.model_.rows[0]["zone"]
    grind.table._header_clicked(col("area"))
    key, desc = grind.table.sort_state
    check("clicking Area re-sorts ascending", key == "area" and desc is False)
    check("area sort is alphabetical",
          grind.table.model_.rows[0]["area"] <= grind.table.model_.rows[-1]["area"])
    grind.table._header_clicked(col("explvl"))
    key, desc = grind.table.sort_state
    check("exp level opens descending again", key == "explvl" and desc is True)
    check("exp level is ranked as a number too, biggest first",
          [float(r["explvl"]) for r in grind.table.model_.rows]
          == sorted((float(r["explvl"]) for r in grind.table.model_.rows), reverse=True),
          [r["explvl"] for r in grind.table.model_.rows][:4])
    # ... AND THE TWO COLUMNS ARE NOT THE SAME LIST, which is worth asserting rather than assuming:
    # the highest-XP zone and the highest-LEVEL zone were the same one only while a data typo
    # (PYRAMID_F6's level 2725) topped both. Sorting back on xp has to restore the same first row -
    # that is the round trip; that the two orders differ is the reason the tab ranks on XP.
    grind.table._header_clicked(col("xp"))
    key, desc = grind.table.sort_state
    check("sorting back on xp restores the same first row",
          key == "xp" and desc is True and grind.table.model_.rows[0]["zone"] == top_before,
          "%s vs %s" % (grind.table.model_.rows[0]["zone"], top_before))

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

    # THE XP NUMBER IS DERIVED, NOT TYPED IN, so it is checked by recomputing it from the parts
    # rather than against a remembered constant. The column is `Zone.xp_per_encounter(fed with
    # dex.xp_reward)`, and `dex.xp_reward` reads the game's own formula out of the Lua: the Coromon's
    # place in its evolution line x the mean of its base stats x its level / 2.
    grind.only_xp.setChecked(False)
    pump(app)
    grouped_zone = next((z for z in all_zones
                         if any(m["count"] > 1 for r in z.encounters() for m in r["members"])), None)
    check("some zone spawns a group", grouped_zone is not None,
          getattr(grouped_zone, "name", None))
    if grouped_zone is not None:
        want = 0.0
        for row, weight in grouped_zone.rollable_encounters():
            want += weight * grouped_zone.encounter_xp(row, dex.xp_reward)
        got = grouped_zone.xp_per_encounter(dex.xp_reward)
        check("xp per fight is the shares times the reward, recomputed member by member",
              abs(got - want) < 0.5, "%.1f vs %.1f in %s" % (got, want, grouped_zone.name))
        # a group is worth EVERY member: drop the extra bodies and the number has to fall
        leader = max(grouped_zone.encounters(),
                     key=lambda r: sum(m["count"] for m in r["members"]))
        bodies = sum(m["count"] for m in leader["members"])
        one = sum(dex.xp_reward(m["uid"], (m["min"] + m["max"]) / 2.0) for m in leader["members"])
        check("a group of %d is worth all %d bodies" % (bodies, bodies),
              sum(dex.xp_reward(m["uid"], (m["min"] + m["max"]) / 2.0) * m["count"]
                  for m in leader["members"]) > one, "%s" % leader["name"])

    # THE XP GEMS ARE COLUMNS, right after the fight's own worth - the user: "after the base XP column,
    # show a column showing how much the coromon holding the Smart Gem would earn ... then Sloth gem -
    # which claims 50% xp for coromon not participating in the battle and Lazy gem - 20% for not
    # participating". The multipliers are the ones my reading of the game's own effect bodies says, so
    # the first check is that the archive still agrees: `items.xp_multiplier` reads
    # `mutateXpEarned(_monsterSprite, _isLazy, _xpPerMonsterSprite, _value)` out of
    # `classes.items.HOLD_*_XP`, where the Smart Gem scales `_value` (what its holder was going to get)
    # and the lazy gems scale `_xpPerMonsterSprite` - but ONLY for a holder that did not face the
    # enemy, because that is when `_value` is 0 and the gem is the whole of its earnings.
    want_gems = {"HOLD_EXTRA_XP": 1.1, "HOLD_LAZY_XP_PREMIUM": 0.5, "HOLD_LAZY_XP": 0.2}
    got_gems = {uid: item_data.xp_multiplier(uid) for uid in want_gems}
    check("the Smart Gem and the two lazy gems read as 1.1x / 0.5x / 0.2x out of the game",
          got_gems == want_gems, got_gems)
    check("... and a gem with no XP effect has none", item_data.xp_multiplier("HOLD_RECOVER_HEALTH") is None)
    headings = [column.heading for column in grind.table.model_.columns]
    # THE NUMBERS COME FIRST - Zone, then the fight's worth and the three gems - because this window
    # is used beside the game: at the size it ships with (1040x560) the table has a 428 px viewport,
    # and with the names in front every XP column was past the right edge, which is why the user
    # asked "it's not showing the extra columns for exp anywhere still, am I overlooking them?".
    check("the four xp columns come first, so they fit a small window",
          [column.key for column in grind.table.model_.columns][:5]
          == ["zone", "xp", "smart", "sloth", "lazy"], headings)
    check("... and they are narrow enough to fit one together",
          sum(column.width for column in grind.table.model_.columns[:5]) <= 430,
          sum(column.width for column in grind.table.model_.columns[:5]))
    # THE FILTER ROW IS ONE LINE AND STILL INSIDE ITS COLUMN, which is a real constraint and not a
    # nicety: the middle column is fitted to the TABLE's width, so a control row wider than the table
    # would push the separator out and take the width from the map. The story-states tick joined it
    # last, and the row measures 691 px against a 719 px table at the window's real size.
    control_row = [grind.level, grind.only_xp, grind.min_share, grind.states_tick, grind.on_top]
    centres = sorted({widget.geometry().center().y() for widget in control_row})
    check("the ranking's filters are one row, and the row fits the column",
          max(centres) - min(centres) <= 2
          and grind.on_top.geometry().right() <= grind.states_tick.parentWidget().width() - 4,
          "centres %s, ends at %d of %d" % (centres, grind.on_top.geometry().right(),
                                            grind.states_tick.parentWidget().width()))
    rows = grind.table.model_.rows
    # tolerance 1.5, not 1: both figures are ROUNDED for display, and the column multiplies the
    # unrounded fight, so a row can land a whole unit off (PYRAMID_F4: 1330 shown, x1.1 = 1463.0, but
    # its real fight is 1329.93 so the cell reads 1462)
    check("every row prices all three gems off its own fight",
          all(abs(float(r["smart"]) - float(r["xp"]) * 1.1) <= 1.5
              and abs(float(r["sloth"]) - float(r["xp"]) * 0.5) <= 1.5
              and abs(float(r["lazy"]) - float(r["xp"]) * 0.2) <= 1.5 for r in rows),
          [(r["xp"], r["smart"], r["sloth"], r["lazy"]) for r in rows[:2]])
    check("... so the Smart Gem is worth MORE than the fight and the lazy gems less",
          all(float(r["smart"]) > float(r["xp"]) > float(r["sloth"]) > float(r["lazy"])
              for r in rows), [(r["xp"], r["smart"]) for r in rows[:1]])
    # a gem column opens biggest-first like the rest of the numbers, and ranks the same zones
    grind.table._header_clicked(col("smart"))
    key, desc = grind.table.sort_state
    check("a gem column sorts, biggest first, on its own numbers",
          key == "smart" and desc is True
          and [float(r["smart"]) for r in grind.table.model_.rows]
          == sorted((float(r["smart"]) for r in grind.table.model_.rows), reverse=True),
          [r["smart"] for r in grind.table.model_.rows][:4])
    check("... in the same order as the base column, since the gems only scale it",
          [r["zone"] for r in grind.table.model_.rows]
          == [r["zone"] for r in sorted(grind.table.model_.rows, key=lambda r: -float(r["xp"]))])
    grind.table._header_clicked(col("xp"))                     # back to the base column

    # THE DATA'S OWN TYPO MUST NOT DECIDE THE RANKING. PYRAMID_F6 ships `GHOST_OCTO_1 minLevel 2725,
    # maxLevel 27` and the game rolls the wild level with `math.random(minLevel, maxLevel)`
    # (`classes.lists.EncounterZoneList.lu` line 115) - which LUA REFUSES for an empty interval, so
    # that entry can never produce a fight. The user: "PYRAMID_F6 ... Squidly L2725-27 14.3% 32237
    # xp ... this does not look plausible", and it was not: 32237 xp came from a level-2725 Coromon
    # and made that floor the best grind in the game. Now the entry is left out of the zone's level
    # and its XP, says so in the pane, and the zone reads as the L27-35 floor it is.
    broken = [z for z in all_zones if any(not rec["rollable"] for rec in z.encounters())]
    check("the game ships a zone with a reversed level range", bool(broken),
          [(z.name, max(rec["min"] for rec in z.encounters())) for z in broken][:2])
    check("... and the ranking flags it instead of trusting the level",
          all("odd levels" in grind._flags(z, z.encounters()) for z in broken),
          [grind._flags(z, z.encounters()) for z in broken][:2])
    if broken:
        zone = broken[0]
        bad = [rec for rec in zone.encounters() if not rec["rollable"]]
        check("... its broken entry is kept in the listing, with the game's own numbers",
              len(bad) == 1 and bad[0]["min"] > bad[0]["max"],
              [(rec["name"], rec["min"], rec["max"]) for rec in bad])
        check("... but it is out of the level and the XP: both stay inside the real spawns",
              zone.average_level <= max(rec["max"] for rec in zone.encounters() if rec["rollable"])
              and 0 < zone.xp_per_encounter(dex.xp_reward) < 1000 * max(rec["max"] for rec in zone.encounters()),
              "%s: L%.2f, %.0f xp/fight"
              % (zone.name, zone.average_level, zone.xp_per_encounter(dex.xp_reward)))
        grind.show_zone(zone)
        pump(app)
        pane = grind.species.toPlainText()

        def dashes(line):
            """The four priced columns of a line as they appear, i.e. "-" when there is no figure."""
            parts = line.split()
            if "(empty" not in parts:
                return None
            return parts[parts.index("(empty") - 4:parts.index("(empty")]

        check("... and the pane refuses to price it",
              "empty level range" in pane
              and any(dashes(ln) == ["-", "-", "-", "-"] for ln in pane.splitlines()),
              [ln for ln in pane.splitlines() if "(empty" in ln][:1])
        check("... while the fights that CAN roll keep their four figures",
              len(pane_rows(pane)) == len([r for r in zone.encounters() if r["rollable"]]),
              len(pane_rows(pane)))
        check("... while the zone's own total is the price of the fights that do happen",
              "%.0f xp / fight" % zone.xp_per_encounter(dex.xp_reward) in pane,
              pane.splitlines()[0])

    # the formula's own parts: linear in level, and a one-stage Coromon (place 1 of 1) is base/2
    solo = next((uid for uid in dex._XP_MONSTERS if dex._xp_lines()[uid] == (1, 1)), None)
    if solo is not None:
        mon = dex._XP_MONSTERS[solo]
        check("a Coromon that never evolves is worth base stats / 2 per level",
              abs(dex.xp_reward(solo, 40) - mon.base_stats * 40 / 2.0) < 0.5,
              "%d for %s (base %.1f)" % (dex.xp_reward(solo, 40), mon.name, mon.base_stats))
    deepest = max(dex._XP_MONSTERS, key=lambda uid: dex._xp_lines()[uid][0] / float(dex._xp_lines()[uid][1]))
    check("... it doubles with level",
          abs(dex.xp_reward(deepest, 40) - 2 * dex.xp_reward(deepest, 20)) <= 1,
          "%d at L40 vs %d at L20 (%s)"
          % (dex.xp_reward(deepest, 40), dex.xp_reward(deepest, 20), dex._XP_MONSTERS[deepest].name))
    # the place in the line is a real multiplier: the last stage of the longest line (three stages)
    # is worth more than the same family's base form at the same level
    longest = max(dex.lines(), key=lambda pair: len(pair[1]))
    first, last = longest[1][0], longest[1][-1]
    check("a later evolution is worth more than its base form",
          dex.xp_reward(last.uid, 20) > dex.xp_reward(first.uid, 20)
          and len(longest[1]) > 1,
          "%s %d vs %s %d at L20" % (last.name, dex.xp_reward(last.uid, 20),
                                     first.name, dex.xp_reward(first.uid, 20)))

    # and the top row really is the best place to grind, by the number in its own column
    key, desc = grind.table.sort_state
    check("the ranking is still the xp column, biggest first", key == "xp" and desc is True)
    xp_rows = [float(r["xp"]) for r in grind.table.model_.rows]
    check("... and its first row is the highest on the list", xp_rows[0] == max(xp_rows),
          "%.0f at %s" % (xp_rows[0], grind.table.model_.rows[0]["zone"]))
    grind.table._header_clicked(col("xp"))
    check("... and flips to smallest first", grind.table.sort_state[1] is False)
    grind.table._header_clicked(col("xp"))

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

    # ... AND THE ZONES WHOSE MAP DRAWS SOMETHING ELSE IN ANOTHER STORY MOMENT CAN BE FOUND, which is
    # what the "with story states" tick in the ranking's own control row is for: the states are a
    # property of the MAP (`base#condition` layers, see the map section), nothing in the ranking shows
    # them, and 23 of the 69 areas have maps like that. It narrows THE TABLE ONLY - no area's tick is
    # touched - and the answer for all 69 maps is read on a thread of its own (36 MB of map files; the
    # cold read cost 1.9 s of the window's 0.8 s build), so the ranking WAITS FOR IT rather than
    # blocking, and comes back through `refresh` when it lands. The tooltip counts it either way.
    while not grind.states_ready():
        QTest.qWait(50)
    check("the reader answers for every area without blocking the window",
          len(grind.states) == len(grind.maps) == 69 and sum(grind.states.values()) == 23,
          "%d of %d area(s) have maps with story states" % (sum(grind.states.values()),
                                                            len(grind.maps)))
    all_zones_shown = grind.table.model_.rowCount()
    before_ticked = len(grind.available)
    areas_visible = [grind.area_list.item(i).isHidden() for i in range(grind.area_list.count())]
    grind.states_tick.setChecked(True)
    pump(app)
    kept = sorted({row[PAYLOAD].map_file for row in grind.table.model_.rows})
    check("the ranking can show only the zones whose map has story states",
          0 < len(kept) < len(grind.maps) and all(grind.states[name] for name in kept)
          and grind.table.model_.rowCount() < all_zones_shown,
          "%d row(s) over %d area(s): %s" % (grind.table.model_.rowCount(), len(kept), kept[:4]))
    check("... which is the middle column's own row, not the area list's",
          grind.table.parentWidget() is grind.states_tick.parentWidget()
          and [grind.area_list.item(i).isHidden() for i in range(grind.area_list.count())]
          == areas_visible
          and len(grind.available) == before_ticked
          and all(grind.area_items[name].checkState() == Qt.CheckState.Checked
                  for name in grind.available),
          "%d of %d area(s) still shown" % (sum(1 for off in areas_visible if not off),
                                            len(areas_visible)))
    # THE TOOLTIP'S COUNT is written by a 250 ms main-thread poll (nothing is emitted from the
    # reader), so the harness waits the way the window does - one interval of the real event loop.
    QTest.qWait(300)
    check("... and the reader's own count is in the tick's tooltip",
          ("%d of the %d areas" % (sum(grind.states.values()), len(grind.maps)))
          in grind.states_tick.toolTip() and not grind._watch.isActive(),
          grind.states_tick.toolTip().splitlines()[-1])
    grind.states_tick.setChecked(False)
    pump(app)
    check("... and unticking it ranks every zone again",
          grind.table.model_.rowCount() == all_zones_shown
          and grind.prefs.get("with_states") is False,
          "%d row(s) -> %d" % (all_zones_shown, grind.table.model_.rowCount()))
    # AND THE SAME TICK ON AN AREA WITH NO STORY STATES SAYS SO, rather than looking like a bug: the
    # ranking's empty hint names the filter instead of the level, which is the other reason a table
    # can be empty.
    grind.set_all(False)
    grind.available = {"oasisCave_1"}
    grind._set_checked("oasisCave_1", True)
    grind.states_tick.setChecked(True)
    pump(app)
    check("... and says so when no ticked area has a map like that",
          grind.table.model_.rowCount() == 0
          and "story" in grind.species.toPlainText().lower()
          and not grind.states.get("oasisCave_1", True),
          grind.species.toPlainText().splitlines()[:1])
    grind.states_tick.setChecked(False)
    grind.set_all(True)
    pump(app)
    check("... and the whole ranking is back",
          grind.table.model_.rowCount() == all_zones_shown,
          grind.table.model_.rowCount())
    # THE CHEAP READER is the one the filter uses for all 69 maps at once - it reads the bytes for a
    # `"name": "...#..."` instead of parsing 36 MB of Tiled JSON - so it has to give the SAME answer
    # as the parser on every map there is.
    mismatch = [name for name in grind.maps
                if maptiles.has_states(name) != bool(
                    maptiles.variants(ez.map_parts(ez.find_map_file(name))[0]))]
    check("... read off the file, and the answer is the parsed one on every map", not mismatch,
          "%d map(s) disagree: %s" % (len(mismatch), mismatch[:4]))

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
        # THE EXPECTED LEVEL IS PER ENCOUNTER TOO, and that is what the crimsonite split has to
        # survive: both forms count (you meet a crimsonite while walking like anything else), but a
        # group's members no longer each carry the whole entry's share, which is what made this
        # column read 74.20 for WATERROUTE_4 against a real 54.33.
        ordinary_level = sum(r["share"] / 100.0 * (r["min"] + r["max"]) / 2.0
                             for r in mixed_zone.monsters.values())
        only_ordinary = sum(weight * mixed_zone.encounter_level(row)
                            for row, weight in mixed_zone.rollable_encounters()
                            if not row["crimsonite"])
        recomputed = sum(weight * mixed_zone.encounter_level(row)
                         for row, weight in mixed_zone.rollable_encounters())
        check("the expected level counts both forms, once per encounter",
              abs(mixed_zone.average_level - recomputed) < 1e-9
              and mixed_zone.average_level != only_ordinary,
              "%.2f (%.2f with only the ordinary spawns, %.2f if the per-species view is believed)"
              % (mixed_zone.average_level, only_ordinary, ordinary_level))
        check("... and it is the level of a Coromon the zone actually spawns",
              min(row["min"] for row in mixed_zone.encounters())
              <= mixed_zone.average_level
              <= max(row["max"] for row in mixed_zone.encounters()),
              "%.2f within L%s-%s"
              % (mixed_zone.average_level,
                 min(row["min"] for row in mixed_zone.encounters()),
                 max(row["max"] for row in mixed_zone.encounters())))
        grind.min_share.setValue(0)
        grind.show_zone(mixed_zone)
        pump(app)
        names = ["Crimsonite " + mixed_zone.species.get(uid, uid)
                 for uid in mixed_zone.crimsonite]
        text = grind.species.toPlainText()
        check("the zone's spawns pane names the crimsonite form",
              all(name in text for name in names)
              and text.lower().count("crimsonite") >= len(names), names)
        grind.show_zone(keep_zone)
        grind.min_share.setValue(keep_share)
        pump(app)

    # A GROUP ENCOUNTER IS ONE LINE NAMING ITS PARTY - the user: "it's sort of weird when showing the
    # info for groups of 3 spawning ... it says tripple battle, in this case it's Armadon and 2 Armodo,
    # but it doesn't say that, so the percentages are misleading". The percentages are the proof the fix
    # works: an encounter's share is the chance of meeting THAT fight, so a zone's lines add up to 100%.
    # The per-species view counted a repeated member once PER PARTY SLOT and therefore summed past it.
    group = next((zone for zone in all_zones
                  if any(row["battles"] > 1 for row in zone.encounters())), None)
    check("some zone rolls a group encounter", group is not None)
    if group is not None:
        grouped = [row for row in group.encounters() if row["battles"] > 1]
        check("a group encounter is one line that names its party",
              bool(grouped) and all(any(rec["count"] > 1 for rec in row["members"])
                                    or len(row["members"]) > 1 for row in grouped),
              [row["name"] for row in grouped][:2])
        shares = [row["share"] for row in group.encounters()]
        check("... and the zone's encounters add up to 100%",
              abs(sum(shares) - 100.0) < 0.05, sum(shares))
        check("... where the per-species view counts a repeated member twice",
              sum(row["share"] for row in group.slots()) > 100.0,
              sum(row["share"] for row in group.slots()))
        check("... and the pane draws those encounters, one line each",
              len(encounter_lines(group)) == len(group.encounters()),
              encounter_lines(group)[:2])
        # THE PANE HAS TO EXPLAIN THE RANKING. The column's claim is XP per fight, so the zone's own
        # pane repeats its total and then what each of its fights is worth - otherwise the number the
        # list is sorted by is a number with no visible source.
        grind.show_zone(group)
        pump(app)
        pane = grind.species.toPlainText()
        check("the spawns pane names the zone's own xp per fight",
              "%.0f xp / fight" % group.xp_per_encounter(dex.xp_reward) in pane,
              pane.splitlines()[0])
        # ... AND SAYS IT IS AN AVERAGE, NAMING THE BEST FIGHT. Without that the headline looks
        # wrong: WATERROUTE_4 reads 4840 while THREE of its five fights are worth more (6101, 4775,
        # 4485) - the user: "weird it still shows the data as WATERROUTE_4 ... 4840 xp / fight ...
        # 6101 xp". Both figures are recomputed here, and the pane's own lines have to agree with the
        # best it names.
        expected, best, count = group.xp_spread(dex.xp_reward)
        check("... and that the figure is the average over the fights, with the best of them named",
              "average of %d" % count in pane and "best %.0f" % best in pane
              and abs(expected - group.xp_per_encounter(dex.xp_reward)) < 0.5,
              pane.splitlines()[1])
        rich = pane_rows(pane)
        check("... and every listed fight's own row, group members counted",
              len(rich) == len([r for r in group.encounters() if r["rollable"]]),
              [r[0] for r in rich][:2])
        listed = [row[2][0] for row in rich]
        check("... with the best figure being one of those fights, not an average of its own",
              bool(listed) and abs(max(listed) - best) <= 1,
              "best %.0f vs the biggest line %s" % (best, max(listed) if listed else "none"))
        check("... the commonest fight on the first line, as the list is ordered",
              bool(rich) and rich[0][1] == "%.1f%%" % group.encounters()[0]["share"],
              rich[:1])
        # THE THREE GEM COLUMNS ARE THE FIGHT SCALED, in the pane as in the table - the user: "after
        # the base XP column, show a column showing how much the coromon holding the Smart Gem would
        # earn ... then Sloth gem ... and lazy gem".
        check("... and each row prices all three gems off its own fight",
              all(abs(row[2][1] - row[2][0] * 1.1) <= 2
                  and abs(row[2][2] - row[2][0] * 0.5) <= 2
                  and abs(row[2][3] - row[2][0] * 0.2) <= 2 for row in rich),
              [r[2] for r in rich][:2])
        check("... under a heading row that names all four columns",
              any(ln.split() == ["level", "share", "fight", "smart", "sloth", "lazy"]
                  for ln in pane.splitlines()), pane.splitlines()[2:3])
        grind.show_zone(keep_zone)          # leave the tab on the zone the run started with
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
    check("the right column stacks the list, the species breakdown and the map",
          database.right_split.orientation() == Qt.Orientation.Vertical
          and database.right_split.count() == 3
          and database.right_split.widget(0) is database.locations
          and database.right_split.widget(1) is database.species
          and database.right_split.widget(2).isAncestorOf(database.map),
          "%d child(ren)" % database.right_split.count())
    # THE SPECIES BREAKDOWN IS UNDER THE LIST, and it is the FIRST TAB'S OWN LINES - the user: "when
    # we pick a location from the list, right under the list it should also show a breakdown of what
    # else spawns there, same way as the first tab". So it is compared against that tab's pane rather
    # than against a formatter, because "the same" is the requirement: that tab draws a heading line
    # and a blank one, this pane draws neither (the selected row above it names the zone).
    database.select(database.lines[0][1][0])
    pump(app, 3)
    chosen = database.locations.current_payload()
    keep_share_pct = grind.min_share.value()
    grind.min_share.setValue(0)        # the two panes are only comparable unfiltered
    grind.show_zone(chosen)
    pump(app, 3)
    # THE HEADINGS ARE STRIPPED BY SHAPE, NOT BY COUNT: the spawn lines are the ones that start with
    # the listing's own two-space indent, so adding a line to the first tab's heading (the average and
    # best line did) cannot silently shift what this compares.
    first_tab = [line for line in grind.species.toPlainText().split("\n") if line.startswith("  ")]
    grind.min_share.setValue(keep_share_pct)
    here = [line for line in database.species.toPlainText().split("\n") if line.startswith("  ")]
    check("picking a location lists what else spawns there, exactly as the first tab does",
          bool(here) and here == first_tab, "%s vs %s" % (first_tab[:1], here[:1]))
    check("... with none of it scrolled out of sight",
          database.species.verticalScrollBar().maximum() == 0,
          "scroll range %d" % database.species.verticalScrollBar().maximum())
    # AND THE MAP IS DIRECTLY UNDER THE LAST OF THEM, not half the column: both panes get their own
    # content height and the map takes the rest - the user: "the map section should be directly below
    # the last line in the list, so we can fit a taller map if needed".
    sizes = database.right_split.sizes()
    check("the list and the breakdown are only as tall as their own content",
          abs(sizes[0] - database.locations.content_height()) <= 2
          and abs(sizes[1] - mono_height(database.species, database.species.toPlainText())) <= 2,
          "list %d/%d px, species %d/%d px"
          % (sizes[0], database.locations.content_height(), sizes[1],
             mono_height(database.species, database.species.toPlainText())))
    check("the map takes the rest of the column", sizes[2] > sizes[0] * 2,
          "list %d, species %d, map %d" % tuple(sizes))

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

    # THE FINISHED LINES CAN BE HIDDEN, and the checkbox keeps its own state - the user: "add a checkbox
    # next to search bar that would hide complete rows, where we have caught all variants. its state
    # needs to persistant". A line is complete when every Coromon in it is caught in all three potential
    # categories, which is what the tab's own predicate says (`_complete`) - so this checks that the
    # FILTER respects it, and that what the save says about completeness is what hides the rows.
    def listed():
        return {index for index in range(len(database.lines))
                if any(cell[0].parentWidget().isVisible()
                       for key, cell in database.cells.items() if key[0] == index)}

    finished = [index for index in range(len(database.lines)) if database._complete(index)]
    check("nothing is hidden while the box is unticked",
          listed() == set(range(len(database.lines))),
          "%d of %d lines visible, %d of them complete"
          % (len(listed()), len(database.lines), len(finished)))
    database.hide_complete.setChecked(True)
    pump(app, 2)
    hidden = listed()
    if finished:
        check("ticking it hides exactly the complete lines",
              hidden == set(range(len(database.lines))) - set(finished),
              "%d visible, %d complete" % (len(hidden), len(finished)))
    else:
        print("note no line is complete in this save, so the box had nothing to hide")
    check("the box is remembered",
          prefs.get(config.HIDE_COMPLETE_KEY) is True
          and state.load_prefs().get(config.HIDE_COMPLETE_KEY) is True,
          prefs.get(config.HIDE_COMPLETE_KEY))
    # ... AND IT IS THE SAVE THAT DECIDES, so reading the record again re-applies it rather than leaving
    # rows that have just become complete on screen.
    database.reload_button.click()
    pump(app, 3)
    check("a reload keeps the finished lines hidden", listed() == hidden,
          "%d vs %d visible" % (len(listed()), len(hidden)))
    database.hide_complete.setChecked(False)
    pump(app, 2)
    check("unticking it brings every line back",
          listed() == set(range(len(database.lines))), len(listed()))

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
    row = [database.search, database.counts, database.saved_label, database.reload_button,
           database.hide_complete]
    check("the search field is capped, not stretched",
          database.search.maximumWidth() < 300, database.search.maximumWidth())
    # A WRAPPED ROW IS TWO LINES, and that is what this looks for: the widgets' CENTRES on one line, and
    # nothing taller than a normal control (a wrapped label would be about 48 px). Comparing the heights
    # instead was fine while the row held only fields and buttons - a checkbox is legitimately 14 px next
    # to a 24 px field, which is shorter, not wrapped.
    heights = [widget.geometry().height() for widget in row]
    centres = [widget.geometry().center().y() for widget in row]
    check("the top row is a single line",
          max(centres) - min(centres) <= 8 and max(heights) <= 26,
          "heights %s, centres %s" % (heights, centres))
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

    # A PLACEHOLDER IS NOT A NAME, and eight of the game's area names ARE placeholders - the game
    # substitutes them while it draws, so reading one out of the localisation and printing it showed
    # the raw key. Measured in the archive: `[monster <UID>]` (`pyramid` is "Pyramid of
    # [monster TITAN_SAND]" and the game draws "Pyramid of Sart"), `[world.map.<key>.name]` and
    # `[map <file>]`. `mapnames.resolve` fills all three in, and the tabs must never show a bracket.
    pool = {mn.NAME_BY_KEY[k] for k in mn.NAME_BY_KEY} | {mn.area(z.map_file) for z in all_zones}
    leftover = sorted(name for name in pool if "[" in mn.resolve(name))
    check("no area name is left as a raw placeholder", not leftover, leftover[:3])
    check("a placeholder naming a Coromon resolves to that Coromon's name",
          mn.area("pyramid_f6") == "Pyramid of Sart F6"
          and "TITAN_SAND" not in mn.area("pyramid_f6"), mn.area("pyramid_f6"))
    check("... and one naming a map resolves to that map's area",
          mn.resolve(mn.NAME_BY_KEY["iceCave"]) == mn.NAME_BY_KEY["frozenCave"]
          and "[" not in mn.resolve(mn.NAME_BY_KEY["iceRoute"]),
          "%s / %s" % (mn.resolve(mn.NAME_BY_KEY["iceCave"]),
                       mn.resolve(mn.NAME_BY_KEY["iceRoute"])))
    check("... including a name that is nothing but a placeholder",
          mn.NAME_BY_KEY["ghostTown_temple"].count("[") == 1
          and mn.resolve(mn.NAME_BY_KEY["ghostTown_temple"]).startswith("Monastery of "),
          mn.resolve(mn.NAME_BY_KEY["ghostTown_temple"]))

    # THE LIST OPENS ON THE BIGGEST SHARE FIRST, and on the NUMBER in that column rather than its
    # text - the user: "by default the list of spawn locations there for a coromon should be ordered
    # descending by the last column, the percentages by default. I see that right now there is a bug
    # that they are ordered alphabetically, so descending sorts 9% before 12%".
    shares = [float(row["share"].rstrip("%")) for row in database.locations.model_.rows]
    check("the locations open sorted by share, biggest first",
          shares == sorted(shares, reverse=True), shares)
    # ... AND IT IS THE DEFAULT FOR EVERY COROMON, which is the check that would actually catch the
    # regression: it walks the first rows of the grid and insists every list comes up non-increasing.
    offenders = []
    for line_index in range(min(8, len(database.lines))):
        database.select(database.lines[line_index][1][0])
        pump(app)
        rows = [float(row["share"].rstrip("%")) for row in database.locations.model_.rows]
        if rows != sorted(rows, reverse=True):
            offenders.append(rows)
    check("... and that is the default for every Coromon, not just the one clicked",
          not offenders, offenders)
    # BACK TO BUZZLET, so the two checks below are about the rows the check above measured.
    QTest.mouseClick(database.cells[buzz[0]][0], Qt.MouseButton.LeftButton)
    pump(app, 2)
    # ... and BOTH directions rank them as numbers, which is checked on the helper and not on this
    # list: every share Buzzlet has is two digits, so a text sort would agree with a numeric one and
    # the bug would hide. "-" is a status skill's or a hidden zone's empty cell, and the non-numbers
    # stay last whichever way round the sort is.
    mixed = [{"share": "9.0%"}, {"share": "12.3%"}, {"share": "100.0%"}, {"share": "-"}]
    check("... ranked as numbers, not as text, in both directions",
          [row["share"] for row in sort_rows(mixed, "share", True)]
          == ["100.0%", "12.3%", "9.0%", "-"]
          and [row["share"] for row in sort_rows(mixed, "share", False)]
          == ["9.0%", "12.3%", "100.0%", "-"],
          [row["share"] for row in sort_rows(mixed, "share", False)])
    # THE HEADING STILL TOGGLES IT, and the FIRST click is the biggest-first direction (the column's
    # own `desc_first`, not a guess from the values).
    database.locations._header_clicked(3)
    key, desc = database.locations.sort_state
    rising = [float(row["share"].rstrip("%")) for row in database.locations.model_.rows]
    check("the share heading toggles to smallest first",
          key == "share" and desc is False and rising == sorted(rising), rising)
    database.locations._header_clicked(1)      # a different column, then back to share
    database.locations._header_clicked(3)
    key, desc = database.locations.sort_state
    check("... and the first click on it is the biggest-first one",
          key == "share" and desc is True, (key, desc))

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

    # ---------------------------------------------------------------- the separator beside a table
    # THE USER'S OWN RULE, and it is one rule for every tab that has a table beside a separator: "the
    # slider/separator between left and right panes is not dynamic, the last column of the tables is as
    # long as the separator is set, instead, the tables should be as narrow as possible and the
    # separator should be static based on that". So, per tab: no column is stretched to fill a pane,
    # every column is only as wide as the text it is holding, and the pane is the width of the columns
    # and nothing else - which is what puts the separator just past the last column.
    for name, tab in (("grind", window.grind), ("database", window.database),
                      ("missing", window.missing), ("items", window.items)):
        window.tabs.setCurrentWidget(tab)
        pump(app, 5)
        fit = tab.pane_fit
        pane = fit.splitter.widget(fit.pane)
        check("%s: the separator is only as far out as the content in the pane" % name,
              pane.width() <= fit.fitted_width() + 1 and fit.elastic != fit.pane,
              "pane %d vs content %d px, elastic %d"
              % (pane.width(), fit.fitted_width(), fit.elastic))
        # ... AND NO COLUMN OF THE TABLE BESIDE IT IS STRETCHED, which is what the last column used to
        # be: given whatever width the pane had left over instead of the width of its own text.
        table = tab.findChild(table_mod.DataTable)
        header = table.horizontalHeader()
        asked = [max(table.sizeHintForColumn(i), header.sectionSizeHint(i)) + table_mod.COLUMN_PAD
                 for i in range(table.model_.columnCount())]
        check("... and no column of its table is stretched or wider than its own text",
              not header.stretchLastSection()
              and all(header.sectionSize(i) <= asked[i]
                      and header.sectionSize(i) <= column.width
                      for i, column in enumerate(table.model_.columns)),
              "columns %s of %s asked" % (
                  [header.sectionSize(i) for i in range(table.model_.columnCount())],
                  [round(value) for value in asked]))

    # THE DATABASE TAB'S MAP IS THE PANE THAT GROWS. Its grid is measured (its own columns, at the dex
    # scale in use) and everything else is the map's column - the user: "the database view also has a
    # gap before the separator making the map small", which was the panel being given the LOCATIONS
    # LIST's width (300 px) while the grid's spare thousand pixels sat empty beside it.
    window.tabs.setCurrentIndex(1)
    pump(app, 6)
    fit = window.database.pane_fit
    check("the database tab measures the grid, and the map column takes the rest",
          fit.pane == 0 and fit.elastic == 1
          and window.database.split.widget(0) is window.database.scroll
          and window.database.split.widget(1).isAncestorOf(window.database.map),
          "pane %d, elastic %d" % (fit.pane, fit.elastic))
    grid_want = window.database.holder.sizeHint().width()
    grid_pane = window.database.scroll.width()
    check("... and the grid column is never wider than the grid itself",
          grid_pane <= fit.fitted_width() + 1
          and fit.want() >= grid_want,
          "grid %d px in a %d px pane (want %d)" % (grid_want, grid_pane, fit.want()))

    # ... AND THE SEPARATOR FOLLOWS THE SPRITE SCALE - the user: "when I change the scale of coromon
    # sprites, the separator should auto adjust". It did not, and the reason is worth a check of its
    # own: the grid's `sizeHint` still reports the PREVIOUS scale until its layout has run again
    # (MEASURED at 1897 px for a grid just scaled down to 911), so the fit is asked for one turn of the
    # event loop later (`FittedPane.refit_later`) rather than in the same call. A WIDE window is used
    # so the grid really is the thing setting the width - in a narrow one both scales are capped by
    # what the map column may not lose, and the check would pass either way.
    wide = window.width()
    window.resize(2600, 560)
    pump(app, 6)
    database.set_zoom(config.ICON_ZOOM_MAX)
    pump(app, 8)
    big_grid = database.holder.sizeHint().width()
    big_want, big_pane = database.pane_fit.fitted_width(), database.scroll.width()
    database.set_zoom(config.ICON_ZOOM_MIN)
    pump(app, 8)
    small_grid = database.holder.sizeHint().width()
    small_want, small_pane = database.pane_fit.fitted_width(), database.scroll.width()
    check("... and it moves when the coromon sprite scale does",
          big_grid > small_grid and big_pane > small_pane
          and abs(big_pane - big_want) <= 1 and abs(small_pane - small_want) <= 1,
          "grid %d -> %d px, column %d -> %d px (fitted %d -> %d)"
          % (big_grid, small_grid, big_pane, small_pane, big_want, small_want))
    database.set_zoom(start)
    window.resize(wide, 560)
    pump(app, 8)

    # ... AND IT STAYS THERE. A wider window must widen the pane BESIDE the table, because a separator
    # that follows the window is the thing being complained about - the columns would grow with it.
    #
    # THE WIDTHS ARE READ OFF THE PANES, not out of `splitter.sizes()`: those are the sizes the
    # SPLITTER was last SET to, and they lag a layout pass - MEASURED here as [844, 556] summing to 200
    # px more than the splitter is wide, and as [0, 0] on a splitter that is 1020 px across. The panes
    # are what the user sees.
    window.tabs.setCurrentIndex(3)
    pump(app, 6)
    fit = window.items.pane_fit
    panes = lambda: [fit.splitter.widget(i).width() for i in range(fit.splitter.count())]  # noqa: E731
    # THE WINDOW IS NOT 1040 px WIDE BY NOW (the Database tab's dex zoom grew its own minimum and took
    # the window with it), so the size to come back to is read rather than assumed.
    width, before = window.width(), panes()
    window.resize(width + 200, 560)
    pump(app, 8)
    after = panes()
    check("a wider window widens the pane beside the table, not the table",
          after[fit.pane] == before[fit.pane] and after[fit.elastic] > before[fit.elastic],
          "window %d -> %d, %s -> %s" % (width, window.width(), before, after))
    window.resize(width, 560)
    pump(app, 8)
    check("... and it goes back when the window does", panes() == before,
          "window %d, %s vs %s" % (window.width(), panes(), before))

    # ---------------------------------------------------------------- missing tab
    # THE DATABASE TAB'S COMPANION: the zones ordered by how many GROUPS are still missing there. The
    # grouping is the user's own rule - "if we have at least one of the evolutions of the 4 groups
    # caught it counts as having that kind of coromon already captured, because transition between
    # evolution levels is trivial ... if we are missing all 3 of a potential level that counts as
    # missing and if we have one that means caught". Counts that depend on the save are always
    # RECOMPUTED here rather than remembered, because the save moves under this test.
    window.tabs.setCurrentIndex(2)
    pump(app, 6)
    miss = window.missing
    # THE SAME RECORD THE DATABASE TAB READ, taken from it rather than read again, so the two tabs are
    # provably looking at one moment - and every save-dependent number below is recomputed from it.
    miss_owned, miss_skins = database.owned, database.skins
    # THE FACTS, not the label text: the Database tab elides its label to fit a row full of controls
    # ("save: saveslot_…"), so comparing the two strings would test the eliding, not the save.
    check("the missing tab read the same save as the database",
          (miss.slot, miss.saved) == (database.slot, database.saved),
          "missing %s/%s, database %s/%s" % (miss.slot, miss.saved, database.slot, database.saved))
    check("the ranking opens on the most lines to catch, biggest first",
          miss.table.sort_state == ("lines", True), miss.table.sort_state)
    check("... and it lists the zones that can fill something",
          miss.table.model_.rowCount() > 0, miss.table.model_.rowCount())

    # THE GROUPS THEMSELVES: four per line at most, and a group counts as caught when ANY stage is.
    lines_now = dex.lines()
    check("a line makes up to four groups, one per kind",
          len(miss.groups_) == 3 * len(lines_now) + len({g.family for g in miss.groups_
                                                         if g.kind == dex.CRIMSONITE}),
          "%d groups over %d lines" % (len(miss.groups_), len(lines_now)))
    check("... the crimsonite group only for the lines that have a form",
          all(g.kind != dex.CRIMSONITE or g.skin == dex.CRIMSONITE for g in miss.groups_)
          and len({g.family for g in miss.groups_ if g.kind == dex.CRIMSONITE}) > 0)
    # ... AND EVERY STAGE OF THE LINE, which is the rule the user settled on last: "if we are missing
    # any member of a line - that counts as one slot discard what I said about evolutions being trivial
    # to get". Cross-checked against `savefile.categories`, the other reader of the same record.
    check("... and a group is caught only when EVERY stage of the line has it",
          all(g.caught == all(g.kind in savefile.categories(mon.uid, miss_owned) for mon in g.members)
              for g in miss.groups_ if g.kind != dex.CRIMSONITE),
          "%d caught of %d" % (sum(1 for g in miss.groups_ if g.caught), len(miss.groups_)))
    check("... the crimsonite group being the line's skin, not a dex entry",
          all(g.caught == savefile.has_skin(miss_skins, g.family, dex.CRIMSONITE)
              for g in miss.groups_ if g.kind == dex.CRIMSONITE))
    check("the tab's own reading of the save agrees with savefile's",
          all(missing_data._owns(miss_owned, uid, kind)
              == (kind in savefile.categories(uid, miss_owned))
              for uid in list(miss_owned)[:40] for kind in ("A", "B", "C")),
          "checked %d uid(s)" % min(40, len(miss_owned)))

    # THE ROWS: the count in the "to catch" column is the number of distinct LINES with something
    # missing that spawn there - the user changed it from a per-group count: "right now we count 1
    # potent missing and 1 perfect missing etc of the same evolutionary line as 2 missing - weight 2,
    # it should rather count as 1 as one member".
    # A ZONE WITH NOTHING LEFT IS STILL LISTED, sorted to the bottom rather than hidden: it is the
    # answer to "where is there nothing for me", which is worth having when the ones above it are all
    # far away. The count is what orders the list.
    check("the rows that are done sit at the bottom",
          [bool(r["missing"]) for r in miss.rows]
          == sorted((bool(r["missing"]) for r in miss.rows), reverse=True),
          [len(r["missing_lines"]) for r in miss.rows][-4:])
    check("... sorted by the lines to catch, and nothing is missing outside the zone's own groups",
          [len(r["missing_lines"]) for r in miss.rows]
          == sorted((len(r["missing_lines"]) for r in miss.rows), reverse=True)
          and all(set(r["missing"]) <= set(r["groups"]) for r in miss.rows))
    check("... a line's several missing kinds counted ONCE, which is the whole change",
          all(len(r["missing_lines"]) <= len(r["missing"]) for r in miss.rows)
          and any(len(r["missing_lines"]) < len(r["missing"]) for r in miss.rows),
          [(len(r["missing_lines"]), len(r["missing"])) for r in miss.rows[:3]])
    check("... and a line is in that count only when it really has something missing",
          all(len({g.family for g in r["missing"]}) == len(r["missing_lines"]) for r in miss.rows)
          and all(set(r["missing_lines"]) <= set(r["hosted_lines"]) for r in miss.rows))
    check("... the table shows those counts, not the group counts, in \"to catch\" and \"of\"",
          [r["lines"] for r in miss.table.model_.rows]
          == ["%d" % len(r["missing_lines"]) for r in miss.rows]
          and [r["of"] for r in miss.table.model_.rows]
          == ["%d" % len(r["hosted_lines"]) for r in miss.rows],
          [(r["lines"], r["of"]) for r in miss.table.model_.rows[:3]])
    sample = miss.rows[0]
    hosted = {uid for uid in sample["zone"].monsters} | set(sample["zone"].crimsonite)
    families = {missing_data.family_of().get(uid) for uid in hosted}
    check("... the top row's groups are exactly the missing ones of the lines that spawn there",
          {g.family for g in sample["missing"]} <= families,
          "%d line(s) / %d group(s) missing, lines %s"
          % (len(sample["missing_lines"]), len(sample["missing"]),
             sorted(f for f in families if f)))
    zone_names = [r["zone"].name for r in miss.rows]
    check("... a zone is listed once", len(zone_names) == len(set(zone_names)))
    check("... every zone in the list really spawns a line a group belongs to",
          all(any(uid in missing_data.family_of() for uid in list(r["zone"].monsters)
                  + list(r["zone"].crimsonite)) for r in miss.rows))

    # THE PANE LISTS THE MISSING COROMON ONE BY ONE - the user: "it doesn't list Fibio, which I don't
    # have, but it spawns there", then "I said that if we are missing a member of line - it should
    # still be listed". The COUNT is still per line (the table's "to catch"), but every Coromon that is
    # short anywhere in the picked zone gets its own row, including the ones with no slot of their own
    # here (they are reachable by evolving a stage that does spawn).
    miss.table.selectRow(0)
    pump(app, 3)
    pane = miss.detail.toPlainText()
    listed = missing_data.members_missing(sample["zone"], sample["missing"])
    check("the pane names the selected zone and how many Coromon are to catch",
          sample["zone"].name in pane and "%d line(s) short \u00b7 %d Coromon to catch"
          % (len(sample["missing_lines"]), len(listed)) in pane, pane.splitlines()[:2])
    named = [line for line in pane.splitlines()
             if line.startswith("  ") and line.strip() and line.split()[0] != "Coromon"]
    check("... with one row per missing Coromon", len(named) == len(listed),
          "%d row(s) for %d Coromon" % (len(named), len(listed)))
    check("... under a heading row of its own",
          [line.split() for line in pane.splitlines() if "needs" in line]
          == [["Coromon", "needs", "line", "how", "levels", "share"]], pane.splitlines()[2:3])
    check("... every one of them named, in the model's own order",
          [line.split()[0] for line in named] == [entry["member"].name for entry in listed],
          [line.split()[0] for line in named][:3])
    row_of = {line.split()[0]: line for line in named}
    check("... each naming the line it belongs to and every kind it needs",
          all(entry["line"] in row_of[entry["member"].name]
              and all(missing_tab.SHORT[kind] in row_of[entry["member"].name]
                      for kind in entry["kinds"]) for entry in listed), named[:2])
    # ... INCLUDING THE ONES THAT DO NOT SPAWN HERE, which is the whole point of "still listed": a
    # Coromon with no slot of its own is marked `evolve` (catch the kind on a stage that IS here and
    # evolve it), and one with a slot shows the levels and share of that slot.
    evolved = [entry for entry in listed if not entry["odds"]]
    check("... with the ones that do not spawn here marked as evolved, not hidden",
          len(evolved) == len([line for line in named if line.rstrip().endswith("-")]),
          "%d evolve-only row(s)" % len(evolved))
    check("... and the ones that do spawn here priced off their own slot",
          all("L%s-%s" % entry["odds"][:2] in row_of[entry["member"].name]
              and "%.1f%%" % entry["odds"][2] in row_of[entry["member"].name]
              for entry in listed if entry["odds"]), listed[0]["member"].name)
    # THE CASE THE USER REPORTED, found rather than assumed: a Coromon whose name is NOT its line's
    # base form (Fibio in the Taddle line) has to be printed by name.
    non_base = [entry for entry in listed if entry["member"].name != entry["line"]]
    check("... including a stage that is not the line's base form", bool(non_base),
          [(entry["member"].name, entry["line"]) for entry in non_base][:2])
    check("... the Fibio case: named on its own row under its line",
          all(entry["member"].name in row_of[entry["member"].name] for entry in non_base),
          [(entry["member"].name, entry["line"]) for entry in non_base][:2])

    # THE RIGHT HALF IS THE MAP, the user: "the right half of the missing tab should also show the map
    # with the zone highlighted like the other tabs". It is the same `ZoneMap` widget, so the picked
    # zone is the solid patch here exactly as on the first two tabs, and the caption appears only when
    # the map could NOT draw the zone (`set_zone` returning False).
    check("the tab is two columns, the ranking and pane beside the map",
          miss.split.count() == 2
          and miss.split.widget(0).findChild(type(miss.table)) is miss.table
          and miss.split.widget(0).findChild(type(miss.detail)) is miss.detail
          and miss.split.widget(1).findChild(type(miss.map)) is miss.map,
          "%d column(s)" % miss.split.count())
    check("neither column can be dragged shut", not miss.split.childrenCollapsible())
    check("picking a row draws that zone on the map",
          miss.map.zone is not None and miss.map.zone.name == sample["zone"].name
          and not miss.map_head.isVisible(), getattr(miss.map.zone, "name", None))
    check("... with the legend the map itself planned",
          miss.legend_box.count() == len(miss.map.legend) + 1 and bool(miss.map.legend),
          "%d chip(s), %d plan entry(ies)" % (miss.legend_box.count() - 1, len(miss.map.legend)))
    # BOTH FOLLOW THE ROW, from the one place that knows it - walking a few rows and comparing.
    mismatched = []
    for row in miss.rows[:5]:
        miss.show_zone(row["zone"])
        if miss.map.zone is not row["zone"]:
            mismatched.append(row["zone"].name)
    check("... and the pane and the map are always the same zone", not mismatched, mismatched)
    miss.table.selectRow(0)
    pump(app)
    check("the map is big enough to be worth having",
          miss.map.minimumWidth() == 240 and miss.map.minimumHeight() == 180
          and miss.map.width() >= 240, "%dx%d" % (miss.map.width(), miss.map.height()))
    check("... and the kinds in the rank column add up to the groups the model counts",
          all(not r["kinds"] or sum(int(part.split()[-1]) for part in r["kinds"].split("\u00b7"))
              == len(model_row["missing"])
              for r, model_row in zip(miss.table.model_.rows, miss.rows)),
          miss.table.model_.rows[0]["kinds"])
    check("the tab's summary counts the groups the same way",
          "%d line(s) short, %d group(s) left" % (
              len({g.family for g in miss.groups_ if not g.caught}),
              sum(1 for g in miss.groups_ if not g.caught)) in miss.summary.text(),
          miss.summary.text())
    # ... AND A ZONE WITH NOTHING LEFT SAYS SO, rather than an empty pane that reads as a bug.
    caught_one = next((r for r in miss.rows if not r["missing"]), None)
    if caught_one is not None:
        check("a zone with everything caught says so",
              "already caught" in miss.detail_text(caught_one["zone"]),
              miss.detail_text(caught_one["zone"]).splitlines()[:2])
    # the gesture the Database tab has: a double click hands the zone to the first tab
    handed = []
    miss.zoneChosen.connect(handed.append)
    miss._jump(miss.table.model_.index(0, 0))
    check("double clicking a row hands its zone to the ranking",
          bool(handed) and getattr(handed[-1], "name", None) == sample["zone"].name,
          getattr(handed[-1], "name", None))
    window.tabs.setCurrentIndex(2)

    # ---------------------------------------------------------------- items tab
    # THE STATS HERE ARE THE GAME'S OWN, read out of its Lua, because the item JSON has no stat field
    # at all (see `items.py`). So these are the numbers the game charges and rolls with, checked
    # against the Spinner line - which is also where the awkward cases live: Platinum overrides the
    # shake count instead of a modifier, and the Dream Spinner's modifier is conditional.
    window.tabs.setCurrentIndex(3)
    pump(app, 3)
    items_tab = window.items
    check("every item record is listed",
          items_tab.table.model_.rowCount() == len(items_tab.all_items),
          items_tab.table.model_.rowCount())
    check("the category filter lists the game's categories",
          items_tab.category.count() == len(item_data.categories(items_tab.all_items)) + 1,
          items_tab.category.count())
    spinners = {item.uid: item for item in items_tab.all_items if item.category == "spinner"}
    check("all 17 spinners are there", len(spinners) == 17, len(spinners))
    for uid, modifier, cost, sell in (("SPINNER_REGULAR_1", 1.0, 200.0, 100.0),
                                      ("SPINNER_REGULAR_2", 1.5, 600.0, 300.0),
                                      ("SPINNER_REGULAR_3", 2.0, 1200.0, 600.0)):
        stats = spinners[uid].stats()
        check("%s: modifier, cost and sell price read off the game" % uid,
              stats.get("getCatchRateModifier") == [modifier]
              and stats.get("getGoldCost") == [cost]
              and stats.get("getGoldSellPrice") == [sell], stats)
    platinum = spinners["SPINNER_REGULAR_4"].stats()
    check("Platinum Spinner has no modifier - it overrides the shake count instead",
          not platinum.get("getCatchRateModifier")
          and platinum.get("getAmountOfShakes") == [5.0], platinum.get("getAmountOfShakes"))
    check("the base catch rates are the game's own rarity table",
          item_data.base_catch_rates() == {"common": 0.375, "uncommon": 0.3, "rare": 0.225,
                                           "legendary": 0.1}, item_data.base_catch_rates())
    spinning = item_data.spinner_sprites("SPINNER_REGULAR_2")
    check("a spinner has the thrown and the spinning sheet, each cut into frames",
          item_data.strip(spinning["spinning"]) == (14, 35)
          and item_data.strip(spinning["throw"]) == (57, 215),
          {key: item_data.strip(one) for key, one in spinning.items()})
    gauntlets = [item for item in items_tab.all_items if item.category == "gauntlet"]
    worn = [item for item in gauntlets if len(item_data.gauntlet_parts(item.uid)) == 2]
    check("all 18 gauntlet skins have both worn sprites",
          len(worn) == len(gauntlets) == 18, "%d of %d" % (len(worn), len(gauntlets)))
    check("every spinner has a bag icon",
          all(item.icon for item in spinners.values()),
          [item.uid for item in spinners.values() if not item.icon])
    items_tab.category.setCurrentIndex(items_tab.category.findData("spinner"))
    pump(app, 2)
    check("filtering to spinners lists exactly them",
          items_tab.table.model_.rowCount() == len(spinners), items_tab.table.model_.rowCount())
    items_tab.table.selectRow(1)
    pump(app, 2)
    shown = items_tab.table.current_payload()
    check("picking one shows its icon, its stats and its pictures",
          shown is not None and not items_tab.icon.pixmap().isNull()
          and items_tab.sprites.count() == 2 and "catch modifier" in items_tab.stats.toPlainText(),
          "%s: %d sprite(s)" % (getattr(shown, "uid", None), items_tab.sprites.count()))
    items_tab.search.setText("zzz-nothing")
    pump(app, 1)
    check("the item find box empties the list", items_tab.table.model_.rowCount() == 0)
    items_tab.search.setText("")
    items_tab.category.setCurrentIndex(0)
    pump(app, 1)

    # THE FIRST COLUMN CARRIES THE EXTRACTED ICON - the user: "the first column has to be an extracted
    # icon tho" - and it is drawn IN the name column rather than in a column of its own, which is what
    # `icon_column="name"` is for. EVERY ICON IS PADDED INTO ONE BOX, because a QTableView has a single
    # icon size for all its cells and Qt would otherwise rescale 16x16 and 20x16 pixel art to fit.
    model = items_tab.table.model_
    filled = [index for index, row in enumerate(model.rows) if row.get(ICON) is not None]
    boxes = {model.rows[index][ICON].size() for index in filled}
    first = filled[0] if filled else -1
    check("the name column draws the item's icon, and no other column does",
          bool(filled)
          and model.data(model.index(first, 0), Qt.ItemDataRole.DecorationRole) is not None
          and model.data(model.index(first, 1), Qt.ItemDataRole.DecorationRole) is None,
          "%d row(s) with an icon" % len(filled))
    check("every list icon is drawn in the one box the table was sized for",
          len(boxes) == 1 and boxes == {items_tab.box}, boxes)
    check("the rows are tall enough for that box",
          items_tab.table.verticalHeader().sectionSize(0) >= items_tab.box.height(),
          "%d px rows, %d px icon" % (items_tab.table.verticalHeader().sectionSize(0),
                                      items_tab.box.height()))
    check("an item the game has no artwork for still draws its name",
          any(row.get(ICON) is None for row in model.rows)
          and model.data(model.index(next(index for index, row in enumerate(model.rows)
                                          if row.get(ICON) is None), 0),
                         Qt.ItemDataRole.DisplayRole),
          "%d of %d rows have no icon"
          % (sum(1 for row in model.rows if row.get(ICON) is None), len(model.rows)))

    # ---------------------------------------------------------------- skills tab
    window.tabs.setCurrentIndex(4)
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

    # ---------------------------------------------------------------- potential tab
    window.tabs.setCurrentIndex(5)
    pump(app, 3)
    pot = window.potential
    # THE FOUR TABLES ARE THE GAME'S OWN NUMBERS, so their totals are still the ones the game rolls
    # against: 3863 was RECORDED off the running game (a `table.rollKey` call inside it) before this
    # tab existed, which is what pins the rest of the table to reality.
    check("the four potential tables still total what the game's own tables total",
          [potential.total(count) for count in sorted(potential.TABLES)]
          == [3194, 3415, 3863, 4567],
          [potential.total(count) for count in sorted(potential.TABLES)])
    check("... and a speed-up never touches the tail (17-21), which is why it dilutes them",
          all(potential.weights(count)[16:] == potential.weights(0)[16:]
              for count in sorted(potential.TABLES)),
          [potential.weights(3)[level - 1] for level in (1, 5, 17, 21)])

    # THE SAME NUMBERS AS EXACT FRACTIONS, the way the wiki writes its odds tables: one pick of
    # potential 9 is `320/3194`, the perfect weight is `1/3194`, and with the scent's THREE picks the
    # denominator is 3194 ** 3 = 32584025384 - which is the very number the wiki's own scent column
    # shows, so the two agree on the shape of the arithmetic as well as on the weights.
    check("... and the wiki's own exact fractions fall out of the same weights",
          potential.fraction(0, 1, 9) == (320, 3194)
          and potential.fraction(0, 1, 21) == (1, 3194)
          and potential.fraction(0, 3, 21)[1] == 3194 ** 3 == 32584025384,
          "one pick of 9: %d/%d, three picks of 21: %d/%d"
          % (potential.fraction(0, 1, 9) + potential.fraction(0, 3, 21)))
    check("... which are the model's own chances, exactly",
          all(abs(numerator / denominator - potential.chances(count, picks)[level - 1]) < 1e-12
              for count in sorted(potential.TABLES) for picks in (1, 3)
              for level, (numerator, denominator) in
              ((level, potential.fraction(count, picks, level)) for level in (1, 9, 19, 21))))

    # THE POTENTIFLATOR - the machine Oleg runs - IS THE SAME ROLL TAKEN TO 21. `Monster:
    # rerollPotential` takes the best of `amountOfRerollsByPotentialValue[potential]` FULL
    # `rollPotential()` draws and adds the Coromon's own potential, so it can never come out lower, and
    # a 20 or 21 is 21 with no draw at all. THE DRAW COUNTS ARE THE WIKI'S OWN NUMERATOR COLUMN
    # (`3/3194` ... `19/3194`, then `30/3194` and `60/3194`, and `3194/3194` at 20), which is what
    # makes the tab checkable against it - as does its fraction being `draws / total`.
    check("the Potentiflator's draws are the wiki's own numerator column",
          potential.REROLL_DRAWS == tuple(list(range(3, 20)) + [30, 60])
          and potential.reroll_draws(1) == 3 and potential.reroll_draws(19) == 60
          and potential.reroll_draws(20) is None,
          [potential.reroll_draws(level) for level in (1, 17, 18, 19, 20)])
    check("... and its fraction is the draws over the weight of 21, unsimplified like the wiki's",
          potential.reroll_fraction(19, 0) == "60/3194"
          and potential.reroll_fraction(20, 0) == "3194/3194"
          and potential.reroll_fraction(19, 2) == "60/3863",
          potential.reroll_fraction(19, 0))
    # THE EXACT CHANCE IS THE WIKI'S FRACTION CORRECTED for the draws being INDEPENDENT: `1 -
    # (1 - p) ** draws`, with `p` one draw's own chance of a 21 (a draw is 1 pick without a scent).
    # It reads a hair BELOW the wiki's linear `draws / total` (1.861% against 1.879% for a 19), so the
    # tab shows both and its tooltip says which is which.
    exact = 1 - (1 - potential.single(0, 21)) ** 60
    check("... and the exact chance of it, which the wiki's fraction slightly overstates",
          abs(potential.reroll_chance(19, 0) - exact) < 1e-12
          and potential.reroll_chance(19, 0) < 60 / 3194.0
          and potential.reroll_chance(20, 0) == 1.0
          and potential.reroll_chance(19, 0, True) > potential.reroll_chance(19, 0),
          "exact %.3f%% vs the wiki's %.3f%%" % (100 * potential.reroll_chance(19, 0),
                                                 100 * 60 / 3194.0))
    check("... and both readings are in the report --selftest prints",
          "320/3194" in potential.report(0) and "60/3194" in potential.report(0)
          # 21 level rows + the Potentiflator's 20, plus 3 heading/summary lines for the first table
          # and 2 for the second one
          and len(potential.report(0).splitlines()) == 21 + 20 + 5,
          len(potential.report(0).splitlines()))

    # THE CONTROLS OPEN ON THE GAME'S OWN SETTINGS, which is the whole point of the tab: `from_game`
    # reads them out of the game's preferences, so what it shows before anything is touched is what
    # the game will really do. Nothing here may be saved - see the tab's own docstring.
    battle, overworld, animations = potential.read_settings()
    check("the tab opened on the game's own settings",
          pot.battle.isChecked() == bool(battle > 1)
          and pot.overworld.isChecked() == bool(overworld > 1)
          # THE ANIMATIONS TICK NAMES THE FLAG, so it is ticked when the game has them OFF
          and pot.animations.isChecked() == (not animations)
          and not pot.scent.isChecked(),
          "battle x%s, game x%s, encounter animations %s -> the tick %s"
          % (battle, overworld, "on" if animations else "off",
             "ticked" if pot.animations.isChecked() else "unticked"))
    # A TICK EACH, not a picker: the game's own test is `> 1`, so the multiplier's SIZE cannot matter
    # and a three-way control would offer a difference that does not exist.
    check("... with a tick per multiplier, because only 'above x1' is a difference",
          isinstance(pot.battle, QCheckBox) and isinstance(pot.overworld, QCheckBox),
          "%s, %s" % (type(pot.battle).__name__, type(pot.overworld).__name__))
    check("... and x1.5 and x2 are worth the same one flag to the roll",
          potential.speed_ups(potential.SPEEDS[1], 1.0, True)
          == potential.speed_ups(potential.SPEEDS[2], 1.0, True) == 1,
          "x%s vs x%s" % (potential.SPEEDS[1], potential.SPEEDS[2]))

    def potential_rows():
        """The tab's table as `{level: (kind, weight, 1 pick, chance)}`, straight off the model."""
        return {int(row["level"]): (row["kind"], row["weight"], row["one"], row["chance"])
                for row in pot.table.model_.rows}

    count, scented = pot.configuration()
    picks = potential.rolls(scented)
    rows = potential_rows()
    check("every potential level is listed once, in order",
          sorted(rows) == list(potential.LEVELS), len(rows))
    check("... each with the game's own weight from the table those flags pick",
          all(rows[level][1] == "%d" % potential.weights(count)[level - 1]
              for level in potential.LEVELS),
          [(level, rows[level][1]) for level in (1, 11, 21)])
    check("... and the category the game's own thresholds give it",
          all(rows[level][0] == potential.KIND_NAMES[potential.level_kind(level)]
              for level in potential.LEVELS), rows[21][0])
    check("... and a chance that is the model's own, per level",
          all(rows[level][3] == potential.percent(potential.chances(count, picks)[level - 1])
              for level in potential.LEVELS),
          [(level, rows[level][3]) for level in (11, 17, 21)])
    # ... AND THE SAME NUMBERS AS FRACTIONS, the wiki's own form, from the same weights.
    check("... each with the exact fraction beside it, the way the wiki writes odds",
          all(pot.table.model_.rows[level - 1]["fraction"]
              == "%d/%d" % potential.fraction(count, picks, level)
              for level in potential.LEVELS),
          [pot.table.model_.rows[level - 1]["fraction"] for level in (1, 9, 21)])
    check("... summing to exactly 100% of the levels",
          abs(sum(potential.chances(count, picks)) - 1.0) < 1e-12)
    # THE DISTRIBUTION BARS ARE DRAWN, AND PROPORTIONALLY - not a count of block characters, which can
    # only change length one glyph at a time and turned the tail of this table into a staircase (the
    # user: "we could have them show real proportional bars, not those staggered ones"). The value in
    # the cell is the level's own share of the peak and the delegate paints exactly that much of the
    # cell, so the check is the MEASUREMENT: two rows whose values are 1.00 and 0.14 draw bars in that
    # ratio, to the pixel.
    bar_column = [column.key for column in pot.table.model_.columns].index("bar")
    bars = [row["bar"] for row in pot.table.model_.rows]
    check("the distribution column is a bar per level, its length the level's share of the peak",
          isinstance(pot.table.itemDelegateForColumn(bar_column), table_mod.BarDelegate)
          and abs(max(bars) - 1.0) < 1e-12 and min(bars) > 0
          and all(abs(value - potential.chances(count, picks)[level - 1] / max(potential.chances(
              count, picks))) < 1e-12 for level, value in zip(potential.LEVELS, bars)),
          [round(value, 4) for value in bars[:4]])
    drawn = []
    shot = pot.table.viewport().grab().toImage()
    bar_left = sum(pot.table.columnWidth(i) for i in range(bar_column))
    bar_width = pot.table.columnWidth(bar_column)
    tint = theme.BAR.lstrip("#")
    wanted = (int(tint[0:2], 16), int(tint[2:4], 16), int(tint[4:6], 16))
    for row_index in (10, 4):                       # the peak, and a level at ~1/7 of it
        y = row_index * 20 + 10
        drawn.append(sum(1 for x in range(bar_left, bar_left + bar_width)
                         if all(abs(channel - want) < 12 for channel, want in
                                zip((shot.pixelColor(x, y).red(), shot.pixelColor(x, y).green(),
                                     shot.pixelColor(x, y).blue()), wanted))))
    peak, small = drawn
    check("... and the pixels it draws are in that ratio, not rounded to whole blocks",
          peak == bar_width - 2 * table_mod.BarDelegate.INSET
          and abs(small - bars[4] * peak) <= 1,
          "peak %d px, 1/7 level %.4f -> %d px (expected %.1f)"
          % (peak, bars[4], small, bars[4] * peak))

    kinds = potential.kind_chances(count, picks)
    check("the header says which table and how many picks",
          ("%d-weight table" % potential.total(count)) in pot.summary.text()
          and (("%d picks" % picks) in pot.summary.text()
               or (picks == 1 and "1 pick" in pot.summary.text())),
          pot.summary.text())
    check("... and the odds of the three categories, as the game's own letters",
          all(potential.percent(kinds[letter]) in pot.odds.text()
              for letter, _name, _low, _high in potential.KINDS)
          and abs(sum(kinds.values()) - 1.0) < 1e-12, pot.odds.text())
    check("... with the perfect odds said as '1 in N' as well",
          potential.one_in(kinds["C"]) in pot.odds.text(),
          potential.one_in(kinds["C"]))

    # THE POTENTIFLATOR'S OWN TABLE beside it: every potential a Coromon can be handed over at, the
    # game's number of draws, the wiki's fraction (`draws / total`) and the exact chance. It is the
    # SAME roll, so the four controls move it too - which is why it shares the tab.
    def reroll_rows():
        """The Potentiflator's table as `{from: (draws, fraction, chance)}`, straight off the model."""
        return {int(row["from"]): (row["draws"], row["fraction"], row["chance"])
                for row in pot.reroll_table.model_.rows}

    rrows = reroll_rows()
    check("the Potentiflator is a table of its own, one row per potential it starts from",
          sorted(rrows) == list(range(1, potential.REROLL_CERTAIN + 1)), len(rrows))
    check("... with the game's own number of draws, and 'always' where it is a guarantee",
          all(rrows[level][0] == ("always" if potential.reroll_draws(level) is None
                                  else "%d" % potential.reroll_draws(level))
              for level in rrows),
          [(level, rrows[level][0]) for level in (1, 17, 18, 19, 20)])
    check("... the draws over the weight of 21 as the fraction, exactly as the wiki writes it",
          all(rrows[level][1] == potential.reroll_fraction(level, count) for level in rrows)
          and rrows[20][1] == "%d/%d" % (potential.total(count), potential.total(count)),
          [rrows[level][1] for level in (1, 19, 20)])
    check("... and the exact chance beside it",
          all(rrows[level][2]
              == potential.percent(potential.reroll_chance(level, count, scented))
              for level in rrows)
          and rrows[20][2] == potential.percent(1.0)
          and float(rrows[19][2].rstrip("%")) > float(rrows[17][2].rstrip("%")),
          [(level, rrows[level][2]) for level in (17, 19, 20)])
    check("... and the wild table sits straight after it, with no separator between them",
          pot.reroll_table.parentWidget() is pot.table.parentWidget()
          and pot.table.x() == pot.reroll_table.x() + pot.reroll_table.width() + 10
          and pot.table.y() == pot.reroll_table.y()
          and pot.table.width() == pot.table.content_width()
          and pot.reroll_table.width() == pot.reroll_table.content_width(),
          "Potentiflator %d..%d, wild %d..%d" % (pot.reroll_table.x(),
                                                 pot.reroll_table.x() + pot.reroll_table.width(),
                                                 pot.table.x(), pot.table.x() + pot.table.width()))

    # THE SCENT: the picks go from 1 / 3 and nothing else moves - so every level's chance changes,
    # and it is the TOP of the table that gains and the middle that pays for it.
    pot.scent.setChecked(True)
    pump(app)
    scented_rows = potential_rows()
    check("the Potent Scent takes the best of three picks",
          pot.configuration()[1] and potential.rolls(True) == 3,
          pot.summary.text())
    check("... which raises the perfect and potent chances and lowers the middle",
          float(scented_rows[21][3].rstrip("%")) > float(rows[21][3].rstrip("%"))
          and float(scented_rows[17][3].rstrip("%")) > float(rows[17][3].rstrip("%"))
          and float(scented_rows[11][3].rstrip("%")) < float(rows[11][3].rstrip("%")),
          "level 21 %s -> %s, level 11 %s -> %s"
          % (rows[21][3], scented_rows[21][3], rows[11][3], scented_rows[11][3]))
    check("... while the weights it draws from are untouched",
          all(scented_rows[level][1] == rows[level][1] for level in potential.LEVELS))
    # ... AND THE MACHINE MOVES WITH IT, because it rolls the same distribution: the fraction stays
    # `draws / weight` (a scent does not change how many draws), and the chance to perfect climbs.
    check("... and the Potentiflator's chance to perfect climbs with the scent too",
          int(pot.reroll_table.model_.rows[18]["draws"]) == 60
          and float(pot.reroll_table.model_.rows[18]["chance"].rstrip("%"))
          > float(rrows[19][2].rstrip("%")),
          "19: %s -> %s (fraction %s)" % (rrows[19][2],
                                          pot.reroll_table.model_.rows[18]["chance"],
                                          pot.reroll_table.model_.rows[18]["fraction"]))
    pot.scent.setChecked(False)

    # THE TWO TICKS: each one is a flag, and there is nothing in between to choose. The animations tick
    # NAMES THE FLAG - "encounter animations off" - so the baseline (nothing sped up) is it UNTICKED,
    # which is also the state a game with the animations on opens in.
    pot.battle.setChecked(False)
    pot.overworld.setChecked(False)
    pot.animations.setChecked(False)
    pump(app)
    plain = pot.configuration()[0]
    plain_rows = potential_rows()
    pot.battle.setChecked(True)
    pump(app)
    one = pot.configuration()[0]
    pot.overworld.setChecked(True)
    pump(app)
    both = pot.configuration()[0]
    check("each speed tick costs exactly one flag",
          plain == 0 and one == plain + 1 and both == one + 1,
          "none -> %d, battle -> %d, both -> %d" % (plain, one, both))
    check("... and a flag really moves the table it rolls against",
          potential.total(both) > potential.total(plain)
          and float(potential_rows()[21][3].rstrip("%"))
          < float(plain_rows[21][3].rstrip("%")),
          "table %d -> %d, perfect %s -> %s"
          % (potential.total(plain), potential.total(both), plain_rows[21][3],
             potential_rows()[21][3]))
    pot.animations.setChecked(True)
    pump(app)
    check("ticking 'encounter animations off' is the third flag",
          pot.configuration()[0] == both + 1 == potential.MAX_SPEED_UPS,
          pot.configuration()[0])
    # ... AND THE POTENTIFLATOR'S TABLE FOLLOWS THE SAME FLAGS: its fraction's denominator is the
    # weight table those flags pick, and it is the Wiki's own three-digit numbers the wiki's table
    # prints under different defaults - so the tab is checkable against it in either state.
    check("... and the Potentiflator's fractions follow the same flags",
          pot.reroll_table.model_.rows[18]["fraction"] == "60/%d" % potential.total(3)
          == "60/4567"
          and pot.reroll_table.model_.rows[0]["fraction"] == "3/%d" % potential.total(3),
          [pot.reroll_table.model_.rows[i]["fraction"] for i in (0, 18, 19)])
    check("a missing setting reads as the game's own default (nothing sped up)",
          potential.speed_ups(None, None, None) == 0, potential.speed_ups(None, None, None))

    pot.reload_button.click()
    pump(app)
    check("'From the game' puts the controls back on the game's settings",
          pot.battle.isChecked() == bool(battle > 1)
          and pot.overworld.isChecked() == bool(overworld > 1)
          and pot.animations.isChecked() == (not animations)
          and not pot.scent.isChecked(),
          "battle x%s -> %s, game x%s -> %s, encounter animations off -> %s"
          % (battle, pot.battle.isChecked(), overworld, pot.overworld.isChecked(),
             pot.animations.isChecked()))
    check("... and says in the tooltip which multiplier the game really has",
          ("x%g" % battle) in pot.battle.toolTip()
          and ("x%g" % overworld) in pot.overworld.toolTip(),
          pot.battle.toolTip().split("\n")[0])

    # ALL 21 LEVELS AT THE WINDOW'S REAL SIZE, which is why the note is a tooltip: at 1040x560 the
    # perfect row is the last one, and a wrapped paragraph under the table is exactly what would push
    # it into a scrollbar. MEASURED, like every other layout claim here.
    window.resize(1040, 560)
    pump(app, 4)
    potle = pot.table
    check("all 21 levels fit the window it is really used at",
          potle.content_height() <= potle.viewport().height() + 2,
          "needs %d px, has %d" % (potle.content_height(),
                                   potle.viewport().height()))
    # ... AND SO DOES THE POTENTIFLATOR'S, which is why the two tables are SIDE BY SIDE: stacked, each
    # would be half as tall and both would scroll.
    check("... and the Potentiflator's table fits beside it",
          pot.reroll_table.content_height() <= pot.reroll_table.viewport().height() + 2,
          "needs %d px, has %d" % (pot.reroll_table.content_height(),
                                   pot.reroll_table.viewport().height()))
    # BOTH TABLES WANT THEIR WIDTH, though, and this is where that is measured. THE WINDOW IS MADE WIDER
    # FOR IT than the 1040 it opens at, and that is not a fudge: THIS PLATFORM HAS NO REAL FONT, so every
    # string measures about 30% wider here than in the window the user runs (measured: the pair needs
    # 1371 px offscreen against 989 px with the real font, where it fits 1040 and 1278 both). What is
    # checked is the property that does not depend on the font: neither table scrolls sideways when the
    # room is there, and each is exactly as wide as its columns ask.
    window.resize(1420, 760)
    pot.scent.setChecked(True)
    pump(app, 4)
    check("... and neither scrolls sideways when there is room, scent and all",
          not potle.horizontalScrollBar().isVisible()
          and not pot.reroll_table.horizontalScrollBar().isVisible()
          and potle.width() >= potle.content_width()
          and pot.reroll_table.width() >= pot.reroll_table.content_width(),
          "wild %d of %d needed, reroll %d of %d"
          % (potle.width(), potle.content_width(), pot.reroll_table.width(),
             pot.reroll_table.content_width()))
    pot.scent.setChecked(False)
    pump(app, 4)
    # ... AND SO DOES THE CONTROL ROW, which is five widgets on one line: the four switches and the
    # button. Measured the way the Database tab's row is (all centres level), plus the one thing that
    # row cannot ask - that the last widget's right edge is still inside the tab.
    controls = [pot.scent, pot.battle, pot.overworld, pot.animations, pot.reload_button]
    centres = {widget.geometry().center().y() for widget in controls}
    check("the four switches and the button are one row, and it fits",
          len(centres) == 1 and controls[-1].geometry().right() <= pot.width() - 4,
          "centres %s, ends at %d of %d" % (sorted(centres), controls[-1].geometry().right(),
                                            pot.width()))
    window.resize(1420, 760)
    pump(app, 4)

    # THE COLUMNS THE SWITCHES MOVE KEEP ONE WIDTH, whatever they are set to - the user: "so that the
    # table column there does not change so much when flipping the checkboxes, could you make it so it's
    # always the width it would show when the longest fractions are calculated? that would be the
    # everything on, but animations off". Each is measured ONCE at its widest (all three flags = the
    # 4567-weight table, plus the scent's three picks = a ten-digit numerator over `total ** 3`) and
    # pinned, so neither table can jump sideways - nor the table beside it, which sits after it.
    def table_widths():
        return (pot.table.content_width(), pot.reroll_table.content_width(),
                tuple(pot.table.columnWidth(i) for i in range(pot.table.model_.columnCount())),
                tuple(pot.reroll_table.columnWidth(i)
                      for i in range(pot.reroll_table.model_.columnCount())))

    metrics = QFontMetrics(pot.table.font())
    before = table_widths()
    seen = {before}
    reaches = {key: 0 for key in potential_tab.PINNED}
    for combo in ((False, False, False, False), (True, False, False, False),
                  (False, True, False, False), (False, False, True, False),
                  (False, False, False, True), (True, True, True, True),
                  (True, True, True, False)):
        for tick, on in zip((pot.scent, pot.battle, pot.overworld, pot.animations), combo):
            tick.setChecked(on)
        pump(app, 2)
        seen.add(table_widths())
        for row in pot.table.model_.rows:
            for key in reaches:
                reaches[key] = max(reaches[key], metrics.horizontalAdvance(row[key]))
    check("the columns the switches move keep ONE width, however they are set",
          len(seen) == 1,
          "%d distinct width set(s) over 7 settings, wild %d px, reroll %d px"
          % (len(seen), before[0], before[1]))
    check("... and their widest value still fits, so nothing is ever elided",
          all(reaches[key] + table_mod.COLUMN_PAD
              <= pot.table.columnWidth([column.key for column
                                        in pot.table.model_.columns].index(key))
              for key in reaches),
          {key: (reaches[key], pot.table.columnWidth([column.key for column
                                                      in pot.table.model_.columns].index(key)))
           for key in reaches})
    for tick, on in zip((pot.scent, pot.battle, pot.overworld, pot.animations),
                        (False, False, False, True)):
        tick.setChecked(on)
    pot.reload_button.click()
    pump(app, 3)

    # ---------------------------------------------------------------- saved state
    check("tab index saved", prefs.get("tab") == 5, prefs.get("tab"))
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
