#!/usr/bin/env python3
"""
gold.py - make the game's own gold counter show the real total, grouped for reading.

The counter stops at 9,999,999 no matter how much you have. That number comes from
`playerCurrency`, which keeps the balance in a module upvalue and offers two getters:

    getGold()             the real balance, uncapped
    getRestrictedGold()   the same value through math.min(9999999, ...)

and the game's display reads the second one. So the balance really does keep growing - it
just cannot be seen. Measured: `getGold()` 17,597,722 against `getRestrictedGold()`
9,999,999. Buying something still subtracts from the real balance, which is why a purchase
seemed to cost nothing: the counter was pinned at the cap and had no room to move down.

THERE ARE TWO CLAMPS, NOT ONE, and the second is why this feature does more than wrap a
getter. Unclamping `getRestrictedGold` is not enough to move the visible counter, because
each of the two top-bar labels clamps AGAIN where it draws:

    goldText.text = string.format('%07d', math.min(9999999.0, math.round(value)))

Two things made that worth working around rather than giving up on:

  * `getRestrictedGold` is reached by exactly five modules - the two top-bar labels, the
    dialog answer buttons, the Rogue interface, and its own definition. Nothing that
    decides whether you can AFFORD something reads it: the shops, the items and
    `spendGold` all go through `getGold()`. It is a display function, so replacing it
    re-labels the game without touching the balance or any purchase check. The dialogs and
    the Rogue readout have no clamp of their own, so the getter is enough for those; only
    the two labels need the second step.
  * The label's text is reachable. It is the first upvalue of the label's animation
    function, so it can be addressed as `debug.getupvalue(inst.doCurrencyAnimation, 1)` and
    given any string - which is also what makes the GROUPING possible, since the game's own
    `%07d` cannot produce separators.

GROUPING. The separator is a plain space (17 597 722). It was picked over the apostrophe
(17'597'722, the Swiss convention) after both were put on screen and seen - which is the only
test that settles this. It costs a little width: the font is proportional and a space advances
about as much as a digit, so the groups sit a full character apart. Measured, the whole string
is 34 units plain, 38 with a space and 38 with an apostrophe - so the total is not the
difference anyone notices; it is that a space puts no ink in the gap.

Whatever goes here must be a character the font actually HAS, which is worth stating because
getting it wrong fails loudly. U+00A0 was tried first and does not render: the text object
stores something other than what was written, and the per-frame pass below then rewrites the
label on every frame and the counter blinks. "" turns grouping off. The names below let
overlays.toml say "space" rather than carry a bare space, and anything that is not a name is
used literally.

AND THE GAP AFTER THE COUNTER, which is the thing that actually felt wrong. The label is
LEFT-aligned at a fixed x and grows rightward, so a wider number eats the space that follows
it. Measured, x is -13 whether the text is 7, 8 or 10 characters long, while the width goes
34 (plain, and also what the game itself draws) to 38 (grouped): the game's layout was built
for a 7-digit counter, so grouping takes 4 units out of the gap after it.

WHERE THE EXTRA ROOM COMES FROM, since the number can have it from neither neighbour. Both of
the obvious single moves fail: shifting the label left CLIPS INTO THE COIN ICON (measured, the
icon does not move when the text changes - x=-19.5, width 9, in the plain, grouped and capped
states alike - and the label's own x=-13 already sits inside the icon's box, which ends at
-10.5), and taking it out of the gap on the right is the thing that felt wrong to begin with.

The fix is to move BOTH, which is what `keep_right_edge` does: the counter keeps the gap the
game left after it, and the coin steps aside with it into the empty space to the left of the
bar. Neither gap changes size - the pair simply sits 4 units further from the middle. Moving
the icon needs to find it, so with no coin in sight the label is left alone rather than
clipped.

The POSITION is applied on every frame, separately from the text, and that separation is the
point. The game's currency animation rewrites the text on every frame of its second, so
writing text during it is both pointless and something the user could see; but the animation
moves nothing, so the position is safe to set at once. Doing them together meant a label built
while an animation ran - opening the pause menu, which animates the number in - sat unshifted
until the animation ended. Now the shift lands in the first frame the label exists.

The catch is that the game's animation does not only write that text, it reads it back:

    transition.number(tonumber(goldText.text), playerCurrency:getRestrictedGold(), ...)

and `tonumber("17 597 722")` is **nil** - Lua 5.1 requires the whole string to parse.
Measured: `transition.number(nil, x, ...)` does not raise, it animates from **0**, so an
unguarded grouped counter would visibly spin up from zero on every purchase. The animation
is therefore wrapped: plain digits go in for the second it runs, and the grouping returns
once it has finished.

WHY THE CORRECTION RUNS EVERY FRAME. Correcting only on the poll (400 ms) left a visible
gap wherever a label is built fresh: `createInstance` draws the number itself, so the pause
menu - which builds its own top bar - showed the ungrouped figure first and the grouped one
a moment later. The per-frame pass costs a few pcalls and two string comparisons in the
steady state, and it means a label is corrected in the same frame it appears, and the
grouping is back in the same frame the game's animation ends rather than up to a poll later.
The poll is only there to refresh the wanted string and the wrappers.

Everything here is drawing only. Nothing raises the balance, nothing is written to the
save, and enabled = false puts all three layers back exactly as they were.
"""

