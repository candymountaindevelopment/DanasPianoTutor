"""Main window: docks, menus, shortcuts, status bar."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..audio.io import backend_summary, export_filter, import_filter, readable_extensions
from ..core.assets import AudioAsset, SynthAsset
from ..core.commands import SetSettings
from ..core.validation import summarize
from ..export import EXPORT_PROFILES, export_game_pack
from ..export.game_pack import export_constants
from ..export.sheet import pattern_to_musicxml, write_musicxml
from ..synth import engine
from .asset_manager import AssetManager
from .chord_lab import ChordLab
from .console_dock import ConsoleDock
from .sample_fx import SampleFx
from .sample_lab import SampleLab
from .synth_dock import SynthDock
from .theme import COLORS, STYLESHEET
from .timeline import TimelineDock
from .timestructure_dock import TimeStructureDock

PROJECT_FILTER = "RAW Project (*.raw.json);;JSON (*.json);;All files (*)"

EDIT_WIDGETS = (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox)


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Project Settings")
        self.c = controller
        s = controller.project.settings
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name = QLineEdit(controller.project.metadata.get("name", "Untitled"))
        self.rate = QComboBox()
        self.rate.addItems(["22050", "44100", "48000"])
        self.rate.setCurrentText(str(s.sample_rate))
        self.tempo = QDoubleSpinBox()
        self.tempo.setRange(20.0, 400.0)
        self.tempo.setValue(s.tempo)
        self.fps = QSpinBox()
        self.fps.setRange(1, 240)
        self.fps.setValue(s.fps)
        form.addRow("Project name", self.name)
        form.addRow("Sample rate", self.rate)
        form.addRow("Tempo (BPM)", self.tempo)
        form.addRow("Game FPS", self.fps)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        self.c.project.metadata["name"] = self.name.text().strip() or "Untitled"
        self.c.push(
            SetSettings(
                sample_rate=int(self.rate.currentText()),
                tempo=self.tempo.value(),
                fps=self.fps.value(),
            )
        )
        self.c.renderer.cache.clear()
        self.c.projectChanged.emit()


class MainWindow(QMainWindow):
    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        self.setWindowTitle("Retro Audio Workstation")
        self.resize(1500, 900)
        self.setStyleSheet(STYLESHEET)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AnimatedDocks
        )

        self.timeline = TimelineDock(controller)
        self.setCentralWidget(self.timeline)

        self.assets_dock = self._dock("ASSET MANAGER", AssetManager(controller),
                                      Qt.DockWidgetArea.LeftDockWidgetArea, 300)
        self.synth_dock = self._dock("SYNTHESIZER", SynthDock(controller),
                                     Qt.DockWidgetArea.RightDockWidgetArea, 340)
        self.structure_dock = self._dock("TIME STRUCTURE", TimeStructureDock(controller),
                                         Qt.DockWidgetArea.RightDockWidgetArea, 340)
        self.sample_dock = self._dock("SAMPLE LAB", SampleLab(controller),
                                      Qt.DockWidgetArea.BottomDockWidgetArea, 240)
        self.fx_dock = self._dock("SAMPLE FX", SampleFx(controller),
                                  Qt.DockWidgetArea.BottomDockWidgetArea, 240)
        self.chord_dock = self._dock("CHORD LAB", ChordLab(controller),
                                     Qt.DockWidgetArea.BottomDockWidgetArea, 260)
        self.console_dock = self._dock("CONSOLE", ConsoleDock(controller),
                                       Qt.DockWidgetArea.BottomDockWidgetArea, 190)
        self.tabifyDockWidget(self.synth_dock, self.structure_dock)
        self.tabifyDockWidget(self.sample_dock, self.fx_dock)
        self.tabifyDockWidget(self.fx_dock, self.chord_dock)
        self.tabifyDockWidget(self.chord_dock, self.console_dock)
        self.synth_dock.raise_()
        self.sample_dock.raise_()

        self._build_menus()
        self._build_status()

        self.c.statusMessage.connect(lambda m, t: self.statusBar().showMessage(m, t))
        self.c.dirtyChanged.connect(self._update_title)
        self.c.projectChanged.connect(self._update_title)
        self.c.assetsChanged.connect(self._update_counts)
        self.c.selectionChanged.connect(lambda _: self._update_counts())
        QApplication.instance().focusChanged.connect(self._focus_changed)

        self._update_title()
        self._update_counts()

    def _dock(self, title: str, widget: QWidget, area, size: int) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setWidget(widget)
        dock.setObjectName(title.replace(" ", "_"))
        self.addDockWidget(area, dock)
        if area in (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea):
            dock.setMinimumWidth(280)
        else:
            dock.setMinimumHeight(120)
        return dock

    # ----------------------------------------------------------------- menus

    def _act(self, menu, label, slot, shortcut=None, tip=""):
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        bar = self.menuBar()

        f = bar.addMenu("&File")
        self._act(f, "New Project", self.new_project, "Ctrl+N")
        self._act(f, "Open Project…", self.open_project, "Ctrl+O")
        f.addSeparator()
        self._act(f, "Save", self.save_project, "Ctrl+S")
        self._act(f, "Save As…", self.save_project_as, "Ctrl+Shift+S")
        f.addSeparator()
        self._act(f, "Import Sample…", self.import_sample, "Ctrl+I")
        self._act(f, "Import Authored JSON…", self.import_authored_paste, "Ctrl+J",
                  "Paste a document written in the RAW authoring format")
        self._act(f, "Import Authored JSON from File…", self.import_authored_file)
        self._act(f, "Export Authoring Format…", self.export_authored,
                  tip="Write this project as an authoring document — a worked example for a chatbot")
        f.addSeparator()
        self._act(f, "Export Selected Audio…", self.export_selected, "Ctrl+E")
        self._act(f, "Export Game Pack…", self.export_pack)
        self._act(f, "Export Code Constants…", self.export_constants)
        self._act(f, "Export Sheet Music (MusicXML)…", self.export_sheet,
                  tip="Write the selected pattern as piano sheet music for MuseScore, Sibelius, Finale or Dorico")
        f.addSeparator()
        self._act(f, "Exit", self.close, "Ctrl+Q")

        e = bar.addMenu("&Edit")
        self.act_undo = self._act(e, "Undo", self.c.undo, "Ctrl+Z")
        self.act_redo = self._act(e, "Redo", self.c.redo, "Ctrl+Y")
        e.addSeparator()
        self._act(e, "Duplicate", self.duplicate_focused, "Ctrl+D",
                  "Duplicates timeline clips when the timeline has focus, otherwise the asset")
        self._act(e, "Delete", self.delete_focused, "Del",
                  "Removes timeline clips when the timeline has focus, otherwise the asset")
        e.addSeparator()
        clips_menu = e.addMenu("Timeline Clips")
        for action in self.timeline.clip_actions:
            clips_menu.addAction(action)
        self._rebind_stack()
        # The command stack is replaced when a project is opened or created.
        self.c.projectChanged.connect(self._rebind_stack)

        v = bar.addMenu("&View")
        for dock in (self.assets_dock, self.synth_dock, self.structure_dock,
                     self.sample_dock, self.fx_dock, self.chord_dock, self.console_dock):
            v.addAction(dock.toggleViewAction())
        v.addSeparator()
        self._act(v, "Reset Layout", self.reset_layout)

        p = bar.addMenu("&Project")
        self._act(p, "Project Settings…", self.project_settings)
        self._act(p, "Validate Project", self.validate_project)
        self._act(p, "Clear Render Cache", self.clear_cache)

        a = bar.addMenu("&Audio")
        new_menu = a.addMenu("New Sound")
        for name in engine.PRESET_NAMES:
            self._act(new_menu, name.title(), lambda _=False, n=name: self.new_sound(n))
        a.addSeparator()
        self.act_play = self._act(a, "Preview", self.play_selected, "Space")
        self._act(a, "Stop", self.c.stop, "Ctrl+.")
        self._act(a, "Render Timeline", self.timeline.render_timeline)
        a.addSeparator()
        self._act(a, "Insert Demo Sounds", self.insert_demo)

        h = bar.addMenu("&Help")
        self._act(h, "Copy Chatbot Brief", self.copy_brief,
                  tip="Copy the authoring-format specification to the clipboard, "
                      "ready to paste into a chatbot")
        self._act(h, "Shortcuts", self.show_shortcuts)
        self._act(h, "Danas Piano Tutor…", self.open_piano_tutor,
                  tip="Open the lesson tutor: scripted beginner pieces with fingering, "
                      "playback, metronome and printable sheet music")
        self._act(h, "About", self.show_about)

    def _build_status(self) -> None:
        self.lbl_counts = QLabel("")
        self.lbl_backend = QLabel(f"audio: {self.c.playback.name}")
        self.lbl_backend.setToolTip(
            f"Playback: {self.c.playback.name}\nFile formats: {backend_summary()}\n"
            f"Readable: {' '.join(readable_extensions())}"
        )
        self.lbl_cache = QLabel("")
        for w in (self.lbl_counts, self.lbl_cache, self.lbl_backend):
            self.statusBar().addPermanentWidget(w)
        self.statusBar().showMessage("Ready")

    # ----------------------------------------------------------------- state

    def _update_title(self) -> None:
        name = self.c.project.metadata.get("name", "Untitled")
        path = self.c.project.path.name if self.c.project.path else "unsaved"
        mark = "• " if self.c.dirty else ""
        self.setWindowTitle(f"{mark}{name} — {path} — Retro Audio Workstation")

    def _update_counts(self) -> None:
        n = len(self.c.project.assets)
        sel = self.c.selected
        self.lbl_counts.setText(f"{n} assets" + (f" · {sel.name}" if sel else ""))
        self.lbl_cache.setText(self.c.renderer.cache.stats)

    def _rebind_stack(self) -> None:
        if self._update_undo not in self.c.stack.on_change:
            self.c.stack.on_change.append(self._update_undo)
        self._update_undo()

    def _update_undo(self) -> None:
        self.act_undo.setEnabled(self.c.stack.can_undo)
        self.act_redo.setEnabled(self.c.stack.can_redo)
        self.act_undo.setText(f"Undo {self.c.stack.undo_label}".strip())
        self.act_redo.setText(f"Redo {self.c.stack.redo_label}".strip())

    def _focus_changed(self, old, new) -> None:
        """Free Space for typing when a text field has focus."""
        editing = isinstance(new, EDIT_WIDGETS)
        self.act_play.setShortcut(QKeySequence() if editing else QKeySequence("Space"))

    def _focus_in_timeline(self) -> bool:
        widget = QApplication.focusWidget()
        while widget is not None:
            if widget is self.timeline:
                return True
            widget = widget.parentWidget()
        return False

    def delete_focused(self) -> None:
        if self._focus_in_timeline():
            self.timeline.view.delete_clips()
        else:
            self.assets_dock.widget()._delete()

    def duplicate_focused(self) -> None:
        if self._focus_in_timeline():
            self.timeline.view.duplicate_clips()
        elif self.c.selected_uid:
            self.c.duplicate_asset(self.c.selected_uid)

    # --------------------------------------------------------------- actions

    def _confirm_discard(self) -> bool:
        if not self.c.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved Changes",
            "This project has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def new_project(self) -> None:
        if self._confirm_discard():
            self.c.new_project()

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", PROJECT_FILTER)
        if not path:
            return
        try:
            self.c.open_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", f"{type(exc).__name__}: {exc}")

    def save_project(self) -> bool:
        if self.c.project.path is None:
            return self.save_project_as()
        try:
            self.c.save_project()
            self._update_title()
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"{type(exc).__name__}: {exc}")
            return False

    def save_project_as(self) -> bool:
        suggested = f"{self.c.project.metadata.get('name', 'project')}.raw.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", suggested, PROJECT_FILTER)
        if not path:
            return False
        try:
            self.c.save_project(path)
            self._update_title()
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"{type(exc).__name__}: {exc}")
            return False

    def import_sample(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Sample", "", import_filter())
        if not path:
            return
        try:
            self.c.import_sample(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------- authored JSON

    BRIEF_PATH = Path(__file__).resolve().parents[2] / "docs" / "AUTHORING_FORMAT.md"

    def import_authored_paste(self) -> None:
        from .import_dialog import ImportAuthoredDialog

        ImportAuthoredDialog(self.c, self).exec()

    def import_authored_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Authored JSON", "", "JSON (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            result = self.c.import_authored(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", f"{type(exc).__name__}: {exc}")
            return
        if result.errors or result.warnings:
            QMessageBox.information(
                self,
                "Import Report",
                "\n".join(
                    [f"Added {len(result.assets)}, updated {len(result.updated)}."]
                    + [f"✗ {e}" for e in result.errors]
                    + [f"⚠ {w}" for w in result.warnings[:25]]
                ),
            )

    def export_authored(self) -> None:
        import json

        suggested = f"{self.c.project.metadata.get('name', 'project')}.author.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Authoring Format", suggested, "JSON (*.json);;All files (*)"
        )
        if not path:
            return
        Path(path).write_text(
            json.dumps(self.c.export_authored(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.c.status(f"Wrote {Path(path).name}")

    def copy_brief(self) -> None:
        if not self.BRIEF_PATH.exists():
            QMessageBox.warning(
                self, "Brief not found",
                f"Expected the specification at:\n{self.BRIEF_PATH}"
            )
            return
        QApplication.clipboard().setText(self.BRIEF_PATH.read_text(encoding="utf-8"))
        self.c.status(
            "Authoring specification copied. Paste it into a chatbot, then ask for sounds "
            "or patterns and bring the JSON back with Ctrl+J.",
            12000,
        )

    def new_sound(self, preset: str) -> None:
        self.c.add_synth_asset(f"sfx_{preset}", engine.preset(preset))
        self.synth_dock.raise_()

    def insert_demo(self) -> None:
        for name in engine.PRESET_NAMES:
            self.c.add_synth_asset(f"sfx_{name}", engine.preset(name))
        self.c.status("Inserted demo sounds — select one and press Space")

    def play_selected(self) -> None:
        if self.c.selected is None:
            self.c.status("Select an asset to preview")
            return
        self.c.preview()

    def export_selected(self) -> None:
        asset = self.c.selected
        if asset is None or not isinstance(asset, (SynthAsset, AudioAsset)):
            QMessageBox.information(self, "Export", "Select a sound or sample first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Audio", f"{asset.name}.wav", export_filter()
        )
        if not path:
            return
        try:
            from ..audio.io import write_audio

            buf = self.c.render(asset, preview=False)
            if buf is None:
                return
            write_audio(buf, path, 16)
            self.c.status(f"Exported {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"{type(exc).__name__}: {exc}")

    def export_pack(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Export Game Pack")
        if not directory:
            return
        profile_name, ok = self._choose_profile()
        if not ok:
            return
        profile = EXPORT_PROFILES[profile_name]
        self.c.status(f"Exporting game pack ({profile_name})…", 0)

        def work():
            return export_game_pack(self.c.project, directory, profile, self.c.renderer)

        def done(manifest):
            self.c.status(
                f"Game pack written: {len(manifest['assets'])} assets, "
                f"{len(manifest['patterns'])} patterns → {directory}",
                10000,
            )

        self.c.run_async(work, done)

    def _choose_profile(self) -> tuple[str, bool]:
        dlg = QDialog(self)
        dlg.setWindowTitle("Export Profile")
        layout = QVBoxLayout(dlg)
        combo = QComboBox()
        combo.addItems(list(EXPORT_PROFILES))
        layout.addWidget(QLabel("Target:"))
        layout.addWidget(combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        return combo.currentText(), ok

    def export_constants(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Constants", "audio_constants.py",
            "Python (*.py);;C# (*.cs);;GDScript (*.gd)"
        )
        if not path:
            return
        lang = {"py": "python", "cs": "csharp", "gd": "gdscript"}.get(Path(path).suffix.lstrip("."), "python")
        export_constants(self.c.project, path, lang)
        self.c.status(f"Wrote {Path(path).name}")

    def export_sheet(self) -> None:
        from ..core.assets import PatternAsset

        asset = self.c.selected
        if not isinstance(asset, PatternAsset):
            QMessageBox.information(
                self, "Export Sheet Music",
                "Select a pattern first.\n\nSheet music is written from a pattern's notes; "
                "sounds and samples have no notes to notate.",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sheet Music", f"{asset.name}.musicxml",
            "MusicXML (*.musicxml);;XML (*.xml)",
        )
        if not path:
            return
        try:
            result = pattern_to_musicxml(asset, self.c.project)
            written = write_musicxml(result, path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"{type(exc).__name__}: {exc}")
            return
        summary = f"Wrote {written.name}: {result.note_count} notes, {result.measures} bar(s)"
        self.c.status(summary, 10000)
        if result.warnings:
            QMessageBox.information(
                self, "Sheet Music Exported",
                summary + "\n\n" + "\n".join(f"⚠ {w}" for w in result.warnings),
            )

    def project_settings(self) -> None:
        dlg = SettingsDialog(self.c, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply()
            self._update_title()

    def validate_project(self) -> None:
        issues = self.c.validate()
        dlg = QDialog(self)
        dlg.setWindowTitle("Project Validation")
        dlg.resize(620, 420)
        layout = QVBoxLayout(dlg)
        header = QLabel(summarize(issues))
        header.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        layout.addWidget(header)
        listing = QListWidget()
        for issue in issues:
            listing.addItem(str(issue))
        if not issues:
            listing.addItem("✓ No issues found.")
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def clear_cache(self) -> None:
        from ..audio.io import clear_source_cache, source_cache_stats

        freed = source_cache_stats()
        self.c.renderer.cache.clear()
        clear_source_cache()
        self._update_counts()
        self.c.status(f"Render cache cleared · dropped decoded sources ({freed})")

    def reset_layout(self) -> None:
        docks = (self.assets_dock, self.synth_dock, self.structure_dock,
                 self.sample_dock, self.fx_dock, self.console_dock)
        for dock in docks:
            dock.setFloating(False)
            dock.show()
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.assets_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.synth_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.structure_dock)
        for dock in (self.sample_dock, self.fx_dock, self.chord_dock, self.console_dock):
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.tabifyDockWidget(self.synth_dock, self.structure_dock)
        self.tabifyDockWidget(self.sample_dock, self.fx_dock)
        self.tabifyDockWidget(self.fx_dock, self.chord_dock)
        self.tabifyDockWidget(self.chord_dock, self.console_dock)
        self.synth_dock.raise_()
        self.sample_dock.raise_()

    def show_shortcuts(self) -> None:
        QMessageBox.information(
            self,
            "Shortcuts",
            "Space\tPreview selected\n"
            "Ctrl+.\tStop\n"
            "Ctrl+N/O/S\tNew / Open / Save\n"
            "Ctrl+I\tImport sample\n"
            "Ctrl+E\tExport selected audio\n"
            "Ctrl+Z/Y\tUndo / Redo\n"
            "Ctrl+D\tDuplicate (clips if the timeline has focus, else the asset)\n"
            "Del\tDelete (clips if the timeline has focus, else the asset)\n"
            "\nIn the timeline:\n"
            "Ctrl+C/X/V\tCopy / cut / paste clips at the playhead\n"
            "Ctrl+A\tSelect all clips\n"
            "drag\tDrag assets in from the Asset Manager\n"
            "Ctrl+Wheel\tZoom the timeline",
        )

    def open_piano_tutor(self) -> None:
        from .teach.window import TeachWindow

        window = TeachWindow()
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._tutor_windows = [w for w in getattr(self, "_tutor_windows", []) if w.isVisible()]
        self._tutor_windows.append(window)
        window.show()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            f"<b>Retro Audio Workstation</b> {__version__}<br><br>"
            "Sounds are stored as recipes, not waveforms. Stretching a synth "
            "asset re-evaluates the recipe over a new time axis, so pitch "
            "identity is preserved exactly.<br><br>"
            f"Project format v2<br>playback: {self.c.playback.name}<br>"
            f"file formats: {backend_summary()}<br>"
            f"readable: {' '.join(readable_extensions())}",
        )

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            self.c.stop()
            event.accept()
        else:
            event.ignore()
