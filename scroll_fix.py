#!/usr/bin/env python3
"""
scroll_fix.py - make Coromon's overworld scroll a constant number of pixels per frame.

THE PROBLEM
-----------
MTE (classes.modules.mte.mte) scrolls the world by translating its map container:

    function MTE.moveCamera(dx, dy)      -- proto (0,73)
        tiledWorld:translate(dx, dy)     -- no rounding, no tween, one call per frame
    end

    function MTE.moveSprite(object, dx, dy)     -- proto (0,56)
        <move object and its childSprites>
        if cameraFocus == object then
            tiledWorld:translate(<dx, dy>)      -- world follows the camera focus
        end
    end

So the world advances by exactly the game's per-frame movement delta. Measured, that
delta is ~**1.04 content pixels per frame** (387 of 393 frames moved while walking,
per-frame deltas 1.0343-1.0457). Content is 480x270 at an exact x3 integer scale, and
the rendered result lands on whole content pixels, so:

    ~24 frames in every 25 step 1 content pixel  (3 screen px)
    the 25th frame        steps 2 content pixels (6 screen px)

Roughly twice a second the background takes a double step. The frame times are perfect
(18.18 ms mean, 19 ms max) and the position is continuous, which is exactly why the
frametime graph looks smooth while the scroll visibly judders.

No amount of re-quantising the *position* can fix this: a non-integer average speed
cannot be expressed as equal integer steps. The fix has to make the step itself a
constant integer number of content pixels.

THE FIX
-------
Round the per-frame delta to whole content pixels, at the single point where the game
asks for it. The sprite is moved by the same rounded delta as the world, so the camera
stays locked to the player and nothing drifts - motion just becomes a uniform 1 pixel
per frame instead of 1,1,1,...,2.

Trade-off: the scroll becomes 1 px/frame instead of 1.04, i.e. ~4% slower, and because
the step is now per-frame rather than per-second the speed scales with the frame rate.
Both are invisible at the game's locked 55 fps. Movement along both axes at once keeps
its direction (both components are scaled by the same factor).

Everything here is reversible: `--off` restores the original functions, and the change
lives only in the running process - it is gone when the game closes.

USAGE
-----
    python tools/scroll_fix.py --walk-fix

Run that once per game launch, while the game is open and you are standing in
the overworld. It measures your frame time, computes the durations, and applies
them. You do NOT need to re-run it for map changes or save loads - a watchdog
re-applies it whenever the game rebuilds the player object.

    --walk-report   show whether it is active, plus call/re-apply counts
    --off           undo it immediately
    --measure       verify: per-frame world step histogram

Changing refresh rate changes the frame time, so re-run --walk-fix afterwards.

The rest of the flags (--on, --snap, --trace, --trace-spawn, --find-speed,
--set-speed, --walk-scale, and the matching -report flags) are the instruments
used to find the cause and are not needed for normal use.
"""
import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from coromon_lua import Bridge, MINIMAL_HOOKS  # noqa: E402

PRELUDE = r"""
local function upval(fn, name)
  if type(fn) ~= 'function' then return nil end
  for i = 1, 120 do
    local n, v = debug.getupvalue(fn, i)
    if not n then return nil end
    if n == name then return v end
  end
end

local function world()
  local MTE = _G.MTE
  if type(MTE) ~= 'table' then return nil, nil end
  return MTE, upval(MTE.getTiledWorld, 'tiledWorld')
end
"""

STATUS = PRELUDE + r"""
local MTE, tw = world()
if type(MTE) ~= 'table' then return '!MTE not found - are you in the overworld?!' end
local s = _G.__scrollfix
local lines = {
  'MTE                  = ' .. tostring(MTE),
  'tiledWorld           = ' .. tostring(tw),
  'tiledWorld.x         = ' .. tostring(tw and tw.x),
  'tiledWorld.y         = ' .. tostring(tw and tw.y),
  'scroll fix installed = ' .. tostring(s ~= nil),
  'moveSprite           = ' .. tostring(MTE.moveSprite),
  'translateCamera      = ' .. tostring(MTE.translateCamera),
  'moveCamera (private) = ' .. tostring(MTE.moveCamera),
}
if s then
  lines[#lines + 1] = '  wrapped = ' .. table.concat(s.names or {}, ', ')
  for _, n in ipairs(s.names or {}) do
    lines[#lines + 1] = '    original ' .. n .. ' = ' .. tostring(s.orig[n])
  end
  lines[#lines + 1] = '  rounded ' .. tostring(s.calls or 0) .. ' calls, changed ' ..
                      tostring(s.changed or 0) .. ' deltas'
end
return table.concat(lines, '\n')
"""

