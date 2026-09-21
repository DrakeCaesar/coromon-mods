#!/usr/bin/env python3
"""dex.py - every Coromon in dex order, where each one appears, and its icon.

THREE THINGS, from three places the game already ships:

  * THE LIST AND ITS ORDER - `data/json/monsters.json`, 139 entries with a `number`, which is the
    in-game dex id. The order the wiki uses is this number, not the file order and not the UID, so
    sorting by it is the whole point of this module rather than reading the JSON as it comes.
  * WHERE IT APPEARS - `data/json/encounterZones.json`, read through `encounters.py` and inverted:
    that file is zones -> monsters, and the question is monster -> zones. Note what this can and
    cannot say: it covers wild encounters, so evolutions, starters and gift Coromon have no
    locations at all, and that is a fact about the data rather than a gap in the reading.
  * THE ICON - `images/animatedMonsterSprites/idle/<UID>_<variant>_front.png`, a horizontal strip
    of square frames, so the first frame IS the icon and needs no art decoding. The variant letter
    is the skin (A/B/C) and one of them is always present; `gold` and other event skins are not
    used here because a dex list wants the ordinary one.

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
CELL = 24                    # atlas cell, and the size of a dex icon
CONTAINER = 17               # the type frame is SMALLER than the cell: the sprite overhangs it

# The atlas order lives in the game's own sheet definition, which is compiled into resource.car -
# so it is read from the extraction car_extract.py produces. Every other tool here needs it too.
EXTRACT = os.environ.get("QR_DISASM") or os.path.join(os.path.expanduser("~"), "qr_disasm")
ATLAS_MODULE = os.path.join(EXTRACT, "classes.modules.monsterAvatarAtlas.lu")

SPRITE_RE = __import__("re").compile(r"^[A-Z0-9][A-Za-z0-9_]*\.png$")

# A dex list wants the ordinary skin, and A is the one that is always there.
VARIANT = "A"
FRAME = 32          # the idle strips are 780x32: twenty-four 32 px frames

# WHERE THE CUT ICONS LIVE, and why this is not a cache for its own sake: it IS the extraction.
# Tk takes 2200 ms to read the 768x744 atlas, and 0.18 ms to read one of the 24 px icons cut from
# it - 118 icons come to 0.02 s. Cutting them once is the difference between a list that appears and
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
    base = "%03d_%s" % (mon.number, mon.uid) if mon.number else mon.uid
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
    def __init__(self, raw):
        self.number = raw.get("number")
        self.uid = raw.get("UID")
        self.name = raw.get("name") or raw.get("UID")
        self.family = raw.get("monsterFamilyUID")
        self.stats = raw.get("baseStats") or {}
        self.raw = raw

    def __repr__(self):
        return "<%s #%s %s>" % (self.uid, self.number, self.name)

    @property
    def total(self):
        return sum(self.stats.values()) if self.stats else 0


def monsters(include_unused=False):
    """Every Coromon, in dex order: numbered first, then the ones with no dex number.

    TWO GROUPS IN THIS FILE ARE NOT THE DEX, and both are easy to list by accident:
      * twenty-one entries numbered 900+, with #900 shared by THREE of them. They are unused slots
        left in the data - no icon, no encounters, duplicate numbers - so they are skipped unless
        asked for, because listing them makes the dex look like it has 139 entries when it has 110.
      * eight entries with no `number` at all: the titans, plus Fusebox and Xena. They are real
        Coromon with no dex number, so they are kept and sorted to the end rather than dropped.
    """
    raw = json.load(open(MONSTERS, encoding="utf-8"))
    keep = [m for m in raw if include_unused or not _unused(m)]
    out = [Species(m) for m in keep]
    out.sort(key=lambda s: (s.number is None, s.number or 0, s.name or ""))
    return out


def _unused(raw):
    """True for the 900+ placeholder slots. The number is the marker; nothing else distinguishes
    them, and three of them share #900, which no real dex entry does."""
    number = raw.get("number")
    return isinstance(number, int) and number >= 900


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


def _sheets():
    """The atlas and the type containers as photo images, decoded once per Tk interpreter.

    THIS IS THE DIFFERENCE BETWEEN A LIST THAT DRAWS AND ONE THAT FREEZES. The atlas is 768x744,
    and the first version of build_icon read and decoded that file again for every Coromon. Measured
    on this machine: 1990 ms per decode, so drawing one 118-row list would have spent 235 SECONDS
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
    xy = avatar_cell(mon.uid)
    sheets = _sheets()
    if xy is not None and sheets is not None:
        try:
            sheet = sheets["atlas"]
            cell = tk.PhotoImage(width=CELL, height=CELL)
            cell.tk.call(cell, "copy", sheet, "-from", xy[0], xy[1], xy[0] + CELL, xy[1] + CELL,
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
    icon.tk.call(icon, "copy", container, "-to", (CELL - CONTAINER) // 2, (CELL - CONTAINER) // 2)
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
    """The Coromon's type, or "grey" - which is the container the game itself falls back to."""
    fam = families().get(mon.family or "") or {}
    kind = fam.get("primaryType") or "grey"
    return kind if os.path.exists(os.path.join(CONTAINERS, "%s.png" % kind)) else "grey"


