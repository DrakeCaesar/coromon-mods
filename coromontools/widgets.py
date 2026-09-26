"""The small widgets all three tabs share.

`note` is the small print that explains a number next to the number, `DetailPane` is the pane under a
ranking whose body is a table when there is something to list and a line of prose when there is not,
and `mono_text` is the read-only monospace pane still used for text that is NOT a table - a skill's
description. `FittedPane` is the last: the rule for where the separator beside a table goes.
"""

from PySide6.QtCore import QEvent, QObject, QSize, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QLabel, QPlainTextEdit, QSizePolicy,
                               QVBoxLayout, QWidget)

from .table import NATURAL, DataTable
from .theme import FIELD, FG, NOTE


def note(text, wrap=None):
    """A dim, wrapped, non-selectable line of small print.

    `wrap` caps the width in pixels where the text would otherwise stretch across the pane and
    become hard to follow back to the control it belongs to.
    """
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(label.textInteractionFlags())
    label.setStyleSheet("color: %s; background: transparent;" % NOTE)
    if wrap:
        label.setMaximumWidth(wrap)
    return label


class DetailPane(QWidget):
    """The pane under a ranking: a headline, then a TABLE - or a line of prose when there is nothing.

    WHAT THIS REPLACES, in the user's words: "when we print an ascii table like this and similar ...
    it needs to be an actual table". Three panes of this window were monospace text areas whose columns
    were padded with spaces - the Missing tab's per-Coromon breakdown, and the spawns list under both
    maps - which meant the columns moved with the widest name in the list, nothing could be sorted, and
    a value could not be copied on its own. They are `DataTable`s now, and this is the one place that
    builds them, so the three panes are laid out and filled the same way.

    THREE PARTS, because two of them are not rows: the HEADLINE says what the table is about (the zone,
    and what is left in it), the NOTE stands in for the table when there is nothing to list ("every
    Coromon of the lines this zone can fill is already caught"), and the table hides with it rather than
    showing an empty grid under a heading.

    THE TABLE FILLS THE PANE, like the ranking above it: the columns are measured to their own values
    (`DataTable.fit_columns`), so a pane with room to spare just shows that room to the right of the
    last column rather than stretching it, and a pane too narrow for the columns scrolls them - which
    is what the monospace pane this replaces did.

    `sort_key` defaults to `table.NATURAL`, which keeps the order the caller filled in - the panes of
    this window list things in a ranked order no column can express (likeliest line first, most common
    spawn first) - and clicking a heading sorts anyway, which is what a heading is for.
    """

    def __init__(self, columns, sort_key=NATURAL, headline=True, tooltip="", parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        self.head = None
        if headline:
            self.head = QLabel("")
            self.head.setWordWrap(True)
            self.head.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            box.addWidget(self.head)
        self.note = note("", wrap=900)
        self.note.setVisible(False)
        box.addWidget(self.note)
        self.table = DataTable(columns, sort_key=sort_key)
        # A DISPLAY, NOT A PICKER: what selects is the ranking above, so a highlighted row here would
        # look like a second selection that does nothing.
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        if tooltip:
            self.table.setToolTip(tooltip)
        box.addWidget(self.table, 1)
        self.rows = []

    def fill(self, headline, rows, message=""):
        """Show `rows` under `headline` - or `message` instead, when there are no rows."""
        self.rows = rows
        if self.head is not None:
            self.head.setText(headline)
        self.table.set_rows(rows)
        self.note.setText(message)
        self.note.setVisible(bool(message))
        self.table.setVisible(bool(rows))
        return bool(rows)

    def sizeHint(self):
        """`(256, height)` - the width the monospace pane this replaces used to ask for.

        THE TABLE'S OWN WIDTH IS NOT A HINT. Letting it through MOVES THE SEPARATOR beside the pane:
        the spawns pane sits in a splitter's map column, and its table's 525 px of columns made that
        column ask for 525 px where a text pane asked for 256 - measured at 1420x800, the map column
        came out 311 px wide instead of 525, because the splitter then divided the room differently.
        A change to the pane's CONTENTS must not resize the map next to it. The pane fills whatever
        width the layout gives it and the table measures its own columns inside that, which is why the
        width here is a constant and only the height follows the content.
        """
        return QSize(256, super().sizeHint().height())


def mono_text(wrap=False):
    """A read-only monospace pane.

    Two callers need opposite wrapping and both are deliberate: the species list must NOT wrap,
    because its columns are aligned with spaces and wrapping destroys the alignment, while a
    skill description is prose and must wrap.
    """
    pane = QPlainTextEdit()
    pane.setReadOnly(True)
    pane.setFont(_mono_font())
    pane.setFrameShape(QFrame.Shape.NoFrame)
    pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if wrap
                         else QPlainTextEdit.LineWrapMode.NoWrap)
    pane.setStyleSheet("background: %s; color: %s;" % (FIELD, FG))
    return pane


