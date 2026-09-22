#!/usr/bin/env python3
"""dex.py - every Coromon in dex order, where each one appears, and its icon.

THREE THINGS, from three places the game already ships - plus the crimsonite forms, which are a
fourth reading of the same two files:

  * THE LIST AND ITS ORDER - `data/json/monsters.json`, 139 entries of which 131 carry a `number`
    (the in-game dex id) - and 110 of those are the real dex, because the rest are 900+
    placeholders. The order the wiki uses is this number, not the file order and not the UID, so
    sorting by it is the whole point of this module rather than reading the JSON as it comes. The
    entries that have no number at all get their order from the game's own dex screen: see
    `unnumbered_order()`.
  * WHERE IT APPEARS - `data/json/encounterZones.json`, read through `encounters.py` and inverted:
    that file is zones -> monsters, and the question is monster -> zones. Note what this can and
    cannot say: it covers wild encounters, so evolutions, starters and gift Coromon have no
    locations at all, and that is a fact about the data rather than a gap in the reading.
  * THE ICON - `images/animatedMonsterSprites/idle/<UID>_<variant>_front.png`, a horizontal strip
    of square frames, so the first frame IS the icon and needs no art decoding. The variant letter
    is the skin (A/B/C) and one of them is always present; `gold` and other event skins are not
    used here because a dex list wants the ordinary one.
  * AND THE CRIMSONITE FORMS - the same species in the form some encounters spawn instead (see
    `crimsonite_forms`, and `encounters.Zone.crimsonite` for the flags). Eleven Coromon, six lines,
    every one of them in the water areas; they are a different sprite (`<UID>_crimsonite`), a
    different type container and their own catch milestones, so they are carried here as Coromon of
    their own - beside the species in the window's list, with their own locations - while
    `lines()` keeps the database grid to the game's own dex entries.

`write_icons()` CARVES THEM OUT as PNG files, one per Coromon, named `<number>_<UID>.png`. It uses
Tk's own PNG support rather than Pillow - Tk reads a PNG photo image, copies a sub-rectangle with
`copy(from_coords=...)` and writes the result back out - so nothing here needs a new dependency.

    python dex.py                 # coverage: how many have icons and how many have locations
    python dex.py --where BUZZLET # every zone a name appears in
    python dex.py --icons OUTDIR  # write the icons out as PNGs
"""

import json
import os
import struct
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(BASE), "Resources")
MONSTERS = os.path.join(RES, "data", "json", "monsters.json")
FAMILIES = os.path.join(RES, "data", "json", "families.json")
SPRITES = os.path.join(RES, "images", "animatedMonsterSprites", "idle")
AVATARS = os.path.join(RES, "images", "interface", "icons", "monsterAvatars")
ATLAS = os.path.join(AVATARS, "borderlessAtlas.png")
CONTAINERS = os.path.join(AVATARS, "typeContainers")
OTHER_ICONS = os.path.join(RES, "images", "interface", "icons", "otherIcons")
# The game's own database badges, straight out of the screen that draws them
# (`classes.interface.screens.monsterDatabaseScreen` names these two paths): a caught entry gets
# `icon_caught` in its top-right corner, and `icon_seen` is the weaker marker it uses for one it
# has only met.
CAUGHT_BADGE = os.path.join(OTHER_ICONS, "icon_caught.png")
SEEN_BADGE = os.path.join(OTHER_ICONS, "icon_seen.png")
# THE PLATE BEHIND AN ENTRY IS A REAL SPRITE, and it is not in the database screen's folder: it is
# the UIContainer system's rounded-rect MASK, `roundedRect_mask_radius4_18x18.png` - the only
# rounded rectangle the container builder ever loads (found by grepping every sprite reference out
# of resource.car: the container names are assembled at runtime, so the style
# `roundedRect_gridBoxBlueOrRed_radius4_withGridBoxBlueOrRedBorder` never appears as a file). The
# sprite is WHITE-ON-BLACK: its red channel is the coverage of an 18x18 rounded rect centred in a
# 28x28 canvas, and the game tints it with the container's fill colour.
PLATE_SPRITE = os.path.join(RES, "images", "interface", "shared", "UIContainer",
                            "roundedRect_mask_radius4_18x18.png")
