#!/usr/bin/env python3
"""
quick_reload.py - put a save back the way it was, without relaunching the game.

LIVE, NOT ABANDONED, despite the directory it sits in. `ingame/reload.py` puts this directory on
`sys.path` and does `import quick_reload`, then sends these chunks into the game verbatim through
`loadstring`. So every chunk in this file IS the reload that runs. The directory name is a leftover
from an earlier design that drove this from outside the game, and it has already misled one reader
into editing the wrong file - do not delete this module.

WHY IT EXISTS. The Potentiflator decides its answer the moment a Coromon is handed over, not
when the steps are walked, so the fast way to a good roll is to hand one over, read the answer
and - if it is not the one you want - go back to just before the handover and do it again. But
the handover saves, so the slot already holds the new state, and an ordinary in-game load brings
back exactly the roll you were trying to escape. What was missing was a copy of the save from
BEFORE the handover, and that is what `--save` takes and `--restore` puts back.

    python quick_reload.py --save       before you hand the Coromon over
    python quick_reload.py --restore    to go back to that moment
    python quick_reload.py --arm        put a pass-through hook on the game's own load
    python quick_reload.py --state      read-only look at what the reload sees
    python quick_reload.py --teardown   run the game's own quit sequence: back to the main menu
    python quick_reload.py --reload     the teardown, then load the captured save
    python quick_reload.py --disarm     take the hook back off
    python quick_reload.py --block      hold off saving, if the handover is writing to the slot

HOW THE SAVE IS REALLY STORED, and why this does not touch the database. The game keeps slots
through Solar2D's own preference store - `SaveslotPreferences` calls `system.setPreferences` and
`system.getPreference` under keys like `saveslot_self_<n>` and `<n>_auto` - and Solar2D holds
those in memory and flushes them to

    %LOCALAPPDATA%\\TRAGsoft\\Coromon\\.system\\CoronaPreferences.sqlite

Editing that file from outside is the wrong lever: the running game reads its own copy, and its
next flush writes over anything put there. So the snapshot is taken and put back through the
game's own preference calls, which move the memory and the file together. Measured before
building this: `getPreference('app', 'saveslot_self_2', 'string')` answered 308472 characters,
the same length as that row in the database, and a set/read/delete on a throwaway key left the
real rows alone.

WHICH KEYS. The slots and their autosaves, listed from the database because that is the only
place their names are written down. The `_onlineCache` rows are left alone: they are caches of
the online profile, they are the largest rows in the store, and they have nothing to do with
what is in a slot.

WHAT DID NOT WORK, so it is not tried again. Writing the store back is correct and verifiable -
--restore reads every value back identical - but a LOAD does not use it. The game keeps decoded
slot data in memory (the same module pairs those preferences with a cache:
`SaveslotPreferences.getCachedOnlineSaveslot`, and `SaveslotFacade` has
`getCachedSaveslotClustersForDeviceId`), so what a load restores from is not the store it was
just handed. It does not stay written either: a second `--restore` found the live slot one
character LONGER than the snapshot, so the game had saved over the restore in between.

WHAT IS ACTUALLY WANTED, since the rest of this is a record of how it was arrived at: save the
game before handing the Coromon over, hand it over, read the potential, and if it is not 21
press a button to reload the save. So the game has to LOAD a save on demand - anything that only
arranges for a load later does not count.

HOW THE LOAD IS TRIGGERED. `playerStateHelper.loadGame` is the game's own load, but calling it
means getting seven parameters right, four of them objects - a saveslot entry, its cluster, and
two version ids read off them - which is re-implementing the menu rather than shortcutting it,
and that is the exact shape of the one crash these tools have caused.

So it is not called blind. `--arm` puts a PASS-THROUGH wrapper on `loadGame` which keeps the
arguments of the next real load and then calls straight through, changing nothing about the
call. `--reload` replays exactly that call. Nothing is guessed, and no arguments are invented:
they are the game's own. `--disarm` puts the original back, and a game restart drops it too.

One load has to happen with the hook in place before `--reload` has anything to replay - the
load from the menu is the teaching step, and every reload after it is one command.

IT DOES NOT WORK AS IT WAS, and finding that out cost a game session. Replaying the captured call
from mid-game left the game in a broken state and it closed. The capture was sound - the hook was
verified pass-through, the kept function was the game's own, and the arguments were the game's
own. What was wrong was the state they were replayed into.

WHAT THE FLOW ACTUALLY IS. Three shipped modules spell it out and they agree, and `loadGame`
itself (lines 293-320) is the one that settles it. Read there, `loadGame` does not tear anything
down: it remembers the slot details, calls `initialisePlayerLibraries(_saveslotData)`, then
`worldHelper:createInstance(...)` to build the world. So it builds a world on top of whatever is
already there, and calling it mid-game is what broke the game - not the arguments.

Tearing down is a separate step, done differently by each of the three callers:

  * the in-game Quit button (outerTopBarQuitButtonBuilder, lines 27-40), which is the flow that
    was done by hand:
        inputHelper:unblockInput()
        worldInterface:destroy() ; worldHelper:destroy()     -- or HexagonWorld:destroy()
        playerStateHelper:quitCurrentGame()
        Achievement:resetPercentageOfMaxProgressCache()
        titleScreen:new()
  * the battle-lost screen (battleLostOverlayBuilder, lines 135-138):
        playerStateHelper:quitCurrentGame() ; titleScreen:new()
  * the saveslot migration test (test_saveslotDataMigrations, lines 103-120): destroys the world
    and calls quitCurrentGame between one load and the next.

`quitCurrentGame` is the small one - two instructions: `:destroy()` on every player module, then
`selectedSaveslotIndex = nil`. It does not touch the world, which is why the Quit button destroys
the world around it.

So the reload is the Quit button's teardown, then the load. Two things carry over from the game's
own code rather than being decided here: the teardown keeps the branch on `app:isCoromon1()`
instead of assuming which two of worldInterface/worldHelper/HexagonWorld this build uses, and the
load is called as the game's debug helper calls it (debug_load_saveslot, line 51) - transition
`'instant'` and no completion callback. The title screen passes `function() _onWorldLoaded() end`,
a closure into a screen that no longer exists by then, and the debug helper proves it is optional.

`titleScreen:new()` and `Achievement:resetPercentageOfMaxProgressCache()` WERE left out of the
teardown at first, on the reasoning that they only put a screen on the display that a load is about
to replace. That reasoning was wrong, and it is the most likely reason the teardown appeared to do
nothing: they are what returns the game to the MAIN MENU, and the main menu is the state a load is
actually done from. Stopping before them left the game with no world and no screen.

THERE IS NO REUSABLE "GO TO THE MAIN MENU" METHOD, which is worth knowing before looking for one.
Checked across every shipped module: the only quit-named method anywhere is
`playerStateHelper.quitCurrentGame`, and it does not go to the menu - it destroys the player modules
and clears the selected slot. The rest of the sequence exists only inline inside two UI button
handlers (the top-bar Quit button and the battle-lost overlay), so it has to be transcribed. What IS
callable is its last step: `titleScreen:new()` (titleScreen.lu, child 2 of 3, lines 21-544, no
arguments).

`gameSettings:saveSettings()` from the title screen's own load routine is the one thing still not
reproduced - it writes to disk and has no part in loading.

THE TEARDOWN IS ASYNCHRONOUS, which is what the first attempt at this got wrong. `worldHelper`'s
`destroy` (worldHelper.lua lines 223-253) does its visible work and then ends with
`nextFrame(function() destroyInstance(...) end)` - and `destroyInstance` (lines 8-11) is two lines:

    instance = display.remove(instance)
    setmetatable(t, nil)

So when `destroy` returns, the instance is still there and the module still has its metatable; a
frame later the instance is nilled AND the module's metatable is stripped, which is what makes
`worldHelper:destroy` and the rest of the instance methods disappear until `createInstance` puts
them back. Loading between those two moments does not fail quietly: `createInstance` builds the new
instance and calls `MTE:loadMap(mapPath, mapFile, builder)`, whose callback is the world builder
(lines 285-502, first thing it does is `instance:insert(MTE:getTiledWorld())`). The deferred
`destroyInstance` then nils the instance the builder is waiting on, and the builder dies at line 286
with "attempt to index upvalue 'instance' (a nil value)". That was the crash.

So the load waits for `worldHelper:isCreated()`, which is literally `return instance ~= nil` (lines
5-7), to go false before it runs - the signal that the deferred half has finished. It is a direct
field of the module rather than a method reached through the metatable, so it still answers after
`destroyInstance` strips the metatable. If it is ever missing, the wait falls back to a fixed delay
rather than loading straight away.

`--block` is kept because it may be needed WITH the reload rather than instead of it: if the
handover writes to the slot, a reload hands the handover back, and saving has to be held off
while the roll is being judged. Whether the handover saves at all is not established.

BEFORE IT PUTS ANYTHING BACK it snapshots what is there now, so a restore that was not wanted
is itself undoable - the same command against the file it just wrote.
"""

import argparse
import json
import os
import sqlite3
import sys
import time

# this file lives in abandoned/, but the tools it drives are in the folder above it
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scroll_fix import _bridge, _eval  # noqa: E402

PREFS_DB = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "TRAGsoft", "Coromon", ".system", "CoronaPreferences.sqlite",
)

# where snapshots live. Beside the grind tool's state, and deliberately NOT inside the tools
# folder: a save is personal, and that folder is a git repository.
BANK = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "coromon-backups")

DEFAULT_FILE = os.path.join(BANK, "quicksave.json")

# how many times to re-ask when the bridge hands back nothing. Reading a slot is a megabyte of
# string through it and it does occasionally drop one.
TRIES = 6
RETRY_WAIT = 0.4


def log(msg):
    print(msg, flush=True)


