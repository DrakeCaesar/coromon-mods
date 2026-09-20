# Coromon tools

Reverse-engineering / automation helpers for **Coromon (Solar2D build, Steam)**.

Coromon is a **Solar2D (Corona SDK)** game: there is no native engine to patch — the
entire game logic is Lua 5.1 bytecode shipped inside `Resources/resource.car`, and the
VM lives in `lua.dll`. So everything is done by reading the `resource.car` archive
and/or talking to the live Lua state inside `coromon.exe`.

## Quick start — starter Potential reader

```bash
python coromon-tools/coromon_starter.py                  # run it and leave it running
python coromon-tools/coromon_starter.py --perfect        # force all three starters to 21
python coromon-tools/coromon_starter.py --force-potential 20   # ... or any value 1-21
python coromon-tools/coromon_starter.py --once           # read the roll a single time and exit
python coromon-tools/coromon_starter.py --verbose        # also show the object path + attributes
python coromon-tools/coromon_starter.py --clear-overlay  # remove the on-screen overlay
python coromon-tools/coromon_starter.py --no-overlay     # console output only
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

In Coromon **Potential** goes 1–21 and the game sorts it into three categories:
**1–16 = Standard**, **17–20 = Potent** (aura/shiny-style sprite), **21 = Perfect**.
Those are the game's *internal* tier names `A`/`B`/`C` — see
[Battle Potential overlay](#battle-potential-overlay) for how they are labelled.
Potent and Perfect lines are highlighted in green/gold on the overlay.

### Forcing the roll (`--perfect`)

`--perfect` (or `--force-potential N`) rewrites the three starters to that value as
soon as they appear, so whichever one you pick is perfect. The natural roll is still
reported:

```
[01:41:23] forcing every starter to potential 21 on sight
[01:41:23] === starter roll #1 ===
  FIRE_TURTLE_1    potential 21  <-- PERFECT   (natural roll 10)
  WATER_SHARK_1    potential 21  <-- PERFECT   (natural roll 9)
  ICE_BEAR_1       potential 21  <-- PERFECT   (natural roll 12)
```

This is not a cosmetic edit. `Monster:setPotential()` is literally `self.potential = v`,
`getPotential()` clamps that field to 1..21, and the *stat points* are handed out by the
game itself as the Coromon reaches its potential levels — so a forced value behaves
exactly like a natural roll (same granted-point count at level 1, same potential
category, and it is what gets written to the save when you pick).

Where the value actually lives (from the shipped bytecode):

```
abstractMonster:resolvePotential()          -- called when the Monster is first built
    if csp.potential == nil then
        csp.potential = monsterUtility:rollPotential(self)   -- the RNG roll, cached
    end
    return csp.potential

spawnables/monster.lua:182  getOrCreateMonster()
    if createdMonster then return createdMonster end         -- lazy + cached
    createdMonster = Monster.new{ level = ..., traitUID = ...,
                                  potential      = self:resolvePotential(),
                                  potentialStats = self.consistentSaveProperties.potentialStats,
                                  ... }
