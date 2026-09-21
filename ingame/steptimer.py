#!/usr/bin/env python3
"""
steptimer.py - how many more steps the Potentiflator and Traitformator still need.

Both of the "leave a Coromon here and come back later" services are counted in steps, and
neither tells you how far along it is: you hand a monster over, walk, and wait for a
notification. This puts the remaining distance in a corner of the screen so you can go and
do something else and still know when to come back.

WHERE THE NUMBER COMES FROM - the game's own, read live, never a copy or a running total:

    settings.SAVEABLE_BATTLE_EFFECTS     the save's pending world effects, one per deposit
      [i]:getTargetPlayerSteps()         the step count that deposit completes at
      [i]:getClassPath()                 which effect it is
    playerStats.getSteps()               steps walked so far

    remaining = max(0, target - steps)

Checked against a live save: steps 82089 with targets 82587 and 83082 gives 498 and 993,
which is the countdown the game itself was showing. The step total matters - Potentiflator
takes 1000 steps and Traitformator 500 - but it is read from the effect's own target rather
than assumed, so the numbers stay right whatever they are set to.

WHY IT SURVIVES A SAVE THAT IS NEVER SAVED. This reads the game's live values out of memory
every tick, so it is unaffected by the save on disk: a run that is never written still counts
down, and reloading an older save simply shows that save's own positions. Nothing is
accumulated here, so there is nothing to go stale.

`SAVEABLE_BATTLE_EFFECTS` is the LIVE list. The neighbouring `SAVED_BATTLE_EFFECTS` is the
serialised one and does not track a deposit as it happens, which is why the live one is what
is read. It is also why nothing has to be hooked to notice a new deposit: a fresh handover
appears in this list with its own target, so the counter picks it up on its next tick and
starts counting down by itself, with no install step and no class hook. The list is re-found
every RESCAN_TICKS because a save reload replaces the table it is read from.

Finding that table is a search, and that is the game's fault rather than a choice: the save
data is not reachable from any global, from Game/Save/the player, or from any displayed
object - it exists only as an upvalue of functions inside loaded modules. So it is found by
walking `package.loaded` and asking each function for its upvalues, taking the table that has
both SAVEABLE_BATTLE_EFFECTS and VISITED_MAPS. Those two keys together are specific enough to
be unambiguous; no other loaded module holds both.

WHICH ENTRIES COUNT. The list also holds item effects - on a live save a magnet hat and a
gold booster sit in it - and those have no step target at all. So an entry counts only when
it actually exposes `getTargetPlayerSteps`. Nothing is keyed on a class name, and a third
step-counted effect would appear without a change here.

AND IT HAS TO STILL HAVE ITS COROMON, which is the part that was missing and is what the
screenshot of "Potentiflator: 1000 steps" over an empty machine was. The constructor files the
monster away and keeps only its identifier:

    abstractUpgradeMonsterAfterPlayerStepsWorldEffect L16
      monsterIdentifier  = playerMonsters:addToHiddenMonsterStorage(_monster)
      targetPlayerSteps  = playerStats:getSteps() + _amountOfPlayerStepsRequired
    getMonster L34
      return playerMonsters:getMonsterInHiddenStorage(monsterIdentifier)

so `getMonster()` is nil once the Coromon has been collected - and the game keeps the entry in
that list anyway, in the SAVED data. That is why the stale countdown could survive a reload and
come back: the step number was real, the deposit was not. An entry with a target but no parked
monster is therefore not shown, and `--report` says how many were skipped rather than hiding it
silently. A deposit whose steps are done but whose Coromon has not been collected still has its
monster, so "ready" still shows. Where an effect type has no `getMonster` at all it is shown as
before, so an unknown future one is not silently suppressed.

THE NAMES are the one thing that is a lookup rather than the game's word: the two the game
has are shown as "Potentiflator" and "Traitformator", from a table keyed on the last part of
the effect's class path. Anything else falls back to its own class name, spaced out, so an
effect this does not know about still has a readable label. The NUMBERS are never from here.

THE RESULT IS READABLE BEFORE THE WALK, and that is the most useful thing this does. The
Potentiflator decides its answer AT HANDOVER, not when the steps are done: the game rerolls,
writes the new value onto the Coromon and sets its `didRerollPotential` flag, then parks it in
hidden monster storage. So the outcome can be read the instant you hand the Coromon over, and
the 1000 steps only need walking once the answer is one you want. The counter shows
"(P19 to P21 PERFECT)" or "(P19 to P20)" immediately. `show_potential` turns it off.

That flag matters and is why it is checked: without it `mon.potential` would still be the
untouched original, and an unset flag would mean the reroll has not been applied yet.

HOW IT DRAWS, and the measurements behind it:

  * the block is drawn on the display stage, so it is screen-space - it does not move with
    the map and is unaffected by the overworld zoom.
  * the font is `outline_10_bold`, the game's larger one.
  * `spacing` is 9 content units, and that number is measured rather than picked. The game
    renders text as ONE SPRITE PER GLYPH inside a box that reserves the full 18-unit line
    height, while the glyphs occupy only 8 units of it (measured on a live label: child
    sprite `pos=0,4 size=8x8`) - the other 5 units each side are empty padding. Two labels
    are therefore separated by `spacing - 8` units of visible gap, and sweep against the
    live screen measured 2/5/10 px at 8.5/9.0/10.0 units. Below 8.5 the two lines physically
    touch, which is why 9 is the default and why anything smaller is not worth setting.
  * for that same reason both counters are NOT one text object holding a newline: a `\\n`
    follows the 18-unit box and gives a 60 px gap with no way to tighten it, where two
    labels give exactly the 5 px the measurement asks for.
  * the order is by remaining steps, smallest first, so whichever finishes next is on top.
  * a count of one reads "1 step", not "1 steps".
"""

