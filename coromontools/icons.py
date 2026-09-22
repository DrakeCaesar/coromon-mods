"""Dex icons, decoded and composed in memory by Qt.

WHAT THIS REPLACES, AND WHY IT MATTERS. Tk could not decode the 768x744 avatar atlas
cheaply: one decode measured 1990 ms, so the Tk version had to compose each icon once, write
it to %APPDATA% as a 24 px PNG, and read it back (0.2 ms each) on every run after that. The
cache on disk existed for no reason other than to avoid Tk's decoder - it needed an atlas
mtime stamp to know when to invalidate, it left ~118 files behind, and it cost 2.4 s the
first time. Qt decodes the same atlas in tens of milliseconds, so the cache is gone: icons
are composed in memory, once each, and there is nothing to invalidate or delete.

The composition is not invented here - `dex.icon_layout` says where the avatar cell is and
where the frame goes, and the Tk extractor that writes the PNG set draws from the same
description.

WHAT AN ICON IS MADE OF, and the two things that are easy to get wrong: the SPRITE depends on
the potential category (three separate atlas cells per Coromon - `A` standard, `B` potent,
`C` perfect - which is how the game draws a Potent green and a Perfect gold, NOT a tint), and
the icon is composed AT THE SIZE IT IS DRAWN, because the frame's 3.5-texel offset is a half
pixel at 1x and would leave the sprite visibly off-centre in it.
"""

import os

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

import dex

from . import font
from .config import (ICON_BADGE_SHIFT, ICON_NUDGE, ICON_NUMBERS, ICON_PAD, NUMBER_GAP, PLATE_FILL,
                     PLATE_OFFSET, PLATE_TEXELS, PLATE_UNKNOWN_FILL, QUESTION_MARK, STATE_CAUGHT,
                     STATE_DARKENING, STATE_NUMBERS, STATE_UNKNOWN)

# THE PLATE IS DRAWN, NOT AN IMAGE. What the database screen puts behind an entry is its GRID BOX
# CONTAINER (`UIContainerStyle.roundedRect_gridBoxBlueOrRed_radius4_withGridBoxBlueOrRedBorder`),
# and no such file exists in `Resources/images` - the container is composited at runtime from a
# tinted rounded rect plus its border, which is why a colour search for the plate's pixels finds
# nothing either. Its geometry and colours were measured off the game's own entry instead:
#   * 19 ICON TEXELS square, and the 17-texel type frame sits centred in it with 1 texel of plate
#     showing all round - measured on the shipped screen as a 95 px box (19 units at 5 px/unit)
#     against the frame's 85 px.
#   * the fill is a vignette, not a flat colour: #3C3C4F through the middle, ramping to #0A0A22
#     over the last 3 texels at every edge. (Those two colours are what the game's own render
#     measures; the plate is semi-transparent white over its background, which composites to
#     exactly #3C3C4F on the screen's #01011A.)
#   * an UNDISCOVERED entry's box is the same border around a BLACK fill (`..._withGridBoxDisabled
#     Border`), measured as pure 0,0,0 inside the same ramp.
# The old implementation drew `monsterBackgroundRounded.png` here - the blue GRID background the
# details pane puts behind the Coromon SPRITE, not this plate. The user's words: "you used the
# background that the coromon sprites have - I meant the one behind the frames".


_ATLAS = None                 # the 768x744 avatar sheet, decoded once for the whole run
_FRAMES = {}                  # type name -> the 17 px frame image
_ICONS = {}                   # (uid, category, zoom) -> QImage, or None when there is no artwork
_PIXMAPS = {}                 # (uid, category, zoom) -> QPixmap
_BADGES = {}                  # badge name -> QImage (the game's own corner markers)
_PLATES = {}                  # (zoom, discovered) -> the tinted entry plate sprite
_PLATE_MASK = [None]          # the plate sprite, cropped to its shape, decoded once
_UNKNOWN = [None]             # dex.UNKNOWN_FRAME cropped to its square, decoded once

# Star imports nothing: the potential category a caller means when it does not say. `A` is the
# ordinary sprite, which is what the window showed before the categories were threaded through.
STANDARD = dex.VARIANT


