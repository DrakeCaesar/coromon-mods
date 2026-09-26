#!/usr/bin/env python3
"""
bag.py - what is left in the encounter bag of the zone you are standing in.

THE BAG, because the name is not a metaphor. A Coromon encounter zone does not roll a flat
random draw: it builds a SHUFFLE BAG of exactly `stepsUntilSeenAllEncounters` entries, adding
`stepsWithEncounter` copies of each of its encounters and filling the rest with a NO_ENCOUNTER
sentinel (`classes.lists.EncounterZoneList`, and `classes.modules.Shufflebag`). A draw takes a
random entry and REMOVES it, and the bag refills from a copy of the whole set only once it is
empty. So within one cycle you are guaranteed exactly `stepsWithEncounter` of each encounter -
in random order - and the NO_ENCOUNTER padding is what makes most steps roll nothing.

THAT MAKES THE BAG STATE ACTUALLY INTERESTING, which is the whole reason for this readout: the
odds are not fixed, they depend on what is left. Standing 2 entries from the end of a cycle
means the next two rolls are certainly nothing, and the ten after them are certain to include
whatever the pad was holding back. Nothing in the game shows this, and it is not derivable from
the zone data alone either - the pad is shared out in a random order per cycle.

HOW IT IS READ. None of it is public: the zone list module exports only functions, and the table
of zone wrappers is an upvalue of them, so it is found by NAME (core's `uv`) and cached. From the
zone wrapper, `roll`'s own upvalue `encounterZoneShuffleBag` is the Shufflebag instance, and from
that instance `roll`'s upvalue `shuffleBag` is the entries still left while
`addAmountOfObject`'s upvalue `allObjects` is the full set it refills from. Everything is looked
up by name and nothing is written: if a build renames one of them this readout says it cannot
see the bag rather than showing a wrong one.

WHICH ZONE. The one the player is STANDING ON, asked exactly the way the game's own fishing code
asks it (`spawnableHelper:findSpawnablesAtTile`, then the object carrying a `zoneUID`). Grass
tiles are `grassArea` objects and water is `fishingZoneArea`, and both carry that field, so one
lookup covers land and fishing; the zone the player is on is the one that rolled last and will
roll next. The wrapper is per zoneUID, so every tile of a zone shows the same bag.

WHAT IT DOES NOT DO. It does not touch the bag, the roll, or the game's random state - this is a
read-only window. And it shows the zone you are standing in rather than the one you are about to
step into, because the roll happens on the step and the bag of the tile you are on is the one
that step will draw from.
"""

NAME = "bag"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment) or (key, default, comment, choices).
SETTINGS = [
    (
        "enabled",
        True,
        "show what is left in the encounter bag of the zone you are standing in",
    ),
    (
        "corner",
        "bottom-left",
        "where the readout sits on screen",
        ["top-left", "top-right", "bottom-left", "bottom-right"],
    ),
    (
        "show_nothing",
        True,
        "also show the NO_ENCOUNTER entries as a 'nothing' row. With it off the readout only "
        "lists the encounters left, which is shorter but hides how close the bag is to refilling",
    ),
    (
        "max_lines",
        8,
        "most rows to draw, the most likely first. A zone with 12 encounters would otherwise fill "
        "the screen; the rows left out are counted on the last line",
    ),
]


def _lua_bool(value):
    """A Lua literal for a setting coming out of overlays.toml."""
    return "true" if value else "false"


