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

# Closed-loop calibration.
#
# The estimator must be immune to PAUSES. Dividing total displacement by total
# frames does not work: when the step is about 1 px, that ratio is really the
# duty cycle - "what fraction of frames was I walking" - not the step size. A
# pause then looks like "too fast" and the loop winds the speed up forever.
#
# So: track runs of continuous movement, closed after 6 consecutive still frames
# (~100 ms). A single 0-px frame inside a walk does NOT close the run, because at
# a true rate just under 1 the game emits isolated 0-px frames as part of normal
# walking. The rate is then sum(|d|)/frames over the longest run.
RATE_INSTALL = PRELUDE + r"""
local MTE, tw = world()
if type(tw) ~= 'table' then return '!no tiledWorld - are you in the overworld?!' end
local s = { tw = tw, n = 0, zeros = 0, runN = 0, runSum = 0,
            bestN = 0, bestSum = 0, runs = 0 }
_G.__rate = s
local function f()
  s.n = s.n + 1
  if s.n > 4000 then return false end
  local x, y = tw.x, tw.y
  local d = 0
  if s.lx then d = math.abs(x - s.lx) + math.abs(y - s.ly) end
  s.lx, s.ly = x, y
  s.runN = s.runN + 1
  s.runSum = s.runSum + d
  if d > 0 then
    s.zeros = 0
  else
    s.zeros = s.zeros + 1
    if s.zeros >= 6 then
      -- a real pause: drop its frames and close the run
      s.runN = s.runN - s.zeros
      s.runs = s.runs + 1
      if s.runN > s.bestN then s.bestN = s.runN; s.bestSum = s.runSum end
      s.runN, s.runSum, s.zeros = 0, 0, 0
    end
  end
  return false
end
s.l = f
Runtime:addEventListener('enterFrame', f)
return 'sampling tiledWorld displacement (longest continuous walk is what counts)'
"""