def atlas():
    """The avatar atlas, decoded once. A QImage belongs to no widget, so one copy serves all."""
    global _ATLAS
    if _ATLAS is None:
        _ATLAS = QImage(dex.ATLAS) if os.path.exists(dex.ATLAS) else QImage()
    return _ATLAS


def frame(kind):
    """The type frame as an image, decoded once per type."""
    if kind not in _FRAMES:
        path = os.path.join(dex.CONTAINERS, "%s.png" % kind)
        _FRAMES[kind] = QImage(path) if os.path.exists(path) else QImage()
    return _FRAMES[kind]


def icon_image(mon, category=STANDARD, zoom=1, state=STATE_CAUGHT):
    """That Coromon's dex icon as a QImage at `zoom`, or None when it has no artwork.

    `category` IS WHICH SPRITE, not a tint: the three potential categories have their own atlas
    cells (`A` standard, `B` potent, `C` perfect - see `dex.icon_layout`), which is how the game
    draws a Potent green and a Perfect gold. `state` is what the database screen does to it - the
    darkening, and the black frame and "?" of an entry you have never met - see `config.STATES`.

    None is a real answer, not a failure: the titans and a few others have no dex entry in the
    atlas and no idle strip either, and their row is simply text.
    """
    key = (mon.uid, category, zoom, state)
    if key not in _ICONS:
        _ICONS[key] = _compose(mon, category, zoom, state) or _from_strip(mon.uid, category, zoom)
    return _ICONS[key]


def icon_pixmap(mon, zoom=1, category=STANDARD, caught=False, number=None, state=STATE_CAUGHT):
    """The icon ready to be drawn at `zoom`, composed at that size so nothing is resampled.

    Whole-number scaling with FastTransformation is exact - 2x of a 24 px cell is the 48 px icon
    the game shows, not a blurry approximation of it - and composing AT the zoom (rather than
    scaling a finished 1x icon) is what lets the type frame sit on the same pixel grid as the
    sprite: see `dex.frame_offset`.

    `caught` draws the game's own `icon_caught` badge in the top-right corner - the marker the
    database screen puts on an entry you own (`dex.CAUGHT_BADGE`) - and `number` (default: the
    `config.ICON_NUMBERS` setting) draws the dex number in the bottom-right, in the colour the
    entry's `state` gets. The database tab passes both; a caller that just wants a picture of the
    Coromon leaves `state` at `caught`, which is the full-colour icon.
    """
    number = ICON_NUMBERS if number is None else number
    key = (mon.uid, category, zoom, caught, bool(number), state)
    if key not in _PIXMAPS:
        image = icon_image(mon, category, zoom, state)
        if image is not None and number and mon.number:
            image = _numbered(image, mon.number, zoom, STATE_NUMBERS[state])
        if image is not None and caught:
            image = _badged(image, zoom)
        _PIXMAPS[key] = QPixmap.fromImage(image) if image is not None else None
    return _PIXMAPS[key]


def _numbered(icon, number, zoom, colour):
    """The dex number in the colour of the entry's state, in the game's own font.

    THE FONT IS THE GAME'S (`font.py` reads `outline_10_bold` out of resource.car) and the glyphs
    are drawn untinted here: the atlas is authored white with a black outline, and the game
    re-colours the fill per state (`fillColor` on the text object) - cyan for an entry you own,
    grey for one you have met, the darker warm grey for one you have not (see
    `config.STATE_NUMBERS`, which is the game's own palette). Tinting is `CompositionMode_SourceIn`
    against the drawn glyphs, so the outline stays black and only the fill takes the colour.

    WHERE: the game anchors its number to the entry's BOX - `magnetRightToLeftAware:bottomRight
    (text, 2, 0, parent)` - so the box's right edge is 2 texels past the plate and the box's
    bottom edge is the plate's own bottom. The digits then hang their 4 texel descent above that,
    which lands them on the plate's bottom-right corner and 2 texels past its right edge, exactly
    as the game's screen shows. (Drawing it INSIDE the plate put it on the type frame's border,
    where a coloured number disappeared into a frame of the same colour.)
    """
    out = QImage(icon)
    painter = QPainter(out)
    left = (ICON_PAD[0] + PLATE_OFFSET) * zoom
    _tinted_text(painter, str(number), zoom, colour,
                 left + PLATE_TEXELS * zoom + NUMBER_GAP * zoom,
                 left + PLATE_TEXELS * zoom)
    painter.end()
    return out