INSTALL = PRELUDE + r"""
local MTE, tw = world()
if type(MTE) ~= 'table' then return '!MTE not found - are you in the overworld?!' end
if _G.__scrollfix then return 'already installed' end

-- MTE.moveCamera is a module-LOCAL (it is not a field on MTE). The public entry
-- points are moveSprite(object, dx, dy) and translateCamera(dx, dy); wrap whichever
-- exist. moveSprite also carries the world translation when the sprite is the
-- camera focus, and the game drives the player through it.
local s = { orig = {}, names = {}, calls = 0, changed = 0 }
_G.__scrollfix = s

-- snap a per-frame delta to whole content pixels, keeping direction.
-- A non-zero axis never rounds to 0 (that would freeze slow movement).
local function snap(dx, dy)
  local ax, ay = math.abs(dx or 0), math.abs(dy or 0)
  local m = ax > ay and ax or ay
  if m == 0 then return 0, 0 end
  local target = m >= 1 and math.floor(m + 0.5) or 1
  local k = target / m
  return (dx or 0) * k, (dy or 0) * k
end

local function note(dx, dy, nx, ny)
  s.calls = s.calls + 1
  if dx ~= nx or dy ~= ny then s.changed = s.changed + 1 end
end

local function install3(name)
  local orig = MTE[name]
  if type(orig) ~= 'function' then return false end
  s.orig[name] = orig
  s.names[#s.names + 1] = name
  MTE[name] = function(object, dx, dy)
    local nx, ny = snap(dx, dy)
    note(dx, dy, nx, ny)
    return orig(object, nx, ny)
  end
  return true
end

local function install2(name)
  local orig = MTE[name]
  if type(orig) ~= 'function' then return false end
  s.orig[name] = orig
  s.names[#s.names + 1] = name
  MTE[name] = function(dx, dy)
    local nx, ny = snap(dx, dy)
    note(dx, dy, nx, ny)
    return orig(nx, ny)
  end
  return true
end

install3('moveSprite')
install2('translateCamera')
if #s.names == 0 then
  _G.__scrollfix = nil
  return '!neither moveSprite nor translateCamera is a function!'
end
return 'installed, wrapped: ' .. table.concat(s.names, ', ')
"""

REMOVE = PRELUDE + r"""
local MTE, tw = world()
local lines = {}

local s = _G.__scrollfix
if s then
  if type(MTE) == 'table' then
    for _, n in ipairs(s.names or {}) do
      if s.orig[n] then MTE[n] = s.orig[n] end
    end
  end
  _G.__scrollfix = nil
  lines[#lines + 1] = 'restored: ' .. table.concat(s.names or {}, ', ')
else
  lines[#lines + 1] = 'scroll fix was not installed'
end

local t = _G.__mtetrace
if t then
  if type(MTE) == 'table' then
    for k, fn in pairs(t.orig) do MTE[k] = fn end
  end
  _G.__mtetrace = nil
  lines[#lines + 1] = 'restored tracer'
end

local sn = _G.__snap
if sn then
  if type(MTE) == 'table' then
    for k, fn in pairs(sn.orig) do MTE[k] = fn end
  end
  _G.__snap = nil
  lines[#lines + 1] = 'restored snap: ' .. table.concat(sn.names, ', ')
end

local sp = _G.__spawntrace
if sp then
  for k, fn in pairs(sp.orig) do sp.inst[k] = fn end
  _G.__spawntrace = nil
  lines[#lines + 1] = 'restored player spawnable methods'
end

local ws = _G.__walkscale
if ws then
  if ws.timer then timer.cancel(ws.timer) end
  if ws.patched and ws.orig then ws.patched.getGridMoveTimeBySpeed = ws.orig end
  _G.__walkscale = nil
  lines[#lines + 1] = 'restored getGridMoveTimeBySpeed and stopped the watchdog'
end

return table.concat(lines, '\n')
"""

# ---------------------------------------------------------------------------
# Empirical tracer. Static analysis guessed the wrong funnel once already, so
# instead of guessing again, count calls into EVERY public MTE function and see
# which one actually fires while walking.
# ---------------------------------------------------------------------------
TRACE_INSTALL = PRELUDE + r"""
local MTE = _G.MTE
if type(MTE) ~= 'table' then return '!MTE not found - are you in the overworld?!' end
if _G.__mtetrace then return 'already tracing' end

local t = { orig = {}, counts = {} }
_G.__mtetrace = t
local n = 0
for k, v in pairs(MTE) do
  if type(v) == 'function' then
    t.orig[k] = v
    t.counts[k] = 0
    MTE[k] = (function(name, fn)
      return function(...)
        t.counts[name] = t.counts[name] + 1
        return fn(...)
      end
    end)(k, v)
    n = n + 1
  end
end
return 'tracing ' .. n .. ' MTE functions - now walk around'
"""

