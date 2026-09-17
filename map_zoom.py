#!/usr/bin/env python3
"""
map_zoom.py - zoom the overworld in/out with a key press, during play.

Coromon has no built-in zoom. I checked: `debugSettings` has no scale/zoom key at all,
`classes.modules.interface.scrollViewBuilder` (the camera) takes no scale parameter, and
the only zoom knob anywhere in the game is `debugHexagonWorldScaleMultiplier` /
`refreshHexagonWorldScale`, which live in
`classes/debug/simulation/debug_load_rogue_hexagonMap_configureBiome.lua` - debug tooling
for the Rogue Planet map generator, not reachable in a normal save.

So the zoom is done by hand. The overworld is one node, `tiledWorld`, reached through

    package.loaded['classes.modules.mte.mte']  ->  upvalue 'tiledWorld' of getTiledWorld

and the player's sprite sits four groups deep *inside* it, so scaling the node scales the
map together with the player. The transform is a zoom about the middle of the viewport:

    tiledWorld.xScale, tiledWorld.yScale = k, k
    tiledWorld.x = k * engineX + (1 - k) * viewportCentreX
    tiledWorld.y = k * engineY + (1 - k) * viewportCentreY

where engineX/Y is the value the engine itself would have written.

The zoom stops are whole screen pixels per texture pixel, read from the game's own
`display.contentToScreenScale`. At a 4x window the stops are 4 -> 3 -> 2 -> 1, i.e.
k = 1, 0.75, 0.5, 0.25, and nothing in between. That is deliberate: the game draws with
nearest-neighbour filtering (`main.lua` sets both `magTextureFilter` and `minTextureFilter`
to 'nearest'), so a fractional scale gives some texels 3 screen pixels and others 2 -
visible as uneven pixel sizes and a faint shimmer as the map scrolls. Zooming in past the
game's own scale is not offered, and neither is going below one screen pixel per texel.

Three things this gets right that the earlier attempts did not:

  * engineX is TRACKED, never written. The engine's camera is incremental - measured live,
    `tiledWorld.x + sprite.x` stays pinned at a constant while walking, so the engine
    carries its own value and adds the sprite's movement each frame. Our listener runs
    after the engine's, so whatever the node gained since we last wrote it is exactly the
    engine's movement; summing those deltas reconstructs the engine's value without
    disturbing it. The first version wrote the node outright, which destroyed that value
    permanently - it left the world 446px out, and the offset survived walking, map
    changes and a reset. `--recenter` exists to repair that.
  * the offset does NOT depend on where the player is, and that is the point. Deriving it
    from the sprite's position pinches them to one spot on screen: fine on a large map,
    where the engine parks them at the centre, but wrong in a small area, where the engine
    leaves the camera alone and walks the player across the screen. There it dragged the
    world along with them at (1 - k) per pixel of movement - the faster you walked and the
    further you zoomed out, the faster the map slid underneath. (An earlier attempt that
    used `sprite.x` as a stand-in for the sprite's coordinate inside the node was
    additionally off by (1 - k) * anchorOffset, a small diagonal shift that reversed with
    the zoom direction and vanished at k = 1.)
  * the tracking is RE-SEEDED whenever the world node is swapped - which is what a location
    change does - and the new world is then left alone for 45 frames. Reconstructing the
    engine's value from deltas only means anything while the node is the same object, and a
    new map is not positioned in a single frame either; both of those broke the camera on
    location transitions while zoomed, in a way only resetting the zoom and transitioning
    again could clear. During the settle window the view shows at scale 1, then the zoom
    re-applies itself.

Because the engine's value is carried through rather than replaced, its edge clamp keeps
working: near a map border the player sits off-centre as usual, and zooming scales that
around the player rather than fighting it.

`--recenter` is the escape hatch for a world an older build already displaced: it derives
the sprite's anchor offset and places the node so the player renders at the middle of the
viewport.

Keys are received inside the game via `Runtime:addEventListener('key', ...)`, so no OS
level hotkey polling is needed. The listener only reads - it never consumes an event, so
the game's own controls keep working.

Usage:
    python map_zoom.py                  # install at the game's own scale, then use the keys
    python map_zoom.py --pixels 2       # install already out at 2 screen px per texel
    python map_zoom.py --status         # scale, px-per-texel and every key the game has seen
    python map_zoom.py --off            # remove the listener and restore the normal view

Requires frida (`pip install frida`) and the game running in the overworld.
"""

import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from coromon_lua import MINIMAL_HOOKS, Bridge