def avatar_size():
    """The sprite's own canvas, in TEXELS: the 24 texel cell plus `config.ICON_PAD`.

    THE PAD IS THE FIX FOR CLIPPED SPRITES, and it has to be part of the canvas the sprite is
    composed on, not just of the finished icon - the sprite is drawn 3 texels up and left of the
    cell (see `config.ICON_NUDGE`), so a 24 texel buffer loses exactly that much off a Coromon big
    enough to reach the corner. Composed on this canvas instead, the sprite has room on the side it
    overhangs - its art spans at most the cell's own 24 texels - and nothing is cut.
    """
    return (dex.CELL + ICON_PAD[0], dex.CELL + ICON_PAD[1])


def icon_size(zoom=1):
    """The size a composed icon comes out at, including the badge and number overhangs, in pixels.

    The canvas is the 24 texel cell, plus `config.ICON_PAD` on the side a nudged sprite hangs over,
    plus whatever `config.ICON_BADGE_SHIFT` pushes the badge PAST its top-right corner - so an
    overhanging sprite and badge are DRAWN rather than cut off. Callers that place an icon (the
    tabs) ask for this instead of computing `dex.CELL * zoom` themselves, or a cell sized for the
    old canvas clips both the sprite and the badge again.

    The NUMBER needs no room of its own any more: anchored to the plate it reaches 1 texel past
    the cell's right edge and stays inside it vertically (see `_numbered`), and the badge's 2
    texels of overhang already cover that.
    """
    return ((dex.CELL + ICON_PAD[0] + max(0, ICON_BADGE_SHIFT[0])) * zoom,
            (dex.CELL + ICON_PAD[1] + max(0, ICON_BADGE_SHIFT[1])) * zoom)


def plate(zoom, discovered=True):
    """The entry plate the database draws UNDER every icon: the game's own sprite, tinted.

    THE SPRITE IS `dex.PLATE_SPRITE` - the UIContainer system's rounded-rect MASK, and the only
    rounded rectangle in the whole game (every other container name is assembled at runtime, which
    is why searching the sprites for the plate's colour finds nothing: the game tints the white mask
    instead). It is white-on-black, so its red channel IS the coverage of an 18x18 rounded rect, and
    the tint is `config.PLATE_FILL` - measured off the game's own entry, where the mask composites
    to #3C3C4F over the screen's #01011A.

    It is drawn at `config.PLATE_TEXELS` = 19 texels, one texel bigger than the mask, because that
    is the size the game renders (the frame sprite template-matches into the screen at x 285..369
    and the plate's pixels span x 280..374).

    PIXELATED, NOT SMOOTHED: the mask is scaled with nearest neighbour, so the shape comes out in
    blocks of whole screen pixels like every other sprite in the window, instead of the soft blur a
    smooth scale gives (which reads as a drawn rounded rectangle, not as the game's sprite). The
    mask's own antialiased edge pixels are what soften the corners - they scale into blocks too.

    `discovered` picks the fill the game uses: its ordinary grid box for an entry it has met, and
    the DISABLED one - the same sprite in BLACK - for one it has not.
    """
    key = (zoom, discovered)
    if key not in _PLATES:
        mask = plate_mask()
        if mask.isNull():
            _PLATES[key] = None
        else:
            colour = QColor(PLATE_FILL if discovered else PLATE_UNKNOWN_FILL)
            # STRAIGHT alpha, not premultiplied: the mask's edge pixels carry a partial COVERAGE,
            # and writing (r, g, b, cover) into a premultiplied image means "the colour is already
            # multiplied by cover" - Qt then unscales it when painting, so every semi-covered edge
            # pixel comes out brighter than the fill and the plate gets a white halo.
            tinted = QImage(mask.size(), QImage.Format.Format_ARGB32)
            tinted.fill(Qt.GlobalColor.transparent)
            for y in range(mask.height()):
                for x in range(mask.width()):
                    cover = mask.pixelColor(x, y).red()
                    if cover:
                        tinted.setPixelColor(x, y, QColor(colour.red(), colour.green(),
                                                           colour.blue(), cover))
            side = PLATE_TEXELS * zoom
            _PLATES[key] = QPixmap.fromImage(
                tinted.scaled(side, side, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.FastTransformation))
    return _PLATES[key]


