"""Items: the game's own item records, with their icons, their sprites and their stats.

THREE SOURCES, because no one of them has everything:

* THE RECORDS are `Resources/data/json/items.json` - 745 of them, each `{UID, name, category,
  categoryWithSubcategory, description, order, id}` - the same typed JSON the other tools here read.
  That gives the name, the category, the description and the game's own order, and NOTHING numeric:
  there is no stat field anywhere in the file. `load()` returns these, in the game's own order.

* THE ICONS are one PNG per item on disk, at `images/interface/icons/itemIcons/<variant>/<UID>.png` -
  the path the game builds itself (`abstractItem.getIconPath` concatenates
  "images/interface/icons/itemIcons/", `getIconUID()` and ".png"). Three variants exist -
  `withoutGlow`, `withGlow`, `withBrightGlow`, 362 files each - and this module reads the plain one,
  because the other two are the same picture with a highlight baked in. 272 of the 745 records have
  one (the rest are quest placeholders and cut content).

* THE STATS ARE ONLY IN THE LUA, in `resource.car`, so `stats()` and `base_catch_rates()` answer
  nothing at all when the game is not installed. A stat is an overridden method on
  `classes.items.<UID>`, and the base catch rates are a rarity table in `classes.items.abstractSpinner`.

HOW A STAT IS READ OFF A MODULE - the part worth knowing before touching this. An item class compiles
to a `new` body that attaches its overrides with a table assignment, so the bytecode for one is

    CLOSURE   Bx = <index of the method's own proto>
    SETTABLE  B  = 256 + <index of the method's NAME in this proto's constants>

and those two instructions, IN THAT ORDER, are the whole of it. So the reader walks the CLOSURE
instructions and takes each one's Bx as the body, and the next SETTABLE with a constant key (its B
carries a 256 flag) as the name - which is exact, and matters: pairing by position does not work
(SPINNER_REGULAR_2 names five things in its constant list but overrides only three, the other two
being calls to the base class), and searching for the proto that merely CONTAINS a name finds the
call site rather than the definition.

Measured against SPINNER_REGULAR_2, this yields `getGoldSellPrice = 300`, `getGoldCost = 600`,
`getCatchRateModifier = 1.5` - which is what the game charges and rolls with.

WHAT THE STATS MEAN, for the ones an item can carry:
  * `getCatchRateModifier` - the multiplier applied to the base catch rate for a wild Coromon of its
    own rarity (see `base_catch_rates`). An item with no override uses the base class's 1.0.
  * `getGoldCost` / `getGoldSellPrice` - what a shop charges and pays.
  * `getType` - which type an elemental spinner is efficient against.
  * `getAmountOfShakes` - the shakes the spinner needs, which is how the Platinum Spinner's
    "catches anything" is written: it has NO modifier at all, it overrides this instead. Anything
    reading these has to expect a stat to be missing rather than assume a number.
"""

import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(BASE), "Resources")
ITEMS_JSON = os.path.join(RES, "data", "json", "items.json")
MONSTERS_JSON = os.path.join(RES, "data", "json", "monsters.json")
CAR = os.path.join(RES, "resource.car")

ICON_ROOT = os.path.join(RES, "images", "interface", "icons", "itemIcons")
ICON_VARIANT = "withoutGlow"
# WHAT A SPINNER LOOKS LIKE WHEN IT IS THROWN, and what a gauntlet looks like when it is worn. Both
# are animation strips in the game's own folders, so a caller has to pick a frame; see `strip`.
SPINNER_SPRITES = os.path.join(RES, "images", "battle", "spinner")
GAUNTLET_PARTS = os.path.join(RES, "images", "characters", "human", "parts", "body", "gauntlet")

ITEM_CLASS = "classes.items."
ABSTRACT_SPINNER = ITEM_CLASS + "abstractSpinner.lu"

# Lua 5.1 opcodes used by the stat reader - CLOSURE is how a method body is referenced, SETTABLE how
# it is attached. THEY ARE LISTED WITH `luadis.OPNAMES`, NOT GUESSED: 36 is CLOSURE (30 is RETURN,
# which reads like the obvious number for it and silently matches nothing).
OP_LOADK, OP_SETTABLE, OP_CLOSURE = 1, 9, 36
# SETTABLE's B operand is an RK value: at or above this, the operand is a CONSTANT index instead of
# a register, which is the flag that says "the key of this assignment is a literal name".
RK_CONSTANT = 256

