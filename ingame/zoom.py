#!/usr/bin/env python3
"""
zoom.py - overworld zoom on in-game keys.

Coromon has no built-in zoom. Checked: `debugSettings` has no scale/zoom key at all,
`classes.modules.interface.scrollViewBuilder` (the camera) takes no scale parameter, and
the only zoom knob anywhere in the game is `debugHexagonWorldScaleMultiplier` /
`refreshHexagonWorldScale`, which live in
`classes/debug/simulation/debug_load_rogue_hexagonMap_configureBiome.lua` - debug tooling
for the Rogue Planet map generator, not reachable in a normal save.

So the zoom is done by hand, as a scale on the overworld node about the middle of the
viewport. The player's sprite sits four groups deep *inside* `tiledWorld`, so scaling the
node scales the map together with the player.

The zoom stops are whole screen pixels per texture pixel, read from the game's own
`display.contentToScreenScale`. At a 4x window the stops are 4 -> 3 -> 2 -> 1, i.e.
k = 1, 0.75, 0.5, 0.25, and nothing in between. That is deliberate: the game draws with
nearest-neighbour filtering, so a fractional scale gives some texels 3 screen pixels and
others 2 - visible as uneven pixel sizes and a faint shimmer as the map scrolls. Zooming in
past the game's own scale is not offered, and neither is going below one screen pixel per
texel.

Three things this gets right that earlier attempts did not:

  * engineX is TRACKED, never written. The engine's camera is incremental - measured live,
    `tiledWorld.x + sprite.x` stays pinned at a constant while walking, so the engine
    carries its own value and adds the sprite's movement each frame. Our listener runs after
    the engine's, so whatever the node gained since we last wrote it is exactly the engine's
    movement; summing those deltas reconstructs the engine's value without disturbing it.
    The first version wrote the node outright, which destroyed that value permanently - it
    left the world 446px out, and the offset survived walking, map changes and a reset.
    `--recenter` exists to repair that.
  * the offset does NOT depend on where the player is, and that is the point. Deriving it
    from the sprite's position pinches them to one spot on screen: fine on a large map,
    where the engine parks them at the centre, but wrong in a small area, where the engine
    leaves the camera alone and walks the player across the screen. There it dragged the
    world along with them at (1 - k) per pixel of movement - the faster you walked and the
    further you zoomed out, the faster the map slid underneath. (An earlier attempt that
    used `sprite.x` as a stand-in for the sprite's coordinate inside the node was
    additionally off by (1 - k) * anchorOffset, a small diagonal shift that reversed with the
    zoom direction and vanished at k = 1.)
  * the tracking is RE-SEEDED whenever the world node is swapped - which is what a location
    change does - and the zoom is held off only until the game has finished writing the new
    node's position. Reconstructing the engine's value from deltas only means anything while
    the node is the same object, and a new map is not positioned in a single frame either;
    both of those broke the camera on location transitions while zoomed, in a way only
    resetting the zoom and transitioning again could clear. The hold-off is measured, not
    timed: once the node's position has stood still for four frames the zoom goes straight
    back on, so a normal transition costs about 80ms of scale 1 rather than a fixed 750ms.

Because the engine's value is carried through rather than replaced, its edge clamp keeps
working: near a map border the player sits off-centre as usual, and zooming scales that
around the player rather than fighting it.
"""

NAME = "zoom"

# Solar2D reports the character the key produces, not the physical key, so the plus key
# arrives as '+' with rightShift alongside - not '=' as you would expect from the keycap.
# `--status` prints every key the game has seen; if one does not react, read its real name
# off there and pass it in.
KEY_OUT = ["-", "minus", "["]
KEY_IN = ["+", "=", "plus", "]"]
KEY_RESET = ["0"]


# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "install the overworld zoom"),
    (
        "pixels",
        0,
        "install already zoomed out, to this many screen pixels per texture pixel; "
        "0 is the game's own scale (no zoom)",
    ),
    ("key_out", list(KEY_OUT), "keys that zoom out one stop"),
    ("key_in", list(KEY_IN), "keys that zoom in one stop"),
    ("key_reset", list(KEY_RESET), "keys that go back to the game's own scale"),
    (
        "recenter",
        False,
        "one-shot repair: put the camera back on the player if an older build displaced "
        "it. Set it true, run once, then set it back to false.",
    ),
]


