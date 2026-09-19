#!/usr/bin/env python3
"""
camera_probe.py - inspect Coromon's overworld camera (the hexagon scroll view).

Static analysis of resource.car says the camera is NOT attached to the player:

    RogueWorldInterface:focusActionTileRow(row, ?, instant)
        x, y, w, h = HexagonWorld:getCenterCameraPositionForActionTileRow(row)   -- math.floor
        HexagonWorld:transitionCameraToTile(x, y, w, h, instant and 0 or 250, inOutSine)

    HexagonWorld:transitionCameraToTile -> scrollView:scrollToPosition{x,y,time,easing}

    classes.modules.interface.scrollViewBuilder
        contentGroup.x = math.clamp(leftLimit - math.round(contentGroup.x), right, left)
        transition.to(contentGroup, {...}, {onBeforeSetProperty =
                                            updateUnroundedContentGroupLocationVariables})

So the rendered scroll offset is math.round()ed to whole pixels and moved by an eased
`transition.to`, re-targeted only when the player enters a new action-tile row.

`scrollViewBuilder` keeps `unroundedContentGroupX` / `unroundedContentGroupY` precisely
because the rendered value is rounded - those let us see the rounding at runtime:

    contentGroup.x      -> what is drawn        (expect whole numbers)
    unroundedContentGroupX -> the true offset   (expect fractional)

This tool only READS fields and upvalues. It never calls a wrapped game/display function
(doing that crashed the game once - see the notes in README.md).

Usage (game must be running):
    python coromon-tools/camera_probe.py --discover
    python coromon-tools/camera_probe.py --watch --seconds 10     # walk in-game meanwhile
"""
import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from coromon_lua import Bridge, MINIMAL_HOOKS  # noqa: E402

# Shared Lua prelude: locate the scroll view and reach into its closure upvalues.
PRELUDE = r"""
local function log(t, s) t[#t + 1] = s end

local function upval(fn, name)
  if type(fn) ~= 'function' then return nil end
  for i = 1, 100 do
    local n, v = debug.getupvalue(fn, i)
    if not n then return nil end
    if n == name then return v end
  end
end

-- HexagonWorld is a singleton: the module's get/isCreated closure holds `instance`.
-- Reading that upvalue beats calling get(), which would be a call into game code.
local function worldInstance()
  local m = package.loaded['classes.hexagon.HexagonWorld']
  if type(m) ~= 'table' then return nil, nil end
  local inst = upval(m.get, 'instance')
  if inst == nil then inst = upval(m.isCreated, 'instance') end
  return m, inst
end

-- first value closed over as `name` by any function stored in `tbl`
local function scanUpval(tbl, name)
  if type(tbl) ~= 'table' then return nil end
  for k, v in pairs(tbl) do
    if type(v) == 'function' then
      local x = upval(v, name)
      if x ~= nil then return x, k end
    end
  end
end

-- scrollView is an upvalue of the world instance's methods; contentGroup and
-- unroundedContentGroupX are upvalues of the scroll view builder's methods.
local function cameraParts()
  local _, inst = worldInstance()
  if type(inst) ~= 'table' then return nil end
  local sv = scanUpval(inst, 'scrollView')
  if type(sv) ~= 'table' then return nil end
  local fn = scanUpval(sv, 'updateUnroundedContentGroupLocationVariables')
  return sv, scanUpval(sv, 'contentGroup'), fn
end
"""

DISCOVER = PRELUDE + r"""
local out = {}
local m, inst = worldInstance()
log(out, 'HexagonWorld module = ' .. tostring(m))
log(out, 'live instance        = ' .. tostring(inst) .. ' (' .. type(inst) .. ')')
if type(inst) ~= 'table' then
  log(out, '=> the overworld does not exist right now (title screen / menu / battle).')
  log(out, '   enter the overworld and walk around, then run --discover again.')
  return table.concat(out, '\n')
end

local sv, cg, fnUnround = cameraParts()
log(out, 'scrollView           = ' .. tostring(sv))
log(out, 'contentGroup         = ' .. tostring(cg))
log(out, 'unrounded tracker fn = ' .. tostring(fnUnround))
if type(fnUnround) == 'function' then
  log(out, 'unroundedContentGroupX = ' .. tostring(upval(fnUnround, 'unroundedContentGroupX')))
  log(out, 'unroundedContentGroupY = ' .. tostring(upval(fnUnround, 'unroundedContentGroupY')))
end
if type(cg) == 'table' then
  log(out, string.format('contentGroup.x = %s   .y = %s', tostring(cg.x), tostring(cg.y)))
end
if type(sv) == 'table' then
  local n = 0
  for _ in pairs(sv) do n = n + 1 end
  log(out, 'scrollView own key count = ' .. n)
end
return table.concat(out, '\n')
"""

