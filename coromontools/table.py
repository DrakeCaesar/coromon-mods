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
without a parallel lookup table.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

PAYLOAD = "_payload"


class Column:
    """One column: the key its value lives under, the heading, its width and its alignment.

    `desc_first` is which way the column sorts the first time its heading is clicked. It is
    declared rather than guessed from the values, because guessing gets it backwards exactly
    where it matters: "most common" is text ("Buzzlet L5-7 80%") and would open A-Z, while
    the column is only ever clicked to see the biggest share first.
    """

    def __init__(self, key, heading, width, align="w", desc_first=False):
        self.key = key
        self.heading = heading
        self.width = width
        self.align = align
        self.desc_first = desc_first

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
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return column.qt_align
        if role == Qt.ItemDataRole.UserRole:
            return self.rows[index.row()].get(PAYLOAD)
        return None

    def row_at(self, row):
        return self.rows[row] if 0 <= row < len(self.rows) else None


class DataTable(QTableView):
    """A sortable single-selection table, with the sort reported so a tab can save it.

    The table owns the sort state because it owns the header buttons, and it emits the change
    instead of reaching for a settings file: `skill_sort` and `skill_desc` are per-table state
    that has to survive a restart, and the tab is the thing that knows where to put them.
    """

    sortChanged = Signal(str, bool)
    selectionChangedTo = Signal(object)   # the payload of the current row, or None

    def __init__(self, columns, sort_key=None, sort_desc=False, parent=None):
        super().__init__(parent)
        self.model_ = TableModel(columns, self)
        self.setModel(self.model_)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSortingEnabled(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(20)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setSectionsClickable(True)
        for i, column in enumerate(columns):
            self.setColumnWidth(i, column.width)
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
        self._select_first_or(keep_row)

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
        if not (keep is not None and self.select_payload(keep)) and self.model_.rowCount():
            self.selectRow(0)

    def _row_changed(self, current, _previous):
        payload = None
        if current.isValid():
            payload = self.model_.data(current, Qt.ItemDataRole.UserRole)
        self.selectionChangedTo.emit(payload)