NAME = "steptimer"

# re-find the live save table every this many ticks. At the 250 ms poll that is 12.5 s, which
# is the cost of a `package.loaded` walk against how long a stale table can survive a reload.
RESCAN_TICKS = 50

CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right"]

# The two step-counted effects the game ships, by the name the game shows for them, keyed on
# the last component of the effect's class path. A display label only - never the number.
NICE_NAMES = {
    "traitRerollWorldEffect": "Traitformator",
    "potentialRerollWorldEffect": "Potentiflator",
}

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "show the Potentiflator / Traitformator step countdown"),
    (
        "corner",
        "top-left",
        "which corner to put it in",
        CORNERS,
    ),
    ("margin_x", 8, "gap from the screen edge, in content units, horizontally"),
    ("margin_y", 8, "gap from the screen edge, in content units, vertically"),
    (
        "spacing",
        9,
        "distance between the two lines, in content units. Measured: the glyphs are 8 units "
        "tall, so the visible gap is spacing - 8, and 9 gives 5 px. Below 8.5 the lines "
        "overlap. Only worth changing if you want them further apart.",
    ),
    ("ready_text", "ready", "what a finished countdown reads, as in \"Traitformator: ready\""),
    (
        "show_potential",
        True,
        "also show the Potentiflator result. The game decides it the moment the Coromon is "
        "handed over, so it reads out before the 1000 steps are walked - a roll you do not "
        "want then costs a reload instead of the whole walk",
    ),
]


def _lua_str(value):
    """A Lua string literal for a setting coming out of overlays.toml."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _nice_table():
    """NICE_NAMES as a Lua table literal."""
    return "{" + ", ".join(
        "[%s] = %s" % (_lua_str(k), _lua_str(v)) for k, v in NICE_NAMES.items()
    ) + "}"


def lua(cfg):
    """The queries. Always part of the chunk - the install summary, the status line and the
    report all read them, whether or not the overlay itself is installed."""
    return r"""
local STEP_SHOW_POTENTIAL = __SHOW__

-- The live save data, found by walking the loaded modules. It is not reachable from any
-- global, from Game/Save/the player, or from any object on screen: it exists only as an
-- upvalue of functions inside loaded modules, so this asks each function for its upvalues
-- and takes the table carrying both SAVEABLE_BATTLE_EFFECTS and VISITED_MAPS (checked
-- against every loaded module - no other one holds both).
local function saveSettings()
  for _, mod in pairs(package.loaded) do
    if type(mod) == 'table' then
      for _, fn in pairs(mod) do
        if type(fn) == 'function' then
          for i = 1, 80 do
            local n, v = debug.getupvalue(fn, i)
            if not n then break end
            if type(v) == 'table'
              and v.SAVEABLE_BATTLE_EFFECTS ~= nil and v.VISITED_MAPS ~= nil then
              return v
            end
          end
        end
      end
    end
  end
