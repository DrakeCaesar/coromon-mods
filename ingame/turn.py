#!/usr/bin/env python3
"""
turn.py - hold a button and a direction to TURN ON THE SPOT instead of walking.

Coromon's overworld is grid-based: the character walks a whole 16 px tile at a time and only
ever faces one of four ways. Tapping a direction you are already facing does nothing, and a
direction you are not facing both turns you AND commits you to a step. There is no way to look
around without walking, which matters whenever the thing you want to look at is the thing you
do not want to step on (an NPC you are lining up a conversation with, a ledge, a one-way tile).

This puts that back: while the button is held, a held direction only turns.

HOW IT IS DONE, and why it is this seam. The player's per-frame input handler is
`receiveInput(self, direction)` - installed on the character by
`classes.spawnables.plugins.inputReceivingMovingSpawnablePlugin`, which `mainCharacter` requires.
Everything that starts a step lives inside it:

    if not direction then
      self:setNextStepIsEmpty()                  -- nothing held: nothing to step onto
      ...                                        -- and re-assert the facing
    else
      ...
      if amountOfFramesHoldingSameDirection >= 4 or self.direction ~= direction then
        self:setNextStep(direction, self:shouldRun() and 'fast' or 'normal')
        self:startGridMove()
      end
    end

so replacing that one method replaces the walking decision and nothing else. The sprite, the
collision shapes, the grid, the step timing and the input layer are all untouched, and the
replacement never calls `setNextStep` - which is the only thing that queues a tile step anywhere
in the game (a whole-game scan: the input plugin is its only caller). Turning is then the game's
own turn, `self:rotate(direction)` from `rotatableSpawnablePlugin`, which is what every NPC uses
to face you.

WHAT WAS MEASURED ON THE LIVE GAME, rather than assumed:

  * the seam is per-frame. worldInterface registers
    `worldHelper:addOnUpdateFunction(function() playerSpawnable:receiveInput(directionalMovementNonFake) end)`,
    and a counting wrapper around the live player saw 81 calls in a second at the game's own
    55 fps. The direction is what the stick, the d-pad and the keyboard are all resolved to,
    by one resolver, so this covers all three without knowing anything about any of them.
  * `sp:rotate('left')` changes `sp.direction` from 'down' to 'left' AND `sp.sprite.sequence`
    from 'down' to 'left', while `sp.currentTileX/currentTileY` do not move. That is the whole
    feature, one call.
  * `sp:isMoving` is NIL when standing, not false, so it has to be used as a plain truth test.
  * `sp.receiveInput` is a plain function on the character (not a class method), so assigning
    over it sticks - but NOT for ever: the plugin installs it per spawnable, so a rebuilt player
    gets a fresh copy of the game's own handler and the wrap disappears. Measured live: after
    wrapping, the character read back the plugin's own closure again while our marker field still
    said we were wrapped, which is why the decision to re-wrap is now made from the identity of
    `receiveInput` itself and never from a marker.
  * the walk condition is `amountOfFramesHoldingSameDirection >= 4 or self.direction ==
    direction` - NOT `~=`. Measured against the game's own handler: facing right, one call with
    'left' does nothing at all (no step, no turn), while one call with 'right' sets `isMoving`
    at once. So a direction you are not facing only starts a step once it has been held for four
    frames, which is why reversing walks after a short beat and why this feature must intercept
    the call rather than just feed it directions.

WHAT IS DELIBERATELY NOT DONE:

  * no key listener and no held-state of our own. The button is polled through
    `inputHelper:isButtonActuallyPressed(name)`, the game's own layer, so the answer follows the
    player's in-game remapping and the device the game has resolved - and it cannot go stale the
    way a listener's own flag can (a key-up missed while the window has no focus would leave a
    listener believing the button is still down, which here would mean never walking again).
  * the button is a LOGICAL game button, not the pad's key name. `BACK` is what the controller's
    B is bound to, verified in `classes.configs.defaultInputDeviceConfigs`, which calls
    `setButton('BACK', 'buttonB')` for the standard pad. So the setting is a name the game knows,
    and a re-bound controller keeps working with no change here.
  * nothing is written to the game's saved state, and nothing is left behind: the original method
    is kept on the character and put back by `kill()`.

ONE NUANCE, and it is the game's own behaviour rather than a limitation here. A step already in
flight cannot be interrupted - the character is mid-tile - so the turn happens as soon as it
lands. `setNextStepIsEmpty()` stops the queued step that would otherwise have followed, so the
walk ends at the end of the tile it is on rather than one tile later.
"""

