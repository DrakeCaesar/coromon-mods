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

import ctypes
import ctypes.wintypes
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from coromon_lua import MINIMAL_HOOKS, Bridge  # noqa: E402
import pad_drive  # noqa: E402  (its Win32 helpers, not its CLI)

PROCESS = "coromon.exe"
SAMPLE_S = 3.0
COUNT = 0          # 0 means until interrupted


# MEMORY, READ WITHOUT ATTACHING. A leak of C-side objects - textures, audio, display objects - is
# invisible to every Lua number in the chunk below, and it is exactly the shape of "it gets laggy
# after many reloads": the Lua heap can sit still while the process grows. psutil is not installed
# here, so this is the Win32 call directly; PagefileUsage is the commit charge, which is the private
# footprint (the same number WMI reports as PageFileUsage).
class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def memory_mb(pid):
    """(working set, private commit) in MB, or None if the process cannot be opened."""
    if pid is None:
        return None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(pmc)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.WorkingSetSize / 1048576.0, pmc.PagefileUsage / 1048576.0
    finally:
        kernel32.CloseHandle(handle)


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

-- 6. TEXTURE MEMORY, WHICH NO LUA NUMBER CAN SEE. Display objects and their textures are C-side:
-- the Lua wrapper for one is tiny, so a leak of display objects can climb into hundreds of MB of
-- process footprint while `lua_kb` above sits perfectly still. This is the counter that would
-- explain a footprint like that, and Solar2D reports it itself.
local tex
pcall(function()
  local ok, v = pcall(function() return system.getInfo('textureMemoryUsed') end)
  if ok and type(v) == 'number' then tex = v end
end)
add('tex_kb', tex and string.format('%.0f', tex) or 'n/a')

-- 7. WHICH LUA TABLE IS KEEPING THINGS. The heap grows ~0.13 MB per reload while the world is
-- destroyed each time, so something OUTSIDE the world is holding what the world made - and a leak of
-- Lua objects is a table somewhere, not a mystery. This walks package.loaded one level deep and
-- reports the biggest tables by entry count: whichever one climbs across reloads is the answer.
-- Bounded, because one of these caches can hold a hundred thousand entries on its own.
local rows, walked = {}, 0
for name, mod in pairs(package.loaded) do
  if type(mod) == 'table' and walked < 400 then
    walked = walked + 1
    for key, v in pairs(mod) do
      if type(v) == 'table' then
        local n = 0
        for _ in pairs(v) do
          n = n + 1
          if n > 200000 then break end
        end
        if n >= 50 then rows[#rows + 1] = { n = n, name = tostring(name) .. '.' .. tostring(key) } end
      end
    end
  end
end
table.sort(rows, function(a, b) return a.n > b.n end)
local total = 0
for _, r in ipairs(rows) do total = total + r.n end
local parts = {}
for i = 1, math.min(#rows, 4) do parts[i] = rows[i].name .. '[' .. rows[i].n .. ']' end
add('tables', total .. ':' .. table.concat(parts, ' '))

-- 8. WHAT OUR OWN LOOP IS DOING, so a column that moves can be read next to it.
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
        pid = pad_drive.process_pid()
        print("%-7s %-9s %-8s %-8s %-7s %-9s %-9s %-7s %s" % (
            "t", "priv_MB", "d_MB", "lua_kb", "objs", "tx_kb", "rl_done", "fps", "rest"))
        first, t0, i, first_reloads = None, time.monotonic(), 0, None
        while True:
            st = sample(b)
            i += 1
            if st is None:
                print("no answer from the game (closed?)")
                return 2
            if first is None:
                first = st
            mem = memory_mb(pid)
            priv = "%.0f" % mem[1] if mem else "?"

            def delta(key):
                try:
                    return "%+d" % (int(float(st[key])) - int(float(first[key])))
                except (KeyError, ValueError):
                    return "-"

            def dmem():
                if not mem or not first.get("_priv"):
                    return "-"
                return "%+.0f" % (mem[1] - first["_priv"])

            if mem and first is not None:
                first.setdefault("_priv", mem[1])
            # A RELOAD RATE, NOT JUST A TOTAL. `rl_done` counts the feature's own reloads, so the
            # memory per reload is the number that says whether reloading is what costs memory -
            # and it can be read off the same row as the raw footprint it is climbing from.
            done = st.get("rl_done")
            if done not in (None, "nil") and first_reloads is None:
                first_reloads = (int(float(done)), first.get("_priv"))
            per = ""
            if done not in (None, "nil") and first_reloads and first.get("_priv"):
                n = int(float(done)) - first_reloads[0]
                if n > 0 and mem:
                    per = "|%.2f MB/reload" % ((mem[1] - first_reloads[1]) / n)

            rest = " ".join("%s=%s" % (k, st[k]) for k in
                            ("tables", "effects", "with_mon", "with_steps", "rl_busy", "qrlog",
                             "groups", "regs", "autoroll") if k in st)
            print("%-7.1f %-9s %-8s %-8s %-7s %-9s %-9s %-7s %s %s" % (
                time.monotonic() - t0, priv, dmem(), st.get("lua_kb", "?"), st.get("objs", "?"),
                st.get("tex_kb", "?"), st.get("rl_done", "?"), st.get("fps", "?"), rest, per))
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
