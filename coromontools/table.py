"""The one table widget the three tabs share.

Qt's QTableView paints only the cells on screen and scrolls natively, so a 669-row ranking
costs the same as a 7-row one. That is the whole reason the Tk version's `sort_tree_rows` -
which moved every ttk row into a new position, one `tree.move` at a time - is gone: sorting
here is sorting the row list and resetting the model.

The SORT ORDER is kept exactly as it was, because both tables mix numbers with "-" (a status
skill has no power, and a zone has no "most common" species when the share filter hid them).
Sorted as text, "-" lands above "95" ascending and above it descending too, so numbers and
non-numbers are ranked as two groups with the non-numbers appended last, whichever way round
the sort is. The rule is on the FORMATTED text, which is what the old code sorted on.

A row is a dict of display strings keyed by column key, plus an optional PAYLOAD key holding
the object the row stands for - the Zone or the skill - so a selection can be mapped back
without a parallel lookup table. A row may also carry an `ICON` (a QPixmap), which is drawn in
ONE column - whichever the table was built with as its `icon_column` - because a decoration
returned for every column would put the same picture in all of them.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QStyle,
                               QStyledItemDelegate, QTableView)

from .theme import BAR

PAYLOAD = "_payload"
ICON = "_icon"

# HOW TALL A ROW IS WITH AND WITHOUT AN ICON. 20 px is what a text row has always been; a row drawing
# an icon is the icon's own height plus this much air, because a 32 px picture in a 20 px row is a
# picture with its top and bottom cut off.
ROW_HEIGHT = 20
ICON_PAD = 6

# HOW MUCH AIR A COLUMN KEEPS ON TOP OF THE WIDEST TEXT IN IT. Qt's `sizeHintForColumn` is the text
# plus the style's own margins, which is exactly the width at which the last letter touches the next
# column's edge - and the header draws its sort arrow inside the section it sorts by.
COLUMN_PAD = 8


class Column:
    """One column: the key its value lives under, the heading, its width and its alignment.

    `desc_first` is which way the column sorts the first time its heading is clicked. It is
    declared rather than guessed from the values, because guessing gets it backwards exactly
    where it matters: "most common" is text ("Buzzlet L5-7 80%") and would open A-Z, while
    the column is only ever clicked to see the biggest share first.

    `width` IS THE CEILING THE COLUMN IS MEASURED AGAINST, not the width the column keeps: every fill
    shrinks a column to the text it actually holds (`DataTable.fit_columns`) - the user: "the tables
    should be as narrow as possible". It is the width the column starts at and the width it can never
    grow past, which is what keeps one long value from deciding the width of the whole table.

    `bar` marks a column whose cell is not text but a BAR: the row's value under that key is a
    fraction of the cell's width (0..1) and `BarDelegate` paints it, so the length is proportional
    instead of a count of block characters. Such a column keeps its declared width when the table is
    fitted, because there is no text to measure.

    `fixed` is a column that keeps its declared width for the opposite reason: it is TEXT, but text the
    user changes while they watch (the Potential tab's odds, whose fraction is twice as long with a
    scent running). A column that re-measures with every toggle makes the whole table jump sideways, so
    the caller measures it ONCE at its widest case and pins it here.
    """

    def __init__(self, key, heading, width, align="w", desc_first=False, bar=False, fixed=False):
        self.key = key
        self.heading = heading
        self.width = width
        self.align = align
        self.desc_first = desc_first
        self.bar = bar
        self.fixed = fixed

    @property
    def qt_align(self):
        return {"w": Qt.AlignmentFlag.AlignLeft,
                "e": Qt.AlignmentFlag.AlignRight,
                "center": Qt.AlignmentFlag.AlignHCenter}[self.align] \
            | Qt.AlignmentFlag.AlignVCenter


def rank_number(text):
    """The number to rank `text` by, or None when it is not a number - used by `sort_rows`.

    A TRAILING "%" IS A NUMBER TOO, and it has to be said here rather than per column: that is how
    every share in this window is shown ("9.0%"), and ranking those as text is the bug the user hit -
    "descending sorts 9% before 12%", because the text "9" sorts above the text "1".
    """
    text = str(text).strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def sort_rows(rows, key, desc):
    """Rank rows by one column's displayed text, numbers before everything else.

    Numbers are compared as numbers and the rest as lowercased text; the two groups are then
    concatenated so the non-numbers are last in both directions. Sorting the numbers as text
    would put "100" before "95", which is how a ranking of levels stops making sense - and a
    percentage is a number too (see `rank_number`).
    """
    numbers, others = [], []
    for row in rows:
        text = row.get(key, "")
        value = rank_number(text)
        if value is None:
            others.append((str(text).lower(), row))
        else:
            numbers.append((value, row))
    numbers.sort(key=lambda pair: pair[0], reverse=desc)
    others.sort(key=lambda pair: pair[0], reverse=desc)
    return [row for _, row in numbers + others]


class TableModel(QAbstractTableModel):
    """A table over a list of row dicts plus the visible columns."""

    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns = list(columns)
        self.rows = []
        # The KEY of the one column that draws each row's `ICON`, or None for a text-only table.
        self.icon_key = None

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        return self.columns[section].heading

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        column = self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.rows[index.row()].get(column.key, "")
        if role == Qt.ItemDataRole.DecorationRole:
            # ONE COLUMN ONLY: a decoration is per index, so returning the row's icon for every column
            # would draw the same picture under every heading.
            if self.icon_key and column.key == self.icon_key:
                return self.rows[index.row()].get(ICON)
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return column.qt_align
        if role == Qt.ItemDataRole.UserRole:
            return self.rows[index.row()].get(PAYLOAD)
        return None

    def row_at(self, row):
        return self.rows[row] if 0 <= row < len(self.rows) else None


class BarDelegate(QStyledItemDelegate):
    """Paints a cell as a bar whose length is EXACTLY the value in it (a fraction of the cell).

    THE VALUE IS A NUMBER, NOT BLOCKS OF TEXT. A text bar ("██████") can only change length one
    character at a time, so a distribution that rises smoothly still came out as a staircase of five
    or six lengths - the user: "we could have them show real proportional bars, not those staggered
    ones". Here the bar is a filled rectangle as wide as the value says, to the pixel.

    The cell's own background is drawn first (the style's item panel), so the alternating row colours
    survive - a delegate that returns without painting it leaves the chart on bars of bare window.
    """

    # Air on each side, so a bar at 1.0 does not touch the next column's edge.
    INSET = 3

    def paint(self, painter, option, index):
        style = option.widget.style() if option.widget is not None else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter,
                            option.widget)
        try:
            fraction = float(index.data(Qt.ItemDataRole.DisplayRole) or 0.0)
        except (TypeError, ValueError):
            return
        fraction = max(0.0, min(1.0, fraction))
        box = option.rect.adjusted(self.INSET, self.INSET, -self.INSET, -self.INSET)
        pixels = int(round(box.width() * fraction))
        # A LEVEL THE TABLE GIVES WEIGHT TO IS NEVER DRAWN AS NOTHING: the two rarest levels are 0.2% of
        # the peak, which is under half a pixel, and an empty cell would read as "impossible".
        if fraction > 0 and pixels < 1:
            pixels = 1
        box.setWidth(pixels)
        if pixels > 0:
            painter.fillRect(box, QColor(BAR))


class DataTable(QTableView):
    """A sortable single-selection table, with the sort reported so a tab can save it.

    The table owns the sort state because it owns the header buttons, and it emits the change
    instead of reaching for a settings file: `skill_sort` and `skill_desc` are per-table state
    that has to survive a restart, and the tab is the thing that knows where to put them.
    """

    sortChanged = Signal(str, bool)
    selectionChangedTo = Signal(object)   # the payload of the current row, or None
    # ... AND THE WIDTH THE COLUMNS OCCUPY, reported whenever a fill re-measures them: the separator
    # beside this table is placed from that width (`widgets.FittedPane`), so it has to know when it
    # moves.
    columnsChanged = Signal()

    def __init__(self, columns, sort_key=None, sort_desc=False, parent=None, icon_column=None):
        super().__init__(parent)
        self.model_ = TableModel(columns, self)
        self.model_.icon_key = icon_column
        self.setModel(self.model_)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSortingEnabled(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        # NO STRETCHED LAST COLUMN: `setStretchLastSection` gives the last heading whatever width the
        # pane has left over, so dragging the separator beside this table widened THAT COLUMN instead
        # of ending where the table ends - the user: "the last column of the tables is as long as the
        # separator is set". The columns are the table's width now, and the pane is fitted to them.
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setSectionsClickable(True)
        for i, column in enumerate(columns):
            self.setColumnWidth(i, column.width)
            if column.bar:
                self.setItemDelegateForColumn(i, BarDelegate(self))
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._sort_key = sort_key or (columns[0].key if columns else None)
        self._sort_desc = sort_desc
        self.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.selectionModel().currentRowChanged.connect(self._row_changed)

    # ------------------------------------------------------------------ contents
    def set_rows(self, rows, keep_row=None):
        """Replace the contents, sorted, and put the selection somewhere sensible.

        `keep_row` is a payload to re-select if it is still listed - a filter that leaves the
        selected zone alone should not move the selection to the top of the table.
        """
        self.model_.set_rows(sort_rows(rows, self._sort_key, self._sort_desc))
        self._reflow()
        self._select_first_or(keep_row)

    def _reflow(self):
        """Re-measure what the new rows cost: the row height (an icon, if the table has one) and the
        width of every column. One place, because both are read by the caller that sizes a pane
        around this table (`content_height`, `content_width`) and a fill that changed one of them has
        changed the other.
        """
        self._fit_icons()
        self.fit_columns()

    def fit_columns(self):
        """Size every column to the text it actually holds - "the tables should be as narrow as
        possible" (the user).

        THE DECLARED WIDTH IS A CEILING, NOT A TARGET: left as a target, a column keeps room its
        values never use, and beside a splitter that unused room is what the separator ends up being
        dragged over. A column is measured against the widest value it is holding RIGHT NOW, so a
        sort or a filter that changes which row is the widest one changes the width - and the ceiling
        is what keeps ONE long value (a zone listing nine species) from deciding the width of the
        whole table on its own.

        The heading counts too, so a column of short numbers is never narrower than its own label.
        """
        header = self.horizontalHeader()
        changed = False
        for i, column in enumerate(self.model_.columns):
            # A BAR COLUMN KEEPS ITS DECLARED WIDTH: there is no text in it to measure, and measuring
            # the number the delegate paints ("0.59") would leave the chart 25 px wide. A `fixed`
            # column keeps it because its caller has already measured the WIDEST value it can hold
            # (see `Column.fixed`) - re-measuring it on every fill is what makes a table jump about
            # when a checkbox is flipped.
            if column.bar or column.fixed:
                want = column.width
            else:
                asked = max(self.sizeHintForColumn(i), header.sectionSizeHint(i)) + COLUMN_PAD
                want = min(column.width, asked)
            if header.sectionSize(i) != want:
                self.setColumnWidth(i, want)
                changed = True
        if changed:
            # ... and the LAYOUT is told, because `sizeHint` is the columns' own width now and a
            # re-measure can change it (the scent grows the potential tab's fraction column by 80 px).
            self.updateGeometry()
            self.columnsChanged.emit()

    def sizeHint(self):
        """The width these columns need, so a layout beside this table gives it that and no more.

        QT'S OWN HINT IS A GUESS AT A "NICE" TABLE - measured, 256 px whatever the columns are - which
        is why a table in a layout with an elastic neighbour comes out squeezed. The Potential tab's
        Potentiflator table has to sit RIGHT AFTER the wild one with no separator between them, and
        that only reads as one table beside another when each takes exactly what its columns ask for.
        Height is left to Qt: a table's height is its container's business.
        """
        hint = super().sizeHint()
        return QSize(self.content_width(), hint.height())

    def content_width(self):
        """The width this table needs to show every column it has, and not a pixel more.

        The sum of the columns as `fit_columns` sized them, plus the frame and the vertical scrollbar a
        long list scrolls with. The scrollbar is counted WHETHER OR NOT IT IS SHOWING: it takes its
        width out of the viewport, so a pane fitted to the columns alone hides the last column's last
        letter behind it - MEASURED on the Database tab's list, whose pane came out 15 px short and
        wore a horizontal scrollbar until this was added.
        """
        header = self.horizontalHeader()
        span = sum(header.sectionSize(i) for i in range(self.model_.columnCount()))
        return span + 2 * self.frameWidth() + self.verticalScrollBar().sizeHint().width()

    def _fit_icons(self):
        """Size the rows and the icon box to whatever decoration the new rows carry, if any.

        ONLY WHEN THEY CARRY ONE: every other table in this window is text, and its rows stay at the
        tight `ROW_HEIGHT` - a taller row would cost rows on screen for nothing. When they do, the row
        is the icon's own height plus `ICON_PAD`, and `content_height` reads `sectionSize(0)`, so a tab
        that sizes itself to its content follows this without knowing anything about icons.
        """
        height = 0
        if self.model_.icon_key:
            for row in self.model_.rows:
                icon = row.get(ICON)
                if icon is not None:
                    height = max(height, icon.height())
        if not height:
            self.setIconSize(QSize(0, 0))
            self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
            return
        self.setIconSize(QSize(height, height))
        self.verticalHeader().setDefaultSectionSize(height + ICON_PAD)

    def content_height(self):
        """The height this table needs to draw every row it has, header and frame included.

        A `QTableView`'s own `sizeHint` is not content aware - it is whatever the layout wants to
        give it - and `sizeHintForRow` is not either: it reports the DELEGATE's 12 px hint, while the
        view lays its rows out at the vertical header's `defaultSectionSize`, which is 20. So the
        rows are counted at the size the view really uses (measured: `visualRect` steps 20 px a row).

        The answer is never smaller than the widget's own `minimumSizeHint`, because Qt will not draw
        a table shorter than a header, one row and its scrollbars - MEASURED at 62 px however few
        rows there are, against the 27 px a rowless table asks for. A caller that wants to sit flush
        under the last row needs the number Qt will actually honour, not the arithmetic one.

        NO ROWS IS NOT ONE ROW: an empty table is its header and nothing under it, which is what lets
        an empty location list leave the map the whole column.
        """
        rows = self.model_.rowCount()
        asked = (self.horizontalHeader().height() + 2 * self.frameWidth()
                 + rows * self.verticalHeader().sectionSize(0))
        return max(asked, self.minimumSizeHint().height())

    def current_payload(self):
        index = self.currentIndex()
        if not index.isValid():
            return None
        return self.model_.data(index, Qt.ItemDataRole.UserRole)

    def current_row(self):
        index = self.currentIndex()
        return self.model_.row_at(index.row()) if index.isValid() else None

    def select_payload(self, payload):
        """Select the row standing for `payload`, if it is listed. True when it was found."""
        for row, item in enumerate(self.model_.rows):
            if item.get(PAYLOAD) is payload:
                self.selectRow(row)
                return True
        return False

    def _select_first_or(self, payload):
        if payload is not None and self.select_payload(payload):
            return
        if self.model_.rowCount():
            self.selectRow(0)
        else:
            # an empty table has no current row, and the panes beside it must be told: nothing
            # would be emitted otherwise, and they would keep showing the last zone's species
            self.selectionChangedTo.emit(None)

    # ------------------------------------------------------------------ sorting
    def set_sort(self, key, desc):
        """Set the sort without re-emitting it - this is the saved state being restored."""
        if key is None:
            return
        self._sort_key, self._sort_desc = key, desc
        self._apply_sort()

    @property
    def sort_state(self):
        return self._sort_key, self._sort_desc

    def _header_clicked(self, section):
        column = self.model_.columns[section]
        if column.key == self._sort_key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key, self._sort_desc = column.key, column.desc_first
        self._apply_sort()
        self.sortChanged.emit(self._sort_key, self._sort_desc)

    def _apply_sort(self):
        keep = self.current_payload()
        self.model_.set_rows(sort_rows(self.model_.rows, self._sort_key, self._sort_desc))
        self._reflow()
        if not (keep is not None and self.select_payload(keep)) and self.model_.rowCount():
            self.selectRow(0)

    def _row_changed(self, current, _previous):
        payload = None
        if current.isValid():
            payload = self.model_.data(current, Qt.ItemDataRole.UserRole)
        self.selectionChangedTo.emit(payload)
