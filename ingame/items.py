#!/usr/bin/env python3
"""
items.py - markers over the collectable objects on the overworld map.

Coromon scatters items that are invisible until you walk into the tile they sit on. They
are plain Tiled objects with `class = "hiddenItem"` in an object layer, carrying their
contents in up to three item<N>_UID / item<N>_amount slots. Containers (chests) use the
same slot layout, so every item in them is shown.

This reads the live map out of the running game and draws a marker over each one, so you
can see where they are without walking the whole map. Labels show the item's display name,
resolved through the game's own localisation so they match the selected language
(`localise('items.<UID>.name')`).

Collected objects drop out of the markers, but the classes do NOT all share one mechanism
- see stillPresent() below. Only hidden items and chests have been verified for that; the
show_all_items extras are unverified.

Objects with no Tiled name are a special case: there is nothing to match a runtime object
against, so they are held to their layer instead. Conditional layers (whileDEMO,
whileChristmas, afterDEFEAT_GHOST_TITAN, ...) are only instantiated when they apply, and the
objects on the ones that do not apply are not in the game - the demo's chest in harbor being
the one that prompted this.

A location change is NOT handled by the 1 Hz poll: the map is watched every frame (two
cheap reads) and its markers are dropped the moment it changes, then redrawn over a short
settling window while the new map's runtime objects appear. See watch() below.

Items that share a tile are merged into a single marker with one label line each - see
mergeByTile() below for why, and for how often it happens.

The markers are children of the game's own `tiledWorld` node, positioned in map pixel
coordinates, so they scroll with the map for free - and, with the zoom on, scale with it,
which is what you want for something drawn in map space.
"""

NAME = "items"

# Which Tiled object classes to mark. Chests do NOT share the hidden items' mechanism
# despite abstractItemChest:shouldAutomaticallyRemoveItemSpawnable() being a bare
# `return true` in the bytecode: measured on an opened chest after save + reload, it is
# still spawned with its sprite at alpha 1 and the state lives in a consistent save
# property instead (itemChestIsOpened). stillPresent() handles both.
HIDDEN_CLASSES = ["hiddenItem"]
CHEST_CLASSES = ["itemChest", "pyramidItemChest"]
# Every other class that carries an item. Counted from the maps the game actually loads -
# Resources/optimizedMaps, 195 maps, where the class is always written inline so a plain
# scan is enough: hiddenItem 380, drillShovelItem 370, itemChest 326, fruitGrowingPot 42,
# pyramidItemChest 30, item 11, treeItem 1. The loose Resources/maps copies are the Tiled
# source of the same project (192 maps, 1122 carriers) and mostly agree, but their classes
# live in templates, so they MUST be resolved before counting - read raw, every hiddenItem
# looks unclassed. See the README for the tile-level differences that remain.
#
# fruitGrowingPot is deliberately excluded even from --show-all-items: it is a repeatable
# harvester (you plant a fruit and take the yield), not a one-time pickup, so "already
# collected" has no meaning for it.
OTHER_CLASSES = ["drillShovelItem", "item", "treeItem"]

# Marker label font. The game ships only two text sizes - 8 and 10, with _bold only at 10 -
# so this is a straight choice between them, and it applies to EVERY line. Change it here.
#     "small" -> outline_8         (measured 60x15)
#     "big"   -> outline_10_bold   (measured 80x18)
# The line spacing is deliberately tighter than the text object's height: Solar2D reports
# contentHeight == height, i.e. the whole padded line box, and the pixel font's glyphs
# occupy well under that. Lower these further if the stack still looks loose.
FONTS = {
    "small": ("outline_8", 10),
    "big": ("outline_10_bold", 12),
}

