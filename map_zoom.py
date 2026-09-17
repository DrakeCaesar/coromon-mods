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
    change does - and the zoom is held off only until the game has finished writing the new
    node's position. Reconstructing the engine's value from deltas only means anything while
    the node is the same object, and a new map is not positioned in a single frame either;
    both of those broke the camera on location transitions while zoomed, in a way only
    resetting the zoom and transitioning again could clear. The hold-off is measured, not
    timed: once the node's position has stood still for four frames the zoom goes straight
    back on, so a normal transition costs about 80ms of scale 1 rather than a fixed 750ms.

One more thing the game does that the zoom has to respect. Some maps mark a whole Tiled layer
`stickToScreen` (`worldRainOverlay`, in every map checked - it is the dark sheet drawn over the
map), and the builder pins such a layer to the screen by translating it by the exact opposite of
the world's translation on every camera move (`tiledWorldBuilder.moveCamera`, lines 363-365). The
pinning itself survives the zoom untouched, because the zoom only ever reads the engine's value,
so the sum the pinning depends on stays intact. What does not survive is the layer's SIZE: it is
laid out to fill the viewport at scale 1, so at a zoom of k it draws k times too small and covers
only the middle of the screen - the dark rectangle that stops short of the edges. Measured at
k = 0.5, a 512x297 overlay covering the whole 480x265 screen shrank to 256x148.5 dead centre,
which is the old viewport exactly. So the zoom counter-scales every screen-locked layer by 1/k,
and because their content is not centred on their own anchor (this one's image sits 480 units to
the right of it) it measures the shift that scaling introduces and puts it back, rather than
assuming the offcentre. They cover the screen at every stop and land back on their exact original
layout at k = 1. `--status` lists them.

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

