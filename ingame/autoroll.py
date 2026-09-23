"""autoroll - reroll the Potentiflator until it lands on a perfect, from inside the game.

WHY THE LOOP LIVES IN HERE AND NOT IN THE SCRIPT. Deciding and reloading are both in-game work:
the decided result has to be read out of the save table, and the reload is a call into another
feature. A driver outside the process can only reach either of them by evaluating a chunk, and
doing that from a separate script means a second Frida session, a 12 KB chunk recompiled on every
poll, and - the reason it is written this way now - no protection at all against two copies of the
driver running and firing two reloads at the same handover. As a feature it is installed once,
torn down once, and the host already guarantees there is only ever one of it: a re-install calls
the previous one's `kill()` first.

So the split is:

    this feature      notices the decided result and fires the reload, in the composited chunk,
                      on the host's own tick - but only while a driver has it ARMED
    autoroll.py       arms it, presses Space, and reports what it is doing

NOTHING HAPPENS UNTIL THE DRIVER ARMS IT (`f.armed`, false at install and tested at the top of
`update()`). The user, having found the opposite: "the idea was that the autoroll.py would be the
one that triggers reloading if a wrong value roll is detected - the issue is that the overlays
script does this even if autoroll.py is not used". Which it did, because the install was what armed
it: merely having the overlay attached reloaded the save on every handover that decided anything
but the target. Deciding and reloading really are in-game work, but they are only worth doing for a
run somebody is driving - the confirm press cannot come from in here, so without a driver the loop
could only rearrange a save nobody was rolling. `autoroll.py` arms it on the way in and disarms it
on the way out, so quitting the driver stops the reloading there and then.

`autoroll.py` drives this; it does not duplicate it. Turning this feature off (the `runtime`
checkbox in the GUI, or `[autoroll] enabled = false`) stops the loop dead, and switching it back
on resets it with the counters at zero - which is also how to reset after a hit, since `ready`
is terminal on purpose.

AFTER A HIT IT WALKS. The Coromon is not collectable until its 1000 steps are walked, so on a hit
the loop switches to holding a direction and turning round at the end of the corridor.

It ASKS THE GAME whether the tile ahead is solid - `findSpawnablesAndObjectProperties`, which
combines the map's own tile properties with the spawnables standing on that square - and turns
BEFORE committing to the move. That timing is the point: movement is grid-based, so a committed
step has to finish, and reacting to having stopped always cost a whole tile. The query is evaluated
once per tile change, which is the only moment its answer can change.

There is no second mechanism underneath it. An earlier version noticed that the tile had stopped
changing and turned round after a moment; it is gone, because the query is the game's own answer and
the fallback only made the real behaviour harder to read. If the query is ever unreachable the walk
stops turning instead of pretending - `blocked=nil` in the driver's line says so.

It stops on the game's own remaining-steps countdown reaching zero, not on a step total counted here.
"""

NAME = "autoroll"

# ===========================================================================================
# THE NUMBERS. All of them, here, so nothing about the timing is hidden in the body below.
# ===========================================================================================
# The potential to stop on. 21 is a perfect Coromon.
TARGET = 21
# How often the loop looks. The host's shared tick is 200 ms and a feature's `period` is rounded up
# to it, so this is 200 ms in practice - it sets how late a decided result is noticed, and nothing
# else. The save-table search behind it is throttled separately by `liveSaveSettings()`, so this
# cannot make the walk happen more often.
# 0 MEANS NO SLOT: the host gives a period-0 feature no place on its shared 200 ms tick, and the
# feature runs itself from its own enterFrame listener instead. That is what this needs, because the
# roll decision is a reaction to a handover: on the shared tick there would be up to 200 ms between
# the game deciding a potential and the reload being fired, and the driver presses Space every 50 ms
# throughout it - every one of those presses is a press past the handover.
#
# Nothing about the cost changes with this, and that is deliberate: liveSaveSettings() is throttled
# in MILLISECONDS, so calling it per frame searches package.loaded at exactly the rate it did on the
# slot. A call-counted throttle here would have made the search twelve times more frequent.
POLL_MS = 0
# After a hit, the Coromon still needs its 1000 steps walked before it can be collected, so the loop
# walks it: hold a direction, and turn round when the game says the tile AHEAD is solid.
WALK_AFTER_HIT = True
# Which way the walk pushes first: +1 is right, -1 is left. It alternates from here.
#
# LEFT, because of WHERE THE WALK STARTS: on the rightmost walkable tile, since that is where the
# Coromon was handed over. So pushing right from there is pushing into the wall - the Coromon stands
# still while the driver is told to turn round - and starting left means the walk moves on its first
# frame and its first turn is at the far end of the corridor.
START_DIRECTION = -1

