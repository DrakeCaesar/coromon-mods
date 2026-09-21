#!/usr/bin/env python3
"""
core.py - everything the live in-game helpers share: the hook-up to the running game,
the engine/display helpers, and the harness that composes the features into one Lua
chunk and one command.

Nothing here knows what the features are. Each feature module exposes the same small
surface (documented in `ingame/__init__.py`) and this module asks for it, which is what
lets any subset be installed, removed and reported on without the others knowing they
exist.

It also owns the process lifecycle, and that is a loop rather than an attach: started
before the game is up it waits for it, and when the game is closed and started again it
re-attaches and puts every feature back on its own. The game is the thing that comes and
goes; the settings file is read once per session there too, so an edit made while the game
is down is picked up by the next launch.
"""

import ctypes
import hashlib
import os
import sys
import tempfile
import threading
import time

# coromon_lua.py sits next to the ingame/ package, not inside it.
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from coromon_lua import MINIMAL_HOOKS, Bridge, GameGone  # noqa: E402

from . import config  # noqa: E402

# ===========================================================================
# Shared Lua. Reached by every feature, so it is defined once and concatenated
# ahead of them.
# ===========================================================================
LUA = r"""
local function uv(fn, name)
  if type(fn) ~= 'function' then return nil end
  for i = 1, 120 do
    local n, v = debug.getupvalue(fn, i)
    if not n then return nil end
    if n == name then return v end
  end
end

-- MTE, whichever way it is reachable: the old item and zoom tools each picked a different
-- one (_G.MTE and package.loaded), and both exist. Accept either rather than repeat that
-- split here.
local function mte()
  local m = _G.MTE
  if type(m) == 'table' and type(m.getTiledWorld) == 'function' then return m end
  m = package.loaded['classes.modules.mte.mte']
  if type(m) == 'table' then return m end
end

-- The overworld node. NEVER cache it: a location change rebuilds it, and a stale one has
-- no .x at all. There is no accessor for it - it is only reachable as an upvalue of
-- MTE.getTiledWorld - so this walks the upvalues every time it is asked.
local function twNode()
  local m = mte()
  if not m then return nil end
  local w = uv(m.getTiledWorld, 'tiledWorld')
  if type(w) ~= 'table' or type(w.x) ~= 'number' then return nil end
  return w
end

local function playerSprite()
  local ok, i = pcall(function() return spawnableHelper:getPlayerSpawnable() end)
  local s = (ok and type(i) == 'table') and i.sprite or nil
  if type(s) == 'table' and type(s.x) == 'number' then return s end
end

-- THE LIVE SAVE TABLE, and this is now the only copy of the search for it.
--
-- It is not reachable from any global, from Game/Save/the player, or from any object on screen:
-- it exists only as an upvalue of functions inside loaded modules. So it is found by asking every
-- function in every loaded module for its upvalues and taking the table that carries BOTH
-- SAVEABLE_BATTLE_EFFECTS and VISITED_MAPS - checked against every loaded module, and no other
-- one holds both.
--
-- steptimer and cooldowns each carried their own identical copy of this loop, which is a
-- duplicate of the most expensive search in the tool: all of package.loaded, up to 80
-- debug.getupvalue calls per function. cooldowns' copy is what cost the main menu ~40 ms per
-- frame (165 fps -> 20) when its throttle was wrong, because at that cost "how often" is the
-- whole question. One copy means one place to get that right.
--
-- CALLERS MUST THROTTLE IT, and each keeps its OWN cache and interval: their staleness budgets
-- are genuinely different - cooldowns needs the table within about a second of an effect starting,
-- while steptimer is content to wait 12.5 s - so this deliberately does no caching of its own.
local function saveSettingsTable()
  for _, mod in pairs(package.loaded) do
    if type(mod) == 'table' then
      for _, fn in pairs(mod) do
        if type(fn) == 'function' then
          for i = 1, 80 do
            local n, v = debug.getupvalue(fn, i)
            if not n then break end
            if type(v) == 'table'
               and v.SAVEABLE_BATTLE_EFFECTS ~= nil and v.VISITED_MAPS ~= nil then
              return v
            end
          end
        end
      end
    end
  end
end

-- THE DECIDED REROLL RESULT, if the game has made one. One copy, because two features need it
-- now: steptimer to draw it, autoroll to decide whether to reload.
--
-- The game decides the potential the moment the Coromon is handed over, not when the steps are
-- walked, and it records that on the monster: `didRerollPotential` is set and `potential` holds
-- the new value, while `getOriginalPotential` still has the old one. That flag is what makes this
-- readable BEFORE the walk finishes, which is the whole reason a reroll loop is cheap.
--
-- The Traitformator has no such accessors and answers nil, which is right: only the Potentiflator
-- has a decided potential to report.
local function decidedPotentialOf(e)
  if type(e) ~= 'table' then return nil, nil end
  if type(e.getOriginalPotential) ~= 'function' or type(e.getMonster) ~= 'function' then
    return nil, nil
  end
  local okM, mon = pcall(function() return e:getMonster() end)
  if not okM or type(mon) ~= 'table' or not mon.didRerollPotential then return nil, nil end
  local okO, from = pcall(function() return e:getOriginalPotential() end)
  return (okO and tonumber(from) or nil), tonumber(mon.potential)
end

-- THE LIVE SAVE TABLE, CACHED. saveSettingsTable() above is the search; this is the throttle, and
-- it is here so a feature cannot get the throttle wrong on its own.
--
-- The mistake this exists to prevent has now been made twice: `if <cache> == nil or
-- (<ticks> % INTERVAL) == 0` runs the search EVERY TICK while the table has never been found,
-- because a failed search is not cached. In cooldowns that cost ~40 ms a frame; in steptimer,
-- whose poll is 250 ms, it was four full walks of package.loaded per second. So this counts from
-- the LAST ATTEMPT, and it refuses to search at all until a save is loaded - `playerStats.getSteps()`
-- is the game's own step counter, it only answers once a save exists, and it is far cheaper than
-- the search it guards.
--
-- It keeps NO reference of its own between calls beyond the cache, so it is safe for any number of
-- features to call every tick. Callers that want the table to survive a save reload should keep
-- their own copy; this only guarantees a recent one.
local LS_found, LS_lastMs = nil, nil
local LS_HUNT_MS, LS_FOUND_MS = 1000, 12000
local function liveSaveSettings()
  local ok, n = pcall(function() return playerStats.getSteps() end)
  if not ok or tonumber(n) == nil then
    LS_found, LS_lastMs = nil, nil     -- no save: nothing to read, nothing to remember
    return nil
  end
  -- THROTTLED IN MILLISECONDS, NOT IN CALLS, and that is about who is allowed to call this.
  --
  -- A call-counted interval makes the cost depend on the caller's rate: the same helper driven once
  -- per frame instead of four times a second searches package.loaded twelve times more often. At 165
  -- fps this would search once every 0.36 s with the table found, and forty times a second while it
  -- is not - which is exactly the mistake that cost cooldowns ~40 ms a frame and steptimer four
  -- searches a second. Measured in time, any feature may call it every frame and the search still
  -- happens at this rate and no faster.
  --
  -- 1 s while nothing has been found, because a save load is the moment the table appears; 12 s once
  -- it has been, matching steptimer's own rescan.
  local now = system.getTimer()
  if type(now) ~= 'number' then now = 0 end
  local every = LS_found and LS_FOUND_MS or LS_HUNT_MS
  if LS_lastMs == nil or (now - LS_lastMs) >= every then
    LS_lastMs = now
    local s = saveSettingsTable()
    if s then LS_found = s end
  end
  return LS_found
end

-- A localised string, or the fallback. A MISSING key comes back as '???' rather than nil
-- (checked live), so it has to be tested for explicitly.
local function loc(key, fallback)
  local ok, v = pcall(localise, key)
  if ok and type(v) == 'string' and v ~= '' and v ~= '???' then return v end
  return fallback
end

-- Coromon's wrapped display has no stroke support at all - setStrokeColor is nil on it -
-- so every border drawn anywhere in these tools is really a filled rect. Anchored 0,0 on
-- integer coordinates, which keeps it crisp under nearest-neighbour filtering.
local function rect(parent, x, y, w, h, c)
  local r = display.newRect(0, 0, w, h)
  r.anchorX, r.anchorY = 0, 0
  r.x, r.y = x, y
  if c then pcall(function() r:setFillColor(c[1], c[2], c[3], c[4] or 1) end) end
  if parent then parent:insert(r) end
  return r
end

local function text(parent, font, s, x, y, c)
  local t = textHelper:new(parent, font, { text = s })
  t.x, t.y = x, y
  if c then pcall(function() t:setFillColor(c[1], c[2], c[3]) end) end
  return t
end

local function drop(g)
  if type(g) == 'table' and g.parent then pcall(function() g:removeSelf() end) end
end

-- Keep a screen-space group on top of whatever the game has drawn since. Stage children
-- draw after everything else, but the game appends its own groups to the stage when it
-- changes scene, which would cover us - so only touch the display list when something was
-- actually placed after us.
local function keepOnTop(g)
  if type(g) ~= 'table' then return end
  local stage = display.getCurrentStage()
  local n = stage.numChildren or 0
  local needs = (g.parent ~= stage)
  if not needs and n > 0 then needs = (stage[n] ~= g) end
  if not needs then return end
  pcall(function()
    if g.parent then g:removeSelf() end
    stage:insert(g)
  end)
end

-- Screen pixels per texture pixel at the game's normal view: a plain live number on the
-- game's display library (4 at a 1920-wide window, 0.25 the other way round) that tracks
-- the window size, not a fixed integer factor.
local function nativeScale()
  local s = display.contentToScreenScale
  if type(s) ~= 'number' or s < 1 then s = 1 end
  return s
end
"""