def _mono_font():
    """Consolas if the machine has it, otherwise whatever fixed-width font it does have."""
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(9)
    if "Consolas" not in QFontDatabase.families():
        return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    return font


def mono_height(pane, text):
    """The height `pane` needs to show all of `text` without scrolling.

    MEASURED, because none of the obvious answers works: `document().size()` AND
    `documentLayout().documentSize()` both report the BLOCK COUNT as their height (1.0 for one line,
    15.0 for fifteen), and `sizeHint()` is 192 whatever the text is. So the lines are counted at the
    font's own `lineSpacing` (14 px for this mono font), plus the document margin on both sides and
    the frame - and never less than the widget's own `minimumSizeHint` (62 px), which Qt enforces
    however little text there is. `DataTable.content_height` has the same floor for the same reason.

    THE LAST PIXEL IS ONE OF ITS OWN, and it was found the only way it could be: by asking the pane
    for the smallest height at which nothing is hidden (`verticalScrollBar().maximum() == 0`, which
    that widget counts in LINES, not pixels). Measured 25 px for one line, 39 for two, 109 for seven
    and 221 for fifteen - the arithmetic above, plus one. Without it Qt keeps a one-line scroll range
    and the last line is cut in half.

    The horizontal scrollbar is added when the longest line does not fit, because a no-wrap pane
    gives those 12 px up out of the viewport's height - and wrapping is not an option for these
    panes: their columns are aligned with spaces, and wrapping destroys the alignment.
    """
    lines = text.split("\n") if text else []
    metrics = pane.fontMetrics()
    margin = pane.document().documentMargin()
    asked = 2 * pane.frameWidth() + 2 * margin + len(lines) * metrics.lineSpacing() + 1
    if lines and pane.viewport().width():
        widest = metrics.horizontalAdvance(max(lines, key=len)) + 2 * margin
        if widest > pane.viewport().width():
            asked += pane.horizontalScrollBar().sizeHint().height()
    return max(asked, pane.minimumSizeHint().height())


