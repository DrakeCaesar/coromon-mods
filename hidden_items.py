#!/usr/bin/env python3
"""
hidden_items.py - find and highlight Coromon's hidden overworld items.

Coromon scatters items that are invisible until you walk into the tile they sit
on. They are plain Tiled objects with `class = "hiddenItem"` in an object layer,
carrying their contents in up to three item<N>_UID / item<N>_amount slots.
Containers (chests) use the same slot layout, so every item in them is shown.

This reads the live map out of the running game and draws a marker over each one,
so you can see where they are without walking the whole map. Labels show the
item's display name, resolved through the game's own localisation so they match
the selected language (`localise('items.<UID>.name')`).

Usage:
    python hidden_items.py             # draw the markers
    python hidden_items.py --list      # just print them, draw nothing
    python hidden_items.py --off       # remove the markers
    python hidden_items.py --no-labels # markers only, no item names
    python hidden_items.py --crates    # also mark item chests (amber borders)
    python hidden_items.py --all       # every item-carrying class

Chests are off by default because they are visible objects anyway; when enabled they
get amber borders so they can be told apart from the white hidden-item borders.

Collected objects drop out of the markers, but the classes do NOT all share one
mechanism - see stillPresent() below. Only hidden items and chests have been verified
for that; the --all extras are unverified.

The markers are children of the game's own `tiledWorld` node, positioned in map
pixel coordinates, so they scroll with the map for free - no per-frame work. A
Lua watchdog re-draws them when you change map or the world node is rebuilt, so
the tool exits immediately after installing.

Requires frida (`pip install frida`) and the game running in the overworld.
"""

import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from coromon_lua import MINIMAL_HOOKS, Bridge  # noqa: E402

# Which Tiled object classes to mark. Note chests do NOT share the hidden items'
# mechanism despite abstractItemChest:shouldAutomaticallyRemoveItemSpawnable() being a
# bare `return true` in the bytecode: measured on an opened chest after save + reload,
# it is still spawned with its sprite at alpha 1 and the state lives in a consistent
# save property instead (itemChestIsOpened). stillPresent() handles both.
HIDDEN_CLASSES = ["hiddenItem"]
CHEST_CLASSES = ["itemChest", "pyramidItemChest"]
# Every other class that carries an item, inventoried across all 194 shipped map files
# with Tiled templates resolved (most of these objects get their class via a template,
# not inline): hiddenItem 371, itemChest 310, fruitGrowingPot 41, pyramidItemChest 30,
# drillShovelItem 11, item 9, treeItem 1.
#
# fruitGrowingPot is deliberately excluded even from --all: it is a repeatable
# harvester (you plant a fruit and take the yield), not a one-time pickup, so
# "already collected" has no meaning for it.
OTHER_CLASSES = ["drillShovelItem", "item", "treeItem"]

# Marker label font. The game ships only two text sizes - 8 and 10, with _bold only at
# 10 - so this is a straight choice between them, and it applies to EVERY line: the
# first item is no longer emphasised. Change it here.
#     "small" -> outline_8         (measured 60x15)
#     "big"   -> outline_10_bold   (measured 80x18)
# The line spacing is deliberately tighter than the text object's height: Solar2D
# reports contentHeight == height, i.e. the whole padded line box, and the pixel font's
# glyphs occupy well under that. Lower these further if the stack still looks loose.
FONT = "small"
FONTS = {
    "small": ("outline_8", 10),
    "big": ("outline_10_bold", 12),
}


def _font_of(choice):
    return FONTS.get(str(choice).lower(), FONTS["small"])


def _sub(code, classes_lua, font_type, line_h):
    """Fill in the placeholders the Lua snippets are written with."""
    return (
        code.replace("__CLASSES__", classes_lua)
        .replace("__FONT__", "'" + font_type + "'")
        .replace("__LINE_H__", str(line_h))
    )


def _lua_classes(classes):
    return "{" + ", ".join("'" + c + "'" for c in classes) + "}"


