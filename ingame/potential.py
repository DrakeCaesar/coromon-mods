#!/usr/bin/env python3
"""
potential.py - the battle Potential readout.

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
          .traitUID                     -- its trait, as a string ('DIMENSIONAL_EYE')
          :getLevel()                   -- its level

A double battle fields more than one 'front' sprite at once, so every one of them is
collected - ordered left-to-right by sprite x - and each gets its own line.

Nothing is hardcoded: the display name comes from the game's own localisation
(`localise('monsters.<UID>.name')` -> "Buzzlet") and so does the trait
(`localise('traits.<UID>.name')` -> "Dimensional Eye"), the same pattern as items. The tier
comes from `monsterUtility:getPotentialCategoryForPotential()` -> 'A'/'B'/'C', which the
game then localises through `global.monsterPotentialCategory.<cat>`:

    A = Standard (1-16)      B = Potent (17-20)      C = Perfect (21)

Each opponent gets ONE line, the way it reads on screen:

    Lunarpup L17 P8 (Dimensional Eye) [Smart Gem]

Name, level, potential, trait and held item. The trait is shown rather than the tier's word because the
tier is already carried by the line's colour, and which trait an opponent has matters far
more at the moment you decide whether to spend a spinner. `m.traitUID` is a plain string on
the Monster; `m:getTrait()` returns only its behaviour classes, no name, so the UID is what
has to go through localisation.

The held item comes last, in square brackets, and only when there is one - it changes what
the turn will do as much as the trait does, and like the trait it is invisible until you
already own the Coromon. `m.holdItemUID` is a plain string on the Monster ('' when it holds
nothing, not nil), and it goes through the same `items.<UID>.name` key the map markers use,
so a held item's name reads the same here as it does on the ground. It is part of the change
signature too: an item can be used up mid-battle, and the line has to notice.

The overlay itself is screen-space, on the display stage, so it does not move with the map
and is unaffected by the overworld zoom.
"""

NAME = "potential"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "install the battle Potential readout (screen overlay)"),
]


