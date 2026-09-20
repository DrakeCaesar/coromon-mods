#!/usr/bin/env python3
"""
sprint.py - a button that latches running on and off, without changing the run button.

Coromon gives you two ways to run, and both need a finger on the button:

    Automatically Run OFF   hold the run button to run, release to walk
    Automatically Run ON    run by default, and hold the button to walk instead

The first version of this latched the RUN button itself, and that turned out to be the wrong
button to take. The run button is also the interact button, so every conversation toggled the
state, and - worse - holding it to run (the habit the game taught) toggled it again every
time. A latch on a button you are meant to hold cannot work.

So the run button keeps its normal behaviour and a SEPARATE button latches instead:

    run button, held        exactly as the game always did
    latch button, pressed   latching on makes you run; off hands control back to the game

The latch is OR'd with the game's own answer, which is what keeps the run button working:

    sp.shouldRun = function(self, ...)
      if latch then return true end
      return orig(self, ...)        -- the game's rule, untouched
    end

Consequences worth stating, because they are why it is built this way:

  * the Automatically Run setting is NOT written. It is saved state the player set on
    purpose, and changing it would persist this mod's choice into their save file.
  * only the PLAYER's character is wrapped, so NPCs and every other spawnable are unaffected.
  * with the latch off, nothing about the game is altered at all - the wrapper just forwards.

Detecting a PRESS, not a hold, and the doubling. Measured from the running game, EVERY key
event is delivered twice - a press arrives as `down, down` and a release as `up, up`:

    buttonB/down  buttonB/down  buttonB/up  buttonB/up   (repeating)

So a handler that toggles on each `down` cancels itself out, and one that tracks only the
phase gets two of everything. The handler below collapses a pair by tracking the key's own
state: a `down` counts only when the key is not already down. That makes a press exactly one
toggle whether the doubling is there or not, and it does not depend on `isRepeat` - which
does not exist in this build (measured: nil on every event).

WHAT A BUTTON IS CALLED. Solar2D reports a controller button as `buttonA`, `buttonB`,
`button9` and so on, and a keyboard key as the character it produces (`a`, `space`, `up`).
This defaults to `buttonB` because that is the pad's B; names are matched case-insensitively
and the setting takes a list, so a keyboard key can be added alongside.
"""

NAME = "sprint"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "add a button that latches running on and off"),
    (
        "key",
        ["buttonB"],
        "what latches running. `buttonB` is the controller's B; `buttonA` and `button9` and "
        "friends are the others, and a keyboard key is named by the character it produces "
        "(`a`, `space`, `up`). Add more entries to have more than one; names are matched "
        "case-insensitively. Deliberately NOT the run button - that one is also interact, "
        "and latching a button you are meant to hold fights the game",
    ),
    (
        "start_sprinting",
        False,
        "start out latched on (running) rather than handing control to the game",
    ),
]


def _lua_str(value):
    """A Lua string literal for a setting coming out of overlays.toml."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _keys(cfg):
    """The setting as a clean list of names."""
    keys = cfg["key"]
    if isinstance(keys, str):
        keys = [keys]
    keys = [str(k).strip() for k in keys if str(k).strip()]
    return keys or ["buttonB"]


def _key_set(keys):
    """The names as a Lua lookup table, lower-cased.

    Normalised here rather than at the comparison: Solar2D reports `buttonB`, so a table of
    the names as written would never match a lower-cased event name.
    """
    return "{" + ", ".join("[%s] = true" % _lua_str(k.lower()) for k in keys) + "}"


def _key_label(cfg):
    return ", ".join(_keys(cfg))


def lua(cfg):
    """The queries. Always part of the chunk, so --status and --report can describe the state
    whether or not the feature is installed."""
    return r"""
-- The player's own character. NOT core's playerSprite(), which unwraps to the .sprite: the
-- method being wrapped lives on the character itself.
local function playerCharacter()
  local ok, sp = pcall(function() return spawnableHelper:getPlayerSpawnable() end)
  if ok and type(sp) == 'table' then return sp end
end

-- What the game itself currently answers. `ok` is tested separately rather than with
-- `ok and v or nil`: shouldRun legitimately returns false, and that idiom would report a
-- successful false as a failure.
local function sprintGameSays()
  local sp = playerCharacter()
  if not sp or type(sp.shouldRun) ~= 'function' then return nil end
  local ok, v = pcall(function() return sp:shouldRun() end)
  if not ok then return nil end
  return v and true or false
end
"""


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return (
        r"""
