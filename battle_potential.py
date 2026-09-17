#!/usr/bin/env python3
"""
battle_potential.py - show the on-field opponents' Potential on screen during battle.

Coromon hides a monster's Potential until you catch it, and the two high tiers are what
make a catch worth keeping. This draws it over the battle so you can decide before
spending a spinner. There is no in-game "potency" field anywhere - it is always
`potential`, 1-21.

How the monsters are found:

    Battle:get()                        -- the live battle instance
      :getMonsterSpritesInBattle()      -- the on-field sprites
        [i].side == 'front'             -- 'front' is an opponent, 'back' is yours
        [i].monster                     -- the Monster object
          .potential                    -- the number (1-21)
          :getLevel()                   -- its level

A double battle fields more than one 'front' sprite at once, so every one of them is
collected - ordered left-to-right by sprite x - and each gets its own block.

Nothing is hardcoded: the display name comes from the game's own localisation
(`localise('monsters.<UID>.name')` -> "Buzzlet"), and the tier from
`monsterUtility:getPotentialCategoryForPotential()` -> 'A'/'B'/'C', which the game
then localises through `global.monsterPotentialCategory.<cat>`:

    A = Standard (1-16)      B = Potent (17-20)      C = Perfect (21)

The letter is the game's own internal name for the tier; the word is what the game
shows the player, so that is what gets drawn.

Usage:
    python battle_potential.py          # draw it during battle
    python battle_potential.py --once   # print every on-field opponent and exit
    python battle_potential.py --off    # remove the overlay

The overlay is installed inside the game and updates itself via a Lua timer, so this
exits immediately and keeps working for every later encounter. Requires frida
(`pip install frida`) and the game running.
"""

import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from coromon_lua import MINIMAL_HOOKS, Bridge  # noqa: E402

PRELUDE = r"""
local FONT = 'outline_10_bold'
local LINE_H = 14

-- The on-field opponents. getMonsterSpritesInBattle() marks each sprite with a `side`:
-- 'front' is an opponent, 'back' is the player's. That is authoritative for trainer
-- battles too, where every opposing monster lacks a catchDate. A double battle fields
-- more than one at once, so this returns a list, ordered left-to-right on screen.
local function opponents()
  local ok, b = pcall(function() return Battle:get() end)
  if not ok or type(b) ~= 'table' then return {} end
  local ok2, sprites = pcall(function() return b:getMonsterSpritesInBattle() end)
  if not ok2 or type(sprites) ~= 'table' then return {} end
  local found = {}
  for _, sp in pairs(sprites) do
    if type(sp) == 'table' and sp.side == 'front' and type(sp.monster) == 'table' then
      found[#found + 1] = { x = tonumber(sp.x) or 0, m = sp.monster }
    end
  end
  table.sort(found, function(a, c) return a.x < c.x end)
  local out = {}
  for i = 1, #found do out[i] = found[i].m end
  return out
end

local function loc(key, fallback)
  local ok, v = pcall(localise, key)
  if ok and type(v) == 'string' and v ~= '' and v ~= '???' then return v end
  return fallback
end

local function localisedName(uid)
  return loc('monsters.' .. uid .. '.name', uid)
end

local function factFor(m)
  local pot = tonumber(m.potential)
  if pot == nil then return nil end
  local uid = tostring(m.UID)
  local ok, lvl = pcall(function() return m:getLevel() end)
  if not ok then lvl = nil end
  local cat = 'A'
  local okc, c = pcall(function()
    return monsterUtility:getPotentialCategoryForPotential(pot)
  end)
  if okc and type(c) == 'string' then cat = c end
  return {
    uid = uid, name = localisedName(uid), level = lvl, pot = pot, cat = cat,
    catName = loc('global.monsterPotentialCategory.' .. cat, cat),
  }
end

-- every opponent on the field, plus a signature used to spot when it changes
local function facts()
  local ms = opponents()
  local out, sig = {}, {}
  for i = 1, #ms do
    local f = factFor(ms[i])
    if f then
      out[#out + 1] = f
      sig[#sig + 1] = f.uid .. '|' .. tostring(f.pot) .. '|' .. tostring(f.level)
    end
  end
  return out, table.concat(sig, ';')
end
"""

ONCE = (
    PRELUDE
    + r"""
local fs = facts()
if #fs == 0 then return 'no battle in progress (or no opponent on the field)' end
local lines = {}
for i = 1, #fs do
  local f = fs[i]
  lines[#lines + 1] = string.format('%s   Lv%s', f.name, tostring(f.level))
  lines[#lines + 1] = string.format('Potential %d  (%s)', f.pot, f.catName)
end
return table.concat(lines, '\n')
"""
)

