#!/usr/bin/env python3
"""
guard.py - stop the interaction button's background swap from crashing the game, and record what it
was that broke it.

THE CRASH. Pressing the world interface's interaction button - the "press to interact" prompt - raises
this, and it takes the game down:

    groupHelper.lua:46: attempt to call method 'insert' (a nil value)
      groupHelper.setObjectContainer
        UIContainerBuilder.setStyle
          WorldInterfaceInteractButton.refreshInteractButtonBackgroundStyle
            WorldInterfaceInteractButton.setPressed
              worldInterface.onPress          <- a press on the interact button
                ... navigation / inputHelper / eventManager ...

        function t.setObjectContainer(self, _object, _container)     -- groupHelper lines 43-48
          _object.container = display.remove(_object.container)      -- L44  tear out the old one
          _object.container = _container                             -- L45  put the new one in
          _object.container:insert(_object)                          -- L46  <-- crashes here
          _object.container:toBack()                                 -- L47
        end

        currentStyle = _newStyle                                     -- UIContainerBuilder L26
        groupHelper:setObjectContainer(                              -- L28
            self,
            _newStyle:createContainer(_newWidth or _width, _newHeight or _height))

The button swaps between three background styles (normal, hover, pressed) and rebuilds its container
to do it. The styles are not private to the button: all three are the SHARED globals
`SpeakerStyle.player:getDialogContainerStyle` / `...HoverStyle` / `...PressedStyle`, which resolve to
cached `UIContainerStyle` entries, so one disposed container can be handed to any button that shares a
style. The error text is "attempt to call method 'insert' (a nil value)", NOT "attempt to index a nil
value": the object existed and had no methods left, which is what a DISPOSED Solar2D display object
looks like from Lua.

THE CASE THAT FITS THE BYTECODE. L44 disposes the object's container on the way past, and L46 inserts
into a container that L45 has just copied from the argument - so when the container handed in IS the
one already on the object, L44 disposes the very object L46 is about to insert into, and `insert` is
gone by then. Setting a style that is already set is a no-op, so that call is skipped rather than
repaired (`re-applied` in --report counts it). That is a fix, not a net.

THREE LAYERS, because each covers a different way the last one can be skipped:
  1. the style being set is the one already on the object -> skip the call (the L44/L46 case above).
     Skipping is safe here precisely because the container left behind is a real one.
  2. the container is unusable (no `insert`) -> ask the STYLE for another one (`_object:getStyle():
     createContainer(w, h)`), which is the only object that satisfies the pipeline; see below.
  3. the wrapper itself is bypassed -> the call is made through `pcall`, and the recovery puts a real
     container back. This is why a crash cannot get past even if 1 and 2 both miss: the crash is an
     ordinary Lua error.

NEVER FABRICATE A STAND-IN. That is the lesson this file paid for twice, and it has one shape: the
consumer of the value reads a CONTRACT, not just a non-nil reference.

  * attempt 1, refusing. `_container` looked bad, so the guard returned early - leaving the builder's
    container nil. `setStyle` then handed that nil on, and the game crashed at
    `UIContainerStyle.lua:305` on the title screen.
  * attempt 2, substituting. The guard put a `rectHelper` container (then a plain `display.newGroup()`)
    in its place. That inserts fine but has no `.containers`, and L305 reads exactly
    `_UIContainerContainer.containers[i]`, so the crash moved into the style instead:
    "attempt to index field 'containers' (a nil value)" - after a few quick reloads, on pressing the
    interact button.

`.containers` and `setFillColor` / `setWidth` / `setHeight` / `setSize` are attached by
`UIContainerStyleCombinedValueObject.createContainer` itself (L274-299), so they exist ONLY on an
object that came from a style. Hence layer 2 asks the style, and if the style cannot produce one and
the container already on the object is real, the call is skipped and that one kept - never replaced
with something merely insertable.

WHY THE FIRST VERSION WAS SILENT, AND WHY THAT MATTERED. It armed itself once, at install time, and
the crash happened anyway - after a quick reload, on pressing the interact button. Two ways it could
be silent, and both were built in:

  * install time. The install runs the moment the game's Lua is up, which is around the splash
    screen. If `_G.groupHelper` did not exist yet, the guard set an internal `missing` flag - and
    `summary()` said "nothing caught" either way, so a guard that was never armed was
    indistinguishable from a guard that had nothing to do. The summary now says NOT ARMED and why.
  * the table can be replaced. `UIContainerBuilder` reaches the function through the GLOBAL
    `groupHelper` (GETGLOBAL, L13 and L28), so wrapping the table we find is what puts us on the
    call path. A module re-require can put a fresh table with the original function in that global,
    and the wrapper goes quiet with it.

So it is a WATCHDOG: every 200 ms it checks that the global still holds our function and re-arms
(re-saving the original) if it does not, counting how often. `--report` distinguishes armed / not
armed / re-arms / calls seen / re-applied / bad containers seen / replaced / kept as-is /
unrecoverable / errors caught, so a silent guard cannot happen again - and the next occurrence says
which layer caught it and what the container was.

Still a safety net in the end: the cause is in the game, and layer 2 costs the element nothing while
layer 3 leaves it unstyled. It is worth having on while playing, because the alternative is a crash
dialog and a lost session.
"""