```

So each starter's potential is rolled **lazily** — on the first `resolvePotential()` call —
and then cached in the spawnable's `consistentSaveProperties.potential`; the Monster
object itself is only built on the first `getOrCreateMonster()`. The tool overwrites that
cached roll (and any Monster that already exists), so the Monster is normally *created*
with the forced value rather than corrected afterwards. If you start the tool while
already standing at the reveal, the existing objects get edited in place instead — the
end state is the same either way.

Category tiers, straight from `monsterPotentialUtility:getCategories()`:
potential 1–16 = A, 17–20 = B, **21 = C** (the only value in the top tier). The game
localises those letters through `global.monsterPotentialCategory.<cat>` as
**Standard / Potent / Perfect**.

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

Content resolution: overlay coordinates are in content space, not pixels. The content size
tracks the window rather than being fixed — measured, `display.contentWidth/Height` was
480×265 at a 1920-wide window, where `display.contentToScreenScale` reads exactly 4. That
scale is a live number (`0.25` the other way round), not a fixed integer factor.

## Other tools

`overlays.py` is the combined entry point: the battle Potential readout, the item markers
and the overworld zoom, all driven from one settings file.

| file | purpose |
|---|---|
| `overlays.py` | **all three live helpers, one command** — a thin front end over `ingame/`; waits for the game, re-attaches when it restarts |
| `overlays.toml` | the settings: which features are on, and every option they have |
| `ingame/config.py` | the settings file — its schema, its defaults, reading it back |
| `ingame/core.py` | the hook-up to the game, the Lua every feature shares, and the harness |
| `ingame/potential.py` | the battle Potential readout |
| `ingame/items.py` | the markers over the map's collectable objects |
| `ingame/zoom.py` | the overworld zoom keys |
| `coromon_starter.py` | the useful one — read the 3 starter potentials and draw them on screen |
| `scroll_fix.py` | make the overworld scroll a constant number of pixels per frame |
| `fps_patch.py` | set the frame rate in the game's own files, and put it back (60/120, or any value with one more patch) |
| `pad_drive.py` | hold a direction in the game window from outside (test instrument) |
| `coromon_lua.py` | generic Lua injection bridge (`--eval`, `--file`, or REPL) |
| `car_extract.py` | Solar2D `resource.car` reader/extractor (`--list`, `--extract`) |
| `luadis.py` | Lua 5.1 bytecode reader/disassembler/string dumper for `.lu` chunks |
| `slot_move.py` | move or copy a save between slots (`--list`, `--move 2 1`, `--copy 2 1`, `--restore <backup>`) |
| `engine_source.py` | clone the Solar2D engine source at the tag this game was built from (`--clone`, `--update`) |

### Everything at once (`overlays.py`)

The three live helpers started as three separate programs and drifted into three copies of
the same scaffolding: the Frida bridge, the `tiledWorld` lookup (done two different ways),
the `localise` wrapper (two different wrappers, same `???` trap), the `display.newRect` and
`textHelper` helpers, the state-table-plus-timer pattern, and a teardown each. Running all
three meant three attachments to the game and three Lua chunks.

`overlays.py` is one command with one attachment and one Lua chunk — but deliberately *not*
one file, and deliberately not flag-driven either:

```bash
python overlays.py
```

That is the whole interface. Everything — which features are on, the zoom keys, which item
classes are marked, the label font — lives in `overlays.toml` beside it, and a run makes the
game match that file: it tears down whatever was installed last time and installs what is
switched on, then prints what it installed, the current state of each feature, and what is
around you. Turning something off is setting its `enabled` to `false` and running again.

The game does not have to be up, and closing it does not end the run. Start `overlays.py`
whenever — before the game, at the title screen, mid-save — and it waits for `coromon.exe`
to appear, attaches, and installs as soon as the Lua state is reachable. Close the game and
start it again and the same happens on its own: the old attachment goes away with the
process, the loop notices, waits for the new one, re-reads `overlays.toml` and puts every
feature back. So the settings can be edited while the game is down and the next launch picks
them up. Ctrl+C stops it; whatever is on screen stays until the game is closed.

Waiting covers a game that is there but not ready, which is the normal case right after a
restart: a closed process stays listed for a moment, and while it is on its way out it
refuses the injector outright — measured, a Frida attach error with
`VirtualAllocEx returned 0x00000005` (ACCESS_DENIED), which arrives as
`frida.NotSupportedError` or `frida.TransportError` depending on where it trips. That, a
missing process, and the other transient Frida failures are all in
`RETRYABLE_ATTACH_ERRORS` in `coromon_lua.py`; `try_attach()` returns `None` for them and
the loop retries once a second, saying why it is waiting, once, rather than dying on a
traceback.

The file documents itself, because it is *generated*: each module declares its settings as
`(key, default, comment)` in `SETTINGS`, `ingame/config.py` renders `overlays.toml` from
those, and a value that does not match its default's type — or the values a setting allows —
is reported and the default used instead. Delete the file and it is written back out of the
defaults. Adding a setting is one line in the module that owns it.

`ingame/core.py` owns the bridge and the shared Lua; each feature is its own module
implementing the same small surface (settings, query Lua, install block, teardown, and the
summary/status/report lines). Core asks the modules for those and composes them, so it never
has to know what the features are, and the features install, remove and report independently.
Re-running replaces the previous install rather than stacking on it, and it tears down the old
`__battlepot` / `__hidden` / `__mapzoom` globals, so you cannot end up with two of everything
on screen. The per-feature detail in the sections below is what the modules took over
unchanged.

### Overworld zoom

Coromon has no zoom. `debugSettings` has no scale key, the camera
(`classes.modules.interface.scrollViewBuilder`) takes no scale parameter, and the only zoom
knob anywhere is `debugHexagonWorldScaleMultiplier` — debug tooling for the Rogue Planet map
generator, not reachable in a normal save. So `ingame/zoom.py` scales the overworld node
(`tiledWorld`) itself, with the player inside it, about the middle of the viewport.

| key | action |
| --- | --- |
| `-` | zoom out one stop |
| `+` | zoom in one stop |
| `0` | back to the game's own scale |

The stops are whole screen pixels per texture pixel: **4 → 3 → 2 → 1** at a 4× window, i.e.
k = 1, 0.75, 0.5, 0.25. Nothing in between, and nothing above 1, deliberately — the game draws
with nearest-neighbour filtering, so a fractional scale gives some texels 3 screen pixels and
others 2, which shows up as uneven pixel sizes and a faint shimmer while the map scrolls. The
stops are read from the game's own `display.contentToScreenScale`, so they stay honest if the
window size changes. The keys themselves are `key_out`, `key_in` and `key_reset` in the
`[zoom]` table of `overlays.toml`, and `pixels` installs already zoomed out — 0, the default,
is no zoom. The status the script prints shows the scale, the px-per-texel and every key the
game has seen, which is how you find a key's real name when one does not react.

Two things to know before touching it:

* **The camera is incremental.** Measured live, `tiledWorld.x + sprite.x` stays pinned at a
  constant while walking, so the engine carries its own camera value and adds the sprite's
  movement each frame. The zoom therefore *tracks* that value — it only ever reads the node,
  never writes it — and re-seeds whenever a location change swaps the node, holding at scale 1
  until the new node stops being positioned (measured: the position standing still for four
  frames, about 80 ms). Writing the node outright destroys the engine's base permanently: the
  first version left the world 446 px out, and the offset survived walking, map changes and a
  reset. `recenter = true` in `[zoom]` is the escape hatch for a world an older build
  displaced that way: set it, run once, set it back to false.

  Deriving the offset from where the player is looks like it should work and does not. It pins
  them to one spot: fine on a large map, where the engine parks them at the centre, but wrong
  in a small area, where the engine leaves the camera alone and walks the player across the
  screen — there it dragged the world along with them at (1 − k) per pixel of movement.

* **Screen-locked layers need their size back.** Maps mark a whole Tiled layer
  `stickToScreen` (`worldRainOverlay` — the full-screen sheet drawn over the map), and the
  builder pins such a layer to the screen by translating it by the exact opposite of the
  world's translation on every camera move (`tiledWorldBuilder.moveCamera`, lines 363-365). The
  pinning survives the zoom untouched, but the layer is laid out to fill the viewport at scale
  1, so at a zoom of k it draws k times too small and covers only the middle of the screen. The
  zoom counter-scales it by 1/k, and because its content is not centred on its own anchor it
  measures the shift that introduces rather than assuming it. The cost is that at 1 px per texel
  the overlay texture is magnified 4×, so its detail is chunky.


### Battle Potential overlay

Coromon hides a monster's Potential until you catch it, and the two high tiers are what
make a catch worth keeping. This draws it over the battle so you can decide before
spending a spinner:

| `potential` | Tier | Wild odds | Overlay colour |
| --- | --- | --- | --- |
| 1–16 | Standard | ~97.1% | white |
| 17–20 | Potent | ~2.9% | green |
| 21 | Perfect | ~0.03% | gold |

There is no separate "potency" field anywhere on the Monster object — it is always
`potential`. The tier is not hardcoded either; it comes from the game's own
`monsterUtility:getPotentialCategoryForPotential()`.

Switched off with `enabled = false` in the `[potential]` table of `overlays.toml`; the report
the script prints always lists the current opponents.

The opponent is found through the live battle instance:

```
Battle:get()                        -- the live battle instance
  :getMonsterSpritesInBattle()      -- the on-field sprites
    [i].side == 'front'             -- 'front' is the opponent, 'back' is yours
    [i].monster                     -- the Monster object; .potential is the number
      .traitUID                     -- its trait, a plain string ('DIMENSIONAL_EYE')