DEFAULT_FONT = "small"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "install the item markers"),
    ("show_chests", False, "also mark item chests (itemChest / pyramidItemChest)"),
    (
        "show_all_items",
        False,
        (
            "mark every item-carrying class (hidden items, chests, drill/gem spots, "
            "ground items, tree items)"
        ),
    ),
    (
        "show_collected",
        True,
        (
            "keep marking items that have already been collected. They are drawn dimmed, so "
            "they are still told apart from the ones left on the map: with this on the markers "
            "are a map of where the items ARE, not a list of what is left to pick up"
        ),
    ),
    ("labels", True, "draw the item name above each marker"),
    (
        "font",
        DEFAULT_FONT,
        'label font: "small" is outline_8, "big" is outline_10_bold',
        sorted(FONTS),
    ),
    (
        "settle_frames",
        30,
        (
            "frames to keep re-reading the map after a location change; ends early once "
            "the marker set is stable"
        ),
    ),
    (
        "settle_stable_reads",
        3,
        (
            "identical reads of the new map required before its markers are drawn; the "
            "game has already-collected objects spawned for the first frames"
        ),
    ),
]


def class_list(cfg):
    out = list(HIDDEN_CLASSES)
    if cfg["show_chests"] or cfg["show_all_items"]:
        out += CHEST_CLASSES
    if cfg["show_all_items"]:
        out += OTHER_CLASSES
    return out