# ===========================================================================
# Host. One state table, one polling timer, one feature registry.
#
# Each feature is a table in _G.__hud.feats with an update slot (run by the
# single timer, at its own period), a kill slot (its teardown) and an `on`
# flag. That is what lets any subset be installed, and any subset removed,
# without every feature knowing about the others.
# ===========================================================================
HOST = r"""
local TICK_MS = 200

-- The three features used to be three separate tools with their own globals. Take any of
-- those down too, or running this instead of them leaves two of everything on screen.
local function killLegacy()
  local b = _G.__battlepot
  if type(b) == 'table' then
    b.on = false
    if b.timer then pcall(function() timer.cancel(b.timer) end) end
    drop(b.group)
    _G.__battlepot = nil
  end
  local i = _G.__hidden
  if type(i) == 'table' then
    i.on = false
    if i.timer then pcall(function() timer.cancel(i.timer) end) end
    drop(i.group)
    _G.__hidden = nil
  end
  local m = _G.__mapzoom
  if type(m) == 'table' then
    m.on = false
    if m.efListener then pcall(function() Runtime:removeEventListener('enterFrame', m.efListener) end) end
    if m.keyListener then pcall(function() Runtime:removeEventListener('key', m.keyListener) end) end
    local w = twNode()
    if w then w.xScale, w.yScale = 1, 1 end
    if type(m.sticky) == 'table' then
      for node, rec in pairs(m.sticky) do
        if type(node) == 'table' then
          node.x = node.x - (rec.corrx or 0)
          node.y = node.y - (rec.corry or 0)
          node.xScale, node.yScale = 1, 1
        end
      end
    end
    _G.__mapzoom = nil
  end
end
killLegacy()

-- Re-running the installer must replace the previous install, not stack on top of it.
local prev = _G.__hud
if type(prev) == 'table' then
  for _, pf in pairs(prev.feats or {}) do if pf.kill then pcall(pf.kill) end end
  if prev.timer then pcall(function() timer.cancel(prev.timer) end) end
  _G.__hud = nil
end

local H = {}
_G.__hud = H
H.feats = {}

-- `period` is the feature's own poll interval in ms; the shared timer runs at the finest
-- one and each feature is run every `every` ticks. A feature that needs every frame takes
-- period 0: it gets an enterFrame listener of its own instead of a slot here.
local function makeFeature(name, period)
  local f = { on = false, period = period or 0 }
  f.every = f.period > 0 and math.max(1, math.floor(f.period / TICK_MS + 0.5)) or 0
  H.feats[name] = f
  return f
end

local n = 0
local function tick()
  local h = _G.__hud
  if not h or h.tickBody ~= tick then return end
  n = n + 1
  for _, f in pairs(h.feats) do
    if f.on and f.update and f.every > 0 and (n % f.every) == 0 then
      pcall(f.update)
    end
  end
end
"""


