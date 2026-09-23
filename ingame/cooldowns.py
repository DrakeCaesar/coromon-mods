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

WHERE THE LABEL GOES. Centred on the icon and tucked under it. `x` is 0 in the slot's own
space, because the slot's origin is where its icons are centred. `y` is measured from the
bottom edge of the largest child that is NOT the marker - the 22-unit frame the user sees -
and `offset_y` shifts it from there. Measured on a live row: that frame's box is x 437..459 and
y 5..27, so offset_y -2 puts the label's top at y 25.

IT IS A CHILD OF THE SLOT, not a stage overlay, and that is the point. The game hides the whole
row by hiding a container, and a stage-level label survives that for one frame - long enough for
the pause menu to capture the screen, blur it, and bake the countdown into the backdrop.
Measured: with the row hidden, the live label was already gone from the stage and the blurred
image still had it, while the icon was missing from both. A child cannot lose that race - it
stops being drawn the instant its ancestor does.

The slot is the node used because it is the one that takes children. The icon itself is a plain
image with no `insert`, and asking it to hold a child throws inside the game's own text helper
(groupHelper.lua:11 - measured, not assumed).

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

IT RUNS EVERY FRAME while anything is counting down, so nothing is missed between polls and the
label never lags a row that has just moved - and while NOTHING is counting down the search is
skipped altogether, because a label can only be drawn where a marker and a matching effect meet,
and with no effect there is neither. That gate is in update(). The search itself was measured at
1217 nodes in 0.043 ms on a live screen, but cdMarkers' budget is 4000 nodes and the pause menu's
tree is that big - so the pass worth not paying for is the menu, sixty times a second, finding
nothing. No countdown of our own ticks anywhere - every number drawn was read out of the game on
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