def _keylist(value, fallback):
    """Accept a bare key name where a list of them is expected."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value:
        return value
    return list(fallback)


def _set(names):
    return "{" + ", ".join("['" + n + "'] = true" for n in names) + "}"


def lua(cfg):
    """The screen-locked-layer queries. Only the zoom fits these, so they live here rather
    than in core - but --status reports them through this module's status()."""
    return r"""
-- Which Tiled layers are pinned to the screen (they carry a `stickToScreen` property).
-- The builder adds one group per layer, in order, so layer n is child n; disabled layers
-- still get their (empty) group, which is why the mapping stays one-to-one.
local function stickyLayerNodes(w)
  local out = {}
  local m = mte()
  if not m or type(m.getLayers) ~= 'function' then return out end
  local ok, layers = pcall(function() return m:getLayers() end)
  if not ok or type(layers) ~= 'table' then return out end
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

-- Centre of everything a node actually draws, in content coordinates. Going through the
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
"""


def section(cfg):
    if not cfg["enabled"]:
        return ""
    kout = _keylist(cfg["key_out"], KEY_OUT)
    kin = _keylist(cfg["key_in"], KEY_IN)
    kreset = _keylist(cfg["key_reset"], KEY_RESET)
    pixels = cfg["pixels"] if cfg["pixels"] > 0 else 0
    return (
        r"""
do
  local f = makeFeature('zoom', 0)   -- every frame, so an enterFrame listener, not a slot
  local z = {}
  f.state = z

  -- The zoom stops are whole numbers of screen pixels per texel: nativeScale, then one
  -- less, down to 1. `dir` of -1 zooms out a stop, +1 zooms in. k = pixels / nativeScale.
  local function stepScale(k, dir)
    local s = nativeScale()
    local i = math.floor(k * s + 0.5) + dir
    if i < 1 then i = 1 end
    if i > s then i = s end
    return i / s
  end

  -- How long to leave a newly built world alone after a location change. The new map is not
  -- positioned in a single frame, and the game's own writes while doing it are absolute
  -- values rather than movements, so they cannot be folded into the delta total.
  --
  -- Rather than waiting a fixed number of frames, wait for the writes to actually stop: the
  -- node's position standing still for a few frames means the map has finished being placed.
  -- A fixed 45-frame window (three quarters of a second at 60fps) left the map on screen at
  -- scale 1 for that whole time, which is very visible when playing zoomed out.
  local SETTLE_STABLE_FRAMES = 4
  local SETTLE_MAX_FRAMES = 90

  -- Screen-locked layers. Maps mark a whole Tiled layer `stickToScreen` (worldRainOverlay -
  -- the full-screen sheet drawn over the map), and the builder pins such a layer to the
  -- screen by translating it by the exact opposite of the world's translation on every
  -- camera move (tiledWorldBuilder.moveCamera, lines 363-365): the world goes one way and
  -- the layer the other, so the layer stands still on screen while the map slides underneath.
  --
  -- Zooming does not break that pinning - the zoom only ever reads the engine's value, so the
  -- world + layer sum it depends on survives - but it does break the layer's SIZE. The layer
  -- is laid out to fill the viewport at scale 1, so at a zoom of k it draws k times too small
  -- and covers only the middle of the screen. Measured at k = 0.5: a 512x297 overlay that
  -- covers the whole 480x265 screen shrank to 256x148.5 dead centre, which is the old
  -- viewport exactly.
  --
  -- So give it its size back: scale it by 1/k. That fixes the size but not the position - the
  -- content is not centred on the layer's own anchor (this overlay's image sits 480 units to
  -- the right of it), so scaling slides it sideways; measured, the centre jumps 240 -> 480.
  -- Rather than hard-code the offcentre, measure it: scale, see how far the content's centre
  -- moved, and put it back. The correction is a plain offset, so the previous one is undone
  -- before the next is derived - nothing is ever computed from a position we wrote ourselves,
  -- and at k = 1 the layer lands back where it started.
  local function fixSticky(w, k)
    local nodes = stickyLayerNodes(w)
    for i = 1, #nodes do
      local node = nodes[i]
      local rec = z.sticky[node]
      if not rec then rec = { corrx = 0, corry = 0 }; z.sticky[node] = rec end
      node.x, node.y = node.x - rec.corrx, node.y - rec.corry
      node.xScale, node.yScale = 1, 1
      rec.corrx, rec.corry = 0, 0
      if k ~= 1 then
        local bx, by = contentCentre(node)
        if bx then
          node.xScale, node.yScale = 1 / k, 1 / k
          local ax, ay = contentCentre(node)
          if ax then
            -- moving the node by d moves its content by k * d, so divide the error by k
            rec.corrx, rec.corry = (bx - ax) / k, (by - ay) / k
            node.x, node.y = node.x + rec.corrx, node.y + rec.corry
          end
        end
      end
    end
  end

  -- Re-fit the screen-locked layers, but only when something they depend on has changed -
  -- the zoom, the world node (a location change swaps it) or the viewport size.
  local function syncSticky(w, k)
    local cw, ch = display.contentWidth, display.contentHeight
    if z.fixNode == w and z.fixK == k and z.fixW == cw and z.fixH == ch then return end
    z.fixNode, z.fixK, z.fixW, z.fixH = w, k, cw, ch
    fixSticky(w, k)
  end

  local function applyZoom()
    local h = _G.__hud
    if not h or h.zoomBody ~= applyZoom or not f.on then return end

    local w = twNode()
    if not w then
      -- Out of the overworld (battle, menu, teardown). Drop everything - the node that comes
      -- back may well be a different object.
      z.node = nil
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
    if not f.on then return end
    -- Clamp to the useful range only: never past the game's own scale (no zooming in), and
    -- never below one screen pixel per texel.
    local s = nativeScale()
    if k > 1 then k = 1 end
    if k < 1 / s then k = 1 / s end
    z.scale = k
    applyZoom()
  end

  local function onKey(e)
    local h = _G.__hud
    if not h or h.keyBody ~= onKey or not f.on or type(e) ~= 'table' then return end
    local k = tostring(e.keyName)
    z.seen[k] = (z.seen[k] or 0) + 1
    z.lastKey = k .. '/' .. tostring(e.phase)
    if e.phase ~= 'down' then return end
    if f.kout[k] then setScale(stepScale(z.scale, -1))
    elseif f.kin[k] then setScale(stepScale(z.scale, 1))
    elseif f.kreset[k] then setScale(1) end
  end

  f.kout, f.kin, f.kreset = __KOUT__, __KIN__, __KRESET__
  f.koutTxt, f.kinTxt, f.kresetTxt = __KOUTTXT__, __KINTXT__, __KRESETTXT__
  z.scale = 1
  z.seen = {}
  z.sticky = setmetatable({}, { __mode = 'k' })

  f.kill = function()
    f.on = false
    pcall(function() Runtime:removeEventListener('enterFrame', applyZoom) end)
    pcall(function() Runtime:removeEventListener('key', onKey) end)
    local w = twNode()
    if w then
      w.xScale, w.yScale = 1, 1
      if z.w0x then w.x, w.y = z.w0x, z.w0y end
    end
    -- Hand the screen-locked layers their size and position back, or they stay scaled up by
    -- the last zoom we applied.
    for node, rec in pairs(z.sticky) do
      if type(node) == 'table' then
        node.x = node.x - (rec.corrx or 0)
        node.y = node.y - (rec.corry or 0)
        node.xScale, node.yScale = 1, 1
      end
    end
    z.sticky = setmetatable({}, { __mode = 'k' })
  end

  f.on = true
  H.zoomBody, H.keyBody = applyZoom, onKey
  Runtime:addEventListener('enterFrame', applyZoom)
  Runtime:addEventListener('key', onKey)

  -- The listener only reads events - it never consumes one, so the game's own controls keep
  -- working.
  setScale((__PIXELS__ > 0) and (__PIXELS__ / nativeScale()) or 1)
end
""".replace("__KOUT__", _set(kout))
        .replace("__KIN__", _set(kin))
        .replace("__KRESET__", _set(kreset))
        .replace("__KOUTTXT__", "'" + "/".join(kout) + "'")
        .replace("__KINTXT__", "'" + "/".join(kin) + "'")
        .replace("__KRESETTXT__", "'" + "/".join(kreset) + "'")
        .replace("__PIXELS__", str(pixels))
    )