def lua(cfg):
    """The queries. show_chests / show_all_items decide which classes are marked, so the
    class table is substituted in here rather than read from a constant."""
    classes = "{" + ", ".join("'" + c + "'" for c in class_list(cfg)) + "}"
    return r"""
-- The object classes treated as collectable.
local ITEM_CLASSES = __CLASSES__

local function isItemClass(c)
  for _, n in ipairs(ITEM_CLASSES) do
    if c == n then return true end
  end
  return false
end

-- Is this item still lying on the map, or has it been picked up?
--
-- The static Tiled data in map.layers is useless for this: it keeps every entry forever,
-- even across a save and reload (verified). The live state is on the runtime objects from
-- MTE.getObjectsAtTile(), and there are TWO different mechanisms:
--
--   * hidden items are REMOVED from the runtime list - their tile keeps only a collision
--     object, so no matching entry means collected;
--   * chests STAY spawned (they have to render their opened sprite) and instead flip a
--     consistent save property: consistentSaveProperties.itemChestIsOpened = true (read off
--     a live collected chest, after save + reload).
--
-- Both are matched by name prefix: the runtime name is the Tiled name plus a suffix
-- (hiddenItem_31_44 -> hiddenItem_31_44_front).
local function runtimeEntry(o)
  local n = o.name
  if type(n) ~= 'string' or n == '' then return nil end
  local m = mte()
  if not m or type(m.getObjectsAtTile) ~= 'function' then return nil end
  local ok, list = pcall(function() return m.getObjectsAtTile(o.tileX, o.tileY) end)
  if not ok or type(list) ~= 'table' then return nil end
  -- Deliberately NOT restricted to the 'interactObjects' layer. Item carriers also live on
  -- layers named items, gems, birds, interactObjects_custom, and on conditional variants
  -- like interactObjects#whileChristmas or afterDEFEAT_GHOST_TITAN. The runtime name is
  -- specific enough on its own, so matching that prefix is necessary and sufficient.
  for _, e in pairs(list) do
    if type(e) == 'table' and type(e.name) == 'string' and e.name:sub(1, #n) == n then
      return e
    end
  end
end

local function stillPresent(o, layerName)
  if type(o.name) ~= 'string' or o.name == '' then
    -- No name to match a runtime object against. The one thing that still says whether this
    -- object exists in the running game is its LAYER: MTE only instantiates objects for the
    -- layer variants that are active, and the maps are full of conditional ones - whileDEMO,
    -- whileChristmas, afterDEFEAT_GHOST_TITAN - whose objects are simply not there.
    --
    -- Measured on harbor (12,14): an `itemChest` on layer whileDEMO carrying the demo's
    -- starter handout (5000 GOLD, 5 SPINNER_REGULAR_3, 10 SCENT_ADD_POTENTIAL_ROLL). The
    -- game had nothing at that tile but the player's own spawnables, so it is not collectable
    -- and must not be marked. A chest stays spawned even once opened, so "no object at all"
    -- means the layer is inactive - not that it was already looted.
    --
    -- So: present only if the tile holds a runtime object on the same layer, comparing base
    -- names because an active conditional layer resolves to its base (interactObjects).
    if not layerName then return true end
    local m = mte()
    if not m or type(m.getObjectsAtTile) ~= 'function' then return true end
    local base = layerName:gsub('#.*$', '')
    local ok, list = pcall(function() return m.getObjectsAtTile(o.tileX, o.tileY) end)
    if not ok or type(list) ~= 'table' then return true end
    for _, e in pairs(list) do
      if type(e) == 'table' and type(e.layerName) == 'string' then
        if e.layerName == layerName or e.layerName:gsub('#.*$', '') == base then return true end
      end
    end
    return false
  end
  local e = runtimeEntry(o)
  if not e then return false end                       -- removed: hidden items
  local sp = e.properties and e.properties.spawnable   -- still spawned: chests
  local csp = type(sp) == 'table' and sp.consistentSaveProperties
  if type(csp) == 'table' then
    for k, v in pairs(csp) do
      -- itemChestIsOpened; matched loosely so a variant flag still works
      if type(k) == 'string' and k:lower():find('opened') and v then return false end
    end
  end
  return true
end

-- Every item-carrying object on the loaded map. Hidden items live on map.layers[*].objects,
-- NOT in MTE.getObjects(), which only returns tile-collision objects.
local function itemCollect()
  local m = mte()
  if not m or type(m.getMap) ~= 'function' then return nil, nil end
  local ok, map = pcall(m.getMap)
  if not ok or type(map) ~= 'table' then return nil, nil end
  local items = {}
  for _, L in pairs(map.layers) do
    if type(L) == 'table' and type(L.objects) == 'table' then
      for _, o in pairs(L.objects) do
        if type(o) == 'table' and isItemClass(o.class)
           and type(o.tileX) == 'number' and type(o.tileY) == 'number' then
          -- properties turn up under either name depending on the object
          local P = o.tiledProperties or o.properties or {}
          -- Containers can hold up to THREE items, in item<N>_UID / item<N>_amount slots.
          -- An empty slot is a nil UID with amount 0, so both are tested. Growing pots,
          -- ground items and tree items use a plain itemUID instead.
          local lines, uids = {}, {}
          for n = 1, 3 do
            local u = P['item' .. n .. '_UID']
            local a = tonumber(P['item' .. n .. '_amount']) or 0
            if u ~= nil and a > 0 then
              local nm = loc('items.' .. tostring(u) .. '.name', tostring(u))
              -- counts are only appended above 1 - "Silver Spinner x1" is noise
              lines[#lines + 1] = a > 1 and (nm .. ' x' .. a) or nm
              uids[#uids + 1] = tostring(u)
            end
          end
          if #lines == 0 and P.itemUID ~= nil then
            lines[1] = loc('items.' .. tostring(P.itemUID) .. '.name', tostring(P.itemUID))
            uids[1] = tostring(P.itemUID)
          end
          if #lines == 0 then
            lines[1], uids[1] = '?', '?'
          end
          items[#items + 1] = {
            tx = o.tileX, ty = o.tileY,
            uid = uids[1], name = lines[1],
            lines = lines, uids = uids, kind = o.class,
            layer = L.name,
            collected = not stillPresent(o, L.name),
          }
        end
      end
    end
  end
  table.sort(items, function(a, b)
    if a.ty ~= b.ty then return a.ty < b.ty end
    return a.tx < b.tx
  end)
  return map, items
end

local function playerTile()
  local tx, ty
  pcall(function()
    local i = spawnableHelper:getPlayerSpawnable()
    if i then tx, ty = i.currentTileX, i.currentTileY end
  end)
  return tx, ty
end

-- One tile can carry more than one collectable: the carriers are separate Tiled objects on
-- separate layers, so a hidden item and a drill/gem spot can share a square. A marker per
-- object would stack two identical 16x16 boxes and two labels in the same spot, so items
-- sharing a tile are merged into one marker and their names become stacked label lines.
--
-- Exhaustive scan of the 195 maps the game loads (Resources/optimizedMaps): 1160 item
-- carriers, and exactly four tiles hold two of them - three of which are mutually exclusive
-- conditional variants (the same object duplicated on whileX / afterX layers, only one of
-- which ever loads). The one real case is desertRoute_5 (8,33): GOLD x2000 and Green Gem,
-- so this matters for a single tile in the game - and only until the gold is picked up.
local function mergeByTile(items)
  local by, order = {}, {}
  for _, it in ipairs(items) do
    local key = it.tx .. ':' .. it.ty
    local e = by[key]
    if not e then
      e = {
        tx = it.tx, ty = it.ty, kind = it.kind,
        lines = {}, uids = {}, collected = it.collected,
      }
      by[key] = e
      order[#order + 1] = e
    end
    for i = 1, #it.lines do
      e.lines[#e.lines + 1] = it.lines[i]
      e.uids[#e.uids + 1] = it.uids[i]
    end
    -- a chest or gem spot outranks a plain hidden item, so the amber marker wins
    if it.kind ~= 'hiddenItem' then e.kind = it.kind end
    if not it.collected then e.collected = false end
  end
  for _, e in ipairs(order) do
    e.name, e.uid = e.lines[1], e.uids[1]
  end
  return order
end

-- What is drawn, one entry per tile, and WHICH items are in it depends on `show_collected`:
--
--   off - only what is still lying there (the older behaviour), so a picked-up item never
--         shows up alongside a live one on the same tile;
--   ON  - everything the map defines, collected included, so the markers say where the items
--         ARE rather than what is left. Merge still folds a tile's items into one marker.
--
-- The caller draws the collected ones DIMMED (see draw()), which is what makes keeping them
-- useful: they read as a record rather than as something still to walk to.
local function visibleByTile(items)
  local out = {}
  for _, it in ipairs(items) do
    if __SHOWCOLLECTED__ or not it.collected then out[#out + 1] = it end
  end
  return mergeByTile(out)
end
""".replace("__CLASSES__", classes).replace(
    "__SHOWCOLLECTED__", "true" if cfg["show_collected"] else "false"
)


