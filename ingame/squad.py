#!/usr/bin/env python3
"""
squad.py - the Potential of every Coromon in your squad, drawn under its level.

The squad screen shows each Coromon with its level and nothing else, so deciding which one
is worth a Potential-boosting item means opening them one at a time. This puts the number
where you are already looking.

Where the numbers come from - measured on the live screen, not assumed:

    the row's visible "L<level>" label      its .parent is the row
      .parent                               the row container
        .monster                            the Monster itself: .potential (1-21)
      label:localToContent(0, 0)            where to draw it, in screen coordinates

Every row carries TWO level labels and only one of them is real. On a live six-Coromon squad
each container held a visible "L64" and a hidden "L26", while that monster's :getLevel() said
64 - the second is a stale preview left in the container. So a label is only accepted when it
is VISIBLE (counting hidden ancestors) AND its number equals the monster's own level, which
makes the choice exact rather than a guess about ordering. Each row is then drawn under once,
deduped by its container.

The tier is the game's own (`monsterUtility:getPotentialCategoryForPotential`, the same call
the battle readout uses) and is carried by the colour rather than written out:

    A white - standard (1-16)      B green - potent (17-20)      C gold - perfect (21)

HOW IT DRAWS, and why not by scanning: the rows are caught as they are built, by wrapping
`classes.interface.TallMonsterButton.new`, and each row's label is added as a CHILD of the
row itself, positioned in the row's own coordinates. So the label travels with the row and
dies with it, and the per-tick work is six rows rather than the display list.

Scanning was tried first and rejected on measurement: the squad screen's tree is ~3700 nodes
(1954 even with the overworld map subtree skipped, since the map is still loaded behind the
pause menu), which costs 1.6 ms per walk - a quarter of one 165 fps frame, four times a
second, i.e. a visible hitch. `squadRows()` remains, but only for the report and the one-off
attach when the feature is installed.

The hook feeds whichever install is live (it looks the feature up on `_G.__hud` each time),
so reinstalling over a running game keeps working instead of leaving a dead closure behind.

The FONT is measured too, not guessed. The level label renders "L64" 21 px wide, and of the
game's two fonts only outline_10_bold reproduces that exactly (outline_8 gives 15), so the
same font is used here: the number reads as part of the row rather than as something bolted
on. `offset_y` is the gap below the level number.

Nothing here is specific to a squad *size*: the rows are whatever is on screen. Whether the
box screen uses the same row class has NOT been checked, so nothing is claimed about it.
"""

NAME = "squad"

# The game ships two fonts: outline_8 and outline_10_bold. The level numbers use the latter
# (measured: 21 px for "L64", against 15 px from outline_8), which is why it is the default.
FONTS = {
    "level": "outline_10_bold",   # what the level numbers themselves are drawn in
    "small": "outline_8",
}

DEFAULT_FONT = "level"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "draw each squad Coromon's Potential under its level"),
    (
        "font",
        DEFAULT_FONT,
        'label font: "level" matches the level numbers, "small" is outline_8',
        sorted(FONTS),
    ),
    ("offset_y", 0, "extra pixels between the level number and the Potential under it"),
    (
        "align",
        "left",
        'line up with the level number: "left" starts under its L, "centre" balances on it',
        ["left", "centre"],
    ),
    (
        "tier_colours",
        True,
        "colour the Potential by tier: white standard, green potent, gold perfect",
    ),
]