# Default keys. Solar2D reports punctuation as the literal character, but the exact
# name for some of them is worth confirming - `--status` prints everything the game has
# seen, so if a key does not react, read it off there and pass --key-out/--key-in.
KEY_OUT = ["-", "minus", "["]
# Solar2D reports the character the key produces, not the physical key, so the plus key
# arrives as '+' (with rightShift alongside). Read back with --status: the keys the game
# had seen were +(66) -(46) 0(8) rightShift(14) leftCommand(3).
KEY_IN = ["+", "=", "plus", "]"]
KEY_RESET = ["0"]


PRELUDE = r"""
local function uv(fn, name)
  if type(fn) ~= 'function' then return nil end
  for i = 1, 100 do
    local n, v = debug.getupvalue(fn, i)
    if not n then return nil end
    if n == name then return v end
  end
end

local function twNode()
  local m = package.loaded['classes.modules.mte.mte']
  if type(m) ~= 'table' then return nil end
  local w = uv(m.getTiledWorld, 'tiledWorld')
  if type(w) ~= 'table' or type(w.x) ~= 'number' then return nil end
  return w
end

local function playerSprite()
  local i
  local ok = pcall(function() i = spawnableHelper:getPlayerSpawnable() end)
  local s = (ok and type(i) == 'table') and i.sprite or nil
  if type(s) == 'table' and type(s.x) == 'number' then return s end
  return nil
end

-- Screen pixels per texture pixel at the game's normal view. It is a plain number on the
-- game's display library (4 at a 1920-wide window, 0.25 the other way round) and tracks
-- the window size, so reading it keeps the zoom stops honest if the window changes.
local function nativeScale()
  local s = display.contentToScreenScale
  if type(s) ~= 'number' or s < 1 then s = 1 end
  return s
end

-- The zoom stops are whole numbers of screen pixels per texel: nativeScale, then one less,
-- down to 1. `dir` of -1 zooms out a stop, +1 zooms in. k = pixels / nativeScale.
--
-- Nothing in between, because the game draws with nearest-neighbour filtering, so a
-- fractional scale gives some texels 3 screen pixels and others 2 - uneven pixel sizes and
-- a faint shimmer while the map scrolls.
local function stepScale(k, dir)
  local s = nativeScale()
  local i = math.floor(k * s + 0.5) + dir
  if i < 1 then i = 1 end
  if i > s then i = s end
  return i / s
end
"""