SETTINGS = [
    (
        "enabled",
        True,
        "reroll the Potentiflator automatically: fire the reload whenever a handover decides a "
        "potential other than TARGET. It is SAFE to leave on, but not for the reason this comment "
        "used to give: the loop does NOTHING until autoroll.py ARMS it (`f.armed`, false at "
        "install), because a reload is only worth firing for a run something outside is driving - "
        "the confirm press cannot come from in here. autoroll.py arms it on the way in and disarms "
        "it on the way out, so the loop stops when the driver does. It needs the reload feature "
        "installed: with that off the loop stops and reports why rather than retrying, and it "
        "stops for good once it hits the target.",
    ),
]


def lua(cfg):
    """The readout. Always part of every composed chunk, so the installer's summary and the status
    report can both say what the loop is doing without knowing anything about it."""
    return r"""
local function autorollState()
  local f = _G.__hud and _G.__hud.feats and _G.__hud.feats.autoroll
  if type(f) ~= 'table' then return 'not installed' end
  if not f.on then return 'switched off' end
  -- ARMED IS THE DIFFERENCE between the loop being installed and the loop being allowed to reload:
  -- only autoroll.py arms it, because only autoroll.py can press the confirm key. Saying so here is
  -- what keeps "it did nothing" from looking like "it is broken".
  if not f.armed then return 'idle - run autoroll.py to start it' end
  -- THE AIMED POTENTIAL is on the table rather than in the loop's closure so the driver can set it
  -- from outside (`python autoroll.py 20`) without re-installing anything; the fallback is the value
  -- the feature was installed with, for an older copy that has no `target`.
  local s = string.format('rolls=%d %s aiming for P%s', f.rolls or 0, tostring(f.state),
    tostring(f.target or __TARGET__))
  if f.hit then s = s .. ' HIT ' .. tostring(f.hit) end
  if f.why then s = s .. ' (' .. tostring(f.why) .. ')' end
  return s
end
""".replace("__TARGET__", str(TARGET))


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return (
        r"""
do
  local f = makeFeature('autoroll', __POLL__)

  -- EVERYTHING THE LOOP REMEMBERS, IN ONE PLACE, because it is now reset from two directions: at
  -- install, and on demand from outside when the driver takes the loop over. Two copies of this
  -- list would drift, and a field missed by one of them is a stale value read as a live one - which
  -- is exactly what let a restarted driver sit on 'state=ready last=P19 to P21 left=0' for a
  -- Coromon the player had already collected, pressing nothing at all.
  --
  -- 'starting', NOT 'waiting', and that is the load-bearing part of the reset. The driver presses
  -- only while the loop is 'waiting', so 'starting' is what makes the loop LOOK BEFORE ANYTHING IS
  -- PRESSED: a Coromon that is already rolled to the target is found on the first tick and the
  -- state goes straight to 'walking', with no press ever sent. The alternative is a real hazard
  -- rather than a tidiness point - the driver presses every 0.05 s and this only looks every
  -- POLL_MS, so a first press could land on the collect prompt and take a perfect roll away before
  -- anything had read what it was.
  --
  -- `f.on` is deliberately NOT touched: re-arming is not switching the loop on or off, and being
  -- switched off is a separate thing the caller checks for itself.
  local function rearm()
    f.rolls, f.state = 0, 'starting'
    f.last, f.hit, f.why = nil, nil, nil
    -- the walk: which way the driver is being asked to hold, the tile last seen, and whether the
    -- tile ahead is solid (nil only while the query is unreachable)
    f.walk, f.dir, f.left = nil, nil, nil
    f.blocked, f.tx, f.ty = nil, nil, nil
  end
  -- Exposed so the driver can ask for a fresh start without knowing what one consists of. The
  -- install path below runs this same function, so the two cannot disagree about what is cleared.
  f.rearm = rearm
  rearm()
  f.on = false

  -- ARMED OR NOT: whether a DRIVER is out there to press the confirm key. FALSE AT INSTALL, and that
  -- is load-bearing rather than tidy - it is the whole difference between this feature and one that
  -- reloads the save of anybody who merely has the overlay attached (see the module docstring).
  -- The driver asks for both halves in one call, so there is no way to arrive at an armed loop with
  -- a stale `ready` still in it:
  local function arm()
    f.armed = true
    rearm()      -- a driver arriving IS a fresh run, and `ready` is terminal on purpose
  end
  local function disarm()
    f.armed = false
    -- stop asking for a direction at once: the driver releases its keys on the way out, and a word
    -- left in `walk` would have the next reader believe a key is still held
    f.walk, f.state = nil, 'idle'
  end
  f.arm, f.disarm, f.armed = arm, disarm, false

  local TARGET = __TARGET__

  -- THE AIMED POTENTIAL, and the one number that may be changed from OUTSIDE at run time: the driver
  -- takes an optional argument (`python autoroll.py 20`) and calls setTarget() with it before arming.
  -- It lives on the feature table rather than in this closure so the driver can reach it without
  -- re-installing anything - and it survives the loop's own reloads, which matter because the reload
  -- is what the loop does between rolls. Every reader goes through aim(), so one place decides.
  f.target = TARGET
  local function aim()
    local t = tonumber(f.target)
    if t == nil then t = TARGET end
    return t
  end
  f.aim = aim
  -- REFUSES ANYTHING THE GAME COULD NEVER DECIDE, rather than arming a loop that would reload for
  -- ever: 1..21 is the game's whole potential range. Returns the value it took, or nil plus why - so
  -- the driver can say which of the two happened instead of silently aiming at something impossible.
  f.setTarget = function(n)
    local t = tonumber(n)
    if t == nil then return nil, 'not a number' end
    t = math.floor(t)
    if t < 1 or t > 21 then return nil, 'outside 1..21' end
    f.target = t
    return t
  end

  local WALK_AFTER_HIT = __WALK__
  local DIR = __DIR__

  -- The parked deposit, if the game has one: the effect and its monster. `getMonster` is a pure
  -- lookup of the identifier the deposit stored, and it answers nil once the Coromon has been
  -- collected - so "still parked" is cheap to test and depends on no class name.
  local function parked()
    local settings = liveSaveSettings()
    if type(settings) ~= 'table' then return nil end
    for _, e in ipairs(settings.SAVEABLE_BATTLE_EFFECTS or {}) do
      if type(e) == 'table' and type(e.getTargetPlayerSteps) == 'function' then
        local okM, mon = pcall(function() return e:getMonster() end)
        if okM and type(mon) == 'table' then return e, mon end
      end
    end
  end

  local function decided()
    local e = parked()
    if not e then return nil end
    return decidedPotentialOf(e)
  end

  -- Steps still owed: the game's own countdown, so the walk stops at the moment the Coromon becomes
  -- collectable rather than at a step total guessed here.
  local function stepsLeft(e)
    local okT, target = pcall(function() return e:getTargetPlayerSteps() end)
    target = okT and tonumber(target) or nil
    local okS, walked = pcall(function() return playerStats.getSteps() end)
    walked = okS and tonumber(walked) or nil
    if not target or not walked then return nil end
    local left = target - walked
    return left < 0 and 0 or left
  end

  -- The player object AND its tile. The object is needed as well as the tile: the game's own
  -- "is this square solid" query is scoped to the player's object LEVEL and takes the player's
  -- ignoredObjects, so it cannot be asked with coordinates alone.
  local function playerObj()
    local ok, i = pcall(function() return spawnableHelper:getPlayerSpawnable() end)
    if ok and type(i) == 'table' then return i end
  end

  local function playerTile()
    local p = playerObj()
    if not p then return nil end
    local tx, ty = p.currentTileX, p.currentTileY
    if type(tx) == 'number' then return tx, ty end
  end

  -- IS THE TILE AT tx,ty SOLID? The game's own answer, and the reason for the whole look-ahead:
  -- ask BEFORE stepping onto the boundary rather than reacting to having stopped against it.
  --
  -- `findSpawnablesAndObjectProperties` is what decides it. It returns
  --     { blocking = <bool>, spawnables = { ... } }
  -- combining the tile's OBJECT PROPERTIES (what the map says) with the spawnables standing on it
  -- (what the objects say) - one call, both halves of the rule, and no rule of ours to get wrong.
  --
  -- It is NOT exported: it is upvalue 1 of findSpawnablesInDirectionUntilBlocking, which is the same
  -- route core.py takes to tiledWorld. Fetched once and kept; a failed lookup is remembered as
  -- `false` so it is not retried on every tile change.
  local solidAtTile
  local function solidQuery()
    if solidAtTile ~= nil then return solidAtTile or nil end
    solidAtTile = false
    local hunt = spawnableHelper and spawnableHelper.findSpawnablesInDirectionUntilBlocking
    if type(hunt) ~= 'function' or type(debug) ~= 'table'
       or type(debug.getupvalue) ~= 'function' then
      return nil
    end
    -- A BARE MULTI-ASSIGNMENT, and that is the whole point of this line. Written as
    --     local ok, fn = pcall(function() return debug.getupvalue(hunt, 1) end)
    -- this silently never worked: pcall returns (true, NAME, VALUE), so capturing two locals takes
    -- `true` and the upvalue's NAME - fn is a string, the type check fails, and the query is
    -- declared unreachable while looking for all the world like it was tried. debug.getupvalue does
    -- not throw for a function, so the pcall was not buying anything either.
    local _, fn = debug.getupvalue(hunt, 1)
    if type(fn) == 'function' then solidAtTile = fn end
    return solidAtTile or nil
  end

  local function tileBlocked(tx, ty, p)
    local fn = solidQuery()
    if not fn or not p then return nil end
    local ok, res = pcall(fn, {
      tileX = tx, tileY = ty, widthInTiles = 1, heightInTiles = 1,
      level = p.currentObjectLevel, ignoredObjects = p.ignoredObjects,
    })
    if not ok or type(res) ~= 'table' then return nil end
    return res.blocking and true or false
  end

  -- THE WALK RUNS PER FRAME AND THE REST STAYS ON THE SLOT, and the split is deliberate.
  --
  -- The direction decision has to be per frame because it is a reaction - on the host's shared tick
  -- the fastest possible reaction is 200 ms, and a feature's period is rounded up to one tick, so
  -- POLL_MS = 10 runs exactly as often as POLL_MS = 200 does.
  --
  -- The expensive half - finding the deposit and reading the step count - stays on the slot, because
  -- liveSaveSettings counts CALLS rather than time: driving that per frame would make it search
  -- package.loaded about forty times a second, which is the very mistake that cost cooldowns and
  -- steptimer a frame rate each.
  local function walkFrame()
    if not f.on or f.state ~= 'walking' then return end
    local p = playerObj()
    local tx, ty = playerTile()
    if tx == nil then
      -- no player readable (mid map load, or a menu is up): hold still rather than walk blind
      f.walk = nil
      return
    end
    if f.tx ~= tx or f.ty ~= ty then
      -- THE TILE CHANGED, and that is the only moment the answer can change - so it is the only
      -- moment the game is asked. Per frame this would be a spawnable search every frame for an
      -- answer that only moves when the player does.
      f.tx, f.ty = tx, ty
      f.blocked = tileBlocked(tx + (((f.dir or DIR) > 0) and 1 or -1), ty, p)
      if f.blocked then
        -- Turn on the tile BEFORE the boundary, so the move that would have been blocked is never
        -- committed. That timing is the whole point: movement is grid-based, so a committed step has
        -- to finish, and reacting to having stopped always cost a whole tile.
        f.dir = -(f.dir or DIR)
      end
    end
    -- f.blocked is nil ONLY when the query is unreachable, and there is deliberately no fallback:
    -- the query is the game's own answer, and a second, worse mechanism underneath it only made the
    -- real behaviour harder to read. If it ever cannot be reached the walk stops turning rather than
    -- pretending - which shows up as blocked=nil in the driver's line, not as a wall.
    f.walk = ((f.dir or DIR) > 0) and 'right' or 'left'
  end

  local function walkStep()
    local e = parked()
    if not e then
      -- the deposit went away: collected, or a reload took it. Either way there is nothing to walk
      f.walk, f.state = nil, 'waiting'
      return
    end
    local left = stepsLeft(e)
    f.left = left
    if left and left <= 0 then
      f.walk, f.state = nil, 'ready'
      return
    end
    f.state = 'walking'
    walkFrame()
  end

  local function fireReload()
    local h = _G.__hud
    local r = h and h.feats and h.feats.reload
    if type(r) ~= 'table' or type(r.fire) ~= 'function' then
      return nil, 'the reload feature is not installed'
    end
    if not r.on then return nil, 'the reload feature is switched off' end
    -- `fire()` sets its own `busy` before it does anything, so this cannot double-fire: the next
    -- tick sees busy, and by the time busy clears the deposit is gone.
    if r.busy then return 'busy' end
    local ok, err = pcall(r.fire)
    if not ok then return nil, 'the reload threw: ' .. tostring(err) end
    return 'fired'
  end

  local function update()
    if not f.on then return end
    -- NOT ARMED: nothing outside is pressing the confirm key, so a reload fired now would be a
    -- reload nobody asked for - see `arm` above and the module docstring. The state says so, and
    -- the readouts print it, because a loop that silently does nothing reads as a broken one.
    if not f.armed then
      f.state, f.why = 'idle', 'run autoroll.py to start the loop'
      return
    end
    if f.state == 'ready' or f.state == 'stuck' then return end
    if f.state == 'walking' then
      walkStep()
      return
    end
    local from, to = decided()
    if not to then
      -- nothing parked with a decided result: either waiting for a handover, or the reload has
      -- just taken the deposit away - which is the moment the previous roll ended
      f.state = 'waiting'
      return
    end
    f.last = string.format('P%s to P%s', tostring(from), tostring(to))
    if tonumber(to) == aim() then
      -- HIT. Take the roll, then walk its steps so it can actually be collected.
      f.hit = f.last
      f.state = WALK_AFTER_HIT and 'walking' or 'ready'
      -- And the walk is entered HERE, in this frame, through the same transition everything else
      -- goes through - rather than leaving it to the next frame's tick. The driver holds a
      -- direction only while the state says walking, so a frame that says walking with `walk`
      -- still nil is a frame the Coromon stands still at the very start of the walk, which is the
      -- one place it is in plain sight. walkStep() sets both halves: the state and the direction.
      if f.state == 'walking' then walkStep() end
      return
    end
    local said, why = fireReload()
    if said == 'fired' then
      f.rolls = f.rolls + 1
      f.state = 'reloading'
    elseif said == 'busy' then
      f.state = 'reloading'
    else
      -- Stuck is terminal, not a retry: whatever is wrong will still be wrong next tick, and a
      -- loop that keeps calling a failing reload is worse than one that stops and says so.
      f.state, f.why = 'stuck', why
    end
  end

  f.update, f.on = update, true
  -- TWO LISTENERS OF ITS OWN, both removed in kill(). The host tears a feature down by calling
  -- kill(), and anything left registered would carry on running after it was switched off - a
  -- direction left held, or a reload fired, for a feature the user had turned off.
  Runtime:addEventListener('enterFrame', walkFrame)
  Runtime:addEventListener('enterFrame', update)
  f.kill = function()
    f.on = false
    pcall(function() Runtime:removeEventListener('enterFrame', walkFrame) end)
    pcall(function() Runtime:removeEventListener('enterFrame', update) end)
  end
end
""".replace("__POLL__", str(POLL_MS))
        .replace("__TARGET__", str(TARGET))
        .replace("__WALK__", "true" if WALK_AFTER_HIT else "false")
        .replace("__DIR__", str(START_DIRECTION))
    )


def summary(cfg):
    return ("'autoroll: rerolls a handover deciding anything but P%d, while autoroll.py drives it"
            " (autoroll.py N aims at another potential)'" % TARGET)


def status(cfg):
    return "autorollState()"


def report(cfg):
    # Required, not optional: core composes one report line per feature and calls this
    # unconditionally, so a feature without it takes the whole report chunk down.
    return "autorollState()"
