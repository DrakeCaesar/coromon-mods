#!/usr/bin/env python3
"""
fog.py - switch off the darkness effect that some maps draw over the screen.

WHAT IT IS. Several maps are dark: a lit area around the character and a flat multiply-darkened
layer over the rest, with a soft round hole over you. All of it is one object in
`classes.world.worldDarkness`, exposed as the GLOBAL `worldDarkness`:

    .get() .isCreated() .createInstance()
    instance          the effect: a CLIPPING DISPLAY GROUP of 3 children
        child 1   alpha 0.5   blend 'multiply'   full-screen darkening
        child 2   alpha 1     blend 'normal'
        child 3   alpha 0.6   blend 'multiply'   the round vignette

Its rects and masks (`darknessRect`, `darknessInnerRect`, `setFlashlightMask`, `setBigFlashlightMask`,
`activeMode` for 'dark' / 'light' / 'brightLight') are closure LOCALS rather than fields, so the group
is the only handle on it from outside. Hiding the group switches the whole effect off and is trivially
reversible; `destroy()` is not used, because the effect also registers itself as a battle effect
(`battleEffectUtility:registerBattleEffect`) and rebuilds on world events.

HOW IT IS REACHED. `isCreated` and `get` both close over the module's own `instance`, so
`debug.getupvalue(wd.isCreated, 1)` names it. There is no exported accessor.

WHO TURNS IT ON, and why hiding the group is not enough on its own. Each dark map does it from its own
create function - `classes.maps.electricCave.electricCave_f1` lines 6-10:

    worldBattleEnvironment:createInstance(BattleEnvironment.electricCave)   -- L7
    worldDungeonDrips:createInstance()                                      -- L8
    worldDarkness:createInstance()                                          -- L9

`worldDarkness` is a GLOBAL there, which is also why searching the archive for the string
'classes.world.worldDarkness' finds almost nothing: the only module that spells the full path is
`classes.require_specific`. The maps that enable it are electricCave_f1 / _f1_A / _f1_B / _bf1,
electricTown.powerTower_f4 (and its #beforeOrWhileSOLVED variant), mescherRealm and developerHome.
So this also WRAPS `worldDarkness.createInstance` and hides whatever it builds.

WHY WRAP RATHER THAN NO-OP. `worldDarkness:get()` is used by the game's own lighting:
`classes.traits.traits.BRIGHT_LIGHT` and `classes.world.effects.items.headgearWorkerItemWorldEffect`
(the mining-helmet lamp) both reach into this instance to change the light mask. Making createInstance
return nothing would leave those looking at a nil instance. Hiding the group keeps all of it working,
you just cannot see the effect.

INSTALLED WHEREVER YOU HAPPEN TO BE, not just in a cave. `worldDarkness` only exists once a map that
uses it has loaded, so the wrap cannot always be put in place at install time. The install calls the
same `apply()` the tick does, and the tick keeps calling it, so entering a dark map later is enough.

NOTHING IS WRITTEN. No file is touched and nothing is saved; switching `enabled = false` puts the
effect and the original `createInstance` back.
"""

NAME = "fog"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    (
        "enabled",
        True,
        "hide the darkness/fog effect on the maps that draw it (electric cave, the electric "
        "power tower, the mescher realm, the developer home)",
    ),
]


