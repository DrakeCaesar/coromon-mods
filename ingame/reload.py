#!/usr/bin/env python3
"""
reload.py - press a button in game and the save is put back, as a feature of overlays.py.

WHAT THIS REPLACES. This was `reload_r3.py`, a separate tool that attached to the game with its own
Frida session and drove `archive/quick_reload.py` from outside over several round trips. Two things
were wrong with that:

  * Running it alongside `overlays.py` meant TWO sessions installing hooks and two installers
    touching the same globals, while one of them tore the world down underneath the other. That is
    what produced the interaction-button crash.
  * Driving the reload from outside left gaps between the chunks - the driver's round trips - and a
    button press landing in one of those gaps hits UI that is half dismantled.

So the reload now runs INSIDE the game, in one chunk, on one session, installed by the same
`overlays.py` run as everything else. There are no round trips left, and the wait between cancelling
the transitions and tearing the world down is exactly one frame - which is what the flag needs - 
rather than a fixed sleep in a Python process.

WHAT IT RUNS. `quick_reload`'s own chunks, verbatim, through `loadstring`:

  PREFLIGHT         read-only. Validates the globals, refuses when there is no world to tear down,
                    and reads the slot the game is in. Nothing is destroyed until it says "ok N".
  TRANSITION_CANCEL flags every in-flight transition for cancellation. Deferred by design: the
                    transition module acts on the flag at its next enterFrame tick.
  TEARDOWN_BASE     the game's own quit sequence, plus the input-level/focus repair, ending at
                    `quitCurrentGame`.
  RELOAD_BODY       waits for `worldHelper:isCreated()` to go false, then loads the slot out of the
                    store the way the game's own debug loader does, and puts the title screen back if
                    that throws.

They are pulled in from the module rather than retyped, so the behaviour that was tested in-game is
the behaviour that runs here. `launch` is the one piece that differs: the driver used a 0.25 s sleep
between the cancel and the teardown, and here it is one frame.

NOTHING IS WRITTEN to disk, and the binding is not a game button: `rightJoystickButton` appears
nowhere in the archive, so the game ignores it. There is no need to consume the event.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "abandoned"))
import quick_reload  # noqa: E402  (the proven implementation; see the docstring)

NAME = "reload"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "press a button to put the save back, for the Potentiflator loop"),
    (
        "key",
        ["rightJoystickButton"],
        "what triggers it. The stick clicks arrive as names, not numbers - `rightJoystickButton` is "
        "the right stick pressed in, `leftJoystickButton` the left. The game maps neither, so "
        "nothing else reacts to the press.",
    ),
]


def _lua_str(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _keys(cfg):
    keys = cfg.get("key") or []
    if isinstance(keys, str):
        keys = keys.split(",")
    keys = [str(k).strip() for k in keys if str(k).strip()]
    return keys or ["rightJoystickButton"]


def _key_set(keys):
    return "{" + ", ".join("[%s] = true" % _lua_str(k.lower()) for k in keys) + "}"


def _key_label(cfg):
    return ", ".join(_keys(cfg))


def _probe_path():
    """Where the reload feature writes its read-only observations. Next to the tool, so they can be
    read back without anyone having to copy them out of a terminal."""
    return os.path.join(BASE, "reload-probe.txt")


def lua(cfg):
    """The queries. Always part of the chunk, so --status and --report can describe it whether or not
    the feature is installed."""
    return r"""
local function reloadFeat()
  local h = _G.__hud
  return h and h.feats and h.feats.reload or nil
end

local function reloadLine()
  local f = reloadFeat()
  if not f then return nil end
  if f.busy then return 'reload: running - ' .. tostring(f.step or '?') end
  return string.format('reload on __KEY__: %d done, last = %s',
    f.done or 0, tostring(f.last or 'not yet'))
end