INSTALL = (
    PRELUDE
    + r"""
local st = _G.__mapzoom or {}
_G.__mapzoom = st

st.on = true
st.kout, st.kin, st.kreset = __KOUT__, __KIN__, __KRESET__
st.seen = st.seen or {}

-- How long to leave a newly built world alone after a location change. The new map is not
-- positioned in a single frame, and the game's own writes while doing it are absolute
-- values rather than movements.
local SETTLE_FRAMES = 45

local function apply()
  local z = _G.__mapzoom
  if not z or not z.on or z.body ~= apply then return end

  local w = twNode()
  if not w then
    -- Out of the overworld (battle, menu, teardown). Drop everything - the node that comes
    -- back may well be a different object.
    z.node, z.spr = nil, nil
    z.wx, z.wy, z.w0x, z.w0y, z.settle = nil, nil, nil, nil, nil
    return
  end

  local k = z.scale

  -- A location change hands us a brand new node, and it is not built in one frame: the game
  -- goes on positioning it while the new map is set up. Re-seeding on the swap alone is not
  -- enough, because those later writes are absolute values rather than movements, and
  -- absorbing them into the delta total below would leave it wrong by exactly our own last
  -- correction. So hold off - leave the node at scale 1 and just watch until it settles.
  if w ~= z.node or z.wx == nil then
    z.settle = SETTLE_FRAMES
  end
  z.node = w

  if z.settle and z.settle > 0 then
    z.settle = z.settle - 1
    w.xScale, w.yScale = 1, 1
    z.w0x, z.w0y = w.x, w.y
    z.wx, z.wy = w.x, w.y
    return
  end

  -- The engine's camera is incremental: measured live, `tiledWorld.x + sprite.x` stays
  -- pinned at a constant while walking, so the engine carries its own value and adds the
  -- sprite's movement each frame. Our listener runs after the engine's, so whatever the
  -- node gained since we last wrote it IS the engine's movement - totalling that
  -- reconstructs the engine's own camera value without ever disturbing it.
  z.w0x = (z.w0x or w.x) + (w.x - z.wx)
  z.w0y = (z.w0y or w.y) + (w.y - z.wy)

  if k == 1 then
    -- Put the node back to the engine's own value before letting go - it still carries the
    -- offset we wrote for the old scale - then resync the tracking.
    w.xScale, w.yScale = 1, 1
    w.x, w.y = z.w0x, z.w0y
    z.wx, z.wy = w.x, w.y
    return
  end

  -- Zoom about the middle of the viewport:
  --     W = k * engineX + (1 - k) * centreX
  --
  -- Deliberately independent of where the player is, and that is the whole point. Deriving
  -- the offset from the player's position pins them to one spot on screen: correct on a
  -- large map, where the engine parks them at the centre - but wrong in a small area, where
  -- the engine leaves the camera alone and walks the player across the screen. There the
  -- old version dragged the world along with them at (1 - k) per pixel of movement, so the
  -- faster you walked and the further you zoomed out, the faster the map slid underneath.
  --
  -- It also means no sprite lookup and no anchor arithmetic per frame: nothing here has to
  -- know where the player is.
  local cx = display.contentWidth / 2
  local cy = display.contentHeight / 2
  w.xScale, w.yScale = k, k
  w.x = k * z.w0x + (1 - k) * cx
  w.y = k * z.w0y + (1 - k) * cy
  z.wx, z.wy = w.x, w.y
end

local function setScale(k)
  local z = _G.__mapzoom
  if not z or not z.on then return end
  -- Clamp to the useful range only: never past the game's own scale (no zooming in), and
  -- never below one screen pixel per texel.
  local s = nativeScale()
  if k > 1 then k = 1 end
  if k < 1 / s then k = 1 / s end
  z.scale = k
  apply()
end

local function onKey(e)
  local z = _G.__mapzoom
  if not z or not z.on or type(e) ~= 'table' then return end
  local k = tostring(e.keyName)
  z.seen[k] = (z.seen[k] or 0) + 1
  z.lastKey = k .. '/' .. tostring(e.phase)
  if e.phase ~= 'down' then return end
  if z.kout[k] then setScale(stepScale(z.scale, -1))
  elseif z.kin[k] then setScale(stepScale(z.scale, 1))
  elseif z.kreset[k] then setScale(1) end
end

if st.efListener then pcall(function() Runtime:removeEventListener('enterFrame', st.efListener) end) end
if st.keyListener then pcall(function() Runtime:removeEventListener('key', st.keyListener) end) end
st.efListener, st.body = apply, apply
st.keyListener, st.keyBody = onKey, onKey
Runtime:addEventListener('enterFrame', apply)
Runtime:addEventListener('key', onKey)

local s0 = nativeScale()
if __PIXELS__ and __PIXELS__ > 0 then
  setScale(__PIXELS__ / s0)
else
  setScale(1)
end

local w = twNode()
if not w then
  return 'installed, but no tiledWorld right now - the keys will start working once you are in the overworld'
end
local stops = {}
for i = s0, 1, -1 do stops[#stops + 1] = tostring(i) end
return string.format(
  'map zoom armed: %s screen px per texel at the game scale, stops %s - %s out, %s in, %s reset',
  tostring(s0), table.concat(stops, '/'), __KOUTTXT__, __KINTXT__, __KRESETTXT__)
"""
)


STATUS = (
    PRELUDE
    + r"""
local z = _G.__mapzoom
if not z then return 'map zoom is not installed' end
local out = {}
out[#out + 1] = string.format('on=%s scale=%s lastKey=%s',
  tostring(z.on), tostring(z.scale), tostring(z.lastKey))
local w = twNode()
if w then
  local s = nativeScale()
  out[#out + 1] = string.format(
    'worldScale=%.4f  =  %.2f screen px per texel (game scale %s)  engineBase=(%.2f, %.2f)',
    w.xScale or 1, (z.scale or 1) * s, tostring(s), z.w0x or 0, z.w0y or 0)
  local p = playerSprite()
  if p then
    local ok, lx, ly = pcall(function() return p:localToContent(0, 0) end)
    out[#out + 1] = string.format(
      'sprite=(%.2f, %.2f)  on screen=(%s, %s)  viewport centre=(%.1f, %.1f)',
      p.x, p.y, tostring(lx), tostring(ly),
      display.contentWidth / 2, display.contentHeight / 2)
  end
else
  out[#out + 1] = 'tiledWorld: not in the overworld'
end
local keys = {}
for k, n in pairs(z.seen or {}) do keys[#keys + 1] = k .. '(' .. n .. ')' end
table.sort(keys)
out[#out + 1] = 'keys seen by the game: ' .. (#keys > 0 and table.concat(keys, ' ') or '(none yet)')
return table.concat(out, '\n')
"""
)