def lua(cfg):
    """The queries. Always part of the chunk, so --status and --report can describe the effect
    whether or not the feature is installed."""
    return r"""
-- The module, which is a global (that is how the maps reach it).
local function fogModule()
  local wd = _G.worldDarkness
  if type(wd) ~= 'table' then return nil end
  return wd
end

-- The live effect. `isCreated` closes over the module's own `instance`, so the upvalue names it;
-- there is no exported accessor for it.
local function fogInstance()
  local wd = fogModule()
  if not wd or type(wd.isCreated) ~= 'function' then return nil end
  if type(debug) ~= 'table' or type(debug.getupvalue) ~= 'function' then return nil end
  local nm, v = debug.getupvalue(wd.isCreated, 1)
  if nm == 'instance' then return v end
  return nil
end

local function fogWrapped()
  return type(_G.__hudFogOrig) == 'function'
end

-- One line, for the installer's summary and --status.
local function fogLine()
  if type(fogModule()) ~= 'table' then
    return 'darkness/fog - no worldDarkness on this map, nothing to hide'
  end
  local inst = fogInstance()
  local visible
  if type(inst) == 'table' then visible = tostring(inst.isVisible) else visible = 'none up' end
  return string.format('darkness/fog off (effect visible = %s, createInstance wrapped = %s)',
    visible, tostring(fogWrapped()))
end
"""


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return r"""
do
  -- period 0 would mean "never", so a second: this has no per-frame work, but it does have to keep
  -- checking, because worldDarkness only appears once a map that uses it has loaded.
  local f = makeFeature('fog', 1000)

  -- Hides the effect and makes sure anything created later gets hidden too. Idempotent: it is called
  -- once here and then every tick, and a repeat must not re-record the "before" value or double-wrap.
  local function apply()
    local wd = fogModule()
    if type(wd) ~= 'table' then return 'no worldDarkness on this map' end

    local inst = fogInstance()
    if type(inst) == 'table' then
      -- Recorded ONCE, before the first hide: this is what kill() puts back, so it has to be the
      -- game's own value rather than whatever we last set.
      if type(_G.__hudFogSaved) ~= 'table' then
        _G.__hudFogSaved = { isVisible = inst.isVisible }
      end
      inst.isVisible = false
    end

    if type(wd.createInstance) == 'function' and not fogWrapped() then
      local orig = wd.createInstance
      _G.__hudFogOrig = orig
      wd.createInstance = function(self, ...)
        -- Repacked rather than forwarded: in Lua 5.1 `...` is not visible inside a nested function
        -- that is not itself vararg, and using it there fails the whole chunk to compile.
        local argc = select('#', ...)
        local argv = { ... }
        local r = orig(self, unpack(argv, 1, argc))
        local made = fogInstance()
        if type(made) == 'table' then made.isVisible = false end
        return r
      end
    end
    f.applied = true
    return 'ok'
  end

  -- Puts the game back exactly as it was: the original createInstance, and the effect's own
  -- visibility. Run by the host when this feature is switched off in overlays.toml.
  local function kill()
    local wd = fogModule()
    if type(_G.__hudFogOrig) == 'function' and type(wd) == 'table' then
      wd.createInstance = _G.__hudFogOrig
    end
    _G.__hudFogOrig = nil
    local inst = fogInstance()
    if type(inst) == 'table' then
      inst.isVisible = (type(_G.__hudFogSaved) == 'table' and _G.__hudFogSaved.isVisible) ~= false
    end
    _G.__hudFogSaved = nil
    f.applied = false
    f.on = false
  end

  f.applied = (apply() == 'ok')
  f.update, f.kill, f.on = apply, kill, true
end
"""


def summary(cfg):
    return "fogLine()"


def status(cfg):
    return (
        r"""(function()
  local f = _G.__hud and _G.__hud.feats.fog
  if not f or not f.on then return nil end
  return fogLine()
end)()"""
    )


def report(cfg):
    return r"""(function()
  local out = {}
  local wd = fogModule()
  out[#out + 1] = 'darkness / fog'
  if type(wd) ~= 'table' then
    out[#out + 1] = '  no worldDarkness global on this map, so no map here draws the effect'
    out[#out + 1] = '  (the maps that do: electric cave f1 / _A / _B / _bf1, the electric power'
    out[#out + 1] = '   tower f4, the mescher realm and the developer home)'
    return table.concat(out, '\n')
  end
  local ex = {}
  for k, v in pairs(wd) do ex[#ex + 1] = k .. ':' .. type(v) end
  table.sort(ex)
  out[#out + 1] = '  worldDarkness exports = ' .. table.concat(ex, ', ')
  local inst = fogInstance()
  if type(inst) == 'table' then
    out[#out + 1] = string.format('  effect: visible = %s, alpha = %s, children = %s',
      tostring(inst.isVisible), tostring(inst.alpha), tostring(inst.numChildren))
    for i = 1, math.min(inst.numChildren or 0, 12) do
      local c = inst[i]
      if c ~= nil then
        out[#out + 1] = string.format('    child %d: alpha = %s, visible = %s, blend = %s',
          i, tostring(c.alpha), tostring(c.isVisible), tostring(c.blendMode))
      end
    end
    out[#out + 1] = string.format('  the game had it visible = %s',
      tostring(type(_G.__hudFogSaved) == 'table' and _G.__hudFogSaved.isVisible or nil))
  else
    out[#out + 1] = '  no effect is up right now'
  end
  out[#out + 1] = '  createInstance wrapped = ' .. tostring(fogWrapped())
  out[#out + 1] = '  the wrap is what keeps it off when another dark map loads; each such map'
  out[#out + 1] = '  calls worldDarkness:createInstance() from its own create function.'
  out[#out + 1] = '  nothing is written to disk, and enabled = false puts all of it back.'
  return table.concat(out, '\n')
end)()"""
