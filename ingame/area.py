#!/usr/bin/env python3
"""
area.py - the current area's name, drawn in the pause menu's bottom right.

The game knows what a place is called and shows it once: `worldInterface:createMapNamePopup` puts
a popup up for a few seconds when a map loads and then fades it away. After that there is nothing
on screen that says where you are, which matters in this game more than in most - the caves are
numbered floors of the same map and read identically ("Frozen Cavern" on every one of its six),
and a lot of travelling is "am I in the right part of this route yet".

THE NAME IS THE GAME'S OWN, taken from the two accessors the popup itself uses:

    worldHelper:getWorldMapData():getMapName()          the map's own name, a key like 'frozenCave'
    worldLocationUtility:getLocalised(<that key>)       localise('world.map.<key>.name')

so what this draws is the same string a player is shown on entering the map, localised the same
way - not a table of names kept here (there is none to keep: the names live in the game's own
localisation data, "Frozen Cavern" for `frozenCave`, "Lux Solis", "Amish Town", ...). Measured:
every map module calls `setMapName(<a key>)` from its own create function, and 63 keys exist.

THE FLOOR NUMBER IS OURS, and it is the reason this feature is worth having at all. The game's
popup shows only the place, so all six floors of the frozen cave say "Frozen Cavern". The map
FILE carries the part that differs (`frozenCave_1`, `desertRoute_3`, `electricCave_f1`) while
`setMapName` names the place, so the two together are what "Frozen Cavern 1" is. The file name is
only used when it STARTS WITH the map's own name, and that guard is the whole safety of it:

    frozenCave_1  + frozenCave   -> "Frozen Cavern 1"        what was asked for
    desertRoute_3 + desertRoute  -> "Desert Route 3"
    electricCave_f1 + electricCave -> "Electric Cave F1"
    amishTown                     -> "Amish Town"            nothing to add
    houseNoah_bf1 + amishTown    -> "Amish Town"             NOT "Amish Town HouseNoah Bf1"

The last case is why the guard exists: a house or a basement inside a town is a different place
whose name is not a suffix of the town's, and appending it would invent something wrong. Turn
`show_sub_area` off to see the plain name everywhere.

WHERE IT IS DRAWN. In the pause menu's bottom bar, whose object the game will hand over:
`pauseMenu:getBottomBar()` (nil when the menu is not open; the accessor lives on the menu's
instance and reaches the module through its metatable, which is why `pauseMenu:getBottomBar()`
works even though the name is not a direct key of the module table). The label is positioned from
that bar's OWN BOX, read from the live object every frame, so it follows the bar while the menu
opens and closes rather than assuming a screen position. It is NOT parented into the bar: that bar
re-lays out the objects it is handed, and a foreign child in the game's own nav bar is the kind of
thing this project has already been bitten by. It is our own group kept on top of the stage
instead, and hidden the moment the menu is not up.

THE BOX COMES FROM `contentBounds`, AND THAT IS NOT A PREFERENCE. The first version used
`localToContent(0,0)..localToContent(w,h)` and drew nothing at all, because on a container that
call answers the object's ORIGIN, which here is its CENTRE. Measured on the live bar: declared
512x32 at x=256 y=272 with anchor 0.5/0.5, so `localToContent(0,0)` returned **(256, 272)** - its
middle - and `localToContent(512,32)` returned **(768, 304)**, off the right-hand side of a 512-unit
screen. The label was at x=668 and simply never on screen. `contentBounds` on the same object is
the box and agrees with the anchor arithmetic exactly: **0..512 x 256..288**. The anchor maths
(`x + w*(1-anchorX)`, `y - h*anchorY`) is kept as the fallback for an object that answers no
bounds. Same trap as the one already recorded for groups in this project: a group's
`localToContent` is not its on-screen box.

WHAT THE BAR CONTAINS, measured, which is what makes the bottom right a place to put this at all:
the bar is 32 units tall across the whole 512-unit width, its top 10 units are a fade gradient with
the two 18x8 ornaments at its left and right ends, and the nav prompts sit in the LEFT half (one
was at x 5..160, y 266..287). The right half was empty. A label placed against the bar's right
edge, centred on the bar's box, therefore lands in clear space just below the ornament line.

The two offsets exist because the bar's box is the game's, not ours - `offset_x` moves the text
in from the bar's right end (the right ornament occupies some of it) and `offset_y` off the bar's
vertical centre. Both are content units, and `--report` prints the bar's measured box, so the
pair can be set from numbers rather than by guessing.
"""

