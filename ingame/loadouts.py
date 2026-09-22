#!/usr/bin/env python3
"""
loadouts.py - save and reload whole squads, hold items included, from the pause menu.

The game lets a squad hold six Coromon, each with one hold item, and alternating between
groups is tedious by hand: the storage item takes a Coromon out of the squad, the item comes
off automatically and lands in the bag, and it has to be put back on by hand every time. So
this draws a row of chips above the Coromon list in the pause menu, each with a LOAD half and
a SAVE half, and a group is one click to put back - Coromon AND items.

WHAT IS STORED, and this is the whole reason a loadout can be replayed exactly: a squad
member is identified by `monster.identifier`, which is unique per instance. The species
`monster.UID` is NOT unique - measured on a live save, five of the six squad members were
`NORMAL_CROW_1` - so a species list could not tell them apart. Verified across all 46 owned
Coromon (6 squad + 35 storage + 5 hidden) that `identifier` has no duplicates. Alongside it
each entry remembers `holdItemUID`.

THE MOVES ARE THE GAME'S OWN. Read out of `classes.interface.screens.monsterStorageScreen`
(L354-374), which is what the storage item does by hand:

    withdraw (squad not full)   playerMonsters:releaseFromMonsterStorage(m); :addToSquad(m)
    swap     (squad full)       releaseFromSquad(old) - releaseFromMonsterStorage(new)
                                addToMonsterStorage(old, false, isRemote) - addToSquad(new)

and the ORDER is restored with the game's own swap, read out of
`classes.modules.playerMonsters` (L366-372) - the same call the pause menu's drag-to-re-arrange
makes:

    swapWithinSquad(monster1, monster2)   -- both found by identity in the squad array, and their
                                          -- two positions exchanged

so this uses releaseFromSquad / releaseFromMonsterStorage / releaseFromHiddenMonsterStorage /
addToMonsterStorage / addToSquad / swapWithinSquad, and never surgery on the arrays the game
hands out. Every call is pcall'd and every step is VERIFIED by looking the monster back up where
it was supposed to land - if a move did not happen the monster is put back where it came from
rather than left in neither list, which is the one outcome worth engineering against.

ORDER, because the box holds a fixed number: everything wanted is taken OUT of the box first
(which frees slots), then the unwanted squad members are stored (which fills them back up),
then the wanted ones are added to the squad. Storing is checked after the fact and falls back
to the hidden storage pen when the box is full, because that pen exists for exactly this.

THE SQUAD'S MAXIMUM IS APPLIED HERE, because the game does not apply it: `getMaxSquadSize` is the
game's own hard-coded 6 and the Coromon tab builds exactly that many cards, but `addToSquad` will
happily add a seventh and an eighth - measured on a live save that had grown to 8, with only the
first six drawn anywhere (the 7th and 8th were in the squad and invisible). So a load takes only
the first `max` entries in the saved order, says which ones it left behind, stores anything in the
squad past the limit, and a snapshot can never record more than that either.

THE ITEM PATH, measured from `classes.monsters.Monster` (L1202-1227):

    getHoldItemUID()                   -> self.holdItemUID
    removeHoldItemAndSendToInventory() -> playerInventory:addItem(itemList[uid], 1), then nil
    setHoldItemByUID(uid)              -> sets the field; TOUCHES NO INVENTORY, and does NOT
                                          release what was already held

so the order has to be: take the old one off and let the game put it back in the bag, check
the bag has one (`playerInventory:getAmount(itemList[uid])` - it takes the ITEM OBJECT, not
the uid string, or it throws), take it out of the bag, then set it. An entry saved with no
item leaves whatever the Coromon is holding alone rather than destroying it.

INPUT is the game's own registration, `inputHelper:addTouchable(obj, listener)`. Read out of
`classes.modules.input.inputHelper` (L1010-1019) that is `eventManager:listen(obj, 'touch',
listener)` plus bookkeeping, so a plain function taking an `event` with a `phase` is all a
button needs - `UIButton.createTouchableListener` on top of it adds navigation and input-level
handling this does not want. `inputHelper:removeTouchable(obj)` is the matching teardown.

THE CARD LIST IS THE GAME'S, so after a load it has to be told, or it keeps showing the squad
that was there a moment ago - which is why a load first needed the pause menu closed and opened
again. Measured: the cards lag exactly one step behind the squad (cards `4 735 774 813 761 760`
against squad `735 4 774 813 761 760`). The game's own answer is a LOCAL function in
`classes.interface.screens.SquadScreen`, `refreshTallMonsterButtons` (lines 231-615, 20
upvalues) - the one it calls itself after a drag-swap and after every hold-item change, so it
already copes with a list that exists. It is not a method of anything (the screen's own method
list is the base builder's), so it is reached through `debug.getupvalue` over the functions
reachable from the pause menu and its cards, exactly the way core.py reaches the save table.
Calling it after a load makes the cards agree with the squad: six cards, no duplicates, in the
right order, repeatably.

The chips are children of the Coromon list's container, placed ABOVE it by measuring the
container rather than by hard-coding a y, so they follow the screen wherever the game puts
it. Nothing is drawn when the list is not on screen.

EACH SLOT ALSO SHOWS ITS GROUP, as the database icons it would show them with: the type frame
behind, the sprite over it, and the sprite is the RIGHT VARIANT - a perfect Coromon is drawn in
its perfect sprite, a potent one in its potent sprite, and a Coromon wearing a skin in its skin,
because a skin and a potential category are different atlas frames rather than recolours
(measured on one species: 253 standard, 257 potent, 261 perfect; and the same monster with the
`SAND_SKELETON|drawnEyes` skin is frame 786 against 787 without it). All three come off the
game: the type from `monsterData:getMonsterFamilyData():getRandomizablePrimaryType()`, the
category from `monsterUtility:getPotentialCategoryForPotential(mon.potential)`, and the skin
from `mon:getSpriteSkinUID()` - the same call the squad card itself makes. The icons live in the
gap between the buttons and the Coromon list, so nothing has to move towards the screen's header
to make room, and the size of that gap sets how big they come out.
"""

NAME = "loadouts"

# The game ships two fonts: outline_8 and outline_10_bold. The chips are small, so the
# smaller one is used unless it is asked for otherwise.
FONTS = {
    "small": "outline_8",
    "level": "outline_10_bold",
}

DEFAULT_FONT = "small"

# Colours are RGB triples for `setFillColor`, in the same shape the other features use.
COLOURS = {
    "empty": (0.16, 0.17, 0.20),     # a slot nothing has been saved into
    "filled": (0.20, 0.30, 0.42),    # a slot that holds a group
    "active": (0.24, 0.43, 0.66),    # ... and holds the group that is in the squad now
    "load": (0.22, 0.24, 0.28),
    "save": (0.30, 0.22, 0.20),
    "clear": (0.25, 0.16, 0.17),     # the lower half of the save button: clear the slot
    "press": (0.55, 0.58, 0.62),
    "panel": (0.11, 0.12, 0.14),
    "dim": (0.0, 0.0, 0.0),
    "text": (0.92, 0.92, 0.92),
    "dark_text": (0.55, 0.58, 0.62),
}

# The settings this feature reads, in the order they are written into overlays.toml:
# (key, default, comment).
SETTINGS = [
    ("enabled", True, "draw the loadout chips above the Coromon list in the pause menu"),
    ("slots", 6, "how many loadout slots to offer, one chip each"),
    (
        "font",
        DEFAULT_FONT,
        'chip font: "small" is outline_8, "level" is outline_10_bold',
        sorted(FONTS),
    ),
    (
        "offset_y",
        14,
        "pixels between my chip row and the top of the Coromon list",
    ),
    (
        "chip_height",
        22,
        "the chips' height in game units (x4 on a 1920-wide window)",
    ),
    (
        "icons",
        True,
        "draw the saved Coromon's database icons under each slot's buttons",
    ),
    (
        "icon_size",
        14,
        (
            "the tallest those icons may be, in game units. They are also limited by the gap "
            "above the Coromon list (offset_y - 3), because that gap is where they are drawn, so "
            "raising offset_y is what makes them bigger"
        ),
    ),
    (
        "icon_scale",
        1.0,
        (
            "how big to DRAW the icons, as a multiple of what the strip's width allows. 1.0 is the "
            "fit the layout computes - six icons across a slot, abutting - and anything above it "
            "makes the icons OVERLAP their neighbours, which is what the value is for: it shows "
            "the left-to-right drawing order (each icon, frame and creature together, above the "
            "one to its left). Half steps (1.5, 2.0) keep the art on the pixel grid"
        ),
    ),
    (
        "icon_fallback_category",
        "A",
        (
            "the sprite variant used when a Coromon's potential category cannot be read. Each "
            "Coromon is drawn in its OWN category (A standard, B potent, C perfect), because "
            "those are different sprites in the database, so this is only a fallback"
        ),
    ),
    (
        "icon_frames",
        True,
        "draw each icon in its type frame, the coloured border the database shows",
    ),
    (
        "empty_frames",
        True,
        (
            "draw a neutral (grey) type frame in every icon position of a slot that holds nothing, "
            "so the row reads as one grid of frames rather than a row with holes in it"
        ),
    ),
    (
        "empty_frame_dim",
        0.5,
        (
            "how much an empty icon position is darkened. 0.5 is the game's own factor - every "
            "TYPE_<X>_DARK colour in classes.constants.colors is half its bright one - and 1 leaves "
            "the neutral frame undimmed"
        ),
    ),
    (
        "confirm_overwrite",
        True,
        "ask before SAVE replaces a slot that already holds a group",
    ),
    (
        "toast_seconds",
        3,
        "how long the result message stays up, in seconds",
    ),
    (
        "store",
        "loadouts.txt",
        (
            "where the slots are kept. Written into the game's own documents folder - the "
            "absolute path is printed by `python overlays.py --status`"
        ),
    ),
]


