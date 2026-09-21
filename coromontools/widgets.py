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


def hline():
    """A one-pixel separator that keeps the dark palette instead of the platform's default."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: %s;" % NOTE)
    return line