NAME = "turn"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    (
        "enabled",
        True,
        "hold a button together with a direction to face that way without taking a step",
    ),
    (
        "button",
        "BACK",
        "which button does it. This is the game's own button name, not the pad's key name, so "
        "it follows the controller mapping the game itself resolved. `BACK` IS the controller's "
        "B: the default pad config binds it with `setButton('BACK', 'buttonB')`. The others are "
        "`USE` (A), `EXTRA_1` (X), `EXTRA_2` (Y), `START` (menu), `LEFT_SHOULDER`, "
        "`RIGHT_SHOULDER`, and the four directions. In the overworld BACK does nothing else, "
        "which is why it is the default",
    ),
]


def _lua_str(value):
    """A Lua string literal for a setting coming out of overlays.toml."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def lua(cfg):
    """The queries. Always part of the chunk, so --status and --report can describe the
    feature whether or not it is installed."""
    return r"""
-- The player's own character. NOT core's playerSprite(), which unwraps to the .sprite: the
-- method this feature wraps lives on the character itself.
local function turnPlayer()
  local ok, sp = pcall(function() return spawnableHelper:getPlayerSpawnable() end)
  if ok and type(sp) == 'table' then return sp end
end

-- Is the button down right now, according to the GAME's own input layer rather than a listener
-- of our own. See the module docstring: the answer follows the player's remapping, cannot go
-- stale, and `BACK` is the controller's B.
--
-- The getter answers the button's UID (truthy) while it is down and NIL while it is not, so nil
-- and false are both "up" and everything else is "down". It is a plain table lookup inside
-- `inputHelper` (`pressedButtonUIDsNonFake[name]`), so polling it costs nothing.
local function turnHeld()
  local ok, v = pcall(function() return inputHelper:isButtonActuallyPressed(__BUTTON__) end)
  if not ok or v == nil or v == false then return false end
  return true
end

-- Our feature table, or nil when the feature is not installed.
local function turnFeature()
  local h = _G.__hud
  local f = h and h.feats and h.feats.turn
  if not f then return nil end
  return f
end

-- Whatever the game had before us. This is nil only when a newer install has already unwrapped
-- the player, so "no original" has to mean "do nothing at all" rather than "walk normally":
-- silently walking is the opposite of what the button asked for.
local function turnPass(orig, self, direction)
  if type(orig) == 'function' then return orig(self, direction) end
end