# WHAT A NEVER-SEEN ENTRY DRAWS INSTEAD OF ITS FRAME: `UNKNOWN.png` is a 17x17 black square (the
# exact size of a type frame) with a hairline lighter edge, sitting in a 24x24 canvas so the game
# can place it with the same `magnet:bottomRight` call it uses for everything else. The companion
# `UNKNOWN_QUESTIONMARK.png` is the same square with the "?" baked into it, used by the screens
# that want the mark but not the silhouette (`hideMonsterAvatar = 'withQuestionMark'`); the
# DATABASE screen uses `'withQuestionMarkAndSilhouette'`, which is this square, the real sprite
# duotoned black, and the "?" drawn as text in the entry's own text colour.
UNKNOWN_FRAME = os.path.join(AVATARS, "UNKNOWN.png")
UNKNOWN_QUESTIONMARK = os.path.join(AVATARS, "UNKNOWN_QUESTIONMARK.png")
CELL = 24                    # atlas cell, and the size of a dex icon
CONTAINER = 17               # the type frame is SMALLER than the cell: the sprite overhangs it
# Where the type frame sits inside a cell, in WHOLE TEXELS: (24 - 17 + 1) // 2 = 4. See
# `frame_offset` below for why a whole texel matters.
FRAME_TEXELS = (CELL - CONTAINER + 1) // 2

# The atlas order lives in the game's own sheet definition, which is compiled into resource.car -
# so it is read from the extraction car_extract.py produces. Every other tool here needs it too.
EXTRACT = os.environ.get("QR_DISASM") or os.path.join(os.path.expanduser("~"), "qr_disasm")
ATLAS_MODULE = os.path.join(EXTRACT, "classes.modules.monsterAvatarAtlas.lu")
# THE ORDER OF THE COROMON THAT HAVE NO DEX NUMBER, which is a second thing only the game knows:
# see `unnumbered_order()`.
DEX_SCREEN_MODULE = os.path.join(EXTRACT, "classes.interface.screens.monsterDatabaseScreen.lu")

# The same order written out, for a machine with no extraction: `unnumbered_order()` falls back to
# this when the module cannot be read, so the window shows the game's order rather than an
# alphabetical one. The titans' names are the ones the game gives them in `monsters.json`.
UNNUMBERED_FALLBACK = ("FUSEBOX", "TITAN_ELECTRIC", "TITAN_GHOST", "TITAN_SAND", "TITAN_FIRE",
                       "TITAN_ICE", "TITAN_WATER")

# ONE MONSTER IS IN THE SHIPPED DATA BUT NOT IN THE GAME: `NORMAL_SPINNER`, "Xena", an experimental
# robot made of Spinners. It has no dex number, the dex screen names neither it nor its family, no
# zone spawns it, and - the clincher - IT HAS NO ARTWORK AT ALL: no cell in the avatar atlas and no
# idle strip (`avatar_cell` and `strip_frame` both return None for it, and it is the one entry the
# icon check used to count as missing). It is unreachable leftovers from the debugging build, the
# same kind of entry as the 900+ placeholders, so the window leaves it out. (It is why the title
# also ships `classes.debug.simulation.battles.coromon1`.)
UNUSED_UIDS = ("NORMAL_SPINNER",)

SPRITE_RE = __import__("re").compile(r"^[A-Z0-9][A-Za-z0-9_]*\.png$")

# A dex list wants the ordinary skin, and A is the one that is always there.
VARIANT = "A"
FRAME = 32          # the idle strips are 780x32: twenty-four 32 px frames

# WHERE THE CUT ICONS LIVE, and why this is not a cache for its own sake: it IS the extraction.
# Tk takes 2200 ms to read the 768x744 atlas, and 0.18 ms to read one of the 24 px icons cut from
# it - 117 icons come to 0.02 s. Cutting them once is the difference between a list that appears and
# one that takes minutes. One folder per atlas, named after the atlas's own size and mtime, so a
# game update lands in a new folder and nothing has to decide when to invalidate anything.
ICON_ROOT = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                         "coromon-dex-icons")


def atlas_stamp():
    """A cheap fingerprint of the atlas the icons are cut from: its size and mtime."""
    size = _png_size(ATLAS)
    try:
        mtime = int(os.stat(ATLAS).st_mtime)
    except OSError:
        return None
    return "%s-%d" % ("x".join(str(d) for d in size) if size else "?", mtime)


def icon_dir():
    return os.path.join(ICON_ROOT, atlas_stamp() or "unknown")


def icon_name(mon, zoom=1):
    """The file name for that Coromon's cut icon - the FORM included, when it has one.

    `mon.key`'s rule in a file name: a crimsonite form shares its UID and its dex number with the
    species it is a form of, so without the skin the two would be the same file and one would
    overwrite the other.
    """
    base = "%03d_%s" % (mon.number, mon.uid) if mon.number else mon.uid
    if mon.skin:
        base += "_%s" % mon.skin
    return base + ("" if zoom == 1 else "_x%d" % zoom) + ".png"


def icon_path(mon, zoom=1):
    return os.path.join(icon_dir(), icon_name(mon, zoom))