# Appended after the feature blocks. Runs everything once so the install reports real
# numbers rather than promises, then says what happened.
DRIVER = r"""
H.tickBody = tick
H.timer = timer.performWithDelay(TICK_MS, tick, 0)

for _, f in pairs(H.feats) do
  if f.on and f.update then pcall(f.update) end
end

local done = {}
__SUMMARIES__
if #done == 0 then return 'nothing selected' end
return 'installed  ' .. table.concat(done, '\n           ')
"""


# Both of these are `CORE + feature query Lua + body`, with __ADD__ replaced by one call
# per feature.
STATUS_BODY = r"""
local out = {}
local function add(s)
  if type(s) == 'string' and s ~= '' then out[#out + 1] = s end
end

local h = _G.__hud
if type(h) ~= 'table' then
  out[#out + 1] = 'overlays: not installed'
else
  local names = {}
  for name, f in pairs(h.feats) do
    names[#names + 1] = name .. (f.on and '=on' or '=off')
  end
  table.sort(names)
  out[#out + 1] = 'overlays: ' .. table.concat(names, '  ')
end

__ADD__

local w = twNode()
if not w then
  out[#out + 1] = 'tiledWorld: not in the overworld'
else
  local p = playerSprite()
  if p then
    local ok, lx, ly = pcall(function() return p:localToContent(0, 0) end)
    if ok then
      out[#out + 1] = string.format(
        'player=(%.2f, %.2f)  on screen=(%.1f, %.1f)  viewport centre=(%.1f, %.1f)',
        p.x, p.y, lx, ly, display.contentWidth / 2, display.contentHeight / 2)
    end
  end
end
return table.concat(out, '\n')
"""