NAME = "area"

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment, choices).
SETTINGS = [
    (
        "enabled",
        True,
        "show the current area's name in the pause menu",
    ),
    (
        "font",
        "outline_8",
        "label font: outline_8 is the game's small font, outline_10_bold the larger one",
        ["outline_8", "outline_10_bold"],
    ),
    (
        "show_sub_area",
        True,
        "also show the part of the map's name that the game does not: the floor number or letter "
        "taken from the map file, so `Frozen Cavern 1` rather than `Frozen Cavern` on all six "
        "floors. It is only used when the map's file name starts with the map's own name, so a "
        "room named differently inside a town is left as the plain name rather than invented",
    ),
    (
        "offset_x",
        -12,
        "how far in from the right end of the pause menu's bottom bar the label sits, in content "
        "units - negative moves it left",
    ),
    (
        "offset_y",
        0,
        "how far off the bar's vertical centre the label sits, in content units",
    ),
]


def _num(value):
    """A Lua number for a setting that may have been typed as an int or a float."""
    try:
        return repr(float(value))
    except (TypeError, ValueError):
        return "0"


def _lua_str(value):
    """A Lua string literal for a setting coming out of overlays.toml."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def lua(cfg):
    """The queries. Always part of the chunk, so --status and --report can describe the area
    whether or not the feature is installed."""
    return r"""
local function areaFeature()
  local h = _G.__hud
  local f = h and h.feats and h.feats.area
  if not f then return nil end
  return f
end

-- The map's own name, the key every map module hands the game from its own create function
-- (`setMapName('frozenCave')`). nil rather than an error when there is no world yet.
local function areaRawName()
  if type(worldHelper) ~= 'table' then return nil end
  local ok, wmd = pcall(worldHelper.getWorldMapData, worldHelper)
  if not ok or type(wmd) ~= 'table' then return nil end
  local okN, raw = pcall(wmd.getMapName, wmd)
  if not okN or type(raw) ~= 'string' or raw == '' then return nil end
  return raw
end