# ---------------------------------------------------------------------------
# The real fix. The tracer showed moveSprite/translateCamera are never called,
# while editObject fires once per walking frame, and the movement class
# (classes.spawnables.abstractEightDirectionalMovingSpawnable) calls
# MTE.<fn>(sprite, { deltaX = ..., deltaY = ... }).
#
# So the world's motion is inherited from the SPRITE's per-frame motion, and the
# sprite moves a fractional number of pixels per frame. Snapping the sprite's
# per-frame delta to whole content pixels makes the world advance exactly 1 pixel
# every frame, which survives the game's own rounding - a constant integer
# advance rounds to itself.
#
# Argument tables are rounded in place; both absolute (x/y) and delta
# (deltaX/deltaY/dx/dy) forms are handled so it does not matter which one the
# call site uses.
# ---------------------------------------------------------------------------
SNAP_INSTALL = PRELUDE + r"""
local MTE = _G.MTE
if type(MTE) ~= 'table' then return '!MTE not found - are you in the overworld?!' end
if _G.__snap then return 'already installed' end

local st = { orig = {}, names = {}, calls = 0, rounded = 0, touched = {}, seen = {}, distinct = 0 }
_G.__snap = st

-- describe an argument: numbers verbatim, tables by key=value (values as numbers
-- or their type). This is how we learn the real field names instead of guessing.
local function shape(v)
  local t = type(v)
  if t == 'number' then return tostring(v) end
  if t == 'string' or t == 'boolean' then return t .. '(' .. tostring(v) .. ')' end
  if t ~= 'table' then return t end
  local bits, n = {}, 0
  for k, vv in pairs(v) do
    n = n + 1
    if n <= 14 then
      bits[#bits + 1] = tostring(k) .. '=' .. (type(vv) == 'number' and tostring(vv) or type(vv))
    end
  end
  return '{' .. table.concat(bits, ', ') .. (n > 14 and ', ...' or '') .. '}'
end

-- keys/types only, so repeated calls with the same shape collapse together.
-- Without this, NPC pathing floods the log and hides the player's call.
local function sig(v)
  local t = type(v)
  if t ~= 'table' then return t end
  local ks = {}
  for k in pairs(v) do ks[#ks + 1] = tostring(k) end
  table.sort(ks)
  return '{' .. table.concat(ks, ',') .. '}'
end

-- round to whole content pixels; a non-zero never collapses to zero
local function snapVal(v)
  if type(v) ~= 'number' then return v end
  if v == 0 then return 0 end
  local r = math.floor(math.abs(v) + 0.5)
  if r < 1 then r = 1 end
  return v < 0 and -r or r
end

local KEYS = { 'deltaX', 'deltaY', 'dx', 'dy', 'x', 'y' }

local function snapArg(a)
  if type(a) ~= 'table' then return false end
  local changed = false
  for _, k in ipairs(KEYS) do
    local v = a[k]
    if type(v) == 'number' then
      local nv = snapVal(v)
      if nv ~= v then
        st.touched[k] = (st.touched[k] or 0) + 1
        a[k] = nv
        changed = true
      end
    end
  end
  return changed
end

local function wrap(name)
  local orig = MTE[name]
  if type(orig) ~= 'function' then return end
  st.orig[name] = orig
  st.names[#st.names + 1] = name
  MTE[name] = function(a, b, c, d)
    st.calls = st.calls + 1
    local ch = snapArg(b)
    if snapArg(c) then ch = true end
    if snapArg(d) then ch = true end
    if ch then st.rounded = st.rounded + 1 end
    local key = name .. '(' .. sig(a) .. ',' .. sig(b) .. ',' .. sig(c) .. ',' .. sig(d) .. ')'
    local e = st.seen[key]
    if e then
      e.n = e.n + 1
    elseif st.distinct < 24 then
      st.seen[key] = { n = 1, ex = string.format('%s , %s , %s , %s',
        shape(a), shape(b), shape(c), shape(d)) }
      st.distinct = st.distinct + 1
    end
    return orig(a, b, c, d)
  end
end

for _, n in ipairs({ 'editObject', 'setSpriteLocation', 'moveSprite', 'translateCamera' }) do
  wrap(n)
end
if #st.names == 0 then
  _G.__snap = nil
  return '!none of the movement functions exist!'
end
return 'snap installed on: ' .. table.concat(st.names, ', ')
"""

SNAP_REPORT = PRELUDE + r"""
local st = _G.__snap
if not st then return '!snap not installed!' end
local out = {
  'wrapped   : ' .. table.concat(st.names, ', '),
  'calls     : ' .. tostring(st.calls),
  'rounded   : ' .. tostring(st.rounded),
}
local keys = {}
for k, c in pairs(st.touched) do keys[#keys + 1] = string.format('%s=%d', k, c) end
table.sort(keys)
out[#out + 1] = 'keys hit  : ' .. (#keys > 0 and table.concat(keys, '  ') or '(none)')
if st.seen then
  local rows = {}
  for k, e in pairs(st.seen) do rows[#rows + 1] = { k, e } end
  table.sort(rows, function(x, y) return x[2].n > y[2].n end)
  out[#out + 1] = string.format('distinct call shapes = %d', #rows)
  for _, r in ipairs(rows) do
    out[#out + 1] = string.format('  x%-5d %s', r[2].n, r[1])
    out[#out + 1] = '        ex: ' .. r[2].ex
  end
end
return table.concat(out, '\n')
"""

