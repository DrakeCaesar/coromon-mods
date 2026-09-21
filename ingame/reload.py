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
""".replace("__KEY__", _key_label(cfg))


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
  return reloadLine()
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
  out[#out + 1] = '  it runs the proven chunks: a read-only pre-flight that refuses unless there'
  out[#out + 1] = '  is a world to tear down, then the transition cancel, then one frame later'
  out[#out + 1] = '  the teardown and the load. Nothing is written to disk.'
  return table.concat(out, '\n')
end)()"""
    ).replace("__KEY__", _key_label(cfg))