def _png_size(path):
    """(width, height) from the PNG header - enough to know a frame size without decoding it."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


class Species:
    def __init__(self, raw, skin=None, name=None):
        self.number = raw.get("number")
        self.uid = raw.get("UID")
        self.name = name or raw.get("name") or raw.get("UID")
        self.family = raw.get("monsterFamilyUID")
        self.stats = raw.get("baseStats") or {}
        self.skin = skin             # None for the ordinary Coromon; CRIMSONITE for that form
        self.raw = raw

    @property
    def key(self):
        """A unique id for THIS entry: the UID, plus the form when there is one.

        TWO COROMON SHARE A UID - a species and its crimsonite form (see `crimsonite_forms`) - so
        anything that keys a widget, a row or a lookup by Coromon must key it by `key`, not by `uid`.
        """
        return "%s#%s" % (self.uid, self.skin) if self.skin else self.uid

    def __repr__(self):
        return "<%s #%s %s%s>" % (self.uid, self.number, self.name,
                                   " (%s)" % self.skin if self.skin else "")

    @property
    def total(self):
        return sum(self.stats.values()) if self.stats else 0


def monsters(include_unused=False, with_crimsonite=False):
    """Every Coromon, in dex order: numbered first, then the ones with no dex number.

    TWO GROUPS IN THIS FILE ARE NOT THE GAME, and both are easy to list by accident:
      * twenty-one entries numbered 900+, with #900 shared by THREE of them. They are unused slots
        left in the data - no icon, no encounters, duplicate numbers - so they are skipped unless
        asked for, because listing them makes the dex look like it has 139 entries when it has 110.
      * `NORMAL_SPINNER` ("Xena"), the one unnumbered entry the game does not have at all - see
        `UNUSED_UIDS` - which leaves the six titans and Fusebox as the entries with no dex number.
        They ARE in the game, so they are kept, and they get their order from the game's own dex
        screen: see `unnumbered_order()`, the one place that order is written down.

    `with_crimsonite` adds the crimsonite forms (`crimsonite_forms`) each immediately after the
    species it is a form of - the same number, the same family, a different Coromon - which is what
    the window's Coromon list shows. `lines()` does NOT ask for them: the database grid is the
    game's own dex, which has no crimsonite entries (see `crimsonite_forms`).
    """
    raw = json.load(open(MONSTERS, encoding="utf-8"))
    keep = [m for m in raw if include_unused or not _unused(m)]
    out = [Species(m) for m in keep]
    if with_crimsonite:
        out += crimsonite_forms()
    rank = _ranks()
    # `bool(s.skin)` keeps a form AFTER its own species: they share a number, and by name alone
    # "Crimsonite Firefly" would sort before "Firefly".
    out.sort(key=lambda s: (s.number is None, s.number or 0, bool(s.skin),
                            rank.get(s.uid, len(rank)), s.name or ""))
    return out


def _unused(raw):
    """True for data that ships with the game but is not IN it: the 900+ placeholder slots and
    `UNUSED_UIDS`. For the placeholders the number is the marker, and nothing else distinguishes
    them - three of them even share #900, which no real dex entry does."""
    number = raw.get("number")
    if isinstance(number, int) and number >= 900:
        return True
    return raw.get("UID") in UNUSED_UIDS


_UNNUMBERED = None


def unnumbered_order():
    """The order of the Coromon with NO dex number - the six titans and Fusebox - as UIDs.

    THERE IS NO NUMBER TO SORT THEM BY, and nothing in their own data implies an order: they have
    no `number`, their `id` is a random GUID, and the two lists that DO hold every monster
    (`classes.lists.monsterDataList`, and `monsters.json` after it) are in ALPHABETICAL UID order,
    which would put Voltgar third. So the order comes from the game's own database screen, which is
    the one place that states it (`monsterDatabaseScreen`, lines 49-62):

        local specialList = {"FUSEBOX", "TITAN_ELECTRIC", "TITAN_GHOST", "TITAN_SAND",
                            "TITAN_FIRE", "TITAN_ICE", "TITAN_WATER"}          -- L49-57
        local list = monsterDataUtility:getSortedMonsterDataListWithNumber()     -- L59
        for i = 1, #specialList do                                              -- L60
            if playerStats:hasSeenMonster(specialList[i]) then                  -- L61
                list[#list + 1] = list[specialList[i]] end end                  -- L62

    i.e. the numbered dex first, then those seven in that order - Fusebox, Voltgar, Illuginn, Sart,
    Hozai, Vørst, Chalchiu - for any of them the player has met. `getSortedMonsterDataListWithNumber`
    is the only other ordering the game has, and it is named for what it leaves out.

    That list is also the proof that nothing else belongs here: it names every unnumbered Coromon
    the game actually has, and the one it does not name is `NORMAL_SPINNER`, which is left out of
    the dex rather than sorted into it (see `UNUSED_UIDS`). Anything unnamed would go after the
    game's own list, in name order, so a patched game that adds one still lists it.
    """
    global _UNNUMBERED
    if _UNNUMBERED is None:
        raw = json.load(open(MONSTERS, encoding="utf-8"))
        listed = [m.get("UID") for m in raw if not m.get("number") and not _unused(m)]
        try:
            order = _dex_screen_order()
        except (OSError, ValueError, ImportError):
            order = []                  # no extraction here: the copy below is the same list
        order = [uid for uid in (order or UNNUMBERED_FALLBACK) if uid in listed]
        names = {m.get("UID"): m.get("name") or m.get("UID") for m in raw}
        order += sorted((uid for uid in listed if uid not in order), key=lambda uid: names[uid])
        _UNNUMBERED = order
    return _UNNUMBERED


