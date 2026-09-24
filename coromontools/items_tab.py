"""The Items tab: every item the game has, its stats and its sprites.

WHAT IT ANSWERS, and why it needs its own tab rather than a corner of the Database one: the item
records in `Resources/data/json/items.json` carry no stats at all, so everything numeric here comes
from the game's Lua (see `items.py`), and an item's artwork is three different things depending on
what the item is - a bag icon, a thrown-and-spinning strip, a worn character part. Nothing about that
belongs in the dex grid, which is one picture per Coromon.

THE COLUMNS ARE THE STATS THE GAME'S OWN CLASSES OVERRIDE (`items.STAT_KEYS`), which is why they can
be empty: 272 of the 745 records have artwork, only some have a class at all, and of those only a
spinner has a catch modifier. THE SPINNERS AND GAUNTLETS ARE THE TWO GROUPS THAT ARE COMPLETE so far
- every one of the 17 and the 18 is extracted - and the rest of the catalogue is listed with whatever
the game has for it.

THE LIST IS A SORTABLE TABLE AND THE DETAIL IS BESIDE IT, the same shape as the Database tab's two
panes: a table is how 745 items are looked through and sorted, and the pictures need room that a
table row cannot give them. Picking a row draws it (`show_item`), and the sprites are scaled with
NEAREST-NEIGHBOUR so they read as blocks of whole pixels like every other sprite in this window.
"""

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QSplitter, QVBoxLayout,
                              QWidget)

import items as item_data

from .table import ICON, PAYLOAD, Column, DataTable
from .widgets import FittedPane, mono_height, mono_text, note

# THE SCALE THE PICTURES ARE DRAWN AT, in screen pixels per texel - TWO of them, because the list and
# the detail pane want opposite things: the bag icons are 16x16 texels, so 4 turns them into 64x64 for
# the pane, while the SAME 4x in the table would make every row 70 px tall and leave eight of them on
# screen. 2x is 32 px next to the name, which is what a list icon is for. Both are whole numbers, so
# nothing is resampled into a blur.
SPRITE_ZOOM = 4
LIST_ZOOM = 2

NOTES = ("Stats come from the game's own item classes, so a dash means the game's Lua says nothing "
         "about it (most records are data-only). An item with no icon has no artwork in the game at "
         "all.")

# The table's columns: what an item is, and every stat an item can carry.
COLUMNS = (
    Column("name", "Item", 210, "w"),
    Column("category", "category", 110, "w"),
    Column("group", "group", 100, "w"),
    Column("catch", "catch", 70, "e"),
    Column("type", "used on", 90, "w"),
    Column("shakes", "shakes", 70, "e"),
    Column("cost", "gold cost", 90, "e"),
    Column("sell", "gold sell", 90, "e"),
)


def _plain(text):
    """The game's description text with its colour markup taken out.

    Item descriptions are localisation strings with `<color TEXT_HP>HP</color>`-style markup baked in,
    because the game colours the nouns. The tags are not decoration to show in a table: they are what
    the game's own text renderer consumes, and a QLabel would print them literally.
    """
    out = []
    depth = 0
    for char in text or "":
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out).strip()


def _number(values):
    """The first number a stat body carries, as text - "-" when there is none, `"a or b"` for a pair.

    A conditional stat really does carry more than one number (the Dream Spinner's modifier is 6 on a
    sleeping Coromon and 1 otherwise), so they are all shown rather than the first being picked and the
    condition thrown away.
    """
    numbers = [value for value in values or [] if isinstance(value, (int, float))]
    if not numbers:
        return "-"
    parts = []
    for value in numbers:
        parts.append("%g" % value)
    return " or ".join(parts)


def _frame(one):
    """The top-left frame of a sprite's sheet, as a QImage, or None.

    ONE FRAME IS ENOUGH TO SEE WHAT SOMETHING IS, and it is also the honest amount to draw: these are
    animation sheets, so drawing the sheet whole would show the spinner mid-throw next to itself
    eight times. Which frame that is - the first cell of the grid, the first frame of the row - is what
    `items.sprite` describes (see `items.strip` for how a sheet is cut up).
    """
    path = one.get("path")
    if not path or not os.path.exists(path):
        return None
    image = QImage(path)
    if image.isNull():
        return None
    size = item_data.strip(one)
    if not size:
        return None
    return image.copy(0, 0, size[0], size[1])


