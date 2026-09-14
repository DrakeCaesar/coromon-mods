#!/usr/bin/env python3
"""
coromon_starter.py - read the Potential of the three starter Coromon in a
running Coromon (Solar2D) game, so you can soft-reset until you roll a good set.

How it works
------------
Coromon runs on Solar2D, so the whole game is Lua 5.1 living in `lua.dll`.
This tool attaches with Frida, captures the game's `lua_State`, and then runs a
small snippet *inside the game* that walks from the "coromon lab" map module to
the three monster spawnables and reads their `potential` field (0..21).

Usage
-----
    python coromon_starter.py              # print the current 3 starters once
    python coromon_starter.py --watch      # keep printing whenever the roll changes
    python coromon_starter.py --verbose    # show where the values came from

Then: reload your save, walk to the reveal, and read the numbers.
"""
import argparse
import re
import sys
import time

import frida

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from coromon_lua import Bridge  # noqa: E402

FIND_STARTERS = r"""
local roots, out = {}, {}
for k, v in pairs(package.loaded) do
  if type(k) == 'string' and k:find('coromonLab') and type(v) == 'table' then
    roots[#roots + 1] = {v = v, p = tostring(k)}
  end
end
if #roots == 0 then return '!NOMAP!' end

-- FORCE = target potential (or nil). Written with `setPotential`, which is just
-- `self.potential = v`: getPotential() clamps that field to 1..21 and the stat
-- points themselves are handed out by the game as the Coromon reaches
-- potential levels, so a forced value behaves exactly like a natural roll.
local FORCE = __FORCE__
local rolled = _G.__coromon_rolled
if type(rolled) ~= 'table' then rolled = {}; _G.__coromon_rolled = rolled end

local seen, seenF, q = {}, {}, {}
local function push(v, p, d)
  local tv = type(v)
  if tv == 'table' then
    if seen[v] then return end
    seen[v] = true
  elseif tv == 'function' then
    if seenF[v] then return end
    seenF[v] = true
  else
    return
  end
  q[#q + 1] = {v = v, p = p, d = d}
end
for _, r in ipairs(roots) do push(r.v, r.p, 0) end

local found, pots, order = {}, {}, {}
local head, nodes, MAXN, MAXD = 1, 0, 150000, 7
local t0 = os.clock()
while head <= #q and nodes < MAXN and (os.clock() - t0) < 5.0 do
  local e = q[head]; head = head + 1; nodes = nodes + 1
  local t = e.v
  if type(t) == 'table' then
    local pot = rawget(t, 'potential')
    if type(pot) == 'number' and e.p:find('interact_starter_') then
      local name = e.p:match('interact_starter_([A-Za-z0-9_]+)')
      if name then
        local score = 0
        if e.p:find('createdMonster') then score = 3
        elseif e.p:find('getOrCreateMonster') then score = 2
        elseif e.p:find('consistentSaveProperties') then score = 1 end
        local extra = ''
        if score >= 2 then
          local ks = {}
          for k, v in pairs(t) do
            local tv = type(v)
            if (tv == 'number' or tv == 'string' or tv == 'boolean') and k ~= 'potential' then
              ks[#ks + 1] = tostring(k) .. '=' .. tostring(v)
            end
          end
          table.sort(ks)
          local mm = rawget(t, 'monsterMeta')
          if type(mm) == 'table' then
            local n = rawget(mm, 'name') or rawget(mm, 'id')
            if type(n) == 'string' then ks[#ks + 1] = 'meta=' .. n end
          end
          extra = table.concat(ks, ',')
        end
        if not found[name] then found[name] = {}; pots[name] = {}; order[#order + 1] = name end
        -- only count real monster objects here: the spawnable's saved copy is
        -- expected to be a separate value and must not raise a stale warning
        if score >= 2 then pots[name][pot] = true end
        if rolled[name] == nil then rolled[name] = pot end
        if FORCE and pot ~= FORCE then
          if score >= 2 then
            pcall(function() t:setPotential(FORCE) end)
            if rawget(t, 'potential') ~= FORCE then rawset(t, 'potential', FORCE) end
          else
            rawset(t, 'potential', FORCE)   -- spawnable's saved copy
          end
        end
        local shown = FORCE or pot
        local best = found[name][1]
        -- prefer the real monster over the spawnable's saved copy, then the
        -- shallowest hit (a freshly rolled world is reached first)
        if not best or score > best.score or (score == best.score and e.d < best.depth) then
          found[name][1] = {potential = shown, score = score, path = e.p, extra = extra,
                            depth = e.d, rolled = rolled[name]}
        end
      end
    end
  end
  if e.d < MAXD then
    if type(t) == 'table' then
      for k, v in pairs(t) do
        push(v, e.p .. '.' .. ((type(k) == 'string') and k or ('[' .. tostring(k) .. ']')), e.d + 1)
      end
    elseif type(t) == 'function' then
      local i = 1
      while true do
        local n, uv = debug.getupvalue(t, i)
        if not n then break end
        push(uv, e.p .. '<' .. n .. '>', e.d + 1)
        i = i + 1
      end
    end
  end
end

table.sort(order)
if #order == 0 then
  _G.__coromon_rolled = nil          -- forget the roll so a reload reports afresh
  return '!NONE! nodes=' .. nodes
end
out[#out + 1] = 'RESULT'
for _, name in ipairs(order) do
  local f = found[name][1]
  out[#out + 1] = string.format('%s\t%d\t%s\t%s\t%s', name, f.potential, f.extra, f.path,
                                tostring(f.rolled))
  -- flag leftovers from a previous world (should not normally happen)
  local vals = {}
  for v in pairs(pots[name]) do vals[#vals + 1] = tostring(v) end
  if #vals > 1 then
    table.sort(vals)
    out[#out + 1] = string.format('WARN\t%s\t%s', name, table.concat(vals, ','))
  end
end
return table.concat(out, '\n')
"""