def lua_str(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def lua_long(value):
    """A Lua long-bracket string holding `value`. The level is raised until the value cannot
    close it - a save row is JSON with quotes in it, so a short bracket would end early."""
    level = 0
    while True:
        bar = "=" * level
        if ("]" + bar + "]") not in value:
            return "[" + bar + "[\n" + value + "\n]" + bar + "]"
        level += 1


def slot_keys():
    """The slots and autosaves, from the only place their names are written down."""
    uri = "file:%s?mode=ro" % PREFS_DB.replace("\\", "/")
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        cur.execute("select key from preference where key like 'saveslot%'")
        keys = [r[0] for r in cur.fetchall()]
    finally:
        con.close()
    return sorted(k for k in keys if not k.endswith("_onlineCache"))


def ask(bridge, code, timeout=90.0):
    """Run one chunk, retrying when the bridge drops the answer."""
    for _ in range(TRIES):
        out = _eval(bridge, code, timeout=timeout)
        if out is not None:
            return out
        time.sleep(RETRY_WAIT)
    return None


def read_current(bridge, keys):
    """What the running game's preference store holds for each key, right now."""
    got = {}
    for key in keys:
        code = (
            "local ok, v = pcall(function()\n"
            "  return system.getPreference('app', %s, 'string')\n"
            "end)\n"
            "if not ok or type(v) ~= 'string' then return 'NOTHERE' end\n"
            "return v\n" % lua_str(key)
        )
        out = ask(bridge, code)
        if out is None or out == "NOTHERE":
            log("  %-32s not in the store" % key)
            continue
        got[key] = out
        log("  %-32s %8d chars" % (key, len(out)))
    return got


def do_save(path, keys):
    bridge = _bridge()
    log("reading the running game's store:")
    data = read_current(bridge, keys)
    if not data:
        log("nothing to save - is the game running with a save loaded?")
        return 1
    snapshot = {
        "captured": time.strftime("%Y-%m-%d %H:%M:%S"),
        "keys": data,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh)
    log("")
    log("saved %d key(s) to %s" % (len(data), path))
    log("now hand the Coromon over; --restore puts this back")
    return 0


def do_restore(path, keys):
    if not os.path.exists(path):
        log("no snapshot at %s - run --save first" % path)
        return 1
    with open(path, encoding="utf-8") as fh:
        snapshot = json.load(fh)
    want = snapshot.get("keys") or {}
    log("snapshot taken %s, %d key(s)" % (snapshot.get("captured", "?"), len(want)))
    if not want:
        log("the snapshot is empty")
        return 1

    bridge = _bridge()

    # what is there now, kept so this restore can itself be undone
    safety = os.path.join(os.path.dirname(path) or ".", "quicksave-before-restore.json")
    current = read_current(bridge, keys)
    with open(safety, "w", encoding="utf-8") as fh:
        json.dump({"captured": time.strftime("%Y-%m-%d %H:%M:%S"), "keys": current}, fh)
    log("")
    log("kept what was there in %s" % safety)

    log("")
    log("putting the snapshot back through the game's own store:")
    failed = []
    for key, value in sorted(want.items()):
        code = (
            "local ok, err = pcall(function()\n"
            "  system.setPreferences('app', { [%s] = %s })\n"
            "end)\n"
            "if not ok then return 'FAILED: ' .. tostring(err) end\n"
            "local v = nil\n"
            "pcall(function() v = system.getPreference('app', %s, 'string') end)\n"
            "if type(v) ~= 'string' then return 'FAILED: unreadable back' end\n"
            "if v ~= %s then return 'FAILED: read back differs' end\n"
            "return 'OK'\n"
            % (lua_str(key), lua_long(value), lua_str(key), lua_long(value))
        )
        out = ask(bridge, code)
        if out == "OK":
            log("  %-32s %8d chars  written and read back" % (key, len(value)))
        else:
            failed.append(key)
            log("  %-32s %s" % (key, out or "no answer"))
    if failed:
        log("")
        log("%d key(s) did not take: %s" % (len(failed), ", ".join(failed)))
        return 1
    log("")
    log("done. The save is back; the slot still has to be LOADED IN GAME for it to matter.")
    return 0


def do_block(on):
    """Turn the game's own save block on or off.

    Two instructions inside the game (`blockSave` / `unblockSave` set a flag), so this is a
    switch rather than a call into anything with behaviour behind it. It is what stops the
    handover being written, which is what makes the slot still hold the state to go back to.
    """
    name = "blockSave" if on else "unblockSave"
    code = (
        "local ps = _G.playerStateHelper\n"
        "if type(ps) ~= 'table' or type(ps.%s) ~= 'function' then\n"
        "  return 'playerStateHelper.%s is not available'\n"
        "end\n"
        "local ok, err = pcall(function() ps:%s() end)\n"
        "if not ok then return 'FAILED: ' .. tostring(err) end\n"
        "local blocked = '?'\n"
        "pcall(function() blocked = tostring(ps:isSaveBlocked()) end)\n"
        "return 'save blocked = ' .. blocked\n" % (name, name, name)
    )
    log(ask(_bridge(), code) or "the game did not answer")
    return 0


def do_arm():
    """Put a pass-through wrapper on the game's own load, so the next real load's arguments can
    be kept and replayed later.

    It changes nothing about the call: the wrapper records what it was handed and calls the
    original with the same arguments. That is deliberate - the point is to avoid inventing the
    seven parameters, so nothing here may interfere with producing them.
    """
    code = r'''
local ps = _G.playerStateHelper
if type(ps) ~= 'table' or type(ps.loadGame) ~= 'function' then
  return 'playerStateHelper.loadGame is not available'
end
if type(ps.__hudQR) == 'table' then
  return 'already armed; captured = ' .. tostring(_G.__qrArgs ~= nil)
end
local orig = ps.loadGame
local function wrapper(...)
  local n = select('#', ...)
  _G.__qrArgs, _G.__qrN = { ... }, n
  return orig(...)
end
ps.__hudQR = { orig = orig }
ps.loadGame = wrapper
return 'armed - now load the save once from the menu, and --reload can repeat it'
'''
    log(ask(_bridge(), code) or "the game did not answer")
    return 0


# Stopping in-flight transitions. This is the half that was missing, and it is what makes closing a
# conversation safe. The dialogue's own destroy() was never the problem - a UIButton's
# press-animation transition COMPLETING afterwards was, delivering a button press to a dialogue that
# had already gone:
#   UIButtonTransition onComplete -> inputHelper -> dialogOverlayBuilder _onComplete
#     -> abstractDialogOverlayBuilder loadNextTextStackStep -> baseOverlayBuilder close
#     -> toBottomOrFadeOut -> magnet.top on a nil contentBounds          [magnet.lua:78]
#
# Cancelling by target is not enough to catch it. `dialog.destroy` DOES cancel transitions, but only
# the ones on its own children (dialog.lu lines 16-23, `transition.cancel(_child)`), and the button is
# not one of them. So the whole set has to go.
#
# TWO THINGS ABOUT HOW THE CANCEL WORKS, both read out of the bytecode, both of which the first
# attempt at this got wrong:
#
#  1. It is `cancelAll()`, NOT `cancel()`. `cancel(handleOrTagOrTarget, shouldFinish)` takes a target
#     (transition.lu lines 375-385) and dispatches through `forEachHandleOrTagOrTarget` (337-354),
#     which does `type(x) == 'string'` -> match a tag, `x.isTransitionHandle` -> a handle, else
#     `transition.target == x`. With no argument that is `nil.isTransitionHandle`, which THROWS. The
#     `pcall` around it swallowed the error, so the first attempt looked like it had run and had
#     silently done nothing. `cancelAll()` (386-390) is the no-argument one:
#
#       function t.cancelAll()
#         for i = 1, #transitions do
#           transitions[i].shouldBeCancelled = true        -- just a flag
#         end
#       end
#
#  2. It is DEFERRED, so it needs a frame before anything is closed. The module's enterFrame listener
#     (lines 83-110) collects every entry carrying the flag into the SAME "completed" list as the
#     ones that finished, removes them, and calls completeTransition on them. completeTransition
#     (lines 26-31) is what makes this safe:
#
#       if transition.onComplete and not transition.shouldBeCancelled then
#         transition.onComplete(transition.target, transition.overtime, transition.overtimeTime)
#       end
#
#     so a cancelled transition has its onComplete SKIPPED - the button never delivers its press to
#     anything. The flag only has to be set before that tick runs: the listener tests
#     `shouldBeCancelled` (L89) BEFORE it tests whether the transition has finished (L91), so a
#     transition flagged in the same frame its duration runs out is cancelled rather than delivered.
#     A flag set from a synchronous chunk is therefore always in time, because the next tick cannot
#     run until that chunk has returned. That is what lets the teardown cancel inline, in the same
#     chunk as the destroying.
#
#     So why is this a separate chunk at all? Because it does a different job. Cancelling earlier, and
#     then letting SETTLE seconds of frames go by, means anything already in flight is retired while
#     everything is still ALIVE - so a press that had not been delivered yet lands on a live dialogue
#     and is harmless, instead of landing on a disposed one. The inline call in the teardown cannot do
#     that: it can only suppress a delivery, not keep a dialogue alive to receive one.
#
# Nothing still animating at this point is meant to survive the next few lines anyway.
TRANSITION_CANCEL = r'''
if type(_G.transition) ~= 'table' then
  return 'there is no transition module'
end
if type(_G.transition.cancelAll) ~= 'function' then
  return 'transition.cancelAll() is not there, so in-flight callbacks cannot be stopped'
end
_G.__qrStep = 'transition.cancelAll()'
local ok, err = pcall(function() _G.transition.cancelAll() end)
_G.__qrReport = ok and 'flagged for cancellation'
                   or ('cancelAll threw: ' .. tostring(err))
return _G.__qrReport
'''

# The frame wait that goes with TRANSITION_CANCEL. The game is a separate process and keeps running
# while this driver sleeps, so the sleep costs nothing and buys free frames for the enterFrame
# listener to act on the flag. One frame would be enough; a quarter second is about fifteen.
SETTLE = 0.25


# The teardown, transcribed from the game's own Quit button
# (classes.interface.navBarObjects.topBarObjects.outerTopBarQuitButtonBuilder, lines 27-40).
# Every call in it is on a global, which is why it can be written out here instead of having to
# be captured. Each step names itself in `_G.__qrStep` before it runs, so if the game dies part
# way through, the next bridge call can say WHICH step it died on rather than just that the game
# is gone.
TEARDOWN_BASE = r'''
local ps = _G.playerStateHelper
if type(ps) ~= 'table' then return 'playerStateHelper is not there' end
if type(_G.app) ~= 'table' or type(_G.app.isCoromon1) ~= 'function' then
  return 'app:isCoromon1() is not there, so which teardown branch this build uses is unknown'
end
local ok, coromon1 = pcall(function() return _G.app:isCoromon1() end)
if not ok then return 'app:isCoromon1() threw: ' .. tostring(coromon1) end

local want = { 'inputHelper', 'pauseMenu' }
if coromon1 then
  want[#want + 1] = 'worldInterface'
  want[#want + 1] = 'worldHelper'
else
  want[#want + 1] = 'HexagonWorld'
end
local missing = {}
for _, name in ipairs(want) do
  if type(_G[name]) ~= 'table' then missing[#missing + 1] = name end
end
if #missing > 0 then
  return 'not available: ' .. table.concat(missing, ', ') .. ' - nothing was done'
end

-- Cancel AGAIN, here, and this one is the one that closes the window. Three different moments are
-- being covered:
--   * a press made before this command was sent  -> flagged by TRANSITION_CANCEL, retired during the
--     settle, delivered to nobody.
--   * a press made during the settle             -> delivered while the dialogue is still alive, so
--     it advances or closes normally and harms nothing. If it is still in flight when the settle
--     ends, it is caught by the call below.
--   * a press made in the gap while this chunk is being sent, or in the last ~96 ms of the settle
--     (that being how long UIButtonTransition's press-down-64 ms + release-32 ms takes)  -> in
--     flight RIGHT NOW, with the destroying happening in this same chunk. This call is what stops
--     it: the flag is only read at the next frame tick, but so is the delivery, and the tick cannot
--     run until this chunk returns.
-- Without this, the window in the third case is wide open - the separate chunk alone cannot cover a
-- press that starts after it has already run.
if type(_G.transition) == 'table' and type(_G.transition.cancelAll) == 'function' then
  _G.__qrStep = 'transition.cancelAll() (in the teardown chunk)'
  pcall(function() _G.transition.cancelAll() end)
end

-- how high the level was while a conversation was up, before anything here lowers it
if type(_G.inputHelper) == 'table' and type(_G.inputHelper.getInputLevel) == 'function' then
  pcall(function() _G.__qrInputLevelAtStart = _G.inputHelper:getInputLevel() end)
end

-- The game's OWN overlay teardown, and this is the one that matters. Every overlay, dialogues
-- included, is built through baseOverlayBuilder.new, which registers it in that module's
-- `activeOverlays` list, and the module exports exactly three things:
--     getAmountOfActiveOverlays, forceDestroyAll, new
--
-- forceDestroyAll (lines 9-13) walks that list in reverse and calls forceDestroy on each, and
-- forceDestroy (lines 261-264) is:
--
--     function obj.forceDestroy(self)
--       transition.cancelRecursive(self)      -- L262
--       self:destroy()                        -- L263
--     end
--
-- and cancelRecursive (transition.lu lines 392-402) is:
--
--     array.forEach(display.getChildrenIncludingParentRecursive(_displayGroup),
--                   function(t) ... transition.cancel(t) ... end)
--
-- i.e. it cancels by target over the overlay's WHOLE display subtree. That covers the Continue
-- button as a descendant, which is the object in the crash stack:
--   UIButtonTransition onComplete -> inputHelper -> dialogOverlayBuilder _onComplete
--     -> abstractDialogOverlayBuilder loadNextTextStackStep -> baseOverlayBuilder close
--     -> toBottomOrFadeOut -> magnet.top on a nil contentBounds
-- Every frame of that is an OVERLAY module. Destroying the dialog module alone never touched any of
-- it: `classes.interface.dialog.dialog` is one owner of one overlay, and the overlay is what holds
-- the container, the text stack and the button.
local bob = package.loaded['classes.interface.overlays.baseOverlayBuilder']
_G.__qrStep = 'baseOverlayBuilder:forceDestroyAll()'
if type(bob) == 'table' and type(bob.forceDestroyAll) == 'function' then
  local n = -1
  if type(bob.getAmountOfActiveOverlays) == 'function' then
    local okc, v = pcall(function() return bob:getAmountOfActiveOverlays() end)
    if okc and type(v) == 'number' then n = v end
  end
  local okb, errb = pcall(function() bob:forceDestroyAll() end)
  _G.__qrClosedOverlays = okb and ('forceDestroyAll over ' .. tostring(n) .. ' overlay(s)')
                              or ('forceDestroyAll FAILED: ' .. tostring(errb))
else
  _G.__qrClosedOverlays = 'baseOverlayBuilder.forceDestroyAll is not there'
end

-- Fallback, for a dialogue whose overlay somehow is not in the registry. After forceDestroyAll
-- this normally finds nothing open. Note destroy(true), not destroy() - see above.
-- A dialogue box is
-- stage-level UI, not world content, so destroying the world leaves it on screen still showing its
-- old text - which is exactly what was seen: a reload from inside a conversation came back with the
-- same conversation box still up. All four dialogue modules export the same pair, isCreated() and
-- destroy(), so one loop covers the main dialogue, the battle ones and Rogue's. Called as methods:
-- Lua ignores surplus arguments, so this is correct whether these functions take self or not.
local dialogues = {
  'classes.interface.dialog.dialog',
  'classes.interface.dialog.battleDialog',
  'classes.interface.dialog.BattleCornerDialog',
  'classes.interface.dialog.RogueDialog',
}
local closed = {}
for _, name in ipairs(dialogues) do
  local d = package.loaded[name]
  if type(d) == 'table' and type(d.isCreated) == 'function' and type(d.destroy) == 'function' then
    local okc, created = pcall(function() return d:isCreated() end)
    if okc and created then
      _G.__qrStep = 'closing ' .. name
      -- destroy(true), NOT destroy(). The second argument is not optional in practice:
      --
      --   function t.destroy(self, _isInstantDestroyAfterCreation)      -- dialog.lu lines 16-23
      --     if _isInstantDestroyAfterCreation and display.isDisplayObject(instance) then
      --       array.forEach(display.getChildrenIncludingParentRecursive(instance), _cancel)  -- L18
      --     end
      --     destroyInstance()      -- L22 = display.remove(instance); setmetatable(t, nil)
      --   end
      --
      -- where `_cancel(_child)` is `transition.cancel(_child)`, and `destroyInstance` is
      -- `instance = display.remove(instance); setmetatable(t, nil)` - it removes the group and
      -- cancels NOTHING. So called as destroy(), the guard at L17 is false, the sweep at L18 is
      -- skipped, and the box is pulled off the stage with its transitions still running against the
      -- removed objects. That is the shape of this crash exactly: `close` at
      -- baseOverlayBuilder.lu:215 -> toBottomOrFadeOut -> magnet.top -> a nil contentBounds, on a
      -- display group that has been removed. Passing true gets the sweep, which cancels by target
      -- over the dialogue's whole display tree - including the Continue button, which is a
      -- descendant, and whose press animation is the thing in the crash stack.
      local okd, errd = pcall(function() d:destroy(true) end)
      closed[#closed + 1] = name .. (okd and '' or (' FAILED: ' .. tostring(errd)))
    end
  end
end
_G.__qrClosedDialogues = (#closed > 0) and table.concat(closed, ', ') or nil

-- Everything that releases an input level must have released it BEFORE the level is corrected below,
-- and the pause menu is the one that does not live in activeOverlays: it is built by
-- classes.modules.interface.menuBuilder, not by baseOverlayBuilder, so the sweep above never sees it
-- and pauseMenu.lu's own forceDestroyIfCreated (lines 24-28 -> instance:forceDestroy()) is what
-- releases its level. Measured 2026-09-21: with the pause menu open, correcting the level while this
-- call was still to come took it one step too far down - level 0, "no navigations registered", input
-- dead - because the correction assumed the pause menu had already handed its level back.
--
-- AND THE MENU, WITH EVERYTHING AFTER IT, IS NOW A FUNCTION. The invariant that note is about is
-- "the menu has released its level BEFORE the correction below runs". forceDestroyIfCreated met it
-- synchronously; the game's own `close` (see MENU_CLOSE_VIA_GAME) meets it in its own callback, ~200 ms
-- later. So the rest of this chunk - the level correction, the world destroy, quitCurrentGame and the
-- final settle - is wrapped up and run at the point the menu is actually gone, whichever route got us
-- there. Everything before this point (the overlay sweep, the dialogues) stays where it is: it releases
-- levels too, and it must still happen before the correction.
local function afterMenuReleased()

-- INPUT LEVEL AND FOCUS. Measured 2026-09-21, and it corrected a wrong assumption of mine:
--
--   in a conversation:      level = 3 | nav[1] focused = false | nav[2] focused = false | nav[3] focused = true
--   right after a teardown: level = 0 | no navigations registered
--   right after the load:   level = 0 | nav[1] focused = true
--
-- So the WORLD's navigation lives at level 1 and a conversation raises the level to 3 (world 1,
-- dialogue 2, answers 3). The level is NOT meant to come back to 0. decreaseInputLevel does
--
--   inputNavigationPerInputLevel[currentInputLevel] = nil        -- L166, the level being left
--
-- so walking the level to 0 DELETES the world's own nav, and the fingerprint is exactly what was
-- measured: level 0, no navigations registered, input dead because nothing is registered at the level
-- being routed. An earlier version of this block did that walk-back; it was the bug, not the fix.
-- The overlays' own destroy already lowers the level correctly (baseOverlayBuilder L249-250), so the
-- number is left alone.
--
-- What does need checking is FOCUS, because that is what routes input: the nav serving the CURRENT
-- level has to be focused. increaseInputLevel marks the level it leaves as
-- `pausedInputNavigations[old] = true`, and decreaseInputLevel only hands focus to the level below
-- when that flag is clear:
--
--   if nav2 and not pausedInputNavigations[currentInputLevel] then   -- L169
--     nav2:handleObtainFocus()                                     -- L170
--     pausedInputNavigations[currentInputLevel] = nil              -- L171
--   end
--
-- so a level left paused never regains focus. Both tables are module locals, reachable as upvalues of
-- increaseInputLevel / decreaseInputLevel, which is why they are read with debug.getupvalue.
_G.__qrStep = 'input level'
if type(_G.inputHelper) == 'table' and type(debug) == 'table'
   and type(debug.getupvalue) == 'function' then
  local ih = _G.inputHelper
  local perLevel, paused = nil, nil
  local function collect(fn)
    if type(fn) ~= 'function' then return end
    local i = 1
    while true do
      local k, v = debug.getupvalue(fn, i)
      if k == nil then break end
      if k == 'inputNavigationPerInputLevel' and type(v) == 'table' then perLevel = v end
      if k == 'pausedInputNavigations' and type(v) == 'table' then paused = v end
      i = i + 1
    end
  end
  collect(ih.increaseInputLevel)
  collect(ih.decreaseInputLevel)
  local level = nil
  if type(ih.getInputLevel) == 'function' then
    local okl, v = pcall(function() return ih:getInputLevel() end)
    if okl and type(v) == 'number' then level = v end
  end
  _G.__qrInputLevelBefore = level

  -- how many overlays are left, read out of baseOverlayBuilder's own registry
  local overlaysLeft = nil
  local bob = package.loaded['classes.interface.overlays.baseOverlayBuilder']
  if type(bob) == 'table' then
    local nm, list = debug.getupvalue(bob.forceDestroyAll, 1)
    if nm == 'activeOverlays' and type(list) == 'table' then overlaysLeft = #list end
  end
  _G.__qrOverlaysLeft = overlaysLeft

  -- The level input belongs at with nothing open is the LOWEST level that still has a navigation
  -- registered. Measured 2026-09-21: the world's nav is at level 1 and a conversation registers navs
  -- above it (dialogue 2, answers 3), so with no overlays left, anything above the lowest is a stale
  -- nav belonging to something destroyed. THIS is where the level went wrong - it was left at 2 with
  -- nav[2] still focused, so input was being routed to a dead dialogue's navigation while the world's
  -- own nav[1] sat unfocused, and the controls were dead with nothing visibly inconsistent. Note the
  -- focus check below cannot catch this on its own: nav[2] WAS focused, so the state was
  -- self-consistent and merely wrong. Derived from the game's state rather than hardcoding 0 or 1,
  -- and it steps down with the game's own decreaseInputLevel so navs and focus are unwound properly.
  local target = nil
  if type(perLevel) == 'table' then
    for k in pairs(perLevel) do
      if type(k) == 'number' and (target == nil or k < target) then target = k end
    end
  end
  _G.__qrTargetLevel = target
  _G.__qrFinalTarget = target
  -- THIS RUNS ON THE MENU ROUND TRIP TOO, and gating it out was a measured mistake.
  --
  -- The reasoning for the gate was that the game's own paired calls - `titleScreen:new()` ->
  -- `increaseInputLevel`, then the title screen's exit -> `decreaseInputLevel` - own the level, so a
  -- hand-derived correction on top would only blur the measurement. What the probe then showed is that
  -- the pair is faithful to whatever level it STARTS from, and that level is not always the world's:
  --
  --   reload starting - ... level=2 navs=2:{1,2} overlays=0 hoverables=4    <- leaked during play
  --     leaving the menu: lvl=3 navs={1,2,3} paused={1,2}                   <- new() raised 2->3
  --     left the menu:    lvl=2 navs={1,2} paused={1}                       <- pair returned to 2, not 1
  --     load done (ok) ... level=2 navs=2:{1,2}                             <- new world registers at 2
  --
  -- The world's own navigation is at level 1, so level 1 ends up paused and unfocused while the game
  -- routes input at level 2. Nothing is visibly wrong until something lowers the level again - closing
  -- a menu, opening a dialogue - and then input lands on a stale level-1 nav and the controls freeze.
  -- That is the reported lock-up, and it is the same fault this block was written for in the first
  -- place: "level left at 2 with nav[2] still focused while the world's own nav[1] sat unfocused".
  --
  -- The block runs in TEARDOWN_BASE, i.e. BEFORE `titleScreen:new()`, so it does not fight the round
  -- trip - it hands it a clean, focused level 1, which is the level the game itself is always at when a
  -- player opens the menu (the Quit button lives in the world's top bar). The round trip then raises
  -- 1->2 and lowers back to 1, which is the game's own sequence exactly.
  if type(level) == 'number' and type(target) == 'number' and level > target
     and overlaysLeft == 0 then
    local steps = 0
    while level > target and steps < 16 do
      local okd = pcall(function() ih:decreaseInputLevel() end)
      if not okd then break end
      steps = steps + 1
      local okl, v = pcall(function() return ih:getInputLevel() end)
      if not okl or type(v) ~= 'number' then break end
      level = v
    end
    _G.__qrStepsDown = steps
  end
  _G.__qrInputLevelAfter = level

  -- And focus, which is what actually routes input: the nav serving the CURRENT level has to be
  -- focused. This must run BEFORE the menu round trip, because `increaseInputLevel` marks the level it
  -- leaves as `pausedInputNavigations[old] = true` if that level's nav is not focused - and
  -- `decreaseInputLevel` then refuses to hand focus back to a paused level (inputHelper L169-171), so a
  -- level left unfocused going INTO the menu can never be re-focused coming out of it.
  if perLevel ~= nil and level ~= nil then
    local nav = perLevel[level]
    _G.__qrNavAtLevel = (nav == nil) and 'nothing registered' or 'present'
    if type(nav) == 'table' and type(nav.isFocused) == 'function' then
      local okf, focused = pcall(function() return nav:isFocused() end)
      if okf then _G.__qrNavAtLevelFocused = focused end
      -- `not focused`, NOT `focused == false`. isFocused() is `return <flag>` (focusBuilder L32-34), so
      -- an unfocused navigation can answer nil, and `nil == false` is false - the repair silently never
      -- ran. That is why `paused` was still `{1}` in the probe after this block: level 1 had not
      -- actually been re-focused, so the menu's increaseInputLevel marked it paused and the way out
      -- could not clear it. The game's own idiom is the truthiness test (combinedNavigationBuilder
      -- L22-23).
      if okf and not focused and type(nav.handleObtainFocus) == 'function' then
        _G.__qrStep = 'handing focus back to the nav at level ' .. tostring(level)
        if type(paused) == 'table' then paused[level] = nil end
        pcall(function() nav:handleObtainFocus() end)
        local ok2, after = pcall(function() return nav:isFocused() end)
        _G.__qrNavFocusedAfterRepair = ok2 and after or ('threw: ' .. tostring(after))
      end
    end
  end
end

_G.__qrStep = 'inputHelper:unblockInput'
_G.inputHelper:unblockInput()
_G.__qrStep = coromon1 and 'worldInterface:destroy + worldHelper:destroy'
                          or 'HexagonWorld:destroy'
if coromon1 then
  _G.worldInterface:destroy()
  _G.worldHelper:destroy()
else
  _G.HexagonWorld:destroy()
end
_G.__qrStep = 'playerStateHelper:quitCurrentGame'
ps:quitCurrentGame()

-- Final word on the level. The correction above runs once everything that releases a level has run,
-- but the world destroy and quitCurrentGame come after it, so this reapplies the target at the true
-- end - and in BOTH directions. Too high and input is routed to a level whose overlay is gone; too low
-- and it is routed to nothing at all. The target was captured earlier, while the world's own nav still
-- existed, because the lowest-registered-level reading it came from is not available any more once the
-- world is gone.
_G.__qrStep = 'settling the input level'
if type(_G.__qrFinalTarget) == 'number' and type(_G.inputHelper) == 'table'
   and type(_G.inputHelper.getInputLevel) == 'function' then
  local ih = _G.inputHelper
  local up, down = 0, 0
  local guard = 0
  while guard < 16 do
    local okc, cur = pcall(function() return ih:getInputLevel() end)
    if not okc or type(cur) ~= 'number' or cur == _G.__qrFinalTarget then break end
    if cur > _G.__qrFinalTarget and type(ih.decreaseInputLevel) == 'function' then
      if not pcall(function() ih:decreaseInputLevel() end) then break end
      down = down + 1
    elseif cur < _G.__qrFinalTarget and type(ih.increaseInputLevel) == 'function' then
      if not pcall(function() ih:increaseInputLevel() end) then break end
      up = up + 1
    else
      break
    end
    guard = guard + 1
  end
  _G.__qrFinalUp = up
  _G.__qrFinalDown = down
  local okv, v = pcall(function() return ih:getInputLevel() end)
  _G.__qrFinalLevel = okv and v or nil

  -- And focus, again, now that nothing else is going to move the level or the world. The earlier
  -- repair runs before worldInterface/worldHelper:destroy(), which is why in every measured run
  -- `nav[1] focused = false` right after the teardown and only the LOAD left it focused. Same repair,
  -- done at the true end instead.
  local perLevel, paused = nil, nil
  if type(debug) == 'table' and type(debug.getupvalue) == 'function' then
    local function collect(fn)
      if type(fn) ~= 'function' then return end
      local i = 1
      while true do
        local k, u = debug.getupvalue(fn, i)
        if k == nil then break end
        if k == 'inputNavigationPerInputLevel' and type(u) == 'table' then perLevel = u end
        if k == 'pausedInputNavigations' and type(u) == 'table' then paused = u end
        i = i + 1
      end
    end
    collect(ih.increaseInputLevel)
    collect(ih.decreaseInputLevel)
  end
  local nav = nil
  if okv and type(perLevel) == 'table' and type(v) == 'number' then nav = perLevel[v] end
  _G.__qrFinalNav = (nav == nil) and 'nothing registered' or 'present'
  if type(nav) == 'table' and type(nav.isFocused) == 'function'
     and type(nav.handleObtainFocus) == 'function' then
    local okq, q = pcall(function() return nav:isFocused() end)
    _G.__qrFinalNavFocused = okq and q or nil
    -- `not q`, not `q == false` - same reason as the repair above: isFocused() answers nil when it is
    -- unfocused, so `q == false` was never true and this repair never ran either.
    if okq and not q then
      if type(paused) == 'table' then paused[v] = nil end
      pcall(function() nav:handleObtainFocus() end)
      local okz, z = pcall(function() return nav:isFocused() end)
      _G.__qrFinalNavFocusedAfterRepair = okz and z or nil
    end
  end
end
_G.__qrStep = 'torn down'
end

-- CLOSE THE MENU. Both routes end in afterMenuReleased(); only the timing and the depth of the
-- teardown differ (see MENU_CLOSE_VIA_GAME). `closeIfCreated` is safe with nothing open - its `elseif
-- opt then opt()` branch calls the callback immediately (pauseMenu.lu proto (0,4) L31-37) - so this
-- is the same route whether or not the player had the menu up.
--
-- AND IT NEEDS A WATCHDOG. The game's close finishes in a transition's onComplete, and
-- `completeTransition` SKIPS the onComplete when the transition carries shouldBeCancelled
-- (transition.lu L26-31). So anything that cancels that 200 ms fade - a stray cancelAll, a
-- cancelRecursive reaching the menu's subtree - makes the callback never come, and then the REST OF
-- THIS TEARDOWN NEVER RUNS: the world stays up and the load would go into it. releaseOnce is the
-- guard, so a late callback cannot run the tail a second time.
local released = false
local function releaseOnce(why)
  if released then return end
  released = true
  _G.__qrMenuRelease = why
  afterMenuReleased()
end

_G.__qrStep = 'pauseMenu:closeIfCreated'
if _G.__qrMenuCloseViaGame and type(_G.pauseMenu.closeIfCreated) == 'function' then
  local okh, errh = pcall(function()
    _G.pauseMenu:closeIfCreated(function() releaseOnce('the close callback') end)
  end)
  if okh then
    _G.__qrMenuClosedVia = 'closeIfCreated'
    -- three times the fadeOut, so it only fires when the callback genuinely never came
    _G.timer.performWithDelay(600, function()
      releaseOnce('WATCHDOG - the close callback never came')
    end, 1)
  else
    -- A close that threw must not leave the rest of the teardown unrun: the world would stay up and
    -- the load would go into it.
    _G.__qrMenuClosedVia = 'closeIfCreated FAILED: ' .. tostring(errh)
    _G.pauseMenu:forceDestroyIfCreated()
    releaseOnce('closeIfCreated threw')
  end
else
  _G.pauseMenu:forceDestroyIfCreated()
  releaseOnce('forceDestroyIfCreated')
  _G.__qrMenuClosedVia = 'forceDestroyIfCreated'
end
'''

# The last two calls of the Quit button's sequence. They are what puts the game back on the main menu,
# and the reload now RUNS them rather than skipping them (see MENU_ROUND_TRIP below).
#
# Skipping them was the earlier design, and it was wrong twice. It was wrong first because stopping
# here leaves the game with no world and no screen. Then it was wrong in the other direction: creating
# the screen and taking it away again crashed the next attempt, with
#     CoromonLogo.lua:35 playSwurmySequence -> 'setSequenceAndPlay' (a nil value)
#       <- titleScreen.lua:200 playRandomSwurmySequences
#       <- titleScreen.lua:520 onComplete  <- transition.lua:29 completeTransition
# and the reason is a race that only the full timeline explains. `titleScreen:new()` ends by starting
# its own entrance, L512-517:
#     transition.fadeIn(parentGroup, 350, outQuad, <closure at L519-540>)
# that closure is `_onShow`, and _onShow starts two more transitions - 500 ms with a 150 ms delay, on
# `logo` and on `menuButtonsGroup` - whose completion is `playRandomSwurmySequences`. So the screen's
# timeline runs to 350 + 150 + 500 = ~1000 ms, while the leave preamble removes the group at ~350 ms.
#     new() at t=0 | cancelRecursive at t~304 | remove at t~354 | crash at t~1000
# Cancelling at leave time cannot fix that: at 300 ms the fadeIn has not completed, so the two
# transitions that actually crash HAVE NOT BEEN CREATED YET, and nothing can cancel a transition that
# does not exist. The answer is NOT to cancel it: `new()` ends with `inputHelper:blockInput()` (L479)
# and the entrance's completion is what calls `inputHelper:unblockInput()` (L522), so cancelling the
# entrance leaves input blocked for the rest of the session - buttons and mapped keys dead while polled
# movement still works. The entrance is therefore left to run, and leaveMenuThenLoad() waits on
# `inputHelper:isInputBlocked()` before taking the screen away.
#
# `timer.cancel('titleScreen')` was never going to help here: the entrance is a transition, not a
# timer. It IS still needed, for a different thing - the birds and the swurmy rolls are scheduled with
# `timer.performWithRandomDelay(..., 'titleScreen')` (L158-173, L199-205), and that is the key it
# cancels. (An earlier note here said input stays blocked until the intro has finished, which would
# have made this impossible for a player. That was wrong: nothing in `new()` blocks input - the only
# `blockInput` in the module is in the Play handler at L361 - so the screen's entrance is a timeline we
# are obliged to respect rather than one the game enforces.)
#
# The other half is now answered from the game too: the screen is LEFT the game's own way
# (titleScreen.lu L386-393, see leaveMenuThenLoad in RELOAD_BODY), so the input level it raised is
# lowered by the game's own decreaseInputLevel, and focus and pausedInputNavigations are unwound by the
# game's own code rather than by the four hand-rolled substitutes this file used to carry.
TEARDOWN_MENU = r'''
if type(_G.Achievement) == 'table' and
   _G.Achievement.resetPercentageOfMaxProgressCache ~= nil then
  _G.__qrStep = 'Achievement:resetPercentageOfMaxProgressCache'
  _G.Achievement:resetPercentageOfMaxProgressCache()
end
-- Recorded, NOT returned. In the reload this chunk sits in the middle of the body chunk, and a
-- `return` here would end the body before RELOAD_BODY ever ran - the load would never be queued and
-- the feature would sit `busy` forever, which is a worse failure than the one being guarded against.
-- So the title screen is skipped, the reason is written down, and leaveMenuThenLoad() sees
-- __qrTitleGroup == nil and loads straight from the teardown, exactly as the reload used to.
if type(_G.titleScreen) ~= 'table' or _G.titleScreen.new == nil then
  _G.__qrTitleGroup = nil
  _G.__qrStep = 'titleScreen:new is not there, so there is no menu to go back to'
  _G.__qrNote = _G.__qrStep .. ' - the load goes straight from the teardown'
else
  _G.__qrStep = 'titleScreen:new'
  -- keep what it returns. It IS the group the screen is built into and it IS what display.remove
  -- accepts - a reload reported "title group removed = true" - and it is the handle the title
  -- screen's OWN exit from the menu uses for its display.remove(parentGroup) (L389).
  _G.__qrTitleGroup = _G.titleScreen:new()
  -- WHAT JUST HAPPENED, because it decides how fast this can be. `new()` ends by calling
  -- `inputHelper:blockInput()` (L479, pc 825-827) and then starting its own entrance
  --     transition.fadeIn(parentGroup, 350, outQuad, <closure at L519-540>)        -- L512-517
  -- and the ENTRANCE'S FIRST ACTION is `inputHelper:unblockInput()` (L522). So the block and the
  -- unblock are a pair and the entrance's completion is the only thing that clears it. That is also
  -- why cancelling the entrance without putting the unblock back leaves input BLOCKED for the rest of
  -- the session - every touch, mouse and mapped-button dispatch suppressed, while movement keeps
  -- working because it is polled from the axes and never goes through that gate. That is exactly the
  -- "movement works, but no other keys do" report, and it appeared the moment the cancel was added.
  --
  -- Both halves of the pair have to be honoured, and CANCEL_MENU_ENTRANCE picks which way:
  --   cancel (default) - cancel the entrance HERE, the instant it is scheduled, and call
  --                      `inputHelper:unblockInput()` at the end of the leave step. This is what makes
  --                      the reload fast: the entrance's timeline is 350 + 150 + 500 = ~1000 ms and
  --                      skipping it skips all of it. Cancelling also removes the crash that started
  --                      this work - `CoromonLogo:playSwurmySequence` -> `setSequenceAndPlay` nil, from
  --                      the entrance's completion firing at ~1000 ms into a group removed at
  --                      ~354 ms - because a cancelled transition never runs its onComplete
  --                      (transition.lu L27). What else it defers is written down in
  --                      CANCEL_MENU_ENTRANCE's note: the settings-migration popups and
  --                      `optionallyShowKeyboardControls`, both of which belong to a menu this reload
  --                      deletes a moment later, so neither could have been shown anyway.
  --   wait            - leave the entrance alone and let leaveMenuThenLoad() poll
  --                      `inputHelper:isInputBlocked()`. The game's own route, ~1000 ms slower.
  if _G.__qrCancelEntrance then
    if type(_G.transition) == 'table' and type(_G.transition.cancelAll) == 'function' then
      local okc, errc = pcall(function() _G.transition.cancelAll() end)
      _G.__qrMenuEntrance = okc and 'entrance cancelled at the source; the leave step unblocks input'
                              or ('transition.cancelAll FAILED: ' .. tostring(errc))
    else
      _G.__qrMenuEntrance = 'no transition.cancelAll, so the entrance is still scheduled'
    end
  else
    _G.__qrMenuEntrance = 'left to run; the leave step waits for input to be unblocked'
  end
  _G.__qrStep = 'at the main menu'
end
'''

# what --teardown runs: the whole Quit button sequence, ending at the menu
TEARDOWN = TEARDOWN_BASE + TEARDOWN_MENU


# GO THROUGH THE MAIN MENU ON THE WAY TO THE LOAD, which is the point of the exercise.
#
# The teardown is the Quit button's sequence, verbatim: it destroys the world, calls quitCurrentGame and
# puts the title screen up. What it does NOT do is LEAVE the menu, and the game's own exit from the menu
# is five statements that do the input bookkeeping (see leaveMenuThenLoad in RELOAD_BODY). Skipping them
# is why this file had to hand-roll a level correction, a target derived from "the lowest level with a
# navigation registered", a focus repair in two places and a bidirectional settle - four substitutes for
# one game call, each of which has been wrong at least once.
#
# So the reload now runs the player's OWN route: quit to the menu, leave the menu, load. The level is
# raised by the game (titleScreen:new -> increaseInputLevel) and lowered by the game (the screen's own
# exit -> decreaseInputLevel), and the load happens from the state the game loads from.
#
# Set to False to go straight from the teardown to the load, which is what the reload did before this.
# One flag rather than a deleted path, because the measurement that decides between them is the
# `leaving the menu` / `left the menu` pair in the probe log: `lvl` has to come back down by exactly
# one. If it does not, this is the switch to flip back.
MENU_ROUND_TRIP = True

# WHAT HAPPENS TO THE TITLE SCREEN'S ENTRANCE, and it is the whole difference between a ~1.4 s reload
# and a ~0.25 s one. `titleScreen:new()` blocks input (L479) and then starts a ~1000 ms entrance
# (350 + 150 + 500, L512-517) whose completion is what unblocks it (L522).
#
#   True  - cancel the entrance the instant it is scheduled, and call `inputHelper:unblockInput()` at
#           the end of the leave step instead (leaveMenuThenLoad). Fast, and it cannot crash: a
#           cancelled transition never runs its onComplete, so the completion that used to fire into a
#           removed screen at ~1000 ms simply does not happen.
#   False - let the entrance run and wait on `inputHelper:isInputBlocked()`. The game's own route.
#
# What cancelling defers, stated so it is not a surprise later: the entrance's completion also drives
# the game-settings migration popups and `gameSettings:optionallyShowKeyboardControls`. Both are things
# the TITLE SCREEN shows, and this reload deletes the title screen a moment later, so neither could
# have been displayed anyway; they will appear on the next ordinary visit to the menu. Nothing else in
# the completion matters to a load - the rest is `playRandomSwurmySequences` (cosmetic, on a screen
# that is going away) and the title screen's own navigation taking focus, which we do not want.
CANCEL_MENU_ENTRANCE = True

# CLOSE THE PAUSE MENU THE GAME'S OWN WAY, instead of force-destroying it.
#
# `pauseMenu:closeIfCreated(cb)` is `if instance then instance:close(opt) elseif opt then opt() end`
# (proto (0,4) L31-37, read out of the bytecode, not assumed), and `close` (proto (0,6,15) L246-268) is a
# complete teardown that `forceDestroy` is not:
#
#   close(cb)  blockInput -> playSound('menuBack') -> menuBuilder.close(self, inner)
#                inner: beforeClose()                       [runs onBeforeCloseFunctions]
#                       topBar:hide(); bottomBar:hide()
#                       transition.fadeOut(getBottomGroup(), 200, outQuad, removeSelf, done)
#                         done:  unblockInput(); afterClose(); if cb then cb() end
#     afterClose (proto (0,6,20) L299-317):
#                       decreaseInputLevelIfNotAlreadyDone()   <- guarded by didDecreaseInputLevel
#                       fadeMusicVolumeModifier(200, 1.0)
#                       destroyInstance()
#                       onAfterCloseFunctions()
#                       topBar:onDestroy(); bottomBar:onDestroy()
#                       eventManager:dispatch('pauseMenuClosed')
#
# and menuBuilder.close -> unloadCurrentScreen(true, cb) runs the mounted SCREEN's outTransition and
# clears it. The pause menu is a screen stack - menuBuilder keeps `history`, `currentScreenObject`,
# `currentScreenName`, and the items/squad/settings pages are screens - so `forceDestroy`, which is only
# `cancelRecursive(self) + onUnloadScreen() + afterClose()`, leaves the current page mounted with its own
# subscriptions alive. That is the measured fault: the items page visible on top of the pause menu, and
# a stale nav bar still listening to the navigation-method event, which is what magnet.lua:41 dies on
# when alt-tab changes the input method.
#
# It also REPLACES OUR OWN BOOKKEEPING. Because afterClose does the guarded
# decreaseInputLevelIfNotAlreadyDone, a separate `decreaseInputLevel()` of ours is a SECOND decrement on
# the same level, and that call has no lower bound (inputHelper L162-173). The leave step's own level
# surgery is therefore skipped when this is on.
#
# It implies the round trip: `close` supplies the input bookkeeping the title screen was being used to
# borrow, so creating the screen and taking it away again is not needed. USE_ROUND_TRIP below enforces
# that, and the ~350 ms entrance wait goes with it.
#
# True  - close the menu with the game's own close and run the rest of the teardown in its callback.
# False - force-destroy it and run the rest immediately, which is the previous behaviour exactly.
MENU_CLOSE_VIA_GAME = True

# The route actually taken. With the game's close there is nothing for the round trip to borrow, and the
# title screen's entrance is not started (so there is nothing to cancel either).
USE_ROUND_TRIP = MENU_ROUND_TRIP and not MENU_CLOSE_VIA_GAME

# BODY itself is composed below RELOAD_BODY, which it needs.


def run_guard(run_id):
    """Prefix that makes a MUTATING chunk safe to be run twice.

    `ask` re-sends a chunk whenever the bridge drops the answer, and this file's chunks are not
    reads: one of them tears the game down and loads a save. A dropped answer therefore ran the
    whole thing a second time - a second teardown, a second poll timer and a second loadGame - and
    that is the most likely explanation for the reload reporting `step = waiting` alongside
    `load = ok` and then freezing. So every run carries an id, the id is written down before
    anything happens, and a repeat of the same id does nothing.
    """
    return (
        "local runId = %s\n"
        "if _G.__qrRan == runId then\n"
        "  return 'a resend of this same run, so nothing was done again - its report was: ' ..\n"
        "         tostring(_G.__qrReport)\n"
        "end\n"
        "_G.__qrRan = runId\n"
        "_G.__qrLog = {}\n"
        "_G.__qrWhy = nil\n"
        "_G.__qrReport = nil\n" % lua_str(run_id)
    )


def stop_transitions(bridge, run_id):
    """Flag every in-flight transition for cancellation, then let the game run a few frames.

    The wait is not decoration and this cannot be folded into the teardown chunk. `cancelAll` only
    sets `shouldBeCancelled`; the transition module acts on it from its own enterFrame listener, and
    `completeTransition` is what skips the onComplete. Closing anything before that tick has run
    reproduces the crash this is here to prevent. See TRANSITION_CANCEL.
    """
    out = ask(bridge, run_guard(run_id) + TRANSITION_CANCEL)
    if out is None:
        log("  in-flight transitions: the game did not answer, carrying on anyway")
    else:
        log("  in-flight transitions: " + out)
    time.sleep(SETTLE)
    return out


# Read-only probe. `cancelAll` (lines 386-390) and `isTransitioning` (326-332) both close over the
# transition module's own `transitions` list, so its upvalue names it and debug.getupvalue can reach
# it. Knowing what is ACTUALLY in flight, and whether it was flagged, is the difference between
# reasoning about this and guessing about it - and guessing has been wrong twice.
TRANSITIONS = r'''
local tr = _G.transition
if type(tr) ~= 'table' then return 'there is no transition module' end
local shape = 'to=' .. tostring(type(tr.to) == 'function') ..
              ' cancel=' .. tostring(type(tr.cancel) == 'function') ..
              ' cancelAll=' .. tostring(type(tr.cancelAll) == 'function')
if type(debug) ~= 'table' or type(debug.getupvalue) ~= 'function' then
  return shape .. ' and no debug.getupvalue to read the list with'
end
local function probe(fn)
  if type(fn) ~= 'function' then return nil end
  local nm, val = debug.getupvalue(fn, 1)
  if nm == 'transitions' and type(val) == 'table' then return val end
  return nil
end
local list = probe(tr.cancelAll) or probe(tr.isTransitioning) or probe(tr.cancelRecursive)
if list == nil then
  return shape .. ' but the transitions list could not be reached'
end
local parts = {}
for i = 1, #list do
  local e = list[i]
  local where = '?'
  if type(e) == 'table' then
    local t = e.target
    if type(t) == 'table' then
      where = tostring(t.name or t.id or t.classname or 'display object')
      if type(t.class) == 'table' and t.class.name then
        where = where .. '<' .. tostring(t.class.name) .. '>'
      end
    elseif t ~= nil then
      where = tostring(t)
    end
  end
  parts[#parts + 1] = string.format('%d %s dur=%s paused=%s flag=%s', i, where,
    tostring(type(e) == 'table' and e.duration),
    tostring(type(e) == 'table' and e.pauseTime),
    tostring(type(e) == 'table' and e.shouldBeCancelled))
end
_G.__qrTrans = #list
return string.format('%d in flight (%s) [%s]', #list, shape, table.concat(parts, ' | '))
'''


def sample_transitions(bridge, when):
    """Read-only, so deliberately NO run_guard: re-sending it is harmless, and a guard would wipe
    `__qrLog`, which the load poll reads."""
    out = ask(bridge, TRANSITIONS)
    log("  transitions %s: %s" % (when, out if out is not None else "no answer"))
    return out


# Read-only. Everything that decides whether input works lives in inputHelper's UPVALUES, not in its
# exports, so this reads them with debug.getupvalue rather than guessing:
#
#   currentInputLevel           - the counter. >0 IS "controls disabled, like when there is a dialog".
#                                 `inputHelper:getInputLevel()` returns it.
#   isInputBlocked              - a plain boolean, set by blockInput, cleared by unblockInput.
#                                 NOTE `inputHelper:isInputBlocked(level)` is a QUERY that compares
#                                 currentInputLevel == level, so calling it with no argument is always
#                                 false - which is why the flag is read here, not through the method.
#   inputNavigationPerInputLevel - the navigation object serving each level. Input is ROUTED through
#                                 this, so a level of 0 with the level-0 nav unfocused is ALSO dead
#                                 input, with nothing obviously wrong to look at.
#   pausedInputNavigations      - levels whose focus was taken away and not yet handed back.
#                                 decreaseInputLevel only re-focuses the level below when this is
#                                 CLEAR:
#                                     if nav2 and not pausedInputNavigations[currentInputLevel] then
#                                       nav2:handleObtainFocus()
#                                       pausedInputNavigations[currentInputLevel] = nil
#                                 so a level left paused means focus is never handed back.
INPUT_DIAG = r'''
local ih = _G.inputHelper
if type(ih) ~= 'table' then return 'there is no inputHelper' end
if type(debug) ~= 'table' or type(debug.getupvalue) ~= 'function' then
  return 'no debug.getupvalue to read inputHelper with'
end
local out = {}
local perLevel, paused = nil, nil
local function read(fn, want)
  local found = nil
  if type(fn) ~= 'function' then return nil end
  local i = 1
  while true do
    local k, v = debug.getupvalue(fn, i)
    if k == nil then break end
    if k == want then found = v end
    if k == 'inputNavigationPerInputLevel' and type(v) == 'table' then perLevel = v end
    if k == 'pausedInputNavigations' and type(v) == 'table' then paused = v end
    i = i + 1
  end
  return found
end
out[#out + 1] = 'level = ' .. tostring(read(ih.getInputLevel, 'currentInputLevel'))
out[#out + 1] = 'blocked = ' .. tostring(read(ih.unblockInput, 'isInputBlocked'))
read(ih.increaseInputLevel, 'currentInputLevel')
read(ih.decreaseInputLevel, 'currentInputLevel')
if type(perLevel) ~= 'table' then
  out[#out + 1] = 'no inputNavigationPerInputLevel'
else
  local keys = {}
  for k in pairs(perLevel) do keys[#keys + 1] = k end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  if #keys == 0 then out[#out + 1] = 'no navigations registered' end
  for _, k in ipairs(keys) do
    local nav = perLevel[k]
    local focused = 'n/a'
    if type(nav) == 'table' and type(nav.isFocused) == 'function' then
      local ok, r = pcall(function() return nav:isFocused() end)
      focused = ok and tostring(r) or ('threw: ' .. tostring(r))
    end
    out[#out + 1] = 'nav[' .. tostring(k) .. '] focused = ' .. focused
  end
end
if type(paused) == 'table' then
  local keys = {}
  for k in pairs(paused) do keys[#keys + 1] = k end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  if #keys == 0 then out[#out + 1] = 'none paused' end
  for _, k in ipairs(keys) do
    out[#out + 1] = 'paused[' .. tostring(k) .. '] = ' .. tostring(paused[k])
  end
end
_G.__qrInputDiag = table.concat(out, ' | ')
return _G.__qrInputDiag
'''


def sample_input(bridge, when):
    """Read-only, same reasoning as sample_transitions: no run_guard."""
    out = ask(bridge, INPUT_DIAG)
    log("  input %s: %s" % (when, out if out is not None else "no answer"))
    return out


# Only for --teardown. Running it from the main menu is not harmless: every run calls
# `titleScreen:new()`, so three runs stack three title screens. If the world is already gone there is
# nothing to tear down, so say so instead.
TEARDOWN_GUARD = r'''
if type(_G.worldHelper) == 'table' and type(_G.worldHelper.isCreated) == 'function' then
  local okw, created = pcall(function() return _G.worldHelper:isCreated() end)
  if okw and created == false then
    _G.__qrStep = 'nothing to tear down: the world is already gone'
    _G.__qrReport = _G.__qrStep
    return _G.__qrReport
  end
end
'''

# The cancel belongs with the guard, and the guard goes FIRST: from the main menu there is nothing to
# tear down, and cancelling there would stop the title screen's own intro transition for no reason.
TEARDOWN_PREPARE = TEARDOWN_GUARD + TRANSITION_CANCEL


def do_teardown():
    """The game's own quit sequence, stopping at the main menu.

    Worth having on its own: "press a button and be back at the main menu" is a result in itself,
    it is the sequence the game runs when the Quit button is pressed, and it is the state a load
    is done from - so it is the right thing to get working before any load is attempted.
    """
    bridge = _bridge()
    prep = ask(bridge, run_guard("teardown-prep-%s" % time.time()) + TEARDOWN_PREPARE)
    if prep is None:
        log("the game did not answer")
        return 1
    # the only answer that means "do not go on" is the guard's; a cancel that failed is
    # reported and the teardown still runs, because a teardown without a cancel is what this
    # did before the cancel existed at all.
    if prep.startswith("nothing to tear down"):
        log(prep)
        return 0
    log("  in-flight transitions: " + prep)
    time.sleep(SETTLE)

    code = run_guard("teardown-%s" % time.time()) + TEARDOWN + r'''
_G.__qrReport = 'teardown finished, step = ' .. tostring(_G.__qrStep)
return _G.__qrReport
'''
    log(ask(bridge, code) or "the game did not answer")
    return 0


def device_id():
    """The device id the save was filed under, from the store. `loadGame` only stores it, and the
    game's own debug loader passes the literal 'self', so this is a nicety rather than a need."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % PREFS_DB.replace("\\", "/"), uri=True)
        try:
            row = con.execute("select value from preference where key='deviceIds'").fetchone()
        finally:
            con.close()
        ids = json.loads(row[0]) if row else []
        return ids[0] if ids else "self"
    except Exception:
        return "self"


def inject(slot=None, device=None):
    """The two values a reload needs that come from outside the game."""
    return ("_G.__qrSlotArg = %s\n_G.__qrDeviceId = %s\n"
            % (slot if isinstance(slot, int) and slot > 0 else "nil",
               lua_str(device or "self")))


PREFLIGHT = r'''
local ps = _G.playerStateHelper
if type(ps) ~= 'table' then return 'playerStateHelper is not there' end
if type(_G.SaveslotPreferences) ~= 'table' then return 'SaveslotPreferences is not there' end
if type(_G.saveslotDataHelper) ~= 'table' then return 'saveslotDataHelper is not there' end
if type(_G.worldHelper) ~= 'table' or type(_G.worldHelper.isCreated) ~= 'function' then
  return 'worldHelper:isCreated() is not there, so the teardown cannot be gated'
end
local created = nil
pcall(function() created = _G.worldHelper:isCreated() end)
if created ~= true then
  return 'there is no world to tear down (isCreated = ' .. tostring(created) ..
         ') - be in game first'
end

-- NOTE: this used to refuse while a conversation was open. It does not any more, because the
-- reload flags every in-flight transition for cancellation before the teardown closes anything,
-- which is what actually stops the crash - a queued button press being delivered to a dialogue that
-- had already been closed. That is `transition.cancelAll()` in its own chunk, and it needs its own
-- chunk because it only sets a flag that the transition module acts on at its next frame tick
-- (TRANSITION_CANCEL). Refusing was the safe stopgap; the cancel is the fix. If a conversation ever
-- crashes the game again, the guard is the thing to put back.

-- read BEFORE the teardown, because quitCurrentGame sets it to nil
local slot = _G.__qrSlotArg
if slot == nil then
  local ok1, v = pcall(function() return ps:getSelectedSaveslotIndex() end)
  if not ok1 or type(v) ~= 'number' then
    return 'the game did not say which slot it is in (' .. tostring(v) ..
           ') - give one: --reload 1'
  end
  slot = v
end
return 'ok ' .. tostring(slot)
'''


RELOAD_BODY = r'''
_G.__qrDone = nil
_G.__qrStep = 'starting'
local slot = _G.__qrSlotArg
local deviceId = _G.__qrDeviceId or 'self'
local prefs = _G.SaveslotPreferences
local helper = _G.saveslotDataHelper

local report = {
  'torn down, no title screen',
  'slot ' .. tostring(slot) .. ', deviceId ' .. tostring(deviceId),
  'the save comes from the store, not from a captured table',
}

-- so a failure after the world is gone does not leave a black screen behind
local function restoreMenu(why)
  _G.__qrNote = 'the load did not happen (' .. why .. '), so the main menu was put back'
  local okm, errm = pcall(function()
    if type(_G.Achievement) == 'table' and
       _G.Achievement.resetPercentageOfMaxProgressCache ~= nil then
      _G.Achievement:resetPercentageOfMaxProgressCache()
    end
    if type(_G.titleScreen) == 'table' and _G.titleScreen.new ~= nil then
      _G.__qrTitleGroup = _G.titleScreen:new()
    end
  end)
  _G.__qrRecovered = okm
  if not okm then
    _G.__qrNote = _G.__qrNote .. ' (and that failed too: ' .. tostring(errm) .. ')'
  end
end

if type(_G.timer) ~= 'table' or type(_G.timer.performWithDelay) ~= 'function' then
  restoreMenu('no timer to wait on')
  return table.concat(report, ' | ') .. ' | no timer, so the load was NOT queued'
end

-- The body is its own chunk, so it cannot see the section's `qrLog`; the teardown's log table is the
-- one thing they share.
local function say(line)
  _G.__qrLog[#_G.__qrLog + 1] = tostring(line)
  if #_G.__qrLog > 60 then table.remove(_G.__qrLog, 1) end
end

-- The level, and the two tables the level is bookkept in. Read-only, and read through
-- debug.getupvalue because `inputNavigationPerInputLevel` and `pausedInputNavigations` are closure
-- upvalues of increase/decreaseInputLevel rather than fields (inputHelper L143-144).
local function levelSnapshot()
  local ih = _G.inputHelper
  local out = { level = nil, navs = nil, paused = nil }
  if type(ih) ~= 'table' then return out end
  local ok, v = pcall(function() return ih:getInputLevel() end)
  if ok and type(v) == 'number' then out.level = v end
  local per, paused = nil, nil
  if type(debug) == 'table' and type(debug.getupvalue) == 'function' then
    local function collect(fn)
      if type(fn) ~= 'function' then return end
      for i = 1, 16 do
        local nm, u = debug.getupvalue(fn, i)
        if nm == nil then break end
        if nm == 'inputNavigationPerInputLevel' and type(u) == 'table' then per = u end
        if nm == 'pausedInputNavigations' and type(u) == 'table' then paused = u end
      end
    end
    collect(ih.increaseInputLevel)
    collect(ih.decreaseInputLevel)
  end
  local function keysOf(t, truthyOnly)
    if type(t) ~= 'table' then return '?' end
    local ks = {}
    for k, val in pairs(t) do
      if val ~= nil and (not truthyOnly or val) then ks[#ks + 1] = tostring(k) end
    end
    table.sort(ks)
    return '{' .. table.concat(ks, ',') .. '}'
  end
  out.navs = keysOf(per, false)
  out.paused = keysOf(paused, true)
  return out
end

local function levelLine(p)
  return string.format('lvl=%s navs=%s paused=%s',
    tostring(p.level), tostring(p.navs), tostring(p.paused))
end

-- The KEY path's state, which is a different machine from the level/axis one. Movement is routed by
-- the navigation at the current level and arrives as direction/axis input, so it survives anything
-- that only touches key events - which is exactly why "movement works, nothing else does" points here.
-- `shouldKeyEventDetectUnknownGamepads` is an upvalue of its own setter (inputHelper.lu L777-779), and
-- the two flags are read by the key handler at L881-888, whose shape for an UNKNOWN device is:
--
--   if shouldKeyEventAcceptFallbackForUnknownGamepads then
--     handleKeyOrMappedButton(t, fallbackButtonNameByKeyName[keyName], ...)   -- L883, buttons work
--   elseif shouldKeyEventDetectUnknownGamepads then
--     handleKeyOrMappedButton(t, nil, ...)                                  -- L885, resolve the device
--   else                                                                     -- L886-888, nothing
--
-- so for an unknown pad, buttons work only with one of those flags set. Read-only.
local function keyFlag(fn, name)
  if type(fn) ~= 'function' or type(debug) ~= 'table'
     or type(debug.getupvalue) ~= 'function' then
    return '?'
  end
  for i = 1, 8 do
    local k, v = debug.getupvalue(fn, i)
    if k == nil then break end
    if k == name then return tostring(v) end
  end
  return '?'
end

local function keyState()
  local ih = _G.inputHelper
  local out = {}
  if type(ih) ~= 'table' then return 'no inputHelper' end
  out[#out + 1] = 'detect=' .. keyFlag(ih.setKeyEventShouldDetectUnknownGamepads,
                                       'shouldKeyEventDetectUnknownGamepads')
  out[#out + 1] = 'fallback=' .. keyFlag(ih.setKeyEventShouldAcceptFallbackForUnknownGamepads,
                                         'shouldKeyEventAcceptFallbackForUnknownGamepads')
  local function call(name)
    if type(ih[name]) ~= 'function' then return '?' end
    local ok, v = pcall(function() return ih[name](ih) end)
    if not ok then return '?' end
    if type(v) == 'table' then return tostring(v.productName or v.displayName or 'table') end
    return tostring(v)
  end
  out[#out + 1] = 'device=' .. call('getCurrentDevice')
  out[#out + 1] = 'usingGamepad=' .. call('isUsingGamepad')
  out[#out + 1] = 'usingKeyboard=' .. call('isUsingKeyboard')
  out[#out + 1] = 'gamepadNav=' .. call('isGamepadNavigationSelected')
  out[#out + 1] = 'keyboardNav=' .. call('isKeyboardNavigationSelected')
  return table.concat(out, ' ')
end

local function finish()
  -- The game's own debug loader, verbatim (debug_load_saveslot lines 50-51).
  _G.__qrStep = 'reading slot ' .. tostring(slot) .. ' out of the store'
  local ok2, err = pcall(function()
    local row = prefs:get(nil, slot, false)
    local data = helper:updateDataToNewestVersion(helper:decrypt(row.encryptedData), 1)
    local keys = 0
    for _ in pairs(data) do keys = keys + 1 end
    _G.__qrLoadedKeys = keys
    _G.__qrDataSlot = data.selectedSaveslotIndex
    _G.__qrHasMainChar = type(data.constructorList) == 'table'
                         and data.constructorList.maincharacter ~= nil
    _G.__qrStep = 'loadGame'
    ps:loadGame(data, { deviceId = deviceId }, nil, nil, 'instant', nil)
  end)
  if ok2 then
    _G.__qrDone = 'ok'
  else
    _G.__qrDone = 'threw: ' .. tostring(err)
    restoreMenu('loadGame threw')
  end
  -- HOW LONG THE RELOAD TOOK, from the press to the load being issued. `system.getTimer()` is ms since
  -- the app started, monotonic, and `__qrT0` is set in the feature's fire(). This is the number that
  -- says whether a speed-up was real, and it includes every wait in the chain (the pre-body frame, the
  -- world-teardown poll, the entrance) so no single one can quietly grow back.
  if type(_G.system) == 'table' and type(_G.system.getTimer) == 'function'
     and type(_G.__qrT0) == 'number' then
    local okt, now = pcall(function() return _G.system.getTimer() end)
    if okt and type(now) == 'number' then
      _G.__qrLoadMs = now - _G.__qrT0
      say(string.format('    load issued: %d ms after the press', _G.__qrLoadMs))
    end
  end
  _G.__qrStep = 'finished'
end

-- LEAVING THE MAIN MENU, THE GAME'S OWN WAY. Everything below is titleScreen.lu proto
-- 0.2.12.0.0.1.0.0, lines 386-393, in the game's own order. This is the half of the flow that was
-- missing: the teardown goes to the menu (the Quit button's own sequence, verbatim) and the game then
-- leaves the menu for the world. Those five statements are the input bookkeeping, not decoration -
-- `decreaseInputLevel` is the ONLY thing that clears inputNavigationPerInputLevel[current] (L166) and
-- the only thing that hands focus back to the level below (L169-171), and both it and
-- increaseInputLevel call inputHelper:releaseInput() (L154, L163), which is what drops a press still
-- held in inputHelper's bookkeeping.
--
-- The one addition is transition.cancelRecursive before the group is removed - harmless (it is a
-- display-object walk and __qrTitleGroup is a container object, so it finds nothing here), kept only
-- as belt and braces now that the entrance is waited out rather than cancelled.
local function leaveMenuThenLoad()
  if not _G.__qrMenuRoundTrip or _G.__qrTitleGroup == nil then
    _G.__qrLeaveSteps = _G.__qrMenuRoundTrip and 'no title group to leave'
                        or 'skipped - not going through the menu'
    -- WHICH ROUTE CLOSED THE MENU, because that is the one line that says whether the game own close
    -- ran or whether we fell back to force-destroying it.
    say('  menu closed by:  ' .. tostring(_G.__qrMenuClosedVia or 'not closed here') ..
        '  (' .. _G.__qrLeaveSteps .. ')')
    finish()
    return
  end
  _G.__qrStep = 'waiting for the menu entrance'
  local before = levelSnapshot()
  _G.__qrLevelBeforeLeave = levelLine(before)
  _G.__qrKeysBeforeLeave = keyState()
  say('  leaving the menu: ' .. _G.__qrLevelBeforeLeave .. '  (' ..
      tostring(_G.__qrMenuEntrance or 'the entrance state was not recorded') .. ')')
  say('    keys before: ' .. _G.__qrKeysBeforeLeave)

  -- WAIT FOR THE ENTRANCE. `titleScreen:new()` blocks input (L479) and the entrance's completion
  -- unblocks it (L522), so isInputBlocked() going false IS "the screen has finished coming up".
  -- Leaving before that has two consequences, both measured: the screen's own onCompletes fire into a
  -- group that has been removed (the CoromonLogo crash), and input stays blocked (movement works, no
  -- other keys do). The bound is a backstop for a build where the flag never clears, not a timing
  -- guess - what it normally waits for is the transition itself, and the line below records the real
  -- number so it can be checked rather than assumed.
  local waited = 0
  local function entranceDone()
    if type(_G.inputHelper) ~= 'table'
       or type(_G.inputHelper.isInputBlocked) ~= 'function' then
      return nil
    end
    local ok, v = pcall(function() return _G.inputHelper:isInputBlocked() end)
    return ok and v or nil
  end
  local function attempt()
    local blocked = entranceDone()
    -- In cancel mode there is no entrance left to wait for - it never runs - but the flag is still
    -- read once, because it is the evidence that the unblock at the end of this step was needed.
    if not _G.__qrCancelEntrance and blocked == true and waited < 3000 then
      waited = waited + 50
      _G.timer.performWithDelay(50, attempt, 1)
      return
    end
    _G.__qrEntranceWait = string.format('%d ms (blocked=%s)', waited, tostring(blocked))
    say('    entrance: ' .. _G.__qrEntranceWait)
    -- cancelRecursive is belt and braces only - see above. It returns silently when handed a
    -- non-display-object (transition.lu L393), which is why the log records that too.
    local okcanc, errcanc = pcall(function()
      _G.transition.cancelRecursive(_G.__qrTitleGroup)
    end)
    _G.__qrLeaveCancel = okcanc and 'cancelled' or ('FAILED: ' .. tostring(errcanc))
    _G.timer.performWithDelay(50, function()
    local steps = {}
    local function step(what, f)
      local oks, errs = pcall(f)
      steps[#steps + 1] = what .. (oks and '=ok' or ('=FAILED:' .. tostring(errs)))
    end
    -- whether __qrTitleGroup is a display object at all. It matters because transition.cancelRecursive
    -- returns SILENTLY when it is not (transition.lu L393: `if not display.isDisplayObject(_g) then
    -- return end`), so a logged `cancelled` meant nothing - and the probe log confirms it is false
    -- (__qrTitleGroup is the container object rectHelper.newContainerObject returns, not a group).
    local isDisp = false
    if type(_G.display) == 'table' and type(_G.display.isDisplayObject) == 'function' then
      local okd, v = pcall(function() return _G.display.isDisplayObject(_G.__qrTitleGroup) end)
      isDisp = okd and v or false
    end
    step('cancelRecursive(displayObject=' .. tostring(isDisp) .. ')', function()
      _G.transition.cancelRecursive(_G.__qrTitleGroup)
    end)
    step('timer.cancel', function() _G.timer.cancel('titleScreen') end)
    step('display.remove', function() _G.display.remove(_G.__qrTitleGroup) end)
    step('pauseMenu:forceDestroyIfCreated',
         function() _G.pauseMenu:forceDestroyIfCreated() end)
    step('decreaseInputLevel', function() _G.inputHelper:decreaseInputLevel() end)
    -- NOT `setKeyEventShouldDetectUnknownGamepads(false)`, and leaving it out is deliberate.
    --
    -- The game does call it false on the way into a world (titleScreen.lu L393, and L55 in its
    -- leave-the-screen helper), but it can afford to: the menu was up with the flag at its default
    -- TRUE (set by `new()`, L31), so the player's first button press ran the detect branch at
    -- inputHelper L885 and RESOLVED the device. Once the device is known, the whole unknown-device
    -- branch (L881-888) is not taken at all and the flag stops mattering.
    --
    -- A reload does not give the menu that button press - there is no player in the loop - so if the
    -- pad is still unknown when we set the flag false, every button key event from it afterwards
    -- falls through to the `else` at L886-888 and is DROPPED, while the stick keeps working because
    -- axes are not key events. That is precisely the report: "movement works, but no other keys do".
    --
    -- So the flag is left exactly as `titleScreen:new()` set it, which is also the state the game is
    -- in for the entire time the menu is up. The keys line below records what it actually is, so this
    -- can be checked rather than trusted.
    if _G.__qrCancelEntrance then
      -- the entrance was already cancelled in TEARDOWN_MENU; calling unblockInput here, at the very end
      -- of the leave step, is putting back the one thing that entrance would have done that matters.
      -- Without it input stays blocked and every button and mapped key is dead while the stick still
      -- moves - the failure that cost a session.
      step('unblockInput', function() _G.inputHelper:unblockInput() end)
    end
    _G.__qrLeaveSteps = table.concat(steps, ' ')
    local after = levelSnapshot()
    _G.__qrLevelAfterLeave = levelLine(after)
    -- The title screen raised the level when it was created, so leaving it should take the level back
    -- down by exactly one. This line is the check on the whole idea: if `lvl` does not come back down,
    -- the pair did not balance and the hand-rolled settle has to come back on.
    say('  left the menu:    ' .. _G.__qrLevelAfterLeave .. '  (' .. _G.__qrLeaveSteps .. ')')
    _G.__qrKeysAfterLeave = keyState()
    say('    keys after:  ' .. _G.__qrKeysAfterLeave)
    _G.__qrStep = 'left the menu, loading'
    finish()
    end, 1)
  end
  -- the entrance poll starts one frame in, so the very first check is not made before the fadeIn exists
  _G.timer.performWithDelay(50, attempt, 1)
end
-- NOT a fixed delay. `worldHelper:destroy()` is only half done when it returns: it ends with
-- `nextFrame(function() instance = display.remove(instance); setmetatable(t, nil) end)`. Loading
-- before that frame runs lets the deferred `destroyInstance` nil the instance that `createInstance`
-- has just built, and the MTE:loadMap callback then dies on `instance:insert(...)` at line 286.
-- `isCreated()` is `return instance ~= nil` and a direct field of the module, so it still answers
-- after the metatable is stripped - it is the signal that the deferred half has run.
local pollTimer
local waited = 0
local stopped = false
local noGate = type(_G.worldHelper) ~= 'table' or type(_G.worldHelper.isCreated) ~= 'function'
if noGate then
  _G.__qrNote = 'worldHelper:isCreated() is not there, waiting a fixed 250 ms instead'
end

local function poll()
  -- a cancel that did not take must not let this run on after the load
  if stopped then return end
  waited = waited + 16
  local created = true
  if not noGate then
    local ok3, v = pcall(function() return _G.worldHelper:isCreated() end)
    if ok3 then created = v end
  end
  _G.__qrLog[#_G.__qrLog + 1] = tostring(waited) .. 'ms isCreated=' .. tostring(created)
  if #_G.__qrLog > 40 then table.remove(_G.__qrLog, 1) end

  local settled = (not noGate) and (created == false)
  -- `isCreated()` going false IS the deferred half of `worldHelper:destroy()` having run - that is what
  -- nils the instance - so the signal alone is enough and the floor is only "let one more frame pass"
  -- (16 ms is one frame; 64 is four, which costs nothing next to the ~1000 ms the entrance used to).
  -- It was 300 ms, which was pure padding: the log shows isCreated=false from 16 ms on every run, so
  -- 284 ms of every reload was spent waiting for nothing. The 5 s cap and the no-signal fallback are
  -- unchanged.
  if (settled and waited >= 64) or waited >= 5000 or (noGate and waited >= 250) then
    stopped = true
    if pollTimer then pcall(function() pollTimer:cancel() end) end
    _G.__qrWhy = settled and ('isCreated() went false after ' .. tostring(waited) .. ' ms')
                 or (noGate and 'no isCreated() to wait on, so a fixed wait'
                     or 'timed out after 5 s with isCreated() still true')
    _G.__qrStep = 'waiting stopped after ' .. tostring(waited) .. ' ms'
    if (settled and waited >= 64) or (noGate and waited >= 250) then
      leaveMenuThenLoad()
    else
      restoreMenu('worldHelper:isCreated() never went false')
    end
  else
    _G.__qrStep = 'waiting, isCreated = ' .. tostring(created) ..
                  ', ' .. tostring(waited) .. ' ms'
  end
end
pollTimer = _G.timer.performWithDelay(16, poll, 0)

_G.__qrReport = table.concat(report, ' | ') ..
                ' | waiting for the teardown to finish, then loading'
return _G.__qrReport
'''

# what the reload runs: the Quit button's sequence, then the title screen's own exit from the menu,
# then the load. The flag goes at the top of the chunk because TEARDOWN_BASE reads it to decide
# whether to do its own level surgery.
BODY = (('_G.__qrMenuCloseViaGame = true\n' if MENU_CLOSE_VIA_GAME else '')
        + (('_G.__qrMenuRoundTrip = true\n'
            + ('_G.__qrCancelEntrance = true\n' if CANCEL_MENU_ENTRANCE else ''))
           if USE_ROUND_TRIP else '')
        + TEARDOWN_BASE
        + (TEARDOWN_MENU if USE_ROUND_TRIP else '')
        + RELOAD_BODY)


def do_reload(slot=None, bridge=None):
    """Tear the current game down and load a slot back, using only the game's own code.

    pre-flight  read-only: is there a world, are the globals there, which slot are we in
    teardown    the Quit button's sequence UP TO the title screen
    wait        until worldHelper:isCreated() is false, so the deferred destroyInstance has run
    load        the debug loader's own three lines: read the slot out of the preferences, bring
                it up to the current data version, then loadGame with 'instant' and no callback

The pre-flight is separate from the teardown on purpose, and that ordering is the whole point.
Reading the slot index AFTER the teardown does not work: `quitCurrentGame` ends with
`selectedSaveslotIndex = nil`, so the value being asked for had just been cleared by our own
teardown - which is exactly what happened, and since a reload's teardown deliberately creates no
title screen, bailing out there left no world AND no menu, i.e. a black screen. Nothing is
destroyed now until every input is known good, and if the load still fails the menu is put back.

Not done, and why. No title screen is created for the load: a reload wants a world, not a menu, and
the screen's intro transition completes AFTER it has been removed and calls into a destroyed
sprite. And arguments are not replayed from a captured menu load: the game empties
`constructorList` while building the world, so a captured table is a reference to something that
has changed since, and loadGame then dies on `saveslotDataHelper`'s
`constructorList.maincharacter.mapPath`.
"""
    bridge = bridge or _bridge()
    pre = ask(bridge, run_guard("preflight-%s" % time.time())
              + inject(slot, None) + PREFLIGHT)
    if pre is not None and pre.startswith("ok "):
        log("pre-flight: " + pre)
        if not pre.startswith("ok "):
            log("nothing was torn down")
            return 1
        picked = int(pre.split()[1])
        log("")
    else:
        log("the game did not answer")
        return 1

    sample_transitions(bridge, "before the cancel")
    sample_input(bridge, "before the cancel")
    stop_transitions(bridge, "cancel-%s" % time.time())
    sample_transitions(bridge, "after the settle")

    code = (run_guard("reload-%s" % time.time()) + inject(picked, device_id())
            + TEARDOWN_BASE + r'''RELOAD_BODY''')
    log(ask(bridge, code) or "the game did not answer")
    sample_transitions(bridge, "just after the teardown")
    sample_input(bridge, "just after the teardown")
    out = None
    for _ in range(12):
        time.sleep(0.25)
        out = ask(bridge,
                  "return 'step = ' .. tostring(_G.__qrStep) .. ' ; load = ' .. "
                  "tostring(_G.__qrDone) .. ' ; why = ' .. tostring(_G.__qrWhy) .. "
                  "' ; slot = ' .. tostring(_G.__qrSlot) .. "
                  "' ; loaded keys = ' .. tostring(_G.__qrLoadedKeys) .. "
                  "' ; data says slot = ' .. tostring(_G.__qrDataSlot) .. "
                  "' ; has constructorList.maincharacter = ' .. tostring(_G.__qrHasMainChar) .. "
                  "' ; menu put back = ' .. tostring(_G.__qrRecovered) .. "
                  "' ; note = ' .. tostring(_G.__qrNote)")
        if out and "load = nil" not in out:
            break
    if out:
        log("  " + out)
        extra = ask(bridge, "return 'poll: ' .. tostring(_G.__qrLog and "
                            "table.concat(_G.__qrLog, ' | '))")
        if extra:
            log("  " + extra)
    sample_input(bridge, "after the load")
    return 0


def do_state():
    """Read-only: what the reload is looking at, changing nothing at all.

    Here so the teardown can be judged on its own. Run it before and after `--teardown` and the
    `isCreated()` line is the answer to "did the world actually go away" without having to load
    into it to find out.

    The input sample at the top is there so a HEALTHY game can be measured: start the game, get into
    the world with nothing open, and run this. The level and the nav that serves it in that state are
    the target a teardown has to leave behind, and knowing them beats assuming 0.
    """
    sample_input(_bridge(), "now")
    code = r'''
local out = {}
local function add(k, v) out[#out + 1] = k .. ' = ' .. tostring(v) end

local wh = _G.worldHelper
add('worldHelper', type(wh))
if type(wh) == 'table' and type(wh.isCreated) == 'function' then
  local ok, v = pcall(function() return wh:isCreated() end)
  add('isCreated()', ok and tostring(v) or ('threw: ' .. tostring(v)))
else
  add('isCreated()', 'not available')
end

local ps = _G.playerStateHelper
add('playerStateHelper', type(ps))
if type(ps) == 'table' then
  local ok, v = pcall(function() return ps:getSelectedSaveslotIndex() end)
  add('getSelectedSaveslotIndex()', ok and tostring(v) or ('threw: ' .. tostring(v)))
  add('loadGame hooked', type(ps.__hudQR) == 'table')
  if type(ps.loadGame) == 'function' then
    local i = debug.getinfo(ps.loadGame, 'S')
    add('loadGame from', tostring(i.short_src) .. ':' .. tostring(i.linedefined))
  end
end

add('overlays closed by the last teardown', _G.__qrClosedOverlays)
add('dialogues closed by the last teardown', _G.__qrClosedDialogues)
add('input level now', (type(_G.inputHelper) == 'table' and
    type(_G.inputHelper.getInputLevel) == 'function')
    and (function() local ok, v = pcall(function()
           return _G.inputHelper:getInputLevel() end)
         return ok and v or ('threw: ' .. tostring(v)) end)() or 'not available')
add('input blocked (the flag, not the method - the method with no argument is always false)',
    (type(_G.inputHelper) == 'table' and type(_G.inputHelper.unblockInput) == 'function')
    and (select(2, debug.getupvalue(_G.inputHelper.unblockInput, 1))) or 'not available')
add('input level at the start of the last teardown', _G.__qrInputLevelAtStart)
add('input level before the last teardown', _G.__qrInputLevelBefore)
add('overlays left after the last teardown', _G.__qrOverlaysLeft)
add('  target level (lowest nav registered)', _G.__qrTargetLevel)
add('  levels stepped down to reach it', _G.__qrStepsDown)
add('input level after the last teardown', _G.__qrInputLevelAfter)
add('  final target level', _G.__qrFinalTarget)
add('  final level', _G.__qrFinalLevel)
add('  final steps up / down', tostring(_G.__qrFinalUp) .. ' / ' .. tostring(_G.__qrFinalDown))
add('  final nav', _G.__qrFinalNav)
add('  final nav focused', _G.__qrFinalNavFocused)
add('  final nav focused after repair', _G.__qrFinalNavFocusedAfterRepair)
add('nav registered at the level after the last teardown', _G.__qrNavAtLevel)
add('  and was it focused', _G.__qrNavAtLevelFocused)
add('  focused after focus was handed back', _G.__qrNavFocusedAfterRepair)
add('input diag now', _G.__qrInputDiag)
add('args captured', type(_G.__qrArgs) == 'table')
local tg = _G.__qrTitleGroup
if tg ~= nil then
  -- is the thing titleScreen:new() returns actually usable as the screen's group?
  add('title group from new()', type(tg))
  if type(tg) == 'table' then
    add('  numChildren', tg.numChildren)
    add('  removeSelf', type(tg.removeSelf))
    add('  isVisible', tg.isVisible)
    add('  localToContent', type(tg.localToContent))
  end
end
add('last run id', _G.__qrRan)
add('last step', _G.__qrStep)
add('last result', _G.__qrDone)
add('last why', _G.__qrWhy)
add('last note', _G.__qrNote)
if type(_G.__qrLog) == 'table' and #_G.__qrLog > 0 then
  add('poll history', table.concat(_G.__qrLog, ' | '))
end
return table.concat(out, '\n')
'''
    log(ask(_bridge(), code) or "the game did not answer")
    return 0


def do_disarm():
    code = r'''
local ps = _G.playerStateHelper
if type(ps) ~= 'table' then return 'playerStateHelper is not there' end
local rec = ps.__hudQR
if type(rec) ~= 'table' or type(rec.orig) ~= 'function' then return 'was not armed' end
ps.loadGame = rec.orig
ps.__hudQR = nil
return 'disarmed - loadGame is the game's own again'
'''
    log(ask(_bridge(), code) or "the game did not answer")
    return 0


def do_list():
    if not os.path.isdir(BANK):
        log("no snapshots yet (%s does not exist)" % BANK)
        return 0
    files = sorted(f for f in os.listdir(BANK) if f.endswith(".json"))
    if not files:
        log("no snapshots in %s" % BANK)
        return 0
    for name in files:
        full = os.path.join(BANK, name)
        try:
            with open(full, encoding="utf-8") as fh:
                snap = json.load(fh)
            keys = snap.get("keys")
            if not isinstance(keys, dict):
                # a bare key -> value mapping, which is what a hand-made dump looks like
                keys = {k: v for k, v in snap.items() if isinstance(v, str)}
                desc = "%d key(s), no timestamp" % len(keys)
            else:
                desc = "%s, %d key(s)" % (snap.get("captured", "?"), len(keys))
        except Exception as exc:                      # a plain database copy also lands here
            desc = "not a snapshot (%s)" % type(exc).__name__
        log("  %-40s %9d B   %s" % (name, os.path.getsize(full), desc))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="remember a save and put it back, through the game's own store")
    what = ap.add_mutually_exclusive_group()
    what.add_argument("--save", action="store_true",
                      help="snapshot the current save (before handing a Coromon over)")
    what.add_argument("--restore", action="store_true",
                      help="put the snapshot back into the running game")
    what.add_argument("--list", action="store_true",
                      help="show the snapshots that exist")
    what.add_argument("--block", action="store_true",
                      help="stop the game saving, so the handover is not written to the slot")
    what.add_argument("--unblock", action="store_true",
                      help="let the game save again")
    what.add_argument("--arm", action="store_true",
                      help="hook the game's load so its arguments can be replayed")
    what.add_argument("--reload", nargs="?", const=0, type=int, metavar="SLOT",
                      help="run the quit sequence, then load that slot out of the store; the "
                           "slot number is optional (default: whichever the game has open)")
    what.add_argument("--teardown", action="store_true",
                      help="run the game's own quit sequence and stop at the main menu")
    what.add_argument("--state", action="store_true",
                      help="read-only: what the reload sees right now, changing nothing")
    what.add_argument("--disarm", action="store_true",
                      help="remove the hook again")
    ap.add_argument("--file", default=DEFAULT_FILE,
                    help="which snapshot to write or read (default: %s)" % DEFAULT_FILE)
    args = ap.parse_args()

    if not os.path.exists(PREFS_DB):
        log("the save database is not where it was expected:")
        log("  " + PREFS_DB)
        return 1
    keys = slot_keys()
    if not keys:
        log("no save slots found in %s" % PREFS_DB)
        return 1

    if args.list:
        return do_list()
    if args.block:
        return do_block(True)
    if args.unblock:
        return do_block(False)
    if args.arm:
        return do_arm()
    if args.reload is not None:
        return do_reload(None if args.reload == 0 else args.reload)
    if args.teardown:
        return do_teardown()
    if args.state:
        return do_state()
    if args.disarm:
        return do_disarm()
    if args.restore:
        return do_restore(args.file, keys)
    return do_save(args.file, keys)


if __name__ == "__main__":
    sys.exit(main())