NAME = "gold"

SETTINGS = [
    (
        "enabled",
        True,
        (
            "show the real gold total instead of the game's 9,999,999 cap, in the top bar, "
            "the dialogs and the Rogue readout"
        ),
    ),
    (
        "fix_counter",
        True,
        (
            "also correct the two top-bar labels, which clamp a second time where they "
            "draw and so ignore the uncapped getter. Turn this off to leave the game's "
            "counter untouched and only fix the dialogs and the Rogue readout"
        ),
    ),
    (
        "separator",
        "space",
        (
            'thousands separator for the counter. "space" (the default, 17 597 722) won on '
            'sight over "apostrophe" (17\'597\'722). Also: "period", "comma", "none". '
            'Anything else is used literally - but it must be a character the font actually '
            'has: U+00A0 renders as an error glyph and makes the counter blink, because the '
            'text object stores something other than what was written. The grouping has to '
            'be drawn by us - the game formats its counter as %07d'
        ),
    ),
    (
        "keep_right_edge",
        True,
        (
            "hold the counter's gap after it at the size the game's layout intended, by "
            "moving the number AND the coin icon beside it left by however much the "
            "grouping widened the number. Moving only the number would clip into the coin, "
            "so both move and neither gap changes size. Off leaves both where the game put "
            "them and lets the gap after the number shrink"
        ),
    ),
]

# Named separators, so overlays.toml can say "space" rather than carry one that is easy to
# lose in a text editor. "" is deliberately NOT a name: a setting of "" or " " falls through
# to the literal, which is what makes "" mean the same thing there as it does in Lua.
NAMED_SEPARATORS = {
    "none": "",
    "off": "",
    "space": " ",
    "apostrophe": "'",
    "quote": "'",
    "tick": "'",
    "period": ".",
    "dot": ".",
    "comma": ",",
}

# The cap, in one place: it is in playerCurrency's getter and again in each top-bar label.
CAP = 9999999

# How long the game's own currency animation runs, plus a margin. It is a 1000 ms tween and
# it rewrites the label on every frame of it, so the grouped text stays out of the way until
# it is over.
ANIM_MS = 1200


def _lua_str(value):
    """A Lua string literal for text coming out of overlays.toml.

    Non-ASCII is emitted as Lua decimal escapes rather than as raw bytes, so a separator like
    U+00A0 survives whatever encoding the chunk is carried in.
    """
    out = []
    for byte in str(value).encode("utf-8"):
        char = chr(byte)
        if char == "\\":
            out.append("\\\\")
        elif char == "'":
            out.append("\\'")
        elif 32 <= byte < 127:
            out.append(char)
        else:
            out.append("\\%d" % byte)
    return "'" + "".join(out) + "'"


def _separator(cfg):
    """The separator setting, by name or taken literally."""
    raw = str(cfg["separator"])
    return NAMED_SEPARATORS.get(raw.strip().lower(), raw)