ORDER = ["FIRE_TURTLE_1", "WATER_SHARK_1", "ICE_BEAR_1"]


def find_code(force=None):
    """FIND_STARTERS with the FORCE target substituted in."""
    if force is None:
        return FIND_STARTERS.replace("__FORCE__", "nil")
    return FIND_STARTERS.replace("__FORCE__", str(int(force)))


def parse(out):
    """Parse the Lua result into {name: (potential, extra, path, rolled)}."""
    res = {}
    for line in (out or "").splitlines():
        if line.startswith("!") or line == "RESULT":
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            pot = int(parts[1])
        except ValueError:
            continue
        extra = parts[2] if len(parts) > 2 else ""
        path = parts[3] if len(parts) > 3 else ""
        rolled = None
        if len(parts) > 4 and parts[4].isdigit():
            rolled = int(parts[4])
        res[name] = (pot, extra, path, rolled)
    return res


def parse_warnings(out):
    """Lines flagged by the Lua side when several candidates disagree."""
    warn = []
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] == "WARN":
            warn.append((parts[1], parts[2]))
    return warn


def fmt(name, pot, rolled=None):
    tag = ""
    if pot >= 21:
        tag = "  <-- PERFECT"
    elif pot >= 20:
        tag = "  <-- potent"
    note = f"   (natural roll {rolled})" if rolled is not None and rolled != pot else ""
    return f"  {name:<16} potential {pot:>2}{tag}{note}"