def section(cfg):
    if not cfg["enabled"]:
        return ""
    font, line_h = FONTS.get(cfg["font"], FONTS[DEFAULT_FONT])
    return (
        r"""
do
  local f = makeFeature('items', 1000)
  f.labels = __LABELS__
  f.count = 0

  -- HOW MUCH ALPHA AN ALREADY-COLLECTED MARKER KEEPS (see show_collected). A live marker is 0.85
  -- (hidden item) or 0.9 (chest), so this is the one step that separates "already taken" from
  -- "still to walk to" - raised from 0.3, which was too faint to find the spot on the map.
  local DIMMED = 0.6

  local function clear()
    drop(f.group)
    f.group, f.world, f.map, f.sig = nil, nil, nil, nil
  end

  local function signature(items)
    local parts = {}
    for _, it in ipairs(items) do parts[#parts + 1] = it.tx .. ':' .. it.ty end
    return table.concat(parts, ',')
  end

  local function draw(map, items, tw)
    clear()
    local g = display.newGroup()
    pcall(function() g.name = 'itemMarks' end)
    for _, it in ipairs(items) do
      -- map-local pixel space: the tile's top-left corner is (tx*16, ty*16), which is
      -- exactly where the engine places a sprite's tile
      local x0, y0 = it.tx * 16, it.ty * 16
      -- white for hidden items, amber for chests, so the two are distinguishable - and a
      -- COLLECTED one keeps its hue but drops to DIMMED, which is what stops "already taken"
      -- from looking like an item still to walk to.
      local alpha
      if it.kind == 'hiddenItem' then alpha = it.collected and DIMMED or 0.85
      else alpha = it.collected and DIMMED or 0.9 end
      local col = (it.kind == 'hiddenItem') and { 1, 1, 1, alpha } or { 1, 0.82, 0.25, alpha }
      rect(g, x0,      y0,      16, 1,  col)    -- top
      rect(g, x0,      y0 + 15, 16, 1,  col)    -- bottom
      rect(g, x0,      y0,      1,  16, col)    -- left
      rect(g, x0 + 15, y0,      1,  16, col)    -- right
      if f.labels then
        -- Every line uses the same font. The block is lifted so its LAST line still sits
        -- just above the tile, which keeps the first item highest.
        local n = #it.lines
        for i = 1, n do
          local t = text(g, __FONT__, it.lines[i], x0 + 8, y0 - 2 - (n - i) * __LINE_H__)
          -- the name dims with its marker, or a collected item's label would shout as loudly as
          -- a live one's
          if it.collected then pcall(function() t.alpha = DIMMED end) end
        end
      end
    end
    tw:insert(g)          -- appended last, so drawn over every map layer
    f.group, f.world, f.map, f.sig, f.count = g, tw, map, signature(items), #items
  end

  -- The collectable objects still on the map, one entry per tile - or nil when there is
  -- nothing readable yet (no world node, or no map).
  local function current()
    local tw = twNode()
    if type(tw) ~= 'table' then return nil end
    local map, items = itemCollect()
    if not map or not items then return nil end
    return map, visibleByTile(items), tw
  end

  local function update()
    if not f.on then return end
    -- While a location change is settling, watch() below owns the markers: it holds them
    -- back until the answer stops moving, and this poll - which can land anywhere in that
    -- window - must not draw the half-built set in the meantime.
    if (f.settle or 0) > 0 then return end
    local map, vis, tw = current()
    if not map then return end
    -- redraw on map change, if our group was dropped, or when the set of items that are
    -- still there changed - i.e. the moment you walk onto one and pick it up
    if map == f.map and tw == f.world and f.group and f.group.parent
       and signature(vis) == f.sig then
      return
    end
    draw(map, vis, tw)
  end

  -- REACTING TO A LOCATION CHANGE.
  --
  -- The 1 Hz poll above is right for picking things up, but far too slow for a transition:
  -- the map can change and the poll can be most of a second away, and until it runs f.sig
  -- still holds the PREVIOUS map's markers. Measured with a per-frame recorder: entering
  -- another map, our drawn set was the old map's for 13 frames before the poll corrected it.
  --
  -- So the map and the world node are watched every frame. Both reads are cheap, and a
  -- location change is exactly when one of them changes. On a change the old markers are
  -- dropped at once, so nothing from the previous map is ever left on screen.
  --
  -- Drawing immediately is NOT enough, and is what the first attempt got wrong. On the
  -- frames right after a map loads the game has every carrier spawned and has not yet
  -- removed or flagged the ones that were already collected, so the set reads as a SUPERSET:
  -- measured on luxSolisTown, 12 markers - every carrier on the map - against the 7 that are
  -- really still there, with the correct answer arriving two frames later. Drawing that
  -- superset is the pop. So the new map is read every frame but drawn only once its answer
  -- has stopped changing for SETTLE_STABLE_READS frames, which also means an empty set
  -- (what a not-yet-populated map looks like) can never end the window early.
  local settle, lastKey, same = 0, nil, 0

  -- This runs on EVERY frame - including the title screen and the game's own start-up - so
  -- it must not be able to take the game down. A listener that throws on every frame is
  -- exactly what freezes a Solar2D game, so the body runs inside pcall: a bad frame is a
  -- skipped frame, and if it keeps failing the watcher removes itself rather than throw for
  -- ever. The 1 Hz poll below still draws, so the markers survive that too.
  local function watchStep()
    local tw = twNode()
    local map
    local m = mte()
    if type(m) == 'table' and type(m.getMap) == 'function' then
      local ok, got = pcall(m.getMap)
      if ok then map = got end
    end
    -- only react to a positive signal, so a moment where neither is readable is not
    -- mistaken for a location change
    if (tw ~= nil and tw ~= f.world) or (map ~= nil and map ~= f.map) then
      clear()
      f.count = nil
      settle, lastKey, same = __SETTLE_FRAMES__, nil, 0
      -- Remember what is being settled FOR. clear() has just set both to nil, so without
      -- this the test above would fire again next frame and reset the window for ever.
      f.world, f.map = tw, map
    end
    f.settle = settle
    if settle <= 0 then return end
    settle = settle - 1
    local rowMap, vis, rowTw = current()
    if not rowMap then return end
    local key = signature(vis)
    if key == lastKey then
      same = same + 1
    else
      same, lastKey = 0, key
    end
    if same >= __SETTLE_STABLE_READS__ or settle <= 0 then
      -- drawn once the answer stopped moving - or, if the window ran out first, whatever
      -- the latest read says, so the markers can never be held back for good
      settle, f.settle = 0, 0
      draw(rowMap, vis, rowTw)
    end
  end

  local function watch()
    if not f.on then return end
    local ok, err = pcall(watchStep)
    if ok then
      f.watchFails = 0
    else
      f.watchFails = (f.watchFails or 0) + 1
      f.lastWatchError = tostring(err)
      if f.watchFails > 30 then
        pcall(function() Runtime:removeEventListener('enterFrame', watch) end)
        f.watchOff = true
      end
    end
  end
  Runtime:addEventListener('enterFrame', watch)

  local function kill()
    pcall(function() Runtime:removeEventListener('enterFrame', watch) end)
    clear()
  end

  f.update, f.kill, f.on = update, kill, true
end
""".replace("__LABELS__", "true" if cfg["labels"] else "false")
        .replace("__FONT__", "'" + font + "'")
        .replace("__LINE_H__", str(line_h))
        .replace("__SETTLE_FRAMES__", str(int(cfg["settle_frames"])))
        .replace("__SETTLE_STABLE_READS__", str(int(cfg["settle_stable_reads"])))
    )