def hline():
    """A one-pixel separator that keeps the dark palette instead of the platform's default."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: %s;" % NOTE)
    return line


class FittedPane(QObject):
    """Keeps a splitter's separator at the width of the content in one of its panes.

    THE SEPARATOR IS PLACED FROM THE CONTENT, not the other way round - the user: "the slider/separator
    between left and right panes is not dynamic, the last column of the tables is as long as the
    separator is set, instead, the tables should be as narrow as possible and the separator should be
    static based on that". So the pane is given exactly the width its content occupies and the room
    left over goes to the pane beside it - the one holding the map, in every tab that has one.

    WHICH PANE HAS THE ROOM IS DECLARED (`elastic`) rather than left to a stretch factor: a window
    made wider must widen the map, not move a boundary that was measured to the pixel.

    `want` is either a `DataTable` (a pane sized to its own columns, which re-fits itself when a fill
    re-measures them) or a CALLABLE returning the width in pixels - the Database tab's dex grid is a
    layout rather than a table, and it re-fits itself where its scale changes instead. `floor` is the
    smallest the fitted pane may be, and `reserve` what the other panes may not lose - both are zero
    unless a caller needs them.

    It fits itself: an event filter on the splitter re-fits on every resize, so nothing here has to
    be called by the tab. THE FIRST FITS DO NOTHING, before the splitter has a width to divide.
    """

    def __init__(self, splitter, pane, want, elastic, floor=0, reserve=0):
        super().__init__(splitter)
        self.splitter = splitter
        self.pane = pane
        self.elastic = elastic
        self.floor = floor
        self._reserve = reserve
        table = None if callable(want) else want
        self._want = want if table is None else want.content_width
        self._asked = None
        # ONE TURN OF THE EVENT LOOP LATER, which is what makes the first fit land: during the resize
        # that hands the splitter its width its panes are still the size of the last one - a splitter
        # 1020 px wide answers [0, 0] (measured) - so the fit is asked for, not done, and the timer
        # is a child of this object so a tab being thrown away cannot leave it armed.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.refit)
        splitter.installEventFilter(self)
        if table is not None:
            table.columnsChanged.connect(self.refit)

    def want(self):
        """The width the fitted pane is being given, in pixels."""
        return self._want()

    def reserve(self):
        """What the panes beside it may not lose, in pixels - measured on the spot when it moves with
        the content (the Database tab's list is another Coromon's columns every time it is filled)."""
        return self._reserve() if callable(self._reserve) else self._reserve

    def fitted_width(self, span=None):
        """`want()`, capped to what the room allows - the width the fitted pane is being given.

        The cap is the whole reason `refit` and the tests agree: on a window too small for the content
        the pane is not as wide as the content, it is as wide as the room MINUS what the panes beside
        it may not lose.
        """
        if span is None:
            span = self.splitter.width() - self.splitter.handleWidth() * (self.splitter.count() - 1)
        return int(min(max(self.floor, self.want()), max(1, span - max(0, self.reserve()))))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self._timer.start()
        return False

    def refit_later(self):
        """Ask for a fit AFTER the layout has run, for a caller that just changed what it measures.

        MEASURING A LAYOUT THAT WAS JUST CHANGED IS MEASURING ITS LAST ANSWER: the Database tab sets
        its grid's column widths and then wants the separator moved, and `sizeHint()` asked in the same
        call still reports the previous scale - MEASURED at 1897 px for a grid that had just been
        scaled down to 911, so the separator did not follow the sprite scale at all (the user: "when I
        change the scale of coromon sprites, the separator should auto adjust"). One turn of the event
        loop later the grid has been laid out and the number is real.
        """
        self._timer.start()

    def refit(self):
        """Put the separator at the fitted pane's width, the elastic pane keeping the rest.

        Returns whether it could - false while there is no width to divide.

        `self._asked` remembers the last (room, width) pair it could not reach, because Qt CLAMPS what
        a splitter may be given: without it, a pane that wants more than the window has would be
        re-asked on every resize event it caused itself, and the two would go round and round.
        """
        count = self.splitter.count()
        if not 0 <= self.pane < count:
            return False
        span = self.splitter.width() - self.splitter.handleWidth() * (count - 1)
        if span <= 0:
            return False
        want = self.fitted_width(span)
        # turn of the event loop after the splitter was resized, so these are the widths it laid out - 
        # and when it has not laid anything out yet (the very first pass, before the window is shown)
        # the room is split by what each pane asks for, so the fit still lands somewhere sensible.
        sizes = [self.splitter.widget(i).width() for i in range(count)]
        if sum(sizes) != span:
            asked = [max(1, self.splitter.widget(i).sizeHint().width()) for i in range(count)]
            share = span / float(sum(asked))
            sizes = [max(1, int(round(value * share))) for value in asked]
        if sizes[self.pane] == want or self._asked == (span, want):
            return True
        self._asked = (span, want)
        sizes[self.pane] = want
        sizes[self.elastic] = max(1, sizes[self.elastic] + span - sum(sizes))
        self.splitter.setSizes(sizes)
        return True
