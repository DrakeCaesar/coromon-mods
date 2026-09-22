"""The game's own bitmap font, read out of the files the game ships.

WHY THIS EXISTS. The dex numbers are text, and matching the game's text means matching its
FONT - which is not a font file at all:

  * the pictures are `Resources/fonts/outline_10_bold_00.png`, a PACKED 512x1024 atlas. Glyph
    rectangles are in no regular order, so the letters cannot be cut out by arithmetic on rows
    and columns, and the atlas has to be read (it is, once, in `fonts/`).
  * the geometry is a generated module inside `Resources/resource.car`:
    `fonts.outline_10_bold_00.lu` is a table of `{name, frame, width, height, xOffset, yOffset,
    xAdvance}` per glyph plus the frame rectangles, and `frame` indexes them. It is Lua
    BYTECODE that builds the table, and the table cannot be recovered from the PNG - so the
    chunk is executed (`qr_luadata`) rather than guessed at.
  * the sizes are in a different unit than the icons: the font atlas is 4x oversampled, i.e.
    `FONT_TEXELS = 4` atlas pixels to one icon texel. Measured against the game's own database
    screen: the digit `1` is 20x32 atlas pixels and draws 5x8 texels there, so it is drawn at
    exactly `zoom / 4` pixels per atlas pixel - 1.25 at the window's 5x zoom, the same
    sub-pixel size the game itself renders, which is why this does not round the scale up.

The glyphs are drawn AS THE ATLAS HAS THEM - white fill with a black outline - and the window
recolours the FILL to the colour the entry's state calls for (`config.STATE_NUMBERS`), the same
thing the game does through `fillColor`/its `fontOutlineColor` filter. The outline stays black:
see `glyph_image`.

Only `outline_10_bold` is wired up, because the database screen's numbers are what this is for
(`textHelper:new(parent, 'outline_10_bold', ...)` in `monsterDatabaseScreen`). `outline_8`,
`outline_10` and the `plain_*` faces are the same mechanism with another module name.
"""

import math
import os

import car_extract
import dex
import qr_luadata
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage


def _whole(value):
    """`value` rounded to the nearest whole pixel, halves UP.

    NOT `round()`, which rounds a half to the EVEN pixel: that makes the position depend on
    whether the pen happens to land on a .5, so shifting a number or a "?" by a whole number of
    texels could move its outline by a pixel. Rounding halves up is translation-invariant - add
    15 px to the anchor and every glyph pixel lands 15 px further over - which is what lets the
    icon canvas carry `config.ICON_PAD` without the text being rasterised differently. The pen
    really does land on halves here: the digits advance in atlas pixels at a 1.25 scale, so an
    anchor at a half texel is the normal case, not a corner one.
    """
    return int(math.floor(value + 0.5))

# The font the database screen draws its numbers with, and the atlas's oversampling: 4 atlas
# pixels to one icon texel (so the em box of the digits, 32 atlas pixels, is 8 texels).
FONT_KEY = "outline_10_bold"
FONT_TEXELS = 4


class Glyph:
    """One character: where it is on the atlas, and how it advances the pen."""

    __slots__ = ("image", "advance", "x_offset", "y_offset", "descent")

    def __init__(self, rect, advance, x_offset, y_offset):
        self.image = rect            # QRect on the atlas, in atlas pixels
        self.advance = advance       # atlas pixels
        self.x_offset = x_offset     # atlas pixels, to the glyph's left edge
        self.y_offset = y_offset     # atlas pixels, from the glyph's top to the baseline
        self.descent = rect.height() - y_offset   # atlas pixels below the baseline