SHOW = (
    PRELUDE
    + r"""
local s = _G.__battlepot or {}
_G.__battlepot = s
s.on = true

local WHITE = { 1, 1, 1 }
-- the game's own categories; B and C are the ones worth spending a spinner on
local COL = {
  A = WHITE,                -- standard, potential 1-16
  B = { 0.45, 1, 0.45 },    -- potent,   potential 17-20
  C = { 1, 0.85, 0.2 },     -- perfect,  potential 21
}

local function removeOverlay()
  if s.group then
    if s.group.parent then pcall(function() s.group:removeSelf() end) end
    s.group, s.bg, s.texts, s.key = nil, nil, {}, nil
  end
end

-- keep it above whatever the battle draws, the same trick coromon_starter.py uses:
-- only touch the display list when something was actually placed after us
local function raise()
  if not s.group then return end
  local stage = display.getCurrentStage()
  local n = stage.numChildren or 0
  local needs = (s.group.parent ~= stage)
  if not needs and n > 0 then needs = (stage[n] ~= s.group) end
  if not needs then return end
  pcall(function()
    if s.group.parent then s.group:removeSelf() end
    stage:insert(s.group)
  end)
end

-- one block per opponent: a name/level line, then the potential line under it, with a
-- small gap before the next opponent so the blocks stay visually separate
local function blockFor(f)
  return {
    { text = string.format('%s%s', f.name, f.level and ('   Lv' .. tostring(f.level)) or ''),
      col = WHITE },
    { text = string.format('Potential %d  (%s)', f.pot, f.catName),
      col = COL[f.cat] or WHITE },
  }
end

local function render(fs, key)
  local stage = display.getCurrentStage()
  if not s.group then
    local g = display.newGroup()
    pcall(function() g.name = 'battlePotentialOverlay' end)
    local bg = display.newRect(0, 0, 10, 10)
    bg.anchorX, bg.anchorY = 0, 0
    pcall(function() bg:setFillColor(0, 0, 0, 0.6) end)
    g:insert(bg)
    g.x, g.y = 6, 6
    s.group, s.bg, s.texts = g, bg, {}
  end

  -- flatten every opponent's block into one list of lines, tracking each line's y
  local lines, cols, ys = {}, {}, {}
  local y = 5
  for i = 1, #fs do
    if i > 1 then y = y + 4 end
    local block = blockFor(fs[i])
    for j = 1, #block do
      lines[#lines + 1] = block[j].text
      cols[#cols + 1] = block[j].col
      ys[#ys + 1] = y
      y = y + LINE_H
    end
  end

  for i = 1, #lines do
    if not s.texts[i] then
      local t = textHelper:new(s.group, FONT, { text = lines[i] })
      t.x = 8
      s.texts[i] = t
    else
      pcall(function() s.texts[i].text = lines[i] end)
    end
    pcall(function() s.texts[i].y = ys[i] end)
    local c = cols[i]
    if c then pcall(function() s.texts[i]:setFillColor(c[1], c[2], c[3]) end) end
  end
  for i = #lines + 1, #s.texts do
    pcall(function() s.texts[i]:removeSelf() end)
    s.texts[i] = nil
  end

  local w = 0
  for i = 1, #lines do
    local tw = s.texts[i].width or 0
    if tw > w then w = tw end
  end
  s.bg.width = w + 16
  s.bg.height = ys[#ys] + LINE_H + 5
  s.key = key
  raise()
end

local function tick()
  local st = _G.__battlepot
  if not st or not st.on or st.tickBody ~= tick then return end
  local fs, key = facts()
  if #fs == 0 then
    -- no battle: take it down rather than leaving a stale readout on screen
    if s.group or s.key then removeOverlay() end
    return
  end
  if key ~= s.key then
    render(fs, key)
  else
    raise()
  end
end

removeOverlay()
s.tickBody = tick
if s.timer then pcall(function() timer.cancel(s.timer) end) end
s.timer = timer.performWithDelay(250, tick, 0)

local fs = facts()
if #fs == 0 then
  return 'watching - no opponent on the field right now, the readout appears in battle'
end
local parts = {}
for i = 1, #fs do
  parts[#parts + 1] = string.format('%s Potential %d (%s)', fs[i].name, fs[i].pot, fs[i].catName)
end
return string.format('showing %d opponent(s): %s', #fs, table.concat(parts, '  |  '))
"""
)

OFF = r"""
local s = _G.__battlepot
if not s then return 'nothing to remove' end
s.on = false
if s.timer then pcall(function() timer.cancel(s.timer) end) s.timer = nil end
if s.group then
  if s.group.parent then pcall(function() s.group:removeSelf() end) end
  s.group = nil
end
_G.__battlepot = nil
return 'battle potential overlay removed'
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
        "--once",
        action="store_true",
        help="print every on-field opponent's potential and exit",
    )
    ap.add_argument("--off", action="store_true", help="remove the overlay")
    args = ap.parse_args()

    b = _bridge(args.process)
    if args.off:
        print(_eval(b, OFF))
    elif args.once:
        print(_eval(b, ONCE))
    else:
        print(_eval(b, SHOW))
    return 0


if __name__ == "__main__":
    sys.exit(main())
