#!/usr/bin/env python3
"""
perf_probe.py - measure Coromon's frame pacing, with and without the tool's
Frida hooks installed, so tool overhead can be told apart from the game itself.

It installs a temporary `enterFrame` listener inside the game, records real
per-frame times, then repeats the measurement with a minimal hook set and with
the full hook set, and prints a comparison.

Usage (game must be running):
    python coromon-tools/perf_probe.py                 # 6s per mode
    python coromon-tools/perf_probe.py --seconds 10
"""
import argparse
import re
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from coromon_lua import Bridge, DEFAULT_HOOKS, MINIMAL_HOOKS  # noqa: E402

INSTALL = r"""
local p = { samples = {}, last = nil }
_G.__perf = p
local function tick()
  local t = system.getTimer()
  local last = p.last
  if last then
    local d = t - last
    if d > 0 and #p.samples < 20000 then p.samples[#p.samples + 1] = d end
  end
  p.last = t
end
p.listener = tick
Runtime:addEventListener('enterFrame', tick)

local function safe(f)
  local ok, v = pcall(f)
  if ok and v ~= nil then return tostring(v) end
  return '?'
end
p.meta = string.format('targetFps=%s actualFps=%s timeScale=%s gcKB=%s texKB=%s kids=%s',
  safe(function() return display.fps end),
  safe(function() return display.actualFps end),
  safe(function() return display.timeScale end),
  safe(function() return math.floor(collectgarbage('count')) end),
  safe(function() return system.getInfo('textureMemoryUsed') end),
  safe(function() return display.getCurrentStage().numChildren end))
return p.meta
"""

COLLECT = r"""
local p = _G.__perf
if not p then return '!none!' end
Runtime:removeEventListener('enterFrame', p.listener)
local n = #p.samples
if n == 0 then return 'meta\t' .. tostring(p.meta) .. '\tn=0' end
local sum, mx, mn = 0, 0, 1e9
local over25, over50 = 0, 0
for i = 1, n do
  local d = p.samples[i]
  sum = sum + d
  if d > mx then mx = d end
  if d < mn then mn = d end
  if d > 25 then over25 = over25 + 1 end
  if d > 50 then over50 = over50 + 1 end
end
_G.__perf = nil
return string.format('meta\t%s\tn\t%d\tmean\t%.2f\tmin\t%.0f\tmax\t%.0f\tover25ms\t%d\tover50ms\t%d',
  tostring(p.meta), n, sum / n, mn, mx, over25, over50)
"""


def measure(hooks, label, seconds):
    b = Bridge("coromon.exe", hooks=hooks)
    for _ in range(150):
        if b.status().get("state"):
            break
        time.sleep(0.2)
    st = b.status()
    if not st.get("state"):
        print(f"{label}: could not capture lua_State")
        b.detach()
        return None

    r = b.eval(INSTALL, timeout=20.0)
    if not r.get("out"):
        print(f"{label}: install failed: {r.get('err')}")
        b.detach()
        return None
    meta = r["out"]

    h0 = b.status().get("hooks")
    t0 = time.time()
    time.sleep(seconds)
    h1 = b.status().get("hooks")
    elapsed = time.time() - t0

    res = b.eval(COLLECT, timeout=20.0)
    b.detach()

    out = res.get("out") or ""
    data = {}
    parts = out.split("\t")
    for i in range(0, len(parts) - 1, 2):
        data[parts[i]] = parts[i + 1]

    rate = (h1 - h0) / elapsed if h0 is not None and h1 is not None else 0
    data["hookedCallsPerSec"] = f"{rate:.0f}"
    data["meta"] = meta
    data["label"] = label
    return data


def show(d, seconds):
    if not d:
        return
    n = int(d.get("n", 0))
    print(f"\n== {d['label']}")
    print(f"   {d['meta']}")
    print(f"   hooked lua_* calls/sec : {d.get('hookedCallsPerSec')}")
    if n == 0:
        print("   no frames observed (game idle or paused?)")
        return
    mean = float(d.get("mean", 0))
    print(f"   frames                 : {n} over {seconds}s  (~{n / seconds:.1f} fps)")
    print(f"   frame time mean/min/max: {mean:.2f} / {float(d['min']):.0f} / {float(d['max']):.0f} ms")
    print(f"   frames > 25ms          : {d.get('over25ms')} ({100 * int(d['over25ms']) / n:.1f}%)")
    print(f"   frames > 50ms          : {d.get('over50ms')} ({100 * int(d['over50ms']) / n:.1f}%)")