# Read-only: installs nothing, so it is safe to run whatever is or is not on screen.
REPORT_BODY = r"""
local out = {}
local function add(s)
  if type(s) == 'string' and s ~= '' then out[#out + 1] = s end
end

__ADD__

return table.concat(out, '\n')
"""


def compose(parts):
    """Join Lua fragments, dropping any that are empty."""
    return "\n".join(p for p in parts if p and p.strip())


# Settings for the top level of overlays.toml. Each feature declares its own section.
CORE_SETTINGS = [
    (
        "process",
        "coromon.exe",
        "the process to attach to - it is waited for, so it need not be running",
    ),
    (
        "features_enabled",
        True,
        (
            "master switch for every feature below. Set it false and run to attach the tool "
            "with NOTHING installed: the session stays, its Frida hooks stay, the features do "
            "not. That is the only way to tell what the script itself costs from what the "
            "features cost - and it is the measurement to make when frame rate is short. The "
            "per-feature `enabled` flags are left alone, so setting this back to true restores "
            "exactly the run that was there before"
        ),
    ),
    (
        "gamepad_zoom",
        True,
        (
            "map the gamepad's triggers to the zoom keys (the triggers reach the game "
            "nowhere else - they are analogue and Solar2D emits no event for them)"
        ),
    ),
    (
        "gamepad_trigger_percent",
        35,
        "how far a trigger must be pulled before it counts as pressed, in percent",
    ),
    (
        "gamepad_repeat_ms",
        220,
        "how often a trigger that stays pulled repeats, so holding it keeps zooming",
    ),
]