def plate_mask():
    """The plate sprite cropped to its white rounded rect, decoded once.

    The crop is measured rather than hard-coded: the mask file is a 28x28 canvas with the shape
    floating in it (an 18x18 rounded rect at 5,6 in the shipped set), and a future one that moves it
    should not need this code changed.
    """
    if _PLATE_MASK[0] is None:
        sheet = QImage(dex.PLATE_SPRITE) if os.path.exists(dex.PLATE_SPRITE) else QImage()
        crop = QImage()
        if not sheet.isNull():
            left = top = None
            right = bottom = -1
            for y in range(sheet.height()):
                for x in range(sheet.width()):
                    if sheet.pixelColor(x, y).red() > 128:
                        left = x if left is None else min(left, x)
                        top = y if top is None else min(top, y)
                        right, bottom = max(right, x), max(bottom, y)
            if left is not None:
                crop = sheet.copy(left, top, right - left + 1, bottom - top + 1)
        _PLATE_MASK[0] = crop
    return _PLATE_MASK[0]


def badge(name="caught"):
    """One of the game's own entry badges, decoded once (see `dex.CAUGHT_BADGE`)."""
    if name not in _BADGES:
        path = dex.CAUGHT_BADGE if name == "caught" else dex.SEEN_BADGE
        _BADGES[name] = QImage(path) if os.path.exists(path) else QImage()
    return _BADGES[name]


def _badged(icon, zoom, name="caught"):
    """The icon with that badge at its TOP-RIGHT corner, offset by `config.ICON_BADGE_SHIFT` texels.

    Positioned against the CELL, not against the canvas: `icon_size` already makes the canvas big
    enough to hold the overhang, so adding the shift to the canvas width would place the badge twice
    as far out and clip it against the right edge.
    """
    piece = badge(name)
    if piece.isNull():
        return icon
    out = QImage(icon)
    piece = _grow(piece, zoom)
    painter = QPainter(out)
    painter.drawImage(QPoint((ICON_PAD[0] + dex.CELL) * zoom - piece.width()
                             + ICON_BADGE_SHIFT[0] * zoom,
                             (ICON_PAD[1] + ICON_BADGE_SHIFT[1]) * zoom), piece)
    painter.end()
    return out


def _plate_into(painter, zoom, discovered=True):
    """The entry plate behind everything else, at `PLATE_OFFSET` of the 24 texel cell."""
    piece = plate(zoom, discovered)
    if piece is not None:
        painter.drawPixmap(QPoint((ICON_PAD[0] + PLATE_OFFSET) * zoom,
                                  (ICON_PAD[1] + PLATE_OFFSET) * zoom), piece)


def _pixmap(image):
    return QPixmap.fromImage(image) if image is not None and not image.isNull() else None


def _grow(image, zoom):
    """The image at `zoom`, whole-number only, so the pixels stay square."""
    if image is None or image.isNull() or zoom <= 1:
        return image
    return image.scaled(image.width() * zoom, image.height() * zoom,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation)