NAME = "guard"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    (
        "enabled",
        True,
        "stop `groupHelper.setObjectContainer` crashing the game when it is handed a container that "
        "has no insert, and record what it was",
    ),
]


def lua(cfg):
    """The queries. Always part of the chunk, so --status and --report can describe it whether or not
    the feature is installed."""
    return r"""
-- What the guard is doing, as a one-line summary for the installer and --status. It says NOT ARMED
-- rather than the quiet 'nothing caught' it used to: a guard that never armed and a guard with
-- nothing to do looked identical, and that is how the crash below got through.
local function guardLine()
  local rec = _G.__hudGuard
  if type(rec) ~= 'table' then return 'container guard: not installed' end
  if (rec.arms or 0) == 0 then
    return 'container guard: NOT ARMED (' .. tostring(rec.why or '?') .. ')'
  end
  local s = string.format('container guard: armed %d, %d calls, %d re-applied, %d bad container',
    rec.arms, rec.calls or 0, rec.same or 0, rec.bad or 0)
  if (rec.fresh or 0) > 0 then s = s .. string.format(', %d replaced', rec.fresh) end
  if (rec.keptOld or 0) > 0 then s = s .. string.format(', %d kept as-is', rec.keptOld) end
  if (rec.noContainer or 0) > 0 then
    s = s .. string.format(', %d UNRECOVERABLE', rec.noContainer)
  end
  if (rec.errors or 0) > 0 then s = s .. string.format(', %d errors caught', rec.errors) end
  return s
end
"""


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return r"""
do
  -- A watchdog rather than an install-time-only feature. The global it wraps may not exist yet when
  -- the installer runs (the install happens around the splash screen), and it can be replaced later
  -- by a module re-require. Re-arming on every tick costs nothing and cannot be silently lost; 200 ms
  -- is the host's own tick, so `every` works out at 1.
  local f = makeFeature('guard', 200)
  local OUT = _G.__hudGuard
  if type(OUT) ~= 'table' then
    OUT = { n = 0, calls = 0, arms = 0 }
    _G.__hudGuard = OUT
  end

  -- Describing the bad container is the point: the type alone does not identify it, so the fields
  -- it does have are listed. `container` and `containerClass` are named first when present because
  -- they are what a style carries.
  local function describe(x)
    if type(x) ~= 'table' then return type(x) end
    local keys = {}
    for k in pairs(x) do keys[#keys + 1] = tostring(k) end
    table.sort(keys)
    if #keys > 14 then
      local head = {}
      for i = 1, 14 do head[i] = keys[i] end
      keys = head
      keys[#keys + 1] = '...'
    end
    return 'table{' .. table.concat(keys, ', ') .. '}'
  end

  -- What the crash is. `_object.container:insert(_object)` raises "attempt to call method 'insert'
  -- (a nil value)", NOT "attempt to index a nil value" - so the object existed and had no methods
  -- left. That is a DISPOSED Solar2D display object, one that has been display.remove()d and is still
  -- being handed around: reading a method off one yields nil. Type-agnostic on purpose, because
  -- whether a display object reports as 'table' or 'userdata' is not worth betting the guard on -
  -- the read itself is the test, and it goes through a pcall because a disposed object can throw on
  -- the read rather than return nil.
  local function unusable(x)
    if x == nil then return true end
    local t = type(x)
    if t ~= 'table' and t ~= 'userdata' then return true end
    local ok, ins = pcall(function() return x.insert end)
    if not ok then return true end
    return type(ins) ~= 'function'
  end

  -- THE ONLY SOUND STAND-IN: ask the style for another one. This is the second correction to the same
  -- mistake, so it is worth spelling out. `setStyle` (UIContainerBuilder) does:
  --
  --   28  groupHelper:setObjectContainer(self, currentStyle:createContainer(w, h))
  --   30  display.applyDisplayObjectMutations(parentGroup.container, _containerMutator)
  --   32  currentStyle:onAfterUIContainerUpdatedOrCreated(parentGroup.container)   <-- reads it again
  --
  -- and a combined style reads a FIELD off it (UIContainerStyle L305):
  --
  --   305  _styleConfigs[i].style:onAfterUIContainerUpdatedOrCreated(_UIContainerContainer.containers[i])
  --
  -- `.containers` - and `setFillColor` / `setWidth` / `setHeight` / `setSize` - are attached by
  -- `createContainer` itself, at L274-299, so they exist ONLY on an object that came from the style.
  -- A fabricated `rectHelper` container or a plain `display.newGroup()` has no `.containers`, and the
  -- result is a fresh crash at UIContainerStyle.lua:305: "attempt to index field 'containers' (a nil
  -- value)". Substituting something that merely *inserts* is not enough; it has to satisfy the
  -- contract the next consumer reads. Refusing was the same error wearing a different hat.
  local function freshContainer(o)
    if OUT.inFresh then return nil end
    OUT.inFresh = true
    local c
    pcall(function()
      local st = o:getStyle()                       -- UIContainerBuilder L17-19, the current style
      if type(st) ~= 'table' or type(st.createContainer) ~= 'function' then return end
      c = st:createContainer(o.width, o.height)
    end)
    OUT.inFresh = nil
    if unusable(c) then return nil end
    return c
  end

  -- Arm on whatever the global holds now. Called every tick, and a no-op when the global still
  -- holds our own wrapper, so a re-require is picked up as soon as it happens.
  local function arm()
    local gh = _G.groupHelper
    if type(gh) ~= 'table' then return 'no groupHelper global yet' end
    if type(gh.setObjectContainer) ~= 'function' then
      return 'groupHelper.setObjectContainer is not a function'
    end
    if gh.setObjectContainer == gh.__hudGuard then return nil end

    local orig = gh.setObjectContainer
    gh.__hudGuardOrig = orig
    gh.__hudGuard = function(self, _object, _container)
      OUT.calls = (OUT.calls or 0) + 1
      local old = type(_object) == 'table' and _object.container or nil

      -- THE CASE THAT FITS THE BYTECODE. L44 disposes the object's container on the way past, and
      -- L46 inserts into a container that L45 has just copied from the argument:
      --
      --   44  _object.container = display.remove(_object.container)
      --   45  _object.container = _container
      --   46  _object.container:insert(_object)      <-- crashes, no `insert` left
      --
      -- so if the container handed in IS the one already on the object, L44 disposes the very
      -- object L46 is about to insert into, and `insert` is gone by the time it is called. Setting a
      -- style that is already set is a no-op by definition, so skip the call: this one is a real fix,
      -- nothing loses its styling, and `re-applied` in --report counts how often it happens. Skipping
      -- is safe here precisely because the container left behind is a real one, which is what L32
      -- goes on to read.
      if old ~= nil and old == _container then
        OUT.same = (OUT.same or 0) + 1
        OUT.sameWhere = debug and debug.traceback and debug.traceback('', 2) or '?'
        return
      end

      -- ONLY the new container is checked. The object's own `container` is nil the first time a
      -- builder is styled - `UIContainerBuilder.new` (L40) calls setStyle straight after
      -- `groupHelper:newObject` (L13), which does not set one - so testing it refused EVERY
      -- first-time style set, and the refusal left the builder's container nil, which crashed one
      -- frame later ("attempt to index local 'UIContainer'") on the title screen, as the splash
      -- screen faded. Measured: that crash was caused by this guard, not by the game.
      if unusable(_container) then
        OUT.bad = (OUT.bad or 0) + 1
        OUT.newKind = describe(_container)
        OUT.oldKind = describe(old)
        OUT.style = describe(_object)
        OUT.last = string.format('new=%s old=%s', OUT.newKind, OUT.oldKind)
        OUT.where = debug and debug.traceback and debug.traceback('', 2) or '?'

        local fresh = freshContainer(_object)
        -- `freshSame` is the discriminating detail: if the style hands back the SAME object, the
        -- style itself is holding a corpse and re-asking cannot help; if it hands back a different
        -- one, the bad container was a stale leftover and this recovery is the fix.
        OUT.oldUsable = not unusable(old)
        if fresh then
          -- The game handed back a container it had already disposed; ask the style for another.
          OUT.fresh = (OUT.fresh or 0) + 1
          if fresh == _container then OUT.freshSame = (OUT.freshSame or 0) + 1 end
          _container = fresh
        elseif not unusable(old) then
          -- Nothing to build one from, but the container already on the object is real, and L32
          -- reads that one. Keep it and skip: the element keeps the look it has, which beats
          -- handing the pipeline an object of the wrong kind.
          OUT.keptOld = (OUT.keptOld or 0) + 1
          return
        else
          -- Nothing usable anywhere. Call through anyway so the traceback is the game's own, and let
          -- the pcall below contain what it can.
          OUT.noContainer = (OUT.noContainer or 0) + 1
        end
      end

      -- Belt and braces, and the reason a crash cannot get past this a second time. The checks above
      -- only help while this wrapper is on the call path, and being bypassed is exactly what happened
      -- before. The crash is an ordinary Lua error, so a pcall contains it; the recovery then leaves
      -- a REAL container behind, because the caller reads `parentGroup.container.width` on its next
      -- line and the style reads `.containers` off it two lines later.
      local ok, err = pcall(orig, self, _object, _container)
      if not ok then
        OUT.errors = (OUT.errors or 0) + 1
        OUT.err = tostring(err)
        OUT.errWhere = debug and debug.traceback and debug.traceback('', 2) or '?'
        local fresh = freshContainer(_object)
        if fresh then
          pcall(orig, self, _object, fresh)
        elseif not unusable(old) then
          pcall(function() _object.container = old end)
        end
      end
    end
    gh.setObjectContainer = gh.__hudGuard
    OUT.arms = (OUT.arms or 0) + 1
    return nil
  end

  -- The tick. The host also runs this once as part of the install, so `armed` is already true by
  -- the time the installer prints its summary.
  local function tick()
    OUT.why = arm()
    OUT.armed = (OUT.why == nil)
  end
  f.update = tick

  local function kill()
    local gh = _G.groupHelper
    if type(gh) == 'table' then
      if gh.setObjectContainer == gh.__hudGuard and type(gh.__hudGuardOrig) == 'function' then
        gh.setObjectContainer = gh.__hudGuardOrig
      end
      gh.__hudGuard, gh.__hudGuardOrig = nil, nil
    end
    OUT.armed, OUT.arms, OUT.why = false, 0, 'disabled'
    f.on = false
  end

  f.kill, f.on = kill, true
end
"""