-- Coromon marks whole Tiled layers `stickToScreen` (in the maps checked there is exactly one,
-- `worldRainOverlay`). The builder collects them and, every time the camera moves, translates
-- each one by the exact opposite of the world's translation - `tiledWorldBuilder.moveCamera`,
-- lines 363-365: the world goes one way and the layer the other, so the layer stands still on
-- screen while the map slides underneath it.
--
-- Zooming does not break that pinning. The zoom only ever reads the engine's own value, so the
-- world + layer sum the pinning depends on survives untouched, and the layer stays put. What
-- it does break is the layer's SIZE: it is laid out to fill the viewport at scale 1, so under a
-- zoom of k it draws k times too small and covers only the middle of the screen - that is the
-- dark rectangle that stops short of the edges once you zoom out. Measured at k = 0.5: a
-- 512x297 overlay that covers the whole 480x265 screen shrank to 256x148.5 dead centre, which
-- is the old viewport exactly.
--
-- So give it its size back: scale it by 1/k. That fixes the size but not the position - the
-- content is not centred on the layer's own anchor (this overlay's image sits 480 units to the
-- right of it), so scaling slides it sideways. Measured: the centre jumps 240 -> 480. Rather
-- than hard-code the offcentre, measure it: scale, see how far the content's centre moved, and
-- put it back. The correction is a plain offset, so the previous one is undone before the next
-- is derived - nothing is ever computed from a position we wrote ourselves, and at k = 1 the
-- layer lands back exactly where it started.
local function stickyLayerNodes(w)
  local out = {}
  local m = package.loaded['classes.modules.mte.mte']
  if type(m) ~= 'table' or type(m.getLayers) ~= 'function' then return out end
  local ok, layers = pcall(function() return m:getLayers() end)
  if not ok or type(layers) ~= 'table' then return out end
  -- The builder adds one group per layer, in order, so layer n is child n. Disabled layers
  -- still get their (empty) group, which is why the mapping stays one-to-one.
  if (w.numChildren or 0) < #layers then return out end
  for i = 1, #layers do
    local props = layers[i] and layers[i].properties
    if type(props) == 'table' and props.stickToScreen then
      local node = w[i]
      if type(node) == 'table' and type(node.x) == 'number' then out[#out + 1] = node end
    end
  end
  return out
end

-- Centre of everything the node actually draws, in content coordinates. Going through the
-- leaves means it does not matter where the node's anchor sits or how it is built inside.
local function contentCentre(node)
  local x1, y1, x2, y2 = math.huge, math.huge, -math.huge, -math.huge
  local function walk(n, depth)
    if depth > 4 or type(n) ~= 'table' then return end
    if n.path ~= nil and type(n.width) == 'number' and type(n.height) == 'number' then
      local ok1, ax, ay = pcall(function() return n:localToContent(-n.width / 2, -n.height / 2) end)
      local ok2, bx, by = pcall(function() return n:localToContent(n.width / 2, n.height / 2) end)
      if ok1 and ok2 then
        x1, y1 = math.min(x1, ax, bx), math.min(y1, ay, by)
        x2, y2 = math.max(x2, ax, bx), math.max(y2, ay, by)
      end
    end
    for i = 1, (n.numChildren or 0) do walk(n[i], depth + 1) end
  end
  walk(node, 0)
  if x1 == math.huge then return nil end
  return (x1 + x2) / 2, (y1 + y2) / 2
end

local function fixStickyLayers(w, k, store)
  local nodes = stickyLayerNodes(w)
  for i = 1, #nodes do
    local node = nodes[i]
    local rec = store[node]
    if not rec then rec = {corrx = 0, corry = 0}; store[node] = rec end
    -- Undo the last correction before looking at anything, so both measurements below are of
    -- the layer's own layout and never of a position we wrote.
    node.x, node.y = node.x - rec.corrx, node.y - rec.corry
    node.xScale, node.yScale = 1, 1
    rec.corrx, rec.corry = 0, 0
    if k ~= 1 then
      local bx, by = contentCentre(node)
      if bx then
        node.xScale, node.yScale = 1 / k, 1 / k
        local ax, ay = contentCentre(node)
        if ax then
          -- Moving the node by d moves its content by k * d, so divide the error by k.
          rec.corrx, rec.corry = (bx - ax) / k, (by - ay) / k
          node.x, node.y = node.x + rec.corrx, node.y + rec.corry
        end
      end
    end
  end
  return #nodes
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
-- Weak-keyed: a location change builds new layer groups, and the old ones are then free to go.
st.sticky = st.sticky or setmetatable({}, {__mode = 'k'})

-- Re-fit the screen-locked layers, but only when something they depend on has changed - the
-- zoom, the world node (a location change swaps it) or the viewport size. Doing it every
-- frame would be wasted work; not doing it at all would let the game re-lay the layers out
-- from under us.
local function syncSticky(w, k)
  local z = _G.__mapzoom
  local store = z.sticky
  if not store then
    store = setmetatable({}, {__mode = 'k'})
    z.sticky = store
  end
  local cw, ch = display.contentWidth, display.contentHeight
  if z.fixNode == w and z.fixK == k and z.fixW == cw and z.fixH == ch then return end
  z.fixNode, z.fixK, z.fixW, z.fixH = w, k, cw, ch
  fixStickyLayers(w, k, store)
end

-- How long to leave a newly built world alone after a location change. The new map is not
-- positioned in a single frame, and the game's own writes while doing it are absolute values
-- rather than movements, so they cannot be folded into the delta total below.
--
-- Rather than waiting a fixed number of frames, wait for the writes to actually stop: the
-- node's position standing still for a few frames means the map has finished being placed.
-- The old fixed 45-frame window (three quarters of a second at 60fps) left the map on screen
-- at scale 1 for that whole time, which is very visible when you are playing zoomed out.
-- The maximum is only a safety net for a map that somehow keeps moving.
local SETTLE_STABLE_FRAMES = 4
local SETTLE_MAX_FRAMES = 90

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
    z.settle, z.stable = SETTLE_MAX_FRAMES, 0
  end
  z.node = w

  if z.settle and z.settle > 0 then
    z.settle = z.settle - 1
    -- We are not writing the node while settling, so any change in it is the game's own.
    if w.x == z.wx and w.y == z.wy then
      z.stable = (z.stable or 0) + 1
    else
      z.stable = 0
    end
    z.w0x, z.w0y = w.x, w.y
    z.wx, z.wy = w.x, w.y
    if (z.stable or 0) < SETTLE_STABLE_FRAMES then
      w.xScale, w.yScale = 1, 1
      syncSticky(w, 1)
      return
    end
    -- Settled - fall through and put the zoom back on this very frame.
    z.settle = 0
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
    syncSticky(w, 1)
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

  -- After the world scale, not before: the fit is measured through it.
  syncSticky(w, k)
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
  local sticky = stickyLayerNodes(w)
  if #sticky > 0 then
    local parts = {}
    for i = 1, #sticky do
      parts[#parts + 1] = string.format('%dx%d at scale %.3f',
        sticky[i].width or 0, sticky[i].height or 0, sticky[i].xScale or 1)
    end
    out[#out + 1] = 'screen-locked layers (fit by the zoom): ' .. table.concat(parts, ', ')
  end
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
-- Hand the screen-locked layers their size and position back, or they stay scaled up by the
-- last zoom we applied.
if z.sticky then
  for node, rec in pairs(z.sticky) do
    if type(node) == 'table' then
      node.x, node.y = node.x - (rec.corrx or 0), node.y - (rec.corry or 0)
      node.xScale, node.yScale = 1, 1
    end
  end
  z.sticky = nil
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