def lua(cfg):
    """The queries. Always part of the chunk - the install summary, the status line and the
    report all read them, whether or not the overlay itself is installed."""
    return r"""
-- Every squad row on screen, in display order, each with its monster and the level label to
-- draw under. Empty when the squad screen is not up.
--
-- A label is only used when it is VISIBLE (hidden ancestors count as hidden) and its number
-- matches monster:getLevel(), because each row also carries a stale, hidden preview label.
--
-- This walks the whole display tree, so it is NOT used to draw: measured at 1.6 ms a call on
-- the squad screen, which is a quarter of a 165 fps frame. The drawing path uses the class
-- hook instead; this is for the report and for the one-off attach at install.
local function squadRows()
  local stage = display.getCurrentStage()
  if type(stage) ~= 'table' then return {} end
  -- The overworld's tile map sits in the same display list and carries thousands of nodes
  -- that can never be a squad row. Measured: walking everything cost 1.66 ms for 3684 nodes,
  -- which at 165 fps is a quarter of a frame four times a second - a visible hitch. Skipping
  -- the map node takes that to a fraction, so this stays cheap enough to run often.
  local map = twNode()
  local seen, order = {}, {}
  local function scan(node, hidden)
    for i = 1, (node.numChildren or 0) do
      local c = node[i]
      if type(c) == 'table' then
        local vis = (c.isVisible ~= false) and not hidden
        local t = c.text
        if vis and type(t) == 'string' then
          local n = t:match('^L(%d+)$')
          if n then
            local p = c.parent
            if type(p) == 'table' and type(p.monster) == 'table' and not seen[p] then
              local ok, lvl = pcall(function() return p.monster:getLevel() end)
              if ok and tonumber(n) == lvl then
                local pot = tonumber(p.monster.potential)
                if pot then
                  seen[p] = true
                  order[#order + 1] = { label = c, level = lvl, pot = pot, mon = p.monster }
                end
              end
            end
          end
        end
        if c ~= map then scan(c, not vis) end
      end
    end
  end
  pcall(scan, stage, false)
  return order
end

-- One row as a fact: the localised species name, its level, its Potential and the game's
-- category letter. Same shape as the battle readout's facts, and the same localisation path.
local function squadFactFor(row)
  local cat = 'A'
  local ok, c = pcall(function()
    return monsterUtility:getPotentialCategoryForPotential(row.pot)
  end)
  if ok and type(c) == 'string' then cat = c end
  local uid = tostring(row.mon.UID)
  return {
    uid = uid,
    name = loc('monsters.' .. uid .. '.name', uid),
    level = row.level, pot = row.pot, cat = cat,
  }
end
"""


