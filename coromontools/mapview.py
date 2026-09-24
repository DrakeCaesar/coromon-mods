"""The area map: which patch of grass "Grass B" actually is.

The wiki lists encounter rates per area as "Grass A / Grass B / ..." and never says which patch
each one is. The game's maps do: a patch carries a `grassArea` marker whose zoneUID IS that
letter. So this draws the area - with the map's OWN TILES as the ground (`maptiles`) - and every
zone on it as a bordered block, the selected one picked out.

A ZONE IS DRAWN AS MERGED BLOCKS, not as the loose cells the map records: a grass patch is already a
connected run of its own tileset, but a zone that names no tile layer (every `_WATER` zone, and the
underwater routes) records its shape as hundreds of separate marker cells - drawn one at a time that
is a field of dots, merged it is the body of water the player means (the user: "the zones just as a
bordered blocks, merging adjanced blocks like the water etc"). `encounter_zones.zone_blocks` does the
merging and `block_outline` the border, so the outline follows a shape of any form and two adjacent
cells share one line.

Drawn in `paintEvent`, not "when the selection changes". That is not a style choice, it removes
two hacks: Qt only paints a widget that is on screen, so the Tk version's
`if not canvas.winfo_ismapped(): return` guard is unnecessary, and because the scale is
recomputed per paint, a resize redraws itself instead of needing the `update_idletasks` call
that existed to force the canvas to have a size before it could be drawn into.

THE VIEW SCROLLS AND ZOOMS. It is a `QAbstractScrollArea`, so a map drawn larger than the pane gets
scrollbars for free; the zoom is `config.MAP_ZOOM_STEPS` - "fitted to the pane" at x1, which is what
this always drew, and the -/+ buttons (`MapZoomBar`) walk up from there. The value belongs to the
WINDOW rather than to a pane: three tabs draw a map, and a zoom that meant something different in each
of them would be a bug (see `set_zoom`).
"""

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractScrollArea, QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QWidget)

import encounter_zones as ez

from . import mapnames, maptiles
from .config import (CHIP_FONT_PX, CHIP_HEIGHT, CHIP_MIN_WIDTH, EDGE_WIDTH, LABEL_MAX_PX,
                     LABEL_PX, MAP_MAX_SCALE, MAP_SMOOTH_DEFAULT, MAP_SMOOTH_KEY,
                     MAP_ZOOM_DEFAULT, MAP_ZOOM_KEY, MAP_ZOOM_STEPS, PATCH_ALPHA, SELECTED_EDGE,
                     SELECTED_EDGE_WIDTH, SELECTED_LABEL_MAX_PX, SELECTED_LABEL_PX, SPOTS_SHOWN)
from .theme import FIELD, MAP_GROUND
from .widgets import note

# HOW WIDE THE -/+ BUTTONS ARE, in pixels: the theme pads a button 12 px a side, so a button is given
# its own zero padding and a fixed size, exactly like the Database tab's dex-zoom pair.
ZOOM_BUTTON_WIDTH = 22


class _Zoom(QObject):
    """The window's map state: one zoom and one scaling filter, every map watching them.

    Module-level values rather than parameters threaded through three tabs, because they ARE one
    value each - the map is drawn in three places and they must agree. `ui_smoke` checks that a step
    moves all of them.
    """

    changed = Signal(float)
    filter_changed = Signal(bool)


_ZOOM = _Zoom()
_ZOOM_VALUE = MAP_ZOOM_DEFAULT
# ... AND THE SCALING FILTER, on by default: the picture is usually drawn smaller than its own pixels
# (see `config.MAP_SMOOTH_*`), where nearest neighbour is moire and smooth is the readable choice.
_SMOOTH = MAP_SMOOTH_DEFAULT
_MAPS = []          # every live ZoneMap, so one step redraws them all


def zoom():
    """The current zoom factor: 1.0 is the map fitted to the pane."""
    return _ZOOM_VALUE