def _data_dir():
    """Where overlays.toml lives.

    Beside the tools when they are run as scripts, but beside the .exe when frozen: a
    one-file build unpacks itself into a temp directory that is wiped on exit, so a config
    written relative to __file__ would vanish - and the whole point of the file is that it
    persists between runs.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _TOOLS


CONFIG_PATH = os.path.join(_data_dir(), config.FILENAME)


def install_code(features, cfg):
    """The whole install as one chunk. The host tears down whatever was installed last
    time first, so this is 'make the game match the config', not 'add to what is there' -
    which is also what makes setting enabled = false in the config remove a feature."""
    summaries = []
    for f in features:
        if not cfg[f.NAME]["enabled"]:
            continue
        summaries.append(
            "do local s = %s if s and s ~= '' then done[#done + 1] = s end end"
            % f.summary(cfg[f.NAME])
        )
    return compose(
        [LUA, HOST]
        + [f.lua(cfg[f.NAME]) for f in features]
        + [f.section(cfg[f.NAME]) for f in features]
        + [DRIVER.replace("__SUMMARIES__", "\n".join(summaries))]
    )


def _composed_doc(features, body, method, cfg):
    adds = "\n".join("add((%s))" % getattr(f, method)(cfg[f.NAME]) for f in features)
    return compose(
        [LUA] + [f.lua(cfg[f.NAME]) for f in features] + [body.replace("__ADD__", adds)]
    )


def status_code(features, cfg):
    return _composed_doc(features, STATUS_BODY, "status", cfg)


def report_code(features, cfg):
    return _composed_doc(features, REPORT_BODY, "report", cfg)


def action_code(feature, lua, cfg):
    """A one-shot action contributed by a feature needs the shared helpers too."""
    return compose([LUA, feature.lua(cfg), lua])


# How often to look for the game while it is not running, and how often to look for its
# Lua state once we are attached to it.
POLL = 1.0
STATE_POLL = 0.2


def eval_(b, code, timeout=30.0):
    """Run one chunk. Raises GameGone if the game closed while it was being run, so a
    restart never gets mistaken for a feature that failed."""
    r = b.eval(code, timeout=timeout)
    if r.get("closed"):
        raise GameGone()
    return r.get("out") or r.get("err")


def wait_for_state(b, log, every=5.0):
    """Wait until the bridge has captured the game's lua_State. False if the game went
    away first, which is not an error - it just means wait for the next one."""
    last = time.time()
    while True:
        st = b.status()
        if st is None:
            return False
        if st.get("state"):
            return st
        now = time.time()
        if now - last >= every:
            last = now
            log(
                "  hooked lua.dll, waiting for the game to run some Lua"
                if st.get("ready")
                else "  waiting for the game to load lua.dll"
            )
        time.sleep(STATE_POLL)


# THE GAME IS NOT SAFE TO INSTALL INTO UNTIL ITS OWN BOOTSTRAP HAS FINISHED.
#
# Capturing a lua_State* only means lua.dll has run some Lua, which happens in the first moments of
# the boot - long before main.lua has finished. Installing then races the game's own startup, and it
# loses in a way that is easy to mistake for something else: the install REQUIRES modules, and Lua
# caches them in package.loaded, so a module loaded while the game is still initialising is cached in
# whatever half-built state it was in, and the game's own require of it later is handed that instead
# of a fresh one. The visible result is the game starting to a black screen and staying there.
#
# It only happens SOMETIMES because whether the install lands before or after the bootstrap finishes
# is a race - and that is the whole character of the bug as reported.
#
# So wait for the globals the features themselves depend on. They are the same ones quick_reload's
# pre-flight checks, so if they are all there the game is far enough along for anything here to be
# safe to touch.
READY = r'''
if type(_G.require) ~= 'function' or type(_G.package) ~= 'table' then return 'boot' end
if type(_G.display) ~= 'table' then return 'boot' end
if type(_G.playerStateHelper) ~= 'table' then return 'boot' end
if type(_G.SaveslotPreferences) ~= 'table' then return 'boot' end
if type(_G.inputHelper) ~= 'table' then return 'boot' end
return 'ready'
'''


def wait_for_ready(b, log=print, every=5.0):
    """Block until the game's own bootstrap is done. False if the game went away first, which is not
    an error - the caller goes back to waiting for the next one."""
    last = time.time()
    while True:
        try:
            out = (eval_(b, READY, timeout=15.0) or "").strip()
        except GameGone:
            return False
        if out == "ready":
            return True
        now = time.time()
        if now - last >= every:
            last = now
            log(
                "  the game is running but still starting up (%s) - not installing into it yet"
                % (out or "no answer")
            )
        time.sleep(STATE_POLL)


def wait_for_game(process, log=print):
    """Block until the game is running and its Lua state is reachable, then hand back the
    bridge. Started before the game, this waits for it; started while it runs, it attaches
    straight away.

    Neither "not there yet" nor "there but not attachable yet" is fatal. The second one is
    what a restart looks like from here: the closed process is still listed for a moment, and
    while it is on its way out it refuses the injector outright - measured, that is a Frida
    attach error with VirtualAllocEx -> ACCESS_DENIED, which arrives as
    frida.NotSupportedError or frida.TransportError depending on where it trips. Retrying
    picks up whatever replaced it, so the loop just keeps going and says why it is waiting,
    once.
    """
    waiting = False
    said = None
    reason = None

    def note(exc):
        nonlocal reason
        reason = str(exc)

    while True:
        reason = None
        b = Bridge.try_attach(process, hooks=MINIMAL_HOOKS, on_retry=note)
        if b is None:
            if not waiting:
                waiting = True
                log(
                    "waiting for %s ...  (start the game whenever; Ctrl+C to stop)"
                    % process
                )
            if reason and reason != said:
                log("  %s is not attachable yet:" % process)
                log("    %s" % reason)
                log(
                    "    retrying - normal while the game is still starting, or still closing"
                )
            said = reason
            time.sleep(POLL)
            continue
        waiting, said = False, None
        log("attached to %s" % process)
        if wait_for_state(b, log):
            return b
        log("  %s closed before its Lua state could be reached" % process)
        b.detach()


def apply(b, features, cfg):
    """Make the game match the config: the one-shot repairs first, then the features, then
    say what was installed and what it looks like from in there."""
    for f in features:
        for name, lua in getattr(f, "ACTIONS", {}).items():
            if cfg[f.NAME].get(name):
                print(eval_(b, action_code(f, lua, cfg[f.NAME])))

    print("--- applied ---")
    print(eval_(b, install_code(features, cfg), timeout=60.0))
    print()
    print("--- status ---")
    print(eval_(b, status_code(features, cfg)))
    print()
    print("--- report ---")
    print(eval_(b, report_code(features, cfg)))


def read_config(features, warn=None):
    """Read overlays.toml, writing it from the defaults if it is not there yet. Returns
    None if it cannot be read at all."""
    warn = warn or (lambda m: print(m, file=sys.stderr))
    try:
        cfg, warnings, created = config.load(CONFIG_PATH, CORE_SETTINGS, features)
    except config.ConfigError as exc:
        warn("config: %s" % exc)
        return None
    for w in warnings:
        warn("config: %s" % w)
    if created:
        warn("config: wrote a fresh %s from the defaults" % CONFIG_PATH)
    return cfg


# ===========================================================================
# One instance at a time.
#
# A run stays alive for ever: it waits for the game, installs, and installs again every time
# the game is closed and started again. Starting it a second time while one is already
# running therefore used to leave two watchers, each holding its own copy of this module's
# code, and every launch then got two installs racing to tear each other down. That is what
# froze the game - an error thrown inside an install leaves Solar2D sitting on it and the
# game never gets another frame.
#
# So a run claims a lock file and a second run refuses to start, naming the process that is
# already watching. The lock lives in the system temp directory, never beside the tools
# (they are under version control, and a lock file there would just be noise in git), and it
# is keyed on this directory so two checkouts do not fight over one lock. A lock whose
# process is gone is taken over silently, so a crashed or killed run cannot block the next.
#
# Claimed from the entry point rather than from main(), so that anything importing this
# module to poke at it keeps working.
# ===========================================================================

LOCK_STEM = "coromon-overlays"


def lock_path():
    key = os.path.abspath(_TOOLS).lower().encode("utf-8", "replace")
    return os.path.join(
        tempfile.gettempdir(),
        "%s-%s.lock" % (LOCK_STEM, hashlib.sha1(key).hexdigest()[:12]),
    )


def _pid_alive(pid):
    """Is that pid still running? Anything other than a definite no counts as yes: taking
    over a lock whose owner is still there is the mistake worth avoiding here."""
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is NOT a liveness probe on Windows - it calls TerminateProcess, so
        # it would kill the very process it is supposedly asking about.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # a process we may not open still exists, and that is an answer
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def claim_single_instance():
    """Claim the lock for this run, or return None if another run holds it - having said
    which one, and what to do about it. The lock path is returned otherwise, to hand back to
    release_single_instance()."""
    path = lock_path()
    me = os.getpid()
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    owner = int(fh.read().strip() or 0)
            except (OSError, ValueError):
                owner = 0
            if owner and owner != me and _pid_alive(owner):
                print("overlays is ALREADY watching the game, in pid %d." % owner)
                print("Stop that one first - Ctrl+C in its terminal - then run this again.")
                print("Only one instance may run: each one installs its own copy of the code")
                print("on every game launch, and two racing installs freeze the game.")
                print("(If pid %d is really gone, delete %s.)" % (owner, path))
                return None
            # stale, or a torn write: clear it and try again
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(me))
        return path
    print("could not take %s - another run is starting?" % path, file=sys.stderr)
    return None


def release_single_instance(path):
    """Drop the lock, but only if it is still ours - never delete someone else's."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            owner = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return
    if owner == os.getpid():
        try:
            os.remove(path)
        except OSError:
            pass