def lua(cfg):
    """The queries, and the helpers the section and the report both use.

    Always part of the chunk, so --status and --report can describe the counter whether or
    not the feature is installed."""
    return r"""
-- From overlays.toml, by name or taken literally. A space by default: it was picked over an
-- apostrophe on sight. Whatever is used here must be a character the font actually has - an
-- unrenderable one is not merely ugly, it makes the counter blink (see goldFix below).
local GOLD_SEP = __SEP__

-- The currency module. A plain table on _G, so it is there as soon as a save is open - but
-- a timer re-checks it anyway, because assuming a table is always there is how a feature
-- quietly stops working after the game rebuilds something.
local function goldModule()
  local t = _G.playerCurrency
  if type(t) == 'table' and type(t.getGold) == 'function' then return t end
end

-- The true total. getGold() and getRestrictedGold() are the same function apart from the
-- cap: both hand back the Battle Dome tokens while a dome session is running and the
-- player's gold otherwise. Only the second wraps its answer in math.min.
--
-- Every call here goes through pcall(fn, arg) rather than pcall(function() ... end): the
-- per-frame pass makes these hot, and a closure each time would be pure garbage.
local function goldReal()
  local t = goldModule()
  if not t then return nil end
  local ok, v = pcall(t.getGold, t)
  if ok and type(v) == 'number' then return v end
end

-- What the game's counter would read with the cap left in. Reads the SAVED original, so it
-- keeps meaning "capped" after the wrapper has replaced the getter.
local function goldCapped()
  local t = goldModule()
  if not t then return nil end
  local fn = type(t.__hudGoldOrig) == 'function' and t.__hudGoldOrig or t.getRestrictedGold
  if type(fn) ~= 'function' then return nil end
  local ok, v = pcall(fn, t)
  if ok and type(v) == 'number' then return v end
end

local function goldWrapped()
  local t = goldModule()
  return (t ~= nil) and (type(t.__hudGoldFn) == 'function')
     and (t.getRestrictedGold == t.__hudGoldFn)
end

-- "17597722" - what the game itself would put on screen, minus its %07d padding.
local function goldDigits(n)
  if type(n) ~= 'number' then return nil end
  return string.format('%d', math.floor(n))
end

-- "17 597 722". Grouped from the right, so a number whose digit count is a multiple of
-- three comes out with a separator in front that has to be trimmed. Checked by hand against
-- 1, 12, 123, 1234, 12345, 123456, 1234567 and 123456789 rather than trusted.
local function goldSpaced(n)
  local digits = goldDigits(n)
  if not digits then return nil end
  if GOLD_SEP == '' then return digits end
  local reversed = digits:reverse():gsub('(%d%d%d)', '%1' .. GOLD_SEP)
  local spaced = reversed:reverse()
  if spaced:sub(1, #GOLD_SEP) == GOLD_SEP then spaced = spaced:sub(#GOLD_SEP + 1) end
  return spaced
end

-- The same thing with commas, for our own readouts in --status and the report.
local function goldGroup(n)
  local digits = goldDigits(n)
  if not digits then return '?' end
  local reversed = digits:reverse():gsub('(%d%d%d)', '%1,')
  local grouped = reversed:reverse()
  if grouped:sub(1, 1) == ',' then grouped = grouped:sub(2) end
  return grouped
end

-- The top bar's instance, or nothing when the bar is not on screen. Split out from
-- goldLabel because taking the animation wrapper off again (in kill) needs the instance even
-- when the label's text cannot be resolved.
local function goldInstance(module)
  if type(module) ~= 'table' then return end
  local isCreated, getInstance = module.isCreated, module.getInstance
  if type(isCreated) ~= 'function' or type(getInstance) ~= 'function' then return end
  local ok, created = pcall(isCreated, module)
  if not ok or not created then return end                -- the top bar is not on screen
  local ok2, inst = pcall(getInstance, module)
  if not ok2 or type(inst) ~= 'table' then return end
  return inst
end

-- The top bar's gold label. Its text is the first upvalue of the animation function, which
-- is the only way to reach it - and it has to be read off the SAVED original, because once
-- the animation has been wrapped the live field is our function, whose upvalues are its own.
--
-- Lives here rather than in the section because the report needs it as well.
local function goldLabel(module)
  local inst = goldInstance(module)
  if not inst then return end
  local anim = inst.__hudGoldAnimOrig or inst.doCurrencyAnimation
  if type(anim) ~= 'function' then return end
  local ok, name, node = pcall(debug.getupvalue, anim, 1)
  if not ok or type(node) ~= 'table' then return end
  return inst, node
end

-- The coin icon sitting immediately left of the counter, so it can step aside with the
-- number instead of the number being clipped by it. Found by shape: it is the narrowest
-- positioned sibling of the label, and narrower than the number - the label itself is a group
-- of glyph sprites and the nav-bar container beside it is 48 wide, so width is what tells them
-- apart. Nothing here is called; the object is only ever positioned.
local function goldCoin(node)
  local parent = node.parent
  if type(parent) ~= 'table' then return end
  local best
  for i = 1, (parent.numChildren or 0) do
    local c = parent[i]
    if type(c) == 'table' and c ~= node then
      local ok, x = pcall(function() return c.x end)
      local ok2, w = pcall(function() return c.width end)
      if ok and ok2 and type(x) == 'number' and type(w) == 'number'
         and type(node.width) == 'number' and w < node.width then
        if not best or w < best.width then best = { node = c, width = w } end
      end
    end
  end
  return best and best.node
end
""".replace("__SEP__", _lua_str(_separator(cfg)))