def _pixmap(image, zoom=SPRITE_ZOOM):
    if image is None:
        return None
    return QPixmap.fromImage(image.scaled(image.width() * zoom, image.height() * zoom,
                                          Qt.AspectRatioMode.IgnoreAspectRatio,
                                          Qt.TransformationMode.FastTransformation))


def _icon(item, zoom, box):
    """The item's bag icon as a pixmap at `zoom`, padded into `box`, or None without artwork.

    None matters rather than a blank pixmap: `table.TableModel` returns whatever is under `ICON` for the
    icon column's DecorationRole, so an item with no artwork draws a bare name in that column instead
    of an empty box, and `_fit_icons` ignores it when measuring the row.

    THE BOX IS THE POINT. A `QTableView` has ONE icon size for every cell, so Qt scales each pixmap to
    it - and these are 16x16 and 20x16 pixel-art sprites, where scaling 32x32 up to 40 and stretching
    40x32 into 40x40 is how pixel art stops being pixel art. So every icon is drawn into the same
    transparent box instead, at its own size, and nothing is rescaled.
    """
    if not item.icon:
        return None
    pixmap = _pixmap(QImage(item.icon), zoom)
    if pixmap is None or pixmap.size() == box:
        return pixmap
    canvas = QPixmap(box)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return canvas


def icon_box(rows, zoom=LIST_ZOOM):
    """The box every list icon is drawn in: the largest bag icon there is, at `zoom`.

    Measured from the PNG headers (`items.icon_size`) so nothing is decoded to find out, and measured
    over the WHOLE catalogue rather than the rows on screen, so the column does not resize when a
    filter changes.
    """
    width, height = 0, 0
    for item in rows:
        size = item_data.icon_size(item.uid)
        if size:
            width, height = max(width, size[0]), max(height, size[1])
    return QSize(width * zoom, height * zoom)