end

-- Steps walked so far, straight from the game's own counter.
local function stepsWalked()
  local ok, n = pcall(function() return playerStats.getSteps() end)
  if ok then
    local v = tonumber(n)
    if v then return v end
  end
end

-- The display names, from NICE_NAMES. A local is needed rather than indexing the literal
-- inline: `{...}[tail]` is not valid Lua, a table constructor cannot be indexed directly.
-- This is the only lookup in the file - the number never comes from here.
local NICE = __NICE__

-- Which effect this is, for the label.
local function effectName(e)
  local ok, p = pcall(function() return e:getClassPath() end)
  if not ok or type(p) ~= 'string' then return 'effect' end
  local tail = p:match('([%w_]+)$') or p
  local nice = NICE[tail]
  if nice then return nice end
  local s = tail:gsub('WorldEffect$', '')
  s = s:gsub('(%l)(%u)', '%1 %2')
  return (s:sub(1, 1):upper() .. s:sub(2))
end

-- The Potentiflator's answer, if the game has already decided it. It decides AT HANDOVER:
-- the new value is written onto the Coromon, which then waits in hidden monster storage
-- until the steps are walked. So this reads immediately, which is the point - an unwanted
-- roll then costs a reload rather than the full 1000-step walk.
--
-- Only the potential-reroll effect has these accessors, and `didRerollPotential` is the flag
-- the game sets once it has applied the result; without it `mon.potential` would still be
-- the untouched original, so it is checked rather than assumed.
local function decidedPotential(e)
  if type(e) ~= 'table' then return nil, nil end
  if type(e.getOriginalPotential) ~= 'function' or type(e.getMonster) ~= 'function' then
    return nil, nil        -- the trait reroll has no such accessors
  end
  local okM, mon = pcall(function() return e:getMonster() end)
  if not okM or type(mon) ~= 'table' or not mon.didRerollPotential then return nil, nil end
  local okO, from = pcall(function() return e:getOriginalPotential() end)
  return (okO and tonumber(from) or nil), tonumber(mon.potential)
end

-- Every pending step-counted deposit, smallest remaining first. An entry counts only when it
-- exposes getTargetPlayerSteps, which is what separates the two reroll services from the
-- item effects (a magnet hat and a gold booster) sitting in the same list with no target.
--
-- AND only when its Coromon IS STILL PARKED. That is the second condition and it is the one that
-- matters after a reload:
--
--   abstractUpgradeMonsterAfterPlayerStepsWorldEffect L16
--     _saveableOptions.monsterIdentifier = playerMonsters:addToHiddenMonsterStorage(_monster)
--     _saveableOptions.targetPlayerSteps = playerStats:getSteps() + _amountOfPlayerStepsRequired
--   getMonster (L34)
--     return playerMonsters:getMonsterInHiddenStorage(_saveableOptions.monsterIdentifier)
--
-- so `getMonster()` is a pure lookup of that identifier and comes back nil once the Coromon has been
-- collected. The game keeps the entry in the list after that, and it is SAVED, which is why
-- "Potentiflator: 1000 steps" could sit on screen over an empty machine and come back after a quit to
-- the menu and a reload. Nothing is keyed on a class name: a genuine handover is in hidden storage by
-- definition, and the same accessor exists on both reroll services because they share that base
-- class. A completed-but-uncollected deposit still has its monster, so "ready" still shows.
local function stepJobs(settings)
  settings = settings or saveSettings()
  local out = {}
  _G.__hudStepsSkipped = 0
  if type(settings) ~= 'table' then return out end
  local steps = stepsWalked()
  if not steps then return out end
  for _, e in ipairs(settings.SAVEABLE_BATTLE_EFFECTS or {}) do
    local getter = type(e) == 'table' and e.getTargetPlayerSteps
    if type(getter) == 'function' then
      local ok, v = pcall(function() return e:getTargetPlayerSteps() end)
      local target = ok and tonumber(v) or nil
      -- The parked-Coromon test only applies where the accessor exists: an effect that has a target
      -- but no getMonster is something this does not know about, and showing it as before is the
      -- safe answer there.
      local checkable = type(e.getMonster) == 'function'
      local parked = false
      if checkable then
        local okM, mon = pcall(function() return e:getMonster() end)
        parked = okM and type(mon) == 'table'
      end
      if target and (not checkable or parked) then
        local left = target - steps
        if left < 0 then left = 0 end
        local from, to = decidedPotential(e)
        out[#out + 1] = { name = effectName(e), left = left, target = target,
                          from = from, to = to }
      elseif target then
        -- a leftover, not a deposit: counted so the report can say so instead of hiding it silently
        _G.__hudStepsSkipped = _G.__hudStepsSkipped + 1
      end
    end
  end
  table.sort(out, function(a, b)
    if a.left ~= b.left then return a.left < b.left end
    return a.name < b.name
  end)
  return out
