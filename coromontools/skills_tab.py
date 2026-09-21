"""The Skills tab: every skill in the wiki's columns, with a full description.

Nothing here reads the save or the tick list - it is the whole skill list all the time,
filtered only by what is typed above, so it works whether or not the left-hand area list is
set up. The columns and the wording of every cell come from `skills.py`, the same module the
command line uses, so the table and `python skills.py` cannot drift apart.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QSplitter,
                               QVBoxLayout, QWidget)

import skills

from .table import PAYLOAD, Column, DataTable
from .widgets import mono_text

ALL = "all"

COLUMNS = tuple(
    Column(key, heading, width, anchor, desc_first=key in ("sp", "power", "acc"))
    for (key, heading, width, anchor) in skills.COLUMNS
)


class SkillsTab(QWidget):
    """The skill table and the description of the selected one."""

    def __init__(self, skill_list, prefs, parent=None):
        super().__init__(parent)
        self.skills = skill_list
        self.prefs = prefs
        self._build()
        self.refresh()

    def _build(self):
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.addWidget(QLabel("find"))
        self.search = QLineEdit()
        self.search.setText(str(self.prefs.get("skill_search", "")))
        self.search.textChanged.connect(self._filters_changed)
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("type"))
        self.type_box = QComboBox()
        self.type_box.addItem(ALL)
        for name in skills.types(self.skills):
            self.type_box.addItem(name)
        saved_type = str(self.prefs.get("skill_type", ALL))
        index = self.type_box.findText(saved_type)
        self.type_box.setCurrentIndex(index if index >= 0 else 0)
        self.type_box.currentIndexChanged.connect(self._filters_changed)
        top.addWidget(self.type_box)
        self.count_label = QLabel("")
        top.addWidget(self.count_label)
        box.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.table = DataTable(COLUMNS,
                               sort_key=str(self.prefs.get("skill_sort", "name")),
                               sort_desc=bool(self.prefs.get("skill_desc", False)))
        self.table.selectionChangedTo.connect(self.show_skill)
        self.table.sortChanged.connect(self._sort_changed)
        splitter.addWidget(self.table)

        self.detail = mono_text(wrap=True)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        box.addWidget(splitter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 6)
        outer.addWidget(panel)

    # ------------------------------------------------------------------ contents
    def _filters_changed(self, _value=None):
        self.prefs.set("skill_search", self.search.text())
        self.prefs.set("skill_type", self.type_box.currentText())
        self.refresh()

    def _sort_changed(self, key, desc):
        self.prefs.set("skill_sort", key)
        self.prefs.set("skill_desc", desc)

    def refresh(self):
        picked = skills.shown(self.skills, self.type_box.currentText(), self.search.text())
        rows = []
        for skill in picked:
            cells = skills.row(skill)
            row = {key: cells[key] for (key, _heading, _width, _anchor) in skills.COLUMNS}
            row[PAYLOAD] = skill
            rows.append(row)
        self.table.set_rows(rows)
        self.count_label.setText("%d of %d skills" % (len(picked), len(self.skills)))
        # the same selected skill may still be listed, but its row is gone from the model after
        # the reset, so the description is re-shown from whatever is selected now
        self.show_skill(self.table.current_payload())

    def show_skill(self, skill):
        self.detail.setPlainText(skills.describe(skill) if skill is not None else "")