def _dex_screen_order():
    """The seven UIDs above, read out of the game's own database screen, or [] when unreadable.

    THE LIST IS A LUA TABLE LITERAL, which is why it is read rather than copied: a patched game
    would move it, and the point of reading it is that the window's order is the game's. In the
    bytecode a table literal is a run of `LOADK` constants - the same proto, consecutive constant
    slots - so the list is the LONGEST run of string constants that name a Coromon we know. That
    also skips an entry that merely resembles one: constants are matched against the UIDs in
    `monsters.json`, so `OUTFIT_TITAN_WATER` cannot be mistaken for `TITAN_WATER`.

    [] - never a partial list - when the module is missing: the caller has `UNNUMBERED_FALLBACK`.
    """
    import luadis                    # only this needs the disassembly tools

    root, _meta = luadis.load(DEX_SCREEN_MODULE)
    known = {m.get("UID") for m in json.load(open(MONSTERS, encoding="utf-8"))}
    best, run = [], []
    for _path, proto in luadis.walk(root):
        for const in (proto.k or []):
            if isinstance(const, str) and const in known:
                run.append(const)
                continue
            if len(run) > len(best):
                best = run
            run = []
        if len(run) > len(best):
            best = run
        run = []
    return best if len(best) > 1 else []


def _ranks():
    """{UID: where it goes among the Coromon with no dex number}, for the sort keys above."""
    return {uid: index for index, uid in enumerate(unnumbered_order())}


def lines(include_unused=False):
    """Every EVOLUTIONARY LINE, in dex order: [(family UID, [Species, ...]), ...].

    THE GAME'S OWN ROWS. The database screen groups by family - it names
    `currentMonsterFamilyUID`, and `getGridBoxIndexByData` places each entry in a row per line -
    and `families.json`'s `evolutionObjects` IS that line: ordered by `atLevel`, the base form at
    level 0, so reading them in that order gives the line left to right.

    Measured on the shipped data: 59 families, every one of the 117 dex entries belongs to one, the
    longest line is three stages, and the dex numbers inside a family are always consecutive - which
    is why sorting the rows by the first stage's number is the same as dex order. Seven families
    (twenty-one entries: ELECTRIC_SPIDER, FIRE_SHARK, GHOST_RAVEN, ICE_WOLF, NORMAL_DRAGON,
    SAND_CHIPMUNK, WATER_MOSQUITO) are entirely inside the 900+ placeholder block, and one more
    (NORMAL_SPINNER) is the entry the game does not have, so both groups drop out with the unused
    entries and the rows come to 51.

    A row with no number to sort by is placed by `unnumbered_order()` instead - that is the seven
    one-Coromon rows at the end (Fusebox and the six titans), in the game's own order.
    """
    raw = json.load(open(FAMILIES, encoding="utf-8"))
    by_uid = {mon.uid: mon for mon in monsters(include_unused)}
    out = []
    for family in raw:
        stages = family.get("evolutionObjects") or []
        stages = sorted(stages, key=lambda stage: stage.get("atLevel") or 0)
        members = [by_uid[stage["UID"]] for stage in stages if stage.get("UID") in by_uid]
        if members:
            out.append((family.get("UID"), members))
    rank = _ranks()
    out.sort(key=lambda pair: (pair[1][0].number is None, pair[1][0].number or 0,
                               rank.get(pair[1][0].uid, len(rank)), pair[0] or ""))
    return out


def strip_frame(uid, variant=VARIANT):
    """(path, x1, y1, x2, y2) - a crop of the first frame of that Coromon's idle strip, or None.

    THE FALLBACK, not the icon a dex uses: this is the plain sprite with no frame around it. It
    exists because the atlas order depends on the extracted game Lua, and icons that silently
    disappear on a machine without it would be worse than plain ones.

    Usually the crop is the first square frame of a horizontal strip, which is why the strip's
    HEIGHT is the frame size. One sheet is not a strip at all (Fusebox: 91x98), and cropping a
    square out of that takes the top of a portrait - so when the image is taller than it is wide,
    the whole image is the icon and the caller scales it.
    """
    for try_variant in (variant, "B", "C", "gold", "crimsonite"):
        path = os.path.join(SPRITES, "%s_%s_front.png" % (uid, try_variant))
        if not os.path.exists(path):
            continue
        size = _png_size(path)
        if size is None or size[0] <= 0 or size[1] <= 0:
            continue
        width, height = size
        if height > width:                    # not a strip: take it whole
            return (path, 0, 0, width, height)
        return (path, 0, 0, height, height)
    return None


