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

Usage (game running, standing in the overworld):
    python tools/scroll_fix.py --status
    python tools/scroll_fix.py --ab                 # measure 6s, install, measure 6s
    python tools/scroll_fix.py --on                 # install only
    python tools/scroll_fix.py --off                # restore
    python tools/scroll_fix.py --measure --seconds 8
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

local st = { orig = {}, names = {}, calls = 0, rounded = 0, touched = {} }
_G.__snap = st

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
            ap.print_help()
    finally:
        b.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