# ---------------------------------------------------------------------------
# Option B: change the RATE instead of the rounding.
#
# classes.spawnables.abstractEightDirectionalMovingSpawnable computes the walk
# step every frame as roughly
#
#     step = movementSpeed * (dt_ms / 1000) * <constant>
#
# and movementSpeed is a plain Lua LOCAL (an upvalue of that function), seeded
# from a 114.0 constant. With dt = 18.18 ms the fit is exact:
#
#     114 * 0.01818 * 0.5 = 1.0363 px/frame   (measured: 1.036)
#
# Because the step is `speed * dt`, the pixels-per-frame the engine produces is
# movementSpeed/2 per SECOND divided by the frame rate - so a step of exactly
# one pixel needs movementSpeed = 2 * fps:
#
#     step = (movementSpeed / 2) / fps  = 1   =>   movementSpeed = 2 * fps
#
#   ~55 fps (165 Hz panel) -> 110        60 fps -> 120
#
# That makes every frame advance exactly 1 content pixel, so the game's own
# math.round() has nothing to alternate between and the scroll is uniform.
# ---------------------------------------------------------------------------
FIND_SPEED = PRELUDE + r"""
local out = {}
local hits, seen = {}, {}

local function check(fn, label)
  if type(fn) ~= 'function' or seen[fn] then return end
  seen[fn] = true
  for i = 1, 60 do
    local n, v = debug.getupvalue(fn, i)
    if not n then return end
    if n == 'movementSpeed' then
      hits[#hits + 1] = { fn = fn, idx = i, old = v, label = label }
    end
  end
end

local function scanTable(t, label, depth)
  if type(t) ~= 'table' or depth > 2 then return end
  for k, v in pairs(t) do
    if type(v) == 'function' then
      check(v, label .. '.' .. tostring(k))
    elseif type(v) == 'table' and depth < 2 then
      scanTable(v, label .. '.' .. tostring(k), depth + 1)
    end
  end
end

for _, name in ipairs({
  'classes.spawnables.abstractEightDirectionalMovingSpawnable',
  'classes.spawnables.worldObjectSpawnable',
  'classes.spawnables.plugins.spriteSpawnablePlugin',
}) do
  local m = package.loaded[name]
  if type(m) == 'table' then scanTable(m, name, 0) end
end

local inst
pcall(function() inst = spawnableHelper:getPlayerSpawnable() end)
if type(inst) == 'table' then
  scanTable(inst, 'playerSpawnable', 0)
  local cls = inst._class
  if type(cls) ~= 'table' then
    local mt = getmetatable(inst)
    cls = mt and mt.__index
  end
  if type(cls) == 'table' then scanTable(cls, 'playerSpawnable._class', 0) end
end

out[#out + 1] = 'playerSpawnable = ' .. tostring(inst)
out[#out + 1] = 'functions carrying a movementSpeed upvalue = ' .. tostring(#hits)
for _, h in ipairs(hits) do
  out[#out + 1] = string.format('  %s   current movementSpeed = %s', h.label, tostring(h.old))
end
return table.concat(out, '\n')
"""

SET_SPEED = PRELUDE + r"""
local target = __SPEED__
local out = {}
local hits, seen = {}, {}

local function check(fn, label)
  if type(fn) ~= 'function' or seen[fn] then return end
  seen[fn] = true
  for i = 1, 60 do
    local n, v = debug.getupvalue(fn, i)
    if not n then return end
    if n == 'movementSpeed' then
      hits[#hits + 1] = { fn = fn, idx = i, old = v, label = label }
    end
  end
end

local function scanTable(t, label, depth)
  if type(t) ~= 'table' or depth > 2 then return end
  for k, v in pairs(t) do
    if type(v) == 'function' then
      check(v, label .. '.' .. tostring(k))
    elseif type(v) == 'table' and depth < 2 then
      scanTable(v, label .. '.' .. tostring(k), depth + 1)
    end
  end
end

for _, name in ipairs({
  'classes.spawnables.abstractEightDirectionalMovingSpawnable',
  'classes.spawnables.worldObjectSpawnable',
  'classes.spawnables.plugins.spriteSpawnablePlugin',
}) do
  local m = package.loaded[name]
  if type(m) == 'table' then scanTable(m, name, 0) end
end

local inst
pcall(function() inst = spawnableHelper:getPlayerSpawnable() end)
if type(inst) == 'table' then
  scanTable(inst, 'playerSpawnable', 0)
  local cls = inst._class
  if type(cls) ~= 'table' then
    local mt = getmetatable(inst)
    cls = mt and mt.__index
  end
  if type(cls) == 'table' then scanTable(cls, 'playerSpawnable._class', 0) end
end

if #hits == 0 then return '!no movementSpeed upvalue found!' end
for _, h in ipairs(hits) do
  local ok = debug.setupvalue(h.fn, h.idx, target)
  out[#out + 1] = string.format('  %s : %s -> %s  (%s)', h.label, tostring(h.old),
    tostring(target), ok and 'ok' or 'FAILED')
end
return string.format('set movementSpeed to %s on %d function(s):\n%s',
  tostring(target), #hits, table.concat(out, '\n'))
"""

# The player spawnable does NOT use abstractEightDirectionalMovingSpawnable's
# dt-based movement (its methods carry no movementSpeed upvalue anywhere we can
# reach, and its own methods are grid-step names: getGridMoveStack,
# untilLocationXY, hasReachedNewTile, isGridMoveBlocked, executeNowOrAfterMove).
# So trace the player spawnable's own methods to find the per-frame mover.
SPAWN_TRACE_INSTALL = PRELUDE + r"""
local inst
pcall(function() inst = spawnableHelper:getPlayerSpawnable() end)
if type(inst) ~= 'table' then return '!no player spawnable found!' end
if _G.__spawntrace then return 'already tracing' end

local t = { inst = inst, orig = {}, counts = {} }
_G.__spawntrace = t
local n = 0
for k, v in pairs(inst) do
  if type(v) == 'function' then
    t.orig[k] = v
    t.counts[k] = 0
    inst[k] = (function(name, fn)
      return function(...)
        t.counts[name] = t.counts[name] + 1
        return fn(...)
      end
    end)(k, v)
    n = n + 1
  end
end
return 'tracing ' .. n .. ' methods on the player spawnable - now walk around'
"""