-- The LOGICAL button names the game currently has down, read out of inputHelper's own table
-- (`pressedButtonUIDsNonFake`, which is upvalue 1 of the getter above, reached the same way the
-- reload tool reads the input level). --report uses it to turn "my B button does nothing" into
-- "the game reports this button as ...", which is the difference between a guess and an answer.
local function turnPressedNames()
  local names = {}
  local ok, t = pcall(function()
    local _, v = debug.getupvalue(inputHelper.isButtonActuallyPressed, 1)
    return v
  end)
  if not ok or type(t) ~= 'table' then return names end
  for k in pairs(t) do names[#names + 1] = tostring(k) end
  table.sort(names)
  return names
end
""".replace("__BUTTON__", _lua_str(cfg["button"]))


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return r"""
do
  -- Period 200: this feature does not need a per-frame slot. The turning happens inside the
  -- wrapped method, which the game already calls once a frame; this poll only exists to put the
  -- wrap back on a player that has been rebuilt.
  local f = makeFeature('turn', 200)
  f.button = __BUTTON__
  f.turns, f.seen, f.wraps = 0, 0, 0

  -- THE SEAM. `receiveInput(self, direction)` is the player's per-frame input handler and the
  -- game calls it with the direction being held, or nil. Everything that starts a step is
  -- inside it, so this replaces the walking decision and nothing else.
  --
  -- While the button is held this does exactly what the game itself does when NO direction is
  -- held, plus one turn:
  --
  --  1. `setNextStepIsEmpty()` is that released-direction call. It clears the one queued step
  --     (`self.gridMoveStack[1] = nil`), which is what makes the walk end on the tile in flight
  --     rather than one tile later. Only the input plugin ever writes that slot (whole-game
  --     scan), so clearing it cannot disturb a scripted move.
  --  2. `self:rotate(direction)` is the game's own turn (rotatableSpawnablePlugin - the same
  --     call every NPC faces you with). It sets `self.direction`, re-picks the standing sequence
  --     for that facing, moves the shadow and dispatches `onSpawnableRotated`. Measured live:
  --     `sp:rotate('left')` takes `sp.direction` and `sp.sprite.sequence` from 'down' to 'left'
  --     and moves nothing. Skipped while a step is in flight, exactly as the game skips it.
  --
  -- It re-checks the button on every call rather than trusting a flag the listener set, so the
  -- state is the game's, and it re-reads the original from the character on every call, so a
  -- re-install that has already unwrapped us is forwarded to instead of swallowed.
  local function wrapper(self, direction)
    local orig = self.__hudTurnOrig
    if not f.on or direction == nil then return turnPass(orig, self, direction) end
    f.seen = f.seen + 1
    f.lastSeen = direction
    if not turnHeld() then return turnPass(orig, self, direction) end
    -- Sitting on something (the surfboard, the minecart): the mount receives the input itself.
    if self.mountedObject then return turnPass(orig, self, direction) end
    -- A cutscene, a dialogue or a scripted walk is driving the character, so do not touch the
    -- facing. The game's own handler refuses on exactly this answer, one line into its body.
    if type(self.isIgnoringPlayerInput) == 'function' then
      local ok, ignore = pcall(self.isIgnoringPlayerInput, self)
      if ok and ignore ~= nil and ignore ~= false then
        return turnPass(orig, self, direction)
      end
    end

    f.turns = f.turns + 1
    f.lastTurn = direction

    if type(self.setNextStepIsEmpty) == 'function' then
      pcall(self.setNextStepIsEmpty, self)
    end
    if not self.isMoving and type(self.rotate) == 'function' then
      local ok, err = pcall(self.rotate, self, direction)
      if not ok then f.err = tostring(err) end
    end
  end

  -- Re-applied rather than applied once. `receiveInput` is an INSTANCE field written by
  -- `inputReceivingMovingSpawnablePlugin.install`, and that install runs again for a spawnable
  -- that is rebuilt - the player character gets a fresh copy of the game's own handler, and the
  -- wrap is gone. Measured live: after wrapping, the character's `receiveInput` was the plugin's
  -- own closure again while our marker field still said otherwise, so the decision here is made
  -- from what is ON the character right now and never from a marker.
  local function wrap()
    if not f.on then return end
    local sp = turnPlayer()
    if not sp then return end
    local cur = sp.receiveInput
    if type(cur) ~= 'function' or cur == wrapper then return end
    -- An earlier install of this feature may still be wrapped around it: take that one off by
    -- putting back the handler IT saved, so the game's own function is what we wrap - two copies
    -- stacked in front of each other would still work, but the marker would stop meaning anything.
    if cur == sp.__hudTurn and type(sp.__hudTurnOrig) == 'function' then
      cur = sp.__hudTurnOrig
    end
    sp.__hudTurnOrig = cur
    sp.__hudTurn = wrapper
    sp.receiveInput = wrapper
    f.wraps = f.wraps + 1
  end

  -- Read-only about what is on the character: restoring is decided by the identity of
  -- `receiveInput` itself, so a wrap the game has already overwritten is only cleared up.
  local function unwrap()
    local sp = turnPlayer()
    if not sp then return end
    if sp.receiveInput == wrapper then sp.receiveInput = sp.__hudTurnOrig end
    if sp.__hudTurn == wrapper then sp.__hudTurn, sp.__hudTurnOrig = nil, nil end
  end

  local function kill()
    f.on = false
    unwrap()
  end

  f.on = true
  f.wrapper = wrapper
  wrap()
  f.update, f.kill = wrap, kill