-- The name to show for `raw`. This is the game's own localisation of it - the same call the
-- map-name popup makes - so the string is the one the player is shown on entering the map. A
-- missing key comes back as '???' (the locator's own marker) and is treated as absent, and an
-- unlocalised key is better than no label at all.
local function areaTitle(raw)
  if type(raw) ~= 'string' or raw == '' then return nil end
  if type(worldLocationUtility) == 'table' then
    local ok, v = pcall(worldLocationUtility.getLocalised, worldLocationUtility, raw)
    if ok and type(v) == 'string' and v ~= '' and v ~= '???' then return v end
  end
  return raw
end

-- The part of the map's name the game does not show: the floor. Taken from the map FILE, and
-- only when that file name starts with the map's own name - see the module docstring for why
-- that guard is the difference between "Frozen Cavern 1" and an invented name.
local function areaSubName(raw)
  if type(raw) ~= 'string' or raw == '' then return nil end
  if type(worldHelper) ~= 'table' then return nil end
  local ok, file = pcall(worldHelper.getMapFile, worldHelper)
  if not ok or type(file) ~= 'string' or file == '' then
    ok, file = pcall(worldHelper.getMapPath, worldHelper)
  end
  if not ok or type(file) ~= 'string' or file == '' then return nil end
  file = tostring(file):gsub('\\', '/')
  file = file:match('([^/]+)$') or file          -- the last path component
  file = file:gsub('%.%w+$', '')                 -- and no extension
  local prefix = raw .. '_'
  if file:sub(1, #prefix) ~= prefix then return nil end
  local tail = file:sub(#prefix + 1)
  if tail == '' then return nil end
  return (tail:gsub('_', ' '):upper())
end

-- What the label says, or nil when there is nothing to say yet.
local function areaLabel()
  local raw = areaRawName()
  if not raw then return nil end
  local title = areaTitle(raw)
  if not title then return nil end
  if not (__SUBF__) then return title end
  local sub = areaSubName(raw)
  if sub then return title .. ' ' .. sub end
  return title
end

-- The pause menu's bottom bar, or nil when the menu is not open. `isCreated` is the pause menu
-- module's own `return instance`, which is nil after the menu is destroyed (measured in this
-- build), and the bar's `parent` is what says it is still on the stage.
local function areaMenuOpen()
  if type(pauseMenu) ~= 'table' then return false end
  local ok, v = pcall(pauseMenu.isCreated, pauseMenu)
  if not ok or v == nil or v == false then return false end
  return true
end

local function areaBottomBar()
  if not areaMenuOpen() then return nil end
  local ok, bar = pcall(pauseMenu.getBottomBar, pauseMenu)
  if not ok or type(bar) ~= 'table' or bar.parent == nil then return nil end
  return bar
end
""".replace("__SUBF__", "true" if cfg["show_sub_area"] else "false")


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return (
        r"""
do
  -- Period 0: this needs every frame, because the pause menu's bar animates in and out and the
  -- label has to follow it and vanish with it. It brings its own enterFrame listener, and the
  -- guard field is its own (`areaFrame`) - core's zoom owns keyBody and the host owns tickBody.
  local f = makeFeature('area', 0)
  f.font = __FONT__
  f.offsetX, f.offsetY = __OX__, __OY__
  f.ticks, f.draws = 0, 0

  -- The game's own text colour for these labels is white with a black outline; a mid-grey fill
  -- on an outline font reads as a blob, so it stays light.
  local FILL = { 0.88, 0.94, 1 }

  local function ensure()
    if f.group then return end
    local g = display.newGroup()
    g.isVisible = false
    local t = textHelper:new(g, f.font, { text = '' })
    pcall(function() t.anchorX, t.anchorY = 0, 0 end)
    pcall(function() t:setFillColor(FILL[1], FILL[2], FILL[3]) end)
    f.group, f.label = g, t
  end

  local function hide()
    ensure()
    if f.group.isVisible then f.group.isVisible = false end
  end

  local function frame()
    local h = _G.__hud
    if not h or h.areaFrame ~= frame or not f.on then return end
    f.ticks = f.ticks + 1

    -- The bar is looked for only when the one we have has gone: per frame this is one
    -- `isCreated` call and a field read, and the search itself runs once per menu.
    local bar = f.bar
    if bar and (bar.parent == nil or not areaMenuOpen()) then bar = nil end
    if not bar then bar = areaBottomBar() end
    f.bar = bar
    if not bar then hide() return end

    ensure()
    -- The bar's own box in content coordinates. NOT localToContent: on a container that answers
    -- the object's ORIGIN, which for this bar is its CENTRE - measured, it returned (256,272) for
    -- a bar declared 512x32 at x=256 y=272, and (768,304) for its far corner on a 512-wide
    -- screen, which is why the first version of this drew nothing at all. contentBounds IS the
    -- box; the anchor arithmetic is the fallback for anything that answers no bounds.
    local brx, btly, bbry
    local okCB, cb = pcall(function() return bar.contentBounds end)
    if okCB and type(cb) == 'table' and type(cb.xMax) == 'number' and type(cb.yMin) == 'number'
       and type(cb.yMax) == 'number' and cb.xMax > cb.xMin and cb.yMax > cb.yMin then
      brx, btly, bbry = cb.xMax, cb.yMin, cb.yMax
    else
      local bw, bh = tonumber(bar.width) or 0, tonumber(bar.height) or 0
      local bx, by = tonumber(bar.x) or 0, tonumber(bar.y) or 0
      local ax, ay = tonumber(bar.anchorX) or 0.5, tonumber(bar.anchorY) or 0.5
      brx = bx + bw * (1 - ax)
      btly = by - bh * ay
      bbry = btly + bh
    end
    if type(brx) ~= 'number' or type(btly) ~= 'number' or type(bbry) ~= 'number' then
      hide() return
    end

    local want = f.want
    if want == nil or (f.ticks % 60) == 0 then
      want = areaLabel() or ''
      f.want = want
    end
    -- Written only when the WANTED string changes, never by comparing with what was read back:
    -- a character the font cannot draw is stored as something else, and a read-back comparison
    -- would then rewrite the label every frame (that is what made the gold counter blink).
    if f.written ~= want then
      pcall(function() f.label.text = want end)
      pcall(function() f.label:setFillColor(FILL[1], FILL[2], FILL[3]) end)
      f.written = want
    end
    if want == '' then hide() return end

    local w = tonumber(f.label.width) or 0
    local hh = tonumber(f.label.height) or 0
    f.label.x = brx + f.offsetX - w
    f.label.y = ((btly + bbry) / 2) + f.offsetY - (hh / 2)
    f.box = string.format('%.0f,%.0f..%.0f,%.0f', brx - (bar.width or 0), btly, brx, bbry)
    f.group.isVisible = true
    keepOnTop(f.group)          -- only touches the display list when the game drew over us
    f.draws = f.draws + 1
  end

  local function kill()
    f.on = false
    pcall(function() Runtime:removeEventListener('enterFrame', frame) end)
    if f.group then drop(f.group) end
    f.group, f.label, f.bar, f.want, f.written = nil, nil, nil, nil, nil
    local h = _G.__hud
    if h and h.areaFrame == frame then h.areaFrame = nil end
  end

  f.on = true
  f.kill = kill
  Runtime:addEventListener('enterFrame', frame)
  local H = _G.__hud
  if H then H.areaFrame = frame end
end
""".replace("__FONT__", _lua_str(cfg["font"]))
        .replace("__OX__", _num(cfg["offset_x"]))
        .replace("__OY__", _num(cfg["offset_y"]))
    )


def summary(cfg):
    return r"""(function()
  local f = areaFeature()
  if not f or not f.on then return nil end
  return 'area name in the pause menu: bottom right, from the game\'s own map name'
end)()"""


def status(cfg):
    return r"""(function()
  local f = areaFeature()
  if not f or not f.on then return nil end
  local bar = areaBottomBar()
  if not bar then return string.format('area name: %s, drawn only while the pause menu is open', tostring(areaLabel())) end
  return string.format('area name: %s  (pause menu open, bottom bar %sx%s)',
    tostring(areaLabel()), tostring(bar.width), tostring(bar.height))
end)()"""


def report(cfg):
    return r"""(function()
  local out = {}
  local f = areaFeature()
  out[#out + 1] = 'area name in the pause menu (bottom right)'
  if not f then
    out[#out + 1] = '  not installed'
  else
    out[#out + 1] = string.format('  installed = %s   frames drawn on = %d   font = %s',
      tostring(f.on == true), f.draws or 0, tostring(f.font))
    out[#out + 1] = string.format('  offsets = x %s, y %s', tostring(f.offsetX), tostring(f.offsetY))
  end

  local raw = areaRawName()
  out[#out + 1] = string.format('  map name as the game holds it = %s', tostring(raw))
  out[#out + 1] = string.format('  localised (what the map popup shows) = %s', tostring(areaTitle(raw)))
  out[#out + 1] = string.format('  sub-area taken from the map file = %s', tostring(areaSubName(raw)))
  out[#out + 1] = string.format('  the label would read = %s', tostring(areaLabel()))

  local okF, file = pcall(worldHelper.getMapFile, worldHelper)
  local okP, path = pcall(worldHelper.getMapPath, worldHelper)
  out[#out + 1] = string.format('  map file = %s   map path = %s',
    tostring(okF and file or nil), tostring(okP and path or nil))

  local open = areaMenuOpen()
  out[#out + 1] = string.format('  pause menu open = %s', tostring(open))
  local bar = areaBottomBar()
  if not bar then
    out[#out + 1] = '  no bottom bar: open the pause menu and run this again to see the bar box'
  else
    -- contentBounds is the box; localToContent on this container is its ORIGIN (its centre).
    -- Both are printed because the difference between them is what made the label invisible.
    local okCB, cb = pcall(function() return bar.contentBounds end)
    if okCB and type(cb) == 'table' and type(cb.xMax) == 'number' then
      out[#out + 1] = string.format('  bar box (contentBounds): x %.2f..%.2f  y %.2f..%.2f',
        cb.xMin, cb.xMax, cb.yMin, cb.yMax)
      out[#out + 1] = string.format('  the label is put at x = %.2f + offset_x, y = %.2f + offset_y',
        cb.xMax, (cb.yMin + cb.yMax) / 2)
    end
    local okTL, tlx, tly = pcall(bar.localToContent, bar, 0, 0)
    local okBR, brx, bry = pcall(bar.localToContent, bar, bar.width, bar.height)
    out[#out + 1] = string.format('  bar declared: %sx%s at x=%s y=%s anchor %s/%s, up %s frames',
      tostring(bar.width), tostring(bar.height), tostring(bar.x), tostring(bar.y),
      tostring(bar.anchorX), tostring(bar.anchorY), tostring(f and f.draws))
    if okTL and okBR then
      out[#out + 1] = string.format('  localToContent: (0,0) -> %.2f,%.2f   (w,h) -> %.2f,%.2f  (its ORIGIN, not its corner)',
        tlx, tly, brx, bry)
    end
    if f and f.label then
      out[#out + 1] = string.format('  label at x=%.2f y=%.2f size %sx%s visible=%s',
        f.label.x, f.label.y, tostring(f.label.width), tostring(f.label.height),
        tostring(f.group and f.group.isVisible))
    end
    out[#out + 1] = string.format('  screen is %sx%s content units',
      tostring(display.contentWidth), tostring(display.contentHeight))
  end
  return table.concat(out, '\n')
end)()"""