SPAWN_TRACE_REPORT = PRELUDE + r"""
local t = _G.__spawntrace
if not t then return '!not tracing!' end
local rows = {}
for k, c in pairs(t.counts) do rows[#rows + 1] = { k, c } end
table.sort(rows, function(a, b) return a[2] > b[2] end)
local out = { 'player spawnable method calls:' }
local shown = 0
for i = 1, #rows do
  if rows[i][2] > 0 then
    shown = shown + 1
    if shown <= 30 then
      out[#out + 1] = string.format('  %-40s %d', rows[i][1], rows[i][2])
    end
  end
end
if shown == 0 then out[#out + 1] = '  (nothing called yet)' end
out[#out + 1] = '  ' .. shown .. ' of ' .. #rows .. ' methods were called'
return table.concat(out, '\n')
"""

# ---------------------------------------------------------------------------
# OPTION B, final form. The trace showed the player moves one tile per
# `gridMove`, with the duration supplied by `getGridMoveTimeBySpeed`, and
# `whileMovingOnGrid` interpolating the offset each frame. So
#
#     pixels per frame = tilewidth / step_duration_in_frames
#
# With a 16 px tile that is 16 / 15.4 = 1.04 today. Making the duration exactly
# 16 frames gives exactly 1 px per frame, which the game's math.round() then
# cannot alternate - the scroll becomes uniform.
#
# getGridMoveTimeBySpeed is a plain method on the live player spawnable, so we
# can wrap it and scale its result. factor 1.0 = observe only.
# ---------------------------------------------------------------------------
WALK_SCALE = PRELUDE + r"""
local factor = __FACTOR__
local inst
pcall(function() inst = spawnableHelper:getPlayerSpawnable() end)
if type(inst) ~= 'table' then return '!no player spawnable - are you in the overworld?!' end

if _G.__walkscale then
  _G.__walkscale.factor = factor
  return 'factor now ' .. tostring(factor) .. ' (calls so far ' .. _G.__walkscale.calls .. ')'
end

local orig = inst.getGridMoveTimeBySpeed
if type(orig) ~= 'function' then return '!getGridMoveTimeBySpeed not found on the spawnable!' end

local st = { factor = factor, orig = orig, inst = inst, calls = 0, samples = {} }
_G.__walkscale = st
inst.getGridMoveTimeBySpeed = function(self, ...)
  local r = orig(self, ...)
  st.calls = st.calls + 1
  if #st.samples < 6 then
    local desc = tostring(r)
    if type(r) == 'table' then
      local bits = {}
      for k, v in pairs(r) do
        if #bits < 20 then
          bits[#bits + 1] = tostring(k) .. '=' .. tostring(v) .. ':' .. type(v)
        end
      end
      desc = '{' .. table.concat(bits, ', ') .. '}'
    end
    st.samples[#st.samples + 1] = string.format('nargs=%d  ->  %s', select('#', ...), desc)
  end
  if factor ~= 1 and type(r) == 'table' then
    -- scale whichever duration-ish field this descriptor carries
    for _, k in ipairs({ 'time', 'duration', 'moveTime', 'ms', 'speed' }) do
      if type(r[k]) == 'number' then r[k] = r[k] * factor end
    end
  end
  return r
end
return 'wrapped getGridMoveTimeBySpeed, factor ' .. tostring(factor)
"""

WALK_FIX = PRELUDE + r"""
-- Duration multipliers, e.g. { normal = 0.968, fast = 1.019 }. A value below 1
-- shortens the tile crossing, i.e. makes the player walk faster.
local factors = __FACTORS__

local st = _G.__walkscale or { calls = 0, samples = {}, reapplies = 0 }
_G.__walkscale = st
st.factors = factors

-- Wrap the method on whatever the CURRENT player spawnable is. The game builds
-- a fresh spawnable on every map change and save load, so the wrapper has to be
-- re-applied or the fix silently disappears when you change area.
local function wrap(inst)
  if type(inst) ~= 'table' then return false end
  local orig = inst.getGridMoveTimeBySpeed
  if type(orig) ~= 'function' then return false end
  inst.getGridMoveTimeBySpeed = function(self, ...)
    local r = orig(self, ...)
    st.calls = st.calls + 1
    if type(r) == 'table' then
      for k, f in pairs(st.factors) do
        if type(r[k]) == 'number' then r[k] = r[k] * f end
      end
      if #st.samples < 3 then
        local bits = {}
        for k, v in pairs(r) do bits[#bits + 1] = tostring(k) .. '=' .. tostring(v) end
        table.sort(bits)
        st.samples[#st.samples + 1] = '{' .. table.concat(bits, ', ') .. '}'
      end
    end
    return r
  end
  st.orig = orig
  st.patched = inst
  return true
end

local function apply()
  local inst
  pcall(function() inst = spawnableHelper:getPlayerSpawnable() end)
  if type(inst) ~= 'table' or inst == st.patched then return end
  if wrap(inst) then st.reapplies = st.reapplies + 1 end
end

apply()
if not st.timer then
  st.timer = timer.performWithDelay(1000, apply, 0)
end

if not st.patched then
  return '!no player spawnable found - are you in the overworld?!'
end
return string.format('walk fix active: normal x%.4f  fast x%.4f  (watchdog re-applies every 1s)',
  factors.normal, factors.fast)
"""