end

-- One line, e.g. "Traitformator: 498 steps" and - at one step - "Traitformator: 1 step".
-- A decided Potentiflator result is appended as "(P19 to P21 PERFECT)", on the same line so
-- no extra row is spent on it.
--
-- The arrow is spelled "to" rather than a glyph: the game draws text from a fixed glyph
-- atlas, and a character it lacks would silently render as nothing.
local function stepLine(job, ready)
  local s
  if job.left <= 0 then
    s = job.name .. ': ' .. ready
  else
    s = job.name .. ': ' .. tostring(job.left) .. ((job.left == 1) and ' step' or ' steps')
  end
  if STEP_SHOW_POTENTIAL and job.to then
    local pre = ''
    if job.from then pre = 'P' .. tostring(job.from) .. ' to ' end
    s = s .. ' (' .. pre .. 'P' .. tostring(job.to)
        .. ((job.to == 21) and ' PERFECT' or '') .. ')'
  end
  return s
end
""".replace("__NICE__", _nice_table()).replace(
        "__SHOW__", "true" if cfg["show_potential"] else "false"
    )


def section(cfg):
    if not cfg["enabled"]:
        return ""
    corner = cfg["corner"] if cfg["corner"] in CORNERS else CORNERS[0]
    spacing = cfg["spacing"]
    if spacing < 1:
        spacing = 9
    return (
        r"""
