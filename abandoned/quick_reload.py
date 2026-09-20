#!/usr/bin/env python3
"""
quick_reload.py - put a save back the way it was, without relaunching the game.

ABANDONED 2026-09-20. Kept rather than deleted because it is close to working and because the
reading of the game's load flow below is expensive to redo. See abandoned/README.md for what was
proven, the two crashes, and the single thing still unverified. Nothing loads this file.

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


# The teardown, transcribed from the game's own Quit button
# (classes.interface.navBarObjects.topBarObjects.outerTopBarQuitButtonBuilder, lines 27-40).
# Every call in it is on a global, which is why it can be written out here instead of having to
# be captured. Each step names itself in `_G.__qrStep` before it runs, so if the game dies part
# way through, the next bridge call can say WHICH step it died on rather than just that the game
# is gone.
TEARDOWN = r'''
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

_G.__qrStep = 'inputHelper:unblockInput'
_G.inputHelper:unblockInput()
_G.__qrStep = 'pauseMenu:forceDestroyIfCreated'
_G.pauseMenu:forceDestroyIfCreated()
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

-- The two calls an earlier version of this script left out, on the grounds that they only put a
-- screen on the display that a load is about to replace. That was wrong: they are what returns the
-- game to the MAIN MENU, and the main menu is the state a load is done from. Stopping before them
-- left the game with no world AND no screen, one call short of the thing the game actually does.
if type(_G.Achievement) == 'table' and
   _G.Achievement.resetPercentageOfMaxProgressCache ~= nil then
  _G.__qrStep = 'Achievement:resetPercentageOfMaxProgressCache'
  _G.Achievement:resetPercentageOfMaxProgressCache()
end
if type(_G.titleScreen) ~= 'table' or _G.titleScreen.new == nil then
  return _G.__qrStep .. '; but titleScreen:new is not there, so the game was left with no screen'
end
_G.__qrStep = 'titleScreen:new'
-- keep what it returns. It is very likely the group this screen is built into, and that group is
-- the `parentGroup` the title screen's own load routine later does `display.remove(...)` on - the
-- handle an automated load needs and which is otherwise an unreachable closure upvalue.
_G.__qrTitleGroup = _G.titleScreen:new()
_G.__qrStep = 'at the main menu'
'''


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


def do_teardown():
    """The game's own quit sequence, stopping at the main menu.

    Worth having on its own: "press a button and be back at the main menu" is a result in itself,
    it is the sequence the game runs when the Quit button is pressed, and it is the state a load
    is done from - so it is the right thing to get working before any load is attempted.
    """
    code = run_guard("teardown-%s" % time.time()) + TEARDOWN_GUARD + TEARDOWN + r'''
_G.__qrReport = 'teardown finished, step = ' .. tostring(_G.__qrStep)
return _G.__qrReport
'''
    log(ask(_bridge(), code) or "the game did not answer")
    return 0


def do_reload():
    """Tear the current game down the way the game's own Quit button does, then load the save
    that was captured while armed.

    The load is deferred by one tick on purpose. The teardown destroys the world, and building a
    new one in the same instant as the old one is being destroyed is the kind of race that has
    broken this game once already.
    """
    code = run_guard("reload-%s" % time.time()) + TEARDOWN + r'''
local args, n = _G.__qrArgs, _G.__qrN
if type(args) ~= 'table' then
  return _G.__qrStep .. '; but nothing was captured - run --arm and load the save once first'
end
_G.__qrDone = nil
_G.__qrStep = 'queued'

-- The wrapper went on as `ps.loadGame`, so whether the game reaches it as a method call or
-- through the module dispatch decides whether the first captured value is `ps` itself.
local off = 0
if args[1] == ps then off = 1 end
local a1, a2, a3, a4 = args[1 + off], args[2 + off], args[3 + off], args[4 + off]

if type(a1) ~= 'table' then
  return _G.__qrStep .. '; captured argument 1 is a ' .. type(a1) .. ', not the saveslot data'
end
local ncapped = n or #args
if off == 1 then ncapped = ncapped - 1 end
local report = {
  'torn down; ' .. tostring(ncapped) .. ' captured argument(s), self ' ..
      (off == 1 and 'included' or 'not included'),
  'save data: selectedSaveslotIndex = ' .. tostring(a1.selectedSaveslotIndex),
  'save data: rogueSession = ' .. tostring(a1.rogueSession and 'present' or 'nil'),
  'cluster: ' .. type(a2) .. ', deviceId = ' ..
      tostring(type(a2) == 'table' and a2.deviceId or nil),
  'manual version = ' .. tostring(a3) .. ', auto version = ' .. tostring(a4),
}

if type(_G.timer) ~= 'table' or type(_G.timer.performWithDelay) ~= 'function' then
  return table.concat(report, ' | ') .. ' | no timer, so the load was NOT queued'
end

local function finish()
  _G.__qrStep = 'loadGame'
  -- 'instant' and no callback, exactly as the game's own debug loader calls it. The callback the
  -- title screen passes is a closure into a screen that is gone by now, and the debug loader
  -- (debug_load_saveslot, line 51) shows the callback is optional.
  local ok2, err = pcall(function() ps:loadGame(a1, a2, a3, a4, 'instant', nil) end)
  _G.__qrDone = ok2 and 'ok' or ('threw: ' .. tostring(err))
  _G.__qrStep = 'finished'
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
  if settled or waited >= 5000 or (noGate and waited >= 250) then
    stopped = true
    if pollTimer then pcall(function() pollTimer:cancel() end) end
    _G.__qrWhy = settled and ('isCreated() went false after ' .. tostring(waited) .. ' ms')
                 or (noGate and 'no isCreated() to wait on, so a fixed wait'
                     or 'timed out after 5 s with isCreated() still true')
    _G.__qrStep = 'waiting stopped after ' .. tostring(waited) .. ' ms'
    finish()
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
    log(ask(_bridge(), code) or "the game did not answer")
    # the load is deferred until the teardown has actually finished, so wait for it rather than
    # reading the state once and reporting whatever it happened to be mid-flight
    out = None
    for _ in range(12):
        time.sleep(0.25)
        out = ask(_bridge(),
                  "return 'step = ' .. tostring(_G.__qrStep) .. ' ; load = ' .. "
                  "tostring(_G.__qrDone) .. ' ; why = ' .. tostring(_G.__qrWhy) .. "
                  "' ; note = ' .. tostring(_G.__qrNote)")
        if out and "load = nil" not in out:
            break
    if out:
        log("  " + out)
        extra = ask(_bridge(), "return 'poll: ' .. tostring(_G.__qrLog and "
                              "table.concat(_G.__qrLog, ' | '))")
        if extra:
            log("  " + extra)
    return 0


def do_state():
    """Read-only: what the reload is looking at, changing nothing at all.

    Here so the teardown can be judged on its own. Run it before and after `--teardown` and the
    `isCreated()` line is the answer to "did the world actually go away" without having to load
    into it to find out.
    """
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
    what.add_argument("--reload", action="store_true",
                      help="tear the current game down the way the Quit button does, then load "
                           "the save captured while armed")
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
    if args.reload:
        return do_reload()
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