def _compose(mon, category, zoom, state=STATE_CAUGHT):
    """The plate with the frame and the sprite over it, in that state, or None when art is missing.

    THE FRAME IS SMALLER THAN THE AVATAR (17 px against 24), so the sprite overhangs it. That
    is the game's own arrangement - it is why a dex entry looks like a sprite sitting on a
    rounded square - and drawing them in this order is what reproduces it. The avatar goes over
    the frame with the default source-over rule, which keeps the frame visible through the
    sprite's transparent pixels instead of punching a hole in it.

    THE CELL GOES AT ITS AUTHORED POSITION, and the frame carries the offset (3.5 texels, the
    centre of the cell). That is ONE RULE FOR EVERY SPRITE, and it is the point: the atlas was
    drawn with every Coromon placed on the same grid, so the cell is the thing to trust. Trying to
    centre each sprite on its own ART instead was a mistake worth recording - the art's bounding
    box is not the sprite's body, it is the body plus whatever tail, wing or glow that Coromon
    happens to have, and it ranges from x 11 (Volcadon, long tail) to x 14.5 (Aroara, none) in the
    shipped sheet. Nudging each sprite by its own box therefore moved every one of them by a
    different amount and made the whole grid look wrong.

    THE FRAME AND SPRITE ARE COMPOSED AT 1x AND SCALED AFTERWARDS, unlike the plate. Both are 1x
    assets (a 17 px frame, a 24 px cell) and the scale is a whole number, so `_grow` is exact
    either way - and the state's darkening is a per-pixel colour change, which is far cheaper on
    the 30x30 padded cell than on the icon at 5x. Scaling and the darkening commute, so this costs
    nothing in fidelity.

    EVERYTHING IS DRAWN `config.ICON_PAD` TEXELS IN, and that pad is what stops the sprite being
    CUT OFF. The sprite sits 3 texels up and left of the cell so the type frame lands 7 texels into
    it, the way the game aligns the two (`magnet:bottomRight` on a 24 texel container); a sprite
    that reaches its cell's top-left corner therefore reaches PAST the canvas's, and the old
    24 texel buffer simply dropped those pixels - visible only on the larger Coromon, and reported
    as "some of the larger icons are getting cropped both on top and left". The canvas is the
    padded one now, so the whole sprite is drawn, and because every other element takes the same
    pad the composition itself is unchanged - the plate is not one texel further from the frame
    than it was.
    """
    layout = dex.icon_layout(mon.uid, category, zoom)
    sheet = atlas()
    if layout is None or sheet.isNull():
        return None
    x, y, w, h = layout["cell"]
    cell = sheet.copy(x, y, w, h)
    piece = frame(dex.primary_type(mon))
    if cell.isNull() or piece.isNull():
        return None

    pad_x, pad_y = ICON_PAD
    avatar = QImage(avatar_size()[0], avatar_size()[1],
                    QImage.Format.Format_ARGB32_Premultiplied)
    avatar.fill(Qt.GlobalColor.transparent)
    painter = QPainter(avatar)
    if state == STATE_UNKNOWN:
        # A Coromon you have never met does not show its type at all: the game draws its own
        # `UNKNOWN.png` where the frame goes (`hideMonsterAvatar = 'withQuestionMarkAndSilhouette'`).
        unknown_frame = _unknown_frame()
        if not unknown_frame.isNull():
            painter.drawImage(QPoint(pad_x + dex.FRAME_TEXELS, pad_y + dex.FRAME_TEXELS),
                              unknown_frame)
    else:
        offset = layout["frame_offset"] // zoom
        painter.drawImage(QPoint(pad_x + offset, pad_y + offset), piece)
    # The sprite's own nudge is one number for the whole grid - see `config.ICON_NUDGE`. The pad is
    # what keeps a negative one from being sliced off at the canvas edge: the art is 17-23 texels in
    # a 24 texel cell, so it can reach the very corner the sprite is nudged past.
    painter.drawImage(QPoint(pad_x + ICON_NUDGE[0], pad_y + ICON_NUDGE[1]), cell)
    painter.end()

    width, height = icon_size(zoom)
    icon = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    icon.fill(Qt.GlobalColor.transparent)
    painter = QPainter(icon)
    _plate_into(painter, zoom, discovered=state != STATE_UNKNOWN)
    painter.drawImage(QPoint(0, 0), _grow(_darkened(avatar, state), zoom))
    if state == STATE_UNKNOWN:
        _question_mark_into(painter, zoom)
    painter.end()
    return icon


def _darkened(avatar, state):
    """The avatar with the game's own state darkening applied, at 1x.

    WHAT THE GAME DOES (`MonsterAvatar:refreshDarkenedState`): it keeps a second copy of the
    sprite AND its frame duotoned black (`fillEffectHelper:setDuotone(x, 0, 0, 0)`) and draws it
    over the first at `darkenedMonsterAlpha`, while `setSaturation` scales how much colour the
    avatar under it keeps. Painting a black copy over a pixel at alpha `a` multiplies that pixel
    by (1 - a), so this is that multiplication and the saturation mix - see `config.STATE_DARKENING`
    for the values and where each comes from.
    """
    alpha, saturation = STATE_DARKENING[state]
    if alpha <= 0.0 and saturation >= 1.0:
        return avatar
    out = QImage(avatar)
    keep = 1.0 - alpha
    for y in range(out.height()):
        for x in range(out.width()):
            colour = out.pixelColor(x, y)
            if not colour.alpha():
                continue
            red, green, blue = colour.red(), colour.green(), colour.blue()
            if saturation < 1.0:
                luma = 0.299 * red + 0.587 * green + 0.114 * blue
                red = int(round(luma + (red - luma) * saturation))
                green = int(round(luma + (green - luma) * saturation))
                blue = int(round(luma + (blue - luma) * saturation))
            out.setPixelColor(x, y, QColor(int(red * keep), int(green * keep), int(blue * keep),
                                           colour.alpha()))
    return out