def section(cfg):
    if not cfg["enabled"]:
        return ""
    return (
        r"""
do
  local f = makeFeature('gold', 200)
  local CAP = __CAP__
  local ANIM_MS = __ANIM__
  local FIX = __FIX__          -- also correct the two top-bar labels?
  local ALIGN = __ALIGN__      -- hold the number's right edge where the game put it?
  f.fixes = 0                  -- label rewrites made since install
  f.frames = 0                 -- per-frame passes that found the hook live

  -- Layer one: the getter. Replacing it re-labels the dialogs and the Rogue readout, and
  -- makes the top bar's own animation aim at the real figure rather than at the cap.
  local function wrap()
    local t = _G.playerCurrency
    if type(t) ~= 'table' or type(t.getGold) ~= 'function' then return end
    if t.getRestrictedGold == t.__hudGoldFn then return end        -- ours is still there
    if type(t.__hudGoldOrig) ~= 'function' then t.__hudGoldOrig = t.getRestrictedGold end
    -- pcall(t.getGold, ...) and NOT a closure calling t:getGold(): in Lua 5.1 `...` is not
    -- visible inside a nested function that is not itself vararg, and the whole chunk then
    -- fails to compile - silently, at install time, leaving the old version running.
    t.__hudGoldFn = function(...)
      local ok, v = pcall(t.getGold, ...)
      if ok then return v end
      if type(t.__hudGoldOrig) == 'function' then return t.__hudGoldOrig(...) end
    end
    t.getRestrictedGold = t.__hudGoldFn
  end

  local function unwrap()
    local t = _G.playerCurrency
    if type(t) ~= 'table' then return end
    if t.getRestrictedGold == t.__hudGoldFn then t.getRestrictedGold = t.__hudGoldOrig end
    t.__hudGoldFn, t.__hudGoldOrig = nil, nil
  end

  -- What the counter should say. nil means "leave the game's own text alone": either there
  -- is no save open, or grouping is off and the figure is under the cap, where the game's
  -- own padding is already the right answer.
  local function wanted()
    local real = goldReal()
    if real == nil then return nil end
    if GOLD_SEP == '' and real <= CAP then return nil end
    return goldSpaced(real)
  end

  -- Layer two: the label's own clamp, which no getter can reach. goldLabel() (in the shared
  -- helpers, so the report can use it too) finds the label object; this is what is done with
  -- it.
  local function setText(node, want)
    if type(node) ~= 'table' or type(want) ~= 'string' then return 0 end
    if node.text == want then return 0 end
    if not pcall(function() node.text = want end) then return 0 end
    return 1
  end

  -- Layer three: keeping the grouping, which means putting the digits back when the game is
  -- about to animate.
  --
  -- `doCurrencyAnimation` starts with transition.number(tonumber(goldText.text), ...), so a
  -- grouped label hands it nil - measured, that is not an error, it just animates from 0,
  -- and the counter would spin up from zero on every purchase. So the animation is wrapped:
  -- plain digits go in before it runs, the grouping comes back after it has finished.
  local function wrapAnim(inst, node)
    local anim = inst.doCurrencyAnimation
    if type(anim) ~= 'function' or anim == inst.__hudGoldAnim then return 0 end
    if type(inst.__hudGoldAnimOrig) ~= 'function' then inst.__hudGoldAnimOrig = anim end
    inst.__hudGoldAnim = function(...)
      local digits = goldDigits(goldReal())
      if digits then setText(node, digits) end
      f.animUntil = (system.getTimer() or 0) + ANIM_MS
      local ok, res = pcall(anim, ...)
      if ok then return res end
    end
    inst.doCurrencyAnimation = inst.__hudGoldAnim
    return 1
  end

  -- Keep an object at the game's own x, less the width the grouping added. The game's x is
  -- remembered the first time the object is seen, and every label is a fresh object, so a
  -- layout change that builds a new one is picked up on its own.
  local function hold(obj, delta)
    if type(obj.__hudGoldX) ~= 'number' then obj.__hudGoldX = obj.x end
    local want = obj.__hudGoldX - delta
    if obj.x ~= want then
      obj.x = want
      return 1
    end
    return 0
  end

  -- Where the counter and the coin belong, applied EVERY frame and independently of the text.
  --
  -- This is what makes the shift instant. The game's currency animation rewrites the text on
  -- every frame of its second, so writing text during it is pointless and is skipped - but the
  -- animation moves nothing, so the position is safe to set straight away. A label that has
  -- just been built is therefore shifted in its own first frame rather than when the animation
  -- ends, which is what it used to do.
  local function position(node)
    if not ALIGN or type(f.delta) ~= 'number' then return 0 end
    local coin = goldCoin(node)
    if not coin then return 0 end
    return hold(node, f.delta) + hold(coin, f.delta)
  end

  -- The width the grouping adds, measured by writing both strings in turn: invisible, because no
  -- frame can render between the statements of one Lua chunk, and the second write leaves the
  -- wanted text in place anyway. Measured rather than computed because the font is proportional,
  -- so "1" and "8" are not the same width.
  local function measureDelta(node, want)
    local plain = goldDigits(goldReal())
    if not plain or plain == want then return 0 end
    setText(node, plain)
    local baseWidth = node.width
    setText(node, want)
    local wantWidth = node.width
    if type(baseWidth) == 'number' and type(wantWidth) == 'number' then
      f.delta = wantWidth - baseWidth
      return 1
    end
    return 0
  end

  -- One label: keep the animation wrapped, put it where it belongs, and give it the wanted
  -- figure. Called every frame, so a label built this frame is handled in this frame.
  --
  -- The `rejected` guard is what stops an unrenderable separator from blinking. The text
  -- object does not necessarily keep what it is given - hand it a character the font has no
  -- glyph for and it stores something else - and a plain "write while it differs" loop would
  -- then write on every frame forever, rebuilding the glyphs that fast. U+00A0 does exactly
  -- that. So the value it turned into is remembered and left alone; only a different wanted
  -- string tries again.
  local function goldFix(module)
    if not FIX then return end
    local inst, node = goldLabel(module)
    if not inst then return end
    wrapAnim(inst, node)
    if type(node.text) ~= 'string' then return end
    position(node)                       -- always: a label built this frame moves this frame

    local want = f.want
    if not want then return end
    -- The extra width is measured once up front and again whenever the text really changes,
    -- because a different number of digits is a different amount of extra width.
    local unknown = (type(f.delta) ~= 'number')
    local wrong = (node.text ~= want) and not (f.rejected and node.text == f.rejected)
    if (system.getTimer() or 0) < (f.animUntil or 0) then return end   -- let it animate
    if not unknown and not wrong then return end
    if measureDelta(node, want) == 1 then position(node) end
    if wrong then
      f.fixes = (f.fixes or 0) + setText(node, want)
      if node.text ~= want then f.rejected = node.text end
    end
  end

  local function frame()
    local h = _G.__hud
    -- Its own guard field: if a newer install has replaced this one, stop. core's zoom owns
    -- keyBody and the host owns tickBody, so neither may be reused here.
    if not h or h.goldFrame ~= frame or not f.on then return end
    f.frames = (f.frames or 0) + 1
    goldFix(_G.innerTopBarGold)
    goldFix(_G.outerTopBarGold)
  end

  local function update()
    if not f.on then return end
    wrap()
    f.want = wanted()
  end

  local function kill()
    pcall(function() Runtime:removeEventListener('enterFrame', frame) end)
    unwrap()
    -- The animation wrapper sits on the INSTANCE, so the host tearing this install down does
    -- not take it off by itself. Left there it would keep writing plain digits over the
    -- counter after the feature was switched off, and it would hold a reference to this dead
    -- feature table. Off it comes, and the counter is put back to the game's own padding.
    for _, module in ipairs({_G.innerTopBarGold, _G.outerTopBarGold}) do
      local inst = goldInstance(module)
      if type(inst) == 'table' then
        if inst.doCurrencyAnimation == inst.__hudGoldAnim then
          inst.doCurrencyAnimation = inst.__hudGoldAnimOrig
        end
        inst.__hudGoldAnim, inst.__hudGoldAnimOrig = nil, nil
      end
      local _, node = goldLabel(module)
      if node then
        if type(node.__hudGoldX) == 'number' then node.x = node.__hudGoldX end
        local coin = goldCoin(node)
        if coin and type(coin.__hudGoldX) == 'number' then coin.x = coin.__hudGoldX end
        setText(node, string.format('%07d', goldCapped() or 0))
        node.__hudGoldX = nil
      end
    end
    local h = _G.__hud
    if h and h.goldFrame == frame then h.goldFrame = nil end
  end

  wrap()
  f.want = wanted()
  f.update, f.kill, f.on = update, kill, true
  Runtime:addEventListener('enterFrame', frame)
  local H = _G.__hud
  if H then H.goldFrame = frame end
end
""".replace("__CAP__", str(CAP))
        .replace("__ANIM__", str(ANIM_MS))
        .replace("__FIX__", "true" if cfg["fix_counter"] else "false")
        .replace("__ALIGN__", "true" if cfg["keep_right_edge"] else "false")
    )