# ---------------------------------------------------------------------------
# Shared helpers. Two things learned the hard way elsewhere in tools/:
#   * the world node must be re-resolved, not cached - a cached one goes stale
#     (its .x becomes nil) when the game rebuilds the map;
#   * hidden items live on map.layers[*].objects, NOT in MTE.getObjects(), which
#     only returns tile-collision objects.
# ---------------------------------------------------------------------------
PRELUDE = r"""
local function world()
  local MTE = _G.MTE
  if type(MTE) ~= 'table' then return nil end
  local f = MTE.getTiledWorld
  if type(f) ~= 'function' then return nil end
  for i = 1, 120 do
    local n, v = debug.getupvalue(f, i)
    if not n then return nil end
    if n == 'tiledWorld' then return v end
  end
end

local function tiny(o)
  if type(o) ~= 'table' then return tostring(o) end
  local ks = {}
  for k in pairs(o) do ks[#ks + 1] = tostring(k) end
  table.sort(ks)
  return '{' .. table.concat(ks, ',') .. '}'
end

local function itemName(uid)
  -- The game's own localisation, so labels match the selected language.
  -- Key shape is 'items.<UID>.name'. A MISSING key comes back as '???' rather
  -- than nil (checked live), so it has to be tested for explicitly.
  local ok, n = pcall(localise, 'items.' .. uid .. '.name')
  if ok and type(n) == 'string' and n ~= '' and n ~= '???' then return n end
  return uid
end

-- The object classes treated as collectable. __CLASSES__ is substituted on the
-- Python side: hiddenItem always, plus the chest classes when --crates is passed.
local ITEM_CLASSES = __CLASSES__

local function isItemClass(c)
  for _, n in ipairs(ITEM_CLASSES) do
    if c == n then return true end
  end
  return false
end

-- Is this item still lying on the map, or has it been picked up?
--
-- The static Tiled data in map.layers is useless for this: it keeps every entry
-- forever, even across a save and reload (verified). The live state is on the runtime
-- objects from MTE.getObjectsAtTile(), and there are TWO different mechanisms:
--
--   * hidden items are REMOVED from the runtime list - their tile keeps only a
--     collision object, so no matching entry means collected;
--   * chests STAY spawned (they have to render their opened sprite) and instead flip
--     a consistent save property: consistentSaveProperties.itemChestIsOpened = true
--     (read off a live collected chest, after save + reload).
--
-- Both are matched by name prefix: the runtime name is the Tiled name plus a suffix
-- (hiddenItem_31_44 -> hiddenItem_31_44_front).
local function runtimeEntry(o)
  local n = o.name
  if type(n) ~= 'string' or n == '' then return nil end
  local MTE = _G.MTE
  local ok, list = pcall(function() return MTE.getObjectsAtTile(o.tileX, o.tileY) end)
  if not ok or type(list) ~= 'table' then return nil end
  -- Deliberately NOT restricted to the 'interactObjects' layer. Item carriers also
  -- live on layers named items, gems, birds, interactObjects_custom, and on
  -- conditional variants like interactObjects#whileChristmas or afterDEFEAT_GHOST_TITAN.
  -- The runtime name is the Tiled name plus a suffix, which is specific enough on its
  -- own, so matching that prefix is both necessary and sufficient.
  for _, e in pairs(list) do
    if type(e) == 'table' and type(e.name) == 'string'
       and e.name:sub(1, #n) == n then
      return e
    end
  end
  return nil
end

local function stillPresent(o)
  if type(o.name) ~= 'string' or o.name == '' then return true end
  local e = runtimeEntry(o)
  if not e then return false end                       -- removed: hidden items
  local sp = e.properties and e.properties.spawnable    -- still spawned: chests
  local csp = type(sp) == 'table' and sp.consistentSaveProperties
  if type(csp) == 'table' then
    for k, v in pairs(csp) do
      -- itemChestIsOpened; matched loosely so a variant flag still works
      if type(k) == 'string' and k:lower():find('opened') and v then return false end
    end
  end
  return true
end

local function collect()
  local MTE = _G.MTE
  if type(MTE) ~= 'table' then return nil, nil end
  local ok, map = pcall(MTE.getMap)
  if not ok or type(map) ~= 'table' then return nil, nil end
  local items = {}
  for k, L in pairs(map.layers) do
    if type(L) == 'table' and type(L.objects) == 'table' then
      for kk, o in pairs(L.objects) do
        if type(o) == 'table' and isItemClass(o.class)
           and type(o.tileX) == 'number' and type(o.tileY) == 'number' then
          -- properties turn up under either name depending on the object
          local P = o.tiledProperties or o.properties or {}
          -- Containers can hold up to THREE items, in item<N>_UID / item<N>_amount
          -- slots. An empty slot is a nil UID with amount 0, so both are tested.
          -- Growing pots, ground items and tree items use a plain itemUID instead.
          local lines, uids = {}, {}
          for n = 1, 3 do
            local u = P['item' .. n .. '_UID']
            local a = tonumber(P['item' .. n .. '_amount']) or 0
            if u ~= nil and a > 0 then
              local nm = itemName(tostring(u))
              -- counts are only appended above 1 - "Silver Spinner x1" is noise
              lines[#lines + 1] = a > 1 and (nm .. ' x' .. a) or nm
              uids[#uids + 1] = tostring(u)
            end
          end
          if #lines == 0 and P.itemUID ~= nil then
            lines[1] = itemName(tostring(P.itemUID))
            uids[1] = tostring(P.itemUID)
          end
          if #lines == 0 then
            lines[1], uids[1] = '?', '?'
          end
          items[#items + 1] = {
            tx = o.tileX, ty = o.tileY,
            -- lines[1] is the "top name"; lines/uids carry the whole contents
            uid = uids[1],
            name = lines[1],
            amount = tonumber(P.item1_amount) or 1,
            lines = lines,
            uids = uids,
            label = lines[1],
            kind = o.class,
            layer = tostring(o.layerName or '?'),
            collected = not stillPresent(o),
            props = tiny(P),
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
"""