def _unknown_frame():
    """`dex.UNKNOWN_FRAME` cropped to its black square, decoded once.

    Like the plate mask, the crop is MEASURED rather than hard-coded: the file is a 24x24 canvas
    with a 17x17 square at (7,7), so it lines up with the frame box on its own.
    """
    if _UNKNOWN[0] is None:
        sheet = QImage(dex.UNKNOWN_FRAME) if os.path.exists(dex.UNKNOWN_FRAME) else QImage()
        crop = QImage()
        if not sheet.isNull():
            left = top = None
            right = bottom = -1
            for y in range(sheet.height()):
                for x in range(sheet.width()):
                    if sheet.pixelColor(x, y).alpha() > 128:
                        left = x if left is None else min(left, x)
                        top = y if top is None else min(top, y)
                        right, bottom = max(right, x), max(bottom, y)
            if left is not None:
                crop = sheet.copy(left, top, right - left + 1, bottom - top + 1)
        _UNKNOWN[0] = crop
    return _UNKNOWN[0]


def _question_mark_into(painter, zoom):
    """The "?" of a never-seen entry, where the game draws it.

    The game adds it as TEXT - `textHelper:new(container, 'outline_10_bold', {text='?', fillColor =
    questionMarkColor})` followed by `magnet:bottomRight(questionMark, 6.0, 0.0, container)` - so
    it is the same font as the number, in the entry's own text colour (`UI_TEXT_RED_DISABLED`).
    The anchor is 6 texels in from the entry box's right edge and flush with its bottom, which puts
    the glyph's ink at 4.5 texels in from the FRAME's left edge and 4 texels down from its top;
    measured against the game's own screen, that is where its "?" sits too (x 628..650 against a
    frame box at x 605..689).
    """
    right = (ICON_PAD[0] + dex.FRAME_TEXELS + QUESTION_MARK[0]) * zoom
    bottom = (ICON_PAD[1] + dex.FRAME_TEXELS + QUESTION_MARK[1]) * zoom
    _tinted_text(painter, "?", zoom, STATE_NUMBERS[STATE_UNKNOWN], right, bottom)


def _tinted_text(painter, text, zoom, colour, right, bottom):
    """Game-font text in `colour`, drawn onto `painter` with its bottom-right at (right, bottom).

    COLOURING THE FILL ONLY, the way the game's text does: the glyphs are white with a black
    outline, and the font multiplies the wanted colour into the fill, which leaves the outline
    black. (The first attempt painted the colour over the whole scratch image with
    `CompositionMode_Multiply`, which Qt also applies to the scratch's TRANSPARENT pixels - so
    every icon came out washed in its state colour.)
    """
    font.draw_number(painter, text, zoom, right, bottom, colour)


def _from_strip(uid, category, zoom):
    """Fallback for a Coromon the atlas does not list: one frame of its idle strip.

    The frame is shrunk to the icon size by whole-number subsampling, which is the only
    scaling Tk had and the only one that does not smear pixel art. Used when the game's own
    sprite order is unavailable - see `dex.atlas_index`. `dex.strip_frame` tries the potential
    category first and then the other skins, so this is category-aware for free.
    """
    crop = dex.strip_frame(uid, category)
    if crop is None:
        return None
    path, x1, y1, x2, y2 = crop
    sheet = QImage(path)
    if sheet.isNull():
        return None
    piece = sheet.copy(x1, y1, x2 - x1, y2 - y1)
    over = max(1, -(-max(piece.width(), piece.height()) // dex.CELL))
    if over > 1:
        piece = piece.scaled(max(1, piece.width() // over), max(1, piece.height() // over),
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.FastTransformation)
    return _grow(piece, zoom)