def _lua_str(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def lua(cfg):
    """The queries. Always part of the chunk, so --status and --report can describe the bag
    whether or not the readout is drawn."""
    return r"""
-- WHERE THE BAGS LIVE, found once and remembered. The module exports functions and nothing else:
-- the table of zone wrappers (`encounterZonesByMapFile`, keyed by map file and then by zoneUID)
-- and the sentinel the bag is padded with (`NO_ENCOUNTER`) are both upvalues of those functions.
-- Both are looked up BY NAME, so a build that renames one gives nil and this feature reports that
-- it cannot see the bag, rather than reading a neighbouring upvalue and inventing a bag.
--
-- `none` is compared by IDENTITY later: it is a table sentinel, so equality is the test the game
-- itself uses (`if enc == NO_ENCOUNTER then return end`).
local BAG_lib
local function bagLib()
  if BAG_lib ~= nil then return BAG_lib end
  BAG_lib = false
  local m = package.loaded['classes.lists.EncounterZoneList']
  if type(m) ~= 'table' then return nil end
  local lib = {}
  for _, fn in pairs(m) do
    if type(fn) == 'function' then
      if lib.zones == nil then lib.zones = uv(fn, 'encounterZonesByMapFile') end
      if lib.none == nil then lib.none = uv(fn, 'NO_ENCOUNTER') end
    end
  end
  if type(lib.zones) ~= 'table' then return nil end
  BAG_lib = lib
  return lib
end

-- The player's own character. NOT core's playerSprite(), which unwraps to the .sprite: this
-- needs the character, because that is what carries currentTileX/currentTileY.
local function bagPlayer()
  local ok, sp = pcall(function() return spawnableHelper:getPlayerSpawnable() end)
  if ok and type(sp) == 'table' then return sp end
end

-- The bag of the zone under the player, as { zone, map, remain, full, none } - or nil when there
-- is no bag to show (not in the overworld, no zone object here, or a build where the names moved).
--
-- `remain` is the table of entries still in the bag and `full` the multiset it refills from, so
-- "#remain of #full" IS the cycle position. Both are read by upvalue name from the bag's own
-- closures; the bag is a plain table of entries, and the game's own methods are the only thing
-- that ever removes one.
local function bagCurrent()
  local lib = bagLib()
  if not lib then return nil end
  local sp = bagPlayer()
  if not sp then return nil end
  local x, y = sp.currentTileX, sp.currentTileY
  if type(x) ~= 'number' or type(y) ~= 'number' then return nil end
  -- The game asks for the spawnables at the player's OWN tile (not the faced one) when it fishes,
  -- so the same call is the honest answer to "which zone am I in".
  local list
  pcall(function() list = spawnableHelper:findSpawnablesAtTile(sp, x, y) end)
  local zoneUID
  if type(list) == 'table' then
    for i = 1, #list do
      local o = list[i]
      if type(o) == 'table' and type(o.zoneUID) == 'string' and o.zoneUID ~= '' then
        zoneUID = o.zoneUID
        break
      end
    end
  end
  if not zoneUID then return nil end
  local mapFile
  pcall(function() mapFile = worldHelper:getMapFile() end)
  if type(mapFile) ~= 'string' then return nil end
  local perMap = lib.zones[mapFile]
  local wrapper = (type(perMap) == 'table') and perMap[zoneUID] or nil
  if type(wrapper) ~= 'table' then return nil end
  local bag = uv(wrapper.roll, 'encounterZoneShuffleBag')
  if type(bag) ~= 'table' then return nil end
  local remain = uv(bag.roll, 'shuffleBag')
  local full = uv(bag.addAmountOfObject, 'allObjects')
  if type(remain) ~= 'table' then return nil end
  return { zone = zoneUID, map = mapFile, remain = remain, full = full, none = lib.none }
end

-- One display row per DISTINCT encounter slot still in the bag, plus the no-encounter filler as a
-- single row. A slot is not a species: a zone's "Encounter 3" can hold two or three monsters, which
-- is a double or triple battle rather than a split rate, so its monsters are joined on one line.
--
-- The label is built from the MONSTER names rather than the slot's own name ("Encounter 3"), and
-- through core's `loc` with the UID as the fallback, so a language without the key still shows
-- something identifiable.
local function bagRows(remain, none, showNothing)
  local rows, order, nothing = {}, {}, 0
  for i = 1, #remain do
    local e = remain[i]
    if e == none or e == nil then
      nothing = nothing + 1
    elseif type(e) == 'table' then
      local names, lo, hi = {}, nil, nil
      local ms = e.monsters
      if type(ms) == 'table' then
        for j = 1, #ms do
          local m = ms[j]
          if type(m) == 'table' and m.monsterUID ~= nil then
            local uid = tostring(m.monsterUID)
            names[#names + 1] = loc('monsters.' .. uid .. '.name', uid)
            if type(m.minLevel) == 'number' and (lo == nil or m.minLevel < lo) then lo = m.minLevel end
            if type(m.maxLevel) == 'number' and (hi == nil or m.maxLevel > hi) then hi = m.maxLevel end
          end
        end
      end
      local label = (#names > 0) and table.concat(names, '/') or ('slot ' .. tostring(e.name))
      if lo ~= nil and hi ~= nil then
        label = label .. (lo == hi and (' L' .. tostring(lo)) or (' L' .. tostring(lo) .. '-' .. tostring(hi)))
      end
      if order[label] == nil then
        order[label] = #rows + 1
        rows[#rows + 1] = { text = label, n = 0 }
      end
      rows[order[label]].n = rows[order[label]].n + 1
    end
  end
  -- most likely first: the rows are what is LEFT, so a row with more copies is more likely to be
  -- the next thing that comes out of the bag
  table.sort(rows, function(a, b)
    if a.n ~= b.n then return a.n > b.n end
    return a.text < b.text
  end)
  if showNothing and nothing > 0 then
    rows[#rows + 1] = { text = 'nothing', n = nothing }
  end
  return rows, nothing, #remain
end
"""


def section(cfg):
    if not cfg["enabled"]:
        return ""
    # The queries put `bagRows` and `bagCurrent` in scope; this only draws what they answer.
    return r"""
do
  local FONT = 'outline_10_bold'
  local LINE_H = 14
  local WHITE = { 1, 1, 1 }
  local DIM = { 0.62, 0.62, 0.62 }   -- "nothing" is not an encounter, so it does not shout

  -- THE BOTTOM CORNERS ARE INSET, because the game keeps its own readout there: the lead
  -- Coromon's sprite, level and HP sit in the bottom left, and a box drawn over them hides the
  -- thing a player looks at while walking.
  --
  -- The number is in CONTENT units, which is the space everything here is drawn in - this build's
  -- content is 512x288, scaled up to the window (`display.contentWidth/Height`) - so the badge's
  -- ~150 px on a 2560x1440 window is ~30 units. 34 clears it with a little room. Measuring this
  -- in PIXELS is the trap: it puts the box a third of the way up the screen.
  local BOTTOM_INSET = 34
  local EDGE = 6

  local f = makeFeature('bag', 250)
  f.texts = {}

  local function clear()
    drop(f.group)
    f.group, f.bg, f.texts, f.key = nil, nil, {}, nil
  end

  local function cornerX(w)
    if __CORNER__ == 'top-right' or __CORNER__ == 'bottom-right' then
      return (display.contentWidth or 0) - w - EDGE
    end
    return EDGE
  end

  local function cornerY(h, lines)
    if __CORNER__ == 'bottom-left' or __CORNER__ == 'bottom-right' then
      return (display.contentHeight or 0) - h - BOTTOM_INSET
    end
    return EDGE
  end

  -- Draw the rows we were handed. Same shape as the other readouts: the group is built once, the
  -- text objects are reused, and the background is sized to the widest line, so nothing is
  -- reallocated while the bag is not changing.
  local function render(title, rows, extra)
    local lines = { { text = title, col = WHITE } }
    for i = 1, #rows do
      local r = rows[i]
      lines[#lines + 1] = {
        text = '  ' .. r.text .. '  x' .. tostring(r.n),
        col = (r.text == 'nothing') and DIM or WHITE,
      }
    end
    if extra and extra ~= '' then
      lines[#lines + 1] = { text = '  ' .. extra, col = DIM }
    end

    if not f.group then
      local g = display.newGroup()
      pcall(function() g.name = 'encounterBagReadout' end)
      f.bg = rect(g, 0, 0, 10, 10, { 0, 0, 0, 0.6 })
      f.group = g
    end

    local y = 5
    for i = 1, #lines do
      if not f.texts[i] then
        f.texts[i] = text(f.group, FONT, lines[i].text, 8, y, lines[i].col)
      else
        pcall(function() f.texts[i].text = lines[i].text end)
        pcall(function() f.texts[i].y = y end)
        local c = lines[i].col
        if c then pcall(function() f.texts[i]:setFillColor(c[1], c[2], c[3]) end) end
      end
      y = y + LINE_H
    end
    for i = #lines + 1, #f.texts do
      pcall(function() f.texts[i]:removeSelf() end)
      f.texts[i] = nil
    end

    local wmax = 0
    for i = 1, #lines do
      local tw = f.texts[i].width or 0
      if tw > wmax then wmax = tw end
    end
    local w, h = wmax + 16, y + 1
    f.bg.width, f.bg.height = w, h
    f.group.x = cornerX(w)
    f.group.y = cornerY(h, #lines)
    keepOnTop(f.group)
  end

  local function update()
    if not f.on then return end
    local b = bagCurrent()
    if not b then
      -- Not standing in a zone (a menu, a battle, a cave floor, the title screen): take the
      -- readout down rather than leaving a stale bag on screen.
      if f.group or f.key then clear() end
      return
    end
    -- The bag only moves when a roll happens, so the CYCLE POSITION is the whole key: same zone,
    -- same entries left, same full set, and nothing needs rebuilding. Rows are only built on the
    -- frames where that changes, which is a handful of times per minute of walking.
    local n = #b.remain
    local key = b.zone .. '|' .. tostring(n) .. '|' .. tostring(b.full and #b.full or -1)
    if key == f.key then
      keepOnTop(f.group)
      return
    end
    local rows, nothing, total = bagRows(b.remain, b.none, __SHOWNOTHING__)
    local MAX = __MAXLINES__
    local extra = ''
    if #rows > MAX then
      local shown = 0
      for i = MAX + 1, #rows do shown = shown + rows[i].n end
      extra = '+' .. tostring(#rows - MAX) .. ' more rows, ' .. tostring(shown) .. ' entr' ..
        (shown == 1 and 'y' or 'ies')
      while #rows > MAX do table.remove(rows) end
    end
    if total > 0 and #rows == 0 then extra = 'nothing can come out of it until it refills' end
    render(string.format('%s  bag %d/%d', b.zone, n, b.full and #b.full or 0), rows, extra)
    f.key = key
    f.n, f.total, f.nothing = n, b.full and #b.full or nil, nothing
  end

  f.update, f.kill, f.on = update, clear, true
end
""".replace("__CORNER__", _lua_str(cfg["corner"])) \
   .replace("__SHOWNOTHING__", _lua_bool(cfg["show_nothing"])) \
   .replace("__MAXLINES__", str(int(cfg["max_lines"])))


def summary(cfg):
    return r"""(function()
  local b = bagCurrent()
  if not b then return nil end
  return string.format('%s bag: %d of %d entr%s left',
    b.zone, #b.remain, b.full and #b.full or 0, #b.remain == 1 and 'y' or 'ies')
end)()"""


def status(cfg):
    if not cfg["enabled"]:
        return "nil"
    return r"""(function()
  local f = _G.__hud and _G.__hud.feats.bag
  if not f or not f.on then return nil end
  local b = bagCurrent()
  if not b then return 'encounter bag: not standing in a zone' end
  local _rows, nothing = bagRows(b.remain, b.none, true)
  return string.format('encounter bag: %s has %d of %d left (%d of them no-encounter)',
    b.zone, #b.remain, b.full and #b.full or 0, nothing)
end)()"""


def report(cfg):
    return r"""(function()
  local out = {}
  out[#out + 1] = 'encounter bag - what is left of the shuffle bag of the zone you are on'
  local lib = bagLib()
  if not lib then
    out[#out + 1] = '  cannot see the bag: classes.lists.EncounterZoneList is not loaded, or it no'
    out[#out + 1] = '  longer holds encounterZonesByMapFile / NO_ENCOUNTER under those names'
    return table.concat(out, '\n')
  end
  local b = bagCurrent()
  if not b then
    out[#out + 1] = '  no zone under the player (not in the overworld, or this tile has none)'
    return table.concat(out, '\n')
  end
  local rows, nothing, total = bagRows(b.remain, b.none, true)
  out[#out + 1] = string.format('  zone            = %s  (%s)', b.zone, tostring(b.map))
  out[#out + 1] = string.format('  entries left    = %d of %d   (drawn without replacement)',
    total, b.full and #b.full or 0)
  out[#out + 1] = string.format('  no-encounter    = %d of those %d', nothing, total)
  out[#out + 1] = '  still in the bag:'
  for i = 1, #rows do
    out[#out + 1] = string.format('    %-28s x%d', rows[i].text, rows[i].n)
  end
  out[#out + 1] = '  A cycle is every entry drawn once: over a full cycle you get exactly'
  out[#out + 1] = '  stepsWithEncounter of each encounter, and the no-encounter entries are the'
  out[#out + 1] = '  padding that makes most steps roll nothing. The order is random, so what is'
  out[#out + 1] = '  LEFT is the only thing that says what the next steps can produce.'
  return table.concat(out, '\n')
end)()"""
