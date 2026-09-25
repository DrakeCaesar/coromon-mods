#!/usr/bin/env python3
"""
dialog.py - make a held button stop running through a conversation.

THE PROBLEM. Coromon does not advance dialogue from key EVENTS, it POLLS the input helper:

    inputHelper:isMappedButtonActuallyPressed('USE')     -- 'USE' is the game's button name

(1.5.4 spells that `isButtonActuallyPressed`; 1.5.5 renamed it. See THE NAME below.)

from the typewriter (so a press finishes the line early) and again from the wait for the
next line, in `classes.interface.dialog.abstractDialogOverlayBuilder`. That call reports
whether the button is down *right now*, and it is polled every frame - so it keeps being
true for as long as the button is held, and the dialogue advances line after line for as
long as the finger is down. A press that is still on its way back up after finishing one
line is therefore already advancing the next one, which is what makes a line vanish before
it can be read.

THE FIX. While a conversation is open, that getter is replaced with an edge-triggered
version: it reports the button exactly once, on the frame it goes down, and nil afterwards.
So a press finishes the typing, and only the NEXT press advances - holding does nothing.
Everything the game already does with that call keeps working; it just stops repeating while
a button is held.

THE NAME. 1.5.4 calls the getter `isButtonActuallyPressed` and 1.5.5 renamed the whole
button family, so this resolves the field name from the input helper itself (`core.inputButtonName`)
and falls back to the 1.5.4 spelling. That matters MORE here than in the other user of
this helper, because this feature WRITES to the table: assigning `h.isButtonActuallyPressed`
on a build that reads `h.isMappedButtonActuallyPressed` would add a field nothing polls - the
swap would look installed, report itself as installed, and silently do nothing.

WHY IT IS SCOPED, AND HOW. Four modules name that getter - `inputHelper` defines it, and
`mainCharacter`, the main dialogue overlay and the battle action panel poll it - so replacing
it globally would change walking and interacting everywhere. It is
only swapped while `classes.interface.dialog.dialog:isCreated()` says a conversation is on
screen, and put back the moment it is not - and the swap is on the input table itself, which
the dialogue looks up at call time (GETGLOBAL inputHelper, then SELF), so the dialogue picks
it up live.

WHAT IT DOES NOT COVER. `isCreated()` is the main dialogue, so the Rogue and battle dialog
overlays - which poll the same helper from their own modules - are left alone. Long lines
that need scrolling also require a press per step rather than a held button, which is the
same trade made everywhere else here.

NOT VERIFIED END TO END: whether a conversation reads better with this is something only
playing it can say. What IS confirmed is the mechanism above, from the bytecode, and that
`isCreated()` is a usable handle at runtime.
"""

NAME = "dialog"

SETTINGS = [
    (
        "enabled",
        True,
        (
            "while a conversation is open, make the button that advances it work on a fresh "
            "press only, so holding it no longer runs through lines - a press still finishes "
            "the line being typed, and the next press moves on"
        ),
    ),
]