def lua(cfg):
    """The pure half: reading the squad, finding a Coromon, moving one, and the slot file.
    Always part of the chunk, because the section and the status line both call it. Nothing
    here runs on its own, so it is safe to include in the read-only report."""
    # `__STORE__` is used HERE as well as in section(), and it is the file the slots are kept in,
    # so it has to be substituted in both. `--check` in _check.py is what found that: a
    # leftover placeholder is valid Lua and parses fine, then reads a nil global.
    return r"""
-- Every owned Coromon, wherever it lives. `getSquad`, `getMonsterStorage` and
-- `getHiddenMonsterStorage` are the three lists a Coromon can be in (the graveyard is not
-- somewhere a loadout can reach), and each is scanned by identifier rather than by index
-- because the indices move as the group is rebuilt.
local function loadoutFind(id)
  local pm = _G.playerMonsters
  if type(pm) ~= 'table' or id == nil then return nil, nil end
  local lists = {
    { 'squad', pm.getSquad },
    { 'storage', pm.getMonsterStorage },
    { 'hidden', pm.getHiddenMonsterStorage },
  }
  for _, entry in ipairs(lists) do
    local ok, list = pcall(entry[2], pm)
    if ok and type(list) == 'table' then
      for _, m in ipairs(list) do
        if type(m) == 'table' and m.identifier == id then return m, entry[1] end
      end
    end
  end
end

-- THE SQUAD'S MAXIMUM, and this is the game's own number rather than a guess: `getMaxSquadSize`
-- is hard-coded to 6.0 in `classes.modules.playerMonsters`, and the Coromon tab builds exactly
-- that many cards.
--
-- IT HAS TO BE APPLIED HERE, because `addToSquad` does NOT enforce it. That is how an 8-member
-- squad got made (measured: 8 in the squad, `getMaxSquadSize()` 6, and the tab drawing only the
-- first six - the 7th and 8th were in the squad but invisible, which is what the player noticed).
local function loadoutSquadMax()
  local ok, n = pcall(function() return tonumber(playerMonsters:getMaxSquadSize()) end)
  if ok and n and n >= 1 then return math.floor(n) end
  return 6
end

-- The squad as it stands, in order: the identity a slot needs, and the item to put back.
--
-- Capped at the maximum, so a slot can never record more than a load could ever put back - a
-- snapshot taken while the squad was over-full would otherwise save members that no load can
-- restore.
local function loadoutSnapshot()
  local pm = _G.playerMonsters
  local out = {}
  if type(pm) ~= 'table' then return out end
  local ok, list = pcall(function() return pm:getSquad() end)
  if not ok or type(list) ~= 'table' then return out end
  local max = loadoutSquadMax()
  for _, m in ipairs(list) do
    if type(m) == 'table' and #out < max then
      out[#out + 1] = { id = m.identifier, item = m.holdItemUID }
    end
  end
  return out
end

local function loadoutCount(which)
  local pm = _G.playerMonsters
  if type(pm) ~= 'table' then return 0 end
  local ok, list = pcall(function() return pm[which](pm) end)
  if ok and type(list) == 'table' then return #list end
  return 0
end

-- Where the slot file lives. `pathForFile` answers with the path whether or not the file is
-- there yet, which is what makes it usable for the first write.
local function loadoutPath()
  local name = __STORE__
  local ok, p = pcall(function()
    return system.pathForFile(name, system.DocumentsDirectory)
  end)
  if ok and type(p) == 'string' and p ~= '' then return p end
end

-- One slot per line:  `3 = 713:HOLD_EXTRA_GOLD 735:`. A Coromon saved with no item keeps the
-- empty half, so `id:` round-trips as "no item" rather than as a missing entry. Anything that
-- does not match the shape is skipped, so a half-written file cannot take the tool down.
--
-- AND ONLY THE FIRST `max` ENTRIES OF A LINE ARE KEPT. A line written before the squad limit was
-- applied can hold more than a load could ever put back (measured: a slot saved while the squad
-- was over-full), and reading it whole would leave the chips, the counts and the report all
-- describing a group larger than the game allows. Trimming here means the rest of the tool only
-- ever sees a legal group, and the file is rewritten clean the next time that slot is saved.
local function loadoutRead()
  local out = {}
  local path = loadoutPath()
  if not path then return out end
  local max = loadoutSquadMax()
  pcall(function()
    local fh = io.open(path, 'r')
    if not fh then return end
    for line in fh:lines() do
      local n, rest = line:match('^%s*(%d+)%s*=%s*(.-)%s*$')
      if n then
        local list = {}
        for id, item in rest:gmatch('(%d+):([%w_]*)') do
          if #list < max then
            list[#list + 1] = { id = tonumber(id), item = (item ~= '' and item or nil) }
          end
        end
        out[tonumber(n)] = list
      end
    end
    fh:close()
  end)
  return out
end

local function loadoutWrite(slots, count)
  local path = loadoutPath()
  if not path then return false, 'no path' end
  local ok, err = pcall(function()
    local fh = io.open(path, 'w')
    if not fh then error('could not open ' .. path) end
    fh:write('# Coromon squad loadouts - written by overlays.py (the loadouts feature).\n')
    fh:write('# slot = <coromon identifier>:<hold item uid|empty> ...\n')
    for i = 1, count do
      local parts = {}
      for _, e in ipairs(slots[i] or {}) do
        parts[#parts + 1] = tostring(e.id) .. ':' .. tostring(e.item or '')
      end
      fh:write(tostring(i) .. ' = ' .. table.concat(parts, ' ') .. '\n')
    end
    fh:close()
  end)
  if ok then return true, path end
  return false, tostring(err)
end

-- A Coromon put in the box, or in the hidden pen when the box will not take it. Verified by
-- looking it back up: a move that silently did nothing is the one thing that would lose a
-- Coromon, so the caller is told rather than trusted.
local function loadoutStore(m)
  local pm = _G.playerMonsters
  if type(pm) ~= 'table' or type(m) ~= 'table' then return nil end
  pcall(function() pm:addToMonsterStorage(m, false, false) end)
  local _, where = loadoutFind(m.identifier)
  if where == 'storage' then return 'storage' end
  pcall(function() pm:addToHiddenMonsterStorage(m) end)
  _, where = loadoutFind(m.identifier)
  if where == 'hidden' then return 'hidden' end
  return nil
end

local function loadoutInSquad(m)
  local pm = _G.playerMonsters
  if type(pm) ~= 'table' then return false end
  local ok, list = pcall(function() return pm:getSquad() end)
  if not ok or type(list) ~= 'table' then return false end
  for _, s in ipairs(list) do
    if s == m then return true end
  end
  return false
end

-- The bag's count for an item uid. `getAmount` takes the ITEM OBJECT - handing it the uid
-- string throws out of the inventory builder - and the object comes from the global itemList.
local function loadoutInBag(uid)
  local inv = _G.playerInventory
  local item = _G.itemList and _G.itemList[uid]
  if type(inv) ~= 'table' or type(item) ~= 'table' then return nil end
  local ok, n = pcall(function() return inv:getAmount(item) end)
  if ok then return tonumber(n) end
end

-- One item back on one Coromon, in the order the game's own methods require.
local function loadoutEquip(m, uid)
  if type(m) ~= 'table' or type(uid) ~= 'string' then return false, 'nothing to do' end
  if m.holdItemUID == uid then return true, 'already' end
  -- Off first, which is what puts the outgoing item back in the bag.
  pcall(function() m:removeHoldItemAndSendToInventory() end)
  local have = loadoutInBag(uid)
  if have == nil then return false, 'not in the bag' end
  if have < 1 then return false, 'none left in the bag' end
  local item = _G.itemList[uid]
  pcall(function() playerInventory:removeItem(item, 1) end)
  local ok = pcall(function() m:setHoldItemByUID(uid) end)
  if ok and m.holdItemUID == uid then return true, 'equipped' end
  -- It did not take: hand the one we just removed straight back rather than lose it.
  pcall(function() playerInventory:addItem(item, 1) end)
  return false, 'the game refused it'
end

-- Whether the squad right now is exactly this slot, in this order - what the chip's highlight
-- means.
local function loadoutMatches(list)
  local now = loadoutSnapshot()
  if #now ~= #list then return false end
  for i, e in ipairs(list) do
    if now[i].id ~= e.id or now[i].item ~= e.item then return false end
  end
  return true
end

-- The Coromon list screen, as the game sees it: the node that answers `getTallMonsterButtons`.
-- The overworld's tile map is skipped - it is thousands of nodes that can never be the list, and
-- it is still loaded behind the pause menu.
local function loadoutListScreen()
  local stage = display.getCurrentStage()
  if type(stage) ~= 'table' then return nil end
  -- `twNode` is core's shared helper for the overworld node; guarded because it is a GLOBAL from
  -- that preamble rather than something this module owns, and a missing one must not be a crash.
  local map = (type(twNode) == 'function') and twNode() or nil
  local budget, hit = 8000, nil
  local function scan(node)
    if hit then return end
    for i = 1, (node.numChildren or 0) do
      local c = node[i]
      if type(c) == 'table' and budget > 0 then
        budget = budget - 1
        if type(c.getTallMonsterButtons) == 'function' then hit = c return end
        if c ~= map then scan(c) end
      end
    end
  end
  pcall(scan, stage)
  return hit
end

-- A DEX ICON FOR A COROMON, drawn with the game's own components - the framed icon the database
-- shows, not the bare sprite.
--
-- `classes.interface.MonsterAvatar` is what the database screen uses. Read out of it (L86-101), an
-- icon is two images in a cell:
--
--   images/interface/icons/monsterAvatars/typeContainers/<type>.png   17x17, UNDER the sprite
--   a frame from the avatar atlas, via `newBorderless`                24x24, over it
--
-- with <type> from `monsterData:getMonsterFamilyData():getRandomizablePrimaryType()`. The folder
-- holds crimsonite, electric, fire, ghost, grey, ice, normal, sand and water, and 'grey' is the
-- game's own fallback. The frame is MAGNETED TO THE BOTTOM-RIGHT of the cell, so the 24-wide sprite
-- overhangs the 17-wide frame towards the top-left - that overhang is what makes the frame read as
-- a border rather than a box, and it is why the frame sits at +3.5,+3.5 here ((24-17)/2, the
-- difference between their centres) with the sprite centred.
--
-- The args for the sprite, in order: the MONSTER DATA (a species), the potential category, and the
-- sprite skin. The data is not the Monster - `getSpriteUID` lives on the data, which is why passing
-- the instance dies inside getMonsterAvatarFrameIndex (measured) - and the category is REQUIRED:
-- with none at all that call throws on a nil potential category. Both of the last two are read off
-- the Monster in loadoutAvatar; see the notes there.
--
-- The two images go into a cell of the game's own 24x24 units so the caller can scale the whole
-- icon as one thing. Returns nil rather than throwing when anything is missing; the caller then
-- draws its own placeholder.
local function loadoutTypeFrame(parent, mon, kindOverride)
  if not __ICON_FRAMES__ then return nil end
  local helper = _G.imageHelper
  if type(helper) ~= 'table' then return nil end
  local new = helper.new or helper.newObject      -- the game uses both names in different places
  if type(new) ~= 'function' then return nil end

  -- THE KIND IS USUALLY THE COROMON'S TYPE, but an explicit one is accepted for the empty-slot
  -- placeholders below, which have no Coromon to ask and want the neutral frame.
  local kind = kindOverride
  if kind == nil then
    local okD, data = pcall(function() return mon:getMonsterData() end)
    if not okD or type(data) ~= 'table' then return nil end
    local okF, fam = pcall(function() return data:getMonsterFamilyData() end)
    if okF and type(fam) == 'table' and type(fam.getRandomizablePrimaryType) == 'function' then
      local okT, t = pcall(function() return fam:getRandomizablePrimaryType() end)
      if okT and type(t) == 'string' and t ~= '' then kind = t end
    end
  end
  if kind == nil then kind = 'grey' end

  local path = 'images/interface/icons/monsterAvatars/typeContainers/' .. kind .. '.png'
  local ok, obj = pcall(new, helper, parent, path, { cache = true })
  if ok and type(obj) == 'table' then return obj end
end

-- A CELL FOR A POSITION THAT HOLDS NOTHING: the neutral (grey) type frame with no avatar in it.
--
-- Same shape as a real cell - a group anchored at its centre, the frame at +3.5,+3.5, which is the
-- (24-17)/2 offset that puts the 17x17 frame's box on the 24x24 cell's - so the layout scales and
-- places it through exactly the same code path as a Coromon's icon and the two cannot drift.
-- AN EMPTY POSITION'S FRAME, ON ITS OWN: the same 17x17 container a real icon's frame is, drawn
-- straight into the strip's FRAME LAYER rather than into a cell that also carries a sprite. The
-- two layers are what gives the strip its z-order - see rebuildIcons.
local function loadoutEmptyFrame(parent)
  local frame = loadoutTypeFrame(parent, nil, 'grey')
  if type(frame) ~= 'table' then return nil end
  local tint = __C_EMPTY_TINT__
  pcall(function()
    frame.anchorX, frame.anchorY = 0.5, 0.5
    -- TINTED LIKE THE DATABASE'S UNDISCOVERED COROMON: the grey container is a light grey box, so
    -- multiplying it by the game's own darkened factor is what turns it into the dark, "nothing
    -- here yet" frame instead of a bright plate that reads like a real entry.
    frame:setFillColor(tint[1], tint[2], tint[3])
  end)
  return frame
end

-- A SAVED COROMON'S SPRITE, ON ITS OWN - the other half of what used to be one "cell". It goes
-- into the strip's AVATAR LAYER, which is inserted above the frame layer.
local function loadoutAvatar(parent, mon)
  local okD, data = pcall(function() return mon:getMonsterData() end)
  if not okD or type(data) ~= 'table' or type(data.getSpriteUID) ~= 'function' then return nil end
  local ma = _G.MonsterAvatar or package.loaded['classes.interface.MonsterAvatar']
  if type(ma) ~= 'table' or type(ma.newBorderless) ~= 'function' then return nil end

  -- WHICH VARIANT: the same two arguments the database passes, and both come off the Monster.
  --
  --  * the potential category (A standard, B potent, C perfect) - what the database's own
  --    category selector picks, and it is a DIFFERENT SPRITE, not a recolour: measured on one
  --    species, atlas frames 253 (A), 257 (B) and 261 (C). From
  --    `monsterUtility:getPotentialCategoryForPotential(potential)`, the same call squad.py uses.
  --  * the sprite skin - `mon:getSpriteSkinUID()`, which is what the squad card itself passes
  --    when it draws a monster (`TallMonsterButton`, L212). The game composes the atlas key as
  --    `<spriteUID>_<category>` with no skin and `<spriteUID>_<category>_<skin suffix>` with one.
  --
  -- Both are looked up leniently: a failure falls back to the standard category and no skin, which
  -- is exactly what the game does for a Coromon that has neither.
  local category = __ICON_CATEGORY__
  do
    local okC, c = pcall(function()
      return monsterUtility:getPotentialCategoryForPotential(mon.potential)
    end)
    if okC and type(c) == 'string' and c ~= '' then category = c end
  end
  local skin
  pcall(function() skin = mon:getSpriteSkinUID() end)

  local ok, sprite = pcall(function()
    return ma:newBorderless(parent, data, category, skin)
  end)
  if not ok or type(sprite) ~= 'table' then return nil end
  pcall(function()
    sprite.anchorX, sprite.anchorY = 0.5, 0.5
    sprite.x, sprite.y = 0, 0
    if sprite.parent ~= parent then parent:insert(sprite) end
  end)
  return sprite
end

-- The container the Coromon list's OWN rows live in, asked of the screen itself rather than guessed:
-- every row `getTallMonsterButtons()` returns is a child of it.
--
-- It exists because selecting a card takes that row OUT of the list and rebuilds it inside the
-- popup that slides over it, which fires the same `TallMonsterButton.new` hook. Measured with a
-- card selected: the list container held 5 card children while the screen still reported 6 rows,
-- and the popup's own container is a fraction of the list's size - which is why a second chip row
-- appeared over the card, squished. Knowing which container is the real list is what stops that.
local function loadoutListContainer(screen)
  if type(screen) ~= 'table' then return nil end
  local ok, rows = pcall(function() return screen:getTallMonsterButtons() end)
  if not ok or type(rows) ~= 'table' then return nil end
  for _, r in pairs(rows) do
    if type(r) == 'table' and type(r.parent) == 'table' then return r.parent end
  end
end

-- Is this the Coromon list's container? Its PARENT is the screen that answers
-- `getTallMonsterButtons` - which is the game's own way of saying "this list is mine", and it is an
-- O(1) question, so it can be asked the moment a row appears. The popup's container fails it: the
-- popup is an overlay hanging off the screen, not a child of the screen's row list.
local function loadoutIsListContainer(container)
  if type(container) ~= 'table' then return false end
  local par = container.parent
  return type(par) == 'table' and type(par.getTallMonsterButtons) == 'function'
end

-- How many of the game's own overlays are open: dialogues, popups, the card action menu. The same
-- call reload.py uses to notice a stale conversation box. nil means the module is not loaded, so a
-- caller can tell "none open" from "cannot tell".
local function loadoutOverlayCount()
  local bob = package.loaded['classes.interface.overlays.baseOverlayBuilder']
  if type(bob) ~= 'table' or type(bob.getAmountOfActiveOverlays) ~= 'function' then return nil end
  local ok, n = pcall(function() return bob:getAmountOfActiveOverlays() end)
  if ok and tonumber(n) then return math.floor(tonumber(n)) end
end

-- A FUNCTION REACHABLE FROM `roots` THAT HAS `name` AS ONE OF ITS UPVALUES.
-- The only way to reach the screen's own refresh, and this is measured rather than assumed:
-- `refreshTallMonsterButtons` is a LOCAL function in `classes.interface.screens.SquadScreen`
-- (lines 231-615, 20 upvalues) - not a method on the screen, not a field of anything. The
-- screen's own method list, read live, is the base builder's (addTopBarObject, setTitle, ...),
-- and contains nothing that rebuilds the cards. So it is visible only through the closures that
-- captured it, which is the same route core.py takes to the save table.
--
-- Bounded on purpose: at most `budget` functions, 60 upvalues each, 80 fields per table. On the
-- live pause menu it finds it after scanning 776 functions. That is a few tens of milliseconds,
-- which is why this runs ONCE PER LOAD and is never touched by the per-tick path.
local function loadoutFindUpvalue(roots, name, budget)
  local seen, queue = {}, {}
  for _, r in ipairs(roots) do queue[#queue + 1] = r end
  local scanned = 0
  while #queue > 0 and scanned < (budget or 1500) do
    local node = table.remove(queue)
    local t = type(node)
    if t == 'function' then
      if not seen[node] then
        seen[node] = true
        scanned = scanned + 1
        for i = 1, 60 do
          local un, v = debug.getupvalue(node, i)
          if not un then break end
          if un == name and type(v) == 'function' then return v end
          local vt = type(v)
          if (vt == 'function' or vt == 'table') and not seen[v] then
            queue[#queue + 1] = v
          end
        end
      end
    elseif t == 'table' then
      if not seen[node] then
        seen[node] = true
        local count = 0
        for _, v in pairs(node) do
          count = count + 1
          if count > 80 then break end
          local vt = type(v)
          if (vt == 'function' or vt == 'table') and not seen[v] then
            queue[#queue + 1] = v
          end
        end
      end
    end
  end
end

-- The load itself. Returns the moved count and a message for the toast:
--   "3 in, 3 out"  /  "slot 2 is empty"  /  "2 skipped: <names>"
local function loadoutApply(slot, list)
  local pm = _G.playerMonsters
  if type(pm) ~= 'table' then return 0, 'no squad' end
  if type(list) ~= 'table' or #list == 0 then return 0, 'slot ' .. slot .. ' is empty' end

  -- 1. What is still ours. Anything released since the save is reported and left out, which is
  -- what was asked for: load everything else rather than refuse the whole thing.
  --
  -- AND NEVER MORE THAN THE SQUAD HOLDS. The first `max` in the SAVED ORDER are the ones that come
  -- back; anything past that is reported instead of added, because a squad over the game's own
  -- maximum is a state its UI cannot show (the extra members exist but no card is drawn for them).
  local maxSquad = loadoutSquadMax()
  local keep, missing, overflow = {}, {}, {}
  for _, e in ipairs(list) do
    local m = loadoutFind(e.id)
    if type(m) == 'table' then
      if #keep < maxSquad then
        keep[#keep + 1] = { mon = m, item = e.item }
      else
        overflow[#overflow + 1] = tostring(e.id)
      end
    else
      missing[#missing + 1] = tostring(e.id)
    end
  end
  if #keep == 0 then return 0, 'nothing in slot ' .. slot .. ' is still yours' end

  local want = {}
  for _, k in ipairs(keep) do want[k.mon] = true end

  -- 2. Free the box first: take everything wanted out of it. The box holds a fixed number of
  -- Coromon, so doing this before anything is stored is what keeps it from overflowing.
  local incoming = {}
  for _, k in ipairs(keep) do
    local _, where = loadoutFind(k.mon.identifier)
    if where == 'storage' then
      pcall(function() pm:releaseFromMonsterStorage(k.mon) end)
    elseif where == 'hidden' then
      pcall(function() pm:releaseFromHiddenMonsterStorage(k.mon) end)
    end
    if loadoutInSquad(k.mon) then
      incoming[#incoming + 1] = k
    elseif loadoutFind(k.mon.identifier) == nil then
      -- Neither in the squad nor in a box: the release worked and it is ours, waiting.
      incoming[#incoming + 1] = k
    else
      -- Still in a box after asking: put it back on the list it came from and say so.
      missing[#missing + 1] = tostring(k.mon.identifier)
    end
  end

  -- 3. Replace, ONE AT A TIME AND IN PLACE - which is the game's own swap.
  --
  -- Three things here come straight out of the game's code (`playerMonsters` L241-347) rather than
  -- from taste:
  --
  --  * `releaseFromSquad(m)` RETURNS THE INDEX it removed, and `addToSquad(m, index)` INSERTS at
  --    one. That is exactly how the storage screen swaps a Coromon - `releaseFromSquad(old)` then
  --    `addToSquad(new, out)` - so a replacement goes back into the slot its predecessor came out
  --    of and the squad keeps its shape as it changes.
  --  * `releaseFromSquad` ALREADY SENDS THE HOLD ITEM TO THE INVENTORY itself (L345). The item
  --    needs no separate removal; doing it first is harmless but redundant, and it is kept only
  --    for the paths that do not go through releaseFromSquad.
  --  * THE SQUAD IS NEVER EMPTIED. `releaseFromSquad` does not refuse that - it is a plain remove -
  --    but the game never lets the player do it, and at least one member has to stay for the rest
  --    of the game to make sense (something is always the lead). So nothing is removed unless the
  --    squad is FULL and a replacement is about to go in: the count dips to max-1 at the lowest,
  --    so even a full six-for-six swap never passes through an empty or near-empty squad.
  --
  -- AND EVERY WALK IS OVER A COPY. `getSquad()` hands back the game's OWN array and the calls
  -- below remove from it, so walking it directly makes `ipairs` step over whichever member shifted
  -- into the current index and then stop early - which is what produced an eight-member squad
  -- (four members to store, two stored, the walk ending before the last two).
  local current = {}
  for _, m in ipairs(pm:getSquad() or {}) do
    current[#current + 1] = m
  end
  local outgoing = {}
  for _, m in ipairs(current) do
    if type(m) == 'table' and not want[m] then outgoing[#outgoing + 1] = m end
  end

  local function squadCount()
    local ok, list = pcall(function() return pm:getSquad() end)
    if ok and type(list) == 'table' then return #list end
    return 0
  end

  local moved, over = 0, 0

  -- Take one out and put it away, reporting the one case that must not pass silently: nowhere to
  -- put it. Then it goes back where it was rather than being lost.
  local function putOut(m, index)
    if type(m) ~= 'table' or not loadoutInSquad(m) then return end
    pcall(function() m:removeHoldItemAndSendToInventory() end)
    pcall(function() pm:releaseFromSquad(m) end)
    if loadoutInSquad(m) then return end          -- it would not come out; leave it be
    if loadoutStore(m) then
      moved = moved + 1
    else
      pcall(function() pm:addToSquad(m, index) end)
      missing[#missing + 1] = 'box full'
    end
  end

  -- 4. The wanted ones in, each taking the place of one that left.
  local added, nextOut = 0, 1
  for _, k in ipairs(incoming) do
    if not loadoutInSquad(k.mon) then
      local index
      if squadCount() >= maxSquad and outgoing[nextOut] ~= nil then
        -- Full, so one has to go first - and the replacement is put back into its index.
        local before = pm:getSquad()
        for i, m in ipairs(before) do
          if m == outgoing[nextOut] then index = i break end
        end
        putOut(outgoing[nextOut], index)
        nextOut = nextOut + 1
      end
      pcall(function() pm:addToSquad(k.mon, index) end)
      if loadoutInSquad(k.mon) then
        added = added + 1
      else
        -- The squad would not take it: put it back in a box so it is not left in limbo.
        loadoutStore(k.mon)
        missing[#missing + 1] = tostring(k.mon.identifier)
      end
    end
  end

  -- Whatever else is not wanted, now that the squad holds what it should.
  --
  -- Never the last one: the squad is not allowed to be empty, and if every wanted Coromon failed to
  -- go in (the squad was full of members that could not be released, or a box refused its return)
  -- these leftovers can be all the squad has left. Stopping at one member is what keeps that from
  -- ending as an empty squad, which is the state the game itself never permits.
  for i = nextOut, #outgoing do
    if squadCount() > 1 then
      putOut(outgoing[i], nil)
    else
      missing[#missing + 1] = tostring(outgoing[i].identifier)
    end
  end

  -- 5. Put them back in the order the slot saved. The squad's order is the player's order - it
  -- decides who leads - so restoring a group with the right Coromon in the wrong sequence is not
  -- restoring it.
  --
  -- The primitive is the game's own, and it is the same one the pause menu's drag-to-re-arrange
  -- uses: measured from `classes.modules.playerMonsters` (L366-372),
  --
  --   swapWithinSquad(monster1, monster2)   -- finds both by IDENTITY in the squad array and
  --                                         -- exchanges their two positions
  --
  -- so it takes OBJECTS, not indices, and it mutates the array in place. That makes this a
  -- selection sort: walk the saved order, and for each position find where the Coromon that
  -- belongs there currently is, and swap it in. Reading the squad fresh on every pass, because
  -- the swap just changed it. Anything not in the saved group gets pushed to the right by the
  -- same swaps, which is where it belongs.
  local swaps = 0
  for pos = 1, #keep do
    local wanted = keep[pos].mon
    local squad = pm:getSquad() or {}
    if squad[pos] ~= wanted then
      local at
      for i, m in ipairs(squad) do
        if m == wanted then at = i break end
      end
      if at and squad[pos] ~= nil then
        if pcall(function() pm:swapWithinSquad(squad[pos], squad[at]) end) then
          swaps = swaps + 1
        end
      end
    end
  end

  -- 6. Settle. Whatever happened above, the squad must not be over the game's own maximum - that
  -- state cannot be shown and is what the missing pieces above produced - so the limit is ENFORCED
  -- here rather than assumed to have fallen out of the earlier steps. Walked from the END
  -- backwards, because removing the last member cannot shift the ones before it, and only members
  -- past the limit are touched, so the order restored in step 5 is left alone. It also cannot empty
  -- the squad: it only ever runs while there are more than `maxSquad` members.
  do
    local squad = pm:getSquad() or {}
    for i = #squad, maxSquad + 1, -1 do
      local m = squad[i]
      if type(m) == 'table' and not want[m] then
        pcall(function() m:removeHoldItemAndSendToInventory() end)
        pcall(function() pm:releaseFromSquad(m) end)
        if not loadoutInSquad(m) then
          if loadoutStore(m) then
            over = over + 1
          else
            pcall(function() pm:addToSquad(m) end)
            missing[#missing + 1] = 'box full'
          end
        end
      end
    end
  end

  -- 7. The items, last, so every outgoing item is already back in the bag and can be counted.
  local unequipped = {}
  for _, k in ipairs(keep) do
    if k.item and loadoutInSquad(k.mon) then
      local ok, why = loadoutEquip(k.mon, k.item)
      if not ok then unequipped[#unequipped + 1] = why end
    end
  end

  local msg = string.format('slot %d: %d in, %d out', slot, added, moved)
  local n = 0
  for _, k in ipairs(keep) do if loadoutInSquad(k.mon) then n = n + 1 end end
  msg = msg .. string.format('  (%d/%d in the squad)', n, #list)
  if swaps > 0 then
    msg = msg .. string.format('  re-ordered in %d swaps', swaps)
  end
  if #overflow > 0 then
    msg = msg .. string.format('\nthe squad holds %d, so these stayed put: %s',
      maxSquad, table.concat(overflow, ', '))
  end
  if over > 0 then
    msg = msg .. string.format('\n%d moved to storage to stay within %d', over, maxSquad)
  end
  if #unequipped > 0 then
    msg = msg .. '\nitems not restored: ' .. table.concat(unequipped, ', ')
  end
  if #missing > 0 then
    msg = msg .. '\nnot available: ' .. table.concat(missing, ', ')
  end
  return added, msg
end
""".replace("__STORE__", _lua_string(cfg["store"])).replace(
        "__ICON_CATEGORY__", _lua_string(str(cfg["icon_fallback_category"]))
    ).replace("__ICON_FRAMES__", "true" if cfg["icon_frames"] else "false").replace(
        "__C_EMPTY_TINT__", _lua_dim(float(cfg["empty_frame_dim"]))
    )


