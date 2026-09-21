"""The area map: which patch of grass "Grass B" actually is.

The wiki lists encounter rates per area as "Grass A / Grass B / ..." and never says which patch
each one is. The game's maps do: a patch carries a `grassArea` marker whose zoneUID IS that
letter. So this draws the area with its zones coloured and the one selected in the ranking
picked out.

Drawn in `paintEvent`, not "when the selection changes". That is not a style choice, it removes
two hacks: Qt only paints a widget that is on screen, so the Tk version's
`if not canvas.winfo_ismapped(): return` guard is unnecessary, and because the scale is
recomputed per paint, a resize redraws itself instead of needing the `update_idletasks` call
that existed to force the canvas to have a size before it could be drawn into.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

import encounter_zones as ez

from .config import MAP_MAX_SCALE, PATCH_ALPHA, SPOTS_SHOWN
from .text import pretty
from .theme import FIELD, MAP_GROUND


class ZoneMap(QWidget):
    """One map, drawn with its encounter zones coloured and the selected one solid.

    What will be drawn is worked out in `set_zone`, not in `paintEvent`: the patches come from
    the map JSON and have to be turned into horizontal runs, and a resize is many repaints, so
    doing it per paint would make dragging the window edge stutter. Painting then only scales
    and fills rectangles.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.zone = None
        self.headline = "pick a zone on the first tab"
        self.legend = []      # [(letter, colour, is_selected)] for the tab to display
        self._plan = None

    # ------------------------------------------------------------------ input
    def set_zone(self, zone):
        """Point the map at a zone. Also sets `headline` and `legend` for the tab to show."""
        self.zone = zone
        self._plan = None
        self.legend = []
        if zone is None:
            self.headline = "pick a zone on the first tab"
            self.update()
            return
        data = ez.zone_map(zone.map_file)
        if data is None:
            self.headline = "no map file found for %s" % zone.map_file
            self.update()
            return
        m, layers, zones = data
        # the zones this map actually marks: the ranking can name one the map file has no marker
        # for, and saying so is better than drawing an empty frame
        drawn = {name: z for name, z in zones.items() if z["patches"] or z["unplaced"]}
        entry = drawn.get(zone.name)
        if entry is None:
            self.headline = ("%s is not marked on the map of %s - it has %s"
                             % (zone.name, pretty(zone.map_file),
                                ", ".join(sorted(drawn)) or "no zones"))
            self.update()
            return
        self._plan = self._plan_map(m, layers, drawn, zone)
        self.headline = self._describe(zone, entry)
        self.legend = [(name.rsplit("_", 1)[-1], ez.colour_for(name), name == zone.name)
                       for name in sorted(drawn)]
        self.update()

    # ------------------------------------------------------------------ planning
    def _plan_map(self, m, layers, drawn, zone):
        """Everything to draw, as numbers in map cells - the paint only scales it."""
        ground = list(ez.runs_by_row(ez.cells_by_row(ez.terrain_cells(m, layers))))
        patches = []
        for name in sorted(drawn):
            other = drawn[name]
            selected = name == zone.name
            colour = QColor(ez.colour_for(name))
            if not selected:
                colour.setAlpha(PATCH_ALPHA)
            runs = []
            labels = []
            for index, tiles in enumerate(other["patches"]):
                runs.extend(ez.runs_by_row(ez.cells_by_row(tiles)))
                xs = [tile[0] for tile in tiles]
                ys = [tile[1] for tile in tiles]
                # the label sits at the centre of the patch it names, so a zone split into two
                # patches is labelled twice and neither label points at the wrong one
                labels.append(((min(xs) + max(xs) + 1) / 2.0, (min(ys) + max(ys) + 1) / 2.0,
                               name.rsplit("_", 1)[-1], selected and index == 0))
            patches.append({
                "colour": colour,
                "runs": runs,
                "labels": labels,
                "marks": [(x, y, QColor(ez.colour_for(name))) for (x, y, _why) in other["unplaced"]],
                "selected": selected,
            })
        # unselected first, so the selected zone is never overdrawn: the legend promises it is
        # "the solid one", and drawing it last is what keeps that true where zones overlap
        patches.sort(key=lambda patch: patch["selected"])
        return {"size": (m["width"], m["height"]), "ground": ground, "patches": patches}

    def _describe(self, zone, entry):
        """The one-line answer to "where is it", shown above the map."""
        placed = sum(len(patch) for patch in entry["patches"])
        spots = ", ".join("(%d,%d)" % (min(tile[0] for tile in patch), min(tile[1] for tile in patch))
                          for patch in entry["patches"][:SPOTS_SHOWN])
        if len(entry["patches"]) > SPOTS_SHOWN:
            # a titan temple map can mark 137 patches, and the line is a label: all of them at
            # once is a paragraph of coordinates nobody reads
            spots += ", ..."
        if entry["patches"]:
            text = "%s   %d patch(es), %d tiles   at %s" % (
                zone.name, len(entry["patches"]), placed, spots or "-")
            if entry["unplaced"]:
                text += "   - %d marker(s) not placed" % len(entry["unplaced"])
            return text
        # NOT a failure to report as one: these zones mark a tile with no layer of its own, so
        # the tiles cannot say what shape the zone is. The marks still say where it is.
        return ("%s   the map gives %d marker(s) with no tile layer, so only their spots are "
                "known - they are the small squares" % (zone.name, len(entry["unplaced"])))

    # ------------------------------------------------------------------ painting
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(FIELD))
        plan = self._plan
        if plan is not None:
            self._paint_plan(painter, plan)
        painter.end()

    def _paint_plan(self, painter, plan):
        width, height = plan["size"]
        if width <= 0 or height <= 0:
            return
        # at least 1:1 - a map bigger than the pane is clipped rather than shrunk to mush - and
        # never above the cap, because a small map filling a maximised window is all block
        scale = max(1.0, min(self.width() / width, self.height() / height, MAP_MAX_SCALE))
        ground = QColor(MAP_GROUND)
        for (y, x0, x1) in plan["ground"]:
            painter.fillRect(QRectF(x0 * scale, y * scale, (x1 - x0) * scale, scale), ground)
        for patch in plan["patches"]:
            painter.setPen(Qt.PenStyle.NoPen)
            for (y, x0, x1) in patch["runs"]:
                painter.fillRect(QRectF(x0 * scale, y * scale, (x1 - x0) * scale, scale),
                                 patch["colour"])
            self._paint_labels(painter, patch["labels"], scale)
            self._paint_marks(painter, patch["marks"], scale)

    def _paint_labels(self, painter, labels, scale):
        if not labels:
            return
        metrics = None
        for (cx, cy, text, big) in labels:
            font = QFont("Consolas")
            font.setBold(True)
            font.setPixelSize(max(7, int(scale * (2.2 if big else 1.4))))
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            metrics = painter.fontMetrics()
            # Tk's create_text centres the text on the point; QPainter draws from the baseline at
            # the left edge, so the offset is done here rather than by giving the label its own rect
            painter.drawText(QPointF(cx * scale - metrics.horizontalAdvance(text) / 2.0,
                                     cy * scale + metrics.ascent() / 2.0), text)

    def _paint_marks(self, painter, marks, scale):
        """The outlined squares: markers with no tile layer (water, cave), at a fixed size.

        Fixed rather than scaled, because a one-tile mark at the map's own scale is a single
        pixel, and for a cave zone these marks can be the only evidence of where it is.
        """
        radius = 2
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for (x, y, colour) in marks:
            painter.setPen(QPen(colour, 1))
            painter.drawRect(QRectF((x + 0.5) * scale - radius, (y + 0.5) * scale - radius,
                                    radius * 2, radius * 2))