def summary(cfg):
    kout = _keylist(cfg["key_out"], KEY_OUT)
    kin = _keylist(cfg["key_in"], KEY_IN)
    kreset = _keylist(cfg["key_reset"], KEY_RESET)
    # Replaced, not %-formatted: the Lua below is full of %s specifiers of its own.
    return (
        r"""(function()
  local s = nativeScale()
  local stops = {}
  for i = s, 1, -1 do stops[#stops + 1] = tostring(i) end
  return string.format(
    'zoom: %s screen px per texel at the game scale, stops %s  (%s out, %s in, %s reset)',
    tostring(s), table.concat(stops, '/'), __KOUTTXT__, __KINTXT__, __KRESETTXT__)
end)()""".replace("__KOUTTXT__", "'" + "/".join(kout) + "'")
        .replace("__KINTXT__", "'" + "/".join(kin) + "'")
        .replace("__KRESETTXT__", "'" + "/".join(kreset) + "'")
    )


def status(cfg):
    return r"""(function()
  local h = _G.__hud
  local f = h and h.feats.zoom
  local z = f and f.state
  if not z then return nil end
  local out = {}
  local s = nativeScale()
  out[#out + 1] = string.format(
    'zoom: %.2f screen px per texel (game scale %s)  lastKey=%s  engineBase=(%.2f, %.2f)',
    (z.scale or 1) * s, tostring(s), tostring(z.lastKey), z.w0x or 0, z.w0y or 0)
  local w = twNode()
  out[#out + 1] = w and string.format('worldScale=%.4f', w.xScale or 1)
    or 'worldScale: not in the overworld'
  if w then
    local sticky = stickyLayerNodes(w)
    local parts = {}
    for i = 1, #sticky do
      parts[#parts + 1] = string.format('%dx%d at scale %.3f',
        sticky[i].width or 0, sticky[i].height or 0, sticky[i].xScale or 1)
    end
    if #parts > 0 then
      out[#out + 1] = 'screen-locked layers (fit by the zoom): ' .. table.concat(parts, ', ')
    end
  end
  local keys = {}
  for k, c in pairs(z.seen or {}) do keys[#keys + 1] = k .. '(' .. c .. ')' end
  table.sort(keys)
  out[#out + 1] = 'keys seen by the game: '
    .. (#keys > 0 and table.concat(keys, ' ') or '(none yet)')
  return table.concat(out, '\n')
end)()"""