LIST = (
    PRELUDE
    + r"""
local map, items = collect()
if not map then return '!no map loaded - are you in the overworld?!' end
local out = { string.format('%s%s   %sx%s tiles   %d collectable object(s)',
  tostring(map.path), tostring(map.filename), tostring(map.width), tostring(map.height), #items) }
local ptx, pty = playerTile()
if ptx then
  out[#out + 1] = string.format('player is on tile (%d, %d)', ptx, pty)
end
if #items == 0 then
  out[#out + 1] = '  (none on this map)'
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
  out[#out + 1] = string.format('  tile (%3d,%3d)  %-24s   (%s)%s%s%s',
    it.tx, it.ty, it.lines[1], it.uids[1], kind,
    it.collected and '   [already collected]' or '', tag)
  -- a container can hold up to three items; the rest sit under the top name
  for i = 2, #it.lines do
    out[#out + 1] = string.format('  %-13s  %-24s   (%s)', '', it.lines[i], it.uids[i])
  end
end
return table.concat(out, '\n')
"""
)

SHOW = (
    PRELUDE
    + r"""
local map, items = collect()
if not map then return '!no map loaded - are you in the overworld?!' end
local tw = world()
if type(tw) ~= 'table' then return '!no tiledWorld!' end

local s = _G.__hidden or {}
_G.__hidden = s
s.on = true
s.labels = __LABELS__

local function clear()
  if s.group then
    if s.group.parent then pcall(function() s.group:removeSelf() end) end
    s.group = nil
  end
end

local function visible(items)
  local v = {}
  for _, it in ipairs(items) do
    if not it.collected then v[#v + 1] = it end
  end
  return v
end

local function signature(items)
  local parts = {}
  for _, it in ipairs(items) do parts[#parts + 1] = it.tx .. ':' .. it.ty end
  return table.concat(parts, ',')
end

local function draw(map, items, tw)
  clear()
  local g = display.newGroup()
  pcall(function() g.name = 'hiddenItemMarks' end)
  for _, it in ipairs(items) do
    -- map-local pixel space: the tile's top-left corner is (tx*16, ty*16), which
    -- is exactly where the engine places a sprite's tile
    local x0, y0 = it.tx * 16, it.ty * 16
    -- Coromon's wrapped display.newRect has NO stroke support at all
    -- (setStrokeColor is nil on it, and rectHelper:newLineRect wants a game-
    -- internal "rect mutator" object), so the border is drawn as four 1px filled
    -- edges. Anchored at 0,0 on integer coordinates, which keeps it crisp at the
    -- game's x3 content scale.
    local edges = {
      { x0,      y0,      16, 1 },    -- top
      { x0,      y0 + 15, 16, 1 },    -- bottom
      { x0,      y0,      1,  16 },   -- left
      { x0 + 15, y0,      1,  16 },   -- right
    }
    -- white for hidden items, amber for chests, so the two are distinguishable
    local col = (it.kind == 'hiddenItem') and { 1, 1, 1, 0.85 } or { 1, 0.82, 0.25, 0.9 }
    for _, e in ipairs(edges) do
      local r = display.newRect(0, 0, e[3], e[4])
      r.anchorX, r.anchorY = 0, 0
      r.x, r.y = e[1], e[2]
      local c = col
      pcall(function() r:setFillColor(c[1], c[2], c[3], c[4]) end)
      g:insert(r)
    end
    if s.labels then
      -- Every line uses the same font - see FONT at the top of the Python. The block
      -- is lifted so its LAST line still sits just above the tile, which keeps the
      -- first item highest.
      local n = #it.lines
      for i = 1, n do
        local t = textHelper:new(g, __FONT__, { text = it.lines[i] })
        t.x, t.y = x0 + 8, y0 - 2 - (n - i) * __LINE_H__
        g:insert(t)
      end
    end
  end
  tw:insert(g)          -- appended last, so drawn over every map layer
  s.group, s.world, s.map, s.count = g, tw, map, #items
  s.sig = signature(items)
end

local function apply()
  if not s.on then return end
  local tw = world()
  if type(tw) ~= 'table' then return end
  local ok, map = pcall(_G.MTE.getMap)
  if not ok or type(map) ~= 'table' then return end
  local _, items = collect()
  if not items then return end
  local vis = visible(items)
  -- redraw on map change, if our group was dropped, or when the set of items that
  -- are still there changed - i.e. the moment you walk onto one and pick it up
  if map == s.map and tw == s.world and s.group and s.group.parent
     and signature(vis) == s.sig then
    return
  end
  draw(map, vis, tw)
end

s.apply = apply
local vis = visible(items)
draw(map, vis, tw)
if s.timer then pcall(function() timer.cancel(s.timer) end) end
s.timer = timer.performWithDelay(1000, function() s.apply() end, 0)

local names = {}
for _, it in ipairs(vis) do names[#names + 1] = it.label end
return string.format('%d of %d collectable(s) still there on %s%s\n  %s',
  #vis, #items, tostring(map.path), tostring(map.filename), table.concat(names, ', '))
"""
)