def lua(cfg):
    """The queries. Always part of the chunk - the install summary, the status report and
    the report all read them, whether or not the overlay itself is installed."""
    return r"""
-- The on-field opponents. getMonsterSpritesInBattle() marks each sprite with a `side`:
-- 'front' is an opponent, 'back' is the player's. That is authoritative for trainer
-- battles too, where every opposing monster lacks a catchDate. A double battle fields more
-- than one at once, so this returns a list, ordered left-to-right on screen.
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

local function potentialFactFor(m)
  local pot = tonumber(m.potential)
  if pot == nil then return nil end
  local uid = tostring(m.UID)
  local ok, lvl = pcall(function() return m:getLevel() end)
  if not ok then lvl = nil end
  local cat = 'A'
  local okc, c = pcall(function() return monsterUtility:getPotentialCategoryForPotential(pot) end)
  if okc and type(c) == 'string' then cat = c end
  -- The trait. A plain string field on the Monster; getTrait() gives only its behaviour
  -- classes, not a name, so the UID is what goes through localisation. loc() falls back to
  -- the raw UID rather than '???' when a trait has no localised name.
  local traitUID = tostring(m.traitUID or '')
  local traitName = nil
  if traitUID ~= '' and traitUID ~= 'nil' then
    traitName = loc('traits.' .. traitUID .. '.name', traitUID)
  end
  -- The held item. Also a plain string UID, and an EMPTY STRING rather than nil when there
  -- is none - so the test is "not empty", not "not absent". Same items.<UID>.name key the
  -- map markers localise with, so the two agree.
  local holdUID = tostring(m.holdItemUID or '')
  local holdName = nil
  if holdUID ~= '' and holdUID ~= 'nil' then
    holdName = loc('items.' .. holdUID .. '.name', holdUID)
  end
  return {
    uid = uid,
    name = loc('monsters.' .. uid .. '.name', uid),
    level = lvl, pot = pot, cat = cat,
    catName = loc('global.monsterPotentialCategory.' .. cat, cat),
    traitUID = traitUID, traitName = traitName,
    holdUID = holdUID, holdName = holdName,
  }
end

-- Every opponent on the field, plus a signature used to spot when it changes.
local function potentialFacts()
  local ms = opponents()
  local out, sig = {}, {}
  for i = 1, #ms do
    local f = potentialFactFor(ms[i])
    if f then
      out[#out + 1] = f
      sig[#sig + 1] = f.uid .. '|' .. tostring(f.pot) .. '|' .. tostring(f.level)
        .. '|' .. tostring(f.traitUID) .. '|' .. tostring(f.holdUID)
    end
  end
  return out, table.concat(sig, ';')
end
"""


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return r"""
do
  local FONT = 'outline_10_bold'
  local LINE_H = 14
  local WHITE = { 1, 1, 1 }
  -- The game's own categories; B and C are the ones worth spending a spinner on.
  local COL = {
    A = WHITE,                -- standard, potential 1-16
    B = { 0.45, 1, 0.45 },    -- potent,   potential 17-20
    C = { 1, 0.85, 0.2 },     -- perfect,  potential 21
  }

  local f = makeFeature('potential', 250)
  f.texts = {}

  local function clear()
    drop(f.group)
    f.group, f.bg, f.texts, f.key = nil, nil, {}, nil
  end

  -- one compact line per opponent - "Lunarpup L17 P8 (Dimensional Eye)". The trait goes
  -- in the parentheses rather than the tier's word: the tier is already the line's colour,
  -- and the trait is the thing that changes what you want to do this turn. The block is
  -- still a LIST of lines so a second line needs no change here.
  local function blockFor(fact)
    local s = fact.name
    if fact.level then s = s .. ' L' .. tostring(fact.level) end
    s = s .. ' P' .. tostring(fact.pot)
    if fact.traitName then s = s .. ' (' .. tostring(fact.traitName) .. ')' end
    if fact.holdName then s = s .. ' [' .. tostring(fact.holdName) .. ']' end
    return { { text = s, col = COL[fact.cat] or WHITE } }
  end

  local function render(facts, key)
    if not f.group then
      local g = display.newGroup()
      pcall(function() g.name = 'potentialReadout' end)
      f.bg = rect(g, 0, 0, 10, 10, { 0, 0, 0, 0.6 })
      g.x, g.y = 6, 6
      f.group = g
    end

    -- flatten every opponent's block into one list of lines, tracking each line's y
    local lines, cols, ys = {}, {}, {}
    local y = 5
    for i = 1, #facts do
      if i > 1 then y = y + 4 end
      local block = blockFor(facts[i])
      for j = 1, #block do
        lines[#lines + 1] = block[j].text
        cols[#cols + 1] = block[j].col
        ys[#ys + 1] = y
        y = y + LINE_H
      end
    end

    for i = 1, #lines do
      if not f.texts[i] then
        f.texts[i] = text(f.group, FONT, lines[i], 8, ys[i], cols[i])
      else
        pcall(function() f.texts[i].text = lines[i] end)
        pcall(function() f.texts[i].y = ys[i] end)
        local c = cols[i]
        if c then pcall(function() f.texts[i]:setFillColor(c[1], c[2], c[3]) end) end
      end
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
    f.bg.width = wmax + 16
    f.bg.height = ys[#ys] + LINE_H + 5
    f.key = key
    keepOnTop(f.group)
  end

  local function update()
    if not f.on then return end
    local facts, key = potentialFacts()
    if #facts == 0 then
      -- no battle: take it down rather than leaving a stale readout on screen
      if f.group or f.key then clear() end
      return
    end
    if key ~= f.key then render(facts, key) else keepOnTop(f.group) end
  end

  f.update, f.kill, f.on = update, clear, true
end
"""


def summary(cfg):
    return r"""(function()
  local facts = potentialFacts()
  return #facts > 0
    and string.format('Potential readout (%d opponent(s) on the field now)', #facts)
    or 'Potential readout (appears when a battle starts)'
end)()"""


def status(cfg):
    return r"""(function()
  local h = _G.__hud
  local f = h and h.feats.potential
  if not f or not f.on then return nil end
  local facts = potentialFacts()
  return #facts > 0
    and string.format('potential readout: showing %d opponent(s)', #facts)
    or 'potential readout: waiting for a battle'
end)()"""


def report(cfg):
    return r"""(function()
  local facts = potentialFacts()
  local out = {}
  if #facts > 0 then
    out[#out + 1] = 'battle - ' .. #facts .. ' opponent(s) on the field:'
    for i = 1, #facts do
      local f = facts[i]
      out[#out + 1] = string.format('  %s   Lv%s     Potential %d  (%s)' .. '%s%s',
        f.name, tostring(f.level), f.pot, f.catName,
        f.traitName and ('     trait: ' .. tostring(f.traitName)) or '',
        f.holdName and ('     holding: ' .. tostring(f.holdName)) or '')
    end
  else
    out[#out + 1] = 'battle - no opponent on the field'
  end
  return table.concat(out, '\n')
end)()"""