def avatar_cell(uid, variant=VARIANT):
    """(x, y) of that Coromon's avatar in the atlas, or None."""
    try:
        frame = atlas_index().get("%s_%s" % (uid, variant))
    except (OSError, ValueError):
        return None                  # no extraction: caller falls back to the sprite strips
    if frame is None:
        return None
    return ((frame % 32) * CELL, (frame // 32) * CELL)


_WHERE = None


def all_where():
    """{uid: [(zone, min, max, share, battles)]} for every Coromon at once, built once.

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
        for uid, row in zone.monsters.items():
            index.setdefault(uid, []).append(
                (zone, row["min"], row["max"], row["share"], row.get("battles", 1)))
    for uid in index:
        index[uid].sort(key=lambda h: (-h[3], h[0].average_level))
    _WHERE = index
    return index


def where(uid):
    """[(zone, min level, max level, share %, battles)] for one Coromon, best share first.

    Read from the same zone objects the grind tab ranks, so the two cannot disagree. Empty means no
    wild encounters - an evolution, a starter, a gift - and callers should say that rather than
    show a blank.
    """
    return all_where().get(uid, [])


# THE EXPLICIT EXPORT, a different thing from the cache above: `--icons` writes a folder you asked
# for, to keep or to publish, while ICON_ROOT is this tool's own working set. Same pictures,
# different sizes, deliberately not the same folder.
EXPORT_DIR = os.path.join(BASE, "icons")


def ensure_icons(zoom=1):
    """{uid: path} of the cut icons, cutting out any that are missing first.

    THE FAST PATH, and the reason the window no longer waits on the atlas: reading one of these back
    costs 0.2 ms, so a list of 118 icons comes to about 0.05 s - against 2200 ms to decode the
    768x744 atlas they are cut from, which is paid even once. Cutting costs a couple of seconds in
    total, so it happens on the first open and never again; the folder is named after the atlas's
    own size and mtime, so a game update lands in a new one with nothing to invalidate by hand.

    NATIVE 24 px, and the caller zooms, because the cost is per FILE rather than per pixel: measured,
    a 24 px icon reads in 0.2 ms and its 2x version in 3.95 ms, so the doubled files take 0.46 s to
    load where the native ones take 0.05 s. Tk's zoom is whole-number and nearest-neighbour, so
    zooming afterwards gives exactly what storing it doubled would have.

    Needs a Tk interpreter to draw with. With none it returns {} and the caller falls back to
    composing icons one at a time, which is what build_icon is for.
    """
    out = {}
    for mon in monsters():
        path = icon_path(mon, zoom)
        if not os.path.exists(path):
            if _sheets() is None:
                return {}
            icon = build_icon(mon, zoom)
            if icon is None:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            icon.write(path, format="png")
        out[mon.uid] = path
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
        for mon in monsters():
            try:
                icon = build_icon(mon, zoom=zoom)
            except tk.TclError as exc:
                missing.append((mon, str(exc)))
                continue
            if icon is None:
                missing.append((mon, "no avatar in the atlas and no idle strip"))
                continue
            name = "%03d_%s.png" % (mon.number, mon.uid) if mon.number else "%s.png" % mon.uid
            out = os.path.join(outdir, name)
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
        for mon in monsters():
            if needle in mon.name.lower() or needle in (mon.uid or "").lower():
                print("#%s %s (%s)" % (mon.number, mon.name, mon.uid))
                for zone, lo, hi, share, battles in where(mon.uid):
                    print("   %-22s %-22s L%-3s-%-3s %5.1f%%%s" % (
                        zone.map_file, zone.name, lo, hi, share,
                        "   x%d battles" % battles if battles > 1 else ""))
        return 0

    mons = monsters()
    no_icon = [m for m in mons if strip_frame(m.uid) is None]
    numbered = [m for m in mons if m.number is not None]
    print("%d Coromon: #%s..#%s, then %d with no dex number (titans and the like)" % (
        len(mons), numbered[0].number, numbered[-1].number, len(mons) - len(numbered)))
    try:
        have_atlas = sum(1 for m in mons if avatar_cell(m.uid))
        print("dex frames in the atlas : %d" % have_atlas)
    except (OSError, ValueError) as exc:
        print("atlas order unavailable : %s" % exc)
    print("with a sprite sheet     : %d" % (len(mons) - len(no_icon)))
    if no_icon:
        print("   no sprite for        : %s" % ", ".join(m.uid for m in no_icon[:8]))
    wild = [m for m in mons if where(m.uid)]
    print("with a location  : %d  (the rest are evolutions, starters, gifts - no wild encounters)"
          % len(wild))
    print("(skipped %d unused 900+ slots)"
          % (len(monsters(include_unused=True)) - len(mons)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
