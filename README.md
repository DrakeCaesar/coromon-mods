# Coromon tools

Reverse-engineering / automation helpers for **Coromon (Solar2D build, Steam)**.

Coromon is a **Solar2D (Corona SDK)** game: there is no native engine to patch — the
entire game logic is Lua 5.1 bytecode shipped inside `Resources/resource.car`, and the
VM lives in `lua.dll`. So everything is done by reading the `resource.car` archive
and/or talking to the live Lua state inside `coromon.exe`.

## Quick start — starter Potential reader

```bash
python tools/coromon_starter.py            # print the 3 starter Coromon potentials once
python tools/coromon_starter.py --watch    # keep polling, print a new roll when it changes
python tools/coromon_starter.py --verbose  # also show the object path + monster attributes
```

Load a save that is just before the starter reveal, attach the tool, then reload the
save until you get what you want. Example output:

```
=== starter roll ===
  FIRE_TURTLE_1    potential 15
  WATER_SHARK_1    potential  7
  ICE_BEAR_1       potential 14
```

In Coromon **Potential** goes 0–21: **20 = "potent"** (aura/shiny-style sprite),
**21 = "perfect"**. The tool flags those automatically.

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

## Other tools

| file | purpose |
|---|---|
| `coromon_starter.py` | the useful one — read the 3 starter potentials |
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
