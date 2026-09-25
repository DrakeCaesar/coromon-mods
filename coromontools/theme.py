"""Dark Qt theme for the Coromon grind tools.

The palette is the one the Tk version used, so the window does not change character: the
colours are named here and imported by the widgets that need them (the map draws its own
ground, the table needs the selection colour), rather than being repeated as literals.

Fusion plus an explicit palette is what makes this work: the Windows native style ignores
most palette roles, which is the same reason the Tk version had to switch to 'clam'.
"""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#1e1f22"          # window and panel background
FIELD = "#2b2d30"       # entries, tables, detail panes
FG = "#dcdcdc"          # text
DIM = "#3a3d41"         # borders and hover
ACCENT = "#3d6ea8"      # selection
NOTE = "#9aa0a6"        # the small print
MAP_GROUND = "#101114"  # the solid ground the zone colours sit on
# THE BARS DRAWN INSIDE A TABLE CELL (`table.BarDelegate`). Lighter than ACCENT on purpose: a selected
# row is painted in ACCENT, and a bar in the same colour would vanish into it.
BAR = "#5b8fc7"

_STYLESHEET = """
QWidget { color: %(fg)s; }
QMainWindow, QDialog, QTabWidget::pane { background: %(bg)s; }
QTabBar::tab {
    background: %(field)s; padding: 6px 14px; border: 1px solid %(dim)s; border-bottom: none;
}
QTabBar::tab:selected { background: %(bg)s; color: #ffffff; }
QTabBar::tab:hover { background: %(dim)s; }
QTableView, QListWidget, QPlainTextEdit, QTextEdit {
    background: %(field)s; border: 1px solid %(dim)s;
    selection-background-color: %(accent)s; selection-color: #ffffff;
}
QTableView { gridline-color: %(dim)s; }
QHeaderView::section {
    background: %(bg)s; color: %(fg)s; border: none; border-right: 1px solid %(dim)s;
    border-bottom: 1px solid %(dim)s; padding: 4px;
}
QHeaderView::section:hover { background: %(dim)s; }
QListWidget::item { padding: 2px; }
QListWidget::item:selected { background: %(accent)s; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: %(field)s; border: 1px solid %(dim)s; padding: 3px;
    selection-background-color: %(accent)s;
}
QComboBox QAbstractItemView { background: %(field)s; selection-background-color: %(accent)s; }
QPushButton {
    background: %(field)s; border: 1px solid %(dim)s; padding: 4px 12px;
}
QPushButton:hover { background: %(dim)s; }
QPushButton:pressed { background: %(bg)s; }
QCheckBox { spacing: 6px; }
QSplitter::handle { background: %(dim)s; }
QSplitter::handle:hover { background: %(accent)s; }
QScrollBar:vertical { background: %(bg)s; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: %(dim)s; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: %(accent)s; }
QScrollBar:horizontal { background: %(bg)s; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: %(dim)s; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: %(accent)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { background: %(field)s; color: %(fg)s; border: 1px solid %(dim)s; }
""" % {"bg": BG, "field": FIELD, "fg": FG, "dim": DIM, "accent": ACCENT}


def apply_theme(app: QApplication):
    """Apply the shared dark theme to the whole application."""
    app.setStyle("Fusion")

    palette = QPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, QColor(BG))
    palette.setColor(role.WindowText, QColor(FG))
    palette.setColor(role.Base, QColor(FIELD))
    palette.setColor(role.AlternateBase, QColor("#26282b"))
    palette.setColor(role.Text, QColor(FG))
    palette.setColor(role.Button, QColor(FIELD))
    palette.setColor(role.ButtonText, QColor(FG))
    palette.setColor(role.Highlight, QColor(ACCENT))
    palette.setColor(role.HighlightedText, QColor("#ffffff"))
    palette.setColor(role.ToolTipBase, QColor(FIELD))
    palette.setColor(role.ToolTipText, QColor(FG))
    # the disabled group is a separate enum from the roles, and dimming greyed-out text is the
    # one place the palette is not enough on its own - Fusion would otherwise keep it readable
    # and it would look enabled
    group = QPalette.ColorGroup
    palette.setColor(group.Disabled, role.Text, QColor(NOTE))
    palette.setColor(group.Disabled, role.WindowText, QColor(NOTE))
    app.setPalette(palette)

    app.setStyleSheet(_STYLESHEET)