# The camera is incremental, so once tiledWorld.x/y has been written absolutely the error
# is baked into the engine's base and survives walking, map changes and resets. This puts
# it back: work out the sprite's anchor offset, then place the node so the player renders
# at the middle of the viewport. Deriving it from the viewport means there is no magic
# number to rot when the window or content size changes.
RECENTER = (
    PRELUDE
    + r"""
local w, p = twNode(), playerSprite()
if not w or not p then return '!need to be in the overworld for this!' end
local lx, ly = p:localToContent(0, 0)
local ax, ay = lx - (w.x + p.x), ly - (w.y + p.y)
local tx = display.contentWidth / 2 - ax
local ty = display.contentHeight / 2 - ay
w.xScale, w.yScale = 1, 1
w.x = tx - p.x
w.y = ty - p.y
local z = _G.__mapzoom
if z then
  z.cx, z.cy = nil, nil
  z.wx, z.wy = nil, nil
  z.scale = 1
end
return string.format(
  'recentred: player was at content (%.1f, %.1f), now (%.1f, %.1f) in a %sx%s viewport',
  lx, ly, display.contentWidth / 2, display.contentHeight / 2,
  tostring(display.contentWidth), tostring(display.contentHeight))
"""
)


OFF = r"""
local z = _G.__mapzoom
if not z then return 'map zoom is not installed' end
z.on = false
if z.efListener then pcall(function() Runtime:removeEventListener('enterFrame', z.efListener) end) end
if z.keyListener then pcall(function() Runtime:removeEventListener('key', z.keyListener) end) end
local m = package.loaded['classes.modules.mte.mte']
if type(m) == 'table' then
  for i = 1, 100 do
    local n, v = debug.getupvalue(m.getTiledWorld, i)
    if not n then break end
    if n == 'tiledWorld' and type(v) == 'table' then
      v.xScale, v.yScale = 1, 1
    end
  end
end
_G.__mapzoom = nil
return 'map zoom removed, world scale restored to 1.0'
"""


def _lua_keys(names):
    """A Lua set keyed by keyName - the handler looks up by name, not by position."""
    return "{" + ", ".join(f"['{n}'] = true" for n in names) + "}"


def _lua_txt(names):
    return "'" + "/".join(names) + "'"


def _bridge(process):
    b = Bridge(process, hooks=MINIMAL_HOOKS)
    for _ in range(150):
        if b.status().get("state"):
            break
        time.sleep(0.2)
    return b


def _eval(b, code, timeout=30.0):
    r = b.eval(code, timeout=timeout)
    return r.get("out") or r.get("err")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--pixels",
        type=int,
        default=None,
        help="install already zoomed out, to N screen pixels per texture pixel "
        "(default: the game's own scale, i.e. no zoom)",
    )
    ap.add_argument(
        "--key-out", default=None, help="comma separated keys that zoom out a stop"
    )
    ap.add_argument(
        "--key-in", default=None, help="comma separated keys that zoom in a stop"
    )
    ap.add_argument(
        "--key-reset", default=None, help="comma separated keys that reset the zoom"
    )
    ap.add_argument("--status", action="store_true", help="show scale and keys seen")
    ap.add_argument(
        "--recenter",
        action="store_true",
        help="put the camera back on the player after an older build displaced it",
    )
    ap.add_argument("--off", action="store_true", help="remove the zoom and listener")
    ap.add_argument("--process", default="coromon.exe")
    args = ap.parse_args()

    b = _bridge(args.process)

    try:
        if args.off:
            print(_eval(b, OFF))
        elif args.status:
            print(_eval(b, STATUS))
        elif args.recenter:
            print(_eval(b, RECENTER))
        else:
            ko = args.key_out.split(",") if args.key_out else KEY_OUT
            ki = args.key_in.split(",") if args.key_in else KEY_IN
            kr = args.key_reset.split(",") if args.key_reset else KEY_RESET
            code = (
                INSTALL.replace("__PIXELS__", str(args.pixels if args.pixels else -1))
                .replace("__KOUT__", _lua_keys(ko))
                .replace("__KIN__", _lua_keys(ki))
                .replace("__KRESET__", _lua_keys(kr))
                .replace("__KOUTTXT__", _lua_txt(ko))
                .replace("__KINTXT__", _lua_txt(ki))
                .replace("__KRESETTXT__", _lua_txt(kr))
            )
            print(_eval(b, code))
    finally:
        b.detach()


if __name__ == "__main__":
    main()