# How often, in frames, a pass at which NOTHING is counting down is allowed to ask whether one has
# started. While an effect is running this is not used at all - the clock is read every frame, as
# it always was - so this only prices the idle case, and it is what lets the marker search be
# skipped outright while there is nothing it could pair. At 60 fps this is ~10 asks a second, and
# each ask is one pass over the save's effect list (a handful of entries, a couple of pcalls each) -
# the walk itself runs on the pass that finds something, and not before.
IDLE_PROBE_FRAMES = 6

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
          -- THE SCALE TEST COMES FIRST, ON ITS OWN, and that ordering is the whole optimisation.
          -- It is the discriminating one: a marker's scale IS remaining/max, so it is a fraction,
          -- while every other node in the tree sits at 1. `localToContent` walks a matrix chain up
          -- the node's whole parent chain - it is far and away the most expensive call in this
          -- walk - so making it run only for nodes that can actually be a marker, instead of for
          -- every small node in the tree, is the difference between two pcalls per small node and
          -- two pcalls per real candidate. Same predicate, short-circuited; the set `out` receives
          -- is identical.
          if ok and type(s) == 'number' and s > 0.001 and s < 0.999 then
            local okp, _, cy = pcall(function() return c:localToContent(0, 0) end)
            if okp and type(cy) == 'number' and cy < strip then
              out[#out + 1] = c
            end
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
-- The SEARCH is no longer written out here: steptimer had already written the same walk, so it
-- now lives once in core's preamble as saveSettingsTable(). What stays here is the caching policy,
-- which is this feature's own - it needs the table within about a second of an effect starting,
-- which is much sooner than steptimer's 12.5 s.
local cdSaveSettings = saveSettingsTable

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

-- What to hang the label from: the slot, if it takes children at all.
--
-- It has to be a CHILD of the row, not a stage overlay, because the game hides the row by
-- hiding a container. A stage-level label survives that for one frame, and one frame is all the
-- pause menu needs: measured, with the row hidden the live label was already gone from the stage
-- while the menu's blurred backdrop still had it - the screen is captured after the row is
-- hidden and before the next pass here. A child cannot lose that race.
--
-- The slot rather than the icon, because the icon is a plain image with no insert - handing it
-- to the game's text helper throws (groupHelper.lua:11, measured). The slot is a group, and as
-- the marker's parent it is inside the row's subtree, so it disappears with the rest of it.
local function cdAttach(slot)
  if type(slot) ~= 'table' or type(slot.insert) ~= 'function' then return nil end
  return slot
end

-- Where in the slot's space: centred, and offset_y from the icon's bottom edge.
--
-- The icon box comes from the largest child that is not the marker, and NOT from the slot
-- itself: our own label now lives inside the slot, and a group's contentBounds includes its
-- children, so reading the slot's box would feed the label's position back into itself and walk
-- it down the screen a little every frame. Labels are skipped for the same reason.
--
-- Measured on a live row: that frame is 22 units wide with its box at x 437..459, y 5..27, and
-- the slot's origin (448, 16) is where both its icons are centred - so x 0 in this space is the
-- icon's centre, and offset_y -2 lands the label's top on y 25.
local function cdPlace(slot, marker, offset)
  local oks, _, sy = pcall(function() return slot:localToContent(0, 0) end)
  if not oks or type(sy) ~= 'number' then return nil end
  local widest, bottom = -1, nil
  for i = 1, (slot.numChildren or 0) do
    local c = slot[i]
    if type(c) == 'table' and c ~= marker and not c.__hudCdLabel then
      local okb, cb = pcall(function() return c.contentBounds end)
      if okb and type(cb) == 'table' and type(cb.xMin) == 'number'
         and type(cb.xMax) == 'number' and type(cb.yMax) == 'number' then
        local w = cb.xMax - cb.xMin
        if w > widest then widest, bottom = w, cb.yMax end
      end
    end
  end
  if bottom == nil then return nil end
  return 0, (bottom + offset) - sy
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
  f.labels = {}                           -- slot -> the label hanging inside that slot
  f.entries = {}

  local FONT = __FONT__
  local OFFSET = __OFFSET__
  local WHITE = { 1, 1, 1 }

  -- The save settings table, re-found on an interval rather than every frame: finding it walks
  -- all of package.loaded, every function in every module, asking each for up to 80 upvalues -
  -- tens of thousands of debug.getupvalue calls per attempt. The old table is kept if a re-find
  -- fails, because a save reload can replace it and a missing one for a frame should not take the
  -- countdown down with it.
  --
  -- THE THROTTLE USED TO BE DEFEATED, and that is the menu stall. The condition was
  --
  --     if f.settings == nil or ((f.passes or 0) % __RESCAN__) == 0 then
  --
  -- and the first term does not cache the failure: while the table has never been found, `f.settings`
  -- is nil on every frame, so the whole package.loaded walk ran EVERY frame instead of every
  -- __RESCAN__ frames. That is the state on the title screen - the table carries
  -- SAVEABLE_BATTLE_EFFECTS and VISITED_MAPS and does not exist until a save is loaded - which is
  -- exactly the reported shape: at the main menu the frame cost is tens of milliseconds (165 fps
  -- down to ~20, CPU up), and it goes away the moment a game is loaded and the table appears and
  -- gets cached.
  --
  -- AND THE TIMER IS GONE ENTIRELY, replaced by the world generation. A save load builds a new
  -- world, so `worldGen()` changing IS the save-loaded event, and reacting to it is both cheaper and
  -- more correct than a rescan on a frame count: that timer kept firing for as long as the feature
  -- was installed - including every 2 s while an effect was running, which is the one state where a
  -- millisecond-scale search under a live countdown is most likely to be felt. Now, once the table
  -- has been found for this world, nothing searches again until the world changes.
  --
  -- The retry belt stays, and it is the mistake this file already made twice: while the table has
  -- never been found, a FAILED search has to be remembered and delayed like a successful one, or it
  -- runs every frame. __RESCAN__ is that delay, and it now applies only in that state.
  local function settings()
    local gen, live = worldGen()
    if f.settings ~= nil and gen == f.gen then return f.settings end
    if not live then
      f.settings, f.gen, f.lastScan = nil, nil, nil
      return nil
    end
    local passes = f.passes or 0
    if f.settings == nil and f.lastScan ~= nil and (passes - f.lastScan) < __RESCAN__ then
      return nil
    end
    f.gen, f.lastScan = gen, passes
    local s = cdSaveSettings()
    if s then f.settings = s end
    return f.settings
  end

  -- Take every label down. They live inside the game's own slot nodes rather than in a group of
  -- ours, so each is removed where it is; nothing else was ever written onto those nodes.
  local function clear()
    for _, t in pairs(f.labels or {}) do pcall(function() t:removeSelf() end) end
    f.labels = {}
    -- lastScan goes too, so a re-install after kill() looks for the settings table on its next
    -- frame rather than waiting out the interval it inherited.
    f.entries, f.settings, f.lastScan = {}, nil, nil
  end

  local function update()
    if not f.on then return end
    f.passes = (f.passes or 0) + 1

    -- WHETHER TO WALK AT ALL IS DECIDED BEFORE THE WALK, AND IT REMEMBERS NOTHING ABOUT MARKERS.
    --
    -- A label is only ever drawn where a marker is AND an effect's fraction matches its scale, so
    -- with no timed effect in the save there is nothing a walk could pair and nothing it could
    -- draw: the search is skipped outright. That is where the cost was - it ran on the title
    -- screen, over an idle overworld and inside every menu sixty times a second to find nothing,
    -- and the pause menu (~4000 nodes) is what paid the most for it.
    --
    -- `f.timed` is only the COUNT of running effects from the last read. Which marker belongs to
    -- which effect is still rediscovered from scratch on every pass the walk runs, so this
    -- survives a save reload, a map change and a rebuilt quick-item row with no invalidation
    -- anywhere. Caching the association instead is the alternative, and it is the bug this project
    -- keeps meeting: a handle that outlives what it points at.
    --
    -- While an effect IS running, the old path runs every frame and the settings table is still
    -- read on every one of those frames, so its rescan interval is exactly what it was. While
    -- nothing is running the question is asked every IDLE_PROBE_FRAMES instead, which is the one
    -- behaviour difference: the rescan interval is then longer in wall-clock terms. Nothing is on
    -- screen to go stale in that state, and the next effect to start is picked up within one probe.
    local markers, effects = {}, {}
    if f.timed == nil or f.timed > 0 then
      markers = cdMarkers()
      f.walks = (f.walks or 0) + 1
      effects = (#markers > 0) and cdEffects(settings()) or {}
    else
      f.idlePasses = (f.idlePasses or 0) + 1
      if f.idlePasses >= __IDLE__ then
        f.idlePasses = 0
        f.probes = (f.probes or 0) + 1
        effects = cdEffects(settings())
        if #effects > 0 then
          markers = cdMarkers()
          f.walks = (f.walks or 0) + 1
        end
      end
    end
    f.markers, f.effects, f.timed = #markers, #effects, #effects

    local entries, seen, used = {}, {}, {}

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
        local parent = cdAttach(slot)
        if left and left > 0.5 and parent then
          used[best.k] = true
          local want = cdFormat(left)
          local t = f.labels[slot]
          if t and t.parent ~= parent then
            -- the row was rebuilt under us and the old label went with the old node
            pcall(function() t:removeSelf() end)
            t = nil
          end
          if not t then
            t = text(parent, FONT, want, 0, 0)
            t.__hudCdLabel = true       -- so the icon search never mistakes it for the icon
            f.labels[slot] = t
          end
          seen[slot] = true
          entries[#entries + 1] = { left = left, uid = best.e.uid }
          if t.__hudCd ~= want then
            -- setting .text rebuilds the glyph sprites, so only when it changed
            pcall(function() t.text = want end)
          end
          t.__hudCd = want
          local px, py = cdPlace(slot, marker, OFFSET)
          if px then
            pcall(function()
              t.anchorX, t.anchorY = 0.5, 0
              t.x, t.y = px, py
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
        .replace("__IDLE__", str(IDLE_PROBE_FRAMES))
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
      local px, py = cdPlace(slot, marker, __OFF__)
      out[#out + 1] = string.format(
        '             inside the slot=%s, label at slot-local (%s, %s), slot children now %s',
        tostring(cdAttach(slot) ~= nil),
        (px and string.format('%.1f', px)) or '?', (py and string.format('%.1f', py)) or '?',
        tostring(slot.numChildren))
    end
  end

  if f then
    out[#out + 1] = string.format(
      'passes=%s  markers last pass=%s  effects seen=%s  labels drawn=%s',
      tostring(f.passes), tostring(f.markers), tostring(f.effects),
      tostring(#(f.entries or {})))
    -- THE NUMBER TO WATCH. `walks` is how many times the top-strip search actually ran, and it used
    -- to be one per pass: with nothing counting down it must now stay flat - sitting on the title
    -- screen or walking around an idle overworld - and step up only while an effect is running.
    out[#out + 1] = string.format(
      'searches: walks=%s of %s passes, idle probes=%s  (walks was one per pass before)',
      tostring(f.walks or 0), tostring(f.passes), tostring(f.probes or 0))
  end
  return table.concat(out, '\n')
end)()""".replace("__TOL__", str(MATCH_TOL)).replace("__OFF__", str(int(cfg["offset_y"])))
    )
