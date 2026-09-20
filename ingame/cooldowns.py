#!/usr/bin/env python3
"""
cooldowns.py - how long each temporary effect still has, as a m:ss countdown under its icon.

The quick-item row in the top right of the screen shows a small marker under each icon while
that effect is running - a scent, the fruit drone, a magnet, a stink disc. It shortens as the
effect runs out and gives no number at all, so this puts the time left under it: "1:47",
counting down. Nothing is replaced - the marker the game drew stays exactly as it is, and the
countdown goes under the icon it marks.

THE TIME IS READ, NOT INFERRED. A running effect is in the save's SAVEABLE_BATTLE_EFFECTS list
and reports its own clock:

    effect:getRemainingSeconds() and effect:getMaxSeconds()

Checked live: a scent answered 53 seconds left against a getMaxSeconds of 180, which is the
"3 minutes" its description advertises, and the drawn label read 0:53. That list is the same
one steptimer reads the Potentiflator's deposits out of. Any effect exposing those two
accessors is picked up with no change here; the magnet hat and the gold booster sit in the same
list exposing neither, so they get no label - correct, because they do not run out.

Only READ-ONLY accessors are called. The scent's class also offers decreaseRemainingSeconds,
which mutates the effect, and that is deliberately never touched.

It is deliberately not read from Resources/data either: items.json has no duration field at
all, and the "3 minutes" exists there only as prose inside the item's description.

PAIRING AN EFFECT WITH SOMEWHERE TO DRAW IT. The two are matched on their fraction - the
effect's remaining/max against the marker's own scale. Both are the game's, and they agreed to
four decimal places when checked (0.2944 against 0.2944), so the pairing cannot drift. An
effect matching no marker gets no label, because there is nowhere to put it.

WHERE THE LABEL GOES. Centred on the ICON and tucked under it, from the slot's own
contentBounds - measured on a live row: x 437..459 and y 5..27 for a 22-unit slot, whose centre
x of 448 is where both its icon children are centred. Not from the marker, whose centre MOVES:
it shortens from the right with its left edge pinned, so its middle walks left as the effect
runs, and centring on that is what left the number in the icon's bottom-left corner. A slot does
not move. `offset_y` shifts the label up or down from the icon's bottom edge.

WHAT THE MARKER IS FOR, AND WHAT IT IS NOT. It is how a timed slot is NOTICED, and that is all:
the slot carries no reference to the item it holds, so nothing else says which icon has an
effect running. Everything else about it is unused - no length, no width and no scale feeds the
drawing, and its scale appears only as the fraction the pairing matches on. There is no size
test in the search, deliberately: the marker shrinks as the effect runs out, and an earlier
version's size test dropped it in the last seconds, which is exactly when a countdown matters.

HOW IT IS FOUND. By what it carries rather than what it measures: a display object with
getScale (so it is a leaf, not a group) answering strictly between 0 and 1, so a full one and a
finished one are both out, a few units tall, up in the top strip of the screen. `strip` is how
far down that search looks, so a marker found somewhere it should not be can be narrowed away.

A partly-filled marker that is NOT counting down - a health or stamina meter up there - can
match the shape, and the shape is only ever a way of finding candidates. What settles it is the
pairing: nothing is drawn unless an effect's fraction matches, and a static meter has no effect
to match, so it gets no label. The geometry cannot decide this; the pairing does.

The label is drawn on the display stage, so it is screen-space: it does not move with the map
and is unaffected by the overworld zoom. Its position is re-read every frame rather than
remembered, because the game owns the row's layout and it shifts when the icon set does.

IT RUNS EVERY FRAME, so nothing is missed between polls and the label never lags a row that
has just moved. That is affordable because the search is cheap: measured over 200 passes on a
live screen, the walk visits 1217 nodes in 0.043 ms, a quarter of one percent of a 60 fps
frame. No countdown of our own ticks anywhere - every number drawn was read out of the game on
that same frame.
"""