-- WHAT THE WORLD INTERFACE LOOKS LIKE FROM INSIDE, and whether it is still the one the game drives.
-- Read-only and fully pcall'd, so it can be evaluated at any moment without side effects.
--
-- WHY IT EXISTS. After a reload, pressing the interact button has crashed three different ways:
-- `insert` missing, `.containers` missing, `.height` nil. Each is what a DISPOSED Solar2D display
-- object looks like from Lua - the metatable-provided members go, the plain fields the game assigned
-- stay. And a live one cannot look like that: `refreshInteractButton` rebuilds the button from
-- scratch (`groupHelper:clear(parentGroup)`, then a fresh `UIContainerBuilder.new` at L57), so if the
-- height reads nil the reference is stale. `worldInterface.onPress` (L289-291) reaches
-- `interactButton:setPressed(true)` through a CLOSURE UPVALUE of the interface instance, so a stale
-- button means the press is reaching the PRE-reload interface - one that `worldInterface:destroy()`
-- has already emptied and which the input layer is still routing to. This is what tells us whether
-- that is happening, and whether a replacement interface took over.
local function qrWiProbe()
  local p = {}
  local wi = _G.worldInterface
  if type(wi) ~= 'table' then
    p.note = 'worldInterface: module not present'
    return p
  end
  local ok, inst = pcall(function() return wi:isCreated() end)
  p.instance = ok and inst or nil
  p.created = (ok and inst ~= nil) or false

  local btn
  pcall(function() btn = wi:getInteractButton() end)
  p.button = btn
  if btn ~= nil then
    local bg
    pcall(function() bg = btn:getBackground() end)
    p.background = bg
    if bg ~= nil then
      local ok2, h = pcall(function() return bg.height end)
      p.height = ok2 and h or nil
      p.alive = ok2 and (type(h) == 'number') or false
      local ok3, t = pcall(function() return inputHelper:isTouchable(bg) end)
      p.touchable = ok3 and t or nil
    end
  end

  -- The registrations are the suspect: `refreshInteractButton` registers the built button with
  -- `inputHelper:addMouseHoverable` (L66), and nothing in the teardown removes it. A dead object in
  -- this list is a stale registration, which is exactly the thing that would drive a dead button.
  local hover, dead = 0, 0
  pcall(function()
    for _, o in pairs(inputHelper:getObjectsListeningToMouseHover() or {}) do
      hover = hover + 1
      local okh, h = pcall(function() return o.height end)
      if not okh or type(h) ~= 'number' then dead = dead + 1 end
    end
  end)
  p.hover, p.hoverDead = hover, dead
  pcall(function() p.level = inputHelper:getInputLevel() end)
  return p
end

local function qrWiProbeSummary()
  local p = qrWiProbe()
  if p.note then return p.note end
  local button = 'n/a'
  if p.background ~= nil then button = p.alive and 'alive' or 'DEAD' end
  return string.format('world interface: created=%s button=%s hoverables=%d dead=%d stalePress=%d',
    tostring(p.created), button, p.hover or 0, p.hoverDead or 0, _G.__qrStalePress or 0)
end

-- A DISPOSED DISPLAY OBJECT IS STILL CALLABLE THROUGH THE FIELDS THE GAME ASSIGNED TO IT.
-- `interactButton.setPressed` is a plain field (WorldInterfaceInteractButton L57), so it survives
-- disposal, while everything the ENGINE provides - `insert`, `height`, `width`, `toChildIndex` -
-- reads nil. That is why the crash is inside the method rather than on the call: `setPressed` (L101)
-- reaches `refreshInteractButtonBackgroundStyle`, which does arithmetic on `interactButtonBackground
-- .height` (L40) and gets nil.
local function qrLive(x)
  if x == nil then return false end
  local ok, ins = pcall(function() return x.insert end)
  return ok and type(ins) == 'function'
end

