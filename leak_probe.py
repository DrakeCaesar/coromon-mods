#!/usr/bin/env python3
"""leak_probe.py - watch the running game for things that pile up.

WHAT THIS IS FOR. "It gets laggy after a few iterations" is a symptom with two very different
shapes: the per-frame cost is constant and the game is simply heavy, or something is being KEPT and
the cost grows. Only measurement tells them apart, and the numbers that separate them are cheap to
read: the live Lua heap, how many display objects exist, how many parked effects the save table
holds, and the counters the reload's own instrumentation keeps.

READ-ONLY, DELIBERATELY. It evals one chunk that only reads: no listeners, no timers, no writes, no
state. A probe that installed a listener to measure frames would be a leak of its own, in a tool
whose whole purpose is finding leaks. The one side effect is `collectgarbage('collect')` before each
sample, which is what makes the heap number the LIVE SET rather than garbage waiting to be swept -
and which is why a number that keeps climbing is worth reading at all.

Each sample is one eval, and evals are not free: the tool's own hook has a state-capture to pay for.
That used to be much worse than it is - an eval restarted the capture, so anything polling often kept
the hook on its expensive path permanently (see the eval export in coromon_lua.py). If samples here
ever get expensive again, that is the first place to look, not the game.

USAGE. Run it beside the driver and roll a few times; every sample prints one row, and the columns
that grow are the answer. Ctrl+C to stop.

    python leak_probe.py              # 3 s between samples, until interrupted
    python leak_probe.py 1 20         # 1 s between samples, 20 of them

The absolute values are not the point - only which column climbs.
"""

import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from coromon_lua import MINIMAL_HOOKS, Bridge  # noqa: E402

PROCESS = "coromon.exe"
SAMPLE_S = 3.0
COUNT = 0          # 0 means until interrupted


# Kept as one chunk so every sample is a single round trip, and short enough to read against the
# game's own code. Everything is pcall-guarded: a probe that throws tells us nothing about the game.
SAMPLE_CODE = r"""
local out = {}
local function add(k, v) out[#out + 1] = k .. '=' .. tostring(v) end

-- 1. THE LIVE LUA HEAP. Collected first, so this is what is being KEPT and not what has not been
-- swept yet. This is the single best answer to "is anything piling up".
pcall(function() collectgarbage('collect') end)
add('lua_kb', string.format('%.0f', collectgarbage('count')))

-- 2. FRAME RATE, if the wrapped display exposes it at all (Solar2D's own read-only property).
local fps
pcall(function()
  local d = _G.display
  if type(d) ~= 'table' then return end
  if type(d.fps) == 'number' then fps = d.fps end
end)
add('fps', fps and string.format('%.1f', fps) or 'n/a')

-- 3. LIVE DISPLAY OBJECTS, walked from the group table the game hangs everything off. Visited by
-- identity so a cycle in the tree cannot hang this, and capped so a large tree cannot either.
--
-- BOTH KINDS OF CONTAINER. A display group answers `numChildren` and its children are `[i]`, but
-- `displayGroups` itself is a plain Lua table of groups - reading numChildren off it answers nil, and
-- the first version of this quietly counted 1 object and looked like it had worked. So a container
-- with no numChildren is walked as a table of values instead.
local seen, n, roots = {}, 0, 0
local function tally(o, depth)
  if n > 40000 or depth > 12 then return end
  local t = type(o)
  if t ~= 'table' and t ~= 'userdata' then return end
  if seen[o] then return end
  seen[o] = true
  n = n + 1
  local okk, kids = pcall(function() return o.numChildren end)
  if okk and type(kids) == 'number' and kids > 0 then
    for i = 1, kids do
      local okc, c = pcall(function() return o[i] end)
      if okc then tally(c, depth + 1) end
    end
  elseif t == 'table' then
    for _, c in pairs(o) do tally(c, depth + 1) end
  end
end
local stage
pcall(function() stage = _G.display.getCurrentStage() end)
tally(_G.displayGroups, 0)
if stage then tally(stage, 0) end
local grefs = 0
if type(_G.displayGroups) == 'table' then for _ in pairs(_G.displayGroups) do grefs = grefs + 1 end end
add('objs', n)
add('groups', grefs)

-- 4. THE SAVE TABLE the autoroll feature reads every frame: how many parked effects it holds, and
-- how many of them still have a Coromon attached. If this climbs per roll, the loop's per-frame scan
-- climbs with it - every entry is a pcall into getMonster() on every frame of the walk.
--
-- The walk itself is core's saveSettingsTable, copied rather than called: this is a standalone chunk
-- and cannot see the composition's locals. A copy that drifts only makes this diagnostic stale; if
-- core's version ever changes, this one is a probe, not a dependency.
local function settingsTable()
  for _, mod in pairs(package.loaded) do
    if type(mod) == 'table' then
      for _, fn in pairs(mod) do
        if type(fn) == 'function' then
          for i = 1, 80 do
            local nm, v = debug.getupvalue(fn, i)
            if not nm then break end
            if type(v) == 'table' and v.SAVEABLE_BATTLE_EFFECTS ~= nil and v.VISITED_MAPS ~= nil then
              return v
            end
          end
        end
      end
    end
  end
end
local s = settingsTable()
if type(s) == 'table' then
  local list = s.SAVEABLE_BATTLE_EFFECTS
  local size, live, steps = 0, 0, 0
  if type(list) == 'table' then
    for _, e in pairs(list) do
      size = size + 1
      if type(e) == 'table' then
        if type(e.getTargetPlayerSteps) == 'function' then steps = steps + 1 end
        local okm, m = pcall(function() return e:getMonster() end)
        if okm and type(m) == 'table' then live = live + 1 end
      end
    end
  end
  add('effects', size)
  add('with_mon', live)
  add('with_steps', steps)
else
  add('effects', 'no save')
end

-- 5. THE RELOAD'S OWN MACHINERY. It runs one repeating 16 ms timer per reload while it waits for the
-- world to settle, and its log writes a file per call, so both are worth watching across rolls.
local r = _G.__hud and _G.__hud.feats and _G.__hud.feats.reload
if type(r) == 'table' then
  add('rl_done', r.done or 0)
  add('rl_busy', tostring(r.busy))
  add('rl_step', tostring(r.step or '-'))
else
  add('rl_step', 'not installed')
end

-- `_G.__qrLog` is reset per reload and only read while one is running: a number here that outlives a
-- reload means something is still appending to it after its reload finished.
add('qrlog', type(_G.__qrLog) == 'table' and #_G.__qrLog or 'nil')

-- Buttons built, summed over its labels. A menu built and never destroyed shows up here first.
local built = _G.__qrBuilt
local total = 0
if type(built) == 'table' then
  for _, v in pairs(built) do if tonumber(v) then total = total + v end end
end
add('buttons', total)

-- Input registrations the census is tracking, live/total for touch, hover and overlay.
local regn = _G.__qrRegN
if type(regn) == 'table' then
  add('regs', string.format('%s/%s/%s', tostring(regn.touch), tostring(regn.hover),
    tostring(regn.overlay)))
else
  add('regs', 'nil')
end

-- 6. WHAT OUR OWN LOOP IS DOING, so a column that moves can be read next to it.
local a = _G.__hud and _G.__hud.feats and _G.__hud.feats.autoroll
if type(a) == 'table' then
  add('autoroll', string.format('%s/left=%s/walk=%s', tostring(a.state), tostring(a.left),
    tostring(a.walk)))
else
  add('autoroll', 'not installed')
end

return table.concat(out, '|')
"""