do
  local f = makeFeature('sprint', 250)
  local KEYS = __KEYS__           -- lower-cased, matched against e.keyName
  local latch = __START__         -- true = forcing a run

  -- Wrap the player's answer to "should I be running", OR-ing the latch over the game's own
  -- rule. The original is called whenever the latch is off, which is what leaves the run
  -- button working exactly as before.
  local function wrap()
    local sp = playerCharacter()
    if not sp or type(sp.shouldRun) ~= 'function' then return end
    if sp.__hudSprint then return end                 -- already ours
    local orig = sp.shouldRun
    sp.__hudSprintOrig = orig
    sp.__hudSprint = true
    sp.shouldRun = function(self, ...)
      if latch then return true end
      return orig(self, ...)
    end
  end

  local function unwrap()
    local sp = playerCharacter()
    if not sp or not sp.__hudSprint then return end
    if type(sp.__hudSprintOrig) == 'function' then sp.shouldRun = sp.__hudSprintOrig end
    sp.__hudSprint, sp.__hudSprintOrig = nil, nil
  end

  -- Every event arrives twice (measured), so the key's own state is tracked: a down counts
  -- only when the key is not already down. One press, one toggle - with or without the
  -- doubling, and without depending on `isRepeat`, which this build does not provide.
  local down = false
  local function onKey(e)
    local h = _G.__hud
    if not h or h.sprintBody ~= onKey or not f.on or type(e) ~= 'table' then return end
    local k = tostring(e.keyName or '')
    if not KEYS[k:lower()] then return end
    if e.phase == 'up' then down = false return end
    if e.phase ~= 'down' then return end
    if down then return end                            -- the second half of the pair
    down = true
    latch = not latch
  end

  local function update()
    if not f.on then return end
    wrap()
  end

  local function kill()
    pcall(function() Runtime:removeEventListener('key', onKey) end)
    unwrap()
  end

  wrap()
  Runtime:addEventListener('key', onKey)
  -- Its OWN guard field. core's zoom feature owns __hud.keyBody, and it treats a changed
  -- keyBody as "a newer install replaced me, stop"; writing that field here would silently
  -- disable the zoom keys.
  local H = _G.__hud
  if H then H.sprintBody = onKey end
  f.update, f.kill, f.on = update, kill, true
end
""".replace("__KEYS__", _key_set(_keys(cfg)))
        .replace("__START__", "true" if cfg["start_sprinting"] else "false")
    )


def _state(cfg):
    """Lua expression -> the latch's state, in the game's own vocabulary."""
    return r"""(function()
  local sp = playerCharacter()
  if not sp then return 'unavailable' end
  if sp.__hudLatch == nil and sp.__hudSprint == nil then return 'unavailable' end
  return sprintGameSays() and 'running' or 'walking'
end)()"""


def summary(cfg):
    return (
        r"""(function()
  local s = __STATE__
  if s == 'unavailable' then
    return 'run latch on "__KEY__" (waiting for the overworld)'
  end
  return 'run latch on "__KEY__" (' .. s .. ')'
end)()""".replace("__STATE__", _state(cfg)).replace("__KEY__", _key_label(cfg))
    )


def status(cfg):
    return (
        r"""(function()
  local h = _G.__hud
  local f = h and h.feats.sprint
  if not f or not f.on then return nil end
  return 'run latch: __KEY__ toggles, currently ' .. __STATE__
end)()""".replace("__STATE__", _state(cfg)).replace("__KEY__", _key_label(cfg))
    )


def report(cfg):
    return r"""(function()
  local out = {}
  local sp = playerCharacter()
  out[#out + 1] = string.format('run latch - key "%s"', '__KEY__')
  if not sp then
    out[#out + 1] = '  the player character is not loaded (not in the overworld?)'
    return table.concat(out, '\n')
  end
  out[#out + 1] = string.format('  the wrapper is installed = %s', tostring(sp.__hudSprint == true))
  out[#out + 1] = string.format('  shouldRun() currently answers = %s', tostring(sprintGameSays()))
  local gmo = sp.gridMoveObject
  if type(gmo) == 'table' and gmo.speed ~= nil then
    out[#out + 1] = string.format('  current move speed = %s', tostring(gmo.speed))
  end
  local ok, v = pcall(function() return gameSettings.getAutomaticallyRunSetting() end)
  out[#out + 1] = string.format('  the game\'s Automatically Run setting = %s (never written '
    .. 'by this; with the latch off its rule is passed straight through)',
    ok and tostring(v) or '?')
  out[#out + 1] = '  speeds: fast=133ms  normal=280ms  slow=400ms  per tile'
  return table.concat(out, '\n')
end)()""".replace("__KEY__", _key_label(cfg))