# ===========================================================================
# The pad's triggers -> the zoom keys.
#
# The triggers cannot be read inside the game, and that is measured rather than assumed.
# Solar2D forwards a gamepad's DIGITAL buttons as ordinary key events - the game's own
# listener sees `descriptor = "Gamepad 3: buttonA"`, `leftShoulderButton1`, the d-pad and so
# on, and its inputHelper is built entirely around that. The triggers are ANALOGUE axes
# though, and no event is emitted for them at all: measured with a listener on Runtime's key
# event while L2 and R2 were pressed, the bumpers showed up and the triggers produced
# nothing. So the game cannot use the triggers, and nothing can be stolen from it.
#
# They are therefore read out here, over XInput - the same API the game loads itself
# (xinput1_4.dll is in its module list). A press is one small eval on the bridge that is
# already attached, and it calls the zoom's owned key handler with the keys already
# configured in overlays.toml. Nothing runs inside the game for this and no per-frame work is
# added anywhere: a pad that misbehaves cannot take the game down with it.
# ===========================================================================

TRIGGER_MAX = 255


class _XINPUT_GAMEPAD(ctypes.Structure):
    # DWORD + this 12-byte block is 16 bytes with natural alignment, so ctypes' default
    # packing is already the layout XInput expects - do not add _pack_ here.
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", _XINPUT_GAMEPAD)]


