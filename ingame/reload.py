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
  -- LAYERING, MEASURED. `displayGroups.interface` turned out not to be reachable as a global (it
  -- reported nil), so this counts the thing that actually IS queryable and is the same fact: the
  -- overlays the game itself thinks are open. Dialogues are overlays, so a stale conversation box
  -- left behind shows up here as a number that does not come back down.
  pcall(function()
    local bob = package.loaded['classes.interface.overlays.baseOverlayBuilder']
    if type(bob) == 'table' and type(bob.getAmountOfActiveOverlays) == 'function' then
      p.overlays = bob:getAmountOfActiveOverlays()
    end
  end)
  -- And which levels have a navigation registered, read the way the teardown reads it (the table is a
  -- closure upvalue, not a field). A level that keeps a navigation after its overlay is gone is what
  -- freezes the character: the world's navigation is level 1 and anything above it takes the input.
  pcall(function()
    local ih = _G.inputHelper
    if type(ih) ~= 'table' then return end
    local per
    local function collect(fn)
      if per ~= nil or type(fn) ~= 'function' then return end
      for i = 1, 12 do
        local nm, v = debug.getupvalue(fn, i)
        if nm == nil then break end
        if nm == 'inputNavigationPerInputLevel' and type(v) == 'table' then per = v end
      end
    end
    collect(ih.increaseInputLevel)
    collect(ih.decreaseInputLevel)
    if per == nil then return end
    local ks = {}
    for k, v in pairs(per) do
      if v ~= nil then ks[#ks + 1] = tostring(k) end
    end
    table.sort(ks)
    p.navLevels = #ks .. ':{' .. table.concat(ks, ',') .. '}'
  end)
  return p
end

local function qrWiProbeSummary()
  local p = qrWiProbe()
  if p.note then return p.note end
  local button = 'n/a'
  if p.background ~= nil then button = p.alive and 'alive' or 'DEAD' end
  -- level is here because a level that creeps up and never comes back down freezes the character: the
  -- world's navigation lives at level 1, so anything above it blocks movement. lastStale names WHICH
  -- interface the declined press came from (#n is the current one, so #n-1 is the one just replaced);
  -- a serial far behind the current one would mean old interfaces are being kept, not just the last.
  return string.format(
    'created=%s button=%s level=%s navs=%s overlays=%s hoverables=%d dead=%d stale=%d staleBack=%d lastStale=#%s of #%s open=%s close=%s touch=%s',
    tostring(p.created), button, tostring(p.level), tostring(p.navLevels),
    tostring(p.overlays), p.hover or 0, p.hoverDead or 0,
    _G.__qrStalePress or 0, _G.__qrStaleBack or 0,
    tostring(_G.__qrStaleSerial or '-'), tostring(_G.__qrSerial or '-'),
    tostring(_G.__qrMenuOpens or 0), tostring(_G.__qrMenuCloses or 0),
    tostring((_G.__qrRegN or {}).touch or 0))
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
  -- Tag each button with a serial so a declined press can be traced back to the interface it belonged
  -- to. #n is the interface running now, so #n-1 is the one this reload replaced; anything older means
  -- interfaces are being retained rather than replaced.
  _G.__qrSerial = (_G.__qrSerial or 0) + 1
  btn.__qrSerial = _G.__qrSerial
  local wrapped = 0
  for _, name in ipairs({ 'setPressed', 'setHovered' }) do
    local orig = btn[name]
    if type(orig) == 'function' then
      btn[name] = function(self, ...)
        if not qrLive(self) then
          _G.__qrStalePress = (_G.__qrStalePress or 0) + 1
          _G.__qrStaleSerial = self.__qrSerial
          return
        end
        return orig(self, ...)
      end
      wrapped = wrapped + 1
    end
  end
  btn.__qrGuarded = true
  _G.__qrGuarded = wrapped
  _G.__qrBtnBefore = btn            -- kept, so its input registrations can be dropped after the load
  pcall(function() _G.__qrBgBefore = btn:getBackground() end)
  return string.format('guarded %d method(s) on button #%d', wrapped, btn.__qrSerial)
end

-- A BACK PRESS WITH NO PAUSE MENU IS MEANINGLESS, AND IT KILLS THE GAME.
--
-- `pauseMenu.lua:201-203` is the Back button's ENTIRE handler:
--
--     local function _onPress()
--       instance:back()          -- L202
--     end
--
-- `instance` is a module-level local, and the module's own `destroy` (L19-22) is
-- `instance = display.remove(instance)` - so once the menu has been destroyed, `instance` is nil and
-- any later Back press indexes nil:
--
--     pauseMenu.lua:202: attempt to index upvalue 'instance' (a nil value)
--       in function '_onPress'
--         <- outerTopBarBackButtonBuilder.lua:27  _onButtonPressed
--         <- UIButton.lua:83 _onComplete  <- UIButtonTransition.lua:90 onComplete
--         <- transition.lua:29 completeTransition
--
-- Note the route: the press arrives through a UIButtonTransition COMPLETING, i.e. the press animation
-- finishes after the press was handled. So this is the second delivery of a press that already closed
-- the menu, or a Back button left registered by an earlier reload - the same stale-delivery family as
-- the interact prompt, one menu over. It needs a reload or two to build up, which is exactly how it was
-- reported.
--
-- The seam: `outerTopBarBackButtonBuilder.new(self, _onBeforePress, _onPress)` takes the callbacks as
-- ARGUMENTS (proto params=3; the onPress closure closes over the third). And a full-game scan says
-- **pauseMenu is the ONLY module that requires this builder** - so every Back button in the game belongs
-- to the pause menu. That is what makes `pauseMenu:isCreated()` the right test, and it is also why this
-- is not a behaviour being suppressed: a Back press with no pause menu has nothing to act on. The call
-- is not intercepted before it is made, it is simply not made, on an object that is gone - the same
-- shape as the interact-button guard above.
local function qrGuardBackButton()
  local pm = _G.pauseMenu
  local builder = package.loaded[
    'classes.interface.navBarObjects.topBarObjects.outerTopBarBackButtonBuilder']
  if type(pm) ~= 'table' or type(pm.isCreated) ~= 'function' then return 'no pauseMenu' end
  if type(builder) ~= 'table' or type(builder.new) ~= 'function' then
    return 'back button builder is not loaded yet'
  end
  -- the tag goes on the module TABLE, not on `new` itself: in Lua a function value cannot be indexed,
  -- so `builder.new.__qrGuarded` is "attempt to index field 'new' (a function value)" and it aborted
  -- the whole install. (The interact-button guard above tags a display object, which is fine.)
  if builder.__qrBackGuarded then return 'already guarded' end
  local orig = builder.new
  local wrapped = function(self, onBeforePress, onPress)
    local guarded = function(...)
      -- `isCreated()` is `return instance`, so it answers the INSTANCE or NIL - never `false`. The first
      -- version of this compared `created == false`, which is never true, so the guard sat in the call
      -- chain doing nothing at all: the probe shows `staleBack=0` right through the session that
      -- crashed, and the traceback shows our closure calling straight through to the game's handler.
      -- Truthiness is the test, and the game uses it itself - combinedNavigationBuilder L22-23 is
      -- `if not self:isFocused() then self:handleObtainFocus()`.
      local ok, created = pcall(function() return pm:isCreated() end)
      if not ok or not created then
        _G.__qrStaleBack = (_G.__qrStaleBack or 0) + 1
        return
      end
      return onPress and onPress(...)
    end
    return orig(self, onBeforePress, guarded)
  end
  builder.__qrBackGuarded = true
  builder.new = wrapped
  return 'the Back button on the pause menu'
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

-- Made reachable by name, because the watcher below runs from ARBITRARY game code, long after this
-- chunk has finished. A watcher that records into a table and hopes a later tick flushes it loses up to
-- one host tick of entries - which is exactly the window a crash lands in. qrLog opens, appends and
-- closes on every call, so this is already crash-safe.
_G.__qrLogLine = qrLog

-- ============================ THE MENU WATCHER ============================
-- READ-ONLY. It wraps a handful of functions to COUNT calls and to record where they came from, calls
-- straight through with the original arguments, and returns the original results. Nothing is declined,
-- nothing is mutated, and it can be installed at any time.
--
-- WHY. The reported fault is the ITEMS menu drawing on top of the NORMAL pause menu after a few
-- reloads, with a Back press then crashing in `pauseMenu.lua:202`. There are two candidate mechanisms
-- and the existing log cannot tell them apart:
--   (a) ONE press opening TWO menus. The escape button is one touchable carrying two handlers -
--       `onBeforePress` and `onPress` - short press opens the menu, long press opens the items page.
--       Normally only one runs. If both run we get exactly the layering described.
--   (b) a STALE registration from an earlier reload. Destroying a menu does not unregister its buttons
--       (a documented Solar2D/inputHelper fact in this project), so a press can reach a button whose
--       menu is gone - the same family as the interact prompt.
-- What separates them is the traceback of each menu OPEN (which handler called it) and whether the
-- registration count grows across reloads. So this records both.

local function qrTrace(skip)
  if type(debug) ~= 'table' or type(debug.traceback) ~= 'function' then return '?' end
  local ok, tb = pcall(debug.traceback, '', skip or 3)
  if not ok or type(tb) ~= 'string' then return '?' end
  local keep = {}
  for line in tb:gmatch('[^\n]+') do
    -- only frames that name a Lua file: the engine's C frames and the chunk header add nothing
    if line:find('%.lua') then
      keep[#keep + 1] = (line:gsub('^%s+', ''))
      if #keep >= 5 then break end
    end
  end
  if #keep == 0 then return '?' end
  return table.concat(keep, '  <-  ')
end

-- The registration census. There is no way to ASK inputHelper how many touchables it holds - the module
-- exports add/remove/isTouchable and getObjectsListeningToMouseHover, and nothing that enumerates the
-- touchables - so the count is maintained here by wrapping the add and remove calls. Weak keys, so this
-- cannot be the reason an object is kept alive.
local qrReg = {
  touch   = setmetatable({}, { __mode = 'k' }),
  hover   = setmetatable({}, { __mode = 'k' }),
  overlay = setmetatable({}, { __mode = 'k' }),
}
_G.__qrRegN = { touch = 0, hover = 0, overlay = 0 }

local function qrCensus()
  local n = _G.__qrRegN
  return string.format('touchables=%d hoverables=%d overlays=%d', n.touch, n.hover, n.overlay)
end

local function qrWatchRegister(prefix, key)
  local ih = _G.inputHelper
  if type(ih) ~= 'table' then return end
  local addName, remName = prefix .. 'addTouchable', prefix .. 'removeTouchable'
  local add, rem = ih[addName], ih[remName]
  if type(add) == 'function' and not ih['__qrWatchAdd' .. key] then
    ih[addName] = function(self, obj, ...)
      if obj ~= nil and qrReg[key][obj] == nil then
        qrReg[key][obj] = true
        _G.__qrRegN[key] = _G.__qrRegN[key] + 1
      elseif obj ~= nil then
        -- A SECOND registration of an object that is already in the list. This is the concrete thing
        -- both suspects produce, and it is worth a line of its own with the caller.
        _G.__qrLogLine(string.format('  DUP %s: an object already in the list was added again\n    from %s',
                                     addName, qrTrace(3)))
      end
      return add(self, obj, ...)
    end
    ih['__qrWatchAdd' .. key] = true
  end
  if type(rem) == 'function' and not ih['__qrWatchRem' .. key] then
    ih[remName] = function(self, obj, ...)
      if obj ~= nil and qrReg[key][obj] ~= nil then
        qrReg[key][obj] = nil
        _G.__qrRegN[key] = _G.__qrRegN[key] - 1
      end
      return rem(self, obj, ...)
    end
    ih['__qrWatchRem' .. key] = true
  end
end

local function qrLevelNow()
  local ok, v = pcall(function() return _G.inputHelper:getInputLevel() end)
  return (ok and type(v) == 'number') and tostring(v) or '?'
end

local function qrWatchMenu()
  local pm = _G.pauseMenu
  if type(pm) ~= 'table' then return 'no pauseMenu' end
  _G.__qrMenuOpens, _G.__qrMenuCloses = _G.__qrMenuOpens or 0, _G.__qrMenuCloses or 0
  local function alive()
    local ok, a = pcall(function() return pm:isCreated() end)
    return ok and a ~= nil
  end
  for _, name in ipairs({ 'createInstance', 'createInstanceAndPauseWorld' }) do
    local orig = pm[name]
    if type(orig) == 'function' and not pm['__qrWatch' .. name] then
      pm[name] = function(self, ...)
        _G.__qrMenuOpens = _G.__qrMenuOpens + 1
        local open = alive()
        -- alreadyOpen is the whole point: a menu opened while one is still up is the layer.
        _G.__qrLogLine(string.format('  menu OPEN  #%d lvl=%s %s alreadyOpen=%s via %s\n    from %s',
          _G.__qrMenuOpens, qrLevelNow(), qrCensus(), tostring(open), name, qrTrace(3)))
        -- AND IT IS REFUSED. This is the layer, and the log named its cause:
        --
        --   menu OPEN  #7 lvl=2 ... alreadyOpen=true via createInstanceAndPauseWorld
        --     from worldInterface.lua:406: in function '_onHold'
        --       <- inputHelper.lua:329: in function 'listener'
        --         <- timer.lua:62: in function 'triggerTimer'
        --
        -- That is a HOLD TIMER, not a press. inputHelper schedules
        -- `timer.performWithDelay(_onHoldDuration, <closure at L325-330>)` when a touchable goes down,
        -- and that closure calls `_onHold(event)` when it expires; the RELEASE handler is what cancels
        -- it (the closure itself even starts with `timer.cancel(target.holdTimer)`). A reload takes the
        -- release away, so a hold that began before the reload expires after it and opens a second
        -- pause menu on top of the one the player had just opened - two menus, level 3, navs {1,2,3},
        -- and one of them apparently showing the items page over the other.
        --
        -- The game never opens a second one: every caller checks `pauseMenu:isCreated()` first and
        -- closes before opening. So refusing cannot break a legitimate flow - and returning the menu
        -- that is already up, rather than nil, keeps whole the callers that use the return value.
        if open and _G.__qrBlockSecondMenu ~= false then
          _G.__qrBlockedMenus = (_G.__qrBlockedMenus or 0) + 1
          _G.__qrLogLine(string.format(
            '  menu OPEN BLOCKED #%d - a pause menu is already up', _G.__qrBlockedMenus))
          local oki, inst = pcall(function() return pm:isCreated() end)
          return oki and inst or nil
        end
        return orig(self, ...)
      end
      pm['__qrWatch' .. name] = true
    end
  end
  for _, name in ipairs({ 'forceDestroyIfCreated', 'closeIfCreated' }) do
    local orig = pm[name]
    if type(orig) == 'function' and not pm['__qrWatch' .. name] then
      pm[name] = function(self, ...)
        -- Logged BEFORE the call and tail-returned, deliberately: this is Lua 5.1, so there is no
        -- table.pack/table.unpack to preserve an unknown number of returns through a pcall - and a
        -- `return orig(self, ...)` cannot be preceded by a log line. If the call throws, it throws
        -- exactly as it would have unwrapped.
        if alive() then
          _G.__qrMenuCloses = _G.__qrMenuCloses + 1
          _G.__qrLogLine(string.format('  menu CLOSE #%d lvl=%s %s via %s',
            _G.__qrMenuCloses, qrLevelNow(), qrCensus(), name))
        end
        return orig(self, ...)
      end
      pm['__qrWatch' .. name] = true
    end
  end
  return 'watching the pause menu'
end

-- How many of the two top-bar buttons exist. A growing count is a stale registration being carried;
-- a count that returns to its old value after a menu closes means the menu cleaned up after itself.
local function qrWatchButtonBuilders()
  local n = 0
  for path, label in pairs({
      ['classes.interface.navBarObjects.topBarObjects.outerTopBarEscapeButtonBuilder'] = 'escape',
      ['classes.interface.navBarObjects.topBarObjects.outerTopBarBackButtonBuilder']   = 'back',
    }) do
    local m = package.loaded[path]
    if type(m) == 'table' and type(m.new) == 'function' and not m.__qrWatchNew then
      local orig = m.new
      m.new = function(...)
        _G.__qrBuilt = _G.__qrBuilt or {}
        _G.__qrBuilt[label] = (_G.__qrBuilt[label] or 0) + 1
        _G.__qrLogLine(string.format('  %s BUTTON #%d built %s\n    from %s',
          label, _G.__qrBuilt[label], qrCensus(), qrTrace(3)))
        return orig(...)
      end
      m.__qrWatchNew = true
      n = n + 1
    end
  end
  return string.format('button builders watched: %d', n)
end

local function qrWatch()
  qrWatchRegister('', 'touch')
  qrWatchRegister('', 'hover')
  -- the overlay variants have their own names
  local ih = _G.inputHelper
  if type(ih) == 'table' then
    if type(ih.addTouchableOverlay) == 'function' and not ih.__qrWatchOverlay then
      local orig = ih.addTouchableOverlay
      ih.addTouchableOverlay = function(self, obj, ...)
        if obj ~= nil and qrReg.overlay[obj] == nil then
          qrReg.overlay[obj] = true
          _G.__qrRegN.overlay = _G.__qrRegN.overlay + 1
        end
        return orig(self, obj, ...)
      end
      ih.__qrWatchOverlay = true
    end
    if type(ih.removeTouchableOverlay) == 'function' and not ih.__qrWatchOverlayRem then
      local orig = ih.removeTouchableOverlay
      ih.removeTouchableOverlay = function(self, obj, ...)
        if obj ~= nil and qrReg.overlay[obj] ~= nil then
          qrReg.overlay[obj] = nil
          _G.__qrRegN.overlay = _G.__qrRegN.overlay - 1
        end
        return orig(self, obj, ...)
      end
      ih.__qrWatchOverlayRem = true
    end
  end
  local a = qrWatchMenu()
  local b = qrWatchButtonBuilders()
  return a .. ', ' .. b
end

-- Which of the four dialogue modules currently has a box up. Read-only; `isCreated` and `destroy`
-- are the pair every dialogue module exports.
local QR_DIALOGUES = {
  'classes.interface.dialog.dialog',
  'classes.interface.dialog.battleDialog',
  'classes.interface.dialog.BattleCornerDialog',
  'classes.interface.dialog.RogueDialog',
}
local function qrDialogueOpen()
  local up = {}
  for _, name in ipairs(QR_DIALOGUES) do
    local d = package.loaded[name]
    if type(d) == 'table' and type(d.isCreated) == 'function' then
      local okc, created = pcall(function() return d:isCreated() end)
      if okc and created then up[#up + 1] = name:match('[^.]+$') end
    end
  end
  if #up == 0 then return nil end
  return table.concat(up, ', ')
end

-- THE DIALOGUE TEARDOWN, AS IT HAPPENS. The proven chunk already leaves a full record of it, and
-- none of this touches the teardown - it copies that record out:
--
--   _G.__qrStep              which stage it is in, set at every step (so a stage that hangs shows up
--                            as the last line in the file)
--   _G.__qrClosedOverlays    the baseOverlayBuilder sweep: how many overlays it was over, or why it
--                            failed. Dialogues are overlays, so this is the main path.
--   _G.__qrClosedDialogues   the fallback sweep over the four dialogue modules (nil when none open)
--   _G.__qrInputLevel*       at start / before / after / target - a conversation runs at level 3 and
--                            the world's navigation is level 1, so the level IS the thing that
--                            freezes the character when it does not come back down
local function qrTeardownLog(f, final)
  if _G.__qrStep ~= f.stepSeen then
    f.stepSeen = _G.__qrStep
    qrLog('  step: ' .. tostring(_G.__qrStep))
  end
  if not final then return end
  if _G.__qrClosedOverlays ~= nil then
    qrLog('  overlays: ' .. tostring(_G.__qrClosedOverlays))
  end
  qrLog('  dialogues closed: ' .. tostring(_G.__qrClosedDialogues or 'none were open'))
  qrLog(string.format('  input level: at start=%s before=%s after=%s target=%s',
    tostring(_G.__qrInputLevelAtStart), tostring(_G.__qrInputLevelBefore),
    tostring(_G.__qrInputLevelAfter), tostring(_G.__qrFinalTarget)))
  if type(_G.__qrLog) == 'table' and #_G.__qrLog > 0 then
    qrLog('  poll: ' .. table.concat(_G.__qrLog, ' | '))
  end
end

-- THE OTHER HALF OF THE SAME FAULT, on the dialogue side. A conversation box that has been taken off
-- the stage can still be driven: `transition.toBottomOrFadeOut` -> `toBottom` ->
-- `magnet.getLocationFor` -> `magnet.top`, and line 78 of magnet.lua reads `obj.contentBounds.yMin` -
-- nil on a removed object. That is the crash after pressing Continue on a stale box.
--
-- Nothing is skipped up front here. The call is made, and only an error that is THIS error is
-- swallowed; anything else is re-raised, so no other bug is hidden and no working transition is
-- affected - a transition that works never enters this path at all.
local function qrGuardTransition()
  local tr = _G.transition
  if type(tr) ~= 'table' then return 'no transition module' end
  local wrapped = 0
  for _, name in ipairs({ 'toBottomOrFadeOut', 'toBottom' }) do
    local orig = tr[name]
    if type(orig) == 'function' and type(tr['__qrOrig_' .. name]) ~= 'function' then
      tr['__qrOrig_' .. name] = orig
      tr[name] = function(...)
        local ok, a, b = pcall(orig, ...)
        if ok then return a, b end
        local msg = tostring(a)
        if msg:find('contentBounds', 1, true) then
          _G.__qrStaleTween = (_G.__qrStaleTween or 0) + 1
          return
        end
        error(a, 0)
      end
      wrapped = wrapped + 1
    end
  end
  _G.__qrTransitionGuarded = wrapped
  return string.format('guarded %d transition method(s)', wrapped)
end

-- LEFTOVER INTERFACES AND THE PROMPT THAT GOES WITH THEM.
-- The interaction prompt is built inside the interface's own container:
--
--   instance       = groupHelper:new(displayGroups.world.interface)                     -- L22
--   hideable       = groupHelper:new(instance, { isVisible = false })                    -- L30
--   interactButton = WorldInterfaceInteractButton.new(self, hideable, 'USE', ...)        -- L284
--
-- so `worldInterface:destroy()` (destroyInstance = display.remove(instance)) should take the prompt
-- with it, and normally does. When it does not, the screen carries two prompts in the same place -
-- the old one and the new one - and two interfaces in the tree. That is "the UI got layered" seen from
-- the outside, and it only shows up when something is on screen right before the reload (a prompt in
-- front of a character), because that is when the old prompt has been made visible.
--
-- The objects are identifiable: the interface container is the only thing in this subtree carrying
-- `getHideableGroup` (assigned to `instance` at L37), and the prompt is the only thing carrying
-- `setPressed`. So this counts them, and when asked it removes the ones that are demonstrably NOT the
-- interface the game is driving now - `worldInterface:isCreated()` and its `getInteractButton()`. It
-- never touches those two, and it is a no-op when there are no duplicates.
local function qrScanInterfaces(remove)
  local wi, dg = _G.worldInterface, _G.displayGroups
  local root = dg and dg.world and dg.world.interface
  if type(wi) ~= 'table' or root == nil then return nil end
  local cur, curBtn
  pcall(function() cur = wi:isCreated() end)
  pcall(function() if cur ~= nil then curBtn = cur:getInteractButton() end end)
  local ifaces, prompts, removed, walked = 0, 0, 0, 0
  local ok = pcall(function()
    walked = qrWalk(root, function(o)
      if type(o) ~= 'table' then return end
      if type(o.getHideableGroup) == 'function' then
        ifaces = ifaces + 1
        if remove and cur ~= nil and o ~= cur then
          if pcall(function() display.remove(o) end) then removed = removed + 1 end
        end
      elseif type(o.setPressed) == 'function' then
        prompts = prompts + 1
        if remove and curBtn ~= nil and o ~= curBtn then
          if pcall(function() display.remove(o) end) then removed = removed + 1 end
        end
      end
    end)
  end)
  return { ifaces = ifaces, prompts = prompts, removed = removed, ok = ok, walked = walked,
           live = cur ~= nil, liveBtn = curBtn ~= nil }
end

-- THE REGISTRATION HALF. Removing the leftover display objects does not remove what routes input to
-- them: `refreshInteractButton` registers the prompt with
--   inputHelper:addMouseHoverable(interactButtonBackground, ...)        (L66)
-- and the interface registers the button itself
--   inputHelper:addTouchable(interactButton, getTouchListener(), ...)   (L286)
-- Neither is undone by `worldInterface:destroy()`. Those registrations are why a press made after a
-- reload still reaches the interface that was destroyed - the press goes to a dead prompt, the button
-- guard declines it, and from the outside the interact button simply does nothing.
--
-- `isTouchable` is used to REPORT the result rather than assume it: the point is to see in the log
-- whether the call did anything. Every call is pcall'd, and nothing happens if the object is gone.
local function qrUnregisterPrompt(btn, bg)
  local ih = _G.inputHelper
  if type(ih) ~= 'table' or btn == nil then return nil end
  local before = nil
  if bg ~= nil then
    pcall(function() before = ih:isTouchable(bg) end)
  end
  local did = {}
  for _, o in ipairs({ btn, bg }) do
    if o ~= nil then
      for _, name in ipairs({ 'removeTouchable', 'removeMouseHoverable', 'removeTouchableOverlay' }) do
        if type(ih[name]) == 'function' then
          if pcall(function() ih[name](ih, o) end) then did[#did + 1] = name end
        end
      end
    end
  end
  local after = nil
  if bg ~= nil then
    pcall(function() after = ih:isTouchable(bg) end)
  end
  return string.format('dropped %s; touchable before=%s after=%s',
    (#did > 0 and table.concat(did, '+') or 'nothing'),
    tostring(before), tostring(after))
end

-- A DESTROYED INSTANCE THAT IS STILL TRUTHY, and the pause-menu crash it causes.
--
--   pauseMenu.lua L19-22  destroyInstance:  instance = display.remove(instance)
--                                           setmetatable(t, nil)
--   pauseMenu.lua L240-242 _onBeforePress:  instance:close()          <- the escape button's callback
--   pauseMenu.lua L31-37   closeIfCreated:  if instance then instance:close(...) end
--
-- A DISPOSED display object reads nil for EVERY field - including methods the game assigned to it -
-- while remaining a truthy value in Lua. So after our teardown destroys the pause menu, the module's
-- own `isCreated()` still answers yes (it returns that upvalue), and both callbacks above then call
-- `close` on the corpse:
--     pauseMenu.lua:239: attempt to call method 'close' (a nil value)
-- A LIVE instance always has `close` (assigned while creating), so `type(inst.close) ~= 'function'` is
-- what tells a corpse apart from a live one - it is the only test used here.
--
-- Clearing the module's `instance` upvalue makes `isCreated()` honest, which is the state the game
-- assumes after a destroy, and the next press creates a fresh menu. `isCreated` (L12-14) is a closure
-- over that one upvalue, so setting it to nil sets the module's own value (upvalues are shared cells).
local function pauseMenuState()
  local pm = _G.pauseMenu
  if type(pm) ~= 'table' then return nil end
  local st = { moduleClose = type(pm.close) }
  local ok, inst = pcall(function() return pm:isCreated() end)
  st.inspected = ok
  st.inst = ok and inst or nil
  st.created = (ok and inst ~= nil) or false
  if st.inst ~= nil then
    local okc, c = pcall(function() return st.inst.close end)
    st.instClose = okc and type(c) or 'threw'
  end
  return st
end

local function pauseMenuFix()
  local pm = _G.pauseMenu
  if type(pm) ~= 'table' or type(pm.isCreated) ~= 'function' then
    return 'pauseMenu: not loaded'
  end
  local st = pauseMenuState()
  if st == nil or not st.inspected then return 'pauseMenu: cannot be asked' end
  if not st.created then return 'pauseMenu: not created' end
  if st.instClose == 'function' then return 'pauseMenu: live instance' end
  -- MEASURED ONLY - IT NO LONGER CLEARS ANYTHING. Clearing the upvalue was tried and it changed this
  -- crash into a worse one: the pause menu's own callbacks (`_onPress`, L202) READ that upvalue, so a
  -- queued button callback that lands after the clear hits `instance` nil instead of a corpse -
  -- `pauseMenu.lua:202: attempt to index upvalue 'instance' (a nil value)`. And if the game's menu
  -- creation path does not put it back, nil persists and every later Back press crashes. The corpse is
  -- at least the state the game's own force-destroy leaves; the fix belongs at the registration that
  -- delivers the press, not here.
  return string.format('pauseMenu: DESTROYED instance still answers isCreated() (close is %s) - left alone',
    tostring(st.instClose))
end

-- ANY INPUT STATE THAT IS STILL "HELD" AFTER A RELOAD. A press whose release went to an object that
-- no longer exists stays held in inputHelper's own bookkeeping, and the pause button carries TWO
-- handlers - `onBeforePress` and `onPress` (outerTopBarEscapeButtonBuilder L31-38) - with a long press
-- routed through the second. A held press that never resolves is therefore how a press and its
-- long-press can both land, or land one after the other, after a reload. These are the game's own
-- release calls, and each is reported so the log says whether it was available.
local function qrReleaseInput()
  local ih = _G.inputHelper
  if type(ih) ~= 'table' then return 'inputHelper: not loaded' end
  local did = {}
  for _, name in ipairs({ 'releaseInput', 'releaseKeys', 'releaseTouches', 'releaseHoverables' }) do
    if type(ih[name]) == 'function' then
      if pcall(function() ih[name](ih) end) then did[#did + 1] = name end
    end
  end
  return 'released held input: ' .. (#did > 0 and table.concat(did, '+') or 'nothing available')
end

-- REGISTRATIONS THAT POINT AT SOMETHING NO LONGER ON THE STAGE, which is what all of this was.
--
-- The reason a capture of the old UI failed: `display.remove(group)` takes the GROUP off the stage and
-- leaves its children ALIVE - off-screen, with every engine property still readable. So they are not
-- corpses (the probe reports `dead=0` throughout), liveness tests cannot find them, and they stay
-- registered with `inputHelper`, which keeps delivering presses to a menu that no longer exists.
-- `pauseMenu.lua:239` (close on the corpse) and `pauseMenu.lua:202` (`instance` nil) are both that.
--
-- A capture of the objects just before the teardown is also too narrow: it only ever sees the state
-- immediately before THAT reload, so leftovers from two reloads ago survive it - which is what
-- "paused, reloaded, long paused, reloaded, paused" produces.
--
-- So the test is MEMBERSHIP OF THE DISPLAY STAGE, taken after the load: walk `obj.parent` up and see
-- whether the chain reaches the stage. A detached object's parent is nil, so it fails; anything still
-- displayed - the live interface, an open dialogue, our own labels - passes, so nothing in use is
-- touched. This also catches leftovers from any number of reloads, because it asks about the current
-- state rather than comparing against a snapshot.
local function qrOnStage(o)
  local stage = display.getCurrentStage()
  if stage == nil then return true end          -- cannot tell: leave it alone
  local cur, guard = o, 0
  while cur ~= nil and guard < 40 do
    if cur == stage then return true end
    local ok, p = pcall(function() return cur.parent end)
    if not ok then return false end
    cur = p
    guard = guard + 1
  end
  return false
end

-- THE ESCAPE BUTTON, AND WHY IT NEEDS THE BACK BUTTON'S TREATMENT TOO.
--
-- The crash this is for, after a session that had layered menus:
--
--   pauseMenu.lua:239: attempt to call method 'close' (a nil value)
--     pauseMenu.lua:239: in function '_onBeforePress'
--     <outerTopBarEscapeButtonBuilder.lua:32: in function <outerTopBarEscapeButtonBuilder.lua:31>
--     inputHelper.lua:311 -> navigations.lua -> combinedNavigationBuilder
--       -> inputHelper.lua:881 handleKeyOrMappedButton
--
-- It can happen at all because `pauseMenu.lua` never calls `removeTouchable` ANYWHERE. Every menu that
-- has ever been opened leaves its two top-bar buttons registered with the input layer for the rest of
-- the session, so one Esc press is dispatched to all of them - including the buttons of menus that
-- were destroyed several reloads ago. Those buttons hold the closures pauseMenu built for them
-- (pauseMenu proto (0,6) pc 357-363: `outerTopBarEscapeButtonBuilder:new(true, <child 13>,
-- <child 14>)`, child 13 = `not self:canClose()` L238-240, child 14 = `instance:close()` L240-242),
-- and both of those act on the menu that was current when the button was built - a dead one.
--
-- The Back guard declines on `pauseMenu:isCreated()`, which was right there because a Back press means
-- nothing when no menu is up. That test CANNOT be reused here: this same builder is what opens the
-- pause menu from nothing, so declining on "no menu" would disable the escape button. The test that is
-- true for both cases is whether the BUTTON ITSELF is still on the stage - a press on a button that is
-- no longer displayed means nothing - which is exactly what qrOnStage already asks. It is applied to
-- BOTH callbacks, so it does not matter which of the two the nil `close` was in.
local function qrGuardEscapeButton()
  local builder = package.loaded[
    'classes.interface.navBarObjects.topBarObjects.outerTopBarEscapeButtonBuilder']
  if type(builder) ~= 'table' or type(builder.new) ~= 'function' then
    return 'escape button builder is not loaded yet'
  end
  if builder.__qrEscapeGuarded then return 'already guarded' end
  local orig = builder.new
  local wrapped = function(self, a, onBeforePress, onPress)
    -- the button is the builder's RETURN VALUE, so it cannot be captured before the call; the closures
    -- read it through this upvalue, and it is set by the time any press can reach them.
    local button = nil
    local function guard(fn)
      if type(fn) ~= 'function' then return fn end
      return function(...)
        if button ~= nil and not qrOnStage(button) then
          _G.__qrStaleEscape = (_G.__qrStaleEscape or 0) + 1
          return false
        end
        return fn(...)
      end
    end
    local out = orig(self, a, guard(onBeforePress), guard(onPress))
    button = out
    return out
  end
  builder.__qrEscapeGuarded = true
  builder.new = wrapped
  return 'the Escape button on the pause menu'
end

local function qrPruneOffstageInput()
  local ih = _G.inputHelper
  if type(ih) ~= 'table' or type(ih.getObjectsListeningToMouseHover) ~= 'function' then
    return 'inputHelper: cannot enumerate its hoverables'
  end
  local ok, hoverables = pcall(function() return ih:getObjectsListeningToMouseHover() end)
  if not ok or type(hoverables) ~= 'table' then return 'inputHelper: enumeration failed' end
  local total, stray, touchable = #hoverables, 0, 0
  local strays = {}
  for _, o in ipairs(hoverables) do
    if type(o) == 'table' and not qrOnStage(o) then
      strays[#strays + 1] = o
      stray = stray + 1
      pcall(function() if ih:isTouchable(o) then touchable = touchable + 1 end end)
    end
  end
  if stray == 0 then
    return string.format('input registrations: all %d hoverables are still on the stage', total)
  end
  local calls = 0
  for _, o in ipairs(strays) do
    for _, name in ipairs({ 'removeTouchable', 'removeMouseHoverable', 'removeTouchableOverlay' }) do
      if type(ih[name]) == 'function' then
        if pcall(function() ih[name](ih, o) end) then calls = calls + 1 end
      end
    end
  end
  local after = total
  pcall(function() after = #(ih:getObjectsListeningToMouseHover() or {}) end)
  return string.format(
    'pruned %d of %d hoverables that were off the stage (%d of them touchable): %d call(s), %d left',
    stray, total, touchable, calls, after)
end

-- A DISPLAY TREE WALK, done by hand, and there is a lesson in why.
--
-- This used `display.getChildrenIncludingParentRecursive` first. That function EXISTS - transition.lu
-- L395 calls it as `array.forEach(display.getChildrenIncludingParentRecursive(x), fn)` - so the name and
-- the call form were both right, and iterating its result with `ipairs` still found nothing: the game
-- hands it to `array.forEach`, which accepts a set as well as an array, so its result is not a plain
-- array. The cost was visible in the log and I misread it: `ifaces=0 prompts=0` on every reload, which
-- looks like "no duplicates" and was actually "the walk found nothing at all".
--
-- The manual walk below uses only what a Solar2D group guarantees: `#group` is its child count and
-- `group[i]` its children. Everything is pcall'd and the guard stops a cycle from hanging the game.
local function qrWalk(root, fn)
  if root == nil then return 0 end
  local stack, seen = { root }, 0
  while #stack > 0 and seen < 5000 do
    seen = seen + 1
    local o = table.remove(stack)
    pcall(function() fn(o) end)
    local n = 0
    pcall(function() n = #o end)
    for i = 1, n do
      local ok, child = pcall(function() return o[i] end)
      if ok and child ~= nil then stack[#stack + 1] = child end
    end
  end
  return seen
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
    out[#out + 1] = string.format('  input level = %s   (the world navigation is at level 1)',
      tostring(p.level))
  end
  if p.overlays ~= nil then
    out[#out + 1] = string.format('  overlays the game thinks are open = %s', tostring(p.overlays))
  end
  if p.navLevels ~= nil then
    out[#out + 1] = string.format('  levels holding a navigation = %s', tostring(p.navLevels))
  end
  out[#out + 1] = string.format('  buttons guarded so far = %s, last press declined from #%s',
    tostring(_G.__qrSerial or 0), tostring(_G.__qrStaleSerial or '-'))
  local scan = qrScanInterfaces(false)
  if scan == nil then
    out[#out + 1] = '  interface tree: not reachable (displayGroups.world.interface)'
  else
    out[#out + 1] = string.format(
      '  interface tree: processes=%d prompts=%d, %d object(s) walked   (the live one is %s, its prompt %s)',
      scan.ifaces or -1, scan.prompts or -1, scan.walked or 0,
      scan.live and 'present' or 'MISSING', scan.liveBtn and 'present' or 'MISSING')
    if (scan.ifaces or 0) > 1 or (scan.prompts or 0) > 1 then
      out[#out + 1] = '  MORE THAN ONE means leftovers are layered on the screen: the next reload'
      out[#out + 1] = '  removes anything that is not the interface the game is driving.'
    end
  end
  -- the feature table, read here rather than inherited: this function is defined at chunk level and
  -- called from the report chunk, so a local `f` from the caller is NOT in scope.
  local fe = _G.__hud and _G.__hud.feats and _G.__hud.feats.reload
  local pm = pauseMenuState()
  if pm == nil then
    out[#out + 1] = '  pauseMenu: not loaded'
  elseif not pm.inspected then
    out[#out + 1] = '  pauseMenu: cannot be asked (isCreated threw)'
  else
    out[#out + 1] = string.format(
      '  pauseMenu: created=%s instanceClose=%s moduleClose=%s',
      tostring(pm.created), tostring(pm.instClose), tostring(pm.moduleClose))
    if pm.created and pm.instClose ~= 'function' then
      out[#out + 1] = '  that is a DESTROYED instance still answering isCreated() = true: the escape'
      out[#out + 1] = '  callback and closeIfCreated both call close() on it and crash. The next'
      out[#out + 1] = '  reload clears it; the repair below does the same thing now on request.'
    end
  end
  if fe and fe.sweep then
    out[#out + 1] = string.format('  after the last reload: ifaces=%d prompts=%d, removed %d',
      fe.sweep.ifaces or -1, fe.sweep.prompts or -1, fe.sweep.removed or 0)
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

  -- Installed once, not per reload: a stale dialogue is closed whenever the game feels like it, not
  -- only around a reload.
  qrLog('install - ' .. qrGuardTransition())
  -- pcall'd on purpose. A mistake in the guard itself took the WHOLE install down once - every other
  -- feature in the chunk went with it and the only symptom was reload=off in --status - so this one
  -- reports its own failure instead of taking the chunk with it.
  local okBack, saidBack = pcall(qrGuardBackButton)
  qrLog('install - back button: ' .. (okBack and tostring(saidBack)
                                        or ('FAILED: ' .. tostring(saidBack))))
  -- the menu watcher: read-only, and installed the same defensive way
  local okWatch, saidWatch = pcall(qrWatch)
  qrLog('install - watcher: ' .. (okWatch and tostring(saidWatch)
                                    or ('FAILED: ' .. tostring(saidWatch))))

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
    -- Start the clock here, at the press, so the body's final line can say how long the whole thing
    -- took. Monotonic and in ms; the body reads it back.
    _G.__qrT0, _G.__qrLoadMs = nil, nil
    if type(_G.system) == 'table' and type(_G.system.getTimer) == 'function' then
      local okt, t0 = pcall(function() return _G.system.getTimer() end)
      if okt and type(t0) == 'number' then _G.__qrT0 = t0 end
    end

    -- THE CLOSE MOVED INTO THE TEARDOWN, AND THAT IS THE FIX.
    --
    -- It used to be started here, with NO callback, and then the teardown called
    -- `pauseMenu:forceDestroyIfCreated()` about 60 ms later. forceDestroy is
    -- `transition.cancelRecursive(self)` first, and cancelRecursive cancels by target over the menu's
    -- whole display subtree - the close's own 200 ms `fadeOut(instance:getBottomGroup(), ...)` is one
    -- of those targets, so the close was CANCELLED ~140 ms before it would have finished.
    --
    -- Everything the close does, it does in that fadeOut's completion, and `completeTransition` skips
    -- an onComplete whose transition carries shouldBeCancelled (transition.lu L26-31). So none of it
    -- ever ran: `unblockInput()`, then `afterClose()` -> `decreaseInputLevelIfNotAlreadyDone`,
    -- `destroyInstance()`, `topBar:onDestroy()`, `bottomBar:onDestroy()` and
    -- `eventManager:dispatch('pauseMenuClosed')`. The close was started and killed before it finished,
    -- and the state it would have left behind was faked by hand afterwards by the level correction and
    -- the focus repairs in the teardown.
    --
    -- It now runs inside TEARDOWN_BASE through `closeIfCreated(cb)`, with the REST OF THE TEARDOWN in
    -- its callback - so the close is given the time it needs and nothing cancels it, and the menu is
    -- released before the world is taken down, which is the game's own order (the Quit button does
    -- `pauseMenu:close(cb)` first and destroys the world from inside `cb`). Only the menu state is read
    -- here: it is the "before" half of the pair and it costs nothing.
    pcall(function()
      local before = pauseMenuState()
      qrLog(string.format('  pauseMenu at the press: created %s, instanceClose %s',
        tostring(before and before.created), tostring(before and before.instClose)))
    end)

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
    f.step = 'pre-flight'

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
    -- The record the teardown leaves behind is cleared here, so what is logged at the end describes
    -- THIS reload even if the teardown throws before it fills any of it in.
    _G.__qrClosedOverlays, _G.__qrClosedDialogues = nil, nil
    _G.__qrInputLevelAtStart, _G.__qrInputLevelBefore = nil, nil
    _G.__qrInputLevelAfter, _G.__qrFinalTarget = nil, nil
    f.stepSeen = nil
    f.prunes, f.hoverSeen = 0, nil
    qrLog('reload starting - ' .. qrWiProbeSummary())
    qrLog('  dialogues open at the press: ' .. tostring(qrDialogueOpen() or 'none'))
    -- (The objects are no longer captured here: a snapshot of the UI just before the teardown only ever
    -- sees that one reload's state, and leftovers from earlier reloads survive it. What replaces it is a
    -- stage-membership prune after the load - see qrPruneOffstageInput.)
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
    -- While the teardown is running, its step marker and its dialogue/level record are copied out as
    -- they change; the rest of the record follows once the load finishes.
    if f.busy then
      qrTeardownLog(f, _G.__qrDone ~= nil)
    end
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
      qrLog(string.format('  press to load: %s ms',
        _G.__qrLoadMs ~= nil and tostring(_G.__qrLoadMs) or 'not timed'))
      qrLog('  ' .. qrWiProbeSummary())
      -- And take away anything from the interface that was just replaced but did not go with it. Only
      -- when there is a duplicate to remove, and never the interface the game is driving now.
      local sw = qrScanInterfaces(true)
      if sw then
        f.sweep = sw
        qrLog(string.format('  interface tree: ifaces=%d prompts=%d -> removed %d leftover(s)',
          sw.ifaces or -1, sw.prompts or -1, sw.removed or 0))
      end
      -- And drop the previous prompt's input registrations, since those are what keep sending presses
      -- to an interface that no longer exists.
      pcall(function()
        local said = qrUnregisterPrompt(_G.__qrBtnBefore, _G.__qrBgBefore)
        if said then qrLog('  previous prompt: ' .. said) end
      end)
      _G.__qrBtnBefore, _G.__qrBgBefore = nil, nil
      -- Drop whatever still routes input to something no longer on the stage, release any input state
      -- still held, then make the pause menu's own isCreated() honest about the instance the teardown
      -- destroyed.
      pcall(function() qrLog('  ' .. tostring(qrPruneOffstageInput())) end)
      pcall(function() qrLog('  ' .. tostring(qrReleaseInput())) end)
      pcall(function() qrLog('  ' .. tostring(pauseMenuFix())) end)
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
      -- AND RE-CHECK THE INPUT LIST AS IT GROWS. The probe shows 0 hoverables right after a load and
      -- 200+ once menus are open, so the game builds that list as UI is created and a single prune at
      -- load time never sees anything. A stale registration therefore appears LATER, which is where the
      -- pause menu's pages (levels 1..7) come in. Cheap to watch for: the count is one `#`, and the
      -- walk only runs when the count actually changed.
      local n = nil
      pcall(function()
        n = #(_G.inputHelper:getObjectsListeningToMouseHover() or {})
      end)
      if n ~= nil and n ~= f.hoverSeen then
        f.hoverSeen = n
        if (f.prunes or 0) < 60 then
          f.prunes = (f.prunes or 0) + 1
          local said = nil
          pcall(function() said = qrPruneOffstageInput() end)
          if type(said) == 'string' and said:find('pruned', 1, true) then
            qrLog('  ' .. said)
          end
        end
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
        .replace("__BODY__", quick_reload.lua_long(quick_reload.BODY))
    )


def summary(cfg):
    return "reloadLine()"


def status(cfg):
    return (
        r"""(function()
  local f = _G.__hud and _G.__hud.feats.reload
  if not f or not f.on then return nil end
  return reloadLine() .. '\n  ' .. qrWiProbeSummary()
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
  out[#out + 1] = '  dialogues open right now = ' .. tostring(qrDialogueOpen() or 'none')
  out[#out + 1] = '  dialogues the last teardown closed = '
    .. tostring(_G.__qrClosedDialogues or 'none were open')
  if _G.__qrClosedOverlays ~= nil then
    out[#out + 1] = '  overlays = ' .. tostring(_G.__qrClosedOverlays)
  end
  out[#out + 1] = string.format('  input level: at start=%s before=%s after=%s target=%s',
    tostring(_G.__qrInputLevelAtStart), tostring(_G.__qrInputLevelBefore),
    tostring(_G.__qrInputLevelAfter), tostring(_G.__qrFinalTarget))
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