_SHEETS = {}      # tk root -> {"atlas": image, "containers": {type: image}}


def frame_offset(zoom=1):
    """Where the type frame is drawn inside a 24 px icon, in pixels, at `zoom`.

    WHOLE TEXELS, and that is the point: the frame's true centre is 3.5 texels - exactly half a
    cell, because 17 and 24 differ by an odd number - which is a HALF TEXEL. Drawn at 3.5 texels
    and scaled, the frame lands 17.5 px into a 5x icon: 2.5 px off the sprite's every-fifth-pixel
    grid, so the two never line up and every edge of the frame sits between the sprite's pixels.
    Rounding to 4 texels (20 px at 5x) puts both images on the SAME grid - every offset in a
    composed icon is now a multiple of the zoom - at the cost of moving the frame half a texel
    from its exact centre, which is the smallest error the parity allows.
    """
    return FRAME_TEXELS * zoom


def icon_layout(uid, category=VARIANT, zoom=1):
    """How a dex icon is composed, as geometry rather than as pixels.

    Returns the atlas rect holding the avatar and where the 17 px type frame sits inside the
    24 px icon. Two renderers draw from this - `build_icon` below, with Tk, which is what
    writes the extracted PNG set, and the Qt window, with QImage - and keeping the geometry in
    one place is what stops them disagreeing about what an icon is.

    `category` IS WHICH PICTURE, not a tint: every Coromon has one atlas cell per potential
    category - `<uid>_A`, `<uid>_B`, `<uid>_C`, measured four apart (Cubzero at 391/395/399) - and
    that is how the game draws a Potent green and a Perfect gold. A species with no cell for the
    category asked for (only the titans, which have no B or C) falls back to A, so the icon still
    says WHICH Coromon it is even when the category has no artwork.

    `zoom` is the scale the icon will be DRAWN at, because the frame's own offset is 3.5 texels -
    exactly the centre of a 24 px cell - and that has to be rounded to whole pixels. At an even
    zoom it comes out exact (7 at 2x); at an odd one it cannot (17.5 at 5x), and rounding keeps the
    frame as close to centred as the parity allows rather than always low.

    None when the atlas order is unknown, which is the caller's cue to use the strip fallback.
    """
    xy = avatar_cell(uid, category) or avatar_cell(uid, VARIANT)
    if xy is None:
        return None
    return {"cell": (xy[0], xy[1], CELL, CELL), "frame_offset": frame_offset(zoom)}


def _sheets():
    """The atlas and the type containers as photo images, decoded once per Tk interpreter.

    THIS IS THE DIFFERENCE BETWEEN A LIST THAT DRAWS AND ONE THAT FREEZES. The atlas is 768x744,
    and the first version of build_icon read and decoded that file again for every Coromon. Measured
    on this machine: 1990 ms per decode, so drawing one 117-row list would have spent 235 SECONDS
    decoding artwork that never changes - which is indistinguishable from hanging. Cached here it is
    2.3 s for the whole list.

    Keyed by the Tk root because a photo image belongs to the interpreter that made it and cannot
    be used from another: the extractor and the window each get their own, and neither gets the
    other's.
    """
    import tkinter as tk

    root = tk._default_root
    if root is None:
        return None
    sheets = _SHEETS.get(root)
    if sheets is None:
        sheets = {"atlas": tk.PhotoImage(file=ATLAS), "containers": {}}
        _SHEETS[root] = sheets
    return sheets