end
""".replace("__BUTTON__", _lua_str(cfg["button"]))


def _button(cfg):
    return str(cfg["button"]).strip() or "BACK"


def summary(cfg):
    return r"""(function()
  local f = turnFeature()
  if not f or not f.on then return nil end
  return 'turn in place: hold __BUTTON__ + a direction to face it without walking'
end)()""".replace("__BUTTON__", _button(cfg))


def status(cfg):
    return r"""(function()
  local f = turnFeature()
  if not f or not f.on then return nil end
  local sp = turnPlayer()
  local where
  if not sp then
    where = 'no player yet'
  elseif sp.__hudTurn == f.wrapper then
    where = 'wrap installed'
  else
    where = 'wrap NOT on the current player'
  end
  return string.format('turn in place: __BUTTON__ + a direction turns without walking (%d turn(s), %s)',
    f.turns or 0, where)
end)()""".replace("__BUTTON__", _button(cfg))


def report(cfg):
    return r"""(function()
  local out = {}
  out[#out + 1] = string.format('turn in place - button "%s"', '__BUTTON__')
  local f = turnFeature()
  local sp = turnPlayer()
  if not f then
    out[#out + 1] = '  not installed, so nothing wraps the player and input is the game\'s own'
  else
    out[#out + 1] = string.format('  installed = %s   wraps over this session = %d',
      tostring(f.on == true), f.wraps or 0)
    if not sp then
      out[#out + 1] = '  no player spawnable right now (not in the overworld)'
    elseif sp.__hudTurn == f.wrapper then
      out[#out + 1] = '  the wrapper IS on the current player: ' .. tostring(sp.receiveInput)
    else
      out[#out + 1] = '  the wrapper is NOT on the current player - the next poll puts it back'
    end
    out[#out + 1] = string.format('  directions seen = %d, turns taken = %d, last = %s',
      f.seen or 0, f.turns or 0, tostring(f.lastTurn))
    if f.err then out[#out + 1] = '  last rotate() error = ' .. tostring(f.err) end
    out[#out + 1] = string.format('  button held right now = %s', tostring(turnHeld()))
  end
  if sp and type(sp.direction) == 'string' then
    out[#out + 1] = string.format('  player faces "%s", isMoving = %s, tile %s,%s',
      sp.direction, tostring(sp.isMoving), tostring(sp.currentTileX), tostring(sp.currentTileY))
  end
  local names = turnPressedNames()
  out[#out + 1] = '  buttons the game reports as down: ' ..
    (#names > 0 and table.concat(names, ', ') or '(none)')
  out[#out + 1] = '  that list is the answer to "the button does nothing": if pressing it does not'
  out[#out + 1] = '  add an entry here, the game has not mapped it and the setting name is wrong.'
  out[#out + 1] = '  What it will do: a held direction only turns - no step is queued, and a step'
  out[#out + 1] = '  already in flight is allowed to land first (it cannot be interrupted).'
  return table.concat(out, '\n')
end)()""".replace("__BUTTON__", _button(cfg))