def summary(cfg):
    return r"""(function()
  local map = itemCollect()
  if not map then return 'item markers (will draw once a map is loaded)' end
  local f = _G.__hud.feats.items
  return string.format('item markers (%d marked on %s%s)', (f and f.count) or 0,
    tostring(map.path or ''), tostring(map.filename or ''))
end)()"""


def status(cfg):
    return r"""(function()
  local h = _G.__hud
  local f = h and h.feats.items
  if not f or not f.on then return nil end
  return string.format('item markers: %d marked on this map%s',
    f.count or 0, f.labels and '' or ' (labels off)')
end)()"""


def report(cfg):
    return r"""(function()
  local map, items = itemCollect()
  if not map then return 'overworld - no map loaded' end
  local out = {
    string.format('overworld - %s%s   %sx%s tiles   %d collectable object(s)',
      tostring(map.path), tostring(map.filename),
      tostring(map.width), tostring(map.height), #items),
  }
  local ptx, pty = playerTile()
  if ptx then
    out[#out + 1] = string.format('player is on tile (%d, %d)', ptx, pty)
  end
  for _, it in ipairs(items) do
    local tag = ''
    if ptx then
      local dx, dy = it.tx - ptx, it.ty - pty
      if math.abs(dx) + math.abs(dy) == 1 then
        tag = '   <-- adjacent to you (' .. (dx == -1 and 'left' or dx == 1 and 'right'
          or dy == -1 and 'up' or 'down') .. ')'
      end
    end
    local kind = (it.kind == 'hiddenItem') and '' or ('   [' .. it.kind .. ']')
    -- the layer matters: an object on a conditional layer that does not apply to this save
    -- (whileDEMO and friends) is not in the game at all and cannot be collected
    local where = it.layer and ('   on ' .. it.layer) or ''
    out[#out + 1] = string.format('  tile (%3d,%3d)  %-24s   (%s)%s%s%s%s',
      it.tx, it.ty, it.lines[1], it.uids[1], kind, where,
      it.collected and '   [already collected]' or '', tag)
    -- a container can hold up to three items; the rest sit under the top name
    for i = 2, #it.lines do
      out[#out + 1] = string.format('  %-13s  %-24s   (%s)', '', it.lines[i], it.uids[i])
    end
  end
  return table.concat(out, '\n')
end)()"""