CAMERA_INSTALL = r"""
local stage = display.getCurrentStage()
local list = {}
local player = nil
pcall(function()
  local sh = _G.spawnableHelper
  if sh and sh.getPlayerSpawnable then
    local ok, p = pcall(function() return sh.getPlayerSpawnable(sh) end)
    if ok and p then player = p end
  end
end)

-- the camera/world group has to be an ancestor of the player sprite, so walk
-- up from the player instead of guessing at stage children
local chain = {}
if player then
  local o = player
  for _ = 1, 8 do
    local p = nil
    pcall(function() p = o.parent end)
    if not p then break end
    chain[#chain + 1] = p
    o = p
  end
end
for i = 1, #chain do list[#list + 1] = chain[i] end

local s = { list = list, player = player, rows = {}, frames = 0 }
_G.__cam = s
local function tick()
  s.frames = s.frames + 1
  for i = 1, #list do
    local o = list[i]
    local row = s.rows[i]
    if not row then row = {}; s.rows[i] = row end
    local ok, x, y = pcall(function() return o.x, o.y end)
    if ok then row[#row + 1] = { x, y } end
  end
  if player then
    s.prow = s.prow or {}
    local ok, x, y = pcall(function() return player.x, player.y end)
    if ok then s.prow[#s.prow + 1] = { x, y } end
  end
end
s.listener = tick
Runtime:addEventListener('enterFrame', tick)
return 'player=' .. tostring(player) .. '  ancestors=' .. #chain
"""

CAMERA_COLLECT = r"""
local s = _G.__cam
if not s then return '!none!' end
Runtime:removeEventListener('enterFrame', s.listener)
_G.__cam = nil
local out, report = {}, {}
if s.prow and #s.prow > 1 then
  out[#out + 1] = string.format('player moved (%d,%d) over %d frames',
    s.prow[#s.prow][1] - s.prow[1][1], s.prow[#s.prow][2] - s.prow[1][2], #s.prow)
else
  out[#out + 1] = 'player: no samples'
end
for i = 1, #s.rows do
  local row = s.rows[i]
  if row then
    local moved, seq = 0, {}
    for k = 2, #row do
      local dx = row[k][1] - row[k - 1][1]
      local dy = row[k][2] - row[k - 1][2]
      if dx ~= 0 or dy ~= 0 then moved = moved + 1 end
      if #seq < 40 then seq[#seq + 1] = string.format('%d,%d', dx, dy) end
    end
    if moved > 0 then
      report[#report + 1] = {addr = tostring(s.list[i]), moved = moved, frames = #row, seq = seq,
                             sx = row[#row][1] - row[1][1], sy = row[#row][2] - row[1][2]}
    end
  end
end
table.sort(report, function(a, b) return a.moved > b.moved end)
out[#out + 1] = string.format('frames=%d  moving groups=%d', s.frames, #report)
for r = 1, math.min(#report, 4) do
  local e = report[r]
  out[#out + 1] = string.format('MOVED %s  moved=%d/%d frames  span=(%d,%d)',
    e.addr, e.moved, e.frames, e.sx, e.sy)
  out[#out + 1] = '   per-frame (dx,dy): ' .. table.concat(e.seq, ' ')
end
return table.concat(out, '\n')
"""


CAMERA_STATUS = r"""
local s = _G.__cam
if not s then return '!none!' end
local prow = s.prow
if not prow or #prow < 2 then return string.format('frames=%d moved=0', s.frames) end
local dx = prow[#prow][1] - prow[1][1]
local dy = prow[#prow][2] - prow[1][2]
return string.format('frames=%d moved=%d', s.frames, math.abs(dx) + math.abs(dy))
"""