WATCH_INSTALL = PRELUDE + r"""
local sv, cg, fnUnround = cameraParts()
if type(sv) ~= 'table' then return '!overworld not found - are you in the overworld?!' end
if type(cg) ~= 'table' then return '!contentGroup not found!' end

local s = { frames = 0, times = {}, gx = {}, ux = {}, gy = {} }
_G.__cam = s
local function sample()
  s.frames = s.frames + 1
  local f = s.frames
  if f > 4000 then return false end
  s.times[f] = system.getTimer()
  s.gx[f] = cg.x
  s.gy[f] = cg.y
  -- re-read the upvalue every frame: it is a plain number slot that the
  -- scroll view updates via onBeforeSetProperty on every transition tick
  if type(fnUnround) == 'function' then s.ux[f] = upval(fnUnround, 'unroundedContentGroupX') end
  return false
end
s.listener = sample
Runtime:addEventListener('enterFrame', sample)
return string.format('watching contentGroup (x=%s y=%s) frame by frame',
  tostring(cg.x), tostring(cg.y))
"""

WATCH_COLLECT = r"""
local s = _G.__cam
if not s then return '!none!' end
if s.listener then Runtime:removeEventListener('enterFrame', s.listener); s.listener = nil end
_G.__cam = nil
_G.__camRefs = nil

local function isInt(v) return type(v) == 'number' and v == math.floor(v) end

local out = {}
local n, sum, mx = 0, 0, 0
for k = 2, s.frames do
  local d = s.times[k] - s.times[k - 1]
  if d and d > 0 then n = n + 1; sum = sum + d; if d > mx then mx = d end end
end
out[#out + 1] = string.format('frames=%d  mean=%.2fms  max=%dms', s.frames, n > 0 and sum / n or 0, mx)

local function describe(label, arr)
  local nonint, changed, counts, run, longest, seq = 0, 0, {}, 0, 0, {}
  local prev = nil
  local lo, hi = nil, nil
  for f = 1, s.frames do
    local v = arr[f]
    if type(v) == 'number' then
      if not isInt(v) then nonint = nonint + 1 end
      if lo == nil or v < lo then lo = v end
      if hi == nil or v > hi then hi = v end
      if prev then
        local d = v - prev
        local ad = d < 0 and -d or d
        if ad > 0 then
          changed = changed + 1
          run = 0
        else
          run = run + 1
          if run > longest then longest = run end
        end
        local bk = math.floor(ad + 0.5)
        counts[bk] = (counts[bk] or 0) + 1
        if #seq < 80 then seq[#seq + 1] = string.format('%.3f', d) end
      end
      prev = v
    end
  end
  local keys = {}
  for k in pairs(counts) do keys[#keys + 1] = k end
  table.sort(keys)
  local parts = {}
  for _, k in ipairs(keys) do parts[#parts + 1] = string.format('%dpx=%d', k, counts[k]) end
  out[#out + 1] = string.format('%s: non-integer frames=%d/%d   range=[%s .. %s]', label, nonint, s.frames, tostring(lo), tostring(hi))
  out[#out + 1] = string.format('   changed on %d frames, longest run of no change = %d', changed, longest)
  out[#out + 1] = '   |delta| buckets: ' .. table.concat(parts, '  ')
  out[#out + 1] = '   first 80 deltas: ' .. table.concat(seq, ' ')
end

describe('contentGroup.x  (what is drawn)', s.gx)
describe('contentGroup.y  (what is drawn)', s.gy)
if s.ux then
  describe('unrounded.x     (true offset)', s.ux)
else
  out[#out + 1] = 'unrounded.x: not reachable'
end

-- the decisive number: frames where the true offset moved but the drawn one did not
local swallowed, total = 0, 0
if s.ux then
  for k = 2, s.frames do
    local a, b = s.ux[k - 1], s.ux[k]
    local p, q = s.gx[k - 1], s.gx[k]
    if type(a) == 'number' and type(b) == 'number' and a ~= b then
      total = total + 1
      if p == q then swallowed = swallowed + 1 end
    end
  end
end
out[#out + 1] = string.format('DECISIVE: true offset moved on %d frames; the drawn position did NOT move on %d of them',
  total, swallowed)
return table.concat(out, '\n')
"""


def run(mode, seconds, verbose=False):
    b = Bridge("coromon.exe", hooks=MINIMAL_HOOKS)
    for _ in range(150):
        if b.status().get("state"):
            break
        time.sleep(0.2)
    if mode == "discover":
        r = b.eval(DISCOVER, timeout=30.0)
        print(r.get("out") or r.get("err"))
        b.detach()
        return
    r = b.eval(WATCH_INSTALL, timeout=30.0)
    print("install:", r.get("out") or r.get("err"))
    print(f"walk in-game for {seconds:g}s ...")
    time.sleep(seconds)
    res = b.eval(WATCH_COLLECT, timeout=60.0)
    print(res.get("out") or res.get("err"))
    b.detach()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="coromon.exe")
    ap.add_argument("--discover", action="store_true",
                    help="locate the HexagonWorld scroll view and its upvalues")
    ap.add_argument("--watch", action="store_true",
                    help="log contentGroup vs unroundedContentGroup every frame")
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    try:
        import frida  # noqa: F401
    except ImportError:
        print("frida is not installed")
        return 1

    if args.discover:
        run("discover", args.seconds)
        return 0
    if args.watch:
        run("watch", args.seconds)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
