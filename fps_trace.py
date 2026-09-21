#!/usr/bin/env python3
"""fps_trace.py - sample how long the game takes to give Lua a turn, fast enough to see hitches.

WHY THIS EXISTS. "Frame times are horrible and it dips at stable intervals" is a claim about a
PERIOD, and a period names the culprit: our features run off a shared 200 ms tick, steptimer retries
every 800 ms and rescans every 10 s, the save-table hunt looks once a second until it finds a table
and every 12 s after that, and the reload's own wait is a 16 ms poll. Different numbers, so measuring
the interval is most of the diagnosis.

WHAT IT ACTUALLY MEASURES, AND WHY NOT THE FRAME RATE. `display.fps` is NOT a measurement - checked
on this build, it returns 165.0 constantly, which is the configured refresh rate, not the rate being
achieved. `display.actualFps` does not exist here. So the only clock readable from outside is this:
an eval runs on the game's Lua thread, so the ROUND TRIP takes as long as the game takes to reach a
point where it can run Lua. A frame that stalls for 250 ms makes the round trip 250 ms. That is the
signal - a proxy for stalls, not a frame rate, and it is labelled as such.

The floor is Frida's message hop plus a frame, so read the SPIKES and their spacing, not the floor.

IT MEASURES BY EVALUATING, which is not free: every sample runs a line of Lua on the game thread. At
25 ms that is 40 a second and it will perturb a frame here and there. If a reading looks marginal,
re-run with a bigger interval.

    python fps_trace.py                # 6 s at 25 ms
    python fps_trace.py 10 50          # 10 s at 50 ms

Run it once with overlays.py attached and once with it stopped. That comparison is the point.
"""

import os
import statistics
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from coromon_lua import MINIMAL_HOOKS, Bridge  # noqa: E402

PROCESS = "coromon.exe"
SECONDS = 6.0
INTERVAL_MS = 25

# The smallest chunk that still proves the Lua thread ran and answered.
READ = "return 1"


def bar(v, peak):
    """A rough sparkline column so the shape is visible in a terminal."""
    if v <= 0:
        return " "
    step = max(1, int(peak / 8))
    return ".:-=+*#@"[min(7, int(v / step))]


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else SECONDS
    interval = (int(sys.argv[2]) if len(sys.argv) > 2 else INTERVAL_MS) / 1000.0

    b = Bridge(PROCESS, hooks=MINIMAL_HOOKS)
    try:
        for _ in range(150):
            if b.status() and b.status().get("state"):
                break
        if not (b.status() and b.status().get("state")):
            print("no lua_State captured yet - is the game past its loading screen?")
            return 2

        samples = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            t = time.monotonic()
            b.eval(READ, timeout=10.0)
            dt = (time.monotonic() - t) * 1000.0
            samples.append((time.monotonic() - t0, dt))
            time.sleep(max(0.0, interval - dt / 1000.0))
    finally:
        b.detach()

    vals = [v for _, v in samples if v > 0]
    if not vals:
        print("no samples")
        return 2
    med = statistics.median(vals)
    ordered = sorted(vals)
    p90 = ordered[int(len(ordered) * 0.9)]
    print("samples=%d  interval=%.0f ms  latency ms: median=%.1f  p90=%.1f  max=%.1f"
          % (len(vals), interval * 1000, med, p90, max(vals)))
    print("(floor is Frida's hop plus a frame - read the spikes, not the floor)")

    # the shape
    peak = max(vals)
    print("shape   : " + "".join(bar(v, peak) for _, v in samples))

    spikes = [(t, v) for t, v in samples if v > med * 3 and v > 20]
    print("spikes over 3x median and 20 ms: %d" % len(spikes))
    if len(spikes) >= 2:
        gaps = [round((b2[0] - a2[0]) * 1000) for a2, b2 in zip(spikes, spikes[1:])]
        print("gaps between spikes (ms): %s" % gaps[:24])
        if len(gaps) >= 3:
            m = statistics.median(gaps)
            print("median gap              : %d ms  -> %.2f Hz" % (m, 1000.0 / max(1.0, m)))
            print("our periodic work is 200 ms (tick), 800 ms (steptimer retry), 1000 ms (save "
                  "hunt), 10 s (steptimer rescan), 12 s (save re-look)")
    elif spikes:
        print("one spike: %.0f ms at t=%.2f" % (spikes[0][1], spikes[0][0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