# The icon variants the game ships, plainest first - `icon_path` uses the first that exists.
VARIANTS = ("withoutGlow", "withGlow", "withBrightGlow")


class Item:
    """One item record, plus whatever the game's Lua says about it (read lazily, see `stats`)."""

    def __init__(self, raw):
        self.uid = raw.get("UID")
        self.name = raw.get("name") or raw.get("UID") or "?"
        self.category = raw.get("category") or "none"
        # "spinner.elemental", "recover.stats", or empty for the categories the game does not split
        self.subcategory = raw.get("categoryWithSubcategory") or ""
        self.description = raw.get("description") or ""
        self.order = raw.get("order") or 0.0
        self.id = raw.get("id")
        self.raw = raw

    @property
    def group(self):
        """The part after the dot in the subcategory - "elemental", "stats", or ""."""
        return self.subcategory.split(".", 1)[1] if "." in self.subcategory else ""

    @property
    def icon(self):
        """Where this item's bag icon is, or None when the game has no artwork for it."""
        return icon_path(self.uid)

    def stats(self, car=None):
        """What the game's own class overrides for this item - {} when it has no class, or none."""
        return stats_of(self.uid, car)

    def __repr__(self):
        return "<Item %s %r>" % (self.uid, self.name)


def load(with_stats=False):
    """Every item record, in the game's own order (`order`, then name).

    `with_stats` reads the Lua for each one, which needs the game and costs a read per item - the
    GUI leaves it off and asks `Item.stats()` for the few rows it is showing.
    """
    with open(ITEMS_JSON, encoding="utf-8") as fh:
        records = json.load(fh)
    items = [Item(rec) for rec in records]
    items.sort(key=lambda item: (item.order, item.name))
    if with_stats:
        for item in items:
            item.stats()
    return items


def by_category(items, category):
    """The items of one category, in the order `load` gave them."""
    return [item for item in items if item.category == category]


def categories(items):
    """`[(category, count), ...]`, most items first - what a filter is built from."""
    counts = {}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


# ---------------------------------------------------------------------------- artwork
def icon_path(uid):
    """The bag icon for `uid`, or None.

    TWO LAYOUTS, ONE LOOKUP. The shipped game files the icons under a VARIANT subfolder -
    `itemIcons/withoutGlow/<UID>.png`, with a glowing copy beside it - and the beta dropped the
    variants and put every icon straight in `itemIcons/`, 363 files and no subfolder at all. The
    variants are tried first because a glowing icon is the better picture, and the flat folder is
    the fallback; a `withoutGlow` file that exists still wins over a flat one, so nothing changes
    on a machine that has both.
    """
    for variant in VARIANTS:
        path = os.path.join(ICON_ROOT, variant, "%s.png" % uid)
        if os.path.exists(path):
            return path
    flat = os.path.join(ICON_ROOT, "%s.png" % uid)
    return flat if os.path.exists(flat) else None


def icon_size(uid):
    """(width, height) of an item's icon, from the PNG header - without decoding the picture.

    It exists so a caller can work out the box its icons need (16x16, and 20x16 for the gauntlets)
    from the headers alone, rather than decoding 272 images to ask them their size.
    """
    path = icon_path(uid)
    if not path:
        return None
    import dex
    return dex._png_size(path)