# Closed-loop calibration. Sums the world's actual per-frame displacement over a
# walking window, so the measured rate includes the game's own rounding. Using
# TOTAL frames as the denominator (not "frames that moved") matters: at a true
# rate below 1 the game emits occasional 0-px frames, and excluding those would
# bias the estimate upward and hide the error.
RATE_INSTALL = PRELUDE + r"""
local MTE, tw = world()
if type(tw) ~= 'table' then return '!no tiledWorld - are you in the overworld?!' end
local s = { tw = tw, n = 0, sx = 0, sy = 0, mx = 0, my = 0 }
_G.__rate = s
local function f()
  s.n = s.n + 1
  if s.n > 4000 then return false end
  local x, y = tw.x, tw.y
  if s.lx and x ~= s.lx then s.sx = s.sx + math.abs(x - s.lx); s.mx = s.mx + 1 end
  if s.ly and y ~= s.ly then s.sy = s.sy + math.abs(y - s.ly); s.my = s.my + 1 end
  s.lx, s.ly = x, y
  return false
end
s.l = f
Runtime:addEventListener('enterFrame', f)
return 'sampling tiledWorld displacement'
"""

RATE_COLLECT = r"""
local s = _G.__rate
if not s then return '!none!' end
Runtime:removeEventListener('enterFrame', s.l)
_G.__rate = nil
local n = s.n - 1
if n <= 0 then return '0\t0\t0\t0\t0' end
return string.format('%d\t%.4f\t%d\t%.4f\t%d', n, s.sx, s.mx, s.sy, s.my)
"""

WALK_ADJUST = PRELUDE + r"""
-- Multiply the current duration multipliers by `rate`. The step is inversely
-- proportional to the duration, so multiplying by the MEASURED step drives the
-- measured step towards 1 over successive passes.
local rate = __RATE__
local st = _G.__walkscale
if not st or not st.factors then return '!walk fix not installed!' end
local out = {}
for k, f in pairs(st.factors) do
  st.factors[k] = f * rate
  out[#out + 1] = string.format('%s x%.5f', k, st.factors[k])
end
table.sort(out)
return 'adjusted by x' .. string.format('%.5f', rate) .. ':  ' .. table.concat(out, '  ')
"""

DT_INSTALL = r"""
local s = { n = 0, t0 = 0, t1 = 0 }
_G.__dt = s
s.l = function()
  if s.n == 0 then s.t0 = system.getTimer() end
  s.t1 = system.getTimer()
  s.n = s.n + 1
  return false
end
Runtime:addEventListener('enterFrame', s.l)
return 'sampling frame times'
"""

DT_COLLECT = r"""
local s = _G.__dt
if not s then return '!none!' end
Runtime:removeEventListener('enterFrame', s.l)
_G.__dt = nil
local n = s.n - 1
if n <= 0 then return '0\t0' end
return string.format('%d\t%.4f', s.n, (s.t1 - s.t0) / n)
"""

WALK_REPORT = PRELUDE + r"""
local st = _G.__walkscale
if not st then return '!not active - run --walk-fix first!' end
local out = { 'calls     : ' .. tostring(st.calls),
              're-applies: ' .. tostring(st.reapplies or 0) .. '   (map changes / save loads)',
              'watchdog  : ' .. tostring(st.timer ~= nil) }
if st.factors then
  for k, f in pairs(st.factors) do out[#out + 1] = '  factor ' .. k .. ' = ' .. tostring(f) end
end
for _, s in ipairs(st.samples) do out[#out + 1] = '   ' .. s end
if #st.samples == 0 then out[#out + 1] = '   (not called yet - walk to trigger a step)' end
return table.concat(out, '\n')
"""

TRACE_REPORT = PRELUDE + r"""
local t = _G.__mtetrace
if not t then return '!not tracing!' end
local rows = {}
for k, c in pairs(t.counts) do rows[#rows + 1] = { k, c } end
table.sort(rows, function(a, b) return a[2] > b[2] end)
local out = { 'MTE calls since tracing started:' }
local shown = 0
for i = 1, #rows do
  if rows[i][2] > 0 then
    shown = shown + 1
    if shown <= 30 then
      out[#out + 1] = string.format('  %-34s %d', rows[i][1], rows[i][2])
    end
  end
end
if shown == 0 then out[#out + 1] = '  (nothing called yet)' end
out[#out + 1] = '  ' .. shown .. ' of ' .. #rows .. ' functions were called'
return table.concat(out, '\n')
"""

MEASURE_INSTALL = PRELUDE + r"""
local MTE, tw = world()
if type(tw) ~= 'table' then return '!tiledWorld not found - are you in the overworld?!' end
local s = { tw = tw, frames = 0, times = {}, fx = {}, fy = {}, label = _G.__scrollfix and 'fixed' or 'baseline' }
_G.__measure = s
local function sample()
  s.frames = s.frames + 1
  local f = s.frames
  if f > 4000 then return false end
  s.times[f] = system.getTimer()
  s.fx[f] = tw.x
  s.fy[f] = tw.y
  return false
end
s.listener = sample
Runtime:addEventListener('enterFrame', sample)
return 'measuring (' .. s.label .. ')'
"""

