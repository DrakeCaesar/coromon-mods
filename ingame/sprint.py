#!/usr/bin/env python3
"""
sprint.py - one button that swaps the game's two running modes.

Coromon has exactly two ways to run, and the Automatically Run option picks between them:

    mode 1 (the game's default)   hold the run button to run, release to walk
    mode 2                        run by default, and hold the button to walk instead

This puts a button on swapping them, so a long walk is: press once, running; press again, back
to normal - and the run button reverses its meaning along with the mode, because that is what
the two modes are.

WHY THE MODES ARE EMULATED rather than the game's setting simply flipped. Flipping it was the
first idea and it does not work: `gameSettings.setAutomaticallyRunSetting(true)` reports success
and changes nothing. Measured - after the call the getter still reads nil two seconds later,
so it only takes effect from inside the options screen. The mode is therefore held here and
imposed on the player's own decision, which also means **nothing is written to the save file**.

HOW THE TWO MODES ARE PRODUCED, and the one piece of reasoning it rests on. The game's answer
under its own setting is `shouldRun()`, and that single boolean means different things
depending on that setting:

    Automatically Run OFF   shouldRun() == the run button is held
    Automatically Run ON    shouldRun() == the run button is NOT held

So the button's real state is recoverable from the game's own answer plus its setting, without
reading the input layer at all:

    held = (setting == ON) and not gameSays or gameSays

and then the wanted mode decides what to do with it. With mode 1 selected this returns exactly
what the game returned, so the game is unmodified until the button is pressed - the wrapper
forwards rather than guesses.

Two earlier attempts are worth recording, because both failed for a reason:

  * latching the RUN button. It is also the interact button, and it is a button you are meant
    to hold - so every conversation toggled the state, and so did every run.
  * assuming the setting could be flipped. See above; it cannot, from here.

Detecting a PRESS, not a hold. Measured from the running game, EVERY key event is delivered
twice - a press arrives as `down, down` and a release as `up, up`:

    buttonB/down  buttonB/down  buttonB/up  buttonB/up   (repeating)

So a handler that toggles on each `down` cancels itself out, and one that counts phases gets
double of everything. The handler below collapses the pair by tracking the key's own state: a
`down` counts only when the key is not already down. That gives exactly one toggle per press
whether or not the doubling is there, and it does not depend on `isRepeat` - which this build
does not provide (measured: nil on every event).

WHAT A BUTTON IS CALLED. Solar2D reports a controller button as `buttonA`, `buttonB`,
`button9` and so on, and a keyboard key as the character it produces (`a`, `space`, `up`).
Names are matched case-insensitively, and the setting takes a list so a keyboard key can be
added alongside the controller's.
"""

