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
"""

import os

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap

import dex

_ATLAS = None                 # the 768x744 avatar sheet, decoded once for the whole run
_FRAMES = {}                  # type name -> the 17 px frame image
_ICONS = {}                   # uid -> QImage, or None when the Coromon has no artwork
_PIXMAPS = {}                 # (uid, zoom) -> QPixmap


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


def icon_image(mon):
    """That Coromon's dex icon as a 24 px QImage, or None when it has no artwork.

    None is a real answer, not a failure: the titans and a few others have no dex entry in the
    atlas and no idle strip either, and their row is simply text.
    """
    if mon.uid not in _ICONS:
        _ICONS[mon.uid] = _compose(mon) or _from_strip(mon.uid)
    return _ICONS[mon.uid]

def icon_pixmap(mon, zoom=1):
    """The icon ready to be drawn at `zoom`, scaled nearest-neighbour so pixel art stays crisp.

    Whole-number upscaling with FastTransformation is exact - 2x of a 24 px cell is the 48 px
    icon the game shows, not a blurry approximation of it.
    """
    key = (mon.uid, zoom)
    if key not in _PIXMAPS:
        image = icon_image(mon)
        if image is not None and zoom > 1:
            image = image.scaled(image.width() * zoom, image.height() * zoom,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
        _PIXMAPS[key] = QPixmap.fromImage(image) if image is not None else None
    return _PIXMAPS[key]


def _compose(mon):
    """The frame with the avatar copied over it, or None when either piece is missing.

    THE FRAME IS SMALLER THAN THE AVATAR (17 px against 24), so the sprite overhangs it. That
    is the game's own arrangement - it is why a dex entry looks like a sprite sitting on a
    rounded square - and drawing them in this order is what reproduces it. The avatar goes over
    the frame with the default source-over rule, which keeps the frame visible through the
    sprite's transparent pixels instead of punching a hole in it.
    """
    layout = dex.icon_layout(mon.uid)
    sheet = atlas()
    if layout is None or sheet.isNull():
        return None
    x, y, w, h = layout["cell"]
    cell = sheet.copy(x, y, w, h)
    piece = frame(dex.primary_type(mon))
    if cell.isNull() or piece.isNull():
        return None
    icon = QImage(dex.CELL, dex.CELL, QImage.Format.Format_ARGB32_Premultiplied)
    icon.fill(Qt.GlobalColor.transparent)
    painter = QPainter(icon)
    offset = layout["frame_offset"]
    painter.drawImage(QPoint(offset, offset), piece)
    painter.drawImage(QPoint(0, 0), cell)
    painter.end()
    return icon


def _from_strip(uid):
    """Fallback for a Coromon the atlas does not list: one frame of its idle strip.

    The frame is shrunk to the icon size by whole-number subsampling, which is the only
    scaling Tk had and the only one that does not smear pixel art. Used when the game's own
    sprite order is unavailable - see `dex.atlas_index`.
    """
    crop = dex.strip_frame(uid)
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
    return piece