MEASURE_COLLECT = r"""
local s = _G.__measure
if not s then return '!none!' end
if s.listener then Runtime:removeEventListener('enterFrame', s.listener); s.listener = nil end
_G.__measure = nil

local out = {}
local n, sum, mx = 0, 0, 0
for k = 2, s.frames do
  local d = s.times[k] - s.times[k - 1]
  if d and d > 0 then n = n + 1; sum = sum + d; if d > mx then mx = d end end
end
out[#out + 1] = string.format('%s: frames=%d  mean=%.2fms  max=%dms',
  s.label, s.frames, n > 0 and sum / n or 0, mx)

local function describe(label, arr)
  local moved, counts, seq, tot = 0, {}, {}, 0
  local prev
  for f = 1, s.frames do
    local v = arr[f]
    if type(v) == 'number' then
      if prev then
        local d = v - prev
        local ad = math.abs(d)
        if ad > 0 then
          moved = moved + 1
          tot = tot + ad
          local bk = math.floor(ad + 0.5)
          counts[bk] = (counts[bk] or 0) + 1
          if #seq < 70 then seq[#seq + 1] = string.format('%.4f', d) end
        end
      end
      prev = v
    end
  end
  local keys = {}
  for k in pairs(counts) do keys[#keys + 1] = k end
  table.sort(keys)
  local parts = {}
  for _, k in ipairs(keys) do parts[#parts + 1] = string.format('%dpx=%d', k, counts[k]) end
  out[#out + 1] = string.format('%s: moved on %d frames, mean |d| = %.4f', label, moved,
    moved > 0 and tot / moved or 0)
  out[#out + 1] = '   step histogram: ' .. table.concat(parts, '  ')
  out[#out + 1] = '   first 70 deltas: ' .. table.concat(seq, ' ')
end

describe('tiledWorld.x', s.fx)
describe('tiledWorld.y', s.fy)
return table.concat(out, '\n')
"""


def _bridge():
    b = Bridge("coromon.exe", hooks=MINIMAL_HOOKS)
    for _ in range(150):
        if b.status().get("state"):
            break
        time.sleep(0.2)
    return b


def _eval(b, code, timeout=30.0):
    r = b.eval(code, timeout=timeout)
    return r.get("out") or r.get("err")


def measure(b, seconds, label):
    print(f"measuring {label} for {seconds:g}s - keep walking ...")
    print(_eval(b, MEASURE_INSTALL))
    time.sleep(seconds)
    print(_eval(b, MEASURE_COLLECT, timeout=60.0))


def _lua_factors(d):
    return "{" + ", ".join(f"{k} = {v!r}" for k, v in d.items()) + "}"


