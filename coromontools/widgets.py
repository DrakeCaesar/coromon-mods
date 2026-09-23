"""The two small widgets all three tabs share.

`note` is the small print that explains a number next to the number, and `mono_text` is the
read-only monospace pane the window uses for text that is really a table: the species list,
the skill description. Both were repeated as inline tkinter options in the Tk version.
"""

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QFrame, QLabel, QPlainTextEdit

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
