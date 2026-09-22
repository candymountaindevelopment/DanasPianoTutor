"""Paste-and-import dialog for authored JSON.

This is the chatbot workflow: copy the JSON out of a chat, paste it here, read
the report. The report matters more than it looks — it is what a user feeds
back to the chatbot to get a corrected document.
"""

from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .theme import COLORS

PLACEHOLDER = """{
  "format": "raw.author",
  "version": 1,
  "sounds": [
    {"name": "sfx_pickup", "from_preset": "coin", "pitch": {"from": "E5", "to": "B5"}}
  ]
}"""


class ImportAuthoredDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.setWindowTitle("Import Authored JSON")
        self.resize(760, 620)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Paste a document in the RAW authoring format. "
            "Help → Copy Chatbot Brief puts the specification on your clipboard."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {COLORS['text_dim']};")
        layout.addWidget(intro)

        self.editor = QPlainTextEdit()
        self.editor.setFont(mono)
        self.editor.setPlaceholderText(PLACEHOLDER)
        layout.addWidget(self.editor, 3)

        layout.addWidget(QLabel("Report"))
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(mono)
        self.report.setMaximumHeight(190)
        layout.addWidget(self.report, 1)

        self.merge = QCheckBox("Update assets that already have the same name")
        self.merge.setChecked(True)
        self.merge.setToolTip(
            "On: re-importing an edited document updates the existing assets in place, "
            "keeping every timeline clip and slot binding pointed at them.\n"
            "Off: everything is imported as a new copy with a suffixed name."
        )
        layout.addWidget(self.merge)

        row = QHBoxLayout()
        check = QPushButton("Check Only")
        check.clicked.connect(lambda: self._run(dry=True))
        row.addWidget(check)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.import_btn = QPushButton("Import")
        self.import_btn.setObjectName("primary")
        self.import_btn.clicked.connect(lambda: self._run(dry=False))
        row.addWidget(self.import_btn)
        row.addWidget(buttons)
        layout.addLayout(row)

    def _run(self, dry: bool) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            self.report.setPlainText("Nothing to import.")
            return

        merge = self.merge.isChecked()
        if dry:
            from ..core.authoring import parse_document

            result = parse_document(text, self.c.project, merge=merge)
        else:
            result = self.c.import_authored(text, merge=merge)

        lines = []
        if result.errors:
            lines.append(f"{len(result.errors)} error(s) — nothing usable was produced:")
            lines += [f"  ✗ {e}" for e in result.errors]
        if result.updated:
            verb = "would update" if dry else "updated"
            lines.append(f"{verb} {len(result.updated)} existing asset(s):")
            lines += [f"  ↻ {a.name}  ({a.type_name})" for a in result.updated]
        if result.assets:
            verb = "would add" if dry else "added"
            lines.append(f"{verb} {len(result.assets)} new asset(s):")
            lines += [f"  + {a.name}  ({a.type_name})" for a in result.assets]
        if result.slots:
            lines.append(f"{len(result.slots)} slot(s): " + ", ".join(sorted(result.slots)))
        if result.warnings:
            lines.append(f"{len(result.warnings)} warning(s):")
            lines += [f"  ⚠ {w}" for w in result.warnings]
        if not result.errors and not result.warnings:
            lines.append("No problems found.")
        self.report.setPlainText("\n".join(lines))
