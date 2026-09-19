#!/usr/bin/env python3
"""
pending_potential.py - read the Potentiflator result before walking the 1000 steps.

The Potentiflator decides a Coromon's new Potential **at the moment you hand it over**, not
when the steps are done. The community advice stops there ("save before handing it over and
reload"), which is correct but costs a full 1000-step walk per attempt to learn a result
that was already fixed before the first step.

It does not have to. The decided value is already applied to the Coromon, which then waits
in hidden monster storage until the steps finish, so it can simply be read:

    mon.potential                 the NEW value, already set
    mon.didRerollPotential        true - the reroll has happened
    effect:getOriginalPotential() the value before the reroll
    effect:hasReachedTargetPlayerSteps()   false - steps not walked yet

So the whole loop becomes: hand the Coromon over, run this, reload if it is not what you
want. The walk is only worth doing once the reading says it is.

    python pending_potential.py

Saving first is still what makes it work - the result is random per handover, and reloading
your save (your manual slot, not the autosave) rerolls it. The save carries no RNG state, so
a reload genuinely gets a fresh roll rather than replaying the same one.

Read-only: it calls only accessors (`getMonster`, `getOriginalPotential`, …) and never
mutates anything.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coromon_lua import GameGone  # noqa: E402
from scroll_fix import _bridge, _eval  # noqa: E402

PROBE = r"""
local out = {}
local function add(s) out[#out + 1] = s end

-- The save data is not reachable from any global, from Game/Save/the player, or from any
-- displayed object: it exists only as an upvalue of functions inside loaded modules. Collect
-- every match and keep the one that actually holds effects.
local best
for _, m in pairs(package.loaded) do
  if type(m) == 'table' then
    for _, f in pairs(m) do
      if type(f) == 'function' then
        for i = 1, 90 do
          local un, v = debug.getupvalue(f, i)
          if not un then break end
          if type(v) == 'table'
            and v.SAVEABLE_BATTLE_EFFECTS ~= nil and v.VISITED_MAPS ~= nil then
            local n = 0
            for _ in ipairs(v.SAVEABLE_BATTLE_EFFECTS) do n = n + 1 end
            if not best or n > best.n then best = { t = v, n = n } end
          end
        end
      end
    end
  end
end
if not best then return 'NOT_ATTACHED_SAVE' end

local okS, steps = pcall(function() return playerStats.getSteps() end)
add('steps walked: ' .. (okS and tostring(steps) or 'unknown'))

local found = 0
for _, e in ipairs(best.t.SAVEABLE_BATTLE_EFFECTS or {}) do
  local okc, cp = pcall(function() return e:getClassPath() end)
  if okc and tostring(cp):find('potentialReroll') then
    found = found + 1
    local okO, original = pcall(function() return e:getOriginalPotential() end)
    local okM, mon = pcall(function() return e:getMonster() end)
    if not okM or type(mon) ~= 'table' then
      add('a Potentiflator deposit is pending, but the Coromon could not be read')
    else
      local new = tonumber(mon.potential)
      local uid = tostring(mon.UID or '?')
      local okN, name = pcall(localise, 'monsters.' .. uid .. '.name')
      if not okN or type(name) ~= 'string' or name == '' or name == '???' then name = uid end
      add('coromon      : ' .. name .. '   (' .. uid .. ')')
      add('original     : ' .. (okO and tostring(original) or '?'))
      add('NEW          : ' .. (new and tostring(new) or '?'))
      add('didReroll    : ' .. tostring(mon.didRerollPotential))
      if new == 21 then
        add('VERDICT      : PERFECT - go and walk the steps, or collect it')
      else
        add('VERDICT      : not perfect - reload now, the walk would be wasted')
      end
    end
  end
end
if found == 0 then
  add('no Potentiflator deposit is pending (hand the Coromon over first)')
end
return table.concat(out, '\n')
"""


def main():
    try:
        bridge = _bridge()
    except Exception as exc:                       # noqa: BLE001 - report, do not crash
        print("could not attach to the game: %s" % exc)
        return 2
    try:
        print(_eval(bridge, PROBE, timeout=60.0))
    except GameGone:
        print("the game closed while reading")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