def summary(cfg):
    return r"""(function()
  local real = goldReal()
  if real == nil then return 'real gold - the currency module is not open yet' end
  if real <= __CAP__ then
    return string.format('real gold: %s, under the cap, so the counter already agrees',
      goldGroup(real))
  end
  return string.format('real gold: %s  (the game\'s own counter caps at %s)',
    goldGroup(real), goldGroup(__CAP__))
end)()""".replace("__CAP__", str(CAP))


def status(cfg):
    return r"""(function()
  local f = _G.__hud and _G.__hud.feats.gold
  if not f or not f.on then return nil end
  local real, capped = goldReal(), goldCapped()
  if real == nil then return 'gold: no save open' end
  if real <= __CAP__ and GOLD_SEP == '' then
    return string.format('gold: %s, under the cap so nothing needed changing',
      goldGroup(real))
  end
  return string.format('gold: %s real, counter would say %s (%d label fix(es) so far)',
    goldGroup(real), goldGroup(capped), f.fixes or 0)
end)()""".replace("__CAP__", str(CAP))


def report(cfg):
    return r"""(function()
  local out = {}
  out[#out + 1] = 'real gold - the game caps its counter at ' .. goldGroup(__CAP__)
  out[#out + 1] = string.format('  separator = %q  (%s)', GOLD_SEP,
    GOLD_SEP == '' and 'plain digits, cap only' or 'grouped')
  local t = goldModule()
  if not t then
    out[#out + 1] = '  the currency module is not loaded, so no save is open'
    return table.concat(out, '\n')
  end
  local real, capped = goldReal(), goldCapped()
  out[#out + 1] = string.format('  real balance      = %s, drawn as %s',
    goldGroup(real), tostring(goldSpaced(real)))
  out[#out + 1] = string.format('  game would show   = %s', goldGroup(capped))
  if real and real > __CAP__ then
    out[#out + 1] = string.format('  hidden by the cap = %s', goldGroup(real - __CAP__))
  end
  out[#out + 1] = string.format('  the getter is replaced = %s', tostring(goldWrapped()))
  local f = _G.__hud and _G.__hud.feats.gold
  if f then
    out[#out + 1] = string.format('  label rewrites = %d   animations wrapped = %d',
      f.fixes or 0, f.animations or 0)
    out[#out + 1] = string.format('  per-frame passes = %d', f.frames or 0)
  end
  local shown = 0
  for _, module in ipairs({_G.innerTopBarGold, _G.outerTopBarGold}) do
    local inst, node = goldLabel(module)
    if node then
      shown = shown + 1
      out[#out + 1] = string.format('  on screen now: %q', tostring(node.text))
    end
  end
  if shown == 0 then out[#out + 1] = '  no top-bar label is on screen here' end
  out[#out + 1] = '  the getter is display-only: 5 modules read it, all of them drawing it -'
  out[#out + 1] = '  shops, items and spendGold use getGold(), so no purchase check moves.'
  out[#out + 1] = '  the two top-bar labels clamp AGAIN at the point they draw, so they are'
  out[#out + 1] = '  set as text; the dialogs and the Rogue readout follow the getter.'
  out[#out + 1] = '  the animation is wrapped so it never reads grouped digits back:'
  out[#out + 1] = '  tonumber("17 597 722") is nil, and the tween would start from 0.'
  return table.concat(out, '\n')
end)()""".replace("__CAP__", str(CAP))
