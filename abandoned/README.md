# abandoned

Stuff that did not work, kept because restarting it would be expensive.

## `quick_reload.py` — the teardown works, the load is abandoned (2026-09-20)

Put a save back the way it was, without relaunching the game, so the Potentiflator could be
re-rolled quickly: hand a Coromon over, read the potential, and if it is not 21 press a button and
be back in front of the NPC again.

**STATUS: `--teardown` is VERIFIED WORKING. `--reload` now loads the slot out of the store the way
the game's own debug loader does, and has NOT been tried in that form.** Four attempts failed first,
and each is written up below: loading with no teardown at all, then racing the deferred half of the
teardown, then creating a title screen and immediately removing it, then replaying a captured table
the game had already emptied. The current form removes all four causes.

```
python abandoned/quick_reload.py --state      read-only, safe
python abandoned/quick_reload.py --teardown   run the game's own quit sequence: back to the main menu
python abandoned/quick_reload.py --reload [N] quit sequence, then load slot N out of the store
python abandoned/quick_reload.py --arm        capture a real load's arguments (diagnostics only now)
```

### Why it was abandoned

Two crashes, both from driving the game's own teardown/load sequence from outside at a moment the
game does not expect. The second one is fixed and unverified; the first was a design error of mine.
Neither damaged a save (the backups below were never needed). The user's call was to stop.

### What is actually proven

Read out of the shipped `.lu` bytecode in `Resources/resource.car`. This is the expensive part —
it took most of a session — and it is all recorded in the repo's memory notes (`coromon.md`,
section "SAVE / LOAD FLOW") as well as here.

**`loadGame` does not tear anything down.** `playerStateHelper` lines 293-320:

```lua
function playerStateHelper.loadGame(_saveslotData, _saveslotCluster,
                                    _manualVersionId, _autoVersionId,
                                    _loadTransition, _onComplete)
  selectedSaveslotIndex         = _saveslotData.selectedSaveslotIndex   -- L294, NOT .slotIndex
  selectedSaveslotDeviceId      = _saveslotCluster.deviceId             -- L295
  selectedSaveslotCluster       = _saveslotCluster
  selectedSaveslotVersionId     = _manualVersionId
  selectedAutoSaveslotVersionId = _autoVersionId
  shouldSilenceOnlineSaveErrorsThisSession = false
  self:initialisePlayerLibraries(_saveslotData)                         -- L301
  -- then worldHelper:createInstance(mapPath, mapFile, {loadTransition=...}, _onComplete)
end
```

So it **builds a world on top of the current one**. `_onComplete` is optional, and
`_saveslotCluster` only has to carry `.deviceId` (the game's own debug loader passes the literal
`{deviceId = 'self'}`). A `.slotIndex` field does not exist anywhere in the game; a scan of all
8,735 modules found zero reads or writes of that key.

**Tearing down is separate, and each caller does it differently:**
the in-game Quit button (`outerTopBarQuitButtonBuilder` lines 27-40: `inputHelper:unblockInput()`,
`worldInterface:destroy()` + `worldHelper:destroy()` (or `HexagonWorld:destroy()`),
`playerStateHelper:quitCurrentGame()`, `Achievement:resetPercentageOfMaxProgressCache()`,
`titleScreen:new()`); the battle-lost overlay (just `quitCurrentGame()` + `titleScreen:new()`); the
saveslot migration test (world destroy + `quitCurrentGame()` between loads).
`quitCurrentGame` itself is two instructions: `:destroy()` on each player module, then
`selectedSaveslotIndex = nil`. It does not touch the world.

**The menu's real load** is `titleScreen.lu` proto `(0,2,12,0,0,1,0,0)` lines 386-436, and it
ends with `loadGame(data, cluster, manualVersionId, autoVersionId, 'fade',
function() _onWorldLoaded() end)`.

**`worldHelper`'s destroy is asynchronous — this is the subtle one.** `destroy` (lines 223-253)
ends with `nextFrame(function() destroyInstance(...) end)`, and `destroyInstance` (lines 8-11) is:

```lua
instance = display.remove(instance)
setmetatable(t, nil)
```

So when `destroy` *returns*, the instance is still there and the module still has its metatable;
one frame later both are gone. `isCreated()` is `return instance ~= nil` (lines 5-7) and is a plain
field, so it still answers after the metatable is stripped — it is the signal that the deferred
half has run. `nextFrame` is `timer.performWithDelay` under `classes/libraries/timer.lua:26`, so
the deferral is timer-driven, not `enterFrame`.