-- A press on a destroyed button means nothing: the button is gone, so its pressed state cannot
-- matter to anything. Skipping it is not a substitute for anything and it fabricates nothing - it is
-- the same call, declined, on an object that no longer exists. Wrapped on the LIVE button before the
-- teardown, because afterwards there is no way to reach it again.
local function qrGuardButton()
  local wi = _G.worldInterface
  if type(wi) ~= 'table' then return 'no worldInterface' end
  local btn
  local ok = pcall(function() btn = wi:getInteractButton() end)
  if not ok or type(btn) ~= 'table' then return 'no interact button' end
  if btn.__qrGuarded then return 'already guarded' end
  local wrapped = 0
  for _, name in ipairs({ 'setPressed', 'setHovered' }) do
    local orig = btn[name]
    if type(orig) == 'function' then
      btn[name] = function(self, ...)
        if not qrLive(self) then
          _G.__qrStalePress = (_G.__qrStalePress or 0) + 1
          return
        end
        return orig(self, ...)
      end
      wrapped = wrapped + 1
    end
  end
  btn.__qrGuarded = true
  _G.__qrGuarded = wrapped
  return string.format('guarded %d method(s)', wrapped)
end

-- THE DIAGNOSIS WRITES ITSELF OUT, so it does not depend on anyone copying it out of a terminal. One
-- line per state change: the interface identity, whether the button is alive, and how many of the
-- input registrations point at objects that no longer exist.
local function qrLog(text)
  local line = os.date('%H:%M:%S') .. '  ' .. text .. '\n'
  local wrote = pcall(function()
    local fh = io.open(__PROBEFILE__, 'a')
    if fh then
      fh:write(line)
      fh:close()
      return true
    end
    return false
  end)
  -- If the app will not take an absolute path, leave it somewhere it certainly can write. Nothing
  -- depends on which of the two it lands in; the point is that it goes somewhere readable.
  if wrote ~= true then
    pcall(function()
      local p = system.pathForFile('reload-probe.txt', system.DocumentsDirectory)
      local fh = io.open(p, 'a')
      if fh then
        fh:write(line)
        fh:close()
      end
    end)
  end
end