def build_icon(mon, zoom=1):
    """A Tk image of that Coromon's DEX ICON: the type container with the avatar drawn over it.

    THE CONTAINER IS SMALLER THAN THE AVATAR (17 px against 24), so the sprite overhangs the frame.
    That is the game's own arrangement - it is why a dex entry looks like a sprite sitting on a
    rounded square - and copying them in this order is what reproduces it. The avatar is copied
    over the container with the default compositing rule, which is what keeps the container visible
    through the sprite's transparent pixels instead of punching holes in it.

    Falls back to the idle strip when the atlas order is unavailable. `zoom` is a whole-number
    nearest-neighbour scale, the only kind Tk has - 2 gives 48 px icons for a list.
    """
    import tkinter as tk

    cell = container = None
    layout = icon_layout(mon.uid)
    sheets = _sheets()
    if layout is not None and sheets is not None:
        try:
            sheet = sheets["atlas"]
            x, y = layout["cell"][0], layout["cell"][1]
            cell = tk.PhotoImage(width=CELL, height=CELL)
            cell.tk.call(cell, "copy", sheet, "-from", x, y, x + CELL, y + CELL,
                         "-to", 0, 0, "-compositingrule", "set")
            kind = primary_type(mon)
            container = sheets["containers"].get(kind)
            if container is None:
                container = tk.PhotoImage(file=os.path.join(CONTAINERS, "%s.png" % kind))
                sheets["containers"][kind] = container
        except tk.TclError:
            cell = container = None

    if cell is None or container is None:
        crop = strip_frame(mon.uid)
        if crop is None:
            return None
        path, x1, y1, x2, y2 = crop
        try:
            sheet = tk.PhotoImage(file=path)
            icon = tk.PhotoImage(width=max(1, x2 - x1), height=max(1, y2 - y1))
            icon.tk.call(icon, "copy", sheet, "-from", x1, y1, x2, y2, "-to", 0, 0,
                         "-compositingrule", "set")
            over = max(1, -(-max(icon.width(), icon.height()) // CELL))
            icon = icon.subsample(over) if over > 1 else icon
        except tk.TclError:
            return None
        return icon.zoom(zoom) if zoom > 1 else icon

    icon = tk.PhotoImage(width=CELL, height=CELL)
    icon.tk.call(icon, "copy", container, "-to", layout["frame_offset"], layout["frame_offset"])
    icon.tk.call(icon, "copy", cell, "-to", 0, 0)
    return icon.zoom(zoom) if zoom > 1 else icon


_ATLAS_INDEX = None


def atlas_index():
    """{sprite name: cell index in the avatar atlas}, read from the game's sheet definition.

    NOTHING IN THE SHIPPED IMAGE DATA SAYS WHICH CELL IS WHICH COROMON. The atlas is pre-baked and
    the order was decided when it was built, so it is read from the module that builds it - which
    is why this needs the extracted game Lua. The order turns out to be alphabetical by sprite
    name, which is worth knowing when reading an index: ELECTRIC_BEETLE_1_A is frame 0 and
    WATER_TURTLE_2_emerald the last one, so a dex number tells you nothing about the cell.
    """
    global _ATLAS_INDEX
    if _ATLAS_INDEX is not None:
        return _ATLAS_INDEX
    import luadis                    # only this needs the disassembly tools

    root, _meta = luadis.load(ATLAS_MODULE)
    index, seen = {}, set()
    for _path, proto in luadis.walk(root):
        for k in (proto.k or []):
            if isinstance(k, str) and SPRITE_RE.match(k) and k not in seen:
                seen.add(k)
                index[k[:-4]] = len(index)
    _ATLAS_INDEX = index
    return index


_FAMILIES = None


def families():
    """{family UID: family record}, which is where the TYPE that colours a dex frame lives."""
    global _FAMILIES
    if _FAMILIES is None:
        _FAMILIES = {f.get("UID"): f for f in json.load(open(FAMILIES, encoding="utf-8"))}
    return _FAMILIES


def primary_type(mon):
    """The Coromon's type, or "grey" - which is the container the game itself falls back to.

    A CRIMSONITE FORM IS ITS OWN TYPE, not the family's: the game draws it in
    `typeContainers/crimsonite.png` whenever `app:usesCrimsoniteAsType()` says so.
    """
    if mon.skin == CRIMSONITE and os.path.exists(os.path.join(CONTAINERS, "%s.png" % CRIMSONITE)):
        return CRIMSONITE
    fam = families().get(mon.family or "") or {}
    kind = fam.get("primaryType") or "grey"
    return kind if os.path.exists(os.path.join(CONTAINERS, "%s.png" % kind)) else "grey"


def avatar_cell(uid, variant=VARIANT):
    """(x, y) of that Coromon's avatar in the atlas, or None.

    `variant` is the potential category (`A` standard, `B` potent, `C` perfect) or a skin
    (`gold`, `crimsonite`, ...) - they are all the same kind of entry in the sheet's own
    definition, which is why one parameter covers both.
    """
    try:
        frame = atlas_index().get("%s_%s" % (uid, variant))
    except (OSError, ValueError):
        return None                  # no extraction: caller falls back to the sprite strips
    if frame is None:
        return None
    return ((frame % 32) * CELL, (frame // 32) * CELL)


_WHERE = None


# The skin name a crimsonite form is filed under, in the atlas (`<UID>_crimsonite`) and in the type
# containers (`typeContainers/crimsonite.png`). The atlas also has a `<UID>_crimsoniteAndTendrils`
# skin; the game's own catch milestones say `[type.crimsonite]`, so that is the one to use.
CRIMSONITE = "crimsonite"


_FORM_CACHE = None


def crimsonite_forms():
    """The crimsonite forms of the Coromon that have one - as SEPARATE Coromon, not a tint.

    WHAT A CRIMSONITE IS. Some encounters carry `crimsonite: true` on their monsters (11 Coromon,
    across six lines, every one of them in the water areas - see `encounters.Zone.crimsonite`).
    Those are the same species in its crimsonite form: a different sprite (`<UID>_crimsonite` in the
    atlas), its own type container (`useCrimsoniteAsType` in `MonsterAvatar`), its own skills (the
    game ships a whole `battle.skills.crimsonite` family - SHADOW REND, CORRUPT - plus a
    `crimsoniteAura` battle rule and a crimsonite weather effect) and its own catch milestones,
    one per line: `MonsterSpriteSkinMilestone` holds `CATCH_CRIMSONITE_ELECTRIC_FIREFLY`, whose text
    is "Catch a Coromon from the [type.crimsonite] [monster ELECTRIC_FIREFLY_1] line". Those six
    milestones name exactly the six lines the encounter data has crimsonite spawns for, so the two
    readings check each other.

    WHY THEY ARE COROMON HERE: they are caught as themselves - a crimsonite Firefly is not a
    Firefly you own - and they are what the user asked for: "in some areas special crimsonite
    coromon spawn, those need to be integrated too as a separate coromon with their own
    encounters". `where(uid, CRIMSONITE)` is exactly those encounters.

    NOT IN THE DEX GRID: the game's own database has no crimsonite entries - a catch milestone is
    not a dex entry - and the grid's three column groups are the potenital categories, which a
    crimsonite form does not have. So `lines()` leaves them out, and this is what the window's
    Coromon list adds beside each species.
    """
    global _FORM_CACHE
    if _FORM_CACHE is None:
        raw = json.load(open(MONSTERS, encoding="utf-8"))
        by_uid = {m.get("UID"): m for m in raw}
        order = {mon.uid: position for position, mon in enumerate(monsters())}
        out = []
        for uid, skin in all_where():
            if skin != CRIMSONITE or uid not in by_uid:
                continue
            base = by_uid[uid]
            out.append(Species(base, skin=CRIMSONITE,
                               name="Crimsonite %s" % (base.get("name") or uid)))
        out.sort(key=lambda s: order.get(s.uid, len(order)))    # beside the species it is a form of
        _FORM_CACHE = out
    return list(_FORM_CACHE)


def all_where():
    """{(uid, skin): [(zone, min, max, share, battles)]} for every Coromon at once, built once.

    THE KEY CARRIES THE FORM, because the same UID is two different Coromon: `skin` is None for the
    ordinary one and `CRIMSONITE` for that form, and the encounter data says which slot is which.
    Counting them together (which this did before crimsonite was read) both overstated the ordinary
    form's share and lost the only place the crimsonite one appears.

    One pass over the zones rather than one per Coromon: `where()` used to re-read the encounter
    file for each call, which a list of 110 rows would do 110 times.
    """
    global _WHERE
    if _WHERE is not None:
        return _WHERE
    import encounters

    zones, species = encounters.load()
    index = {}
    for zone in encounters.all_zones(zones, species):
        for skin, group in ((None, zone.monsters), (CRIMSONITE, zone.crimsonite)):
            for uid, row in group.items():
                index.setdefault((uid, skin), []).append(
                    (zone, row["min"], row["max"], row["share"], row.get("battles", 1)))
    for key in index:
        index[key].sort(key=lambda h: (-h[3], h[0].average_level))
    _WHERE = index
    return index


def where(uid, skin=None):
    """[(zone, min level, max level, share %, battles)] for one Coromon, best share first.

    `skin` is `CRIMSONITE` for that form of it (see `crimsonite_forms`) and None for the ordinary
    Coromon. Read from the same zone objects the grind tab ranks, so the two cannot disagree. Empty
    means no wild encounters - an evolution, a starter, a gift, a scripted crimsonite battle - and
    callers should say that rather than show a blank.
    """
    return all_where().get((uid, skin), [])


# THE EXPLICIT EXPORT, a different thing from the cache above: `--icons` writes a folder you asked
# for, to keep or to publish, while ICON_ROOT is this tool's own working set. Same pictures,
# different sizes, deliberately not the same folder.
EXPORT_DIR = os.path.join(BASE, "icons")


def ensure_icons(zoom=1):
    """{uid: path} of the cut icons, cutting out any that are missing first.

    THE FAST PATH, and the reason the window no longer waits on the atlas: reading one of these back
    costs 0.2 ms, so a list of 117 icons comes to about 0.05 s - against 2200 ms to decode the
    768x744 atlas they are cut from, which is paid even once. Cutting costs a couple of seconds in
    total, so it happens on the first open and never again; the folder is named after the atlas's
    own size and mtime, so a game update lands in a new one with nothing to invalidate by hand.

    NATIVE 24 px, and the caller zooms, because the cost is per FILE rather than per pixel: measured,
    a 24 px icon reads in 0.2 ms and its 2x version in 3.95 ms, so the doubled files take 0.46 s to
    load where the native ones take 0.05 s. Tk's zoom is whole-number and nearest-neighbour, so
    zooming afterwards gives exactly what storing it doubled would have.

    Needs a Tk interpreter to draw with. With none it returns {} and the caller falls back to
    composing icons one at a time, which is what build_icon is for.

    THE CRIMSONITE FORMS ARE CUT TOO, and keyed by `mon.key` rather than by UID: they are Coromon
    the window shows, so an icon set that skipped them would be half a set - and they share a UID
    with the species they are a form of, so a uid-keyed dict would drop one of the two.
    """
    out = {}
    for mon in monsters(with_crimsonite=True):
        path = icon_path(mon, zoom)
        if not os.path.exists(path):
            if _sheets() is None:
                return {}
            icon = build_icon(mon, zoom)
            if icon is None:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            icon.write(path, format="png")
        out[mon.key] = path
    return out


def write_icons(outdir, zoom=1):
    """Write every Coromon's icon out as a PNG, `<dex>_<UID>.png`.

    Tk does the work: it reads the atlas and the container as photo images, copies the cell over
    the frame and writes the result. Not elegant, but it is the only PNG decoder and encoder
    already on this machine - Pillow is not installed - and it keeps the files readable by
    anything afterwards. Returns (written, missing, which style was available).
    """
    import tkinter as tk

    os.makedirs(outdir, exist_ok=True)
    root = tk.Tk()
    root.withdraw()
    try:
        style = "dex icons (type container + atlas avatar)" if avatar_cell(
            monsters()[0].uid) else "plain sprites (no atlas order - run car_extract.py)"
        written, missing = [], []
        for mon in monsters(with_crimsonite=True):
            try:
                icon = build_icon(mon, zoom=zoom)
            except tk.TclError as exc:
                missing.append((mon, str(exc)))
                continue
            if icon is None:
                missing.append((mon, "no avatar in the atlas and no idle strip"))
                continue
            out = os.path.join(outdir, icon_name(mon, zoom))
            icon.write(out, format="png")
            written.append(out)
    finally:
        root.destroy()
    return written, missing, style


def main(argv):
    if "--icons" in argv:
        at = argv.index("--icons")
        outdir = argv[at + 1] if len(argv) > at + 1 and not argv[at + 1].startswith("--") \
            else EXPORT_DIR
        zoom = 2 if "--2x" in argv else 1
        written, missing, style = write_icons(outdir, zoom=zoom)
        print("wrote %d icons to %s as %s%s" % (len(written), outdir, style,
                                                " at 2x" if zoom > 1 else ""))
        for m in missing[:10]:
            print("   no icon:", m)
        return 0
    if "--where" in argv:
        needle = argv[argv.index("--where") + 1].lower()
        for mon in monsters(with_crimsonite=True):
            if needle in mon.name.lower() or needle in (mon.uid or "").lower():
                print("#%s %s (%s)" % (mon.number, mon.name, mon.key))
                for zone, lo, hi, share, battles in where(mon.uid, mon.skin):
                    print("   %-22s %-22s L%-3s-%-3s %5.1f%%%s" % (
                        zone.map_file, zone.name, lo, hi, share,
                        "   x%d battles" % battles if battles > 1 else ""))
        return 0

    mons = monsters()
    forms = crimsonite_forms()
    no_icon = [m for m in mons if strip_frame(m.uid) is None]
    numbered = [m for m in mons if m.number is not None]
    print("%d Coromon: #%s..#%s, then %d with no dex number (titans and the like)" % (
        len(mons), numbered[0].number, numbered[-1].number, len(mons) - len(numbered)))
    print("crimsonite forms        : %d, across %d line(s), %d of them with encounters" % (
        len(forms), len({m.family for m in forms}),
        sum(1 for m in forms if where(m.uid, m.skin))))
    try:
        have_atlas = sum(1 for m in mons if avatar_cell(m.uid))
        print("dex frames in the atlas : %d" % have_atlas)
    except (OSError, ValueError) as exc:
        print("atlas order unavailable : %s" % exc)
    print("with a sprite sheet     : %d" % (len(mons) - len(no_icon)))
    if no_icon:
        print("   no sprite for        : %s" % ", ".join(m.uid for m in no_icon[:8]))
    wild = [m for m in mons if where(m.uid, m.skin)]
    print("with a location  : %d  (the rest are evolutions, starters, gifts - no wild encounters)"
          % len(wild))
    print("(skipped %d unused 900+ slots)"
          % (len(monsters(include_unused=True)) - len(mons)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