# Columns worth a delta against the first sample: these are the ones that must NOT climb.
WATCH = ("lua_kb", "objs", "effects", "with_mon", "with_steps", "buttons")


def sample(b):
    out = (b.eval(SAMPLE_CODE, timeout=30.0) or {}).get("out") or ""
    if out == "":
        return None
    fields = {}
    for part in out.split("|"):
        key, _, value = part.partition("=")
        if key:
            fields[key] = value
    return fields


def main():
    every = SAMPLE_S
    count = COUNT
    if len(sys.argv) > 1:
        every = float(sys.argv[1])
    if len(sys.argv) > 2:
        count = int(sys.argv[2])

    b = Bridge(PROCESS, hooks=MINIMAL_HOOKS)
    try:
        for _ in range(150):
            if b.status() and b.status().get("state"):
                break
            time.sleep(0.2)
        if not (b.status() and b.status().get("state")):
            print("attached, but no lua_State captured yet - is the game past its loading screen?")
            return 2

        print("reading only: no listeners, no timers, no writes. Ctrl+C to stop.")
        print("%-8s %-8s %-7s %-9s %-9s %-8s %-9s %s" % (
            "t", "lua_kb", "d_kb", "objs", "effects", "buttons", "fps", "rest"))
        first, t0, i = None, time.monotonic(), 0
        while True:
            st = sample(b)
            i += 1
            if st is None:
                print("no answer from the game (closed?)")
                return 2
            if first is None:
                first = st

            def delta(key):
                try:
                    return "%+d" % (int(float(st[key])) - int(float(first[key])))
                except (KeyError, ValueError):
                    return "-"

            rest = " ".join("%s=%s" % (k, st[k]) for k in
                            ("with_mon", "with_steps", "rl_done", "rl_busy", "qrlog", "regs",
                             "autoroll") if k in st)
            print("%-8.1f %-8s %-7s %-9s %-9s %-8s %-9s %s" % (
                time.monotonic() - t0, st.get("lua_kb", "?"), delta("lua_kb"), st.get("objs", "?"),
                st.get("effects", "?"), st.get("buttons", "?"), st.get("fps", "?"), rest))
            if count and i >= count:
                return 0
            time.sleep(every)
    except KeyboardInterrupt:
        print("stopped.")
        return 0
    finally:
        b.detach()


if __name__ == "__main__":
    sys.exit(main())