class ItemsTab(QWidget):
    """The item catalogue: a filter row, the sortable list, and the picked item's pictures."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.all_items = item_data.load()
        # ONE BOX FOR EVERY LIST ICON, computed once from the whole catalogue (see `icon_box`).
        self.box = icon_box(self.all_items)
        self.rows = []               # the items currently listed, in table order
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 6)

        top = QHBoxLayout()
        top.addWidget(QLabel("find"))
        self.search = QLineEdit()
        self.search.setToolTip("hide items whose name or UID do not match")
        self.search.textChanged.connect(self.apply_filter)
        top.addWidget(self.search)
        top.addWidget(QLabel("category"))
        self.category = QComboBox()
        self.category.setToolTip("show one category - spinners, gauntlets, clothing, ...")
        self.category.addItem("all", "")
        for name, count in item_data.categories(self.all_items):
            self.category.addItem("%s (%d)" % (name, count), name)
        self.category.currentIndexChanged.connect(self.apply_filter)
        top.addWidget(self.category)
        self.counts = QLabel("")
        top.addWidget(self.counts, 1)
        outer.addLayout(top)

        self.table = DataTable(COLUMNS, sort_key="name", icon_column="name")
        self.table.selectionChangedTo.connect(self.show_item)
        outer.addWidget(self.table, 1)

        pane = QWidget()
        box = QVBoxLayout(pane)
        box.setContentsMargins(8, 0, 0, 0)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.icon)
        self.title = QLabel("")
        self.title.setWordWrap(True)
        box.addWidget(self.title)
        self.description = QLabel("")
        self.description.setWordWrap(True)
        box.addWidget(self.description)
        self.stats = mono_text(wrap=False)
        box.addWidget(self.stats)

        self.sprites = QHBoxLayout()
        self.sprites.setContentsMargins(0, 0, 0, 0)
        box.addLayout(self.sprites)
        box.addWidget(note(NOTES, wrap=300))
        box.addStretch(1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self.table)
        split.addWidget(pane)
        # THE TABLE IS AS WIDE AS ITS COLUMNS and the details pane takes the rest - the 760:460 this
        # used to be given is a ratio rather than a width, so the last column stretched to fill
        # whatever the separator was left at (see `widgets.FittedPane`).
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        self.pane_fit = FittedPane(split, 0, self.table, elastic=1)
        outer.addWidget(split, 1)

        self.apply_filter()

    # ------------------------------------------------------------------ contents
    def apply_filter(self):
        """Re-list the items the filters allow, and select the first of them.

        The list is rebuilt rather than hidden row by row (the Database tab does the opposite, because
        there the rows are a fixed grid): here the set of rows IS the filter, and a sort click has to
        re-rank just those.
        """
        needle = self.search.text().strip().lower()
        category = self.category.currentData()
        picked = [item for item in self.all_items
                  if (not category or item.category == category)
                  and (not needle or needle in item.name.lower() or needle in item.uid.lower())]
        self.rows = picked
        self.table.set_rows([self._row(item) for item in picked])
        self.counts.setText("%d of %d item(s)" % (len(picked), len(self.all_items)))

    def _row(self, item):
        """One table row: the stats are read here, lazily, because only listed items need them.

        The icon goes under `ICON` rather than into a column of its own - it is drawn IN the name
        column, which is why the table was built with `icon_column="name"`.
        """
        stats = item.stats()
        return {
            "name": item.name,
            "category": item.category,
            "group": item.group,
            "catch": _number(stats.get("getCatchRateModifier")),
            "type": ", ".join(str(value) for value in stats.get("getType") or []) or "-",
            "shakes": _number(stats.get("getAmountOfShakes")),
            "cost": _number(stats.get("getGoldCost")),
            "sell": _number(stats.get("getGoldSellPrice")),
            ICON: _icon(item, LIST_ZOOM, self.box),
            PAYLOAD: item,
        }

    def show_item(self, item):
        """Draw the picked item: its icon, its description, its stats and every sprite it has."""
        self._clear_sprites()
        if item is None:
            self.icon.clear()
            self.title.setText("")
            self.description.setText("")
            self.stats.setPlainText("")
            return
        self.icon.setPixmap(_pixmap(QImage(item.icon)) or QPixmap() if item.icon else QPixmap())
        self.title.setText("%s" % item.name)
        self.description.setText(_plain(item.description) or "-")
        text = self._stats_text(item)
        self.stats.setPlainText(text)
        # HIDDEN WHEN THERE IS NOTHING TO SHOW, and sized to its own lines when there is: an empty
        # mono pane is a 62 px box (its own minimum, see `widgets.mono_height`) standing in the middle
        # of the column for no reason - most items have no stats at all.
        self.stats.setVisible(bool(text))
        if text:
            self.stats.setFixedHeight(mono_height(self.stats, text))
        self._show_sprites(item)

    def _stats_text(self, item):
        """The stats as the mono lines the pane shows - one per stat the game's class carries."""
        stats = item.stats()
        lines = []
        for method, label in item_data.STAT_KEYS:
            values = stats.get(method)
            if not values:
                continue
            if method in ("getGoldCost", "getGoldSellPrice", "getAmountOfShakes"):
                shown = " or ".join("%g" % value for value in values
                                    if isinstance(value, (int, float))) or "-"
            else:
                shown = " or ".join(str(value) for value in values)
            lines.append("  %-16s %s" % (label, shown))
        # A GAUNTLET'S ONE IDENTITY IS THE PART IT IS WORN AS, and it is also where its sprite came
        # from (`body_gauntlet_...` names the folder), so it belongs in this pane rather than nowhere.
        parts = stats.get("getPartKey") or []
        if parts:
            lines.append("  %-16s %s" % ("worn as", ", ".join(str(value) for value in parts)))
        if item.category == "spinner":
            rates = item_data.base_catch_rates()
            if rates:
                lines.append("")
                lines.append("  base rates by rarity: %s"
                             % ", ".join("%s %g" % (key, value)
                                         for key, value in sorted(rates.items())))
                lines.append("  (the wild Coromon's own rarity decides which one applies)")
        return "\n".join(lines)

    def _show_sprites(self, item):
        """Every extra sprite the game has for this item, at the first frame of its sheet."""
        for key, one in sorted(item_data.sprites(item).items()):
            pixmap = _pixmap(_frame(one))
            if pixmap is None:
                continue
            caption = "worn: %s" % key if item.category == "gauntlet" else key
            holder = QWidget()
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 8, 0)
            picture = QLabel()
            picture.setPixmap(pixmap)
            picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(picture)
            column.addWidget(QLabel(caption))
            self.sprites.addWidget(holder)

    def _clear_sprites(self):
        while self.sprites.count():
            entry = self.sprites.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.deleteLater()