def _lua_string(value):
    """A Lua string literal for a setting coming out of overlays.toml."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _lua_colour(name):
    r, g, b = COLOURS[name]
    return "{ %s, %s, %s }" % (r, g, b)


def _lua_dim(value):
    """A grey triple for a single 0..1 dimming factor the player sets.

    Kept as a SETTING rather than a colour: the config reader only knows the names in SETTINGS, so
    a hand-added `empty_frame_tint` in overlays.toml was rejected with "not a setting - ignored"
    (seen in the install output) and never reached the code.
    """
    if value < 0:
        value = 0.0
    if value > 1:
        value = 1.0
    return "{ %s, %s, %s }" % (value, value, value)


def section(cfg):
    if not cfg["enabled"]:
        return ""
    font = FONTS.get(cfg["font"], FONTS[DEFAULT_FONT])
    return (
        r"""
do
  local FONT = __FONT__
  local SLOTS = __SLOTS__
  local OFFSET = __OFFSET__
  local CHIP_H = __CHIP_H__
  local ICONS = __ICONS__
  local ICON_SIZE = __ICON_SIZE__
  local ICON_SCALE = __ICON_SCALE__
  local ICON_FRAMES = __ICON_FRAMES__
  local EMPTY_FRAMES = __EMPTY_FRAMES__
  local CONFIRM = __CONFIRM__
  local TOAST_MS = __TOAST_MS__
  local ROW_CLASS = 'classes.interface.TallMonsterButton'
  local COL = {
    empty = __C_EMPTY__, filled = __C_FILLED__, active = __C_ACTIVE__,
    load = __C_LOAD__, save = __C_SAVE__, clear = __C_CLEAR__, press = __C_PRESS__,
    panel = __C_PANEL__, dim = __C_DIM__,
    text = __C_TEXT__, dark = __C_DARK__,
  }

  local f = makeFeature('loadouts', 250)
  f.bars = {}        -- the Coromon list's container -> the chip row drawn above it
  f.rows = {}        -- row -> true, only used to find the container
  f.slot = nil       -- the slot open in the confirm overlay, if any
  f.overlay = nil
  f.inputFn = nil    -- the overlay's Runtime touch listener, held so it is always removed
  f.pressed = nil    -- a press whose `began` this listener saw
  f.toast = nil
  f.last = 'idle'
  f.path = nil
  f.slots, f.path = loadoutRead(), loadoutPath()

  -- =====================================================================
  -- Input. One registration, no navigation and no input-level handling:
  -- `inputHelper:addTouchable` is `eventManager:listen(obj, 'touch', fn)` plus the
  -- bookkeeping that makes it removable, so a function reading `event.phase` is all a
  -- chip needs. PCall'd throughout: a button that cannot be registered is a button that
  -- does nothing, not an install that fails.
  --
  -- The EVENT is handed to the callbacks as well as the phase, because the modal below decides
  -- its answer from WHERE the press landed rather than from which object it hit.
  -- =====================================================================
  local function touchable(obj, onDown, onUp)
    if type(obj) ~= 'table' then return end
    local listener = function(event)
      local phase = type(event) == 'table' and event.phase or nil
      if phase == 'began' then
        if onDown then pcall(onDown, event) end
      elseif phase == 'ended' or phase == 'cancelled' then
        if onUp then pcall(onUp, event) end
      end
    end
    local ok = pcall(function() inputHelper:addTouchable(obj, listener) end)
    if not ok then
      -- The one fallback worth having: the engine's own listener, which is what the call
      -- above ends up doing anyway.
      pcall(function()
        obj:addEventListener('touch', function(event)
          local phase = event.phase
          if phase == 'began' then
            if onDown then pcall(onDown, event) end
          elseif phase == 'ended' or phase == 'cancelled' then
            if onUp then pcall(onUp, event) end
          end
        end)
      end)
    end
    return function()
      pcall(function() inputHelper:removeTouchable(obj) end)
      pcall(function() obj:removeEventListener('touch') end)
    end
  end

  -- =====================================================================
  -- HOVER HIGHLIGHTS, decided by POSITION rather than by per-object listeners.
  --
  -- MEASURED on the live pause menu, with two test rects on the stage (one plain, one registered
  -- through `inputHelper:addTouchable`) each carrying a 'mouse' listener, and a Runtime 'mouse'
  -- listener as well, then moving the cursor over each of them:
  --
  --   * the Runtime listener receives every move: `type = "move"` (not "mouseover"), with `x`/`y`
  --     in SCREEN pixels - the same frame a touch reports `x`/`y` in - and `xStart`/`yStart` nil;
  --   * the per-object listeners NEVER fired, for either rect. Both read
  --     `isHitTestable = false`, and a listener alone does not change that, so there is no
  --     object-level hover to be had here.
  --
  -- That is the same conclusion the modal's press reached, so hover has the same shape: one Runtime
  -- listener, a list of boxes with their rectangles in their OWN GROUP's coordinates, and the
  -- cursor's position deciding which one is lit.
  --
  -- The list is REBUILT BY THE LAYOUT - which runs every tick, because the row moves with the list -
  -- so a highlight can never be a frame behind a row that moved, and it is dropped when a modal
  -- takes over or the row is hidden, so nothing keeps pointing at a box that is not on screen.
  -- =====================================================================
  local hot = nil        -- { g = <group>, spots = { { key, x0, y0, x1, y1, box, base, lit } } }
  local hotLit = nil     -- the spot lit now, if any
  local hotCursor = nil  -- the last cursor position, in content units

  -- ONE highlight rule for every box, whatever its own colour: blend towards the press colour. The
  -- chip's three faces and the modal's two buttons are all different colours, and a fixed highlight
  -- colour would look right on one and wrong on the next.
  local HOVER_MIX = 0.45
  local function litColour(base)
    local p = COL.press
    return {
      base[1] + (p[1] - base[1]) * HOVER_MIX,
      base[2] + (p[2] - base[2]) * HOVER_MIX,
      base[3] + (p[3] - base[3]) * HOVER_MIX,
    }
  end

  local function paint(spot, on)
    if spot == nil or type(spot.box) ~= 'table' then return end
    local c = on and spot.lit or spot.base
    pcall(function() spot.box:setFillColor(c[1], c[2], c[3]) end)
  end

  -- Repaints ONLY on a change of box. The list is rebuilt every tick, so the spot TABLES are new
  -- each time and comparing them would repaint twice per tick for nothing - the key is what
  -- identifies the button.
  local function applyHot(found)
    if found == nil and hotLit == nil then return end
    if found ~= nil and hotLit ~= nil and found.key == hotLit.key then
      hotLit = found
      return
    end
    if hotLit ~= nil then paint(hotLit, false) end
    hotLit = found
    if hotLit ~= nil then paint(hotLit, true) end
  end

  -- A cursor position in CONTENT units against the current list. The conversion is `contentToLocal`
  -- on the group the spots were measured in, which is the same call the modal's press uses.
  local function hotAt(cx, cy)
    local h = hot
    if type(h) ~= 'table' or type(h.g) ~= 'table' then applyHot(nil) return end
    local ok, x, y = pcall(function() return h.g:contentToLocal(cx, cy) end)
    if not ok or tonumber(x) == nil or tonumber(y) == nil then applyHot(nil) return end
    x, y = tonumber(x), tonumber(y)
    local found
    for _, s in ipairs(h.spots) do
      if x >= s.x0 and x <= s.x1 and y >= s.y0 and y <= s.y1 then found = s break end
    end
    applyHot(found)
  end

  -- Replaces the list of buttons that may light up, keeping whatever is lit only if the same button
  -- is still there - a relayout must not blink the highlight off, so this re-tests the last known
  -- cursor position against the new list instead of clearing.
  local function setHot(g, spots)
    hot = (g ~= nil and type(spots) == 'table' and #spots > 0) and { g = g, spots = spots } or nil
    if hot == nil or hotCursor == nil then
      applyHot(nil)
      return
    end
    hotAt(hotCursor[1], hotCursor[2])
  end

  -- SCREEN pixels (what a mouse event carries) to content units, which is what `contentToLocal`
  -- wants. The scale and the origin are ASKED OF THE ENGINE rather than assumed to be 5 and 0: the
  -- origin is not 0 when the window is letterboxed or the content area is offset.
  local function screenToContent(sx, sy)
    local sc = tonumber(display.contentToScreenScale) or 1
    if sc <= 0 then sc = 1 end
    local ox, oy = 0, 0
    pcall(function()
      local x0, y0 = display.contentToScreen(0, 0)
      ox, oy = tonumber(x0) or 0, tonumber(y0) or 0
    end)
    return (sx - ox) / sc, (sy - oy) / sc
  end

  local function onMouseMove(event)
    if type(event) ~= 'table' or event.type ~= 'move' then return end
    local sx, sy = tonumber(event.x), tonumber(event.y)
    if sx == nil or sy == nil then return end
    local cx, cy = screenToContent(sx, sy)
    hotCursor = { cx, cy }
    hotAt(cx, cy)
  end
  pcall(function() Runtime:addEventListener('mouse', onMouseMove) end)

  -- NOTHING THIS FEATURE DRAWS MAY TAKE A PRESS BY ITSELF.
  --
  -- `isHitTestable` is the ENGINE's property; Coromon's own `inputHelper:isTouchable(obj)` is a
  -- different question entirely - it only asks whether `obj.touch` holds a listener (inputHelper
  -- L1031), and the first attempt here set `isTouchable`, which is a plain key on a display object
  -- that nothing reads.
  --
  -- What is MEASURED on the live pause menu, and what the two reports were:
  --
  --   * a plain rect reads `isHitTestable = false` (and `isVisible = true`), and a picture with no
  --     listener does not take a press - the press falls to whatever the engine finds under it;
  --   * a label lying over a button DOES take it, which is why the buttons first responded only
  --     around their edges - "as if the target were smaller than the button";
  --   * nothing in the modal takes a press unless one object is registered for it, which is why a
  --     press could reach the cards underneath.
  --
  -- The game's own backdrop, out of `rectHelper` (L26-31) and `baseOverlayBuilder` (L82-85): an
  -- INVISIBLE object with `isHitTestable = true`, registered as the one touchable. The modal below
  -- is built as that same object (`rectHelper.newFullScreenContainer`) and decides by position,
  -- so there is no picture anywhere in it that can answer a press.
  local function untouchable(obj)
    pcall(function() obj.isHitTestable = false end)
  end

  -- =====================================================================
  -- MUTING THE LIST WHILE THE MODAL IS UP.
  --
  -- The press that opens the modal landed on a chip, and the cards are the layer directly under
  -- the chips, so a modal that fails to take a press does not fall into a hole - it selects a
  -- card. Rather than hope the backdrop takes every press, the card buttons are taken out of the
  -- game's touchable list for as long as the modal is up.
  --
  -- `inputHelper:addTouchable` writes the listener to `obj.touch` as well as registering it, and
  -- `removeTouchable` drops both, so the listener is readable from the object itself and can be
  -- put back through the game's own registration. Measured in `inputHelper` L1009-1028. A button
  -- whose touch was NOT registered through the helper gets `isHitTestable = false` instead, which
  -- makes it miss the hit test for exactly as long as the modal is up.
  --
  -- Every step is PCall'd and the record survives until the restore has been attempted, because a
  -- modal that closes over a half-restored list is worse than one that never opened.
  -- =====================================================================
  local function muteCards()
    if f.muted then return end
    local ok, rows = pcall(function()
      return f.screen ~= nil and f.screen:getTallMonsterButtons() or nil
    end)
    if not ok or type(rows) ~= 'table' then return end
    local held = {}
    for _, b in pairs(rows) do
      if type(b) == 'table' then
        local rec = { b = b, fn = b.touch, hit = b.isHitTestable }
        if type(rec.fn) == 'function' then
          pcall(function() inputHelper:removeTouchable(b) end)
        else
          pcall(function() b.isHitTestable = false end)
        end
        held[#held + 1] = rec
      end
    end
    if #held > 0 then f.muted = held end
  end

  local function unmuteCards()
    local held = f.muted
    f.muted = nil
    if type(held) ~= 'table' then return end
    for _, rec in ipairs(held) do
      local b = rec.b
      if type(b) == 'table' and b.parent ~= nil then
        if type(rec.fn) == 'function' then
          pcall(function()
            if b.touch == nil then inputHelper:addTouchable(b, rec.fn) end
          end)
        else
          -- nil had left the default in place, and the default is `true`.
          pcall(function() b.isHitTestable = (rec.hit == nil) and true or rec.hit end)
        end
      end
    end
  end

  -- =====================================================================
  -- The message. A stage child kept on top, because it is a reply to a click and must be
  -- visible over whatever the pause menu has drawn.
  --
  -- MEASURED on the game's own text objects, and both facts are what was wrong before:
  --
  --   * `width`/`height` are the real box of the text - a 60-character message at outline_8
  --     measures 240.25 x 15, and a two-line one is as wide as its WIDEST line and 30 tall - so
  --     the panel can be sized from the text instead of being a fixed 420 that long messages ran
  --     out of;
  --   * the anchor of these objects is 0,0, TOP-LEFT, so the old version - which placed the text
  --     at the panel's CENTRE - started the first character in the middle of the panel and let the
  --     rest bleed out of its right edge. That is the reported bleeding, and why the start of the
  --     text looked unrelated to the panel it was in.
  --
  -- So: measure the text, size the panel to it (clamped to the screen), and left-align the text
  -- with a fixed padding, which gives every message the same left edge whatever it says.
  --
  -- AND IT SITS ABOVE THE CHIP ROW, not over it. The row's own top edge is in content coordinates
  -- and the message is usually about that row, so covering the six slots to say what happened to
  -- them is the wrong way round; the strip between the game's header and the chips is empty.
  -- =====================================================================
  local function toast(msg)
    if f.toast then drop(f.toast) end
    if type(msg) ~= 'string' or msg == '' then return end
    local g = display.newGroup()
    keepOnTop(g)

    local pad = 8
    local maxW = math.max(120, (tonumber(display.contentWidth) or 512) - 16)
    local t = text(g, FONT, msg, 0, 0, COL.text)
    pcall(function() t.anchorX, t.anchorY = 0, 0 end)
    local tw = tonumber(t.width) or (#msg * 5)
    local th = tonumber(t.height) or 15

    -- EVERY LINE THAT IS TOO WIDE IS WRAPPED, using the width per character the object itself
    -- reports. The feature's own messages are already multi-line, but one of those lines can still
    -- be a long list - "items not restored: ..." - and a line that does not fit is a line that
    -- bleeds out of the panel, which is the complaint this answers.
    if tw + pad * 2 > maxW then
      local perChar = tw / math.max(1, #msg)
      local room = math.max(8, math.floor((maxW - pad * 2) / perChar))
      local wrapped = {}
      for segment in (msg .. '\n'):gmatch('([^\n]*)\n') do
        if segment == '' then
          wrapped[#wrapped + 1] = ''
        else
          local line = ''
          for word in segment:gmatch('%S+') do
            local candidate = (line == '') and word or (line .. ' ' .. word)
            if #candidate > room and line ~= '' then
              wrapped[#wrapped + 1] = line
              line = word
            else
              line = candidate
            end
          end
          wrapped[#wrapped + 1] = line
        end
      end
      local joined = table.concat(wrapped, '\n')
      if joined ~= msg then
        pcall(function() t:removeSelf() end)
        t = text(g, FONT, joined, 0, 0, COL.text)
        pcall(function() t.anchorX, t.anchorY = 0, 0 end)
        tw = tonumber(t.width) or tw
        th = tonumber(t.height) or th
      end
    end

    local w = math.min(maxW, tw + pad * 2)
    local h = th + pad
    rect(g, -w / 2, -h / 2, w, h, { 0.08, 0.09, 0.11, 0.92 })
    g:insert(t)          -- the panel was created after the text, so the text goes back on top
    pcall(function() t.x, t.y = -w / 2 + pad, -h / 2 + pad / 2 end)

    -- Above the chips, wherever they are: the top-left corner of the row in content coordinates.
    local chipsTop
    for _, bar in pairs(f.bars or {}) do
      if type(bar.group) == 'table' then
        local ok, _, y = pcall(function() return bar.group:localToContent(0, 0) end)
        y = ok and tonumber(y) or nil
        if y and (chipsTop == nil or y < chipsTop) then chipsTop = y end
      end
    end
    local vw = tonumber(display.contentWidth) or 512
    local vh = tonumber(display.contentHeight) or 288
    local y = (chipsTop or (vh * 0.16)) - 5 - h / 2
    if y - h / 2 < 2 then y = 2 + h / 2 end       -- never off the top of the screen
    g.x, g.y = vw / 2, y
    f.toast = g
    local mine = g
    timer.performWithDelay(TOAST_MS, function()
      if f.toast == mine then
        f.toast = nil
        drop(mine)
      end
    end)
  end

  -- =====================================================================
  -- The confirm overlay: pictures, plus a RUNTIME touch listener that decides the answer from
  -- where the press landed. See the block above `openInput` for why it is not an object-level
  -- touchable like every other button in this file - that is the part that was wrong twice.
  -- =====================================================================
  local function closeInput()
    local fn = f.inputFn
    f.inputFn, f.pressed = nil, nil
    if fn then pcall(function() Runtime:removeEventListener('touch', fn) end) end
  end

  local function closeOverlay()
    local g = f.overlay
    f.overlay, f.slot, f.catcher = nil, nil, nil
    closeInput()
    -- ...and the hover highlight goes with it: its spots name boxes inside the group being dropped,
    -- and the row's own are put back by the layout on the next tick.
    if hotLit ~= nil and hotLit.box ~= nil and hotLit.box.parent ~= nil then paint(hotLit, false) end
    hot, hotLit = nil, nil
    unmuteCards()
    if g then drop(g) end
  end

  -- =====================================================================
  -- TAKING THE PRESS OFF THE RUNTIME, which is the third and final shape of this modal.
  --
  -- MEASURED, by watching a real click arrive in the live game with a Runtime touch logger
  -- installed and two probe objects under the cursor - one on the stage, one six groups deep inside
  -- the pause menu:
  --
  --   * `began` is dispatched to EVERY object the press is over: both probes got it;
  --   * every LATER phase goes to the one object that owns the touch, and the owner is the game's
  --     own Coromon card under the point - neither probe got the `ended`;
  --   * a Runtime-level `touch` listener receives every phase of every press, whoever owns it.
  --
  -- Everything else in this feature reacts on `ended` - as does every button in the game - so an
  -- object-level catcher whatever its kind responds only where it happens to own the touch, which
  -- is precisely what was reported: the yes/no boxes and the rest of the modal answer only where
  -- nothing clickable is under them, and a card takes the press instead.
  --
  -- The Runtime listener removes that dependency entirely. It is registered while the modal is up,
  -- and the answer is decided from the press POSITION - which the modal was already doing - using
  -- `xStart`/`yStart`: measured, those are the CONTENT coordinates of the press (the same event
  -- carries `x`/`y` as screen PIXELS - a press at content 200,180 arrives as x=1000 y=900 with
  -- xStart=200).
  --
  -- `f.pressed` makes a press count only if its own `began` was seen, so the press that OPENED the
  -- modal - whose `began` went to the chip - can never be read back as an answer to it.
  --
  -- TWO DEFENCES, because position alone was not enough. The position test (a press must START
  -- inside the panel) catches a click-through whenever the engine says where the press started. The
  -- TIME guard in `openInput` catches it when the engine says nothing about position at all, or when
  -- a split/replayed sequence arrives with coordinates that happen to be inside the panel: nothing is
  -- answered in the first 300 ms the dialog is up. Reported twice by the player as "the prompt is
  -- dismissed immediately", once after an overlay reload.
  --
  -- THE CARDS ARE MUTED while this listener is up, because the engine still delivers the press to
  -- them and the owner still acts on it: that is what stops the click that opens the modal from
  -- counting as a click on a Coromon as well.
  --
  -- WHAT WAS TRIED AND DOES NOT WORK - not again: a plain full-screen rect (any alpha); the game's
  -- own backdrop object from `rectHelper:newFullScreenContainer`; the same object inserted by hand
  -- into the pause menu's own group six levels down, above the cards; `isHitTestable = true` or
  -- false on any of them; and `keepOnTop` on every tick, which only proves the modal really is the
  -- topmost stage child (measured: 14 stage children, the modal is the 14th). None of it matters,
  -- because the phases after `began` follow the owner and not the display list.
  -- =====================================================================
  local function openInput(g, geo)
    closeInput()

    -- A PRESS THAT ARRIVES TOO SOON AFTER THE DIALOG APPEARS IS THE CLICK THAT OPENED IT, however
    -- the engine split, repeated or replayed that click into touch sequences. This is the SECOND
    -- line of defence and the one that does not care what the event says: the position test below
    -- needs a press that STARTS inside the panel to fail, and it cannot fail at all for an event
    -- that carries no coordinates - where the old code had to assume the press was good, which is
    -- exactly the assumption that let the opening click answer the dialog it had just opened.
    --
    -- 300 ms is far longer than a repeated sequence of one click (those arrive within a few ms) and
    -- far shorter than reaching for a button. When the engine is old enough not to answer
    -- `system.getTimer` the guard disables itself rather than blocking presses.
    local GUARD_MS = 300
    local openedMs = 0
    pcall(function() openedMs = tonumber(system.getTimer()) or 0 end)
    -- What the guard saw, for `--status`: "ignored" here means the dialog was protected, not stuck.
    local function tooSoon()
      if openedMs <= 0 then return false end
      local now = 0
      pcall(function() now = tonumber(system.getTimer()) or 0 end)
      if now <= 0 then return false end
      local dt = now - openedMs
      if dt < GUARD_MS then
        f.last = string.format('confirm: press %d ms after opening -> ignored', dt)
        return true
      end
      return false
    end

    -- A press, in the dialog's own coordinates. `xStart`/`yStart` are the CONTENT coordinates of
    -- the press (measured: a press at content 200,180 arrives as x=1000 y=900 with xStart=200), so
    -- the same `contentToLocal` the answer uses converts them.
    local function localPoint(ev)
      local cx = tonumber(ev.xStart)
      local cy = tonumber(ev.yStart)
      if cx == nil or cy == nil then return nil end
      local ok, x, y = pcall(function() return g:contentToLocal(cx, cy) end)
      if not ok or tonumber(x) == nil or tonumber(y) == nil then return nil end
      return tonumber(x), tonumber(y)
    end

    local function onTouch(event)
      if type(event) ~= 'table' then return end
      if f.overlay ~= g then closeInput() return end
      local phase = event.phase

      -- WHERE THE PRESS STARTED NOW MATTERS AS MUCH AS WHERE IT ENDED, and that is the fix for a
      -- prompt that was sometimes dismissed the instant it appeared.
      --
      -- `f.pressed` alone cannot catch that: it only proves this listener saw a `began`, and one
      -- physical click can arrive as more than one touch sequence - the click that OPENED the modal
      -- among them, whose `began` therefore comes AFTER this listener exists. That press is on the
      -- chip, but the ANSWER is read from the `ended`, and a stray `ended` is all it takes. So the
      -- `began` is now converted into the dialog's coordinates and only a press that STARTED inside
      -- the dialog may answer: a click-through from the button underneath can never be one, however
      -- the engine splits or repeats the sequence.
      if phase == 'began' then
        -- THE OPENING CLICK ITSELF, whatever it claims about where it started.
        if tooSoon() then f.pressed = nil return end
        local x, y = localPoint(event)
        if x == nil then
          -- No start coordinates to judge by: fall back to the old behaviour rather than refuse
          -- presses outright, which would leave the dialog unanswerable. The time guard above has
          -- already rejected the only press this could have been wrong about.
          f.pressed = true
          return
        end
        local inside = (math.abs(x) <= (tonumber(geo.pw) or 0) / 2 + 6)
          and (math.abs(y) <= (tonumber(geo.ph) or 0) / 2 + 6)
        if inside then
          f.pressed = true
        else
          f.pressed = nil
          f.last = string.format('confirm: press began at %.0f,%.0f (outside) -> ignored', x, y)
        end
        return
      end
      -- A cancelled press is not an answer, and must not leave `pressed` set for the next one.
      if phase == 'cancelled' then
        f.pressed = nil
        return
      end
      if phase ~= 'ended' or not f.pressed then return end
      f.pressed = nil
      -- ...and a press that ENDS too soon is the tail of that same click: a sequence split in two,
      -- or an `ended` that reached a freshly installed listener. See `tooSoon` above.
      if tooSoon() then return end
      -- CONTENT coordinates of the press, per the measurement above.
      local cx = tonumber(event.xStart) or tonumber(event.x)
      local cy = tonumber(event.yStart) or tonumber(event.y)
      if cx == nil or cy == nil then return end
      -- ...into the frame the pictures were placed in, which is the group's own. Measured: the
      -- yes button sits at content (202,172) and reads as (-54,28) there - the coordinates it was
      -- built with - while reading as (202,172) in a catcher-box frame, which is what the first
      -- attempt compared against and why it matched nothing.
      local ok, x, y = pcall(function() return g:contentToLocal(cx, cy) end)
      if not ok or tonumber(x) == nil or tonumber(y) == nil then return end
      x, y = tonumber(x), tonumber(y)
      local bw, bh, by, yesX, noX = geo.bw, geo.bh, geo.by, geo.yesX, geo.noX
      local slack = geo.slack
      -- Recorded before the answer runs, so `--status` can tell "no press was seen" from "a press
      -- was seen and swallowed" - the difference between never being asked and answering wrong.
      f.last = string.format('confirm: press %.0f,%.0f -> ', x, y)
      if math.abs(y - by) <= bh / 2 + slack then
        if math.abs(x - yesX) <= bw / 2 + slack then
          f.last = f.last .. 'yes'
          return geo.thenFn()
        end
        if math.abs(x - noX) <= bw / 2 + slack then
          f.last = f.last .. 'no'
          return closeOverlay()
        end
      end
      -- Anywhere else on the screen: swallowed, and the modal stays up.
      f.last = f.last .. 'outside the buttons'
    end
    f.inputFn = onTouch
    pcall(function() Runtime:addEventListener('touch', onTouch) end)
  end

  -- =====================================================================
  -- One confirm dialog for both destructive buttons - save-over and clear. The QUESTION is the only
  -- thing that differs, so it is the only thing that is a parameter: the panel, the two buttons,
  -- the positional decision and the card muting are all the same, and two copies of them would be
  -- two places for the press handling to drift.
  --
  -- `line` is the one being destroyed, `question` the one being asked about it, and `thenFn` runs
  -- only on a yes.
  -- =====================================================================
  local function askConfirm(slot, line, question, thenFn)
    closeOverlay()
    local g = display.newGroup()
    keepOnTop(g)

    local w, h = 300, 116
    local vw = tonumber(display.contentWidth) or 512
    local vh = tonumber(display.contentHeight) or 288
    local bw, bh = 92, 30                 -- what the buttons ARE for the press, art included
    local by = h / 2 - 30                 -- their centreline, in the group's own coordinates
    local yesX, noX = -54, 54
    local x0, y0 = -vw / 2 - 20, -vh / 2 - 20
    local dialogBox = {}

    -- The pictures. Every one of them is marked non-hit-testable, so none of them can answer a
    -- press, and none of them is registered either - the press is taken from the Runtime listener.
    untouchable(rect(g, x0, y0, vw + 40, vh + 40, { 0, 0, 0, 0.35 }))
    untouchable(rect(g, -w / 2, -h / 2, w, h, COL.panel))
    untouchable(text(g, FONT, line, -w / 2 + 14, -h / 2 + 18, COL.text))
    untouchable(text(g, FONT, question, -w / 2 + 14, -h / 2 + 36, COL.dark))
    for _, s in ipairs({ { yesX, 'yes', COL.active }, { noX, 'no', COL.load } }) do
      local box = rect(g, s[1] - bw / 2, by - bh / 2, bw, bh, s[3])
      untouchable(box)
      untouchable(text(g, FONT, s[2], s[1] - 12, by - 6, COL.text))
      dialogBox[s[2]] = box
    end

    g.x, g.y = vw / 2, vh / 2
    f.overlay, f.slot = g, slot
    -- The two buttons light up under the cursor while the modal is up, in the modal's own
    -- coordinates, which are the ones they were built in.
    setHot(g, {
      { key = 'dialog:yes', x0 = yesX - bw / 2, y0 = by - bh / 2, x1 = yesX + bw / 2, y1 = by + bh / 2,
        box = dialogBox.yes, base = COL.active, lit = litColour(COL.active) },
      { key = 'dialog:no', x0 = noX - bw / 2, y0 = by - bh / 2, x1 = noX + bw / 2, y1 = by + bh / 2,
        box = dialogBox.no, base = COL.load, lit = litColour(COL.load) },
    })
    -- Set before the input goes on, so a press arriving in the same frame finds a live modal.
    -- `pw`/`ph` are the PANEL's box: a press has to start inside it to count as an answer, which is
    -- what stops a click-through from the chip underneath dismissing the dialog (see openInput).
    openInput(g, { bw = bw, bh = bh, by = by, yesX = yesX, noX = noX, slack = 6, thenFn = thenFn,
      pw = w, ph = h })
    muteCards()
  end

  local function askOverwrite(slot, thenFn)
    askConfirm(slot, 'slot ' .. slot .. ' already holds a group.',
      'Replace it with the squad you have now?', thenFn)
  end

  local function askClear(slot, thenFn)
    askConfirm(slot, 'slot ' .. slot .. ' holds a group.',
      'Clear it? The squad on screen is not touched.', thenFn)
  end

  -- =====================================================================
  -- Saving and loading a slot.
  -- =====================================================================
  local function saveSlot(n)
    f.slots[n] = loadoutSnapshot()
    local ok, path = loadoutWrite(f.slots, SLOTS)
    f.path = path or f.path
    f.last = string.format('saved slot %d (%d Coromon)%s',
      n, #f.slots[n], ok and '' or ' - FILE NOT WRITTEN: ' .. tostring(path))
    toast(f.last)
  end

  -- ASK THE GAME TO REBUILD ITS OWN LIST, so a load is visible at once instead of only after the
  -- pause menu is closed and opened again. Declared before loadSlot, because in Lua a local
  -- referenced above its own declaration is a GLOBAL - nil at the call.
  --
  -- Measured: after a swap the cards lag one step behind the squad (cards `4 735 774 813 761 760`
  -- against squad `735 4 774 813 761 760`), and calling `refreshTallMonsterButtons` makes them
  -- agree - 6 cards, no duplicates, in the right order, and repeatable.
  local function refreshCards()
    local screen = f.screen
    if type(screen) ~= 'table' then return false end

    local roots = { screen }
    if type(_G.pauseMenu) == 'table' then
      roots[#roots + 1] = _G.pauseMenu
      local ok, inst = pcall(function() return _G.pauseMenu:isCreated() end)
      if ok and type(inst) == 'table' then roots[#roots + 1] = inst end
    end
    -- The cards too: the drag handlers that call the refresh live on them, so this is the
    -- shortest route to the closure in the common case.
    local okRows, rows = pcall(function() return screen:getTallMonsterButtons() end)
    if okRows and type(rows) == 'table' then
      for _, r in pairs(rows) do
        roots[#roots + 1] = r
        for i = 1, (type(r) == 'table' and (r.numChildren or 0) or 0) do
          roots[#roots + 1] = r[i]
        end
      end
    end

    local fn = loadoutFindUpvalue(roots, 'refreshTallMonsterButtons', 1500)
    if type(fn) ~= 'function' then return false end
    return (pcall(fn))
  end

  local function loadSlot(n)
    if f.hidden or f.overlay then return end   -- a popup or our own modal is up; ignore presses
    local added, msg = loadoutApply(n, f.slots[n])
    local cards = refreshCards() and ' cards refreshed' or ''
    f.last = 'load ' .. tostring(msg):gsub('\n', ' | ') .. cards
    toast(msg)
    -- The game's rebuild may have replaced the container the chips are drawn in. One extra tick
    -- of our own, immediately, so they are back in the same frame rather than up to 250 ms later.
    if f.update then pcall(f.update) end
  end

  f.save, f.load = saveSlot, loadSlot       -- reachable from the console / the bridge
  -- The messenger too: the same way in, for anything that wants to say something in this style
  -- (the probes use it to check the panel against messages of awkward lengths).
  f.notify = toast

  local function onSave(n)
    if f.hidden or f.overlay then return end   -- a popup or our own modal is up; ignore presses
    if type(f.slots[n]) == 'table' and #f.slots[n] > 0 and CONFIRM then
      askOverwrite(n, function() closeOverlay() saveSlot(n) end)
      return
    end
    saveSlot(n)
  end

  -- CLEARING A SLOT: the slot is forgotten, and nothing else is touched - the squad on screen stays
  -- as it is, which is why the question says so. `nil` rather than an empty list is what the rest of
  -- the file already treats as "nothing saved" (`#f.slots[n] > 0` decides a slot's colour), and
  -- `loadoutWrite` writes `n = ` with nothing after it for both, so the file stays in the shape
  -- `loadoutRead` parses.
  local function clearSlot(n)
    f.slots[n] = nil
    local ok, path = loadoutWrite(f.slots, SLOTS)
    f.path = path or f.path
    f.last = string.format('cleared slot %d%s', n,
      ok and '' or ' - FILE NOT WRITTEN: ' .. tostring(path))
    toast(f.last)
  end

  -- ALWAYS ASKED, unlike save-over: replacing a slot at least puts something in its place, while a
  -- clear is the only button here that destroys a group outright. That also means it does not ride
  -- on `confirm_overwrite` - a player who turned that off did not ask for silent destruction.
  local function onClear(n)
    if f.hidden or f.overlay then return end   -- a popup or our own modal is up; ignore presses
    if type(f.slots[n]) ~= 'table' or #f.slots[n] == 0 then
      f.last = string.format('slot %d is already empty', n)
      return
    end
    askClear(n, function() closeOverlay() clearSlot(n) end)
  end

  -- Exported HERE and not next to `f.save`/`f.load`, which are declared above this function: a
  -- local referenced above its own declaration is a GLOBAL, so `f.clear = clearSlot` up there would
  -- have assigned nil without raising anything (the same trap `refreshCards` carries a note about).
  f.clear = clearSlot

  -- =====================================================================
  -- The chips. One row per Coromon list container: built once, then re-laid-out and recoloured
  -- every tick, which is a dozen arithmetic operations and a dozen assignments - so it follows
  -- the list if the screen moves, re-lays it out, or grows from one card to six.
  --
  -- THE FIRST ATTEMPT WAS WRONG, and the numbers that say so are worth keeping. It centred the
  -- row on the list container's origin and sized it from the container's width. Measured on the
  -- live pause menu:
  --
  --   the visible area, in the parent's own coordinates   x 0..512, y 0..288
  --   the list container's origin in those coordinates    (0, 0)
  --   the container's declared box                        423 x 150
  --   the cards inside it                                 128 x 150, centred at y 140
  --
  -- So the container's origin is the top-left CORNER OF THE SCREEN, not the middle of anything,
  -- and a 520-wide row centred there put 260 units of itself off the left edge - which is exactly
  -- what happened on screen: slots 4, 5 and 6 visible, 1 to 3 gone. And the container's own box
  -- is not the list's box: 150 tall, while the cards inside it sit at y 140 with height 150, so
  -- its top edge is 140 units ABOVE the top of any card.
  --
  -- So the list is measured from the ROWS instead - the union of the container's row children,
  -- read straight off their x/y/width/height, which are already in the container's coordinates -
  -- and placed by converting that box into the parent's coordinates through localToContent and
  -- contentToLocal. Every position is then clamped inside the visible area, so the chips cannot
  -- leave the screen whatever the list does. Nothing here is hard-coded to one screen size.
  -- =====================================================================
  -- A point in `node`'s own coordinates, expressed in the HOST's coordinates. Two transforms,
  -- both PCall'd, because either end can be a disposed object while a screen is going away.
  local function pointIn(node, x, y, host)
    local okA, cx, cy = pcall(function() return node:localToContent(x, y) end)
    if not okA or not tonumber(cx) then return nil end
    cx, cy = tonumber(cx), tonumber(cy)
    if host == nil then return cx, cy end
    local okB, hx, hy = pcall(function() return host:contentToLocal(cx, cy) end)
    if not okB or not tonumber(hx) then return nil end
    return tonumber(hx), tonumber(hy)
  end

  -- The VISIBLE AREA in the host's own coordinates. Measured on the live pause menu, this came
  -- back as x 0..512, y 0..288 while the Coromon list's container sits at the origin - so the
  -- container's own position says nothing about where the screen starts, and the first attempt's
  -- "centre on the container" put 260 units of the chip row off the left edge. Everything is
  -- clamped into this box now, so the chips cannot leave the screen whatever the list does.
  local function visibleBox(host)
    local l, t = pointIn(host, 0, 0, nil)
    local r, b = pointIn(host, display.contentWidth, display.contentHeight, nil)
    if l and r and r > l and b > t then return l, t, r, b end
    return 0, 0, tonumber(display.contentWidth) or 512, tonumber(display.contentHeight) or 288
  end

  -- The box of the Coromon list's ROWS, in the container's coordinates: the union of the children
  -- that are rows. Read straight off their x/y/width/height, which are already in that space, so no
  -- transform is involved. The container's declared box is deliberately NOT used for the TOP edge -
  -- measured, it is 150 tall while the cards inside it sit at y 140 with height 150, so its top edge
  -- is 140 units above the top of any card.
  --
  -- ONE ROW IS ENOUGH, and that is the fix for the row vanishing when a one-Coromon group is loaded.
  -- This used to refuse to measure anything below two rows, because it was also the source of the
  -- row's WIDTH and one row is not a width. It is not the source of the width any more - that comes
  -- from the container - so all it has to answer is where the top edge is, and one row answers that.
  -- What it must never do is fall back to a single remembered row: measured in that exact state,
  -- the bar's group sat at content y -24, ABOVE the top of the screen, invisible and looking for all
  -- the world like a row that was never built. The rows that are live now are the only ones that can
  -- say where the list is now.
  local function listSpan(container)
    local minX, maxX, minY, count
    for i = 1, (container.numChildren or 0) do
      local c = container[i]
      if type(c) == 'table' and type(c.monster) == 'table' then
        local hw = (tonumber(c.width) or 0) / 2
        local hh = (tonumber(c.height) or 0) / 2
        local cx, cy = tonumber(c.x) or 0, tonumber(c.y) or 0
        local l, r, t = cx - hw, cx + hw, cy - hh
        if minX == nil or l < minX then minX = l end
        if maxX == nil or r > maxX then maxX = r end
        if minY == nil or t < minY then minY = t end
        count = (count or 0) + 1
      end
    end
    if (count or 0) < 1 then return nil end
    return minX, minY, maxX
  end

  local GAP = 8               -- between the chips
  local ICON_GAP = 2          -- between a chip and its icon row
  -- The most icons a slot can usefully show: exactly what a load would put in the squad, which is
  -- the game's own maximum. A slot saved while the squad was over-full holds more entries than
  -- that, and drawing them all would promise a group that cannot be loaded.
  local MAX_ICONS = loadoutSquadMax()

  -- Up to six icons per slot - a squad is six - each the game's own 24x24 atlas frame scaled to
  -- the row's height. A saved Coromon that is no longer owned gets a dim block instead, so the
  -- slot still shows how many it held rather than quietly looking shorter.
  --
  -- Declared BEFORE rebuildRow, which calls it: a local referenced above its own declaration is a
  -- global, which is nil at the call.
  --
  -- EACH ICON IS DRAWN AS A PAIR, IN ASCENDING ORDER, AND THAT ORDER IS THE Z-ORDER: position 1's
  -- frame, then position 1's Coromon, then position 2's frame, position 2's Coromon, and so on. In a
  -- group the later child draws over the earlier one, so one icon - its frame AND its sprite - always
  -- sits above the icon to its left, and a sprite sits above its own frame.
  --
  -- IT IS DELIBERATELY NOT "all frames first, then all sprites". That reads well on paper - every
  -- sprite over every frame - but it puts a LEFT icon's sprite above a RIGHT icon's frame, which is
  -- exactly backwards for this row: the rule is that an icon wins over everything to its left.
  -- Frames and sprites really do overlap their neighbours (a frame is drawn +3.5 texels right of its
  -- own sprite, the game's own cell convention, and a cell is 9.6 units at an 8.8-unit pitch), so
  -- the order is visible and must stay exactly this: frame i, sprite i, frame i+1, sprite i+1...
  --
  -- `slot.frames[i]` and `slot.icons[i]` are PARALLEL ARRAYS INDEXED BY POSITION 1..MAX_ICONS (nil
  -- where a position holds nothing), which is what lets the layout walk a fixed grid without caring
  -- what is filled: an empty position has a frame and no sprite, a Coromon that is gone has a dim
  -- block and no frame (there is no type to show), a real one has both.
  local function clearGroup(group)
    for i = (group.numChildren or 0), 1, -1 do
      local c = group[i]
      if type(c) == 'table' then pcall(function() c:removeSelf() end) end
    end
  end

  local function rebuildIcons(slot, list)
    local group = slot.iconGroup
    clearGroup(group)
    slot.frames, slot.icons, slot.missing = {}, {}, 0

    -- THE REST OF THE GRID IS FILLED WITH EMPTY FRAMES, so every slot draws MAX_ICONS frames
    -- whether it holds six Coromon, three, or none. A group of three then shows three icons and
    -- three neutral frames instead of three icons and a gap, and the row reads as one grid of
    -- frames rather than a ragged line of icons.
    --
    -- Same position, and therefore the same code path through the layout, as a real icon - which is
    -- why this is here rather than a separate strip of placeholders that could drift out of step.
    -- Only positions that hold nothing get one: a Coromon that is saved but gone keeps its dim
    -- block, which is a different statement. Without `empty_frames`, or with no art to draw,
    -- nothing is padded and the grid simply ends where the icons do. The padding APPENDS, which is
    -- what keeps the order left to right - the empty positions are to the right of the real ones.
    local function pad(from)
      if not EMPTY_FRAMES then return end
      for i = from, MAX_ICONS do
        local empty = loadoutEmptyFrame(group)
        if not empty then
          empty = rect(group, 0, 0, 17, 17, COL.empty)
          pcall(function() empty.anchorX, empty.anchorY = 0.5, 0.5 end)
        end
        slot.frames[i] = empty
      end
    end

    for i = 1, math.min(#list, MAX_ICONS) do
      local mon = loadoutFind(list[i].id)
      -- THE FRAME IS CREATED FIRST SO THAT IT IS INSERTED FIRST: it has to end up under the sprite
      -- of its OWN position. A position whose Coromon is gone has no frame at all - a frame says
      -- what TYPE the Coromon is, and there is nothing to say it about - so that one is removed
      -- again below rather than left behind.
      local frame = mon and loadoutTypeFrame(group, mon)
      local sprite = mon and loadoutAvatar(group, mon)
      if sprite then
        if not frame then
          frame = rect(group, 0, 0, 17, 17, COL.dark)
          pcall(function() frame.anchorX, frame.anchorY = 0.5, 0.5 end)
        end
        slot.frames[i] = frame
        slot.icons[i] = sprite
      else
        if frame then pcall(function() frame:removeSelf() end) end
        local block = rect(group, 0, 0, 24, 24, COL.dark)
        pcall(function() block.anchorX, block.anchorY = 0.5, 0.5 end)
        slot.missing = slot.missing + 1
        slot.icons[i] = block
      end
    end
    -- An empty list pads from 1, so a slot that holds nothing needs no case of its own.
    pad(math.min(#list, MAX_ICONS) + 1)
  end

  local function rebuildRow(bar)
    for _, slot in ipairs(bar.slots) do
      local list = bar.slotsets[slot.n]
      local filled = type(list) == 'table' and #list > 0
      local active = filled and loadoutMatches(list)
      local bg = filled and (active and COL.active or COL.filled) or COL.empty
      -- Kept on the record because the LAYOUT needs it too: an empty slot's clear button does
      -- nothing (onClear only reports that), so its face is left out of the hover list.
      slot.filled = filled
      pcall(function() slot.bg:setFillColor(bg[1], bg[2], bg[3]) end)
      -- THE NUMBER AND THE CLEAR LABEL FADE ON AN EMPTY SLOT, AND THEY FADE THE SAME WAY. Both
      -- buttons can do nothing there - load reports "slot n is already empty" and clear the same -
      -- so both are dimmed by ALPHA, which is one rule for both halves. The number used to be dimmed
      -- by swapping its fill colour instead, which is a different kind of "off" sitting next to the
      -- clear label's fade, and it read as brighter than it should.
      --
      -- Alpha, and not a black fill, because both fonts are OUTLINE fonts: a black fill leaves the
      -- black stroke behind and the word becomes a blob (measured, on clear before this).
      --
      -- SAVE STAYS BRIGHT: saving into an empty slot is the normal thing to do, not an error.
      pcall(function() slot.num:setFillColor(COL.text) end)
      pcall(function() slot.num.alpha = filled and 1 or 0.35 end)
      pcall(function() slot.clear.alpha = filled and 1 or 0.35 end)
      slot.num.text = tostring(slot.n)
      -- The icons are only rebuilt when the slot's contents actually change: they are image
      -- objects from the game's atlas, and recreating six of them per slot every tick would be
      -- pure waste. The signature is the identifiers, in order.
      local sig = ''
      if filled then
        local parts = {}
        for i = 1, math.min(#list, MAX_ICONS) do
          parts[#parts + 1] = tostring(list[i].id)
        end
        sig = table.concat(parts, ',')
      end
      if slot.sig ~= sig then
        slot.sig = sig
        pcall(rebuildIcons, slot, filled and list or {})
      end
    end
  end

  -- Geometry, recomputed and re-applied on every tick. It is a dozen arithmetic operations and a
  -- dozen assignments, and it means the row follows the list if the screen re-lays it out, grows
  -- to six cards after the first one appears, or moves - none of which needs a rebuild.
  local function layoutBar(bar)
    local host = bar.host
    local vl, vt, vr, vb = visibleBox(host)
    local visW = vr - vl

    -- THE LIST'S WIDTH COMES FROM THE CONTAINER, NOT FROM THE CARDS IN IT, and that is the fix for
    -- the six slots changing their total width with the size of the squad. Measured on the live
    -- pause menu with three Coromon in the squad:
    --
    --   the container's own box          content x   0 .. 387
    --   the union of the filled cards    content x  66 .. 312
    --
    -- The rows were what the row was measured from, so with three Coromon the chips came out at
    -- the 34-unit floor (382 -> 244 wide) and with six they came out 57 wide (382 wide) - the same
    -- six slots, a different total, depending only on how many Coromon were in the squad.
    --
    -- The empty Slot 4/5/6 placeholders are part of the list but are not rows, so no measurement of
    -- the row children can know how wide the list is. The container's box does, and it is the whole
    -- six-slot list, so that is what the row is sized from now.
    --
    -- Its own box is NOT used for the TOP edge: measured, the container is 150 tall while the cards
    -- sit at y 140 with height 150, so its top edge is 140 units above the top of any card. That
    -- still comes from the rows, with the single row as the fallback until a second one exists.
    local left, right, top
    do
      local cw = tonumber(bar.container.width) or 0
      if cw > 0 then
        local l = pointIn(bar.container, 0, 0, host)
        local r = pointIn(bar.container, cw, 0, host)
        if l and r and r > l then left, right = l, r end
      end
    end
    do
      local rowH = tonumber(bar.row.height) or 150
      local rowW = tonumber(bar.row.width) or 128
      local lx, ly = listSpan(bar.container)
      local _, ry
      if lx then
        _, ry = pointIn(bar.container, lx, ly, host)
      else
        _, ry = pointIn(bar.row, 0, -rowH / 2, host)
        if not left then
          local l2 = pointIn(bar.row, -rowW / 2, -rowH / 2, host)
          left = l2
          right = l2 and (l2 + rowW) or nil
        end
      end
      top = ry or top
    end
    left = left or (vl + 12)
    right = right or (vr - 12)
    top = top or (vt + 12)

    -- =====================================================================
    -- THE CHIPS SIT IN THE SAME COLUMNS AS THE COROMON SLOTS BELOW THEM. Slot 3's chip is slot 3's
    -- column, at the same width, the same gap and the same spacing - which is the whole point of
    -- the row.
    --
    -- MEASURED on the live pause menu, from the container's own children:
    --
    --   the six slot columns    x 66, 125, 184, 243, 302, 361   -> pitch 59
    --   an empty slot frame     56 wide                          -> gap 3
    --   a card                  128 wide, and consecutive cards OVERLAP by 69 units
    --
    -- AND EVERY CHILD IS ANCHORED 0.5, SO THOSE x's ARE THE SLOT CENTRES, NOT ITS LEFT EDGES. A
    -- slot frame is 56 wide, so slot 1 occupies content x 38 .. 94 and slot 6 ends at 389; the
    -- 128-wide card hangs over its neighbours on both sides because it is centred on the same
    -- point. Reading `child.x` as a left edge is what put every chip half a slot too far right -
    -- each chip's LEFT edge landed on its slot's CENTRE, which is visible immediately as a row
    -- that starts too far right and drifts further out of step towards the right of the list.
    --
    -- The width and the pitch still come from the slots, and the row is CENTRED on the first
    -- slot's centre - `slotX - chipW / 2` - so chip n is centred exactly on slot n. The old
    -- band-based layout stays as the fallback for the moment when no columns have been measured
    -- yet - the row can be built before the list has its six children.
    -- =====================================================================
    local slotPitch, slotW, slotX
    do
      local first, second, i = nil, nil, 0
      for c = 1, (bar.container.numChildren or 0) do
        local child = bar.container[c]
        if type(child) == 'table' then
          local cx = tonumber(child.x)
          if cx then
            i = i + 1
            if i == 1 then first = cx elseif i == 2 then second = cx end
            if slotW == nil and type(child.monster) ~= 'table' then
              slotW = tonumber(child.width)
            end
          end
        end
      end
      if first and second and second > first then slotPitch = second - first end
      if first then slotX = pointIn(bar.container, first, 0, host) end
    end
    if slotPitch and slotX then
      if slotW == nil then slotW = bar.slotW or (slotPitch - 3) end
      bar.slotPitch, bar.slotW = slotPitch, slotW
    else
      slotPitch, slotW, slotX = bar.slotPitch, bar.slotW, bar.slotX
    end
    if slotX then bar.slotX = slotX end

    local chipW, gap, total, x
    if slotPitch and slotW and slotX then
      chipW = math.floor(slotW)
      if chipW < 20 then chipW = 20 end
      gap = slotPitch - slotW
      if gap < 0 then gap = 0 end
      total = chipW * SLOTS + gap * (SLOTS - 1)
      -- slotX is a slot's CENTRE (see above), and the chips are laid out from this x as their
      -- LEFT edges, so the row starts half a chip to the left of the first slot's centre.
      x = slotX - chipW / 2
    else
      -- THE FALLBACK, and it is what the row used to be built from: as wide as the list, sticky so
      -- it cannot shrink while the list is being rebuilt, never wider than the screen.
      local band = right - left
      if band < 40 or band > visW then band = visW end
      if bar.band ~= nil and band < bar.band then
        band = bar.band
      else
        bar.band = band
      end
      if band > visW then band = visW end
      chipW = math.floor((band - GAP * (SLOTS - 1)) / SLOTS)
      if chipW > 80 then chipW = 80 end
      if chipW < 34 then chipW = 34 end
      gap = GAP
      total = chipW * SLOTS + gap * (SLOTS - 1)
      x = (left + right) / 2 - total / 2
      if x < vl + 6 then x = vl + 6 end
      if x + total > vr - 6 then x = vr - 6 - total end
    end
    local loadW = math.floor(chipW / 2) - 2
    -- Kept for `--status`: the row's own numbers, so a row that is not where it should be can be
    -- read out instead of guessed at.
    bar.chipW, bar.gap, bar.cards = chipW, gap, (bar.container.numChildren or 0)

    local y = top - OFFSET - CHIP_H

    -- THE ICON ROW LIVES IN THE GAP, so the buttons do not have to move up towards the screen's
    -- header to make room. Its height is what the gap allows (offset_y - 3), capped by icon_size,
    -- which is why raising offset_y makes the icons bigger.
    local iconH = math.min(ICON_SIZE, OFFSET - 3)
    bar.iconH = (ICONS and iconH >= 6) and iconH or 0

    -- =====================================================================
    -- EVERYTHING IS PLACED ON THE SCREEN'S PIXEL GRID, which is what stops the specks of the atlas
    -- frames ABOVE a saved Coromon's icon showing along the top of the strip.
    --
    -- MEASURED, and the user's own guess was right: the marks are not part of the icons. Zoomed 8x,
    -- an orange dash and a grey smudge sit a couple of screen pixels above each icon, belonging to
    -- whatever is next to that frame in the sheet. Nothing in the game sets `magFilter`/`minFilter` -
    -- that was searched for and there is not one use in the whole codebase - so every sprite is
    -- sampled with the default LINEAR filter, and linear filtering reads up to half a texel OUTSIDE
    -- the frame it was asked for. The game itself never notices because it places its art on whole
    -- screen pixels; the row here did not. Measured before this change, an icon's centre sat at
    -- screen y 292.5 - a half pixel - so the filter's outermost tap reached into the frame above.
    --
    -- One screen pixel is `1 / nativeScale()` content units (0.2 at this window), so that is the
    -- grid. Snapping moves anything by at most half a pixel, and it only has to be done where a
    -- sprite actually starts or ends: the row's own position, each slot's icon strip, and each cell
    -- inside it. The relative offsets in a cell are already whole pixels (the frame sits 7 screen
    -- pixels in from the sprite's centre), so they follow.
    -- =====================================================================
    local px = 1 / math.max(1, nativeScale())
    local function onGrid(v) return math.floor(v / px + 0.5) * px end

    pcall(function() bar.group.x, bar.group.y = onGrid(x), onGrid(y) end)

    -- THE BUTTON'S TWO LABELS ARE CENTRED IN THEIR OWN HALVES, not pinned 5 units in from each
    -- box's left edge. Each half is a button - the number sits in the load button (0 .. loadW) and
    -- "save" in the save button (loadW + 4 .. chipW) - so each label is centred on ITS box's
    -- middle. That has to be measured from the text's own width rather than inset by a constant:
    -- the number is one character (4 units) and "save" is four (21 units) in the game's font, so a
    -- fixed inset cannot centre both, and centring is what stops the pair drifting within the
    -- button when the chip's width changes with the list.
    --
    -- ON THE PIXEL GRID, because a text object placed on a half pixel is a text object the game
    -- draws blurred - the two widths are odd and even, so the exact centre can land on a half
    -- pixel. Snapping moves it at most half a pixel and it stays crisp.
    -- `origin` is the slot's own left offset and is passed IN rather than closed over: the loop's
    -- `sx` is declared inside the loop, so a function defined here that named `sx` would be reading
    -- a global (nil) and would silently throw inside the caller's pcall.
    local function centred(origin, boxLeft, boxW, obj)
      local tw = 0
      pcall(function() tw = tonumber(obj.width) or 0 end)
      return onGrid(origin + boxLeft + (boxW - tw) / 2)
    end
    -- ...and centered VERTICALLY as well, in a BAND of the button rather than the whole of it. The
    -- bands are what the split save button needs: save in 0 .. CHIP_H/2, clear in CHIP_H/2 .. CHIP_H.
    --
    -- Same reasoning as the horizontal one, and it is measured: the text object's box is 15 units
    -- tall and its ink is centred inside it (25 screen px of box above the "1" and 26 below), so
    -- centring the box centres the ink. That matters MORE here, because a band is 11 units and the
    -- box does not fit in it - at 0 .. 11 the box is asked for y = -2 and at 11 .. 22 for y = 9, so
    -- the box overhangs the chip on both labels while the INK lands where it should: 3 .. 8 and
    -- 14 .. 19, each 5 units of glyph in an 11-unit band. The box is invisible; only the ink shows.
    --
    -- `bandH` defaults to the whole chip, which is what the number in the load half uses.
    local function centredY(obj, bandTop, bandH)
      bandTop, bandH = bandTop or 0, bandH or CHIP_H
      local th = bandH
      pcall(function() th = tonumber(obj.height) or bandH end)
      return onGrid(bandTop + (bandH - th) / 2)
    end
    local saveW = chipW - loadW - 4
    local halfH = CHIP_H / 2

    for _, slot in ipairs(bar.slots) do
      local sx = (slot.n - 1) * (chipW + gap)
      pcall(function() slot.bg.x, slot.bg.width = sx, chipW end)
      pcall(function() slot.loadBox.x, slot.loadBox.width = sx, loadW end)
      -- The save button's two halves, each the full width of the save box and half its height.
      pcall(function() slot.saveBox.x, slot.saveBox.width = sx + loadW + 4, saveW end)
      pcall(function() slot.saveBox.y, slot.saveBox.height = 0, halfH end)
      pcall(function() slot.clearBox.x, slot.clearBox.width = sx + loadW + 4, saveW end)
      pcall(function() slot.clearBox.y, slot.clearBox.height = halfH, halfH end)
      pcall(function() slot.num.x = centred(sx, 0, loadW, slot.num) end)
      pcall(function() slot.num.y = centredY(slot.num, 0, CHIP_H) end)
      pcall(function() slot.save.x = centred(sx, loadW + 4, saveW, slot.save) end)
      pcall(function() slot.save.y = centredY(slot.save, 0, halfH) end)
      pcall(function() slot.clear.x = centred(sx, loadW + 4, saveW, slot.clear) end)
      pcall(function() slot.clear.y = centredY(slot.clear, halfH, halfH) end)
      pcall(function() slot.iconGroup.x, slot.iconGroup.y = sx, CHIP_H + ICON_GAP end)
    end

    -- THE THREE BUTTON FACES THAT LIGHT UP UNDER THE CURSOR, in the row's own coordinates: the
    -- row's group origin IS the left edge of chip 1, so a face's rectangle is the same `sx` maths
    -- the boxes were just placed with. Rebuilt here rather than kept from the build, because the
    -- row moves with the list and a stale rectangle would light the wrong button.
    do
      local spots = {}
      for _, slot in ipairs(bar.slots) do
        local ax = (slot.n - 1) * (chipW + gap)
        -- LOAD AND CLEAR ARE ONLY HOVERABLE ON A FILLED SLOT: on an empty one they do nothing but
        -- report that it is empty, so neither is in the list at all - which also means nothing
        -- lights there if the slot is emptied while the cursor sits over it.
        if slot.filled then
          spots[#spots + 1] = { key = 'load:' .. slot.n, box = slot.loadBox, base = COL.load,
            lit = litColour(COL.load), x0 = ax, y0 = 0, x1 = ax + loadW, y1 = CHIP_H }
        end
        spots[#spots + 1] = { key = 'save:' .. slot.n, box = slot.saveBox, base = COL.save,
          lit = litColour(COL.save), x0 = ax + loadW + 4, y0 = 0, x1 = ax + chipW, y1 = halfH }
        if slot.filled then
          spots[#spots + 1] = { key = 'clear:' .. slot.n, box = slot.clearBox, base = COL.clear,
            lit = litColour(COL.clear), x0 = ax + loadW + 4, y0 = halfH, x1 = ax + chipW,
            y1 = CHIP_H }
        end
      end
      -- NOT WHILE THE ROW IS HIDDEN (a popup is over it) - a hidden row must not leave a face lit
      -- for when it returns. WHILE THE MODAL IS UP the row LEAVES THE LIST ALONE: the modal owns it
      -- then, and this function runs every tick, so clearing here would put the dialog's button out
      -- again the moment it was lit (measured: it did exactly that, the yes button sampling at its
      -- base colour while the cursor was over it).
      if f.overlay ~= nil then
        -- the modal's own two buttons are the ones that light up
      elseif f.hidden then
        setHot(nil, nil)
      else
        setHot(bar.group, spots)
      end
    end

    -- =====================================================================
    -- The icons themselves: ONE SIZE FOR EVERY ICON IN EVERY SLOT, at an INTEGER scale, in a fixed
    -- grid of MAX_ICONS cells that fills the button.
    --
    -- What was wrong, and it was all three of those:
    --
    --   * the size came from the space the slot's own icons needed -
    --     `size = min(iconH, (chipW - (n-1)) / n)` - so a slot holding six saved Coromon drew
    --     smaller icons than a slot holding two, and the next slot drew a third size again;
    --   * the strip was CENTRED in the slot, so its WIDTH depended on the count and every icon
    --     MOVED when a Coromon was added: five 9.6-wide frames centred in a 56-wide chip start
    --     half a frame further left than six of them do. The grid below is centred instead, and a
    --     cell's place in it does not depend on how many cells are filled;
    --   * the scale was that size over 24 - 8.67/24 for six icons - which resamples the pixel art
    --     by a fraction and makes the icons look uneven.
    --
    -- ONE CELL IS ONE FRAME, so the spacing IS the frame: measured live, a cell is the avatar
    -- sprite at 24x24 texture pixels with the type container (17x17, at +3.5,+3.5) inside that box,
    -- so at scale `iconSize / 24` its footprint is exactly `iconSize` and consecutive cells abut.
    --
    -- The atlas frames are 24 texture pixels across and the game draws content at 5 screen pixels
    -- per unit (measured: `display.contentToScreenScale`), so ONE TEXTURE PIXEL COUNTS ONE SCREEN
    -- PIXEL at 24/5 = 4.8 content units, two at 9.6, three at 14.4. Stepping in those units is what
    -- keeps the art on the pixel grid, so the size is the largest multiple of 4.8 that fits both
    -- the strip's height and six icons (a full squad) across the slot.
    -- =====================================================================
    if bar.iconH > 0 then
      local unit = 24 / math.max(1, nativeScale())          -- content units per texture pixel
      local factor = math.floor((bar.iconH + 0.001) / unit)
      -- Six of them have to fit across a slot - a slot plus its gap is `chipW + gap` wide, so a
      -- slot's icons may use that much and no more.
      local widest = math.floor((chipW + gap) / (MAX_ICONS * unit))
      if factor > widest then factor = widest end
      if factor < 1 then factor = 1 end
      bar.iconSize = unit * factor                          -- 9.6 at this screen scale: two pixels
      -- THE MULTIPLIER GOES IN AFTER THE CLAMPS, on purpose. The clamps are what fit six icons
      -- across a slot; `icon_scale` is the knob that overrides that fit, so the icons grow past
      -- their grid and overlap each other. It is a test and a preference knob, not a layout one:
      -- the SPACING below is still the measured 44 px pitch, so a doubled icon spills half its
      -- width over each neighbour rather than pushing its neighbours aside.
      local scaleMult = tonumber(ICON_SCALE) or 1
      if scaleMult < 0.25 then scaleMult = 0.25 end
      if scaleMult > 4 then scaleMult = 4 end
      bar.iconSize = unit * factor * scaleMult
      bar.iconScale = scaleMult
      bar.iconFactor = factor
      local scale = bar.iconSize / 24
      -- ==================================================================
      -- THE CELLS' SPACING IS SET IN SCREEN PIXELS, because that is the unit the frames are
      -- measured in, and it is the only unit in which they come out even.
      --
      -- MEASURED on the live pause menu, with the cell (24x24 texels) drawn at 2 screen px per
      -- texel and the type container (17x17, at +3.5 texels) drawn inside it:
      --
      --   the frame's VISIBLE box is 30 px wide            (15 texels)
      --   it is centred 7 px RIGHT of the cell's centre    (the +3.5 texels, 3.5 x 2 = 7 px)
      --   so the cell's centre + [-8, +22] is what it draws
      --
      -- THAT IS WHAT WAS WRONG: at the old 48 px pitch (one cell = 9.6 units) the frames started
      -- 12 px into the 280 px button and the sixth ran 2 px past its end, because a cell's centre
      -- is not where its ink is. Now the INK is what is placed: 15 px in from the button's left
      -- edge, 30 px of frame, then 14 px to the next frame - a 44 px pitch, and 15 px left over at
      -- the right edge, so the six frames sit SYMMETRICALLY in the button.
      --
      -- The cell is still 9.6 units (48 px) wide, so consecutive cells OVERLAP by 4 px. That is
      -- deliberate and invisible: the frame is only 30 px of the 48, so the overlap is entirely
      -- inside the margins, and shrinking the art to an exact 44 px would need a scale of
      -- 44/24/5 = 0.3666, which resamples the pixel art. Keeping 0.4 keeps 2 screen px per texel.
      --
      -- IT FILLS THE BUTTON EXACTLY: 15 + 6x30 + 5x14 + 15 = 280. (An earlier count had an 8 px
      -- lead and a trailing 14 + 8, which added up to the same 280 but pushed the frames 7 px left
      -- and left 22 px of dead space at the right end.)
      -- ==================================================================
      local INK_PX, GAP_PX, LEAD_PX, SHIFT_PX = 30, 14, 15, 7
      local iconPitch = (INK_PX + GAP_PX) * px              -- 44 px = 8.8 units between cell centres
      local iconFirst = (LEAD_PX + INK_PX / 2 - SHIFT_PX) * px   -- the first cell's centre, 23 px in
      for _, slot in ipairs(bar.slots) do
        -- The slot's own left offset, recomputed here rather than carried over: it is the strip's
        -- position inside the row, and the row moves whenever the list does.
        local sx = (slot.n - 1) * (chipW + gap)
        pcall(function()
          slot.iconGroup.x, slot.iconGroup.y = onGrid(sx), onGrid(CHIP_H + ICON_GAP)
        end)
        -- THE FRAMES AND THE SPRITES ARE PLACED BY POSITION, from the two parallel arrays, so the
        -- grid is the same six cells under every chip whatever it holds. Frame i and sprite i share
        -- the same centre - the frame is offset by its own +3.5 texels (the game draws the type
        -- container bottom-right of the sprite), which at this scale is 1.4 units, 7 px, so it is
        -- re-applied here rather than baked into the builder: the builders no longer know about
        -- cells, and this is the one place that decides where a position's ink lands. Both go into
        -- ONE group (see rebuildIcons) - the drawing order is theirs, this is only their geometry.
        local FRAME_SHIFT = 3.5 * scale
        for i = 1, MAX_ICONS do
          local centre = iconFirst + (i - 1) * iconPitch
          -- Centres, and placed so that the FRAME lands where it was asked to (see the ink/pitch
          -- note above), which is why the first position's centre is 23 px in and not its own
          -- half-width. On the grid, because a sprite placed off it is a sprite whose filter
          -- reads the next frame.
          local frame = slot.frames and slot.frames[i]
          if frame then
            pcall(function()
              frame.xScale, frame.yScale = scale, scale
              frame.x = onGrid(centre + FRAME_SHIFT)
              frame.y = onGrid(bar.iconH / 2 + FRAME_SHIFT)
            end)
          end
          local sprite = slot.icons and slot.icons[i]
          if sprite then
            pcall(function()
              sprite.xScale, sprite.yScale = scale, scale
              sprite.x = onGrid(centre)
              sprite.y = onGrid(bar.iconH / 2)
            end)
          end
        end
      end
    end
  end

  local function buildRow(container, row)
    -- The parent of the container is the band the chips may sit in; it is the pause menu screen,
    -- and it is wider than the 423-wide list container.
    local host = container
    if type(container.parent) == 'table'
       and (tonumber(container.parent.width) or 0) > (tonumber(container.width) or 0) then
      host = container.parent
    end

    local g = display.newGroup()
    host:insert(g)
    pcall(function() g.anchorX, g.anchorY = 0, 0 end)

    local bar = {
      group = g, host = host, container = container, row = row,
      slots = {}, slotsets = f.slots, iconH = 0,
    }
    for n = 1, SLOTS do
      local x = (n - 1) * (80 + GAP)
      local bg = rect(g, x, 0, 80, CHIP_H, COL.empty)
      local loadBox = rect(g, x, 0, 38, CHIP_H, COL.load)
      -- THE SAVE BUTTON IS TWO BUTTONS: save on the top half, clear on the bottom. They are two
      -- rects rather than one with a hit test of my own, because every other press in this file
      -- goes through `touchable`, and a half-height rect is a hit test the ENGINE does.
      local saveBox = rect(g, x + 42, 0, 38, CHIP_H / 2, COL.save)
      local clearBox = rect(g, x + 42, CHIP_H / 2, 38, CHIP_H / 2, COL.clear)
      local num = text(g, FONT, tostring(n), x + 5, 6, COL.text)
      local sv = text(g, FONT, 'save', x + 47, 6, COL.dark)
      local cl = text(g, FONT, 'clear', x + 47, CHIP_H / 2 + 6, COL.dark)
      -- THE LABELS MUST NOT TAKE A PRESS, and this is measured rather than assumed: a label lying
      -- over a button DOES take the press (the note above `untouchable` records it - that is what
      -- once made a button respond only around its edges, "as if the target were smaller than the
      -- button"). The split save button makes that matter: its two halves are 11 units tall with a
      -- label in the middle of each, so a label that answers presses would leave the clear half
      -- pressable only above and below its own word.
      untouchable(num)
      untouchable(sv)
      untouchable(cl)
      -- The icon row is a group of its own, so a slot can be re-iconed without touching the
      -- buttons - and so the icons are removed with the row when the screen goes. ONE group, not a
      -- group per icon and not a layer per kind: the children are added as frame, sprite, frame,
      -- sprite... so an icon and its frame both sit above everything to their left. See
      -- rebuildIcons, which owns that order.
      local icons = display.newGroup()
      g:insert(icons)
      pcall(function() icons.anchorX, icons.anchorY = 0, 0 end)
      icons.x, icons.y = x, CHIP_H + ICON_GAP
      local rec = {
        n = n, bg = bg, loadBox = loadBox, saveBox = saveBox, clearBox = clearBox,
        num = num, save = sv, clear = cl,
        iconGroup = icons, frames = {}, icons = {}, sig = nil,
      }
      bar.slots[n] = rec
      rec.off1 = touchable(loadBox, nil, function() loadSlot(n) end)
      rec.off2 = touchable(saveBox, nil, function() onSave(n) end)
      rec.off3 = touchable(clearBox, nil, function() onClear(n) end)
    end
    rebuildRow(bar)     -- colours, and the icons for whatever is saved
    layoutBar(bar)      -- the real sizes and positions, from the list
    return bar
  end

  local function killBar(bar)
    for _, slot in ipairs(bar.slots) do
      if slot.off1 then pcall(slot.off1) end
      if slot.off2 then pcall(slot.off2) end
      if slot.off3 then pcall(slot.off3) end
    end
    drop(bar.group)
  end

  local function killRows()
    for _, bar in pairs(f.bars) do
      killBar(bar)
    end
    f.bars = {}
  end

  -- =====================================================================
  -- Finding the Coromon list, the same way squad.py does: the rows are caught as the game
  -- builds them, so nothing here walks the display list per tick.
  -- =====================================================================
  local function hookRows()
    local cls = package.loaded[ROW_CLASS]
    if type(cls) ~= 'table' or type(cls.new) ~= 'function' then return end
    if cls.__hudLoadoutsHooked then return end
    cls.__hudLoadoutsHooked = true
    local origNew = cls.new
    cls.new = function(...)
      local inst = origNew(...)
      local live = _G.__hud and _G.__hud.feats and _G.__hud.feats.loadouts
      if type(inst) == 'table' and type(live) == 'table' and type(live.rows) == 'table' then
        live.rows[inst] = true
      end
      return inst
    end
  end

  -- The rows ALREADY on screen when this installs, so the chips appear without needing a
  -- screen change first.
  --
  -- NOT by looking for a `.monster` field, which was the first attempt and is far too loose:
  -- measured on the live pause menu, 176 of the 4001 nodes under the stage carry one (every
  -- sprite, bar and label inside a card references the monster), so that test would have built
  -- a chip row for 176 nodes' parents. A row that is already built is instead asked for by the
  -- screen itself - the Coromon list screen exposes `getTallMonsterButtons`, which is the
  -- game's own answer and needs no matching at all. The class hook covers the rest.
  --
  -- One walk of the display list, once: the tree is 4001 nodes and it is skipped for the
  -- overworld's tile map, which can never hold a row and is still loaded behind the pause menu.
  local function seed()
    local screen = loadoutListScreen()
    if screen == nil then return 0, 'no Coromon list on screen' end
    local ok, rows = pcall(function() return screen:getTallMonsterButtons() end)
    if not ok or type(rows) ~= 'table' then return 0, 'the list would not name its rows' end
    local n = 0
    for _, row in pairs(rows) do
      if type(row) == 'table' then
        f.rows[row] = true
        n = n + 1
      end
    end
    f.screen = screen
    f.container = loadoutListContainer(screen)
    return n, nil
  end

  local function refresh()
    if not f.on then return end
    if next(f.bars) == nil then hookRows() end
    f.slots = f.slots or {}

    -- HIDDEN WHILE A POPUP IS UP. Selecting a card slides a dimming popup over the list, and the
    -- chips are children of the screen underneath it: they were showing through the dim, and a load
    -- clicked through a popup would be a real surprise. Hiding also makes the touchables unreachable
    -- (an invisible object is not hit-tested), and `f.hidden` is checked in the click handlers as
    -- well, so a press that started before the popup opened cannot land on one either.
    f.overlays = loadoutOverlayCount()
    f.hidden = (f.overlays ~= nil and f.overlays > 0)

    -- THE MODAL HAS TO STAY THE TOPMOST THING ON THE STAGE while it is up: the engine gives a
    -- press to the topmost hit-testable object under it, so a group the game inserts after ours
    -- would take the press the modal is there to take. keepOnTop is one comparison when we are
    -- already last, so this is free per tick and removes the whole question.
    if f.overlay then pcall(function() keepOnTop(f.overlay) end) end

    -- The list container: known from the screen, and re-asked if the screen was rebuilt.
    if f.container ~= nil and f.container.parent == nil then f.container = nil end
    if f.container == nil then
      f.container = loadoutListContainer(f.screen)
      if f.container ~= nil then f.bars = f.bars or {} end
    end

    for row in pairs(f.rows) do
      local container = type(row) == 'table' and row.parent or nil
      if type(row) ~= 'table' or row.parent == nil then
        f.rows[row] = nil
      elseif type(container) == 'table' then
        -- ONLY THE REAL LIST. A row the popup built is skipped here - its container is not the
        -- list's - so no second, squished chip row is drawn over the selected card. The container
        -- is remembered once, and until it is known a container is accepted only if its parent is
        -- the screen that owns the list, which is asked in O(1) rather than by a display walk.
        local isList = (container == f.container)
        if not isList and f.container == nil then
          isList = loadoutIsListContainer(container)
        end
        if isList and f.container == nil then f.container = container end
        if isList then
          -- KEPT FRESH, NOT SET ONCE - and that is a fix, not tidiness. The pause menu builds a NEW
          -- screen every time it is opened, and the old screen's parent becomes nil. With
          -- `f.screen = f.screen or container.parent` the first screen was kept forever, so from
          -- the second visit onwards `f.screen.parent` was nil, which the check at the end of this
          -- function reads as "the screen went" - and it closed the modal on the very next tick.
          -- That is the bug the player reported as "the click that opened the dialog also dismissed
          -- it": the dialog is a stage child and lives exactly one tick. `refreshCards` wanted this
          -- too - it asks the screen to rebuild its list, and a dead screen never answered.
          if type(container.parent) == 'table' then f.screen = container.parent end
          local bar = f.bars[container]
          if bar == nil then
            local ok, built = pcall(buildRow, container, row)
            if ok and built then
              bar = built
              f.bars[container] = bar
            end
          end
          if bar then
            -- Re-measured every tick, so the row follows the list rather than trusting the
            -- geometry from the moment the first card appeared (which is when a screen is still
            -- being built and may hold one card of six). Icons first, so anything newly created
            -- is positioned in the same pass.
            pcall(rebuildRow, bar)
            pcall(layoutBar, bar)
          end
        end
      end
    end
    -- The chips follow the popup state, so the row is not left showing through the dim while a
    -- card's popup is open.
    for _, bar in pairs(f.bars) do
      pcall(function() bar.group.isVisible = not f.hidden end)
    end
    -- The screen went: the chips were children of its container, so they went with it, and
    -- holding the table would keep a dead screen alive.
    for container, bar in pairs(f.bars) do
      if type(container) ~= 'table' or container.parent == nil then
        killBar(bar)
        f.bars[container] = nil
      end
    end
    -- And the modal goes with the screen rather than staying over whatever comes next: it is a
    -- stage child, so nothing else would remove it. ONLY A SCREEN WE KNOW ABOUT COUNTS: a nil
    -- `f.screen` means no list has been seen yet, which is not evidence that the screen went away,
    -- and treating it as such closed the dialog one tick after it opened.
    if f.overlay ~= nil and f.screen ~= nil and f.screen.parent == nil then closeOverlay() end
  end

  local function kill()
    killRows()
    closeOverlay()
    -- The hover listener is Runtime-level, so the feature has to take it with it.
    pcall(function() Runtime:removeEventListener('mouse', onMouseMove) end)
    hot, hotLit, hotCursor = nil, nil, nil
    if f.toast then drop(f.toast); f.toast = nil end
    f.last = 'off'
  end

  f.update, f.kill, f.on = refresh, kill, true
  hookRows()
  f.seeded, f.seedNote = seed()
  refresh()
end
"""
        .replace("__FONT__", _lua_string(font))
        .replace("__SLOTS__", str(int(cfg["slots"])))
        .replace("__OFFSET__", str(int(cfg["offset_y"])))
        .replace("__CHIP_H__", str(int(cfg["chip_height"])))
        .replace("__CONFIRM__", "true" if cfg["confirm_overwrite"] else "false")
        .replace("__TOAST_MS__", str(int(float(cfg["toast_seconds"]) * 1000)))
        .replace("__STORE__", _lua_string(cfg["store"]))
        .replace("__ICONS__", "true" if cfg["icons"] else "false")
        .replace("__ICON_SIZE__", str(int(cfg["icon_size"])))
        .replace("__ICON_SCALE__", "%g" % float(cfg["icon_scale"]))
        .replace("__ICON_FRAMES__", "true" if cfg["icon_frames"] else "false")
        .replace("__EMPTY_FRAMES__", "true" if cfg["empty_frames"] else "false")
        .replace("__C_EMPTY__", _lua_colour("empty"))
        .replace("__C_FILLED__", _lua_colour("filled"))
        .replace("__C_ACTIVE__", _lua_colour("active"))
        .replace("__C_LOAD__", _lua_colour("load"))
        .replace("__C_SAVE__", _lua_colour("save"))
        .replace("__C_CLEAR__", _lua_colour("clear"))
        .replace("__C_PRESS__", _lua_colour("press"))
        .replace("__C_PANEL__", _lua_colour("panel"))
        .replace("__C_DIM__", _lua_colour("dim"))
        .replace("__C_TEXT__", _lua_colour("text"))
        .replace("__C_DARK__", _lua_colour("dark_text"))
    )


def summary(cfg):
    # NOTE: these three return Lua that itself contains `%d`/`%s` inside string.format, so the
    # slot count goes in by REPLACEMENT rather than by Python's `%` operator. Using `%` here
    # makes Python claim the Lua specifiers as its own and raise "not enough arguments".
    return r"""(function()
  local s = loadoutRead()
  local used = 0
  for i = 1, __SLOTS__ do
    if type(s[i]) == 'table' and #s[i] > 0 then used = used + 1 end
  end
  return string.format('squad loadouts: %d slots, %d saved (%s)', __SLOTS__, used,
    tostring(loadoutPath() or 'NO FILE PATH'))
end)()""".replace("__SLOTS__", str(int(cfg["slots"])))


def status(cfg):
    return r"""(function()
  local f = _G.__hud and _G.__hud.feats.loadouts
  if not f or not f.on then return nil end
  local bars = 0
  local layout = 'none'
  for _, bar in pairs(f.bars or {}) do
    bars = bars + 1
    layout = string.format('chipW=%s gap=%s cards=%s', tostring(bar.chipW), tostring(bar.gap),
      tostring(bar.cards))
  end
  local used = 0
  for i = 1, __SLOTS__ do
    if type(f.slots[i]) == 'table' and #f.slots[i] > 0 then used = used + 1 end
  end
  return string.format('loadouts: %d/%d saved, %d row(s) [%s], seeded=%d%s, squad=%d/%d, overlays=%s hidden=%s modal=%s%s, file=%s, last=%s',
    used, __SLOTS__, bars, layout, f.seeded or 0,
    (f.seeded or 0) == 0 and (' (' .. tostring(f.seedNote or 'waiting for the list') .. ')') or '',
    #playerMonsters:getSquad(), loadoutSquadMax(),
    tostring(f.overlays), tostring(f.hidden), f.overlay and 'open' or 'closed',
    f.muted and (' (cards muted: ' .. #f.muted .. ')') or '',
    tostring(f.path or 'none'), tostring(f.last or 'idle'))
end)()""".replace("__SLOTS__", str(int(cfg["slots"])))


def report(cfg):
    return r"""(function()
  local s = loadoutRead()
  local out = {}
  for i = 1, __SLOTS__ do
    local list = s[i] or {}
    if #list > 0 then
      local parts = {}
      for _, e in ipairs(list) do
        parts[#parts + 1] = tostring(e.id) .. ':' .. tostring(e.item or '-')
      end
      out[#out + 1] = string.format('  slot %d (%d): %s', i, #list, table.concat(parts, ' '))
    end
  end
  local head = string.format('loadouts: %d saved in %s', #out,
    tostring(loadoutPath() or '???'))
  if #out == 0 then return head end
  return head .. '\n' .. table.concat(out, '\n')
end)()""".replace("__SLOTS__", str(int(cfg["slots"])))