```

`side` is the reliable discriminator. "The monster with no `catchDate`" also works for a
wild encounter, but it matches every opposing monster in a trainer battle, where no
monster has been caught.

**Double battles.** More than one `front` sprite can be on the field at once, so the tool
does not stop at the first match — it collects every opponent and draws a separate
name/level/potential line per opponent, ordered left-to-right by the sprite's `x` so
the lines line up with what is on screen. Verified against a 1v2: two `SAND_MOLE_1` at
Lv7 / potential 7 and Lv9 / potential 10 both show, where the earlier first-match
version silently reduced that to one arbitrary opponent.

Nothing is hardcoded, including the tier label. The name comes from the game's own
localisation (`localise('monsters.<UID>.name')` → "Buzzlet", the same pattern as
`items.<UID>.name`) and the tier from `monsterUtility:getPotentialCategoryForPotential()`,
which returns the game's internal letter — `A`/`B`/`C`, the same letters the game's own
potential handbook popup uses before substituting them. Running that letter back through
`localise('global.monsterPotentialCategory.<cat>')` turns it into the word the game shows
the player.

Each opponent is **one line** — name, level, potential, trait:

```
Lunarpup L17 P8 (Dimensional Eye)
```

The trait goes in the parentheses rather than the tier's word, because the tier is already
carried by the line's colour (green for Potent, gold for Perfect), and which trait an
opponent has matters more at the moment you decide whether to spend a spinner. `m.traitUID`
is a plain string on the Monster — `m:getTrait()` returns only the trait's behaviour classes
and no name — so it is the UID that goes through localisation, as `traits.<UID>.name`. An
unlocalised trait shows its raw UID rather than `???`, the same fallback the item names use.

The ranges are confirmed by `monsterPotentialUtility:getCategories()` and match the wiki:
`A` = 1–16, `B` = 17–20, `C` = 21.

The overlay carries its own 250 ms Lua timer, so the Python process exits immediately
and it keeps working for every later encounter. Each tick it is re-inserted at the end
of the stage so the battle cannot draw over it, and it removes itself when no opponent
is on the field rather than leaving a stale readout.

### Hidden items

Coromon scatters items that stay invisible until you walk onto their tile. They are
plain Tiled objects with `class = "hiddenItem"` in an object layer (usually
`interactObjects`), carrying their contents in up to three slots:

Settings, in the `[items]` table of `overlays.toml`:

| setting | default | effect |
|---|---|---|
| `enabled` | `true` | draw the markers |
| `show_chests` | `false` | also mark item chests (amber borders) |
| `show_all_items` | `false` | mark every item-carrying class |
| `labels` | `true` | draw the item name above each marker |
| `font` | `"small"` | label font, `"small"` or `"big"` |

The report the script prints lists them with tile coordinates.

**Which classes carry items.** Counted from the maps the game actually loads,
`Resources/optimizedMaps/**/*.json` — 195 maps, and every one of these 1160 carriers has
its class written inline, so a plain scan is enough there:

| class | count | item property | covered by |
|---|---|---|---|
| `hiddenItem` | 380 | `item1_UID` | default |
| `drillShovelItem` | 370 | `item1_UID` | `show_all_items` |
| `itemChest` | 326 | `item1_UID` | `show_chests` |
| `fruitGrowingPot` | 42 | `itemUID` | never — see below |
| `pyramidItemChest` | 30 | `item1_UID` | `show_chests` |
| `item` | 11 | `itemUID` | `show_all_items` |
| `treeItem` | 1 | `itemUID` | `show_all_items` |

**The tool reads the live map, not these files.** `MTE.getMap()` returns the map table the
engine actually built, and `itemCollect()` walks `map.layers[*].objects` on it — so the
markers always follow the running game, and no map file is ever opened. The counts above
are offline reference only.

**`optimizedMaps` vs the loose `maps` copy.** Both are the same Tiled project, and they
mostly agree: 195 maps / 1160 carriers against 192 / 1122. The loose copy is the *source*,
with most carriers' classes living in Tiled templates rather than inline — read raw, every
`hiddenItem` looks unclassed, so its templates must be resolved before any count means
anything (that mistake produced a bogus "11 `drillShovelItem`" reading of these files
once). Differences are tile-level and rare, e.g. `desertRoute_5` (8,33) is `GOLD` x2000 +
`GEM_GREEN_2` in `optimizedMaps` but a lone desert plant in the loose copy.
`resource.car` carries both forms as well (`classes.maps.<path>.lu`, plus an
`optimizedMaps.…` entry), and the loaded map's own `map.path` / `map.filename` name it
fastest — `desertRoute/desertRoute_5/desertRoute_5` is the path form, not a folder pair.

`fruitGrowingPot` is excluded even from `show_all_items`: it is a repeatable harvester
(plant a fruit, take the yield), not a one-time pickup, so "already collected" has no
meaning.

**Containers hold up to three items**, in `item<N>_UID` / `item<N>_amount` slots — an
empty slot is a nil UID with amount 0, so both are tested. Every item is shown: the
first is the "top name" and the rest stack underneath it on screen, with the block
lifted so its last line still sits just above the tile. In the report the extras are
indented under the first.

**Two things can share a tile.** The carriers are independent Tiled objects on separate
layers, so a hidden item and a drill/gem spot can occupy the same square — `desertRoute_5`
(8,33) is `GOLD` x2000 on `interactObjects` plus `GEM_GREEN_2` on `gems`. A marker per
object would stack two identical boxes and two labels in the same spot, so items sharing a
tile are merged: one box, and the names become stacked label lines, exactly like a
container's. A non-`hiddenItem` on the tile wins, so the merged marker is amber.

Collected items are dropped *before* merging, so a picked-up item never reappears as an
extra line next to a live one. Exhaustively, over all 195 maps: 1160 carriers, and only
four tiles hold two at once — three of which are mutually exclusive conditional variants
(the same object duplicated on `whileX` / `afterX` layers, only one of which ever loads).
So this affects exactly one tile in the game, and only until its gold is taken.

**Label font** is one choice for every line, set by `font` in `[items]`:

| `font` | fontType | measured | line spacing |
|---|---|---|---|
| `small` (default) | `outline_8` | 60×15 | 10px |
| `big` | `outline_10_bold` | 80×18 | 12px |

The spacing is deliberately tighter than the measured height, because Solar2D reports
`contentHeight == height` — the whole padded line box — while the pixel glyphs occupy
well under it. Tune the numbers in the `FONTS` table in `ingame/items.py`.

The game ships only two text sizes — **8 and 10**, with `_bold` only at 10 — so there
is no third option. `plain_*` variants exist but would not stay legible over a busy
map, and the `*_nonPixelArt_*` variants (27 pages each vs 1–2) are the full-Unicode
fallbacks the game switches to for languages the pixel font cannot render, so they are
not a safe choice here.

**The layers vary, so don't filter by layer.** Item carriers live on layers named
`interactObjects`, `interactObjects_custom`, `items`, `gems`, `birds`, and on
conditional variants like `interactObjects#whileChristmas`, `afterDEFEAT_GHOST_TITAN`
and `whileDEMO`. Matching on the runtime **name prefix** is both necessary and
sufficient, since the runtime name is the Tiled name plus a suffix.