OFF = r"""
local s = _G.__hidden
if not s then return 'nothing to remove' end
s.on = false
if s.timer then pcall(function() timer.cancel(s.timer) end) s.timer = nil end
if s.group and s.group.parent then pcall(function() s.group:removeSelf() end) end
_G.__hidden = nil
return 'hidden item highlights removed'
"""


def _bridge(process):
    b = Bridge(process, hooks=MINIMAL_HOOKS)
    for _ in range(150):
        if b.status().get("state"):
            break
        time.sleep(0.2)
    return b


def _eval(b, code, timeout=30.0):
    r = b.eval(code, timeout=timeout)
    return r.get("out") or r.get("err")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="coromon.exe")
    ap.add_argument(
        "--list", action="store_true", help="print the hidden items, draw nothing"
    )
    ap.add_argument("--off", action="store_true", help="remove the highlights")
    ap.add_argument(
        "--no-labels", action="store_true", help="markers only, no item names"
    )
    ap.add_argument(
        "--crates",
        action="store_true",
        help="also mark item chests (itemChest / pyramidItemChest), not just hidden items",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="mark every item-carrying class (hidden items, chests, drill/gem spots, "
        "ground items, tree items)",
    )
    args = ap.parse_args()

    classes = list(HIDDEN_CLASSES)
    if args.crates or args.all:
        classes += CHEST_CLASSES
    if args.all:
        classes += OTHER_CLASSES
    cls_lua = _lua_classes(classes)
    font_type, line_h = _font_of(FONT)

    b = _bridge(args.process)
    if args.off:
        print(_eval(b, OFF))
    elif args.list:
        print(_eval(b, _sub(LIST, cls_lua, font_type, line_h)))
    else:
        print(
            _eval(
                b,
                _sub(SHOW, cls_lua, font_type, line_h).replace(
                    "__LABELS__", "false" if args.no_labels else "true"
                ),
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