`createInstance` (lines 32-503) finishes with `MTE:loadMap(mapPath, mapFile, builder)`, where
`builder` (lines 285-502) starts at line 286 with `instance:insert(MTE:getTiledWorld())`.

### The two crashes

1. **Replaying the captured `loadGame` arguments mid-game, with no teardown.** Things got a second
   world built on top of the first; the game went to a broken state and closed. The arguments were
   the game's own and were correct — the state was wrong. Recorded here as the design error: a load
   is a flow, not a function.
2. **Tearing down by hand and loading one tick later.** `worldHelper.lua:286:
   attempt to index upvalue 'instance' (a nil value)`. The load landed *between* `destroy` returning
   and its deferred `destroyInstance`, so the new instance was nilled and MTE's pending callback
   died.

And a third failure that was mine and is fixed: `scroll_fix._eval` drops answers, the retry helper
re-sent the chunk, and because that chunk tears down **and** loads, it ran twice — two teardowns,
two poll timers, two `loadGame`. Fixed with a run-id guard so a resend returns the cached report
instead of acting again, plus a `stopped` flag so a `timer:cancel()` that does not take cannot let
the poll loop continue past the load.

### The question that exposed a bug: "could we just run the correct methods to get to the main menu?"

**There is no such method.** Checked across every shipped module: the only quit-named method anywhere
is `playerStateHelper.quitCurrentGame`, and it does **not** go to the menu — it destroys the player
modules and clears the selected slot. The rest of the sequence exists only inline inside two UI
button handlers (the top-bar Quit button, the battle-lost overlay), so it has to be transcribed.
What *is* callable is its last step: `titleScreen:new()` (`titleScreen.lu`, child 2 of 3, lines
21-544, no arguments).

And asking the question found the bug. An earlier version of the teardown **left `titleScreen:new()`
and `Achievement:resetPercentageOfMaxProgressCache()` out**, on the reasoning that they only put a
screen on the display that a load is about to replace. That was wrong: they are what returns the game
to the **main menu**, and the main menu is the state a load is done from. Stopping before them left
the game with no world *and* no screen — one call short of what the game actually does. That is a
strong candidate for the reported symptom, "it did not teardown the save, and after a few seconds
the game froze".

### The teardown: VERIFIED

```
before   isCreated() = true    getSelectedSaveslotIndex() = 2
--teardown   -> "teardown finished, step = at the main menu"
after    isCreated() = false   getSelectedSaveslotIndex() = nil
```

The slot index going `2 -> nil` is exactly what `quitCurrentGame` does, and the screen returns to a
working main menu. One command replaces quit-to-title plus the slot navigation, even with no
automated load — so the tedious half of the Potentiflator loop is already gone.

Also found by running it repeatedly: it is **not** idempotent by nature, because every run calls
`titleScreen:new()`, so three runs from the menu stack three title screens. `--teardown` now checks
`isCreated()` first and reports "nothing to tear down: the world is already gone" if so.

### The handle an automated load needs

`titleScreen:new()` takes `(self, ...)` and returns a value (`RETURN A=3` at L543) — almost certainly
the group the screen is built into, which is the `parentGroup` the title screen's own load routine
later does `display.remove(parentGroup)` on. That was previously written off here as an unreachable
closure upvalue; it is not, we can just keep what `new` returns. The teardown now stores it in
`_G.__qrTitleGroup`, and `--state` reports its type, `numChildren`, `removeSelf`, `isVisible` and
`localToContent` so it can be confirmed as a real display object before anything is built on it.

### What the automated load does

```
teardown   close any open conversation, then the Quit button's sequence UP TO the title screen
wait       until worldHelper:isCreated() is false, so the deferred destroyInstance has run
load       the game's own debug loader, verbatim (debug_load_saveslot lines 50-51):
             row  = SaveslotPreferences:get(nil, slot, false)
             data = saveslotDataHelper:updateDataToNewestVersion(
                      saveslotDataHelper:decrypt(row.encryptedData), 1)
             playerStateHelper:loadGame(data, {deviceId = ...}, nil, nil, 'instant', nil)
```

**A dialogue box is not world content.** Destroying the world leaves an open conversation on screen
with its old text - reported from running the reload from inside a conversation. All four dialogue
modules (`classes.interface.dialog.dialog`, `.battleDialog`, `.BattleCornerDialog`, `.RogueDialog`)
export the same pair, `isCreated()` and `destroy()`, so the teardown loops over them and destroys the
ones that are up, before the world goes away so the dialogue is closed while everything it refers to
is still alive. `--state` reports what it closed.