def summary(cfg):
    return "guardLine()"


def status(cfg):
    return (
        r"""(function()
  local f = _G.__hud and _G.__hud.feats.guard
  if not f or not f.on then return nil end
  return guardLine()
end)()"""
    )


def report(cfg):
    return r"""(function()
  local out = {}
  local gh = _G.groupHelper
  out[#out + 1] = 'container guard - groupHelper.setObjectContainer'
  local rec = _G.__hudGuard
  if type(rec) ~= 'table' then
    out[#out + 1] = '  not installed (no record; the feature is off, or was never loaded)'
    return table.concat(out, '\n')
  end
  if not rec.armed then
    out[#out + 1] = '  NOT ARMED - ' .. tostring(rec.why or '?')
    out[#out + 1] = '  it re-checks every 200 ms, so this means the global it needs is still absent'
    return table.concat(out, '\n')
  end
  out[#out + 1] = string.format('  armed %d time(s) - more than 1 means the game replaced the',
    rec.arms or 0)
  out[#out + 1] = '  module table (a re-require) and the wrapper was put back'
  out[#out + 1] = string.format('  calls seen = %d', rec.calls or 0)
  out[#out + 1] = string.format('  re-applied (style already set, call skipped) = %d', rec.same or 0)
  out[#out + 1] = string.format('  bad containers handed in = %d', rec.bad or 0)
  out[#out + 1] = string.format('    of those, replaced by asking the style again = %d', rec.fresh or 0)
  out[#out + 1] = string.format('    kept the container already there = %d', rec.keptOld or 0)
  out[#out + 1] = string.format('    nothing usable could be found = %d', rec.noContainer or 0)
  if (rec.fresh or 0) > 0 then
    out[#out + 1] = string.format('    of the replaced ones, the style returned the same object = %d',
      rec.freshSame or 0)
    out[#out + 1] = '    (same object means the style is holding a dead container itself, and re-asking'
    out[#out + 1] = '     cannot help - a different object means the bad one was a stale leftover)'
  end
  out[#out + 1] = '  the container already on the object was usable = ' .. tostring(rec.oldUsable)
  if (rec.noContainer or 0) > 0 then
    out[#out + 1] = '  UNRECOVERABLE means the game handed over a dead container and the style could'
    out[#out + 1] = '  not produce another - expect an unstyled element, or a crash of the game\'s own.'
  end
  if (rec.same or 0) > 0 and rec.sameWhere then
    out[#out + 1] = '    last re-applied came from:'
    for line in tostring(rec.sameWhere):gmatch('[^\n]+') do
      out[#out + 1] = '    ' .. line
    end
  end
  if type(gh) == 'table' and gh.setObjectContainer == gh.__hudGuard then
    out[#out + 1] = '  and the global still holds our function, so calls do come through it'
  else
    out[#out + 1] = '  but the global no longer holds our function'
  end
  if (rec.errors or 0) > 0 then
    out[#out + 1] = string.format('  errors caught in the call itself = %d', rec.errors)
    out[#out + 1] = '    last: ' .. tostring(rec.err)
    if rec.errWhere then
      for line in tostring(rec.errWhere):gmatch('[^\n]+') do
        out[#out + 1] = '    ' .. line
      end
    end
  end
  if (rec.bad or 0) > 0 then
    out[#out + 1] = '  last bad container: new = ' .. tostring(rec.newKind)
    out[#out + 1] = '                      old = ' .. tostring(rec.oldKind)
    out[#out + 1] = '                      obj = ' .. tostring(rec.style)
    if rec.where then
      out[#out + 1] = '  where it came from:'
      for line in tostring(rec.where):gmatch('[^\n]+') do
        out[#out + 1] = '    ' .. line
      end
    end
  end
  out[#out + 1] = '  recovering by asking the style for another container keeps the element styled;'
  out[#out + 1] = '  falling back to the container already there leaves it with the look it had.'
  out[#out + 1] = '  Either way the cause is still in the game: something disposed a container that was'
  out[#out + 1] = '  still being handed around.'
  return table.concat(out, '\n')
end)()"""
