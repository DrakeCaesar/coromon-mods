#!/usr/bin/env python3
"""
gold_log.py - print the gold counter's coordinates whenever they change.

The counter in the top bar is positioned by a right-to-left aware magnet, so its x is derived
from the bar's right edge and everything to the right of it. Something in that chain goes
stale when the pause menu opens and is refreshed by an input switch, and the only way to see
it is to watch the numbers move.

This installs a watcher in the running game that records the counter (and its coin) whenever
anything about their placement changes, and prints each new record here. Nothing is written
to the game: the watcher only reads.

A label's x is relative to its parent, so the log carries BOTH the label's own x and its
position in content space. An ancestor moving shows up in the content position and not in the
relative one - which is what a first run of this established, when the counter visibly moved
while every relative number stayed put.

    python gold_log.py                  keep printing until Ctrl+C
    python gold_log.py --seconds 30     stop on its own after 30 seconds
    python gold_log.py --interval 0.1   poll the game faster (default 0.3 s)

The watcher lives in the game until the game is closed or the feature code is reinstalled;
run with --remove to take it out yourself.
"""

import argparse
import sys
import time

try:
    from scroll_fix import _bridge, _eval
except ImportError:  # pragma: no cover - the module lives beside this file
    sys.exit("run this from the coromon-tools directory (it needs scroll_fix.py)")

WATCHER = r'''
local function nodeOf(module)
  if type(module) ~= 'table' then return end
  local ok, created = pcall(module.isCreated, module)
  if not ok or not created then return end
  local ok2, inst = pcall(module.getInstance, module)
  if not ok2 or type(inst) ~= 'table' then return end
  local anim = inst.__hudGoldAnimOrig or inst.doCurrencyAnimation
  if type(anim) ~= 'function' then return end
  local ok3, _, node = pcall(debug.getupvalue, anim, 1)
  if not ok3 or type(node) ~= 'table' then return end
  return inst, node
end

local function coinOf(node)
  local parent = node.parent
  if type(parent) ~= 'table' then return end
  local best
  for i = 1, (parent.numChildren or 0) do
    local c = parent[i]
    if type(c) == 'table' and c ~= node and type(c.x) == 'number'
       and type(c.width) == 'number' and type(node.width) == 'number'
       and c.width < node.width then
      if not best or c.width < best.width then best = c end
    end
  end
  return best
end

local function describe()
  local _, node = nodeOf(_G.innerTopBarGold)
  if not node then return 'no label on screen' end
  local coin = coinOf(node)
  local parent = node.parent
  -- A label's x is relative to its PARENT, so an ancestor that moves by a couple of pixels is
  -- completely invisible in it - which is exactly what a run of this showed: the counter moved
  -- on screen while every number on the old line stayed put. The content-space origin is what
  -- the eye actually sees, and the parent's own x says which level of the tree moved.
  local okc, cx = pcall(function() return node:localToContent(0, 0) end)
  return string.format(
    'screen x=%-9s | label x=%-8.3f base=%-8s set=%-8s delta=%-4s w=%-4s | parent x=%-9s | coin x=%-8s | "%s"',
    okc and string.format('%.3f', cx) or 'n/a',
    node.x,
    node.__hudGoldX and string.format('%.2f', node.__hudGoldX) or '-',
    node.__hudGoldSet and string.format('%.2f', node.__hudGoldSet) or '-',
    tostring(node.__hudGoldDelta), tostring(node.width),
    (type(parent) == 'table' and type(parent.x) == 'number')
      and string.format('%.3f', parent.x) or '-',
    coin and string.format('%.3f', coin.x) or 'none',
    tostring(node.text))
end

if _G.__goldLoggerTick then
  Runtime:removeEventListener('enterFrame', _G.__goldLoggerTick)
  _G.__goldLoggerTick = nil
end
_G.__goldLogger = { n = 0, log = {} }
local name, last = 0, nil
local function record()
  local s = describe()
  if s ~= last then
    last = s
    local w = _G.__goldLogger
    w.n = w.n + 1
    w.log[#w.log + 1] = { n = w.n, t = system.getTimer() or 0, s = s }
    if #w.log > 500 then table.remove(w.log, 1) end
  end
end
-- Wrapped: this is a debug tool, and a mistake in it must not throw inside the game's frame
-- loop, where it would take the whole runtime down with it.
local function tick()
  local ok, err = pcall(record)
  if not ok then
    local w = _G.__goldLogger
    w.error = tostring(err)
  end
end
_G.__goldLoggerTick = tick
Runtime:addEventListener('enterFrame', tick)
tick()
return 'watcher installed'
'''

DRAIN = r'''
local w = _G.__goldLogger
if not w then return 'NO LOGGER' end
local out = {}
for i = 1, #w.log do
  if w.log[i].n > __AFTER__ then
    out[#out + 1] = string.format('%10.3f  %s', w.log[i].t / 1000, w.log[i].s)
  end
end
return string.format('%d\n%s', w.n, table.concat(out, '\n'))
'''

REMOVE = r'''
if _G.__goldLoggerTick then
  Runtime:removeEventListener('enterFrame', _G.__goldLoggerTick)
  _G.__goldLoggerTick = nil
end
_G.__goldLogger = nil
return 'watcher removed'
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after this long (0 = until Ctrl+C)")
    ap.add_argument("--interval", type=float, default=0.3,
                    help="how often to read the game, in seconds (default 0.3)")
    ap.add_argument("--remove", action="store_true",
                    help="take the watcher out of the game and exit")
    args = ap.parse_args()

    bridge = _bridge()

    if args.remove:
        print(_eval(bridge, REMOVE, timeout=60.0))
        return

    print("installing the watcher ...")
    print(_eval(bridge, WATCHER, timeout=90.0))
    print()
    print("t=game seconds   counter and coin placement")
    print("-" * 118)

    seen = 0
    started = time.time()
    try:
        while True:
            reply = _eval(bridge, DRAIN.replace("__AFTER__", str(seen)), timeout=60.0)
            if reply is None:                       # the bridge hiccups now and then
                time.sleep(args.interval)
                continue
            if reply.startswith("NO LOGGER"):
                print("the watcher is gone - the game was restarted? run this again")
                return
            head, _, body = reply.partition("\n")
            try:
                seen = int(head)
            except ValueError:
                time.sleep(args.interval)
                continue
            if body:
                print(body)
                sys.stdout.flush()
            if args.seconds and (time.time() - started) >= args.seconds:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        print("-" * 118)
        print("the watcher stays in the game (use --remove to take it out)")


if __name__ == "__main__":
    main()