do
  local f = makeFeature('steptimer', 250)
  f.texts = {}
  f.group = nil
  f.save = nil
  f.ticks = 0

  local FONT = 'outline_10_bold'
  local WHITE = { 1, 1, 1 }
  local GOLD = { 1, 0.85, 0.2 }        -- the same gold the Potential readouts use for 21
  local CORNER = __CORNER__
  local MX, MY = __MX__, __MY__
  local SPACING = __SPACING__
  local READY = __READY__

  -- The live save table, re-found periodically: a save reload replaces the table this reads
  -- from, and the old one goes stale without ever looking wrong.
  local function save()
    if f.save == nil or (f.ticks % __RESCAN__) == 0 then
      local s = saveSettings()
      if s then f.save = s end
    end
    return f.save
  end

  local function clear()
    drop(f.group)
    f.group, f.texts, f.key = nil, {}, nil
  end

  -- Anchors are set per label rather than on the group so a corner can be honoured without
  -- knowing the block's size in advance - the two lines are different widths, so there is no
  -- single right edge to align and no measured width to rely on.
  local function place(g, n)
    local right = (CORNER == 'top-right' or CORNER == 'bottom-right')
    local bottom = (CORNER == 'bottom-left' or CORNER == 'bottom-right')
    local ax, ay = (right and 1 or 0), (bottom and 1 or 0)
    g.x = right and (display.contentWidth - MX) or MX
    g.y = bottom and (display.contentHeight - MY) or MY
    for i = 1, n do
      local t = f.texts[i]
      if t then
        -- growing upward from a bottom corner means the LAST line sits on the anchor and the
        -- first is furthest from it - otherwise the list would read bottom-to-top
        local off = bottom and -(n - i) * SPACING or (i - 1) * SPACING
        pcall(function()
          t.anchorX, t.anchorY = ax, ay
          t.x, t.y = 0, off
        end)
      end
    end
  end

  local function render(jobs)
    if not f.group then
      local g = display.newGroup()
      pcall(function() g.name = 'stepTimer' end)
      f.group = g
    end
    for i = 1, #jobs do
      local job = jobs[i]
      local s = stepLine(job, READY)
      local t = f.texts[i]
      if not t then
        t = text(f.group, FONT, s, 0, 0)
        f.texts[i] = t
      elseif t.__hudStep ~= s then
        -- setting .text rebuilds the glyph sprites, so only when it actually changed
        pcall(function() t.text = s end)
      end
      t.__hudStep = s
      -- a decided perfect is the one thing worth shouting about, so that line goes gold
      local col = (job.to == 21) and GOLD or WHITE
      pcall(function() t:setFillColor(col[1], col[2], col[3]) end)
    end
    for i = #jobs + 1, #f.texts do
      pcall(function() f.texts[i]:removeSelf() end)
      f.texts[i] = nil
    end
    place(f.group, #jobs)
    keepOnTop(f.group)
  end

  local function update()
    if not f.on then return end
    f.ticks = f.ticks + 1
    local jobs = stepJobs(save())
    if #jobs == 0 then
      -- nothing pending: take it down rather than leave a stale countdown on screen
      if f.group then clear() end
      return
    end
    render(jobs)
  end

  f.update, f.kill, f.on = update, clear, true
end
""".replace("__CORNER__", _lua_str(corner))
        .replace("__MX__", str(int(cfg["margin_x"])))
        .replace("__MY__", str(int(cfg["margin_y"])))
        .replace("__SPACING__", str(int(spacing)))
        .replace("__READY__", _lua_str(cfg["ready_text"]))
        .replace("__RESCAN__", str(RESCAN_TICKS))
    )


def _lines_expr(cfg):
    """The Lua expression for the current countdown, as one comma-separated line."""
    return (
        r"""(function()
  local jobs = stepJobs()
  local bits = {}
  for i = 1, #jobs do bits[#bits + 1] = stepLine(jobs[i], __READY__) end
  return table.concat(bits, ', ')
end)()""".replace("__READY__", _lua_str(cfg["ready_text"]))
    )


def summary(cfg):
    return (
        r"""(function()
  local s = __LINES__
  if s == '' then return 'step countdown (idle - nothing at the Potentiflator or Traitformator)' end
  return 'step countdown (' .. s .. ')'
end)()""".replace("__LINES__", _lines_expr(cfg))
    )


def status(cfg):
    return (
        r"""(function()
  local f = _G.__hud and _G.__hud.feats.steptimer
  if not f or not f.on then return nil end
  if (f.ticks or 0) == 0 then return 'step countdown: not polled yet' end
  local s = __LINES__
  if s == '' then return 'step countdown: idle' end
  return 'step countdown: ' .. s
end)()""".replace("__LINES__", _lines_expr(cfg))
    )


def report(cfg):
    return r"""(function()
  local out = {}
  local steps = stepsWalked()
  out[#out + 1] = 'steps walked: ' .. (steps and tostring(steps) or 'unavailable')
  local jobs = stepJobs()
  if _G.__hudStepsSkipped and _G.__hudStepsSkipped > 0 then
    out[#out + 1] = string.format(
      '%d entr%s in the saveable list had no Coromon in hidden storage, so %s not shown:',
      _G.__hudStepsSkipped, (_G.__hudStepsSkipped == 1) and 'y' or 'ies',
      (_G.__hudStepsSkipped == 1) and 'it is' or 'they are')
    out[#out + 1] = '  that is the leftover the game keeps after a Coromon is collected. It is in the'
    out[#out + 1] = '  SAVED data, which is why a stale countdown came back over an empty machine'
    out[#out + 1] = '  after a quit to the menu and a reload - the steps number was real, but there'
    out[#out + 1] = '  was nothing left to collect.'
  end
  if #jobs == 0 then
    out[#out + 1] = 'nothing pending at the Potentiflator or Traitformator'
    return table.concat(out, '\n')
  end
  for i = 1, #jobs do
    out[#out + 1] = string.format('  %-16s %6s left   (completes at step %s)',
      jobs[i].name, tostring(jobs[i].left), tostring(jobs[i].target))
  end
  return table.concat(out, '\n')
end)()"""