RATE_COLLECT = r"""
local s = _G.__rate
if not s then return '!none!' end
Runtime:removeEventListener('enterFrame', s.l)
_G.__rate = nil
-- close whatever run is still open at the end
s.runN = s.runN - s.zeros
if s.runN > s.bestN then s.bestN = s.runN; s.bestSum = s.runSum end
return string.format('%d\t%d\t%.4f\t%d', s.n - 1, s.bestN, s.bestSum, s.runs)
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

WALK_FACTORS = PRELUDE + r"""
local st = _G.__walkscale
if not st or not st.factors then return '!not installed!' end
local out = {}
for k, f in pairs(st.factors) do out[#out + 1] = string.format('%s=%.8f', k, f) end
table.sort(out)
return table.concat(out, '\t')
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

# ---------------------------------------------------------------------------
# Sub-content-pixel camera.
#
# The game writes the camera focus as math.round(...) - see
# classes.modules.mte.mte proto at lines 1141-1152:
#
#     posX = sprite.x + sprite.width * 0.5 + sprite.cameraFocusOffsetX
#     return math.round(posX), math.round(posY)
#
# That is the ONLY integer quantization on the scroll path (the transition sets
# sprite.x to a float). So the world can only ever advance in whole content
# pixels - at this window size, 3 screen pixels at a time.
#
# This nudges the camera between those integers by re-adding the fraction the
# game threw away. It never guesses the game's offset formula or its rounding
# rule. Instead it self-calibrates the invariant
#
#     tw.x + tx  =  C + frac,    frac in [-0.5, +0.5),  C constant per map
#
# from the observed min/max of (tw.x + tx) while you walk: the midpoint of that
# range is C, and the exact target is then just C - tx.
#
# Safety: the correction is clamped to +-0.75 content px from the value the game
# itself wrote, so anything the game does deliberately (map-edge clamping, a
# camera locked for a cutscene) still wins. We can nudge the world by a fraction
# of a pixel; we can never override the game's intent.
# ---------------------------------------------------------------------------
SUBPIXEL_INSTALL = PRELUDE + r"""
local MTE, tw = world()
if type(tw) ~= 'table' then return '!no tiledWorld - are you in the overworld?!' end

local s = _G.__subpix2 or {}
_G.__subpix2 = s
-- Solar2D cannot enumerate or remove listeners selectively, so every earlier
-- generation of this listener that was ever installed in this game process is
-- still registered. They all hardcode a state global, so the ones from before
-- this revision are neutralised by leaving their globals switched off:
--   _G.__subpix      (first revision)  - stale copies see on=false and bail
--   _G.__subpix_live (second revision) - same
-- Copies of THIS revision are handled by the identity check in tick() below.
local function decoy(name)
  if type(_G[name]) ~= 'table' then
    _G[name] = { on = false }
  else
    _G[name].on = false
  end
end
decoy('__subpix')
decoy('__subpix_live')
s.on = true
s.n = 0
s.tw = tw
s.frames = 0
-- 0 = continuous.  N > 0 = snap the world to whole DEVICE pixels, i.e. to
-- multiples of 1/N content px. With nearest filtering that keeps every source
-- texel mapped to exactly N x N device pixels (crisp, no crawl) while letting
-- the pan advance one device pixel at a time instead of N at a time.
s.grid = __GRID__
s.latches = s.latches or 0
s.unstable = s.unstable or 0
s.sat = s.sat or 0
s.snapped = 0
s.devN, s.devSum, s.devAbsSum = 0, 0, 0
s.devMin, s.devMax = nil, nil
s.latches, s.unstable, s.sat, s.skipped, s.snapped = 0, 0, 0, 0, 0
s.phMax = nil
s.x, s.y = {}, {}

-- One axis: derive C, then hand back the game's value plus the fraction it
-- discarded. Returns the value to write.
--
-- The game writes   g = C - math.round(t)   (mte.lu, camera focus functions), and
-- t is a positive content coordinate, so for t >= 0 math.round(t) is exactly
-- math.floor(t + 0.5). Measured live: C = 240 for x and 135 for y - the screen
-- centre - and it never changes.
--
-- The camera is only written WHEN IT CHANGES, so most frames read back whatever
-- was there before (which may be our own value). A latch taken on such a frame is
-- garbage, and a wrong C silently disables the whole feature. So C is (re)taken
-- only on a frame where math.round(t) changed: that is exactly when the camera
-- moved, so the game is guaranteed to have refreshed it and g is trustworthy.
-- Taking it there on every such frame also makes it self-healing across map
-- changes, with no calibration period and nothing to reset.
local function axis(e, t, g)
  local r = math.floor(t + 0.5)        -- the game's own rounding, for t >= 0
  local crossed = (e.r ~= nil) and (r ~= e.r)
  e.r = r
  if not e.C then
    -- Bootstrap from the game's own current value. This is deliberately NOT a
    -- guess at the screen centre: our copy of the focus formula is off by a small
    -- constant (measured), so a guessed C can differ from the game's by more than
    -- the 0.75 guard and then every frame gets refused. Latching g + round(t)
    -- absorbs that constant instead, which makes the whole correction invariant
    -- to it: want = C - t is exact for ANY constant error in t.
    e.C = g + r
    s.latches = s.latches + 1
  end
  if crossed then
    e.C = g + r                        -- trustworthy: the game just wrote it
    s.latches = s.latches + 1
  end
  if not e.C then return g end         -- nothing to correct yet
  local want = e.C - t                 -- the exact, unrounded camera target
  if s.grid and s.grid > 0 then
    -- whole device pixels: the world offset * grid becomes an integer again, so
    -- every source texel still maps to exactly grid x grid device pixels
    local q = want * s.grid
    local rq = math.floor(q + 0.5)
    if rq ~= q then s.snapped = s.snapped + 1 end
    want = rq / s.grid
  end
  local dev = want - g
  if dev > 0.75 or dev < -0.75 then
    -- we disagree with the game by more than any rounding could explain (a
    -- camera locked for a cutscene, a map-edge clamp): stand down rather than
    -- shove the world sideways. It re-latches on the next pixel crossing.
    s.sat = s.sat + 1
    return g
  end
  if math.abs(dev) > -1 then
    s.lastDev = dev              -- reported by SUBPIXEL_REPORT
  end
  local res = g + dev
  s.devN = s.devN + 1
  s.devSum = s.devSum + dev
  s.devAbsSum = s.devAbsSum + math.abs(dev)
  if not s.devMin or dev < s.devMin then s.devMin = dev end
  if not s.devMax or dev > s.devMax then s.devMax = dev end
  if s.grid and s.grid > 0 then
    -- how far the world still is from a whole device pixel: 0 = perfectly crisp
    local q = res * s.grid
    local fr = math.abs(q - math.floor(q + 0.5))
    if not s.phMax or fr > s.phMax then s.phMax = fr end
  end
  return res
end

local function tick()
  local st = _G.__subpix2
  -- st.tick is set to this closure by the install below. Two copies of the same
  -- revision would otherwise both write tw.x and corrupt each other's C latch.
  if not st or not st.on or st.tick ~= tick then return false end
  st.n = st.n + 1
  if st.n % 60 == 0 then
    local _, tw2 = world()
    if type(tw2) == 'table' and tw2 ~= st.tw then
      st.tw = tw2
      st.x, st.y = {}, {}
      st.latches = st.latches + 1
    end
    pcall(function()
      local i = spawnableHelper:getPlayerSpawnable()
      if i and i.sprite then st.spr = i.sprite end
    end)
  end
  local spr, twc = st.spr, st.tw
  if type(spr) ~= 'table' or type(twc) ~= 'table' then return false end
  local gx, gy = twc.x, twc.y
  if type(gx) ~= 'number' or type(gy) ~= 'number' then return false end
  local tx = spr.x + (spr.width or 0) * 0.5 + (spr.cameraFocusOffsetX or 0)
  local ty = spr.y - (spr.height or 0) * 0.5 + (spr.cameraFocusOffsetY or 0)
  if type(tx) ~= 'number' or type(ty) ~= 'number' then return false end
  st.frames = st.frames + 1
  twc.x = axis(st.x, tx, gx)
  twc.y = axis(st.y, ty, gy)
  return false
end
s.l = tick
s.tick = tick          -- identity marker: only this closure may run (see tick)
Runtime:addEventListener('enterFrame', tick)
return string.format('sub-pixel camera ON (grid=%s) (listener registered last, so it runs after the game)',
  s.grid and s.grid > 0 and ('1/' .. tostring(s.grid) .. ' content px') or 'continuous')
"""

SUBPIXEL_REPORT = PRELUDE + r"""
local s = _G.__subpix2
if not s then return '!sub-pixel camera not installed!' end
local _, tw = world()
local function c(e)
  if e and e.C then return string.format('%.4f', e.C) end
  return '(not calibrated yet)'
end
local out = {
  string.format('active=%s  grid=%s  frames=%d  C latched=%d  C changed=%d  saturated=%d',
    tostring(s.on), s.grid and s.grid > 0 and tostring(s.grid) or 'continuous',
    s.frames or 0, s.latches or 0, s.unstable or 0, s.sat or 0),
  'calibrated C: x=' .. c(s.x) .. '  y=' .. c(s.y),
}
-- Live check. The game itself only ever writes WHOLE pixels here, so a fractional
-- tiledWorld value can only have come from us. While you stand still the offset is
-- ~0 by design (the camera only exists relative to the player), so a zero offset
-- with a high frame count is "idle", not "dead" - the tell-tale is "applied on N
-- frames" and a non-zero last offset once you walk.
do
  local live = 'tiledWorld: unavailable'
  pcall(function()
    if type(tw) == 'table' and type(tw.x) == 'number' then
      live = string.format('tiledWorld.x = %.4f  (fractional part %.4f)',
        tw.x, tw.x - math.floor(tw.x))
    end
  end)
  out[#out + 1] = live
  local d = s.lastDev
  out[#out + 1] = string.format('last offset we applied: %s   (0 while idle is expected)',
    d and string.format('%+.4f content px = %+.2f device px', d, d * (s.grid or 1)) or 'none yet')
end
do
  local keys = {}
  for k, v in pairs(display) do
    if type(k) == 'string' and k:find('pixel') or (type(k) == 'string' and k:find('cale')) then
      keys[#keys + 1] = k .. '(' .. type(v) .. ')'
    end
  end
  table.sort(keys)
  if #keys > 0 then out[#out + 1] = 'display scale keys: ' .. table.concat(keys, '  ') end
end
if (s.devN or 0) > 0 then
  out[#out + 1] = string.format(
    'offset applied on %d frames (skipped %d): range %.4f .. %.4f   mean |offset| %.4f   mean offset %+.4f',
    s.devN, s.skipped or 0, s.devMin or 0, s.devMax or 0,
    s.devAbsSum / s.devN, s.devSum / s.devN)
  out[#out + 1] = '   (mean offset ~0 means our t reproduces the game\'s exactly; a steady\n'
    .. '    bias instead means one term of the camera formula is not what we think)'
  if s.grid and s.grid > 0 and s.phMax then
    out[#out + 1] = string.format(
      'crispness: worst distance from a whole device pixel = %.9f device px (0 = perfect)',
      s.phMax)
  end
else
  out[#out + 1] = 'no offset applied yet'
end
return table.concat(out, '\n')
"""

SUBPIXEL_REMOVE = r"""
local s = _G.__subpix2
if not s then return 'sub-pixel camera was not installed' end
s.on = false
if s.l then Runtime:removeEventListener('enterFrame', s.l); s.l = nil end
_G.__subpix2 = nil
return 'sub-pixel camera OFF - the game owns tw.x/tw.y again'
"""

# ---------------------------------------------------------------------------
# Pure-math self test for the sub-pixel camera: replays a simulated walk against
# the REAL axis() source lifted out of SUBPIXEL_INSTALL (so the test cannot drift
# from the code), models the game only writing the camera when it changes, and
# checks the resulting world offsets in whole device pixels. Touches no game
# state and needs no input.
# ---------------------------------------------------------------------------
SELFTEST_SUBPIXEL = r"""
local s = { grid = __GRID__, latches = 0, snapped = 0, devN = 0, devSum = 0,
            devAbsSum = 0, skipped = 0, sat = 0 }__AXIS__

local C = 240                      -- screen centre, as measured live
local step = 1.00054               -- content px/frame of the fixed walk at 59 fps
local t = 100.0
local e = {}
local world = C - math.floor(t + 0.5)
local lastGame = world
local n, stepMin, stepMax, stepSum = 0, nil, nil, 0
local crisp, negSteps, bigSteps = 0, 0, 0
local prev
for f = 1, 900 do
  t = t + step
  local exp = C - math.floor(t + 0.5)
  if exp ~= lastGame then
    lastGame = exp              -- the game refreshed the camera this frame
    world = exp
  end
  world = axis(e, t, world)     -- read it, correct it, write it back
  if prev then
    local d = (world - prev) * 3
    n = n + 1
    stepSum = stepSum + d
    if not stepMin or d < stepMin then stepMin = d end
    if not stepMax or d > stepMax then stepMax = d end
    if d < 0 then negSteps = negSteps + 1 end
    if d > 4.0001 then bigSteps = bigSteps + 1 end
  end
  local q = world * 3
  local fr = math.abs(q - math.floor(q + 0.5))
  if fr > crisp then crisp = fr end
  prev = world
end
return string.format(
  'grid=%-10s frames=%d  device px/frame: min %.4f  mean %.4f  max %.4f  backwards steps=%d  steps > 4px=%d',
  s.grid > 0 and tostring(s.grid) or 'continuous', n, stepMin, stepSum / n, stepMax, negSteps, bigSteps)
  .. string.format('\n   worst distance from a whole device pixel: %.9f device px', crisp)
  .. string.format('\n   applied=%d skipped=%d saturated=%d  latched C=%.6f (want 240)',
       s.devN, s.skipped, s.sat, e.C or -1)
"""


def _axis_source():
    """Lift the real axis() implementation out of SUBPIXEL_INSTALL."""
    txt = SUBPIXEL_INSTALL
    start = txt.index("local function axis(")
    end = txt.index("\nlocal function tick()")
    return txt[start:end]


def selftest_subpixel(b):
    for grid in (3, 0):
        print(_eval(b, SELFTEST_SUBPIXEL.replace("__GRID__", str(grid))
                    .replace("__AXIS__", _axis_source()), timeout=30.0))
        print()

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

# ---------------------------------------------------------------------------
# Per-frame forensics. Records the frame time, the camera step and the sprite's
# sub-pixel phase on the SAME frame, which is what separates the two remaining
# explanations for "it holds for a frame, then jumps 2 px":
#
#   * rounding-driven: the camera target is math.round(sprite.x + offset).
#     sprite.x creeps up by slightly more than 1 px per frame, so every N frames
#     the rounded value skips a whole pixel. The double step then ALWAYS lands at
#     the same sub-pixel phase (just after the fractional part wraps), whatever
#     the frame time was.
#
#   * pacing-driven: the game missed a vsync, so one frame interval was ~2x
#     normal (33 ms). The interpolation correctly advances 2 px because two
#     frames of wall clock really did pass. The double step then lands on a
#     LONG frame, at any phase.
# ---------------------------------------------------------------------------
WALK_DIAG_INSTALL = PRELUDE + r"""
local MTE, tw = world()
if type(tw) ~= 'table' then return '!no tiledWorld - are you in the overworld?!' end
local spr
pcall(function()
  local i = spawnableHelper:getPlayerSpawnable()
  spr = i and i.sprite
end)
local s = { n = 0, tw = tw, spr = spr, t = {}, cx = {}, cy = {}, sx = {} }
if _G.__wdiag and _G.__wdiag.l then
  Runtime:removeEventListener('enterFrame', _G.__wdiag.l)
end
_G.__wdiag = s
local function tick()
  s.n = s.n + 1
  local f = s.n
  if f > 40000 then return false end
  if f % 120 == 0 then
    pcall(function()
      local i = spawnableHelper:getPlayerSpawnable()
      if i and i.sprite then s.spr = i.sprite end
    end)
  end
  s.t[f] = system.getTimer()
  s.cx[f] = tw.x
  s.cy[f] = tw.y
  if s.spr then s.sx[f] = s.spr.x end
  return false
end
s.l = tick
Runtime:addEventListener('enterFrame', tick)
return 'recording (about 11 minutes of frames). Play normally, then run --walk-diag report'
"""

# Stop recording without producing a report.
WALK_DIAG_OFF = r"""
local s = _G.__wdiag
if not s then return 'diagnostic was not recording' end
if s.l then Runtime:removeEventListener('enterFrame', s.l) end
_G.__wdiag = nil
return 'diagnostic stopped'
"""

# Progress readout so the caller can wait for real walking instead of guessing.
WALK_DIAG_PEEK = r"""
local s = _G.__wdiag
if not s then return '0\t0' end
local walk = 0
for f = 2, s.n do
  if math.abs(s.cx[f] - s.cx[f - 1]) + math.abs(s.cy[f] - s.cy[f - 1]) > 0 then
    walk = walk + 1
  end
end
return string.format('%d\t%d', s.n, walk)
"""

WALK_DIAG_COLLECT = r"""
local s = _G.__wdiag
if not s then return '!none!' end
Runtime:removeEventListener('enterFrame', s.l)
_G.__wdiag = nil
local n = s.n
if n < 20 then return 'only ' .. tostring(n) .. ' frames captured' end

local out = {}

-- a frame counts as "walking" if the camera moved within +-3 frames of it
local inRun = {}
for f = 2, n do
  local a = math.max(math.abs(s.cx[f] - s.cx[f - 1]), math.abs(s.cy[f] - s.cy[f - 1]))
  if a > 0 then
    for g = math.max(2, f - 3), math.min(n, f + 3) do inRun[g] = true end
  end
end

local dtAll, dtRun = {}, {}
for f = 2, n do
  local d = s.t[f] - s.t[f - 1]
  dtAll[#dtAll + 1] = d
  if inRun[f] then dtRun[#dtRun + 1] = d end
end

local function stats(t, label)
  if #t == 0 then return label .. ': (no frames)' end
  local mn, mx, sum, o24, o30 = t[1], t[1], 0, 0, 0
  for i = 1, #t do
    local v = t[i]
    if v < mn then mn = v end
    if v > mx then mx = v end
    sum = sum + v
    if v > 24 then o24 = o24 + 1 end
    if v > 30 then o30 = o30 + 1 end
  end
  return string.format('%s: n=%d  min %.2f  mean %.3f  max %.2f   >24ms:%d  >30ms:%d',
    label, #t, mn, sum / #t, mx, o24, o30)
end

out[#out + 1] = stats(dtAll, 'frame time (all frames)')
out[#out + 1] = stats(dtRun, 'frame time (while walking)')

local hist, walkN, odd = {}, 0, {}
for f = 2, n do
  if inRun[f] then
    walkN = walkN + 1
    local d = math.max(math.abs(s.cx[f] - s.cx[f - 1]), math.abs(s.cy[f] - s.cy[f - 1]))
    local k = math.floor(d + 0.5)
    hist[k] = (hist[k] or 0) + 1
    odd[#odd + 1] = { f = f, k = k, dt = s.t[f] - s.t[f - 1] }
  end
end
local keys = {}
for k in pairs(hist) do keys[#keys + 1] = k end
table.sort(keys)
local parts = {}
for _, k in ipairs(keys) do parts[#parts + 1] = string.format('%dpx=%d', k, hist[k]) end
out[#out + 1] = 'camera step while walking: ' .. table.concat(parts, '  ')

-- Fractional steps are the proof that the world stopped advancing in whole
-- pixels: with the sub-pixel camera on, these become ~1.0000 everywhere instead
-- of a mix of 0 and 1 (or 1 and 2).
do
  local dmin, dmax, dsum, fN = nil, nil, 0, 0
  for f = 2, n do
    if inRun[f] then
      local d = math.max(math.abs(s.cx[f] - s.cx[f - 1]), math.abs(s.cy[f] - s.cy[f - 1]))
      if d > 0 then
        fN = fN + 1
        dsum = dsum + d
        if not dmin or d < dmin then dmin = d end
        if not dmax or d > dmax then dmax = d end
      end
    end
  end
  if fN > 0 then
    out[#out + 1] = string.format(
      'step over moving frames: min %.4f  mean %.4f  max %.4f   (n=%d)',
      dmin, dsum / fN, dmax, fN)
  end
end

-- How far does the player drift on screen while walking? If the camera and the
-- sprite are quantized differently (round() on one, not the other) the player
-- wobbles by up to half a content pixel = 1.5 screen pixels. Pinned is best.
do
  local px = {}
  for f = 2, n do
    if inRun[f] and s.sx[f] then px[#px + 1] = s.sx[f] + s.cx[f] end
  end
  if #px > 4 then
    local lo, hi, sum = px[1], px[1], 0
    for i = 1, #px do
      if px[i] < lo then lo = px[i] end
      if px[i] > hi then hi = px[i] end
      sum = sum + px[i]
    end
    out[#out + 1] = string.format(
      'player screen offset (sprite.x + tiledWorld.x): range %.4f content px = %.2f screen px',
      hi - lo, (hi - lo) * 3)
  end
end

-- The interesting frames are the ones that disagree with the DOMINANT step, not
-- the ones that disagree with 1: when the run key is held the correct step is 2,
-- and reporting all of those as anomalies buries the real events.
local mode, modeN = 0, -1
for _, k in ipairs(keys) do
  if hist[k] > modeN then mode, modeN = k, hist[k] end
end
out[#out + 1] = string.format('dominant step = %d px/frame (%d of %d walking frames)',
  mode, modeN, walkN)

local anom = {}
for _, r in ipairs(odd) do
  if r.k ~= mode then anom[#anom + 1] = r end
end
out[#out + 1] = string.format('frames that disagreed with %d px: %d of %d',
  mode, #anom, walkN)

if #anom > 0 then
  -- group them: a halt one frame after a normal step is a rounding/pacing artefact,
  -- a halt at the START or END of a run is just the game starting/stopping to walk
  local halts, overs, gaps = 0, 0, {}
  local prevF = nil
  for _, r in ipairs(anom) do
    if r.k < mode then halts = halts + 1 else overs = overs + 1 end
    if prevF then gaps[#gaps + 1] = r.f - prevF end
    prevF = r.f
  end
  local gsum, gmax, gmin = 0, 0, nil
  for _, g in ipairs(gaps) do
    gsum = gsum + g
    if g > gmax then gmax = g end
    if not gmin or g < gmin then gmin = g end
  end
  out[#out + 1] = string.format(
    '   halts (< %d px): %d    overshoots (> %d px): %d    gaps between them: min %s max %d (~%s of a tile)',
    mode, halts, mode, overs, gmin and tostring(gmin) or '-', gmax,
    gmin and string.format('%.2f', (gsum / #gaps) / 16) or '-')
  out[#out + 1] = '   frame   step   dt(ms)  prev dt(ms)  prev step   sprite frac'
  for i = 1, math.min(#anom, 16) do
    local f = anom[i].f
    local dtp, pk = 0, 0
    if f >= 3 then
      dtp = s.t[f - 1] - s.t[f - 2]
      pk = math.floor(math.max(math.abs(s.cx[f - 1] - s.cx[f - 2]),
                               math.abs(s.cy[f - 1] - s.cy[f - 2])) + 0.5)
    end
    local ph = -1
    if s.sx and s.sx[f] then ph = s.sx[f] - math.floor(s.sx[f]) end
    out[#out + 1] = string.format('   %5d   %4d   %7.2f   %10.2f   %9d   %11.4f',
      f, anom[i].k, anom[i].dt, dtp, pk, ph)
  end
end

-- where in the sprite's sub-pixel phase do the anomalies land?
local phMin, phMax = nil, nil
if s.sx then
  for f = 2, n do
    local v = s.sx[f]
    if inRun[f] and v then
      local ph = v - math.floor(v)
      if not phMin or ph < phMin then phMin = ph end
      if not phMax or ph > phMax then phMax = ph end
    end
  end
end
if phMin then
  out[#out + 1] = string.format('sprite.x fractional part spans %.4f .. %.4f', phMin, phMax)
end
if #anom > 0 then
  -- a tight phase cluster means the camera rounding did it; a spread means the
  -- frame time did (or that the walk simply started/stopped inside the window)
  local lo, hi = 1, 0
  local dtsum, nbig = 0, 0
  for _, r in ipairs(anom) do
    local f = r.f
    if s.sx and s.sx[f] then
      local ph = s.sx[f] - math.floor(s.sx[f])
      if ph < lo then lo = ph end
      if ph > hi then hi = ph end
    end
    dtsum = dtsum + r.dt
    if r.dt > 24 then nbig = nbig + 1 end
  end
  out[#out + 1] = string.format(
    '   verdict inputs: phase cluster %.3f wide, mean dt on anomalies %.2f ms, anomalies on a >24ms frame: %d',
    hi - lo, dtsum / #anom, nbig)
end

local okd, dinfo = pcall(function()
  local function g(f)
    local ok, v = pcall(f)
    return ok and v or nil
  end
  return string.format(
    'display: content %s x %s   pixel %s x %s   contentScale %s / %s   fps %s   msPerFrame %s',
    tostring(g(function() return display.contentWidth end)),
    tostring(g(function() return display.contentHeight end)),
    tostring(g(function() return display.pixelWidth end)),
    tostring(g(function() return display.pixelHeight end)),
    tostring(g(function() return display.contentScaleX end)),
    tostring(g(function() return display.contentScaleY end)),
    tostring(g(function() return display.fps end)),
    tostring(g(function() return display.msPerFrame end)))
end)
out[#out + 1] = okd and dinfo or ('display info unavailable: ' .. tostring(dinfo))
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


def diag(b, mode):
    """Install / report / stop the per-frame camera-step recorder.

    Nothing here waits on you: 'on' just starts recording in the background while
    you play, and 'report' analyses whatever it has buffered so far, whenever you
    feel like running it.
    """
    if mode == "off":
        print(_eval(b, WALK_DIAG_OFF))
        return
    if mode == "report":
        print(_eval(b, WALK_DIAG_COLLECT, timeout=60.0))
        return
    print(_eval(b, WALK_DIAG_INSTALL))


def _lua_factors(d):
    return "{" + ", ".join(f"{k} = {v!r}" for k, v in d.items()) + "}"


def _parse_factors(s):
    """Parse the 'normal=0.968\tfast=1.019' readout into a dict."""
    d = {}
    for part in s.strip().split("\t"):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                d[k.strip()] = float(v)
            except ValueError:
                pass
    return d


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
    ap.add_argument("--walk-const", type=float, nargs=2, default=None,
                    metavar=("NORMAL_MS", "FAST_MS"),
                    help="apply fixed tile durations, skipping the frame-time measurement")
    ap.add_argument("--iters", type=int, default=0,
                    help="max calibration passes (0 = until it converges, default)")
    ap.add_argument("--walk-report", action="store_true",
                    help="show the baseline getGridMoveTimeBySpeed descriptors")
    ap.add_argument("--walk-diag", nargs="?", const="on", default=None,
                    choices=["on", "off", "report"],
                    help="camera-step recorder: 'on' records in the background while you play "
                         "(no waiting on you), 'report' prints the analysis, 'off' stops it")
    ap.add_argument("--walk-subpixel", nargs="?", const="on", default=None,
                    choices=["on", "off", "report", "grid"],
                    help="re-inject the fraction math.round() discards: 'on' = continuous, "
                         "'grid' = snap to whole device pixels (crisp pixel art, 1 device px "
                         "of motion granularity), off/report")
    ap.add_argument("--subpixel-grid", type=float, default=3.0,
                    help="device pixels per content pixel for --walk-subpixel grid (default 3)")
    ap.add_argument("--selftest-subpixel", action="store_true",
                    help="replay a simulated walk against the real sub-pixel camera code "
                         "(pure math, touches no game state, needs no input)")
    ap.add_argument("--set-speed", type=float, default=None,
                    help="set movementSpeed (want 2 * fps: 110 at 55 fps, 120 at 60 fps)")
    ap.add_argument("--seconds", type=float, default=None,
                    help="seconds per measurement (default 6, and 90 as the walking wait "
                         "budget for --walk-diag)")
    args = ap.parse_args()
    secs = args.seconds if args.seconds is not None else 6.0

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
            # Pure closed loop: start from stock (no change), then correct using
            # the MEASURED step. No feed-forward guess, so there is no assumption
            # about tile size, frame time, or the shape of the formula.
            #
            # Runs INDEFINITELY by default (--iters N to bound it) and keeps
            # refining after it reaches target, so the value can be watched for
            # stability before being locked in as a constant. Ctrl+C prints the
            # lockable durations.
            print(_eval(b, WALK_FIX.replace(
                "__FACTORS__", _lua_factors({"normal": 1.0, "fast": 1.0}))))
            print(_eval(b, WALK_REPORT))
            print()
            print("Calibrating indefinitely - KEEP WALKING. Press Ctrl+C to stop")
            print(f"and print the durations to lock in. Each pass samples {secs:g}s.")
            print()
            print("  pass  frames  walk%     step px/frame    normal_x     fast_x   action")
            hist = []
            try:
                for it in range(1, (args.iters if args.iters > 0 else 10 ** 9) + 1):
                    print(_eval(b, RATE_INSTALL))
                    time.sleep(secs)
                    raw = _eval(b, RATE_COLLECT).strip()
                    try:
                        parts = raw.split("\t")
                        n = int(parts[0])          # total frames sampled
                        runN = int(parts[1])       # longest uninterrupted walk
                        runSum = float(parts[2])
                    except Exception:
                        print(f"  {it:<6} unparsable: {raw!r}")
                        continue
                    if runN < 60:
                        print(f"  {it:<6} no continuous walk "
                              f"({runN} frames in the longest stretch) - keep walking")
                        continue
                    rate = runSum / runN
                    ratio = runN / n
                    hist.append((it, n, ratio, rate))
                    cur = _parse_factors(_eval(b, WALK_FACTORS))
                    if abs(rate - 1.0) <= 0.005:
                        action = "on target"
                    else:
                        # DAMPED. Applying the full measured error makes the loop
                        # chase quantisation noise and wander instead of settling:
                        # half the error in log space converges smoothly.
                        corr = rate ** 0.5
                        _eval(b, WALK_ADJUST.replace("__RATE__", repr(corr)))
                        cur = _parse_factors(_eval(b, WALK_FACTORS))
                        action = f"adjust x{corr:.5f}"
                    print(f"  {it:<6}{n:<8}{ratio * 100:>5.1f}   {rate:>11.5f}   "
                          f"{cur.get('normal', 0):>9.6f}  {cur.get('fast', 0):>9.6f}   {action}")
            except KeyboardInterrupt:
                print()
                print("---- stopped by user ----")

            cur = _parse_factors(_eval(b, WALK_FACTORS))
            print()
            print(_eval(b, WALK_REPORT))
            if cur and hist:
                nmms = 280.0 * cur.get("normal", 1.0)
                fmms = 133.0 * cur.get("fast", 1.0)
                smms = 400.0 * cur.get("slow", 1.0)
                rates = [v for (_, _, r, v) in hist if r >= 0.7]
                print()
                print(f"passes run: {len(hist)}   usable readings: {len(rates)}")
                if rates:
                    best = min(rates, key=lambda x: abs(x - 1.0))
                    print(f"  last step = {rates[-1]:.5f}    closest to 1.0 = {best:.5f}")
                print()
                print("LOCKED FOR THIS FRAME RATE")
                print(f"  normal = {nmms:.3f} ms     fast = {fmms:.3f} ms     slow = {smms:.3f} ms")
                print()
                print("Lock that in as a fixed constant (no frame-time measurement):")
                print(f"  python tools/scroll_fix.py --walk-const {nmms:.3f} {fmms:.3f}")
        elif args.walk_const is not None:
            # Absolute durations, no measurement - reproducible and lockable.
            nm, fm = args.walk_const
            factors = {"normal": nm / 280.0, "fast": fm / 133.0}
            print(f"applying fixed durations: normal {nm:.3f} ms, fast {fm:.3f} ms")
            print(f"  (slow left at 400 ms; multipliers normal x{factors['normal']:.6f} "
                  f"fast x{factors['fast']:.6f})")
            print(_eval(b, WALK_FIX.replace("__FACTORS__", _lua_factors(factors))))
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
        elif args.walk_report:
            print(_eval(b, WALK_REPORT))
        elif args.walk_diag is not None:
            diag(b, args.walk_diag)
        elif args.walk_subpixel is not None:
            if args.walk_subpixel == "off":
                print(_eval(b, SUBPIXEL_REMOVE))
            else:
                if args.walk_subpixel in ("on", "grid"):
                    grid = args.subpixel_grid if args.walk_subpixel == "grid" else 0.0
                    print(_eval(b, SUBPIXEL_INSTALL.replace("__GRID__", repr(float(grid)))))
                    print()
                    print("NOTE: this is live from the first frame, but it only has something to")
                    print("do while you are MOVING - the camera only exists relative to the")
                    print("player. Walk a few steps, then:")
                    print("    python tools/scroll_fix.py --walk-subpixel report")
                    print()
                print(_eval(b, SUBPIXEL_REPORT))
        elif args.selftest_subpixel:
            selftest_subpixel(b)
        elif args.off:
            print(_eval(b, REMOVE))
        elif args.on:
            print(_eval(b, INSTALL))
            print(_eval(b, STATUS))
        elif args.ab:
            measure(b, secs, "BASELINE (game as shipped)")
            print()
            print(_eval(b, INSTALL))
            measure(b, secs, "FIXED (constant step)")
            print()
            print("The fix is left installed. Run --off to restore, or close the game.")
        elif args.measure:
            print(_eval(b, STATUS))
            measure(b, secs, "current")
        else:
            print(__doc__.strip())
            print()
            print("EVERYDAY COMMANDS")
            print("  --walk-fix      apply the fix (run once per game launch, while in the overworld)")
            print("  --walk-report   show whether it is active, plus call and re-apply counts")
            print("  --off           undo it immediately (also undone by closing the game)")
            print()
            print("CALIBRATION")
            print("  --walk-calibrate --seconds 8   closed loop: corrects until the measured")
            print("                                 step is 1 px/frame, printing every pass and")
            print("                                 the durations to lock in")
            print("  --walk-const NORMAL FAST       apply fixed durations (no measurement)")
            print()
            print("DIAGNOSTICS (none of these wait on you)")
            print("  --walk-diag on|report|off")
            print("                              records camera step, sprite sub-pixel phase")
            print("                              and frame time per frame while you play;")
            print("                              'report' analyses whatever it captured")
            print("  --walk-subpixel on|grid|off|report")
            print("                              fractional camera: 'on' scrolls between whole")
            print("                              content pixels; 'grid' snaps to whole device")
            print("                              pixels instead (1/3 content px here), which stays")
            print("                              crisp under nearest filtering")
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