**Chests** are off by default (they're visible objects anyway). `show_chests = true` adds
`itemChest` / `pyramidItemChest`, drawn with amber borders so the two kinds are
distinguishable.

They do **not** share the hidden items' mechanism, despite
`abstractItemChest:shouldAutomaticallyRemoveItemSpawnable()` being a bare `return true`
in the bytecode — which is misleading. Measured on a chest that had been opened, saved
and reloaded: it is *still spawned*, sprite at `alpha = 1`, and the state is a
consistent save property instead, `consistentSaveProperties.itemChestIsOpened = true`
on `entry.properties.spawnable`. So absence from the runtime list is not enough for
chests, and the tool checks both. `drillShovelItem` / `item` / `treeItem` are **not
yet verified** this way — if one of them turns out to use a third mechanism, its marker
would linger after collection.

Labels show the item's display name, resolved through the game's **own localisation**:
`localise('items.<UID>.name')`. That follows the selected language (the game ships
`classes.language.items_<locale>.lu` chunks) rather than the English names baked into
`Resources/data/json/items.json`. A missing key comes back as `'???'` rather than nil,
so the tool tests for that and falls back to the raw UID.

They are found on `map.layers[*].objects` — **not** in `MTE.getObjects()`, which only
returns tile-collision objects. Markers are inserted as children of the game's own
`tiledWorld` node at map-local pixel `(tileX*16 + 8, tileY*16 + 8)`, which is exactly
where the engine centres a sprite on a tile, so they scroll with the map and need no
per-frame work. A Lua watchdog re-draws them on map change.

**Collected items are dropped from the markers.** The static Tiled data can't tell you
anything here — it keeps every entry forever, verified across a save and reload. The
live state is on the runtime objects from `MTE.getObjectsAtTile()`, and there are two
different mechanisms:

* **hidden items are removed** from the runtime list — their tile keeps only a
  collision object, so no matching entry means collected;
* **chests stay spawned** and flip `consistentSaveProperties.itemChestIsOpened = true`
  on the spawnable at `entry.properties.spawnable`.

Both are matched by name **prefix**, because the runtime name is the Tiled name plus a
suffix (`hiddenItem_31_44` → `hiddenItem_31_44_front`). For reference,
`playerStats:getAmountOfHiddenItemsFound()` only tracks a global counter for the
`FIND_150_HIDDEN_ITEMS` achievement, with no per-item flag anywhere in
`playerWorldData`. The watchdog re-checks every second, so a marker disappears as soon
as the item is taken. The report still shows collected ones, tagged `[already collected]`.

**Gotcha:** Coromon's wrapped `display.newRect` has **no stroke support** —
`setStrokeColor` is `nil` on it (and `strokeWidth` is a plain field that does nothing).
Wrapping the call in `pcall` hides this and silently leaves you with a filled square.
The border is therefore drawn as four 1px filled edges, anchored at 0,0 on integer
coordinates to stay crisp at the game's content-to-screen scale. `rectHelper:newLineRect`
exists and builds the same four lines internally, but its 5th parameter wants a
game-internal "rect mutator" object (it indexes `fillColor` / `anchorX`), not a table.

Note the world node must be **re-resolved**, never cached: a cached `tiledWorld` goes
stale (`.x` becomes nil) when the game rebuilds the map.

### Real gold counter (`gold.py`)

The counter stops at **9,999,999** however much you have. `playerCurrency` keeps the balance
in a module upvalue and offers two getters — `getGold()` (the real figure) and
`getRestrictedGold()`, the same value through `math.min(9999999, ...)` — and the display reads
the second. The balance really does keep growing; it just cannot be seen. That is also why a
purchase can look free: the counter is pinned at the cap and has no room to move down.

There are **two clamps, not one**. Unclamping the getter is not enough, because each top-bar
label clamps again where it draws:

```lua
goldText.text = string.format('%07d', math.min(9999999.0, math.round(value)))
```

Both are replaced. The getter is reached by exactly five modules — the two labels, the dialog
answer buttons, the Rogue interface and its own definition — and nothing that decides whether
you can afford something reads it: the shops, the items and `spendGold` all go through
`getGold()`. It is a display function, so replacing it re-labels the game without touching the
balance or any purchase check.

The label's text is reachable as the first upvalue of its animation function, and that is what
makes the **grouping** possible at all — the game's own `%07d` cannot produce separators:

```lua
debug.getUpvalue(inst.doCurrencyAnimation, 1).text   -- the label's text object
```

The separator is a plain space, chosen over the apostrophe after both were put on screen and
seen. It must be a character the font actually has: U+00A0 does not render, and the text object
then stores something other than what it was given, which turns the per-frame pass into a
rewrite-every-frame loop and makes the counter blink.

**The gap after the number.** The label is fixed at its left edge and grows rightward, and the
game's layout was built for a 7-digit counter, so grouping takes 4 units out of the gap that
follows. The obvious fix fails — shifting the label alone clips it into the coin icon, which
never moves — so `keep_right_edge` moves **both**, by exactly the width the grouping added
(measured on the counter: 37 → 41 units, 2 per separator). Neither gap changes size; the pair
simply sits 4 units further from the middle.

**Where the shift is measured from is not fixed.** The bar re-lays the counter out around the
same objects rather than rebuilding them, so the counter's x is re-read whenever it moves, with
the value we last wrote remembered so that our own write cannot be mistaken for the game's.

**And the layout itself is refreshed.** The bar positions the counter with a right-to-left
*aware* magnet, applied when the bar is built and not again afterwards: on the pause menu the
counter's container sits at `x=190` on open and `x=188` after any input-device change — those
2 units were the last of this to be found, and they move the counter and its coin together
because the container is an ancestor of both. The refresh is stored on that container as
`refreshMagnetX`, so it is simply called, every frame: the layout is not computable on the frame
the bar is built, because the input prompts it depends on are not in place yet.

Settings, in the `[gold]` table of `overlays.toml`:

| setting | default | meaning |
| --- | --- | --- |
| `enabled` | `true` | the whole feature; off restores the getter, the text and the positions |
| `fix_counter` | `true` | correct the two top-bar labels as well as the getter |
| `separator` | `"space"` | `space`, `apostrophe`, `period`, `comma`, `none`, or any literal |
| `keep_right_edge` | `true` | move the number *and* the coin so neither gap changes size |

Everything is drawing only: nothing raises the balance and nothing is written to the save.

### Frame rate (`fps_patch.py`)

Solar2D picks the frame rate **once, at startup**, from the game's `config.lua`:
`application.content.fps`. It is used for the whole session — `Runtime::BeginRunLoop()` is
`fTimer->SetInterval(1000 / fFPS)`. Two things follow. It cannot be changed while the game
runs, and `display.fps` is a **dead knob**: setting it to 120/165/240 from Lua changes
nothing at all (measured — 60.0 fps, 16.67 ms per frame, every time).

The engine this build ships accepts **exactly two values**. Disassembled at RVA
`0x183595` of `CoronaLabs.Corona.Native.dll`, right before the `exitOnError` read in
`Runtime::ReadConfig`:

```asm
cmp  eax, 0x3c                       ; fps == 60 ?
je   store
cmp  eax, 0x78                       ; fps == 120 ?
jne  skip
store: mov byte ptr [esi+0x64], al   ; fFPS = fps
```

Anything else is ignored and `fFPS` keeps its default of **30** — so writing 165 into
config.lua without patching that check makes the game run at *half* speed. Hence:

```bash
python coromon-tools/fps_patch.py                              # what the files say now
python coromon-tools/fps_patch.py --set 120                    # the one supported step up
python coromon-tools/fps_patch.py --unlock-engine --set 165    # any 1-255, e.g. a 165 Hz panel
python coromon-tools/fps_patch.py --restore                    # back to the shipped 60 / locked
```

It writes the double next to the `fps` key in the compiled `config.lua` inside
`resource.car`, and the two bytes of that `jne` in the DLL (`75 03` → `90 90`). Both are
found by pattern rather than by stored offset, so a game update makes it refuse to write
instead of writing somewhere wrong; `--restore` always puts back what the game ships. The
DLL needs the game closed — Windows will not let anything write to a loaded module.

What it does not change: presentation is **vsync-locked**, so the ceiling stays the
monitor's refresh rate — which is where the odd numbers come from (60 fps at 120 Hz, and
55 fps at 165 Hz, a 16.67 ms frame landing on the 3rd 6.06 ms vsync). And game **speed** is
unaffected: durations are absolute milliseconds (a tile crossing is 280 ms at normal speed,
the same at any frame rate), so extra frames are interpolation, not a faster game. The one
thing counted in frames is `tiledAnimationProxyBuilder`, which steps animated tiles by
`display.msPerFrame`; pin it with `display.msPerFrame = 16.6667` if those look too fast.
The Win32 timer behind all this polls with a 10 ms `SetTimer`, so what you ask for is not
necessarily what you get — measure it with `perf_probe.py`.

### Poking around yourself

```bash
python coromon-tools/coromon_lua.py                       # interactive REPL inside the game
python coromon-tools/coromon_lua.py --eval "return tostring(_G.Monster)"
python coromon-tools/car_extract.py Resources/resource.car --list | grep -i starter
python coromon-tools/luadis.py <file.lu> --tree --strings --globals
```

`resource.car` notes: magic `rac\x01`, TOC starts at offset `0x10`, each entry is
`u32 type, u32 offset, u32 namelen, char name[namelen], NUL, pad4`; the payload at
`offset` starts with `u32 type, u32 usize, u32 csize` followed by raw bytes (Lua chunks
are plain Lua 5.1 bytecode with full source paths in their debug info).

### Debug info

The shipped bytecode keeps debug info, and `debug.getinfo(f, 'S')` on any function at
runtime yields `source` + `linedefined`, i.e. the original *file and line* — that is how
the map/spawnable call chain above was identified.
