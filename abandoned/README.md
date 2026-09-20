# abandoned

Stuff that did not work, kept because restarting it would be expensive.

## `quick_reload.py` — abandoned 2026-09-20

Put a save back the way it was, without relaunching the game, so the Potentiflator could be
re-rolled quickly: hand a Coromon over, read the potential, and if it is not 21 press a button and
be back in front of the NPC again.

Run it from anywhere; it imports `scroll_fix` from the folder above.

```
python abandoned/quick_reload.py --state      read-only, safe
python abandoned/quick_reload.py --teardown   destroys the current game world, safe-ish
python abandoned/quick_reload.py --reload     teardown + load, HAS CRASHED THE GAME TWICE
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

### The one thing left unverified

**Whether the teardown works when it runs once.** The last run's diagnostics were polluted by the
double-execution, so it is genuinely unknown. Everything else has been measured.

```
1. start the game, load your save          (so you are in game)
2. python abandoned/quick_reload.py --state      expect isCreated() = true
3. python abandoned/quick_reload.py --teardown   watch the screen
4. python abandoned/quick_reload.py --state      expect isCreated() = false
```

If step 4 says `false`, the teardown is sound and `--arm` + one menu load + `--reload` is worth one
careful try. If it says `true`, `destroyInstance` never ran and building a world on top will keep
producing the two-worlds freeze — in that case stop hand-rolling the teardown and drive the game's
own pause-menu quit, then load from the title screen, which is the order the game itself uses.

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