CAMERA_RESET = r"""
local s = _G.__cam
if not s then return '!none!' end
s.rows, s.prow, s.frames = {}, {}, 0
return 'reset'
"""


def camera(seconds, wait=60.0):
    b = Bridge("coromon.exe", hooks=MINIMAL_HOOKS)
    for _ in range(150):
        if b.status().get("state"):
            break
        time.sleep(0.2)
    r = b.eval(CAMERA_INSTALL, timeout=20.0)
    print("install:", r.get("out") or r.get("err"))
    print(f"waiting for you to walk in-game (up to {wait:g}s) ...")
    deadline = time.time() + wait
    while time.time() < deadline:
        st = b.eval(CAMERA_STATUS, timeout=10.0)
        out = st.get("out") or ""
        m = re.search(r"moved=(\d+)", out)
        if m and int(m.group(1)) > 4:
            print(f"movement detected ({m.group(1)} px) - sampling {seconds:g}s ...")
            break
        time.sleep(0.4)
    else:
        print("no movement detected - walk around and re-run")
        b.eval(CAMERA_COLLECT, timeout=20.0)
        b.detach()
        return
    b.eval(CAMERA_RESET, timeout=10.0)
    time.sleep(seconds)
    res = b.eval(CAMERA_COLLECT, timeout=20.0)
    print(res.get("out") or res.get("err"))
    b.detach()


FINDER_INSTALL = r"""
local stage = display.getCurrentStage()
local objs, paths = {}, {}
local function walk(o, path, d)
  if not o or d > 6 or #objs > 2000 then return end
  local n = 0
  pcall(function() n = o.numChildren or #o end)
  if n and n > 0 then
    objs[#objs + 1] = o
    paths[#paths + 1] = path
  end
  for i = 1, math.min(n, 60) do
    local c = nil
    pcall(function() c = o[i] end)
    if c then walk(c, path .. '[' .. i .. ']', d + 1) end
  end
end
walk(stage, 'stage', 0)
local xs, ys = {}, {}
for i = 1, #objs do xs[i] = {}; ys[i] = {} end
local s = { objs = objs, paths = paths, xs = xs, ys = ys, times = {}, frames = 0 }
_G.__find = s
local function getxy(o) return o.x, o.y end
-- every frame, so a position that only jumps every N frames is caught
local function sample()
  s.frames = s.frames + 1
  local f = s.frames
  if f > 4000 then return false end
  s.times[f] = system.getTimer()
  for i = 1, #objs do
    local ok, x, y = pcall(getxy, objs[i])
    if ok and x then xs[i][f] = x; ys[i][f] = y end
  end
  return false
end
s.listener = sample
Runtime:addEventListener('enterFrame', sample)
return 'scanned ' .. #objs .. ' groups/objects (depth<=6), sampling every frame'
"""

