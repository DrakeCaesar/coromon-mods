"""Offscreen smoke test for the grind window.

Checks that the data reaches the widgets: the four tabs, a filled ranking, a map with a plan, the
game's own database grid and the dex icons in it, the item catalogue with the stats the game's Lua
holds, the skill table and its description, sorting, filtering, the cross-tab jump, and the window's
saved geometry. Runs on Qt's `offscreen` platform, so it needs no display and nothing running.

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
import items as item_data                                   # noqa: E402
import missing as missing_data                              # noqa: E402
import savefile                                             # noqa: E402

import coromontools.state as state                          # noqa: E402
from coromontools import MainWindow, font, icons, mapnames as mn   # noqa: E402
from coromontools import config                              # noqa: E402
from coromontools.database_tab import CATEGORIES, SKIN_COLUMN  # noqa: E402
from coromontools.grind import encounter_lines                # noqa: E402
from coromontools import missing_tab                          # noqa: E402
from coromontools.table import ICON, sort_rows                # noqa: E402
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
    # the Missing tab joined the set after the Database one, which it is the companion of.
    titles = [window.tabs.tabText(index).strip() for index in range(window.tabs.count())]
    check("five tabs, in that order",
          titles == ["Where to grind", "Database", "Missing", "Items", "Skills"], titles)
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
        check("plan draws no colourless patch", all("colour" in p for p in plan["patches"]))
        check("legend lists the zones", len(grind.map.legend) > 0, len(grind.map.legend))

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

    # THE PANE: ONE LINE PER EVOLUTIONARY LINE, with the kinds it is short of on that line - the user
    # spelled out the unit: "standard, potent and perfect kyreptil should count as 1 group ... only if
    # we were missing both potent kyreptil and kyraptor does that count as one missing slot". So a
    # 2-stage line short of three kinds is ONE row here, and the kinds are named on it.
    miss.table.selectRow(0)
    pump(app, 3)
    pane = miss.detail.toPlainText()
    check("the pane names the selected zone and how many lines and groups are short",
          sample["zone"].name in pane and "%d line(s) short, %d of %d groups missing"
          % (len(sample["missing_lines"]), len(sample["missing"]), len(sample["groups"])) in pane,
          pane.splitlines()[:2])
    named = [line for line in pane.splitlines() if line.startswith("  ") and line.strip()]
    check("... with one line per LINE short, not per kind",
          len(named) == len(sample["missing_lines"]), len(named))
    check("... each naming its line and every kind that line is short of",
          all(any(g.family and g.name in line and
                  all(missing_tab.SHORT[k] in line
                      for k in {x.kind for x in sample["missing"] if x.family == g.family})
                  for g in sample["missing"]) for line in named),
          named[:2])
    check("... each row standing for one of the lines that is short here",
          sorted(line.split()[0] for line in named)
          == sorted({g.name for g in sample["missing"]}), named[:2])
    row_of = {line.split()[0]: line for line in named}
    check("... with every missing kind named on its line's own row",
          all(missing_tab.SHORT[g.kind] in row_of[g.name] for g in sample["missing"]), named[:2])
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

    # ---------------------------------------------------------------- saved state
    check("tab index saved", prefs.get("tab") == 4, prefs.get("tab"))
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