def smooth():
    """Whether the map is scaled with the smooth filter (True) or nearest neighbour (False)."""
    return _SMOOTH


def set_smooth(enabled):
    """Turn the filter on or off for every map and redraw them, without touching the view.

    A REDRAW, not a re-plan: the blocks are geometry and do not move - only the pixels of the map
    picture under them are resampled.
    """
    global _SMOOTH
    _SMOOTH = bool(enabled)
    for widget in list(_MAPS):
        widget.viewport().update()
    _ZOOM.filter_changed.emit(_SMOOTH)
    return _SMOOTH


def set_zoom(factor):
    """Set the window's map zoom, snapped to the nearest step, and redraw every map.

    SNAPPED, not clamped: the steps are the values the buttons walk (`config.MAP_ZOOM_STEPS`), so a
    saved 1.37 from some earlier tune lands back on 1.25 rather than being carried as a factor nothing
    can produce again.
    """
    global _ZOOM_VALUE
    step = min(MAP_ZOOM_STEPS, key=lambda value: abs(value - float(factor)))
    _ZOOM_VALUE = step
    for widget in list(_MAPS):
        widget.zoom_changed()
    _ZOOM.changed.emit(_ZOOM_VALUE)
    return _ZOOM_VALUE


def step_zoom(direction):
    """One press of + or -: the next step up or down, and the value it landed on."""
    index = MAP_ZOOM_STEPS.index(_ZOOM_VALUE) if _ZOOM_VALUE in MAP_ZOOM_STEPS else 0
    index = max(0, min(len(MAP_ZOOM_STEPS) - 1, index + (1 if direction > 0 else -1)))
    return set_zoom(MAP_ZOOM_STEPS[index])