FINDER_COLLECT = r"""
local s = _G.__find
if not s then return '!none!' end
if s.listener then Runtime:removeEventListener('enterFrame', s.listener); s.listener = nil end
_G.__find = nil
local out = {}
-- frame pacing over the same window, so a position stall can be tied to a frame
local n, sum, mx = 0, 0, 0
for k = 2, s.frames do
  local d = s.times[k] - s.times[k - 1]
  if d and d > 0 then
    n = n + 1; sum = sum + d
    if d > mx then mx = d end
  end
end
out[#out + 1] = string.format('frames=%d  mean=%.2fms  max=%dms', s.frames, n > 0 and sum / n or 0, mx)

local report = {}
for i = 1, #s.objs do
  local x = s.xs[i]
  local counts, run, longest, seq = {}, 0, 0, {}
  local prev, moved = nil, 0
  for f = 1, s.frames do
    local v = x[f]
    if v then
      if prev then
        local d = v - prev
        local ad = d < 0 and -d or d
        counts[ad] = (counts[ad] or 0) + 1
        if ad == 0 then
          run = run + 1
          if run > longest then longest = run end
        else
          moved = moved + 1; run = 0
        end
        if #seq < 80 then seq[#seq + 1] = ad end
      end
      prev = v
    end
  end
  if moved > 0 then
    local keys = {}
    for d in pairs(counts) do keys[#keys + 1] = d end
    table.sort(keys)
    report[#report + 1] = { path = s.paths[i], moved = moved, longest = longest,
                           keys = keys, counts = counts, seq = seq }
  end
end
table.sort(report, function(a, b) return a.moved > b.moved end)
out[#out + 1] = string.format('objects that moved=%d', #report)
for r = 1, math.min(#report, 6) do
  local e = report[r]
  -- bucket by whole pixel, but keep the exact mean so sub-pixel smoothness is visible
  local buckets, tot = {}, 0
  for _, d in ipairs(e.keys) do
    local bk = math.floor(d)
    buckets[bk] = (buckets[bk] or 0) + e.counts[d]
    tot = tot + d * e.counts[d]
  end
  local bks = {}
  for k in pairs(buckets) do bks[#bks + 1] = k end
  table.sort(bks)
  local parts = {}
  for _, k in ipairs(bks) do parts[#parts + 1] = string.format('%dpx=%d', k, buckets[k]) end
  local n = 0
  for _, d in ipairs(e.keys) do n = n + e.counts[d] end
  out[#out + 1] = string.format('MOVER %s   moved on %d of %d frames   longest run of 0px = %d',
    e.path, e.moved, n, e.longest)
  out[#out + 1] = string.format('   mean |dx| = %.4f px/frame   integer buckets: %s',
    n > 0 and tot / n or 0, table.concat(parts, '  '))
  out[#out + 1] = '   first 80 |dx|: ' .. table.concat(e.seq, ' ')
end
return table.concat(out, '\n')
"""


def finder(seconds):
    b = Bridge("coromon.exe", hooks=MINIMAL_HOOKS)
    for _ in range(150):
        if b.status().get("state"):
            break
        time.sleep(0.2)
    r = b.eval(FINDER_INSTALL, timeout=30.0)
    print("install:", r.get("out") or r.get("err"))
    print(f"walk in-game for {seconds:g}s (keep moving the whole time) ...")
    time.sleep(seconds)
    res = b.eval(FINDER_COLLECT, timeout=30.0)
    print(res.get("out") or res.get("err"))
    b.detach()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="coromon.exe")
    ap.add_argument("--seconds", type=float, default=6.0, help="seconds per mode")
    ap.add_argument("--camera", action="store_true",
                    help="sample the stage/background position every frame instead")
    ap.add_argument("--finder", action="store_true",
                    help="find which display object scrolls the world")
    ap.add_argument("--track", action="store_true",
                    help="deprecated: --finder now samples every frame")
    args = ap.parse_args()

    try:
        import frida  # noqa: F401
    except ImportError:
        print("frida is not installed")
        return 1

    if args.finder:
        finder(args.seconds)
        return 0

    if args.camera:
        camera(args.seconds)
        return 0

    print("Measuring the game with a minimal hook set, then with the full set.")
    print("Move around in-game while this runs so the world is actually scrolling.")

    minimal = measure(MINIMAL_HOOKS, "minimal hooks (lua_gettop only)", args.seconds)
    full = measure(DEFAULT_HOOKS, "full hooks (8 hot lua_* functions)", args.seconds)

    show(minimal, args.seconds)
    show(full, args.seconds)

    if minimal and full and minimal.get("n") and full.get("n"):
        dm = float(minimal["mean"])
        df = float(full["mean"])
        delta = df - dm
        print("\n== verdict")
        print(f"   mean frame time {dm:.2f} ms (minimal) vs {df:.2f} ms (full) -> "
              f"{delta:+.2f} ms from the extra hooks")
        if abs(delta) < 1.0:
            print("   the hooks are not what is making it stutter")
        else:
            print("   the extra hooks ARE costing frame time - run the tool with --no-overlay "
                  "or leave it detached while playing")
        if dm > 20:
            print(f"   note: the game itself is already at ~{dm:.1f} ms/frame "
                  f"(~{1000 / dm:.0f} fps) with no tool overhead")
    return 0


if __name__ == "__main__":
    sys.exit(main())
