# Coromon tools

Reverse-engineering / automation helpers for **Coromon (Solar2D build, Steam)**.

Coromon is a **Solar2D (Corona SDK)** game: there is no native engine to patch — the
entire game logic is Lua 5.1 bytecode shipped inside `Resources/resource.car`, and the
VM lives in `lua.dll`. So everything is done by reading the `resource.car` archive
and/or talking to the live Lua state inside `coromon.exe`.

## Quick start — starter Potential reader

```bash
python tools/coromon_starter.py                  # run it and leave it running
python tools/coromon_starter.py --once           # read the roll a single time and exit
python tools/coromon_starter.py --verbose        # also show the object path + attributes
python tools/coromon_starter.py --clear-overlay  # remove the on-screen overlay
python tools/coromon_starter.py --no-overlay     # console output only
```

Start it any time — even at the title screen or before the starters exist. It keeps
polling, so it picks the roll up as soon as it is available and reports a new one
after every reload:

```
[01:21:10] attached to coromon.exe (lua_State 0xcf313d8)
[01:21:10] watching - values appear when the starters are rolled and update on
           every reload; Ctrl+C to stop

[01:21:10] === starter roll #1 ===
  FIRE_TURTLE_1    potential 15
  WATER_SHARK_1    potential  7
  ICE_BEAR_1       potential 14

[01:21:32] === starter roll #2 ===
  FIRE_TURTLE_1    potential 21  <-- PERFECT
  WATER_SHARK_1    potential 20  <-- potent
  ICE_BEAR_1       potential  3
  -> a PERFECT (21) is on the table!
```

While the starters are not rolled yet it prints `waiting for the starter reveal ...`
(and shows that on the overlay) instead of reporting stale numbers.

The same numbers are drawn in the top-left corner **on top of the game** (works in
fullscreen; the overlay survives scene changes and re-attaches itself above the
game's UI). The overlay stays on screen after the tool exits, so use
`--clear-overlay` when you are done; while the tool runs it stays in sync with
every reload.

In Coromon **Potential** goes 0–21: **20 = "potent"** (aura/shiny-style sprite),
**21 = "perfect"**. Those lines are highlighted in green/gold on the overlay.

Requires `frida` (`pip install frida`) and the game running. It attaches read-only:
no memory is patched, nothing is written to the save.

## How it works

1. `coromon_lua.py` hooks a few very common Lua C entry points inside `lua.dll`
   (`lua_gettop`, `lua_pushstring`, …) with Frida to capture the game's `lua_State*`.
2. A snippet is then executed *inside the game* with `luaL_loadstring()` + `lua_pcall()`:
   the bridge pushes the code, runs it, reads the returned string and restores the stack.
3. `coromon_starter.py` uses that to walk (breadth-first, following table fields **and
   closure upvalues**) from the loaded map module
   `package.loaded['classes.maps.luxSolisTown.coromonLab_bf1']` down to:

   ```
   map.addCutscene <cutscenes> . interact_starter_FIRE_TURTLE_1
        <_function> <_monsterSpawnable> . getOrCreateMonster <createdMonster>.potential
   ```

   and prints the `potential` of the three starter monsters.

`consistentSaveProperties.potential` on the same spawnable object carries the same
value; the tool prefers the actual monster object.

### Drawing the overlay

Coromon is a Solar2D game but it *wraps* the `display` global: the wrapper has no
`newText`, and text is built through the game's own factory:

```lua
textHelper:new(parentGroup, 'outline_10_bold', { text = "..." })   -- then :setFillColor(r,g,b)
```

The overlay group is inserted into `display.getCurrentStage()`. Stage children are
drawn *after* everything else (world, dialogs), so they end up on top. Two gotchas:

* `#stage` always reports `0`; use `stage.numChildren` and `stage[i]` instead.
* the group is removed from its parent and re-inserted on every refresh, because the
  game appends its own groups to the stage when it changes scene, which would
  otherwise cover the overlay.

Content resolution is 485×283 and the window is scaled up from there, so overlay
coordinates are in that space (not pixels).

## Other tools

| file | purpose |
|---|---|
| `coromon_starter.py` | the useful one — read the 3 starter potentials and draw them on screen |
| `coromon_lua.py` | generic Lua injection bridge (`--eval`, `--file`, or REPL) |
| `car_extract.py` | Solar2D `resource.car` reader/extractor (`--list`, `--extract`) |
| `luadis.py` | Lua 5.1 bytecode reader/disassembler/string dumper for `.lu` chunks |

### Poking around yourself

```bash
python tools/coromon_lua.py                       # interactive REPL inside the game
python tools/coromon_lua.py --eval "return tostring(_G.Monster)"
python tools/car_extract.py Resources/resource.car --list | grep -i starter
python tools/luadis.py <file.lu> --tree --strings --globals
```

`resource.car` notes: magic `rac\x01`, TOC starts at offset `0x10`, each entry is
`u32 type, u32 offset, u32 namelen, char name[namelen], NUL, pad4`; the payload at
`offset` starts with `u32 type, u32 usize, u32 csize` followed by raw bytes (Lua chunks
are plain Lua 5.1 bytecode with full source paths in their debug info).

### Debug info

The shipped bytecode keeps debug info, and `debug.getinfo(f, 'S')` on any function at
runtime yields `source` + `linedefined`, i.e. the original *file and line* — that is how
the map/spawnable call chain above was identified.
