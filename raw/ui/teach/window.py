"""Piano Tutor main window.

Layout: the engraved score on top, a scrolling note lane and the keyboard
under it, hands and lesson notes on the right. A transport bar controls
tempo, hands, metronome, count-in and the practice loop. The Script tab holds
the lesson document itself, so a teacher can write a piece, press Apply and
hear it at once.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...audio.io import write_audio
from ...teach import APP_NAME, TAGLINE
from ...teach import __version__ as TUTOR_VERSION
from ...teach.authoring import LessonResult, load_lesson_file
from ...teach.score import HAND_NAMES, HANDS, LEFT, RIGHT, Lesson
from ...teach.sheet import lesson_to_musicxml, write_musicxml
from ..piano import PianoKeyboard
from ..theme import COLORS, STYLESHEET
from .export import print_lesson, write_pdf
from .hands_view import HandsView
from .lane_view import HAND_COLOURS, HAND_DIM, LaneView
from .script_editor import ScriptEditor
from .splash import SplashScreen
from .staff_view import StaffView
from .transport import LessonTransport

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "docs" / "examples" / "lessons"
DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
HAND_MODE_LABELS = [("both", "Both hands"), ("right", "Right hand only"), ("left", "Left hand only")]


class TeachWindow(QMainWindow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 860)
        self.setStyleSheet(STYLESHEET)

        self.transport = LessonTransport(self)
        self.result: LessonResult | None = None
        self.lesson: Lesson | None = None
        self.path: Path | None = None
        self._syncing = False

        self._build_menus()
        self._build_ui()
        self.transport.positionChanged.connect(self._position_changed)
        self.transport.stateChanged.connect(self._state_changed)
        self.transport.message.connect(lambda m: self.statusBar().showMessage(m, 6000))

        self.statusBar().showMessage("Open a lesson (File → Open Example) or write one in the Script tab")
        self.script.load_template()

    # ================================================================ build

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self._act(file_menu, "&Open Lesson…", "Ctrl+O", self.open_lesson)
        self.examples_menu = file_menu.addMenu("Open &Example")
        self._fill_examples()
        self._act(file_menu, "&Reload from Disk", "F5", self.reload)
        file_menu.addSeparator()
        self._act(file_menu, "&New Lesson Script", "Ctrl+N", self.new_script)
        self._act(file_menu, "&Save Script", "Ctrl+S", self.save_script)
        self._act(file_menu, "Save Script &As…", "Ctrl+Shift+S", self.save_script_as)
        file_menu.addSeparator()
        self._act(file_menu, "Export &MusicXML…", "Ctrl+E", self.export_musicxml)
        self._act(file_menu, "Export &PDF…", "Ctrl+Shift+E", self.export_pdf)
        self._act(file_menu, "&Print…", "Ctrl+P", self.print_sheet)
        self._act(file_menu, "Export &Audio (WAV)…", None, self.export_audio)
        file_menu.addSeparator()
        self._act(file_menu, "&Quit", "Ctrl+Q", self.close)

        play_menu = bar.addMenu("&Play")
        self._act(play_menu, "Play / Pause", "Space", self.transport.toggle)
        self._act(play_menu, "Stop", "Escape", self.transport.stop)
        self._act(play_menu, "Rewind", "Home", lambda: self.transport.seek(0))
        play_menu.addSeparator()
        self._act(play_menu, "Slower", "-", lambda: self._nudge_tempo(-5))
        self._act(play_menu, "Faster", "=", lambda: self._nudge_tempo(5))
        self._act(play_menu, "Original tempo", "0", self._reset_tempo)

        view_menu = bar.addMenu("&View")
        self.inferred_action = QAction("Show inferred fingering", self, checkable=True, checked=True)
        self.inferred_action.setToolTip("Fingers derived from the hand position are drawn in grey")
        self.inferred_action.toggled.connect(self._toggle_inferred)
        view_menu.addAction(self.inferred_action)
        self._act(view_menu, "Larger score", "Ctrl+=", lambda: self.staff.set_scale(self.staff.canvas.scale + 1))
        self._act(view_menu, "Smaller score", "Ctrl+-", lambda: self.staff.set_scale(self.staff.canvas.scale - 1))

        help_menu = bar.addMenu("&Help")
        self._act(help_menu, "Lesson Scripting Reference", "F1", self.show_reference)
        self._act(help_menu, "Copy Reference for a Chatbot", None, self.copy_reference)
        help_menu.addSeparator()
        self._act(help_menu, "Splash Screen", None, self.show_splash)
        self._act(help_menu, "About", None, self.about)

    def _act(self, menu, text, shortcut, slot) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _fill_examples(self) -> None:
        self.examples_menu.clear()
        files = sorted(EXAMPLES_DIR.glob("*.json")) if EXAMPLES_DIR.exists() else []
        if not files:
            self.examples_menu.addAction("(no examples found)").setEnabled(False)
        for path in files:
            action = self.examples_menu.addAction(path.stem.replace("_", " ").title())
            action.triggered.connect(lambda _checked=False, p=path: self.load_path(p))

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)
        outer.addWidget(self._build_transport())

        split = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(split, 1)

        # Left column: score / lane / keyboard.
        left = QSplitter(Qt.Orientation.Vertical)
        self.staff = StaffView()
        self.staff.divisionClicked.connect(self.transport.seek)
        left.addWidget(self.staff)
        self.lane = LaneView()
        self.lane.divisionClicked.connect(self.transport.seek)
        left.addWidget(self.lane)
        self.keys = PianoKeyboard(low_midi=48, octaves=3)
        self.keys.interactive = False
        self.keys.setMinimumHeight(110)
        self.keys.keyClicked.connect(self.transport.preview_note)
        left.addWidget(self.keys)
        left.setStretchFactor(0, 5)
        left.setStretchFactor(1, 2)
        left.setStretchFactor(2, 0)
        left.setSizes([520, 150, 120])
        split.addWidget(left)

        # Right column: hands, then tabs.
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        self.hands = HandsView()
        self.hands.setMinimumHeight(230)
        self.hands.setMaximumHeight(280)
        rl.addWidget(self.hands)
        self.tabs = QTabWidget()
        rl.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([960, 400])

        # Lesson tab.
        lesson_tab = QWidget()
        ll = QVBoxLayout(lesson_tab)
        ll.setContentsMargins(4, 4, 4, 4)
        self.lesson_list = QListWidget()
        self.lesson_list.setMaximumHeight(120)
        self.lesson_list.currentRowChanged.connect(self._lesson_selected)
        ll.addWidget(QLabel("Lessons in this document"))
        ll.addWidget(self.lesson_list)
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)
        ll.addWidget(self.info, 1)
        self.tabs.addTab(lesson_tab, "Lesson")

        # Script tab.
        self.script = ScriptEditor()
        self.script.applied.connect(self._script_applied)
        self.script.modified.connect(lambda _d: self._update_title())
        self.tabs.addTab(self.script, "Script")

        self.setCentralWidget(central)

    def _build_transport(self) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        self.play_btn = QPushButton("▶  Play")
        self.play_btn.setObjectName("primary")
        self.play_btn.setMinimumWidth(90)
        self.play_btn.clicked.connect(self.transport.toggle)
        h.addWidget(self.play_btn)
        stop = QPushButton("■")
        stop.setToolTip("Stop (Esc)")
        stop.clicked.connect(self.transport.stop)
        h.addWidget(stop)

        self.position_label = QLabel("bar 1 · beat 1")
        self.position_label.setMinimumWidth(120)
        self.position_label.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600;")
        h.addWidget(self.position_label)

        h.addWidget(self._vsep())
        h.addWidget(QLabel("Tempo"))
        self.tempo_slider = QSlider(Qt.Orientation.Horizontal)
        self.tempo_slider.setRange(25, 150)
        self.tempo_slider.setValue(100)
        self.tempo_slider.setMinimumWidth(140)
        self.tempo_slider.setToolTip("Playback speed as a percentage of the lesson's tempo")
        self.tempo_slider.valueChanged.connect(self._tempo_slider_changed)
        h.addWidget(self.tempo_slider)
        self.tempo_spin = QSpinBox()
        self.tempo_spin.setRange(20, 300)
        self.tempo_spin.setSuffix(" bpm")
        self.tempo_spin.valueChanged.connect(self._tempo_spin_changed)
        h.addWidget(self.tempo_spin)
        self.tempo_pct = QLabel("100%")
        self.tempo_pct.setMinimumWidth(38)
        h.addWidget(self.tempo_pct)

        h.addWidget(self._vsep())
        self.hands_combo = QComboBox()
        for key, label in HAND_MODE_LABELS:
            self.hands_combo.addItem(label, key)
        self.hands_combo.currentIndexChanged.connect(self._hands_changed)
        h.addWidget(self.hands_combo)

        h.addWidget(self._vsep())
        self.metronome_check = QCheckBox("Metronome")
        self.metronome_check.setChecked(True)
        self.metronome_check.toggled.connect(lambda on: self.transport.update_options(metronome=bool(on)))
        h.addWidget(self.metronome_check)
        h.addWidget(QLabel("Count-in"))
        self.count_in_spin = QSpinBox()
        self.count_in_spin.setRange(0, 2)
        self.count_in_spin.setValue(1)
        self.count_in_spin.setSuffix(" bar")
        self.count_in_spin.valueChanged.connect(lambda v: self.transport.update_options(count_in_bars=int(v)))
        h.addWidget(self.count_in_spin)

        h.addWidget(self._vsep())
        self.loop_check = QCheckBox("Loop")
        self.loop_check.setToolTip("Repeat the practice range until you stop")
        self.loop_check.toggled.connect(self.transport.set_loop)
        h.addWidget(self.loop_check)
        h.addWidget(QLabel("bars"))
        self.from_spin = QSpinBox()
        self.from_spin.setRange(1, 1)
        self.from_spin.setPrefix("from ")
        self.from_spin.valueChanged.connect(self._range_changed)
        h.addWidget(self.from_spin)
        self.to_spin = QSpinBox()
        self.to_spin.setRange(1, 1)
        self.to_spin.setPrefix("to ")
        self.to_spin.valueChanged.connect(self._range_changed)
        h.addWidget(self.to_spin)
        whole = QPushButton("Whole piece")
        whole.clicked.connect(self._whole_piece)
        h.addWidget(whole)
        h.addStretch(1)
        return bar

    @staticmethod
    def _vsep() -> QWidget:
        w = QLabel("|")
        w.setStyleSheet(f"color: {COLORS['border']};")
        return w

    # ============================================================ documents

    def load_path(self, path: str | Path) -> None:
        if not self._confirm_discard():
            return
        path = Path(path)
        result = load_lesson_file(path)
        if not result.ok:
            QMessageBox.warning(self, "Cannot open lesson", "\n".join(result.errors))
            return
        self.path = path
        self.script.set_text(path.read_text(encoding="utf-8"))
        self.script.report.setPlainText(result.report())
        self._apply_result(result)
        self.statusBar().showMessage(f"Opened {path.name}: {len(result.lessons)} lesson(s)", 5000)

    def open_lesson(self) -> None:
        start = str(self.path.parent if self.path else EXAMPLES_DIR)
        path, _ = QFileDialog.getOpenFileName(self, "Open lesson", start, "Lesson documents (*.json);;All files (*)")
        if path:
            self.load_path(path)

    def reload(self) -> None:
        if self.path and self.path.exists():
            self.load_path(self.path)

    def new_script(self) -> None:
        if not self._confirm_discard():
            return
        self.path = None
        self.script.load_template()
        self.script.apply()
        self.tabs.setCurrentWidget(self.script)

    def save_script(self) -> None:
        if self.path is None:
            self.save_script_as()
            return
        self.path.write_text(self.script.text(), encoding="utf-8")
        self.script.mark_clean()
        self.statusBar().showMessage(f"Saved {self.path.name}", 4000)

    def save_script_as(self) -> None:
        suggested = self.path or (EXAMPLES_DIR.parent / f"{self.lesson.name if self.lesson else 'lesson'}.lesson.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save lesson script", str(suggested), "Lesson documents (*.json)")
        if not path:
            return
        self.path = Path(path)
        self.save_script()

    def _confirm_discard(self) -> bool:
        if not self.script.dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved script",
            "The script has edits that were not saved. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            self.transport.stop()
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------- results

    def _script_applied(self, result: LessonResult) -> None:
        if result.ok:
            self._apply_result(result)
            self.statusBar().showMessage(
                f"Applied: {len(result.lessons)} lesson(s), {len(result.warnings)} warning(s)", 5000)
        else:
            self.statusBar().showMessage("Script has errors — see the report", 6000)

    def _apply_result(self, result: LessonResult) -> None:
        self.result = result
        current = self.lesson.name if self.lesson else None
        self._syncing = True
        self.lesson_list.clear()
        for lesson in result.lessons:
            self.lesson_list.addItem(f"{lesson.title}  ·  level {lesson.level}")
        self._syncing = False
        names = [lesson.name for lesson in result.lessons]
        row = names.index(current) if current in names else 0
        self.lesson_list.setCurrentRow(row)
        self._lesson_selected(row)

    def _lesson_selected(self, row: int) -> None:
        if self._syncing or self.result is None or row < 0 or row >= len(self.result.lessons):
            return
        self.set_lesson(self.result.lessons[row])

    def set_lesson(self, lesson: Lesson) -> None:
        self.lesson = lesson
        self.transport.set_lesson(lesson)
        self.staff.set_lesson(lesson)
        self.lane.set_lesson(lesson)
        self.hands.set_lesson(lesson)
        self._fit_keyboard(lesson)
        self._syncing = True
        self.from_spin.setRange(1, lesson.measures)
        self.to_spin.setRange(1, lesson.measures)
        self.from_spin.setValue(1)
        self.to_spin.setValue(lesson.measures)
        self.tempo_slider.setValue(100)
        self.tempo_spin.setValue(int(round(lesson.tempo)))
        self._syncing = False
        self._update_tempo_labels()
        self._show_info(lesson)
        self._position_changed(0.0)
        self._update_title()

    def _fit_keyboard(self, lesson: Lesson) -> None:
        lo, hi = lesson.midi_range()
        for hand in HANDS:
            keys = lesson.position_keys(hand)
            if keys:
                lo, hi = min(lo, keys[0]), max(hi, keys[-1])
        low = (lo // 12) * 12
        octaves = max(2, (hi - low) // 12 + 1)
        self.keys.set_range(low, min(octaves, 5))

    def _show_info(self, lesson: Lesson) -> None:
        ts = f"{lesson.time_signature[0]}/{lesson.time_signature[1]}"
        rows = [
            ("Key", lesson.key), ("Time", ts), ("Tempo", f"{lesson.tempo:g} bpm"),
            ("Bars", str(lesson.measures)), ("Voice", lesson.voice),
        ]
        html = [f"<h2 style='margin:0'>{_esc(lesson.title)}</h2>"]
        if lesson.composer:
            html.append(f"<p style='margin:0;color:{COLORS['text_dim']}'>{_esc(lesson.composer)}</p>")
        html.append("<table cellpadding='2'>")
        for k, v in rows:
            html.append(f"<tr><td style='color:{COLORS['text_dim']}'>{k}</td><td><b>{_esc(v)}</b></td></tr>")
        html.append("</table>")
        html.append("<h3>Hand positions</h3><ul>")
        for hand in HANDS:
            colour = HAND_COLOURS[hand]
            html.append(f"<li><span style='color:{colour}'><b>{HAND_NAMES[hand]}</b></span>: "
                        f"{_esc(lesson.position_label(hand))}</li>")
        html.append("</ul>")
        if lesson.instructions:
            html.append(f"<h3>Instructions</h3><p>{_esc(lesson.instructions)}</p>")
        tips = list(lesson.tips) or DEFAULT_TIPS
        html.append("<h3>Posture tips</h3><ul>" + "".join(f"<li>{_esc(t)}</li>" for t in tips) + "</ul>")
        problems = [w for w in (self.result.warnings if self.result else []) if f"[{lesson.name}]" in w]
        if problems:
            html.append(f"<h3 style='color:{COLORS['warn']}'>Script warnings</h3><ul>"
                        + "".join(f"<li>{_esc(p)}</li>" for p in problems) + "</ul>")
        self.info.setHtml("".join(html))

    def _update_title(self) -> None:
        name = self.path.name if self.path else "untitled"
        star = " *" if self.script.dirty else ""
        title = self.lesson.title if self.lesson else APP_NAME
        self.setWindowTitle(f"{title} — {name}{star} — {APP_NAME}")

    # ============================================================ transport

    def _tempo_slider_changed(self, pct: int) -> None:
        if self._syncing or self.lesson is None:
            return
        bpm = int(round(self.lesson.tempo * pct / 100.0))
        self._syncing = True
        self.tempo_spin.setValue(max(20, min(300, bpm)))
        self._syncing = False
        self._update_tempo_labels()
        self.transport.update_options(tempo=float(self.tempo_spin.value()))

    def _tempo_spin_changed(self, bpm: int) -> None:
        if self._syncing or self.lesson is None:
            return
        pct = int(round(100.0 * bpm / max(1.0, self.lesson.tempo)))
        self._syncing = True
        self.tempo_slider.setValue(max(25, min(150, pct)))
        self._syncing = False
        self._update_tempo_labels()
        self.transport.update_options(tempo=float(bpm))

    def _update_tempo_labels(self) -> None:
        if self.lesson is None:
            return
        pct = 100.0 * self.tempo_spin.value() / max(1.0, self.lesson.tempo)
        self.tempo_pct.setText(f"{pct:.0f}%")

    def _nudge_tempo(self, delta: int) -> None:
        self.tempo_spin.setValue(self.tempo_spin.value() + delta)

    def _reset_tempo(self) -> None:
        if self.lesson:
            self.tempo_spin.setValue(int(round(self.lesson.tempo)))

    def _hands_changed(self, _index: int) -> None:
        mode = self.hands_combo.currentData() or "both"
        self.transport.update_options(hands=mode)
        dim = {"both": (), "right": (LEFT,), "left": (RIGHT,)}[mode]
        self.staff.set_dim_hands(dim)
        self.lane.set_dim_hands(dim)
        self.hands.set_dim_hands(dim)
        self._refresh_keys()

    def _range_changed(self, _value: int) -> None:
        if self._syncing or self.lesson is None:
            return
        first, last = self.from_spin.value(), self.to_spin.value()
        if last < first:
            self._syncing = True
            self.to_spin.setValue(first)
            self._syncing = False
            last = first
        whole = first == 1 and last == self.lesson.measures
        self.transport.update_options(loop_bars=None if whole else (first, last))
        self.lane.set_loop_range(None if whole else self.lesson.measure_range(first, last))
        if not self.transport.playing:
            self.transport.seek(self.lesson.measure_range(first, last)[0])

    def _whole_piece(self) -> None:
        if self.lesson is None:
            return
        self._syncing = True
        self.from_spin.setValue(1)
        self.to_spin.setValue(self.lesson.measures)
        self._syncing = False
        self._range_changed(0)

    def _state_changed(self, playing: bool) -> None:
        self.play_btn.setText("❚❚  Pause" if playing else "▶  Play")

    def _position_changed(self, division: float) -> None:
        if self.lesson is None:
            return
        self.staff.set_position(division)
        self.lane.set_position(division)
        self.hands.set_position(division)
        self._refresh_keys(division)
        beat = self.transport.count_in_beat(division) if self.transport.playing else None
        if beat is not None:
            self.position_label.setText(f"count-in · {beat}")
        else:
            bar, b = self.lesson.bar_beat(max(0.0, division))
            self.position_label.setText(f"bar {min(bar, self.lesson.measures)} · beat {b}")

    def _refresh_keys(self, division: float | None = None) -> None:
        if self.lesson is None:
            self.keys.set_marks({})
            return
        if division is None:
            division = self.transport.position
        mode = self.hands_combo.currentData() or "both"
        active_hands = {"both": HANDS, "right": (RIGHT,), "left": (LEFT,)}[mode]
        marks: dict[int, tuple[str, str]] = {}
        # Resting position: faint tint with the finger that sits on each key.
        for hand in active_hands:
            keys = self.lesson.position_keys(hand)
            if not keys:
                continue
            for midi in keys:
                finger = self.lesson.finger_for(hand, midi)
                marks[midi] = (HAND_DIM[hand], str(finger) if finger else "")
        for n in self.lesson.sounding(division, active_hands):
            marks[n.midi] = (HAND_COLOURS[n.hand], str(n.shown_finger or ""))
        self.keys.set_marks(marks)

    def _toggle_inferred(self, show: bool) -> None:
        self.staff.set_show_inferred(show)

    # ============================================================== export

    def _need_lesson(self) -> bool:
        if self.lesson is None:
            QMessageBox.information(self, "No lesson", "Open or apply a lesson first.")
            return False
        return True

    def _suggest(self, suffix: str) -> str:
        base = self.path.parent if self.path else Path.home()
        return str(base / f"{self.lesson.name}{suffix}")

    def export_musicxml(self) -> None:
        if not self._need_lesson():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export MusicXML", self._suggest(".musicxml"),
                                              "MusicXML (*.musicxml *.xml)")
        if not path:
            return
        result = lesson_to_musicxml(self.lesson, include_inferred=self.inferred_action.isChecked())
        out = write_musicxml(result, path)
        msg = f"Wrote {out.name}: {result.note_count} notes, {result.measures} bars"
        if result.warnings:
            msg += " — " + "; ".join(result.warnings)
        self.statusBar().showMessage(msg, 8000)

    def export_pdf(self) -> None:
        if not self._need_lesson():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", self._suggest(".pdf"), "PDF (*.pdf)")
        if not path:
            return
        out, pages = write_pdf(self.lesson, path, self.inferred_action.isChecked())
        self.statusBar().showMessage(f"Wrote {out.name} ({pages} page{'s' if pages != 1 else ''})", 6000)

    def print_sheet(self) -> None:
        if not self._need_lesson():
            return
        if not print_lesson(self.lesson, self, self.inferred_action.isChecked()):
            self.statusBar().showMessage("Printing cancelled or unavailable — use Export PDF instead", 6000)

    def export_audio(self) -> None:
        if not self._need_lesson():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export audio", self._suggest(".wav"), "WAV (*.wav)")
        if not path:
            return
        rendered = self.transport.ensure_rendered()
        if rendered is None:
            return
        out = write_audio(rendered.buffer, path)
        self.statusBar().showMessage(f"Wrote {out.name} ({rendered.buffer.duration:.1f} s at "
                                     f"{self.transport.options.tempo:g} bpm)", 6000)

    # ================================================================ help

    def show_reference(self) -> None:
        doc = DOCS_DIR / "LESSON_FORMAT.md"
        text = doc.read_text(encoding="utf-8") if doc.exists() else "docs/LESSON_FORMAT.md is missing."
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Lesson scripting reference")
        browser = QTextBrowser()
        browser.setMarkdown(text)
        browser.setMinimumSize(760, 560)
        layout = dialog.layout()
        layout.addWidget(browser, 0, 0, 1, layout.columnCount())
        dialog.setStandardButtons(QMessageBox.StandardButton.Close)
        dialog.exec()

    def copy_reference(self) -> None:
        doc = DOCS_DIR / "LESSON_FORMAT.md"
        if doc.exists():
            QApplication.clipboard().setText(doc.read_text(encoding="utf-8"))
            self.statusBar().showMessage("Reference copied — paste it into a chatbot and ask for a lesson", 6000)

    def show_splash(self) -> None:
        SplashScreen(timeout_ms=0).show_centered_on(self)

    def about(self) -> None:
        QMessageBox.about(self, APP_NAME,
                          f"<b>{APP_NAME}</b> v{TUTOR_VERSION}<br>Part of Retro Audio Workstation.<br><br>"
                          f"{TAGLINE}<br><br>Lessons are scripted as JSON, synthesised with the RAW engine, "
                          "and engraved for practice and printing.")


DEFAULT_TIPS = [
    "Sit tall on the front half of the bench, feet flat, elbows level with the keys.",
    "Curve your fingers as if holding a ball; play on the fingertips.",
    "Keep wrists level and relaxed — no sagging, no lifting.",
    "Thumb is finger 1, little finger is 5. Say the numbers as you play.",
    "Practise slowly with the metronome; speed comes after accuracy.",
]


def _esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
