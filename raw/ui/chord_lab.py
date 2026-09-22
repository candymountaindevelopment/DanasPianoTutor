"""Chord Lab — pick notes on a keyboard, hear them, bake them to a sample.

The output is a real WAV in the project plus an AudioAsset, so a baked chord
drops straight into Sample Lab for slicing and Sample FX for processing. The
recipe that produced it is kept in the asset's metadata, so it can be read back
even though the audio has been rendered.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.assets import InstrumentAsset, SynthAsset
from ..core.bake import BakeError, bake_buffer
from ..core.commands import AddAsset
from ..core.ids import slugify
from ..export.sheet import chord_to_musicxml, write_musicxml
from ..patterns.chords import CHORDS, ROOT_NAMES, chord_name, chord_notes, describe, render_chord
from ..synth import engine
from .piano import PianoKeyboard
from .theme import COLORS
from .waveform import WaveformView


class ChordLab(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self._syncing = False
        self._buffer = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # --- chord picker ----------------------------------------------
        picker = QHBoxLayout()
        self.root = QComboBox()
        self.root.addItems(ROOT_NAMES)
        self.root.currentIndexChanged.connect(self._apply_chord)
        self.quality = QComboBox()
        self.quality.addItems(list(CHORDS))
        self.quality.setCurrentText("Minor 7")
        self.quality.currentTextChanged.connect(self._apply_chord)
        self.octave = QSpinBox()
        self.octave.setRange(0, 8)
        self.octave.setValue(4)
        self.octave.setPrefix("oct ")
        self.octave.valueChanged.connect(self._apply_chord)
        self.inversion = QSpinBox()
        self.inversion.setRange(0, 4)
        self.inversion.setPrefix("inv ")
        self.inversion.valueChanged.connect(self._apply_chord)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.keys_clear)
        for w, stretch in ((QLabel("Chord"), 0), (self.root, 0), (self.quality, 2),
                           (self.octave, 0), (self.inversion, 0), (clear, 0)):
            picker.addWidget(w, stretch)
        picker.addStretch(1)
        self.notes_label = QLabel("")
        self.notes_label.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600;")
        picker.addWidget(self.notes_label)
        outer.addLayout(picker)

        self.keys = PianoKeyboard(low_midi=48, octaves=3)
        self.keys.selectionChanged.connect(self._selection_changed)
        outer.addWidget(self.keys)

        body = QHBoxLayout()
        outer.addLayout(body, 1)

        # --- voice ------------------------------------------------------
        voice = QGroupBox("Voice")
        vform = QFormLayout(voice)
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self._changed)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.02, 30.0)
        self.duration.setValue(1.2)
        self.duration.setSingleStep(0.1)
        self.duration.setDecimals(3)
        self.duration.setSuffix(" s")
        self.duration.valueChanged.connect(self._changed)
        self.strum = QDoubleSpinBox()
        self.strum.setRange(0.0, 0.5)
        self.strum.setValue(0.0)
        self.strum.setSingleStep(0.01)
        self.strum.setDecimals(3)
        self.strum.setSuffix(" s")
        self.strum.setToolTip("Delay between successive notes — 0 is a block chord")
        self.strum.valueChanged.connect(self._changed)
        vform.addRow("Instrument", self.source)
        vform.addRow("Duration", self.duration)
        vform.addRow("Strum", self.strum)
        body.addWidget(voice, 1)

        # --- preview ----------------------------------------------------
        preview = QGroupBox("Preview")
        pv = QVBoxLayout(preview)
        self.wave = WaveformView()
        self.wave.setMinimumHeight(80)
        pv.addWidget(self.wave)
        row = QHBoxLayout()
        self.play_btn = QPushButton("▶  Loop")
        self.play_btn.setCheckable(True)
        self.play_btn.setObjectName("primary")
        self.play_btn.toggled.connect(self._toggle_play)
        row.addWidget(self.play_btn)
        self.info = QLabel("")
        self.info.setStyleSheet(f"color: {COLORS['text_dim']};")
        row.addWidget(self.info, 1)
        pv.addLayout(row)
        body.addWidget(preview, 3)

        # --- bake -------------------------------------------------------
        bake = QGroupBox("Bake to Sample")
        bform = QFormLayout(bake)
        self.name = QLineEdit()
        self.name.setPlaceholderText("chord name")
        self.bake_btn = QPushButton("Bake")
        self.bake_btn.setObjectName("primary")
        self.bake_btn.setToolTip(
            "Render to a WAV inside the project and add it as a sample, ready "
            "for Sample Lab and Sample FX"
        )
        self.bake_btn.clicked.connect(self.bake)
        bform.addRow("Name", self.name)
        bform.addRow(self.bake_btn)
        self.sheet_btn = QPushButton("Export Sheet…")
        self.sheet_btn.setToolTip("Write the selected notes as one bar of piano sheet music (MusicXML)")
        self.sheet_btn.clicked.connect(self.export_sheet)
        bform.addRow(self.sheet_btn)
        self.bake_note = QLabel("")
        self.bake_note.setWordWrap(True)
        self.bake_note.setStyleSheet(f"color: {COLORS['text_dim']};")
        bform.addRow(self.bake_note)
        body.addWidget(bake, 1)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._render)

        self.c.assetsChanged.connect(self._reload_sources)
        self.c.projectChanged.connect(self._reload_sources)
        self.c.playbackStarted.connect(self._external_playback)
        self.c.playbackStopped.connect(lambda _origin: self._set_playing(False))

        self._reload_sources()
        self._apply_chord()

    # --------------------------------------------------------------- sources

    def _reload_sources(self) -> None:
        """Built-in presets plus any synth asset in the project."""
        self._syncing = True
        current = self.source.currentData()
        self.source.clear()
        for name in engine.PRESET_NAMES:
            self.source.addItem(f"preset: {name}", f"preset:{name}")
        for asset in self.c.project.assets.sorted():
            if isinstance(asset, (SynthAsset, InstrumentAsset)):
                self.source.addItem(asset.name, f"asset:{asset.uid}")
        index = self.source.findData(current)
        self.source.setCurrentIndex(index if index >= 0 else 0)
        self._syncing = False

    def _params(self):
        data = self.source.currentData() or "preset:blip"
        kind, _, value = data.partition(":")
        if kind == "asset":
            asset = self.c.project.assets.get(value)
            if asset is not None:
                return asset.params.copy()
        params = engine.preset(value if value in engine.PRESET_NAMES else "blip")
        return params

    # ---------------------------------------------------------------- chord

    def _apply_chord(self) -> None:
        if self._syncing:
            return
        root_midi = (self.octave.value() + 1) * 12 + self.root.currentIndex()
        notes = chord_notes(root_midi, self.quality.currentText(), self.inversion.value())
        low = min(notes)
        high = max(notes)
        if low < self.keys.low_midi or high > self.keys.high_midi:
            self.keys.set_range(max(0, (low // 12) * 12), max(2, (high - low) // 12 + 2))
        self.keys.set_selected(notes)
        self.name.setPlaceholderText(
            slugify(f"chord_{chord_name(root_midi, self.quality.currentText())}")
        )

    def keys_clear(self) -> None:
        self.keys.clear()

    def _selection_changed(self) -> None:
        self.notes_label.setText(describe(self.keys.selected))
        self._changed()

    def _changed(self) -> None:
        if self._syncing:
            return
        # Drop the cached render straight away. The debounce only delays the
        # redraw; leaving the old buffer in place let Bake write the previous
        # chord while the metadata described the new one.
        self._buffer = None
        self._refresh_timer.start()

    # --------------------------------------------------------------- render

    def _render(self):
        notes = self.keys.selected
        if not notes:
            self._buffer = None
            self.wave.set_buffer(None, "select notes on the keyboard")
            self.info.setText("")
            return None
        buffer, warnings = render_chord(
            self._params(),
            notes,
            sample_rate=self.c.project.settings.sample_rate,
            strum=self.strum.value(),
            tempo=self.c.project.settings.tempo,
            duration=self.duration.value(),
        )
        self._buffer = buffer
        self.wave.set_buffer(buffer)
        self.info.setText(
            f"{len(notes)} note(s) · {buffer.duration:.3f}s · peak {buffer.peak():.3f}"
            + ("  ⚠ " + warnings[0] if warnings else "")
        )
        return buffer

    def buffer(self):
        """The current render, produced now if the debounce has not fired."""
        if self._buffer is None:
            self._refresh_timer.stop()
            return self._render()
        return self._buffer

    # -------------------------------------------------------------- preview

    def _set_playing(self, on: bool) -> None:
        self._syncing = True
        self.play_btn.setChecked(on)
        self.play_btn.setText("■  Stop" if on else "▶  Loop")
        self._syncing = False

    def _external_playback(self, origin: str, _duration: float) -> None:
        if origin != "chord_lab":
            self._set_playing(False)

    def _toggle_play(self, on: bool) -> None:
        if self._syncing:
            return
        if not on:
            self.c.stop()
            return
        buffer = self.buffer()
        if buffer is None:
            self._set_playing(False)
            return
        self._set_playing(True)
        self.c.play(buffer, loop=True, origin="chord_lab")

    # ---------------------------------------------------------------- sheet

    def export_sheet(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        notes = self.keys.selected
        if not notes:
            self.c.status("Select some notes first")
            return
        title = self.name.text().strip() or self.name.placeholderText() or "chord"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sheet Music", f"{slugify(title)}.musicxml",
            "MusicXML (*.musicxml);;XML (*.xml)",
        )
        if not path:
            return
        result = chord_to_musicxml(notes, title.replace("_", " ").title(), self.c.project)
        written = write_musicxml(result, path)
        self.c.status(f"Wrote {written.name}: {result.note_count} notes", 8000)

    # ----------------------------------------------------------------- bake

    def bake(self) -> None:
        buffer = self.buffer()
        if buffer is None:
            self.c.status("Select some notes first")
            return
        name = self.name.text().strip() or self.name.placeholderText()
        meta = {
            "generator": "chord_lab",
            "notes": self.keys.selected,
            "chord": self.quality.currentText(),
            "root": self.root.currentText(),
            "octave": self.octave.value(),
            "inversion": self.inversion.value(),
            "voice": self.source.currentData(),
            "strum": self.strum.value(),
            "duration": self.duration.value(),
        }
        try:
            asset = bake_buffer(
                self.c.project, buffer, name, meta=meta, tags=["chord"],
                description=f"{chord_name((self.octave.value() + 1) * 12 + self.root.currentIndex(), self.quality.currentText())} — {describe(self.keys.selected)}",
            )
        except BakeError as exc:
            QMessageBox.information(self, "Save the project first", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Bake failed", f"{type(exc).__name__}: {exc}")
            return

        self.c.push(AddAsset(asset))
        self.c.assetsChanged.emit()
        self.c.select(asset.uid)
        self.bake_note.setText(
            f"Wrote {asset.source_path} — now editable in Sample Lab and Sample FX."
        )
        self.c.status(f"Baked {asset.name} ({asset.duration:.3f}s)")