class MapZoomBar(QWidget):
    """The map's -/+ buttons with the factor between them, which resets the view to "fit".

    One of these sits over every map; all of them show the same value and move the same zoom (see
    `set_zoom`), which is what makes the control mean "the map" rather than "this pane".
    """

    def __init__(self, prefs=None, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        self.zoom_out = QPushButton("-")
        self.zoom_in = QPushButton("+")
        self.fit_button = QPushButton("fit")
        for button, tip, direction in ((self.zoom_out, "smaller map", -1),
                                       (self.zoom_in, "bigger map", 1)):
            button.setToolTip(tip)
            button.clicked.connect(lambda *_a, step=direction: self.step(step))
        # THE THEME'S 12 px SIDE PADDING IS WIDER THAN THIS BUTTON: with it left on, Qt has no content
        # rect and draws an empty square (the same trap the dex-zoom pair documents). Only the SIDES are
        # dropped - the 4 px top and bottom are what make a button a button, and taking them off left the
        # two controls standing "only as tall as the chars" against the theme's own 22 px (the user).
        self.zoom_out.setStyleSheet("padding: 4px 0;")
        self.zoom_in.setStyleSheet("padding: 4px 0;")
        self.fit_button.setStyleSheet("padding: 4px 6px;")
        self.fit_button.setToolTip("fit the map to the pane again (zoom x1)")
        self.fit_button.clicked.connect(lambda *_a: self.step(None))
        # AND THE THREE ARE THE SAME HEIGHT, taken from the tallest of them: a `-` next to a `fit` that
        # is one text line taller is the kind of thing that reads as broken.
        height = max(button.sizeHint().height()
                     for button in (self.zoom_out, self.zoom_in, self.fit_button))
        for button in (self.zoom_out, self.zoom_in):
            button.setFixedSize(ZOOM_BUTTON_WIDTH, height)
        self.fit_button.setFixedHeight(height)
        box.addWidget(self.zoom_out)
        box.addWidget(self.zoom_in)

        self.value = QLabel("")
        self.value.setToolTip("the map's zoom - click to fit it to the pane again")
        box.addWidget(self.value)
        box.addWidget(self.fit_button)
        box.addStretch(1)

        _ZOOM.changed.connect(self.show_value)
        self.show_value(zoom())

    def step(self, direction):
        """Walk the zoom steps (or go back to fit when `direction` is None), and remember it."""
        value = set_zoom(MAP_ZOOM_DEFAULT) if direction is None else step_zoom(direction)
        if self.prefs is not None:
            self.prefs.set(MAP_ZOOM_KEY, value)

    def show_value(self, factor):
        self.value.setText("x%g" % factor)
        self.zoom_out.setEnabled(factor > MAP_ZOOM_STEPS[0])
        self.zoom_in.setEnabled(factor < MAP_ZOOM_STEPS[-1])


def head_row(head, prefs=None):
    """The row every map panel wears: its caption (elastic), the zoom controls and the filter tick.

    The caption is the map's own headline, which a tab shows only when the map could NOT draw - so it
    is allowed to be narrower than its text and the controls keep their place either way.
    """
    row = QWidget()
    box = QHBoxLayout(row)
    box.setContentsMargins(0, 0, 0, 0)
    head.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    box.addWidget(head, 1)
    box.addWidget(MapZoomBar(prefs))
    box.addWidget(MapFilterCheck(prefs))
    return row


class MapFilterCheck(QCheckBox):
    """The scaling-filter tick every map panel wears, and the state it writes back.

    A CHECKBOX RATHER THAN A BUTTON because it is a state and not an action, and its tooltip says what
    each state is for: the picture is usually drawn SMALLER than its own pixels, where the smooth
    filter is the readable one, and blown up past 1:1 the nearest neighbour the game itself draws with
    is the crisp one.
    """

    def __init__(self, prefs=None, parent=None):
        super().__init__("smooth", parent)
        self.prefs = prefs
        self.setToolTip("scale the map with the smooth filter (unticked: nearest neighbour, which is "
                        "what the game itself draws with, and crisper when zoomed in past x1)")
        self.setChecked(smooth())
        self.toggled.connect(self._changed)
        _ZOOM.filter_changed.connect(self.setChecked)

    def _changed(self, on):
        set_smooth(on)
        if self.prefs is not None:
            self.prefs.set(MAP_SMOOTH_KEY, on)


def fill_legend(box, legend, note_text=None):
    """Fill a legend row from a map's `legend`: one chip per zone, then the small print.

    THE CHIP IS SIZED TO ITS OWN TEXT. The labels are not all one letter - every water zone ends in
    "WATER" and the event zones in "SPECIAL" - and a fixed 22 px square wearing the window's font cut
    both of them off (the user: "in the legend, labels like WATER don't fit the small square at the
    top"). The chip's font is the small print's own size, and the square stays a square for a single
    letter because that is what the letters look like on the map.

    One place for all three tabs, so a chip cannot be a different size in each of them.
    """
    while box.count():
        item = box.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
    for (letter, colour, selected) in legend:
        chip = QLabel(letter)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = chip.font()
        font.setPixelSize(CHIP_FONT_PX)
        font.setBold(True)
        chip.setFont(font)
        chip.setFixedSize(max(CHIP_MIN_WIDTH, chip.fontMetrics().horizontalAdvance(letter) + 8),
                          CHIP_HEIGHT)
        # the selected zone's chip wears the same white border the selected block is drawn with, which
        # is the only thing telling it apart now that every fill is translucent
        chip.setStyleSheet("background: %s; color: #ffffff; border: %s;"
                           % (colour, "1px solid %s" % SELECTED_EDGE if selected else "none"))
        chip.setToolTip("the selected zone" if selected else "")
        box.addWidget(chip)
    if note_text:
        box.addWidget(note(note_text), 1)
    else:
        box.addStretch(1)


def label_cell(block):
    """The cell of `block` to write the zone's name on: the one nearest the middle of its box.

    THE MIDDLE OF THE BOUNDING BOX IS OFTEN NOT IN THE BLOCK. An L, a ring, or two patches that only
    touch at a corner all leave the box's centre in a hole, and the name then floats over the map with
    no zone under it - the user: "currently we put the labels in the center of a group - but that could
    easily be outside of the group - we need to still put them into the group". The nearest CELL is
    always inside it, and ties are broken by the cell's own corner so the answer is stable.
    """
    xs = [tile[0] for tile in block]
    ys = [tile[1] for tile in block]
    cx = (min(xs) + max(xs) + 1) / 2.0
    cy = (min(ys) + max(ys) + 1) / 2.0
    return min(block, key=lambda tile: ((tile[0] + 0.5 - cx) ** 2 + (tile[1] + 0.5 - cy) ** 2,
                                        tile[1], tile[0]))


class ZoneMap(QAbstractScrollArea):
    """One map, drawn with its own tiles and its encounter zones as translucent bordered blocks.

    What will be drawn is worked out in `set_zone`, not in `paintEvent`: the blocks come from the map
    JSON and have to be turned into horizontal runs, and a resize is many repaints, so doing it per
    paint would make dragging the window edge stutter. Painting then only scales and fills rectangles.

    `empty` is the headline for having no zone at all, and it is a parameter because THREE tabs draw
    this map now: the first tab's map belongs to its ranking ("pick a zone on the first tab" would be
    nonsense inside the Database tab, which is the tab it was said on).
    """

    def __init__(self, parent=None, empty="pick a zone on the first tab"):
        super().__init__(parent)
        self.empty = empty
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # NO FRAME: this is a pane in a panel, not a boxed field, and a frame would inset the map by
        # its own width on top of the scrollbars.
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.zone = None
        self.headline = empty
        self.legend = []      # [(letter, colour, is_selected)] for the tab to display
        self._plan = None
        self._scale = 1.0     # cells -> pixels, recomputed per paint and by `_update_scrollbars`
        _MAPS.append(self)
        self.destroyed.connect(self._forget)

    def _forget(self, *_args):
        if self in _MAPS:
            _MAPS.remove(self)

    # ------------------------------------------------------------------ zoom
    def zoom_changed(self):
        """The window's zoom moved: the scroll ranges and the picture both change with it.

        THE VIEW STAYS WHERE IT WAS LOOKING - the map point at the middle of the pane is the one that
        stays under the middle, which is what every other zoom does. Without it a + press would snap the
        view back to the top-left corner of the map and the zone being examined would leave the pane.
        """
        centre = self._view_centre()
        self._update_scrollbars()
        self.centre_on(centre)
        self.viewport().update()

    # ------------------------------------------------------------------ input
    def set_zone(self, zone):
        """Point the map at a zone. Also sets `headline` and `legend` for the tab to show.

        RETURNS whether it had a map to draw. A caller that shows `headline` as a caption can use
        that to show it only when the map could not answer - "no map file for X", "X is not marked
        on the map of Y" - instead of captioning every map with a line of coordinates.
        """
        self.zone = zone
        self._plan = None
        self.legend = []
        if zone is None:
            self.headline = self.empty
            self._update_scrollbars()
            self.viewport().update()
            return False
        data = ez.zone_map(zone.map_file)
        if data is None:
            self.headline = "no map file found for %s" % zone.map_file
            self._update_scrollbars()
            self.viewport().update()
            return False
        m, layers, zones = data
        # the zones this map actually marks: the ranking can name one the map file has no marker
        # for, and saying so is better than drawing an empty frame
        drawn = {name: z for name, z in zones.items() if z["patches"] or z["unplaced"]}
        entry = drawn.get(zone.name)
        if entry is None:
            self.headline = ("%s is not marked on the map of %s - it has %s"
                             % (zone.name, mapnames.area(zone.map_file),
                                ", ".join(sorted(drawn)) or "no zones"))
            self._update_scrollbars()
            self.viewport().update()
            return False
        self._plan = self._plan_map(m, layers, drawn, zone)
        self.headline = self._describe(zone, entry)
        self.legend = [(name.rsplit("_", 1)[-1], ez.colour_for(name), name == zone.name)
                       for name in sorted(drawn)]
        self._update_scrollbars()
        # THE VIEW DOES NOT MOVE WHEN THE SELECTION DOES. It used to scroll the picked zone's own
        # block into sight, which reads well once and is wrong the moment you are comparing rows: the
        # map jumps under the cursor on every click. The scroll is the user's now - the zoom buttons
        # keep the view centred on what it was showing, and that is the only thing that moves it
        # (the user: "it looks like it auto scrolls the maps to focus on the selected area - that
        # should not happen").
        self.viewport().update()
        return True

    # ------------------------------------------------------------------ planning
    def _plan_map(self, m, layers, drawn, zone):
        """Everything to draw, as numbers in map cells - the paint only scales it."""
        patches = []
        for name in sorted(drawn):
            other = drawn[name]
            selected = name == zone.name
            # EVERY ZONE GETS THE SAME TRANSLUCENT FILL, the selected one included - the user: "some of
            # the blocks show as opaque, they should all be semi transparent with solid edges around
            # the zones". The selected zone is told apart by its edge instead (white, a little wider -
            # see `_paint_outline`), which is also what its legend chip wears.
            fill = QColor(ez.colour_for(name))
            fill.setAlpha(PATCH_ALPHA)
            edge = QColor(SELECTED_EDGE if selected else ez.colour_for(name))
            blocks = []
            for index, block in enumerate(ez.zone_blocks(other)):
                horizontal, vertical = ez.block_outline(block)
                xs = [tile[0] for tile in block]
                ys = [tile[1] for tile in block]
                # EVERY AREA IS NAMED, so a zone that merges into several of them (a water zone's 41)
                # is readable all over rather than being in one place and implied elsewhere. A BLOCK OF
                # ONE CELL IS A MARKER, NOT AN AREA - and there are 72 of those over the shipped maps,
                # each of them a name as wide as the text on a single tile - so only the biggest block
                # is named whatever its size (a zone must never be nameless) and the rest have to be
                # worth naming: the user: "to reduce the number of labels".
                cell = label_cell(block)
                labels = [(cell[0] + 0.5, cell[1] + 0.5, name.rsplit("_", 1)[-1], selected)] \
                    if index == 0 or len(block) > 1 else []
                blocks.append({"runs": ez.runs_by_row(ez.cells_by_row(block)),
                               "outline": (horizontal, vertical), "labels": labels,
                               # how many cells it is, and the box it occupies in cells - the first
                               # is what decides whether a lone marker cell is worth naming, the
                               # second is what the caller scrolls to
                               "cells": len(block),
                               "bbox": (min(xs), min(ys), max(xs) + 1, max(ys) + 1)})
            patches.append({"fill": fill, "edge": edge, "blocks": blocks, "selected": selected})
        # unselected first, so the selected zone is never overdrawn - its white edge has to survive a
        # zone that overlaps it
        patches.sort(key=lambda patch: patch["selected"])
        return {"size": (m["width"], m["height"]),
                # THE MAP'S OWN TILES, if they could be read - the ground the blocks are drawn on.
                # `paintEvent` falls back to the flat terrain footprint when this is None.
                "picture": maptiles.picture(zone.map_file),
                "ground": list(ez.runs_by_row(ez.cells_by_row(ez.terrain_cells(m, layers)))),
                "patches": patches}

    def _describe(self, zone, entry):
        """The one-line answer to "where is it", shown above the map."""
        blocks = ez.zone_blocks(entry)
        spots = ", ".join("(%d,%d)" % (min(tile[0] for tile in block), min(tile[1] for tile in block))
                          for block in blocks[:SPOTS_SHOWN])
        if len(blocks) > SPOTS_SHOWN:
            # a titan temple map can mark 137 blocks, and the line is a label: all of them at
            # once is a paragraph of coordinates nobody reads
            spots += ", ..."
        cells = sum(len(block) for block in blocks)
        text = "%s   %d block(s), %d tiles   at %s" % (zone.name, len(blocks), cells, spots or "-")
        # HOW MUCH OF THAT SHAPE IS A MARKER RATHER THAN A TILE REGION, said out loud: a water zone
        # is cells the map never tied to a tile layer, and the merge is the only thing that makes a
        # block out of them (see `encounter_zones.zone_blocks`).
        if entry["unplaced"]:
            text += "   - %d marker cell(s) merged" % len(entry["unplaced"])
        return text

    # ------------------------------------------------------------------ painting
    def _map_scale(self):
        """Cells -> pixels: the map fitted to the viewport, times the window's zoom.

        NEVER BELOW 1:1 (a map bigger than the pane is scrolled, not shrunk to mush - and that floor is
        what makes the scrollbars worth having). The FIT is capped at `MAP_MAX_SCALE`, and the cap is
        applied there - to the fit - rather than to the finished scale: capping the product capped the
        ZOOM, so a map that fits at 2.1 px a cell drew the same picture at x4, x5 and x6 while the
        label kept counting, and the user reported the buttons as dead. Every step now multiplies the
        same base.
        """
        if not self._plan:
            return 1.0
        width, height = self._plan["size"]
        if width <= 0 or height <= 0:
            return 1.0
        fit = min(self.viewport().width() / width, self.viewport().height() / height)
        return max(1.0, min(fit, MAP_MAX_SCALE)) * zoom()

    def _drawn_size(self):
        """The map's size in pixels at the current scale - what the scroll ranges are laid over."""
        if not self._plan:
            return (0, 0)
        width, height = self._plan["size"]
        return (int(round(width * self._scale)), int(round(height * self._scale)))

    def _view_centre(self):
        """Where the pane is pointing, as a fraction of the whole map - so it survives a zoom."""
        drawn_w, drawn_h = self._drawn_size()
        if drawn_w <= 0 or drawn_h <= 0:
            return (0.0, 0.0)
        viewport = self.viewport()
        return ((self.horizontalScrollBar().value() + viewport.width() / 2.0) / drawn_w,
                (self.verticalScrollBar().value() + viewport.height() / 2.0) / drawn_h)

    def centre_on(self, fraction):
        """Scroll so that point of the map (as a fraction of it) is in the middle of the pane.

        The scrollbars clamp it themselves, so asking for a point outside the map - which is what
        centring on a zone near an edge does - lands on the edge rather than past it.
        """
        drawn_w, drawn_h = self._drawn_size()
        viewport = self.viewport()
        self.horizontalScrollBar().setValue(
            int(round(fraction[0] * drawn_w - viewport.width() / 2.0)))
        self.verticalScrollBar().setValue(
            int(round(fraction[1] * drawn_h - viewport.height() / 2.0)))

    def _update_scrollbars(self):
        """Make the scroll ranges match the map at the current zoom and pane size.

        Called from `set_zone`, from a resize and from a zoom change - the three things that move the
        target - and it is also where `self._scale` is refreshed, because the paint has to use the same
        number the scrollbars were laid out with.
        """
        self._scale = self._map_scale()
        viewport = self.viewport().size()
        drawn_w, drawn_h = self._drawn_size()
        for bar, span, have in ((self.horizontalScrollBar(), drawn_w, viewport.width()),
                                (self.verticalScrollBar(), drawn_h, viewport.height())):
            bar.setRange(0, max(0, span - have))
            bar.setPageStep(have)
            bar.setSingleStep(max(8, have // 8))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scrollbars()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor(FIELD))
        plan = self._plan
        if plan is not None:
            # THE SCROLL OFFSET, in one translate: every rectangle below is in map pixels, so the
            # viewport shows the window of the map the scrollbars are pointing at.
            painter.translate(-self.horizontalScrollBar().value(),
                              -self.verticalScrollBar().value())
            self._paint_plan(painter, plan)
        painter.end()

    def _paint_plan(self, painter, plan):
        width, height = plan["size"]
        if width <= 0 or height <= 0:
            return
        scale = self._scale
        area = QRectF(0, 0, width * scale, height * scale)
        picture = plan["picture"]
        if picture is not None:
            # THE MAP'S OWN TILES, drawn into the same rect the zone rectangles are placed in, so
            # the two cannot drift apart. WHICH FILTER is the user's choice and it matters: the map is
            # usually drawn much smaller than its own pixels, where smooth is the only readable one,
            # and past 1:1 nearest neighbour is what the game itself draws with (see `smooth()`).
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth())
            painter.drawPixmap(area, picture, QRectF(picture.rect()))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        else:
            # NO TILES READABLE: the flat footprint the zones are found on, as this drew before.
            ground = QColor(MAP_GROUND)
            for (y, x0, x1) in plan["ground"]:
                painter.fillRect(QRectF(x0 * scale, y * scale, (x1 - x0) * scale, scale), ground)
        for patch in plan["patches"]:
            painter.setPen(Qt.PenStyle.NoPen)
            for block in patch["blocks"]:
                for (y, x0, x1) in block["runs"]:
                    painter.fillRect(QRectF(x0 * scale, y * scale, (x1 - x0) * scale, scale),
                                     patch["fill"])
            # THE MERGED BORDER, after every fill of this zone so no fill can paint over it, and in
            # the zone's own colour at full strength (the fill is the translucent one).
            self._paint_outline(painter, patch, scale)
            for block in patch["blocks"]:
                for label in block["labels"]:
                    self._paint_label(painter, label, scale)

    def _paint_outline(self, painter, patch, scale):
        """The block borders: one line per side that faces out of its own block.

        SCREEN pixels, not cells: at 3-4 px a cell a one-cell border would be a slab that swallows the
        block, and on a one-cell-wide water channel it would be all border. Widened a little with the
        map so a big map's lines are not hairlines, and capped so they never grow into the fill. The
        selected zone's edge is white and wider, which is how it is told apart now that every fill is
        translucent.
        """
        wanted = SELECTED_EDGE_WIDTH if patch["selected"] else EDGE_WIDTH
        pen = QPen(patch["edge"], max(1.0, min(wanted, scale * 0.3)))
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for block in patch["blocks"]:
            horizontal, vertical = block["outline"]
            for (x0, y, x1) in horizontal:
                painter.drawLine(QPointF(x0 * scale, y * scale), QPointF(x1 * scale, y * scale))
            for (x, y0, y1) in vertical:
                painter.drawLine(QPointF(x * scale, y0 * scale), QPointF(x * scale, y1 * scale))

    def _paint_label(self, painter, label, scale):
        """The zone's letter, centred on the cell of its block it names."""
        cx, cy, text, big = label
        font = QFont("Consolas")
        font.setBold(True)
        # A MARKER, NOT A CAPTION: the size follows the zoom so the name stays legible on a small map,
        # but it stops growing long before the block it names does - uncapped, the selected zone's name
        # came out 26 px tall against 12 px cells (the user: "the font is way too big").
        wanted = scale * (SELECTED_LABEL_PX if big else LABEL_PX)
        font.setPixelSize(int(max(7.0, min(wanted, SELECTED_LABEL_MAX_PX if big
                                           else LABEL_MAX_PX))))
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        metrics = painter.fontMetrics()
        # Tk's create_text centres the text on the point; QPainter draws from the baseline at
        # the left edge, so the offset is done here rather than by giving the label its own rect
        painter.drawText(QPointF(cx * scale - metrics.horizontalAdvance(text) / 2.0,
                                 cy * scale + metrics.ascent() / 2.0), text)
