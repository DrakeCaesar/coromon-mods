#!/usr/bin/env python3
"""
core.py - everything the live in-game helpers share: the hook-up to the running game,
the engine/display helpers, and the harness that composes the features into one Lua
chunk and one command.

Nothing here knows what the features are. Each feature module exposes the same small
surface (documented in `ingame/__init__.py`) and this module asks for it, which is what
lets any subset be installed, removed and reported on without the others knowing they
exist.
"""

import os
import sys
import time

# coromon_lua.py sits next to the ingame/ package, not inside it.
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from coromon_lua import MINIMAL_HOOKS, Bridge  # noqa: E402

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
    ("process", "coromon.exe", "the process to attach to"),
]

CONFIG_PATH = os.path.join(_TOOLS, config.FILENAME)


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


def connect(process):
    b = Bridge(process, hooks=MINIMAL_HOOKS)
    for _ in range(150):
        if b.status().get("state"):
            break
        time.sleep(0.2)
    return b


def eval_(b, code, timeout=30.0):
    r = b.eval(code, timeout=timeout)
    return r.get("out") or r.get("err")


def main(features):
    """Read overlays.toml and make the game match it. No arguments, by design: the file is
    the only place settings live."""
    try:
        cfg, warnings, created = config.load(CONFIG_PATH, CORE_SETTINGS, features)
    except config.ConfigError as exc:
        print("config: %s" % exc, file=sys.stderr)
        return 2

    for w in warnings:
        print("config: %s" % w, file=sys.stderr)
    if created:
        print(
            "config: wrote a fresh %s from the defaults" % CONFIG_PATH, file=sys.stderr
        )

    b = connect(cfg["core"]["process"])
    try:
        # A one-shot repair runs first, then the settings are applied as usual, so a
        # displaced camera can be fixed and the features put back in the same run.
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
    finally:
        b.detach()
    return 0
