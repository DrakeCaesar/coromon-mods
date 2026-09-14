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

local found, order = {}, {}
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
        if not found[name] or found[name].score < score then
          if not found[name] then order[#order + 1] = name end
          found[name] = {potential = pot, score = score, path = e.p, extra = extra}
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
if #order == 0 then return '!NONE! nodes=' .. nodes end
out[#out + 1] = 'RESULT'
for _, name in ipairs(order) do
  local f = found[name]
  out[#out + 1] = string.format('%s\t%d\t%s\t%s', name, f.potential, f.extra, f.path)
end
return table.concat(out, '\n')
"""

ORDER = ["FIRE_TURTLE_1", "WATER_SHARK_1", "ICE_BEAR_1"]


def parse(out):
    """Parse the Lua result into {name: (potential, extra, path)}."""
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
        res[name] = (pot, extra, path)
    return res


def fmt(name, pot, extra=""):
    tag = ""
    if pot >= 21:
        tag = "  <-- PERFECT"
    elif pot >= 20:
        tag = "  <-- potent"
    return f"  {name:<16} potential {pot:>2}{tag}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="coromon.exe")
    ap.add_argument("--watch", action="store_true", help="poll and report changes")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

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

    last = None
    first = True
    try:
        while True:
            r = b.eval(FIND_STARTERS, timeout=30.0)
            out = r.get("out") or ""
            if r.get("err") and not out:
                print("lua error:", r["err"])
                return 1
            if "!NOMAP!" in out or "!NONE!" in out:
                if not args.watch:
                    print("Could not find the starter monsters.")
                    print("(are you at the starter reveal in the coromon lab?)")
                    print("lua said:", out.strip())
                    return 1
                if args.watch:
                    print(".", end="", flush=True)
            else:
                data = parse(out)
                sig = tuple(sorted((k, v[0]) for k, v in data.items()))
                if sig and (sig != last or first):
                    last = sig
                    names = [n for n in ORDER if n in data] or sorted(data)
                    print()
                    print("=== starter roll ===" if first else "=== new roll ===")
                    for n in names:
                        pot, extra, path = data[n]
                        print(fmt(n, pot))
                        if args.verbose:
                            if extra:
                                print(f"      {extra}")
                            print(f"      {path}")
                    best = max(v[0] for v in data.values()) if data else 0
                    if best >= 21:
                        print("  -> a PERFECT (21) is on the table!")
                    elif best >= 20:
                        print("  -> a 20 is on the table!")
                    sys.stdout.flush()
                elif first and args.watch:
                    print(".", end="", flush=True)
            first = False
            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0
    finally:
        b.detach()


if __name__ == "__main__":
    sys.exit(main())