# --------------------------------------------------------------------------
# on-screen overlay
#
# Coromon's `display` global is a wrapper around Solar2D's display library and
# has no newText; text is built through Coromon's own `textHelper`:
#     textHelper:new(parentGroup, fontType, { text = "..." })
# Objects added to `display.getCurrentStage()` are drawn on top of everything
# (world, UI, dialogs), which is exactly what we want for an overlay.
# --------------------------------------------------------------------------
OVERLAY_LUA = r"""
local FONT = 'outline_10_bold'
local LINES = __LINES__
local COLS = __COLS__
local LINE_H, PAD_X, PAD_Y, TEXT_H = 17, 10, 7, 18

local stage = display.getCurrentStage()
local ov = _G.__coromon_overlay
if ov then
  -- the game can tear our group down when it reloads a save / changes scene
  local alive = false
  pcall(function() alive = ov.group ~= nil and ov.group.parent ~= nil end)
  if not alive then ov = nil; _G.__coromon_overlay = nil end
end
if not ov then
  local g = display.newGroup()
  local bg = display.newRect(0, 0, 120, 60)
  bg.anchorX, bg.anchorY = 0, 0
  pcall(function() bg:setFillColor(0, 0, 0, 0.75) end)
  g:insert(bg)
  g.x, g.y = 6, 6
  ov = {group = g, bg = bg, texts = {}, key = nil}
  _G.__coromon_overlay = ov
end

local key = table.concat(LINES, '|')
if ov.key ~= key then
  for i = 1, #ov.texts do pcall(function() ov.texts[i]:removeSelf() end) end
  ov.texts = {}
  local maxw = 0
  for i = 1, #LINES do
    local t = textHelper:new(ov.group, FONT, {text = LINES[i]})
    t.x, t.y = PAD_X, PAD_Y + (i - 1) * LINE_H
    local c = COLS[i]
    if c then pcall(function() t:setFillColor(c[1], c[2], c[3]) end) end
    if (t.width or 0) > maxw then maxw = t.width end
    ov.texts[#ov.texts + 1] = t
  end
  ov.bg.width = maxw + PAD_X * 2
  ov.bg.height = PAD_Y * 2 + (#LINES - 1) * LINE_H + TEXT_H
  ov.key = key
end

-- re-attach at the end of the stage so the game cannot draw over us
local g = ov.group
local reattached = pcall(function()
  if g.parent then g:removeSelf() end
  stage:insert(g)
end)
if not reattached then
  _G.__coromon_overlay = nil
  return 'overlay lost, will be rebuilt'
end
return string.format('overlay ok (%d lines, %s)', #ov.texts, tostring(g))
"""

REMOVE_OVERLAY_LUA = r"""
local ov = _G.__coromon_overlay
if ov and ov.group then pcall(function() ov.group:removeSelf() end) end
_G.__coromon_overlay = nil
return 'overlay removed'
"""