def section(cfg):
    if not cfg["enabled"]:
        return ""
    font = FONTS.get(cfg["font"], FONTS[DEFAULT_FONT])
    return (
        r"""
do
  local FONT = __FONT__
  local OFFSET = __OFFSET__
  local ALIGN = __ALIGN__
  local TIER = __TIER__
  local ROW_CLASS = 'classes.interface.TallMonsterButton'
  -- The game's own categories; the same colours the battle readout uses.
  local COL = {
    A = { 1, 1, 1 },              -- standard, potential 1-16
    B = { 0.45, 1, 0.45 },        -- potent,   potential 17-20
    C = { 1, 0.85, 0.2 },         -- perfect,  potential 21
  }

  local f = makeFeature('squad', 250)
  f.rows = {}      -- row container -> { text = the label drawn in it }
  f.count = 0

  -- The level label inside one row, in the row's OWN coordinates. Only that row's subtree is
  -- walked - a handful of nodes - never the display list.
  local function rowLabel(row)
    local found
    local function scan(node, hidden)
      if found then return end
      for i = 1, (node.numChildren or 0) do
        local c = node[i]
        if type(c) == 'table' then
          local vis = (c.isVisible ~= false) and not hidden
          local t = c.text
          if vis and type(t) == 'string' then
            if t:match('^L%d+$') then
              local ok, lvl = pcall(function() return row.monster:getLevel() end)
              if ok and tonumber(t:match('^L(%d+)$')) == lvl then
                found = c
                return
              end
            end
          end
          scan(c, not vis)
        end
      end
    end
    pcall(scan, row, false)
    return found
  end

  local function attach(row)
    if type(row) ~= 'table' or type(row.monster) ~= 'table' then return end
    local pot = tonumber(row.monster.potential)
    if not pot then return end
    local label = rowLabel(row)
    if not label then return end        -- not built yet; the next tick finds it
    local rec = f.rows[row]
    if type(rec) ~= 'table' then
      rec = { text = text(row, FONT, 'P' .. pot, 0, 0) }
      pcall(function() rec.text.anchorX, rec.text.anchorY = 0.5, 0 end)
      f.rows[row] = rec
    end
    pcall(function() rec.text.text = 'P' .. pot end)
    -- Horizontal. The level numbers are centred in their own box, and my string is narrower
    -- than theirs - "P21" is 19 px against "L64"'s 21, and "P8" only 15 because of the narrow
    -- 1s and the shorter string. So centring the two boxes leaves the left edge further right
    -- on the narrower ones: measured across a live squad, the CENTRES already agreed to 0.00 px
    -- in all six columns while the left edges sat +1.00, +0.50, +1.00, +1.00, +2.50, +2.50.
    -- Left-aligning flushes every one of them under the level's first glyph instead.
    local lw = tonumber(label.width) or tonumber(label.contentWidth) or 0
    local mine = tonumber(rec.text.width) or 0
    if ALIGN == 'left' then
      rec.text.x = (tonumber(label.x) or 0) + mine / 2
    else
      rec.text.x = (tonumber(label.x) or 0) + lw / 2
    end
    rec.text.y = (tonumber(label.y) or 0) + (tonumber(label.contentHeight) or 0) + OFFSET
    local cat = 'A'
    local ok, c = pcall(function()
      return monsterUtility:getPotentialCategoryForPotential(pot)
    end)
    if ok and type(c) == 'string' then cat = c end
    local col = COL[cat] or COL.A
    if not TIER then col = COL.A end
    pcall(function() rec.text:setFillColor(col[1], col[2], col[3]) end)
  end

  -- Rows are caught by the class hook rather than looked for. Wrapping once and looking the
  -- live feature up each time means a reinstall keeps working: the wrapper is never stale.
  local function hookRows()
    local cls = package.loaded[ROW_CLASS]
    if type(cls) ~= 'table' or type(cls.new) ~= 'function' then return end
    if cls.__hudSquadHooked then return end
    cls.__hudSquadHooked = true
    local origNew = cls.new
    cls.new = function(...)
      local inst = origNew(...)
      local live = _G.__hud and _G.__hud.feats and _G.__hud.feats.squad
      if type(inst) == 'table' and type(live) == 'table' and type(live.rows) == 'table' then
        live.rows[inst] = true
      end
      return inst
    end
  end

  -- The rows already on screen when this installs, so it works without needing a screen
  -- change first. One scan, once.
  local function seed()
    for _, row in ipairs(squadRows()) do
      local container = row.label and row.label.parent
      if type(container) == 'table' and f.rows[container] == nil then
        f.rows[container] = true
      end
    end
  end

  local function refresh()
    if not f.on then return end
    if next(f.rows) == nil then hookRows() end      -- the class may not be loaded yet
    local n = 0
    for row in pairs(f.rows) do
      if type(row) ~= 'table' or row.parent == nil then
        f.rows[row] = nil      -- the screen went: the label was a child of the row, so it went too
      else
        attach(row)
        n = n + 1
      end
    end
    f.count = n
  end

  local function kill()
    for _, rec in pairs(f.rows) do
      if type(rec) == 'table' and type(rec.text) == 'table' then
        pcall(function() rec.text:removeSelf() end)
      end
    end
    f.rows, f.count = {}, 0
  end

  hookRows()
  seed()
  f.update, f.kill, f.on = refresh, kill, true
  refresh()
end
""".replace("__FONT__", "'" + font + "'")
        .replace("__OFFSET__", str(int(cfg["offset_y"])))
        .replace("__ALIGN__", "'" + ("left" if cfg["align"] == "left" else "centre") + "'")
        .replace("__TIER__", "true" if cfg["tier_colours"] else "false")
    )


def summary(cfg):
    return r"""(function()
  local n = #squadRows()
  if n == 0 then return 'squad Potential readout (draws when the squad screen is open)' end
  return string.format('squad Potential readout (%d Coromon on screen)', n)
end)()"""


def status(cfg):
    font = FONTS.get(cfg["font"], FONTS[DEFAULT_FONT])
    return r"""(function()
  local f = _G.__hud and _G.__hud.feats.squad
  if not f or not f.on then return nil end
  if (f.count or 0) == 0 then return 'squad Potential: squad screen not open' end
  return string.format('squad Potential: %d drawn, %s%s', f.count, '__FONT__',
    __TIER__ and ', tier colours' or '')
end)()""".replace("__FONT__", font).replace("__TIER__", "true" if cfg["tier_colours"] else "false")


def report(cfg):
    return r"""(function()
  local rows = squadRows()
  if #rows == 0 then return 'squad - the squad screen is not open' end
  local out = { string.format('squad - %d Coromon', #rows) }
  for i = 1, #rows do
    local fact = squadFactFor(rows[i])
    out[#out + 1] = string.format('  %-16s  L%-4s  P%-2s  [%s]', fact.name,
      tostring(fact.level), tostring(fact.pot), fact.cat)
  end
  return table.concat(out, '\n')
end)()"""