The same shape caught us earlier in the overlays: the pause menu's blurred backdrop kept a countdown
drawn over it. A thing drawn on the stage is not torn down by tearing down the world.

**No title screen is created.** The teardown ends with `titleScreen:new()` when run as `--teardown`,
because that is what the Quit button does - but a reload wants a world, not a menu. Creating it and
then taking it away crashed the third attempt: the screen starts an intro `transition.to`, and its
onComplete (`titleScreen.lu:520` -> `CoromonLogo:playSwurmySequence` -> `setSequenceAndPlay`) fired
*after* the screen had been removed and called into a destroyed sprite. `timer.cancel('titleScreen')`
does not stop that, because it is a transition and not a timer, and the game never has to cancel it
because a human leaves the menu up until the animation finishes.

**The arguments are not replayed from a captured load.** `--arm` stores a *reference* to the table
the game handed `loadGame`, and the game empties `constructorList` while building the world - so by
the time it is replayed, `loadGame` dies on `saveslotDataUtility.lua:12`
(`_saveslotData.constructorList.maincharacter.mapPath`). Reading the slot out of the store at reload
time is what the game's own debug loader does, and it has the happy side effect of removing the need
to arm anything at all. `--arm` is kept only for looking at what the game passes.

**Timing.** `worldHelper:destroy()` is only half done when it returns: it ends with
`nextFrame(function() instance = display.remove(instance); setmetatable(t, nil) end)`. Loading before
that frame runs is what crashed the second attempt - the deferred `destroyInstance` nils the instance
`createInstance` has just built. `isCreated()` is `return instance ~= nil` and a plain field of the
module, so it still answers after the metatable is stripped; the reload waits on it.

**Which slot.** `--reload` asks the game (`playerStateHelper:getSelectedSaveslotIndex()`) unless it
is given a number. Note the slot index is also *inside* the save payload - which is why `slot_move.py`
has to re-encrypt when it moves one, and why a plain row rename would half-work.

### The next unknown for an automated load

Tearing the **title screen** back down. Its own load routine does `timer.cancel('titleScreen')`,
`display.remove(parentGroup)`, `pauseMenu:forceDestroyIfCreated()`,
`inputHelper:decreaseInputLevel()` and `inputHelper:setKeyEventShouldDetectUnknownGamepads(false)`
before calling `loadGame` — and `parentGroup` is an upvalue of that closure, which we have no handle
on. Worth solving only after the main-menu milestone works.

### Also worth knowing before resuming

- `--arm` puts a pass-through wrapper on `loadGame` and captures its arguments. A game restart
  loses it, and loses anything captured.
- `--restore` writes the save back through the game's own preference store and **does not work**:
  a load does not read from that store, the game keeps decoded slot data in memory, and the game
  re-saves over a restore anyway. `--save` / `--list` / `--block` / `--unblock` all work.
- With cloud saves disabled the manual version id the game passes is the literal string
  `"failedToSaveOnline"` — the title screen does
  `onlineManual.version or offlineManual.versionId or iCloudManual.versionId`, and the failure
  marker wins. Harmless (`loadGame` only stores it) but it means args 3/4 are not real version ids.
- `--state` is the safe thing to reach for. It only reads.

### Backups (outside the repo, never needed)

```
%APPDATA%\coromon-backups\CoronaPreferences-20260920-215837.sqlite   full store copy
%APPDATA%\coromon-backups\saveslots-20260920-215837.json             the slot rows
%APPDATA%\coromon-backups\quicksave.json                             snapshot for --restore
%APPDATA%\coromon-backups\quicksave-before-restore.json
```

### `ps.lu`

The extracted `classes.modules.playerStateHelper` bytecode this work was disassembled from. It can
be regenerated at any time:

```
python car_extract.py ../Resources/resource.car --extract <somewhere>
python luadis.py <somewhere>/ps.lu --proto <index from --tree>
```

Delete it if you would rather not carry extracted game data.

## Not abandoned, and not part of the above

- **`luadis.py`** — its disassembler gained proper RK operands, the source register on `MOVE`, and
  `CLOSURE` pointing at the right child proto. All three were needed to read the bytecode above and
  are useful on their own.
- **`overlays.py`** — the cooldowns feature is described as sitting under an *icon*, not a bar.