def _lua_str(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def overlay_code(lines, colors):
    lines_lua = "{" + ", ".join(_lua_str(l) for l in lines) + "}"
    cols_lua = "{" + ", ".join("{%s, %s, %s}" % tuple(c) for c in colors) + "}"
    return OVERLAY_LUA.replace("__LINES__", lines_lua).replace("__COLS__", cols_lua)


WHITE = (1, 1, 1)
GREEN = (0.45, 1, 0.5)
GOLD = (1, 0.85, 0.3)


def overlay_lines(data, force=None):
    """Turn the parsed roll into display lines + per-line colours."""
    head = "COROMON STARTERS" if not force else f"COROMON STARTERS -> {force}"
    if not data:
        return [head, "waiting for the", "starter reveal ..."], [GOLD, WHITE, WHITE]
    names = [n for n in ORDER if n in data] or sorted(data)
    lines, cols = [head], [GOLD]
    for n in names:
        pot = data[n][0]
        lines.append(f"{n:<14}{pot:>3}")
        cols.append(GOLD if pot >= 21 else (GREEN if pot >= 20 else WHITE))
    best = max(v[0] for v in data.values())
    if best >= 21:
        lines.append("PERFECT (21)!")
        cols.append(GOLD)
    elif best >= 20:
        lines.append("potent (20)!")
        cols.append(GREEN)
    return lines, cols


def stamp():
    return time.strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="coromon.exe")
    ap.add_argument("--once", action="store_true",
                    help="read the roll a single time and exit")
    ap.add_argument("--watch", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-overlay", action="store_true",
                    help="do not draw the values on top of the game")
    ap.add_argument("--clear-overlay", action="store_true",
                    help="remove the on-screen overlay and exit")
    ap.add_argument("--perfect", action="store_true",
                    help="force every starter to potential 21")
    ap.add_argument("--force-potential", type=int, default=None, metavar="N",
                    help="force every starter to potential N (1-21)")
    args = ap.parse_args()
    continuous = not args.once

    if args.force_potential is not None and not 1 <= args.force_potential <= 21:
        ap.error("--force-potential must be between 1 and 21")
    force = args.force_potential if args.force_potential is not None else (21 if args.perfect else None)

    try:
        b = Bridge(args.process)
    except Exception as e:
        print(f"could not attach to {args.process}: {e}")
        print("is the game running?")
        return 1

    # wait until we captured the lua_State from the game's own calls
    state = None
    for _ in range(200):
        st = b.status()
        if st.get("state"):
            state = st["state"]
            break
        time.sleep(0.15)
    if not state:
        print("could not capture the game's lua_State (is the game paused?)")
        return 1

    if args.clear_overlay:
        r = b.eval(REMOVE_OVERLAY_LUA, timeout=15.0)
        print(r.get("out") or r.get("err"))
        b.detach()
        return 0

    use_overlay = not args.no_overlay
    drew_overlay = False

    def show_overlay(data):
        nonlocal drew_overlay
        if not use_overlay:
            return
        lines, cols = overlay_lines(data, force)
        r = b.eval(overlay_code(lines, cols), timeout=15.0)
        if r.get("err") and not r.get("out"):
            print("[overlay] lua error:", r["err"])
        else:
            drew_overlay = True

    last = None
    rolls = 0
    waiting = False
    print(f"[{stamp()}] attached to {args.process} (lua_State {state})")
    if force:
        print(f"[{stamp()}] forcing every starter to potential {force} on sight")
    if continuous:
        print(f"[{stamp()}] watching - values appear when the starters are rolled "
              f"and update on every reload; Ctrl+C to stop")
    try:
        while True:
            r = b.eval(find_code(force), timeout=30.0)
            out = r.get("out") or ""
            if r.get("err") and not out:
                print("lua error:", r["err"])
                return 1
            if "!ERROR!" in out:
                print("[lua]", out.strip())
                return 1
            if "!NOMAP!" in out or "!NONE!" in out:
                if last is not None or not waiting:
                    print(f"[{stamp()}] waiting for the starter reveal ...")
                    waiting = True
                last = None            # report the next roll even if it repeats
                show_overlay({})
                if not continuous:
                    print("Could not find the starter monsters.")
                    print("(are you at the starter reveal in the coromon lab?)")
                    print("lua said:", out.strip())
                    return 1
            else:
                waiting = False
                data = parse(out)
                sig = tuple(sorted((k, v[0]) for k, v in data.items()))
                if sig and sig != last:
                    rolls += 1
                    last = sig
                    names = [n for n in ORDER if n in data] or sorted(data)
                    print()
                    print(f"[{stamp()}] === starter roll #{rolls} ===")
                    for n in names:
                        pot, extra, path, rolled = data[n]
                        print(fmt(n, pot, rolled))
                        if args.verbose:
                            if extra:
                                print(f"      {extra}")
                            print(f"      {path}")
                    best = max(v[0] for v in data.values())
                    if best >= 21:
                        print("  -> a PERFECT (21) is on the table!")
                    elif best >= 20:
                        print("  -> a 20 is on the table!")
                    for name, vals in parse_warnings(out):
                        print(f"  !! {name}: leftover monsters from an earlier reload "
                              f"disagree ({vals}) - using the freshest")
                    sys.stdout.flush()
                show_overlay(data)
            if not continuous:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0
    finally:
        if drew_overlay:
            print("overlay left on screen - remove it with: "
                  "python coromon_starter.py --clear-overlay")
        b.detach()


if __name__ == "__main__":
    sys.exit(main())
