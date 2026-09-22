"""The lesson script: a JSON editor with an Apply button and a report pane."""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...teach.authoring import LessonResult, parse_lesson_document
from ..theme import COLORS

TEMPLATE = {
    "format": "raw.author",
    "version": 1,
    "lessons": [
        {
            "name": "five_finger_walk",
            "title": "Five-Finger Walk",
            "tempo": 72,
            "time": "4/4",
            "key": "C",
            "level": 1,
            "position": {"right": "C4", "left": "C3"},
            "instructions": "Sit tall, curve your fingers, and keep each hand in its five-finger position.",
            "tips": ["Play with a relaxed wrist.", "Count 1-2-3-4 out loud."],
            "right": {"notes": "C4(1) D4 E4 F4 | G4(5) F4 E4 D4 | C4:4"},
            "left": {"notes": "C3(5) D3 E3 F3 | G3(1) F3 E3 D3 | C3:4"},
        }
    ],
}


class ScriptEditor(QWidget):
    applied = pyqtSignal(object)      # LessonResult
    modified = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        head = QHBoxLayout()
        self.apply_btn = QPushButton("Apply  (Ctrl+Enter)")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply)
        self.format_btn = QPushButton("Tidy JSON")
        self.format_btn.setToolTip("Re-indent the document (only if it is valid JSON)")
        self.format_btn.clicked.connect(self.tidy)
        head.addWidget(self.apply_btn)
        head.addWidget(self.format_btn)
        head.addStretch(1)
        self.state = QLabel("")
        self.state.setStyleSheet(f"color: {COLORS['text_dim']};")
        head.addWidget(self.state)
        layout.addLayout(head)

        split = QSplitter(Qt.Orientation.Vertical)
        self.editor = QPlainTextEdit()
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(10)
        self.editor.setFont(mono)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setTabStopDistance(24)
        self.editor.textChanged.connect(self._changed)
        split.addWidget(self.editor)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(mono)
        self.report.setMaximumBlockCount(500)
        self.report.setPlaceholderText("Warnings and errors from the last Apply appear here.")
        split.addWidget(self.report)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)

        QShortcut(QKeySequence("Ctrl+Return"), self.editor, activated=self.apply)
        QShortcut(QKeySequence("Ctrl+Enter"), self.editor, activated=self.apply)
        self._clean_text = ""

    # ---------------------------------------------------------------- text

    def text(self) -> str:
        return self.editor.toPlainText()

    def set_text(self, text: str, clean: bool = True) -> None:
        self.editor.setPlainText(text)
        if clean:
            self.mark_clean()

    def set_document(self, doc: dict) -> None:
        self.set_text(json.dumps(doc, indent=2, ensure_ascii=False))

    def load_template(self) -> None:
        self.set_document(TEMPLATE)

    def mark_clean(self) -> None:
        self._clean_text = self.text()
        self._changed()

    @property
    def dirty(self) -> bool:
        return self.text() != self._clean_text

    def _changed(self) -> None:
        dirty = self.dirty
        self.state.setText("edited — press Apply" if dirty else "")
        self.modified.emit(dirty)

    # ------------------------------------------------------------- actions

    def tidy(self) -> None:
        try:
            doc = json.loads(self.text())
        except json.JSONDecodeError as exc:
            self.report.setPlainText(f"error: not valid JSON: {exc}")
            return
        self.editor.setPlainText(json.dumps(doc, indent=2, ensure_ascii=False))

    def apply(self) -> LessonResult:
        result = parse_lesson_document(self.text())
        self.report.setPlainText(result.report())
        if result.ok:
            self.mark_clean()
        self.applied.emit(result)
        return result