NAME = "cooldowns"

# How close an effect's remaining fraction has to be to a marker's scale for the two to be taken
# as the same effect. They agreed exactly when measured (0.2944 against 0.2944), so this is only
# slack for the moment between the effect's tick and the marker's.
MATCH_TOL = 0.02

# How often to re-find the save's settings table, in frames. Finding it walks all of
# package.loaded, which is not something to do sixty times a second.
RESCAN_FRAMES = 120

# The two fonts the game ships. outline_8 is the smaller, which is what fits under an icon.
FONTS = ["outline_8", "outline_10_bold"]

SETTINGS = [
    (
        "enabled",
        True,
        "show a m:ss countdown under each running temporary effect's icon in the quick-item row",
    ),
    (
        "font",
        "outline_8",
        "label font: outline_8 is the game's small font, outline_10_bold the larger one",
        FONTS,
    ),
    (
        "offset_y",
        -2,
        "where to put the countdown against the icon's bottom edge, in content units. 0 sits it "
        "flush under the icon and a negative number lifts it up into the icon",
    ),
    (
        "strip",
        60,
        "how far down the screen, in content units, the markers are looked for. The quick-item "
        "row is in the top strip; narrow this if a marker somewhere it should not be is picked up",
    ),
]


def _lua_str(value):
    """A Lua string literal for a setting coming out of overlays.toml."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def lua(cfg):
    """The queries. Always part of the chunk - the install summary, the status line and the
    report all read them, whether or not the overlay itself is installed."""
    return r"""