def report(cfg):
    return r"""(function()
  local h = _G.__hud
  local z = h and h.feats.zoom and h.feats.zoom.state
  if not z then return nil end
  return string.format('zoom - %s screen px per texel', (z.scale or 1) * nativeScale())
end)()"""


# The camera is incremental, so once tiledWorld.x/y has been written absolutely the error is
# baked into the engine's base and survives walking, map changes and resets. This puts it
# back: work out the sprite's anchor offset, then place the node so the player renders at the
# middle of the viewport. Deriving it from the viewport means there is no magic number to rot
# when the window or content size changes.
RECENTER = r"""
local w, p = twNode(), playerSprite()
if not w or not p then return '!need to be in the overworld for this!' end
local lx, ly = p:localToContent(0, 0)
local ax, ay = lx - (w.x + p.x), ly - (w.y + p.y)
w.xScale, w.yScale = 1, 1
w.x = display.contentWidth / 2 - ax - p.x
w.y = display.contentHeight / 2 - ay - p.y
local h = _G.__hud
local z = h and h.feats.zoom and h.feats.zoom.state
if z then
  z.wx, z.wy = nil, nil
  z.scale = 1
end
return string.format(
  'recentred: player was at content (%.1f, %.1f), now (%.1f, %.1f) in a %sx%s viewport',
  lx, ly, display.contentWidth / 2, display.contentHeight / 2,
  tostring(display.contentWidth), tostring(display.contentHeight))
"""

# One-shot actions: a setting of the same name turns one on (see SETTINGS above), and core
# runs it before applying the rest.
ACTIONS = {"recenter": RECENTER}