NAME = "sprint"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "add a button that swaps the two running modes"),
    (
        "key",
        ["leftJoystickButton"],
        "what swaps them. `leftJoystickButton` is pressing the left stick in (L3) - that is "
        "the name Solar2D reports, measured rather than guessed: it is not a number like the "
        "`button1`..`button10` range, and the game's own button tables do not list it at "
        "all. `buttonA`, `buttonB`, `buttonX`, `buttonY`, `buttonZ` and `buttonStart` are "
        "the others, and a keyboard key is named by the character it produces (`a`, "
        "`space`, `up`). Add more entries to have more than one; names are matched "
        "case-insensitively. Deliberately NOT the run button - that one is also interact, "
        "and it is meant to be held",
    ),
    (
        "start_running",
        False,
        "start in mode 2 (run by default) rather than the game's own mode 1",
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
    """The queries. Always part of the chunk, so --status and --report can describe the mode
    whether or not the feature is installed."""
    return r"""
-- The player's own character. NOT core's playerSprite(), which unwraps to the .sprite: the
-- method being wrapped lives on the character itself.
local function playerCharacter()
  local ok, sp = pcall(function() return spawnableHelper:getPlayerSpawnable() end)
  if ok and type(sp) == 'table' then return sp end
end

-- Whether the game's own setting says "run by default". It reads nil when unset, and nil and
-- false mean the same thing, so this normalises rather than passing nil around. `ok` is
-- tested on its own: `ok and v or nil` reports a successful nil as nothing at all.
local function sprintGameAuto()
  local ok, v = pcall(function() return gameSettings.getAutomaticallyRunSetting() end)
  if not ok then return false end
  return v == true
end

-- Our selected mode, as the feature holds it. nil when the feature is not installed.
local function sprintMode()
  local h = _G.__hud
  local f = h and h.feats and h.feats.sprint
  if not f then return nil end
  return f.auto and true or false
end

local function sprintModeText(auto)
  if auto == nil then return 'unknown' end
  if auto then return 'run by default (the run button walks)' end
  return 'hold to run (the run button runs)'
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
  f.auto = __START__              -- false = the game's own mode, true = run by default

  -- Wrap the player's answer to "should I be running".
  --
  -- The game's answer means different things under its two settings, so the run button's real
  -- state is recovered from it rather than read from the input layer:
  --     setting OFF -> gameSays == held     setting ON -> gameSays == not held
  -- With our mode 1 selected this returns exactly `gameSays`, so the game behaves as it always
  -- did until the mode is swapped.
  local function wrap()
    local sp = playerCharacter()
    if not sp or type(sp.shouldRun) ~= 'function' then return end
    if sp.__hudSprint then return end                 -- already ours
    local orig = sp.shouldRun
    sp.__hudSprintOrig = orig
    sp.__hudSprint = true
    sp.shouldRun = function(self, ...)
      -- The arguments are repacked rather than forwarded straight through. In Lua 5.1 `...` is
      -- not visible inside a nested function that is not itself vararg, so the obvious
      -- `pcall(function() return orig(self, ...) end)` does not compile at all - it fails the
      -- whole chunk with "cannot use '...' outside a vararg function". That error is silent at
      -- the call site: the install simply does not happen and any older version stays running.
      local argc = select('#', ...)
      local argv = { ... }
      local ok, gameSays = pcall(function() return orig(self, unpack(argv, 1, argc)) end)
      if not ok then return f.auto end
      local said = gameSays and true or false
      local held
      if sprintGameAuto() then held = not said else held = said end
      if f.auto then return not held end
      return held
    end
  end

  local function unwrap()
    local sp = playerCharacter()
    if not sp or not sp.__hudSprint then return end
    if type(sp.__hudSprintOrig) == 'function' then sp.shouldRun = sp.__hudSprintOrig end
    sp.__hudSprint, sp.__hudSprintOrig = nil, nil
  end

  -- Every event arrives twice (measured), so the key's own state is tracked: a down counts
  -- only when the key is not already down. One press, one swap - with or without the
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
    f.auto = not f.auto
    f.swaps = (f.swaps or 0) + 1
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
  f.swaps = 0
  f.update, f.kill, f.on = update, kill, true
end
""".replace("__KEYS__", _key_set(_keys(cfg)))
        .replace("__START__", "true" if cfg["start_running"] else "false")
    )


def summary(cfg):
    return (
        r"""(function()
  local auto = sprintMode()
  if auto == nil then return 'run-mode button on "__KEY__"' end
  return 'run-mode button on "__KEY__" (' .. sprintModeText(auto) .. ')'
end)()""".replace("__KEY__", _key_label(cfg))
    )


def status(cfg):
    return (
        r"""(function()
  local f = _G.__hud and _G.__hud.feats.sprint
  if not f or not f.on then return nil end
  return string.format('run-mode button: __KEY__ swaps it, %d swap(s) so far; %s',
    f.swaps or 0, sprintModeText(f.auto))
end)()""".replace("__KEY__", _key_label(cfg))
    )


def report(cfg):
    return r"""(function()
  local out = {}
  out[#out + 1] = string.format('run-mode button - key "%s"', '__KEY__')
  local f = _G.__hud and _G.__hud.feats.sprint
  if not f then
    out[#out + 1] = '  not installed, so the game runs its own mode and nothing is wrapped'
  else
    out[#out + 1] = string.format('  our mode = %s', sprintModeText(f.auto))
    out[#out + 1] = string.format('  swaps since install = %d', f.swaps or 0)
  end
  local sp = playerCharacter()
  if sp then
    out[#out + 1] = string.format('  the wrapper is installed = %s',
      tostring(sp.__hudSprint == true))
    if type(sp.shouldRun) == 'function' then
      local ok, v = pcall(function() return sp:shouldRun() end)
      out[#out + 1] = string.format('  shouldRun() answers = %s', ok and tostring(v) or 'ERROR')
    end
  end
  out[#out + 1] = string.format("  the game's own Automatically Run setting = %s",
    tostring(sprintGameAuto()))
  out[#out + 1] = '  that setting is only READ - flipping it does not work from outside the'
  out[#out + 1] = '  options screen (measured), and it is saved state, so nothing writes it.'
  out[#out + 1] = '  speeds: fast=133ms  normal=280ms  slow=400ms  per tile'
  return table.concat(out, '\n')
end)()""".replace("__KEY__", _key_label(cfg))