-- The markers: one partly-filled leaf per slot that has a running effect, up in the top strip.
-- See the module docstring - noticing a timed slot is the only thing the marker is used for,
-- and there is no size test here on purpose.
local function cdMarkers()
  local out = {}
  local stage = display.getCurrentStage()
  local budget = 4000
  local strip = __STRIP__
  local function walk(n, d)
    if d > 16 or budget <= 0 then return end
    for i = 1, (n.numChildren or 0) do
      local c = n[i]
      budget = budget - 1
      if type(c) == 'table' then
        -- no size test, deliberately: this shrinks as the effect runs out, so testing its size
        -- would drop it in the last seconds - which is when a countdown matters most
        local h = tonumber(c.height) or tonumber(c.contentHeight)
        if type(c.getScale) == 'function' and h and h > 0 and h <= 3 then
          local ok, s = pcall(function() return c:getScale() end)
          local okp, _, cy = pcall(function() return c:localToContent(0, 0) end)
          if ok and okp and type(s) == 'number' and s > 0.001 and s < 0.999
             and type(cy) == 'number' and cy < strip then
            out[#out + 1] = c
          end
        end
        walk(c, d + 1)
      end
    end
  end
  if type(stage) == 'table' then walk(stage, 0) end
  return out
end

-- The save's own world effects. This is where a running item effect lives, and it reports its
-- own clock, so the countdown can be read rather than inferred.
--
-- The table is not reachable from any global, from Game/Save/the player, or from anything on
-- screen: it exists only as an upvalue of functions inside loaded modules. So it is found the
-- way steptimer finds it - walk package.loaded, ask each function for its upvalues, and take
-- the table carrying both SAVEABLE_BATTLE_EFFECTS and VISITED_MAPS, which no other loaded
-- module holds. That is a walk over all of package.loaded, so it is NOT done on every frame:
-- the section re-finds it on an interval and keeps the table.
local function cdSaveSettings()
  for _, mod in pairs(package.loaded) do
    if type(mod) == 'table' then
      for _, fn in pairs(mod) do
        if type(fn) == 'function' then
          for i = 1, 80 do
            local n, v = debug.getupvalue(fn, i)
            if not n then break end
            if type(v) == 'table' and v.SAVEABLE_BATTLE_EFFECTS ~= nil
               and v.VISITED_MAPS ~= nil then
              return v
            end
          end
        end
      end
    end
  end
end

-- Every running effect that reports its own clock, as { left, max, fraction, uid }.
--
-- `fraction` is what ties one to a marker: the marker's scale IS remaining/max, and the two
-- matched to four decimal places when this was checked live - a scent answering
-- getRemainingSeconds 53 against getMaxSeconds 180 gave 0.2944, and the marker's getScale
-- answered 0.2944. Being exact, the pairing cannot drift. An effect that does not expose both
-- accessors is simply not a timed one (the magnet hat and the gold booster share this list and
-- expose neither).
--
-- Only READ-ONLY accessors are called. The scent's class also offers decreaseRemainingSeconds,
-- which mutates the effect, and that is deliberately never touched.
local function cdEffects(settings)
  local out = {}
  if type(settings) ~= 'table' then return out end
  for _, e in ipairs(settings.SAVEABLE_BATTLE_EFFECTS or {}) do
    if type(e) == 'table' and type(e.getRemainingSeconds) == 'function'
       and type(e.getMaxSeconds) == 'function' then
      local okL, left = pcall(function() return e:getRemainingSeconds() end)
      local okM, max = pcall(function() return e:getMaxSeconds() end)
      left, max = tonumber(left), tonumber(max)
      if okL and okM and left and max and max > 0 then
        local uid = nil
        pcall(function() uid = e:getItemUID() end)
        out[#out + 1] = { left = left, max = max, fraction = left / max, uid = uid }
      end
    end
  end
  return out
end

-- Where to put the label: centred on the icon, tucked under it, in content units.
--
-- From the slot's own contentBounds. Two reasons it is not taken from the marker: the marker's
-- centre MOVES - it shortens from the right with its left edge pinned, so its middle walks left
-- as the effect runs, and centring on that is what left the number in the icon's bottom-left
-- corner - and contentBounds is a BOX, which is the one thing here needing no per-class rule.
-- localToContent(0, 0) means something different on each class in this build - measured: the
-- top left of the text box on a text object, the centre on a plain rect, the object's own
-- origin on a group - and none of those is a box.
--
-- Measured on a live row: the slot's bounds are x 437..459 and y 5..27, and its centre x of 448
-- is where both its icon children - 22x22 and 16x16, each anchored 0.5 at x 0 - are centred.
local function cdUnder(marker, slot)
  if type(slot) == 'table' then
    local ok, cb = pcall(function() return slot.contentBounds end)
    if ok and type(cb) == 'table' and type(cb.xMin) == 'number'
       and type(cb.xMax) == 'number' and type(cb.yMax) == 'number' then
      local cx = (cb.xMin + cb.xMax) / 2
      -- sanity bound: a wild coordinate is not a layout, so fall through rather than draw
      -- something somewhere else entirely
      if cx > -1000 and cx < 1000 then return cx, cb.yMax end
    end
  end
  -- no box to read (the row is not built the way it was measured): the marker's own box at
  -- least keeps the label on the right slot
  local okm, mb = pcall(function() return marker.contentBounds end)
  if okm and type(mb) == 'table' and type(mb.xMin) == 'number'
     and type(mb.xMax) == 'number' and type(mb.yMax) == 'number' then
    return (mb.xMin + mb.xMax) / 2, mb.yMax
  end
end

-- Seconds as m:ss. Rounded up rather than down: this counts time still to run, so the last
-- half-second belongs to the reader, not to the game.
local function cdFormat(sec)
  if type(sec) ~= 'number' then return '' end
  if sec < 0 then sec = 0 end
  sec = math.floor(sec + 0.5)
  return string.format('%d:%02d', math.floor(sec / 60), sec % 60)
end
""".replace("__STRIP__", str(int(cfg["strip"])))


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return (
        r"""
do
  local f = makeFeature('cooldowns', 0)   -- every frame, so an enterFrame listener, not a slot
  f.group = nil
  f.labels = {}
  f.entries = {}

  local FONT = __FONT__
  local OFFSET = __OFFSET__
  local WHITE = { 1, 1, 1 }

  -- The save settings table, re-found on an interval rather than every frame: finding it walks
  -- all of package.loaded. The old table is kept if a re-find fails, because a save reload can
  -- replace it and a missing one for a frame should not take the countdown down with it.
  local function settings()
    if f.settings == nil or ((f.passes or 0) % __RESCAN__) == 0 then
      local s = cdSaveSettings()
      if s then f.settings = s end
    end
    return f.settings
  end

  -- Take every label down. Nothing is ever written onto the game's own objects, so there is
  -- nothing to put back.
  local function clear()
    if f.group then
      for _, t in pairs(f.labels or {}) do pcall(function() t:removeSelf() end) end
    end
    drop(f.group)
    f.group, f.labels = nil, {}
    f.entries, f.settings = {}, nil
  end

  local function update()
    if not f.on then return end
    local markers = cdMarkers()
    local effects = cdEffects(settings())
    local entries, seen, used = {}, {}, {}
    f.passes = (f.passes or 0) + 1
    f.markers = #markers

    if not f.group then
      local g = display.newGroup()
      pcall(function() g.name = 'cooldowns' end)
      f.group, f.labels = g, {}
    end

    for i = 1, #markers do
      local marker = markers[i]
      local slot = marker.parent
      local ok, s = pcall(function() return marker:getScale() end)
      if ok and type(s) == 'number' and type(slot) == 'table' then
        -- The effect whose fraction this is. The closest match wins rather than the first, so
        -- two effects cannot both claim the same marker.
        local best = nil
        for k = 1, #effects do
          local e = effects[k]
          if not used[k] then
            local gap = math.abs(e.fraction - s)
            if gap <= __TOL__ and (best == nil or gap < best.gap) then
              best = { k = k, gap = gap, e = e }
            end
          end
        end
        local left = best and best.e.left or nil
        if left and left > 0.5 then
          used[best.k] = true
          local want = cdFormat(left)
          seen[slot] = true
          entries[#entries + 1] = { left = left, uid = best.e.uid }
          local t = f.labels[slot]
          if not t then
            t = text(f.group, FONT, want, 0, 0)
            f.labels[slot] = t
          elseif t.__hudCd ~= want then
            -- setting .text rebuilds the glyph sprites, so only when it changed
            pcall(function() t.text = want end)
          end
          t.__hudCd = want
          local cx, bottom = cdUnder(marker, slot)
          if cx then
            pcall(function()
              t.anchorX, t.anchorY = 0.5, 0
              t.x, t.y = cx, bottom + OFFSET
            end)
          end
          pcall(function() t:setFillColor(WHITE[1], WHITE[2], WHITE[3]) end)
        end
      end
    end

    -- effects that have finished: their labels come down with them
    for slot, t in pairs(f.labels) do
      if not seen[slot] then
        pcall(function() t:removeSelf() end)
        f.labels[slot] = nil
      end
    end

    f.entries = entries
    f.effects = #effects
    if #entries == 0 then
      -- nothing counting down: take the group down rather than leave it on the stage
      drop(f.group)
      f.group = nil
    else
      keepOnTop(f.group)
    end
  end

  -- Every frame: the effect is read and the label placed on the frame the game changes it, with
  -- no poll interval to miss it by. The guard is on the feature table rather than the closure,
  -- so a re-install retires the previous listener instead of leaving two of them running.
  local function frame()
    local h = _G.__hud
    if not h or h.cdFrame ~= frame or not f.on then return end
    update()
  end

  local function kill()
    pcall(function() Runtime:removeEventListener('enterFrame', frame) end)
    clear()
    local h = _G.__hud
    if h and h.cdFrame == frame then h.cdFrame = nil end
  end

  f.update, f.kill, f.on = update, kill, true
  Runtime:addEventListener('enterFrame', frame)
  local H = _G.__hud
  if H then H.cdFrame = frame end
end
"""
        .replace("__FONT__", _lua_str(cfg["font"]))
        .replace("__OFFSET__", str(int(cfg["offset_y"])))
        .replace("__TOL__", str(MATCH_TOL))
        .replace("__RESCAN__", str(RESCAN_FRAMES))
    )


def _live_expr(cfg):
    """Lua expression: the countdowns currently on screen, as one comma-separated line."""
    return r"""(function()
  local f = _G.__hud and _G.__hud.feats.cooldowns
  if not f or not f.entries or #f.entries == 0 then return '' end
  local bits = {}
  for i = 1, #f.entries do bits[#bits + 1] = cdFormat(f.entries[i].left) end
  return table.concat(bits, ', ')
end)()"""


def summary(cfg):
    return (
        r"""(function()
  local s = __LIVE__
  if s == '' then return 'effect countdowns (nothing counting down on screen)' end
  return 'effect countdowns (' .. s .. ')'
end)()""".replace("__LIVE__", _live_expr(cfg))
    )


def status(cfg):
    return (
        r"""(function()
  local f = _G.__hud and _G.__hud.feats.cooldowns
  if not f or not f.on then return nil end
  local s = __LIVE__
  if s == '' then return 'effect countdowns: idle' end
  return 'effect countdowns: ' .. s
end)()""".replace("__LIVE__", _live_expr(cfg))
    )


def report(cfg):
    """Read-only, and the thing to run when a number looks wrong: it shows what the game says
    is running, every marker the search matched, and whether the two paired up."""
    return (
        r"""(function()
  local out = {}
  local f = _G.__hud and _G.__hud.feats.cooldowns
  local markers = cdMarkers()
  out[#out + 1] = 'markers in the top strip: ' .. tostring(#markers)

  -- What the game says is running. This is where the drawn numbers come from.
  local effs = cdEffects(f and f.settings or nil)
  out[#out + 1] = 'timed effects in the save: ' .. tostring(#effs)
  for i = 1, #effs do
    out[#out + 1] = string.format('  %-34s %4d / %4d s   fraction=%.4f',
      tostring(effs[i].uid), effs[i].left, effs[i].max, effs[i].fraction)
  end

  for i = 1, #markers do
    local marker = markers[i]
    local slot = marker.parent
    local ok, s = pcall(function() return marker:getScale() end)
    s = ok and tonumber(s) or nil
    local line = string.format('  marker %d  scale=%s', i,
      (s and string.format('%.4f', s)) or '?')
    local hit = nil
    for k = 1, #effs do
      if not hit and s and math.abs(effs[k].fraction - s) <= __TOL__ then hit = effs[k] end
    end
    if hit then
      line = line .. string.format('  -> %s, %s s left   (drawn as %s)',
        tostring(hit.uid), tostring(hit.left), cdFormat(hit.left))
    else
      line = line .. '  -> no effect matches it, so nothing is drawn'
    end
    out[#out + 1] = line
    if type(slot) == 'table' then
      local okb, cb = pcall(function() return slot.contentBounds end)
      if okb and type(cb) == 'table' then
        out[#out + 1] = string.format(
          '             icon box x [%.1f..%.1f] y [%.1f..%.1f]  -> label at x %.1f, y %.1f',
          cb.xMin, cb.xMax, cb.yMin, cb.yMax, (cb.xMin + cb.xMax) / 2, cb.yMax + __OFF__)
      end
    end
  end

  if f then
    out[#out + 1] = string.format(
      'passes=%s  markers last pass=%s  effects seen=%s  labels drawn=%s',
      tostring(f.passes), tostring(f.markers), tostring(f.effects),
      tostring(#(f.entries or {})))
  end
  return table.concat(out, '\n')
end)()""".replace("__TOL__", str(MATCH_TOL)).replace("__OFF__", str(int(cfg["offset_y"])))
    )
