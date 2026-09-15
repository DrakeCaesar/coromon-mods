#!/usr/bin/env python3
"""
perf_probe.py - measure Coromon's frame pacing, with and without the tool's
Frida hooks installed, so tool overhead can be told apart from the game itself.

It installs a temporary `enterFrame` listener inside the game, records real
per-frame times, then repeats the measurement with a minimal hook set and with
the full hook set, and prints a comparison.

Usage (game must be running):
    python tools/perf_probe.py                 # 6s per mode
    python tools/perf_probe.py --seconds 10
"""
import argparse
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
p.meta = string.format('targetFps=%s timeScale=%s avgDelta=%.4f gcKB=%.0f texKB=%s kids=%d',
  tostring(display.fps), tostring(display.timeScale), display.getAverageDelta(),
  collectgarbage('count'), tostring(system.getInfo('textureMemoryUsed')),
  display.getCurrentStage().numChildren)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="coromon.exe")
    ap.add_argument("--seconds", type=float, default=6.0, help="seconds per mode")
    args = ap.parse_args()

    try:
        import frida  # noqa: F401
    except ImportError:
        print("frida is not installed")
        return 1

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