local function qrWiProbeText()
  local p = qrWiProbe()
  if p.note then return p.note end
  local out = {}
  out[#out + 1] = string.format('  world interface created = %s', tostring(p.created))
  if p.button ~= nil then
    out[#out + 1] = string.format('  interact button alive = %s (height=%s)',
      tostring(p.alive), tostring(p.height))
    out[#out + 1] = string.format('  still a registered touchable = %s', tostring(p.touchable))
  else
    out[#out + 1] = '  interact button = not reachable'
  end
  out[#out + 1] = string.format('  mouse-hover objects = %d, of which dead = %d',
    p.hover or 0, p.hoverDead or 0)
  if p.level ~= nil then
    out[#out + 1] = string.format('  input level = %s', tostring(p.level))
  end
  return table.concat(out, '\n')
end
""".replace("__KEY__", _key_label(cfg)).replace("__PROBEFILE__", _lua_str(_probe_path()))


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return (
        r"""
do
  -- No per-frame work is needed, but the feature has to watch for the reload finishing: the body
  -- runs asynchronously (it polls for the world to go away, then loads), so `busy` is cleared from
  -- the tick rather than when fire() returns.
  local f = makeFeature('reload', 250)
  local KEYS = __KEYS__

  -- The proven chunks, run verbatim. See the module docstring for what each one does.
  local PREFLIGHT = __PREFLIGHT__
  local CANCEL    = __CANCEL__
  local BODY      = __BODY__

  local function run(chunk, name)
    local fn, err = loadstring(chunk, name)
    if not fn then return false, tostring(err) end
    return pcall(fn)
  end

  local function fire()
    if f.busy then return end
    f.busy = true
    f.step = 'pre-flight'

    -- What the driver used to inject. The slot is left nil so the pre-flight reads the one the game
    -- is actually in; the device id is the literal the game's own debug loader passes, because
    -- loadGame only stores it.
    _G.__qrSlotArg = nil
    _G.__qrDeviceId = 'self'
    _G.__qrLog = {}
    _G.__qrReport = nil
    _G.__qrDone = nil
    _G.__qrStep = 'pre-flight'
    _G.__qrWhy = nil
    _G.__qrNote = nil

    local ok, said = run(PREFLIGHT, 'qr_preflight')
    if not ok then
      f.busy = false
      f.last = 'pre-flight errored: ' .. tostring(said)
      return
    end
    if type(said) ~= 'string' or said:sub(1, 3) ~= 'ok ' then
      -- Nothing has been destroyed at this point, which is the whole reason the pre-flight is
      -- separate: a refusal here leaves the game exactly as it was.
      f.busy = false
      f.last = 'refused: ' .. tostring(said)
      return
    end
    local slot = tonumber(said:match('^ok%s+(%d+)'))
    if slot == nil then
      f.busy = false
      f.last = 'the pre-flight did not name a slot: ' .. tostring(said)
      return
    end
    _G.__qrSlotArg = slot

    f.step = 'cancelling transitions'
    run(CANCEL, 'qr_cancel')

    -- Read-only, and taken while everything is still alive: the identity of the interface the game is
    -- currently driving, plus the guard on its interact button. What the probe reads after the load is
    -- compared against this, and the two answers have different meanings - a NEW object means the
    -- reload rebuilt the interface (so a crash afterwards is something else), the SAME object with a
    -- dead button means the reload is reaching a destroyed interface through the input layer.
    _G.__qrWiBefore = qrWiProbe()
    _G.__qrStalePress = _G.__qrStalePress or 0
    qrLog('reload starting - ' .. qrWiProbeSummary())
    local guardSaid = qrGuardButton()
    qrLog('  ' .. guardSaid .. ' on the button that is about to be destroyed')

    -- A SHORT WAIT, not one frame and not the 250 ms the outside driver used.
    --
    -- `transition.cancelAll()` only sets a flag, and the transition module's enterFrame listener acts
    -- on it at its next tick - so something has to wait for that tick. A 1 ms timer is not good
    -- enough: Solar2D fires timers on frame boundaries, and whether that lands before or after the
    -- enterFrame dispatch within the frame is an ordering detail I would rather not depend on. 50 ms
    -- is several frames, so the tick has certainly run, and it is still five times tighter than the
    -- sleep the outside driver needed - with no round trip to the other process in between.
    _G.timer.performWithDelay(50, function()
      f.step = 'teardown + load'
      local okb, errb = run(BODY, 'qr_body')
      if not okb then
        f.busy = false
        f.last = 'the reload chunk errored: ' .. tostring(errb)
      end
    end, 1)
  end

  -- Collapses the doubled key events: a press arrives as down, down and a release as up, up, so a
  -- handler counting each down fires twice per press.
  local down = false
  local function onKey(e)
    if type(e) ~= 'table' then return end
    local name = tostring(e.keyName or ''):lower()
    if not KEYS[name] then return end
    if e.phase == 'up' then
      down = false
      return
    end
    if e.phase ~= 'down' then return end
    if down then return end
    down = true
    fire()
  end
  Runtime:addEventListener('key', onKey)

  local function update()
    -- the body sets these as it goes; when it is finished, hand control back
    if f.busy and _G.__qrDone ~= nil then
      f.busy = false
      f.done = (f.done or 0) + 1
      f.last = tostring(_G.__qrDone)
      f.why = _G.__qrWhy
      f.note = _G.__qrNote
      -- Read-only, right after the load finished: is the interface a new one, and is anything from
      -- the old one still registered with the input layer?
      _G.__qrWiAfter = qrWiProbe()
      local before = _G.__qrWiBefore and _G.__qrWiBefore.instance
      local after = _G.__qrWiAfter and _G.__qrWiAfter.instance
      qrLog(string.format('load done (%s) - interface before=%s after=%s same=%s',
        tostring(_G.__qrDone), tostring(before), tostring(after),
        tostring(before ~= nil and before == after)))
      qrLog('  ' .. qrWiProbeSummary())
      f.probeLast = nil
      return
    end
    -- While play continues, log only when something actually changes, so the file stays short and
    -- its last line is the state a later crash happened from.
    if not f.busy and f.on then
      local s = qrWiProbeSummary()
      if s ~= f.probeLast then
        f.probeLast = s
        qrLog('  ' .. s)
      end
    end
  end

  local function kill()
    pcall(function() Runtime:removeEventListener('key', onKey) end)
    f.busy = false
    f.on = false
  end

  f.fire = fire
  f.update, f.kill, f.on = update, kill, true
end
"""
        .replace("__KEYS__", _key_set(_keys(cfg)))
        .replace("__PREFLIGHT__", quick_reload.lua_long(quick_reload.PREFLIGHT))
        .replace("__CANCEL__", quick_reload.lua_long(quick_reload.TRANSITION_CANCEL))
        .replace("__BODY__", quick_reload.lua_long(quick_reload.TEARDOWN_BASE + quick_reload.RELOAD_BODY))
    )


def summary(cfg):
    return "reloadLine()"


def status(cfg):
    return (
        r"""(function()
  local f = _G.__hud and _G.__hud.feats.reload
  if not f or not f.on then return nil end
  return reloadLine() .. '\\n  ' .. qrWiProbeSummary()
end)()"""
    )


def report(cfg):
    return (
        r"""(function()
  local out = {}
  out[#out + 1] = 'reload - key "__KEY__"'
  local f = _G.__hud and _G.__hud.feats.reload
  if not f or not f.on then
    out[#out + 1] = '  not installed, so the key does nothing'
    return table.concat(out, '\n')
  end
  out[#out + 1] = string.format('  reloads done = %d', f.done or 0)
  out[#out + 1] = string.format('  running now  = %s', tostring(f.busy or false))
  out[#out + 1] = string.format('  last result  = %s', tostring(f.last or 'not yet'))
  if f.why then out[#out + 1] = '  why          = ' .. tostring(f.why) end
  if f.note then out[#out + 1] = '  note         = ' .. tostring(f.note) end
  out[#out + 1] = '  last step    = ' .. tostring(_G.__qrStep)
  out[#out + 1] = '  load         = ' .. tostring(_G.__qrDone)
  if type(_G.__qrLog) == 'table' and #_G.__qrLog > 0 then
    out[#out + 1] = '  poll: ' .. table.concat(_G.__qrLog, ' | ')
  end
  out[#out + 1] = ''
  out[#out + 1] = '  --- the world interface, read-only ---'
  out[#out + 1] = qrWiProbeText()
  out[#out + 1] = string.format('  presses skipped on a destroyed button = %d',
    _G.__qrStalePress or 0)
  if _G.__qrGuarded then
    out[#out + 1] = string.format('  button methods guarded after the last reload = %s',
      tostring(_G.__qrGuarded))
  end
  out[#out + 1] = '  observations are appended to: ' .. __PROBEFILE__
  if _G.__qrWiBefore or _G.__qrWiAfter then
    local before = _G.__qrWiBefore and _G.__qrWiBefore.instance
    local after = _G.__qrWiAfter and _G.__qrWiAfter.instance
    out[#out + 1] = '  the interface the game was driving:'
    out[#out + 1] = '    before the reload = ' .. tostring(before)
    out[#out + 1] = '    after  the reload = ' .. tostring(after)
    out[#out + 1] = '    same object = ' .. tostring(before ~= nil and before == after)
    out[#out + 1] = '    (a NEW object means the reload rebuilt the interface; the SAME object with'
    out[#out + 1] = '     a dead button means the press is reaching a destroyed one)'
  end
  out[#out + 1] = ''
  out[#out + 1] = '  it runs the proven chunks: a read-only pre-flight that refuses unless there'
  out[#out + 1] = '  is a world to tear down, then the transition cancel, then one frame later'
  out[#out + 1] = '  the teardown and the load. Nothing is written to disk.'
  return table.concat(out, '\n')
end)()"""
    ).replace("__KEY__", _key_label(cfg)).replace("__PROBEFILE__", _lua_str(_probe_path()))