def strip(sprite):
    """The size of ONE frame of `sprite`, in pixels.

    The sheets this module points at are not all the same shape - the spinner strips are one ROW of
    frames, and the gauntlet parts are grids of whole 28x28 character cells - so a sprite says how it
    is cut up and this is the arithmetic in one place.
    """
    import dex
    size = dex._png_size(sprite["path"])
    if size is None:
        return None
    cell = sprite.get("cell") or 0
    return (cell or size[0] // max(1, sprite.get("columns", 1)),
            cell or size[1] // max(1, sprite.get("rows", 1)))


# ---------------------------------------------------------------------------- stats
_STATS = {}          # uid -> {method name: [values]}, read once per uid
_TOC = []            # the car's table of contents, parsed once (it is 8736 entries)


def _toc(car=None):
    """The car's table of contents, parsed once per process."""
    global _TOC
    if not _TOC:
        import car_extract as ce
        path = car or CAR
        if not os.path.exists(path):
            return []
        _TOC = ce.parse_toc(path)
    return _TOC


def _module(name, car=None):
    """The bytes of one `.lu` entry, or None when the car has no such entry."""
    import car_extract as ce
    path = car or CAR
    for typ, off, entry in _toc(car):
        if entry == name:
            _type, _usize, _csize, data = ce.read_entry(path, typ, off)
            return data
    return None


def _fields(raw):
    """(opcode, A, B, C, Bx) of one 32-bit Lua 5.1 instruction.

    BOTH OPERAND LAYOUTS ARE DECODED because both are needed: CLOSURE reads a proto index out of Bx,
    and SETTABLE reads its key out of B (where a value at or above `RK_CONSTANT` is a constant index
    rather than a register).
    """
    return (raw & 0x3F, (raw >> 6) & 0xFF, (raw >> 23) & 0x1FF, (raw >> 14) & 0x1FF,
            (raw >> 14) & 0x3FFFF)


def _overrides(proto):
    """`[(name, body proto), ...]` - the methods this proto attaches to a table.

    THE RULE IS IN THE BYTECODE AND NOWHERE ELSE: a method is a CLOSURE whose Bx is the body's own
    proto, and the assignment right after it is a SETTABLE whose B carries the constant flag (see the
    module docstring). Both halves are needed, which is why neither the constant list nor the proto
    list alone can be zipped: a class names more things than it overrides.
    """
    out = []
    body = None
    for raw in proto.code:
        op, _a, b_operand, _c, bx = _fields(raw)
        if op == OP_CLOSURE:
            body = bx if bx < len(proto.protos) else None
        elif op == OP_SETTABLE and body is not None:
            index = b_operand - RK_CONSTANT if b_operand >= RK_CONSTANT else -1
            if 0 <= index < len(proto.k) and isinstance(proto.k[index], str):
                out.append((proto.k[index], proto.protos[body]))
            body = None
    return out


def _values(proto):
    """The literal constants a method body works with, in order - its numbers and its strings.

    Booleans are dropped: a couple of these methods answer true/false (`shouldShareAcrossSaveslots`),
    which is behaviour rather than a number anyone reads off.
    """
    return [value for value in proto.k if not isinstance(value, bool)]


# WHAT A BODY CALLS THE CONSTANTS IT READS, filled in by `_constants()`: a method that uses one
# compiles to a GETGLOBAL with the name in its constant list, so what would otherwise be reported as
# the string "AMOUNT_OF_SHAKES_REQUIRED" is reported as the 5 the game shakes a spinner for.
_CONSTANTS = {}

# THE METHODS THAT ARE NOT STATS, skipped for that reason. `new` and `install` are the class protocol
# every item shares, and reading `new` as a stat reports the class's own list of method names as if it
# were a value - which is exactly what the first version of this file did.
NOT_STATS = ("new", "install")


def stats_of(uid, car=None):
    """`{method name: [values]}` for `classes.items.<uid>`, read from the car and cached.

    EVERY OVERRIDE, not only the stats a window shows: plenty of them are behaviour (a message, a
    catch sequence) with string constants in the body, and the caller decides what to display -
    `STAT_KEYS` is that decision, with the reason for each one next to it.

    An empty dict means the car is not installed, or the car has no class for this item (many records
    are data-only). Those are worth telling apart when something looks wrong, so `_module` is what
    distinguishes them and is available to callers that need to know.
    """
    if uid in _STATS:
        return _STATS[uid]
    data = _module(ITEM_CLASS + "%s.lu" % uid, car)
    if data is None:
        _STATS[uid] = {}
        return _STATS[uid]
    import luadis
    root, _header = luadis.load_data(data)
    _constants(car)
    out = {}
    for _path, proto in luadis.walk(root):
        for name, body in _overrides(proto):
            if name in NOT_STATS:
                continue
            values = [_CONSTANTS.get(value, value) for value in _values(body)]
            if values:
                out.setdefault(name, []).extend(values)
    _STATS[uid] = out
    return out


XP_GEM_EFFECT = "mutateXpEarned"


def xp_multiplier(uid, car=None):
    """The XP a hold item lets its holder earn, as a multiplier - or None when it has no effect.

    READ OUT OF THE GAME'S OWN EFFECT BODY, whose argument names say what each figure means
    (`classes.items.HOLD_EXTRA_XP`, `mutateXpEarned(_monsterSprite, _isLazy, _xpPerMonsterSprite,
    _value)`):

      * the Smart Gem returns `1.1 * _value` for its own holder - it scales what that Coromon was
        going to get, so a benched holder (whose `_value` is 0) gets 0.1 of nothing;
      * the lazy gems return `factor * _xpPerMonsterSprite + _value`, and ONLY when `_isLazy` - i.e.
        when the holder did not face the enemy. Since `_value` is 0 in that case, the figure is
        exactly the factor of its share: the Sloth Gem's 0.5, the Lazy Gem's 0.2.

    `classes.battle.rules.afterMonsterSpritesFaintedXp` is what passes those arguments, and it is
    also what divides an enemy's reward among the sprites that FACED it.
    """
    values = stats_of(uid, car).get(XP_GEM_EFFECT) or []
    numbers = [value for value in values if isinstance(value, (int, float))]
    return float(numbers[-1]) if numbers else None


def _constants(car=None):
    """`{NAME: value}` for the named constants the item classes read, read from the abstract spinner.
    Only the ones an item's own body can reference are worth having, and there is exactly one in this
    family: a body that needs the shake count loads the global `AMOUNT_OF_SHAKES_REQUIRED` rather
    than a literal, which is why the Platinum Spinner reads as `getAmountOfShakes =
    ['AMOUNT_OF_SHAKES_REQUIRED']` without this.
    """
    if _CONSTANTS:
        return _CONSTANTS
    data = _module(ABSTRACT_SPINNER, car)
    if data is None:
        return _CONSTANTS
    import luadis
    root, _header = luadis.load_data(data)
    for _path, proto in luadis.walk(root):
        for index, value in enumerate(proto.k[:-1]):
            following = proto.k[index + 1]
            if isinstance(value, str) and value.isupper() and isinstance(following, float):
                _CONSTANTS.setdefault(value, following)
    return _CONSTANTS


def base_catch_rates(car=None):
    """The game's own base catch rate per wild-Coromon rarity, from `abstractSpinner`.

    THE TABLE IS THE LONGEST RUN OF (LOWERCASE WORD, NUMBER) PAIRS in one constant list, and it has to
    be read as a RUN rather than as pairs: that chunk holds other table literals whose keys are also
    lowercase words (`monster`, `random`, `amount`, `min`, `chance` came out of the first version
    alongside the real ones), and the four rarity rates are the only place where several of those
    pairs sit next to each other. Measured: common 0.375, uncommon 0.3, rare 0.225, legendary 0.1 - a
    run of four, against isolated singles for the rest.

    A LIMIT WORTH KNOWING: this gives the rates, but nothing here can say WHICH rarity a given Coromon
    is. The rarity is not in `monsters.json` at all (it has no such field), and the game's own enum
    has six values (`RARITY_COMMON`, `_UNCOMMON`, `_RARE`, `_TITANIC`, `_SPECIAL`, `_HEXED`) against
    the four this table names, so pairing a Coromon with its rate needs a second read of the Lua.
    """
    data = _module(ABSTRACT_SPINNER, car)
    if data is None:
        return {}
    import luadis
    root, _header = luadis.load_data(data)
    best = []
    for _path, proto in luadis.walk(root):
        run = []
        index = 0
        while index < len(proto.k) - 1:
            key, value = proto.k[index], proto.k[index + 1]
            if isinstance(key, str) and key.islower() and key.isalpha() \
                    and isinstance(value, float):
                run.append((key, value))
                index += 2
                continue
            if len(run) > len(best):
                best = run
            run = []
            index += 1
        if len(run) > len(best):
            best = run
    return dict(best) if len(best) > 1 else {}


def shakes_required(car=None):
    """How many shakes a spinner needs (`AMOUNT_OF_SHAKES_REQUIRED` in `abstractSpinner`)."""
    return _constants(car).get("AMOUNT_OF_SHAKES_REQUIRED")


# THE STATS A WINDOW SHOWS, in the order it shows them, with what each one is called there. The reader
# returns EVERY override (see `stats_of`); this is the selection, and it is a selection rather than a
# guess: each of these is a value an item really carries, and the rest are behaviour.
STAT_KEYS = (
    ("getCatchRateModifier", "catch modifier"),
    ("getType", "used on"),
    ("getAmountOfShakes", "shakes"),
    ("getGoldCost", "gold cost"),
    ("getGoldSellPrice", "gold sell"),
)


# ---------------------------------------------------------------------------- sprites
# HOW MANY FRAMES EACH SHEET HOLDS, counted from the images themselves (the game documents none of
# it): the two spinner sheets are 112x35 and 570x215, i.e. 8 frames of 14x35 and 10 of 57x215 in a
# single row, and the gauntlet parts are grids of 28x28 character cells - 140x112 is 5x4 and 196x168
# is 7x6.
SPINNING_FRAMES, THROW_FRAMES = 8, 10
GAUNTLET_CELL = 28


def sprite(path, columns=1, rows=1, cell=0):
    """One drawable sprite: where it is, and how its sheet is cut up.

    A dict rather than a tuple because the three numbers mean different things (a frame count across,
    a frame count down, and a cell size that overrides both), and a caller that unpacks them in the
    wrong order draws the wrong picture rather than raising.
    """
    return {"path": path, "columns": columns, "rows": rows, "cell": cell}


def spinner_sprites(uid):
    """What a spinner looks like thrown and spinning - `{key: sprite}`, or {}.

    `spinning` is the spinner turning in the air and `throw` is the whole throw-and-catch sequence.
    """
    out = {}
    for key, name, frames in (("spinning", "spinning_%s.png", SPINNING_FRAMES),
                              ("throw", "spinner_unspawnAndCatch_%s.png", THROW_FRAMES)):
        path = os.path.join(SPINNER_SPRITES, name % uid)
        if os.path.exists(path):
            out[key] = sprite(path, columns=frames)
    return out


def gauntlet_parts(uid):
    """The worn sprite of a gauntlet skin - `{key: sprite}`, or {} when the item is not one.

    THE FOLDER IS NAMED BY THE ITEM'S OWN PART KEY, not by its UID: `abstractGauntlet.getPartKey()`
    returns e.g. `body_gauntlet_luxSolisGold_red`, and the folder on disk is the part between
    `body_gauntlet_` and the end. That key is also the only way to tell which item owns which folder
    (GAUNTLET_GOLD_RED -> body_gauntlet_luxSolisGold_red is not a rename anyone could guess), so it is
    read out of the Lua like any other stat.
    """
    parts = stats_of(uid).get("getPartKey") or []
    if not parts:
        return {}
    key = str(parts[0])
    skin = key[len("body_gauntlet_"):] if key.startswith("body_gauntlet_") else key
    out = {}
    for name, file_name, columns, rows in (("walk", "walkAndRun.png", 5, 4),
                                           ("charge", "gauntletWithCharge.png", 7, 6)):
        path = os.path.join(GAUNTLET_PARTS, skin, file_name)
        if os.path.exists(path):
            out[name] = sprite(path, columns=columns, rows=rows, cell=GAUNTLET_CELL)
    return out


def sprites(item):
    """Every extra sprite the game has for `item`, keyed by what it is: `{key: sprite}`."""
    if item.category == "spinner":
        return spinner_sprites(item.uid)
    if item.category == "gauntlet":
        return gauntlet_parts(item.uid)
    return {}


if __name__ == "__main__":
    all_items = load()
    print("items: %d in %d categories" % (len(all_items), len(categories(all_items))))
    for category, count in categories(all_items)[:8]:
        print("  %-12s %d" % (category, count))
    icons = sum(1 for item in all_items if item.icon)
    print("icons present: %d of %d" % (icons, len(all_items)))
    print("base catch rates:", base_catch_rates())
    print("shakes required:", shakes_required())
    for category in ("spinner", "gauntlet"):
        print("\n%s:" % category)
        for item in by_category(all_items, category):
            stats = item.stats()
            extra = " ".join("%s=%s" % (key, value) for key, value in sorted(stats.items())
                             if key != "getPartKey")
            sprites_ = ", ".join("%s(%dx%d)" % (key, *strip(one))
                                 for key, one in sorted(sprites(item).items()))
            print("  %-26s %-22s %s" % (item.uid, item.name, extra))
            if sprites_:
                print("      sprites: %s" % sprites_)