def _measure_dt(b):
    """Average enterFrame interval in ms. Falls back to 60 fps."""
    print("measuring frame time (stand still) ...")
    print(_eval(b, DT_INSTALL))
    time.sleep(1.5)
    r = _eval(b, DT_COLLECT)
    try:
        dt = float(r.strip().split("\t")[1])
    except Exception:
        dt = 1000.0 / 60.0
    return dt if dt > 0 else 1000.0 / 60.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="coromon.exe")
    ap.add_argument("--status", action="store_true", help="show whether the fix is installed")
    ap.add_argument("--on", action="store_true", help="install the constant-step fix")
    ap.add_argument("--off", action="store_true", help="restore the original functions")
    ap.add_argument("--measure", action="store_true", help="log per-frame world deltas")
    ap.add_argument("--ab", action="store_true",
                    help="measure baseline, install, measure again, then leave it installed")
    ap.add_argument("--trace", action="store_true",
                    help="count calls into every public MTE function (walk while it runs)")
    ap.add_argument("--trace-report", action="store_true",
                    help="print the MTE call counts accumulated so far")
    ap.add_argument("--snap", action="store_true",
                    help="round the per-frame movement delta to whole content pixels")
    ap.add_argument("--snap-report", action="store_true",
                    help="show what the snap wrapper has been catching")
    ap.add_argument("--find-speed", action="store_true",
                    help="locate the movementSpeed upvalue in the walk code")
    ap.add_argument("--trace-spawn", action="store_true",
                    help="count calls into the player spawnable's own methods")
    ap.add_argument("--trace-spawn-report", action="store_true",
                    help="print the player spawnable method counts")
    ap.add_argument("--walk-scale", type=float, default=None,
                    help="scale getGridMoveTimeBySpeed uniformly (1.0 = observe)")
    ap.add_argument("--walk-fix", action="store_true",
                    help="measure the frame time and set the tile durations for a whole-pixel step")
    ap.add_argument("--walk-speed", type=float, default=None,
                    help="sanity check: force a specific px/frame (e.g. 10). Restore with --walk-fix")
    ap.add_argument("--walk-calibrate", action="store_true",
                    help="closed loop: measure the real step and correct until it is exactly 1 px/frame")
    ap.add_argument("--iters", type=int, default=4, help="max calibration passes")
    ap.add_argument("--walk-report", action="store_true",
                    help="show the baseline getGridMoveTimeBySpeed descriptors")
    ap.add_argument("--set-speed", type=float, default=None,
                    help="set movementSpeed (want 2 * fps: 110 at 55 fps, 120 at 60 fps)")
    ap.add_argument("--seconds", type=float, default=6.0, help="seconds per measurement")
    args = ap.parse_args()

    try:
        import frida  # noqa: F401
    except ImportError:
        print("frida is not installed")
        return 1

    b = _bridge()
    try:
        if args.status:
            print(_eval(b, STATUS))
        elif args.trace:
            print(_eval(b, TRACE_INSTALL))
        elif args.trace_report:
            print(_eval(b, TRACE_REPORT))
        elif args.snap:
            print(_eval(b, SNAP_INSTALL))
        elif args.snap_report:
            print(_eval(b, SNAP_REPORT))
        elif args.find_speed:
            print(_eval(b, FIND_SPEED))
        elif args.trace_spawn:
            print(_eval(b, SPAWN_TRACE_INSTALL))
        elif args.trace_spawn_report:
            print(_eval(b, SPAWN_TRACE_REPORT))
        elif args.walk_scale is not None:
            print(_eval(b, WALK_SCALE.replace("__FACTOR__", repr(args.walk_scale))))
        elif args.walk_fix:
            print(_eval(b, REMOVE))
            dt = _measure_dt(b)
            # px/frame = (tilewidth / duration_ms) * dt_ms.  Want exactly 1 for
            # normal and exactly 2 for fast => 16*dt and 8*dt.
            fn = (16.0 * dt) / 280.0
            ff = (8.0 * dt) / 133.0
            print(f"avg frame time = {dt:.4f} ms  ({1000.0 / dt:.2f} fps)")
            print(f"  normal 280 ms -> {280.0 * fn:.2f} ms   = 1 px/frame")
            print(f"  fast   133 ms -> {133.0 * ff:.2f} ms   = 2 px/frame")
            print(f"  slow   400 ms unchanged (0.67 px/frame; making it whole would equal normal)")
            code = WALK_FIX.replace("__FACTORS__", _lua_factors({"normal": fn, "fast": ff}))
            print(_eval(b, code))
            print(_eval(b, WALK_REPORT))
        elif args.walk_calibrate:
            # Pure closed loop: start from whatever is installed (stock = no
            # change), then correct using the MEASURED step. No feed-forward
            # guess, so there is no assumption about tile size, frame time or
            # the shape of the formula that could be wrong.
            print(_eval(b, WALK_FIX.replace(
                "__FACTORS__", _lua_factors({"normal": 1.0, "fast": 1.0}))))
            print(_eval(b, WALK_REPORT))
            for it in range(1, args.iters + 1):
                print()
                print(f"pass {it}/{args.iters}: walk continuously for {args.seconds:g}s ...")
                print(_eval(b, RATE_INSTALL))
                time.sleep(args.seconds)
                raw = _eval(b, RATE_COLLECT).strip()
                try:
                    parts = raw.split("\t")
                    n = int(parts[0])
                    sx, mx = float(parts[1]), int(parts[2])
                    sy, my = float(parts[3]), int(parts[4])
                except Exception:
                    print(f"  unparsable: {raw!r}")
                    break
                if n <= 0:
                    print("  no frames")
                    break
                if mx >= my:
                    total, moved, axis = sx, mx, "x"
                else:
                    total, moved, axis = sy, my, "y"
                ratio = moved / n
                rate = total / n
                print(f"  frames={n}  axis={axis}  moved on {ratio * 100:.1f}% of frames")
                print(f"  measured step = {rate:.5f} px/frame   (want 1.00000)")
                if ratio < 0.7:
                    print("  not enough continuous walking - walk without stopping")
                    continue
                if abs(rate - 1.0) < 0.002:
                    print("  within tolerance - done")
                    break
                # step is inversely proportional to duration, so multiplying the
                # current multipliers by the measured error drives step -> 1
                print("  " + _eval(b, WALK_ADJUST.replace("__RATE__", repr(rate))))
            print()
            print(_eval(b, WALK_REPORT))
        elif args.walk_speed is not None:
            # Deliberately wrong on purpose: a big obvious speed change proves the
            # wrapper really is driving the walk speed. Restore with --walk-fix.
            print(_eval(b, REMOVE))
            dt = _measure_dt(b)
            f = (16.0 * dt / args.walk_speed) / 280.0
            print(f"avg frame time = {dt:.4f} ms")
            print(f"  target {args.walk_speed} px/frame -> all durations x{f:.5f}")
            print(f"  normal 280 -> {280.0 * f:.3f} ms, fast 133 -> {133.0 * f:.3f} ms, "
                  f"slow 400 -> {400.0 * f:.3f} ms")
            code = WALK_FIX.replace(
                "__FACTORS__", _lua_factors({"normal": f, "fast": f, "slow": f}))
            print(_eval(b, code))
            print(_eval(b, WALK_REPORT))
        elif args.set_speed is not None:
            print(_eval(b, SET_SPEED.replace("__SPEED__", repr(args.set_speed))))
        elif args.off:
            print(_eval(b, REMOVE))
        elif args.on:
            print(_eval(b, INSTALL))
            print(_eval(b, STATUS))
        elif args.ab:
            measure(b, args.seconds, "BASELINE (game as shipped)")
            print()
            print(_eval(b, INSTALL))
            measure(b, args.seconds, "FIXED (constant step)")
            print()
            print("The fix is left installed. Run --off to restore, or close the game.")
        elif args.measure:
            print(_eval(b, STATUS))
            measure(b, args.seconds, "current")
        else:
            print(__doc__.strip())
            print()
            print("EVERYDAY COMMANDS")
            print("  --walk-fix      apply the fix (run once per game launch, while in the overworld)")
            print("  --walk-report   show whether it is active, plus call and re-apply counts")
            print("  --off           undo it immediately (also undone by closing the game)")
            print()
            print("DIAGNOSTICS")
            print("  --measure --seconds 8   per-frame world step histogram, to verify it worked")
            print()
            print("Leftovers from the investigation, not needed for normal use:")
            print("  --on --snap --trace --trace-spawn --find-speed --set-speed --walk-scale")
            print("  --status --ab --trace-report --snap-report --trace-spawn-report")
    finally:
        b.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