def _load_xinput():
    for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    return None


class TriggerKeys(threading.Thread):
    """Watches the pad's triggers and presses a key when one is pulled.

    Held triggers repeat, so holding L2 keeps zooming out and holding R2 keeps zooming in.
    A pad that is not connected is simply never seen: XInputGetState answers "disconnected"
    and the loop idles."""

    POLL = 1.0 / 30

    def __init__(self, press, keys, threshold=0.35, repeat_ms=220):
        super().__init__(daemon=True)
        self._press = press
        self._keys = dict(keys)          # {'left': '-', 'right': '+'}
        self._threshold = threshold
        self._repeat = max(0.05, float(repeat_ms) / 1000.0)
        self._stop = threading.Event()
        self._xinput = _load_xinput()
        self.failed = None if self._xinput is not None else "xinput is not available"
        self.presses = 0

    @property
    def keys(self):
        return dict(self._keys)

    def stop(self):
        self._stop.set()

    def run(self):
        if self._xinput is None:
            return
        due = {}                     # (pad, side) -> when the next repeat is allowed
        while not self._stop.is_set():
            try:
                for pad in range(4):
                    state = _XINPUT_STATE()
                    if self._xinput.XInputGetState(pad, ctypes.byref(state)) != 0:
                        continue
                    for side, value in (("left", state.Gamepad.bLeftTrigger),
                                        ("right", state.Gamepad.bRightTrigger)):
                        key = self._keys.get(side)
                        if not key:
                            continue
                        slot = (pad, side)
                        now = time.monotonic()
                        pulled = value / float(TRIGGER_MAX) >= self._threshold
                        if not pulled:
                            due.pop(slot, None)
                            continue
                        if now >= due.get(slot, 0.0):
                            due[slot] = now + self._repeat
                            self._fire(key)
            except Exception as exc:              # a pad being unplugged mid-read, mostly
                self.failed = str(exc)
                time.sleep(0.5)
            self._stop.wait(self.POLL)

    def _fire(self, key):
        try:
            self._press(key)
            self.presses += 1
        except Exception as exc:                  # the game closing mid-press, mostly
            self.failed = str(exc)


def press_key_code(key):
    """Lua that hands the zoom's own key handler one key event.

    It is a direct call into the feature rather than a synthetic input event, so the game's
    input system never sees it - only the zoom reacts."""
    literal = '"%s"' % key.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
    return (
        "local h = _G.__hud\n"
        "local k = h and h.keyBody\n"
        "if type(k) ~= 'function' then return 'no key handler' end\n"
        "local ok, err = pcall(k, { keyName = %s, phase = 'down' })\n"
        "return ok and 'pressed' or tostring(err)\n" % literal
    )


def start_trigger_keys(cfg, features, b):
    """Start the trigger bridge, or return None when it is switched off or pointless."""
    core_cfg = cfg.get("core") or {}
    if not core_cfg.get("gamepad_zoom", True):
        return None
    zoom_cfg = cfg.get("zoom") or {}
    if not zoom_cfg.get("enabled", True):
        return None
    if not any(getattr(f, "NAME", "") == "zoom" for f in features):
        return None

    def first_key(name, fallback):
        value = zoom_cfg.get(name)
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
        if isinstance(value, str) and value:
            return value
        return fallback

    keys = {"left": first_key("key_out", "-"), "right": first_key("key_in", "+")}
    threshold = float(core_cfg.get("gamepad_trigger_percent", 35) or 35) / 100.0
    pad = TriggerKeys(
        press=lambda key: eval_(b, press_key_code(key), timeout=10.0),
        keys=keys,
        threshold=threshold,
        repeat_ms=core_cfg.get("gamepad_repeat_ms", 220) or 220,
    )
    pad.start()
    return pad


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def effective_config(features, baseline, note=print):
    """The baseline with the runtime overrides laid over it.

    The override file is only ever read here, and a broken one is reported and skipped, because
    the tool must not become impossible to install just because a runtime file was left
    half-written by whatever was toggling it.
    """
    path = config.runtime_path(CONFIG_PATH)
    try:
        runtime = config.load_runtime(path)
    except config.ConfigError as exc:
        note("runtime: %s" % exc)
        return baseline
    if not runtime:
        return baseline
    return config.apply_runtime(baseline, runtime)