def lua(cfg):
    """Shared helpers, so --status and the report can describe it too."""
    return r"""
-- The game's input helper, if it is loaded AND it still has a button getter we know about.
local function dialogInput()
  local h = _G.inputHelper
  if type(h) == 'table' and inputButtonName() then return h end
end

-- Is a conversation on screen? This is the game's own main dialogue module.
local function dialogOpen()
  local d = package.loaded['classes.interface.dialog.dialog']
  if type(d) ~= 'table' or type(d.isCreated) ~= 'function' then return false end
  local ok, v = pcall(d.isCreated, d)
  return (ok and v) and true or false
end

local function dialogSwapped()
  local h = dialogInput()
  local name = inputButtonName()
  return (h ~= nil) and (name ~= nil) and (type(h.__hudDialogEdge) == 'function')
     and (h[name] == h.__hudDialogEdge)
end
"""


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return r"""
do
  local f = makeFeature('dialog', 200)
  f.edges = 0          -- presses the dialogue has been handed

  local base            -- the game's own button getter, kept for the swap back
  local held, seen = {}, {}

  -- True only on the frame a button goes down. `base` reports the button for as long as it is
  -- held, which is what let a held finger walk through a conversation.
  local function edge(a, b)
    if not base then return nil end
    -- called as a method by the dialogue, but accept the dot form rather than guess
    local self, button = a, b
    if type(a) == 'string' and b == nil then self, button = nil, a end
    local ok, now = pcall(base, self, button)
    if not ok then now = nil end
    local was = held[button]
    held[button] = now and true or false
    seen[button] = true
    if now and not was then
      f.edges = (f.edges or 0) + 1
      return now
    end
    return nil
  end

  -- Keep `held` true to life between the dialogue's own polls. If a press and its release both
  -- happened while nothing was asking, the next press would look like a continuation of the
  -- old one and be swallowed - so the buttons we have been asked about are re-read each frame.
  local function refresh()
    if not base then return end
    local h = dialogInput()
    if not h then return end
    for button in pairs(seen) do
      local ok, now = pcall(base, h, button)
      if ok then held[button] = now and true or false end
    end
  end

  -- The FIELD NAME is resolved on every swap rather than once, because 1.5.5 renamed it
  -- (isButtonActuallyPressed -> isMappedButtonActuallyPressed) and this is the feature that WRITES
  -- to that field: writing the name a build does not have would install a field nothing polls, so
  -- the dialogue would keep using the game's own getter and the swap would look installed while
  -- doing nothing.
  local function wrap()
    local h = dialogInput()
    local name = inputButtonName()
    if not h or not name or h[name] == edge then return end
    if type(base) ~= 'function' then base = h[name] end
    held, seen = {}, {}
    h[name] = edge
    h.__hudDialogName = name
    h.__hudDialogEdge = edge
    h.__hudDialogBase = base
  end

  local function unwrap()
    local h = dialogInput()
    if not h then return end
    local name = inputButtonName()
    if name and h[name] == h.__hudDialogEdge then
      h[name] = h.__hudDialogBase
    end
    h.__hudDialogName, h.__hudDialogEdge, h.__hudDialogBase = nil, nil, nil
    held, seen = {}, {}
  end

  local function frame()
    local h = _G.__hud
    if not h or h.dialogFrame ~= frame or not f.on then return end
    if dialogOpen() then
      wrap()
      f.active = true
      refresh()
    elseif f.active then
      unwrap()                       -- the conversation ended: give the helper back
      f.active = false
    end
  end

  local function update()
    if not f.on then return end
    if dialogOpen() then wrap() f.active = true else unwrap() f.active = false end
  end

  local function kill()
    pcall(function() Runtime:removeEventListener('enterFrame', frame) end)
    unwrap()
    local h = _G.__hud
    if h and h.dialogFrame == frame then h.dialogFrame = nil end
  end

  f.update, f.kill, f.on = update, kill, true
  if dialogOpen() then wrap() f.active = true end
  Runtime:addEventListener('enterFrame', frame)
  local H = _G.__hud
  if H then H.dialogFrame = frame end
end
"""


def summary(cfg):
    if not cfg["enabled"]:
        return "''"
    return r"""(function()
  if dialogSwapped() then
    return string.format('dialogue: holding the button no longer advances it (%d press(es) passed on)',
      (_G.__hud and _G.__hud.feats.dialog and _G.__hud.feats.dialog.edges) or 0)
  end
  if dialogOpen() then return 'dialogue: a conversation is open, but the swap is not in' end
  return 'dialogue: no conversation open, so the button is untouched for now'
end)()"""


def status(cfg):
    if not cfg["enabled"]:
        return "nil"
    return r"""(function()
  local f = _G.__hud and _G.__hud.feats.dialog
  if not f or not f.on then return nil end
  if dialogSwapped() then
    return string.format('dialogue: a held button does not advance it (%d press(es) passed on)',
      f.edges or 0)
  end
  return 'dialogue: waiting for a conversation to start'
end)()"""


def report(cfg):
    return r"""(function()
  local out = {}
  out[#out + 1] = 'dialogue - a held button should not run through lines'
  local h = dialogInput()
  if not h then
    out[#out + 1] = '  inputHelper is not loaded'
    return table.concat(out, '\n')
  end
  out[#out + 1] = string.format('  conversation open   = %s', tostring(dialogOpen()))
  out[#out + 1] = string.format('  the swap is in      = %s', tostring(dialogSwapped()))
  local f = _G.__hud and _G.__hud.feats.dialog
  if f then out[#out + 1] = string.format('  presses passed on   = %d', f.edges or 0) end
  local name = inputButtonName() or 'isButtonActuallyPressed'
  local ok, v = pcall(function() return h[name](h, 'USE') end)
  out[#out + 1] = string.format('  %s(\'USE\') right now = %s', name,
    tostring(ok and v or 'ERR'))
  out[#out + 1] = '  the game does not advance dialogue from key events: it polls this one call'
  out[#out + 1] = '  from the typewriter and from the wait for the next line, every frame - so'
  out[#out + 1] = '  while the button reads as down it keeps advancing. It is replaced with an'
  out[#out + 1] = '  edge-triggered version only while a conversation is on screen, because other'
  out[#out + 1] = '  modules poll it too and mainCharacter is one of them (1.5.5 renamed the field).'
  return table.concat(out, '\n')
end)()"""