class BitmapFont:
    """The font's glyph table plus its atlas, decoded once per process."""

    _CACHE = {}

    def __init__(self, key=FONT_KEY):
        self.key = key
        self.glyphs = {}
        self.image = QImage()
        self.descent = 0
        self._painted = {}           # (char, zoom, colour) -> the scaled, tinted glyph
        self._load()

    # ------------------------------------------------------------------ loading

    def _load(self):
        sheet = _sheet(self.key)
        if not sheet:
            return
        frames = sheet.get("sheetOptions", {}).get("frames") or {}
        for record in (sheet.get("glyphs") or {}).values():
            glyph = _glyph(record, frames)
            if glyph is not None:
                self.glyphs[_character(record.get("name", ""))] = glyph
        self.descent = max((g.descent for g in self.glyphs.values()), default=0)
        name = sheet["sheetOptions"].get("file") or ""
        path = os.path.join(dex.RES, *str(name).split("/"))
        if os.path.exists(path):
            self.image = QImage(path)

    @classmethod
    def get(cls, key=FONT_KEY):
        """The font, or None when the game's files are not where they should be."""
        if key not in cls._CACHE:
            font = cls(key)
            cls._CACHE[key] = font if font.glyphs and not font.image.isNull() else None
        return cls._CACHE[key]

    # ------------------------------------------------------------------ measuring and drawing

    def advance(self, text):
        """How wide `text` is in atlas pixels (the pen's total travel)."""
        return sum(self.glyphs[ch].advance for ch in text if ch in self.glyphs)

    def descent_of(self, text):
        """How far below the baseline `text` reaches, in atlas pixels, for the glyphs IT uses.

        THE STRING'S OWN DESCENT, not the font's maximum (48 atlas pixels, set by a decorative
        glyph no number contains). The game trims its text boxes to the glyphs drawn - that is
        what `textVerticalCenterCorrectionWhenNotTrimmed` in its font list is about - and the
        measurement of its own database screen agrees: a digit's 16-pixel descent puts the
        number's bottom edge exactly on the entry box's bottom edge, which is where the font's
        global descent would have left it 8 texels adrift.
        """
        return max((self.glyphs[ch].descent for ch in text if ch in self.glyphs), default=0)

    def draw(self, painter, text, zoom, right, bottom, colour=None):
        """Draw `text` right-aligned to `right` and sitting on `bottom`, in pixels.

        `right`/`bottom` are the BOTTOM-RIGHT of the text's box, which is what the game anchors:
        the database screen places its number with `magnetRightToLeftAware:bottomRight(text, 2, 0,
        parent)`, and `parent` is the entry's 19-unit box - so the box's right edge is 2 units
        past that box and its bottom edge is that box's bottom. The descent then puts the digits'
        baseline above the bottom edge, which is why the glyphs land where they do.

        `colour` recolours the glyph FILL and leaves the outline black - how the game's own text
        works, where the atlas is the white-on-black stamp and `fillColor` picks the ink (see
        `glyph_image`). The atlas is scaled by `zoom / FONT_TEXELS`; the destination rectangles are
        rounded to whole pixels so a glyph never lands on half a pixel of the screen.
        """
        scale = zoom / float(FONT_TEXELS)
        pen = right - self.advance(text) * scale
        baseline = bottom - self.descent_of(text) * scale
        for ch in text:
            glyph = self.glyphs.get(ch)
            if glyph is None:
                continue
            piece = self.glyph_image(ch, zoom, colour)
            if piece is None:
                pen += glyph.advance * scale
                continue
            painter.drawImage(QPoint(_whole(pen + glyph.x_offset * scale),
                                     _whole(baseline - glyph.y_offset * scale)), piece)
            pen += glyph.advance * scale

    def glyph_image(self, char, zoom, colour=None):
        """One glyph at `zoom`, optionally with its fill in `colour`, made once and kept.

        THE FILL IS COLOURED BY MULTIPLYING, which is what the artwork is for: the glyph is white
        with a black outline, so multiplying a pixel by the wanted colour turns the white into it
        and leaves the black outline black. Painting the colour over the glyph would recolour the
        outline too, and a whole-image blend is worse still - it washes the colour over every
        transparent pixel as well. Doing it per glyph also makes it CACHEABLE: the tints depend on
        the glyph, the zoom and the colour and nothing else, so the 354 database cells share ten
        tinted digits instead of re-tinting thousands of pixels each.
        """
        key = (char, zoom, colour)
        if key not in self._painted:
            glyph = self.glyphs.get(char)
            if glyph is None:
                self._painted[key] = None
            else:
                rect = glyph.image
                piece = self.image.copy(rect).scaled(
                    int(round(rect.width() * zoom / float(FONT_TEXELS))),
                    int(round(rect.height() * zoom / float(FONT_TEXELS))),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                if colour is not None:
                    piece = piece.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                    wash = QColor(colour)
                    for y in range(piece.height()):
                        for x in range(piece.width()):
                            pixel = piece.pixelColor(x, y)
                            if not pixel.alpha():
                                continue
                            # premultiplied already, so scaling the channels IS the multiply
                            piece.setPixelColor(x, y, QColor(
                                pixel.red() * wash.red() // 255,
                                pixel.green() * wash.green() // 255,
                                pixel.blue() * wash.blue() // 255, pixel.alpha()))
                self._painted[key] = piece
        return self._painted[key]


def draw_number(painter, number, zoom, right, bottom, colour=None):
    """Draw a dex number in the game's font. Returns False when the font is unavailable.

    False is a real answer and not an error: it is what happens when the tool is pointed at a
    games folder it cannot read, and the caller draws the entry without a number rather than
    crashing.
    """
    font = BitmapFont.get()
    if font is None:
        return False
    font.draw(painter, str(number), zoom, right, bottom, colour)
    return True


def available():
    return BitmapFont.get() is not None


# ---------------------------------------------------------------------- reading the game's module


def _sheet(key):
    """Run `fonts.<key>_00.lu` out of resource.car and return the table it builds.

    The chunk is straight-line table construction, so executing it is the faithful read: the
    glyph names (`Kroeger_10_bold_0x30` - the character's code point in hex), the atlas
    rectangles and the advances are all only in there. Anything that goes wrong (no car, no
    module, a runtime the VM cannot follow) comes back as None.
    """
    data = _module_bytes("fonts.%s_00.lu" % key)
    if not data:
        return None
    try:
        result = qr_luadata.run_data(data, tolerant=True)
    except Exception:      # a broken/updated car must not take the window down with it
        return None
    table = result[0] if result else None
    return table if isinstance(table, dict) else None


def _module_bytes(name):
    car = os.path.join(dex.RES, "resource.car")
    if not os.path.exists(car):
        return None
    try:
        for typ, offset, entry in car_extract.parse_toc(car):
            if entry == name:
                return car_extract.read_entry(car, typ, offset)[3]
    except Exception:
        return None
    return None


def _character(name):
    """`Kroeger_10_bold_0x30` -> '0'. The name carries the code point, in hex."""
    _, _, code = str(name).rpartition("0x")
    try:
        return chr(int(code, 16))
    except ValueError:
        return ""


def _glyph(record, frames):
    rect = frames.get(record.get("frame"))
    if not rect:
        return None
    return Glyph(QRect(int(rect["x"]), int(rect["y"]), int(rect["width"]), int(rect["height"])),
                 int(record.get("xAdvance", 0)),
                 int(record.get("xOffset", 0)),
                 int(record.get("yOffset", 0)))