def watch_for_config(b, features, cfg, note=print):
    """Stay attached until the game goes away, re-applying whenever overlays.toml changes.

    THIS IS THE RUNTIME ON/OFF SWITCH, and it is built out of machinery that already exists.
    `apply()` re-runs the install chunk, and the install chunk's first act is to call `kill()` on
    every feature installed last time and drop the host. So switching a feature off tears down its
    enterFrame and key listeners, the functions it wrapped, its timers and the objects it drew;
    switching it back on rebuilds it from the same generated chunk. Nothing new has to unwind
    anything, and no feature needs to know how - the teardown that already runs on every launch
    runs again here.

    The only thing that was missing was the re-read: the file was consulted once per game session,
    so an edit needed the game restarted before it took effect. That is what made "which feature is
    costing me frame rate?" a restart-per-answer question.

    A file that will not parse is reported and the settings already installed are kept, because
    losing every overlay to a typo in a comment is worse than ignoring the edit. Returns the
    config that was in force when the game went away.
    """
    watched = (CONFIG_PATH, config.runtime_path(CONFIG_PATH))
    last = tuple(_mtime(p) for p in watched)
    while True:
        if b.wait(0.5):
            return cfg
        now = tuple(_mtime(p) for p in watched)
        if now == last:
            continue
        last = now
        baseline = read_config(features, warn=note)
        if baseline is None:
            note("config: not readable - keeping what is already installed")
            continue
        fresh = effective_config(features, baseline, note=note)
        if fresh == cfg:
            note("changed, but nothing installed differs")
            continue
        note("--- %s changed - re-applying in the running game ---" % config.FILENAME)
        cfg = fresh
        try:
            if not wait_for_ready(b):
                return cfg
            apply(b, features, cfg)
        except GameGone:
            return cfg
        note("--- re-applied - the game matches the baseline plus the runtime overrides ---")


def main(features):
    """Watch the game: wait for it, install what overlays.toml asks for, stay out of the
    way while it runs, and do it all again when it is closed and started again. No
    arguments, by design: the file is the only place settings live."""
    baseline = read_config(features)
    if baseline is None:
        return 2
    cfg = effective_config(features, baseline)
    process = cfg["core"]["process"]

    try:
        while True:
            b = wait_for_game(process)
            pad = None
            try:
                # Before anything is installed: a game that is still booting must not have its
                # modules required out from under it. See READY.
                if not wait_for_ready(b):
                    raise GameGone()
                apply(b, features, cfg)
                pad = start_trigger_keys(cfg, features, b)
                print()
                print(
                    "watching %s - this stays attached until the game closes and re-attaches"
                    % process
                )
                print("by itself when it is started again. Ctrl+C to stop.")
                print(
                    "edits to %s are picked up while this runs: save the file and the features "
                    "are torn down and re-installed to match it, with no restart. Flipping one "
                    "`enabled` at a time is how to see what a feature costs." % config.FILENAME
                )
                if pad is not None:
                    if pad.failed:
                        print("gamepad: not reading the triggers - %s" % pad.failed)
                    else:
                        print(
                            "gamepad: %s zooms out, %s zooms in (hold to keep zooming)"
                            % (pad.keys["left"], pad.keys["right"])
                        )
                watch_for_config(b, features, cfg)
            except GameGone:
                pass
            finally:
                if pad is not None:
                    pad.stop()
                b.detach()
            print()
            print("--- the game closed - waiting for it to come back ---")
            # Reload between sessions, so an edit made while the game is down is picked up
            # by the next launch. A file that will not parse keeps the last good settings.
            fresh = read_config(features)
            if fresh is not None:
                baseline = fresh
                cfg = effective_config(features, baseline)
                process = cfg["core"]["process"]
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0
