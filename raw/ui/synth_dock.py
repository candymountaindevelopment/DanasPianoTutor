"""Synthesizer dock — edits the recipe, never the waveform."""

from __future__ import annotations

import random

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.assets import InstrumentAsset, SynthAsset
from ..core.commands import SetAssetField
from ..synth.filters import FILTER_PARAMS, FILTER_TYPES, FilterNode, param_range
from ..synth.oscillator import oscillator_names
from ..synth.trajectory import Trajectory
from .theme import COLORS
from .waveform import WaveformView

DUTY_OSCILLATORS = {"square", "pulse_pair"}


def _dspin(lo, hi, step, decimals, value, suffix="") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class FilterDialog(QDialog):
    """Generic editor built from FILTER_PARAMS, so new filter types need no UI work."""

    def __init__(self, node: FilterNode, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Filter — {node.type}")
        self.node = node
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.type_combo = QComboBox()
        self.type_combo.addItems(list(FILTER_TYPES))
        self.type_combo.setCurrentText(node.type)
        self.type_combo.currentTextChanged.connect(self._retype)
        form.addRow("Type", self.type_combo)
        layout.addLayout(form)

        self.param_form = QFormLayout()
        layout.addLayout(self.param_form)
        self.spins: dict[str, QDoubleSpinBox] = {}
        self._build_params()

        note = QLabel(
            "Filters run after synthesis, so they cannot change the pitch curve."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {COLORS['text_dim']};")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_params(self) -> None:
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self.spins.clear()
        kind = self.type_combo.currentText()
        for name, default in FILTER_PARAMS.get(kind, {}).items():
            current = self.node.param(name)
            if isinstance(current, Trajectory):
                label = QLabel(f"trajectory ({len(current.points)} points) — edit via console")
                label.setStyleSheet(f"color: {COLORS['warn']};")
                self.param_form.addRow(name, label)
                continue
            lo, hi = param_range(kind, name)
            span = hi - lo
            step = 0.01 if span <= 2.0 else (0.1 if span <= 50.0 else 10.0)
            decimals = 3 if span <= 2.0 else 2
            spin = _dspin(lo, hi, step, decimals,
                          float(current if isinstance(current, (int, float)) else default))
            self.param_form.addRow(name, spin)
            self.spins[name] = spin

    def _retype(self, kind: str) -> None:
        self.node.type = kind
        self.node.params = {}
        self._build_params()

    def result_node(self) -> FilterNode:
        self.node.type = self.type_combo.currentText()
        for name, spin in self.spins.items():
            self.node.params[name] = spin.value()
        return self.node


class SynthDock(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self._params = None
        self._uid = ""
        self._loading = False

        self._commit = QTimer(self)
        self._commit.setSingleShot(True)
        self._commit.setInterval(400)
        self._commit.timeout.connect(self._push)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.wave = WaveformView()
        self.wave.setMinimumHeight(78)
        self.wave.setMaximumHeight(110)
        outer.addWidget(self.wave)

        self.scroll = scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        v = QVBoxLayout(body)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        self.header = QLabel("No sound selected")
        self.header.setStyleSheet(f"color: {COLORS['text_dim']}; font-weight: 600;")
        v.addWidget(self.header)

        # --- oscillator ---------------------------------------------------
        osc_box = QGroupBox("Oscillator")
        osc_form = QFormLayout(osc_box)
        self.osc = QComboBox()
        self.osc.addItems(oscillator_names())
        self.osc.currentTextChanged.connect(self._changed)
        self.duty = _dspin(0.01, 0.99, 0.025, 3, 0.5)
        self.duty.valueChanged.connect(self._changed)
        self.duration = _dspin(0.005, 60.0, 0.01, 3, 0.25, " s")
        self.duration.valueChanged.connect(self._changed)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        self.seed.setKeyboardTracking(False)
        self.seed.valueChanged.connect(self._changed)
        seed_row = QHBoxLayout()
        seed_row.addWidget(self.seed, 1)
        dice = QPushButton("Randomize")
        dice.clicked.connect(lambda: self.seed.setValue(random.randint(0, 2**31 - 1)))
        seed_row.addWidget(dice)
        seed_wrap = QWidget()
        seed_wrap.setLayout(seed_row)
        seed_row.setContentsMargins(0, 0, 0, 0)
        osc_form.addRow("Waveform", self.osc)
        osc_form.addRow("Duty cycle", self.duty)
        osc_form.addRow("Duration", self.duration)
        osc_form.addRow("Seed", seed_wrap)
        v.addWidget(osc_box)

        # --- pitch ---------------------------------------------------------
        pitch_box = QGroupBox("Pitch Trajectory")
        pitch_form = QFormLayout(pitch_box)
        self.pitch_start = _dspin(1.0, 20000.0, 10.0, 1, 880.0, " Hz")
        self.pitch_start.valueChanged.connect(self._changed)
        self.pitch_end = _dspin(1.0, 20000.0, 10.0, 1, 220.0, " Hz")
        self.pitch_end.valueChanged.connect(self._changed)
        self.pitch_curve = QComboBox()
        self.pitch_curve.addItems(["exponential", "linear", "step"])
        self.pitch_curve.currentTextChanged.connect(self._changed)
        self.pitch_note = QLabel("")
        self.pitch_note.setStyleSheet(f"color: {COLORS['warn']};")
        self.pitch_note.setWordWrap(True)
        pitch_form.addRow("Start", self.pitch_start)
        pitch_form.addRow("End", self.pitch_end)
        pitch_form.addRow("Curve", self.pitch_curve)
        pitch_form.addRow(self.pitch_note)
        v.addWidget(pitch_box)

        # --- envelope ------------------------------------------------------
        env_box = QGroupBox("Envelope")
        env_form = QFormLayout(env_box)
        self.env_a = _dspin(0.0, 5.0, 0.005, 4, 0.005, " s")
        self.env_d = _dspin(0.0, 5.0, 0.005, 4, 0.04, " s")
        self.env_s = _dspin(0.0, 1.0, 0.05, 3, 0.6)
        self.env_r = _dspin(0.0, 5.0, 0.005, 4, 0.08, " s")
        for w, label in ((self.env_a, "Attack"), (self.env_d, "Decay"),
                         (self.env_s, "Sustain"), (self.env_r, "Release")):
            w.valueChanged.connect(self._changed)
            env_form.addRow(label, w)
        v.addWidget(env_box)

        # --- character -----------------------------------------------------
        char_box = QGroupBox("Character")
        char_form = QFormLayout(char_box)
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(50)
        self.brightness.valueChanged.connect(self._changed)
        self.brightness_label = QLabel("0.50")
        bright_row = QHBoxLayout()
        bright_row.setContentsMargins(0, 0, 0, 0)
        bright_row.addWidget(self.brightness, 1)
        bright_row.addWidget(self.brightness_label)
        bright_wrap = QWidget()
        bright_wrap.setLayout(bright_row)
        self.amplitude = _dspin(0.0, 1.5, 0.05, 3, 0.8)
        self.amplitude.valueChanged.connect(self._changed)
        self.pan = _dspin(-1.0, 1.0, 0.1, 2, 0.0)
        self.pan.valueChanged.connect(self._changed)
        char_form.addRow("Brightness", bright_wrap)
        char_form.addRow("Amplitude", self.amplitude)
        char_form.addRow("Pan", self.pan)
        v.addWidget(char_box)

        # --- drift ---------------------------------------------------------
        drift_box = QGroupBox("Hardware Drift")
        drift_form = QFormLayout(drift_box)
        self.drift = QSlider(Qt.Orientation.Horizontal)
        self.drift.setRange(0, 100)
        self.drift.valueChanged.connect(self._changed)
        self.drift_label = QLabel("0%")
        drift_row = QHBoxLayout()
        drift_row.setContentsMargins(0, 0, 0, 0)
        drift_row.addWidget(self.drift, 1)
        drift_row.addWidget(self.drift_label)
        drift_wrap = QWidget()
        drift_wrap.setLayout(drift_row)
        self.drift_hz = _dspin(0.1, 40.0, 0.5, 2, 3.0, " Hz")
        self.drift_hz.valueChanged.connect(self._changed)
        self.drift_domain = QComboBox()
        self.drift_domain.addItems(["absolute", "normalized"])
        self.drift_domain.setToolTip(
            "absolute: the LFO rate stays in Hz when the sound is stretched "
            "(models the hardware).\nnormalized: the rate is cycles per sound, "
            "so it stretches with the note."
        )
        self.drift_domain.currentTextChanged.connect(self._changed)
        drift_form.addRow("Authenticity", drift_wrap)
        drift_form.addRow("LFO rate", self.drift_hz)
        drift_form.addRow("Time domain", self.drift_domain)
        v.addWidget(drift_box)

        # --- filters -------------------------------------------------------
        filt_box = QGroupBox("Filter Stack")
        filt_v = QVBoxLayout(filt_box)
        self.filters = QListWidget()
        self.filters.setMaximumHeight(110)
        self.filters.itemDoubleClicked.connect(self._edit_filter)
        filt_v.addWidget(self.filters)
        frow = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._add_filter)
        rem = QPushButton("Remove")
        rem.clicked.connect(self._remove_filter)
        up = QPushButton("↑")
        up.setMaximumWidth(30)
        up.clicked.connect(lambda: self._move_filter(-1))
        down = QPushButton("↓")
        down.setMaximumWidth(30)
        down.clicked.connect(lambda: self._move_filter(1))
        for b in (add, rem, up, down):
            frow.addWidget(b)
        filt_v.addLayout(frow)
        v.addWidget(filt_box)

        actions = QHBoxLayout()
        self.btn_preview = QPushButton("Preview")
        self.btn_preview.setObjectName("primary")
        self.btn_preview.clicked.connect(self._preview)
        actions.addWidget(self.btn_preview)
        v.addLayout(actions)
        v.addStretch(1)

        self.c.selectionChanged.connect(self._load)
        self.c.projectChanged.connect(lambda: self._load(""))
        self.setEnabled(False)
        self.scroll.setVisible(False)

    # ------------------------------------------------------------------ load

    def _load(self, uid: str) -> None:
        asset = self.c.project.assets.get(uid) if uid else None
        if not isinstance(asset, (SynthAsset, InstrumentAsset)):
            self._uid = ""
            self._params = None
            self.setEnabled(False)
            # Hide the controls rather than leaving the previous asset's values
            # on screen next to something they do not describe.
            self.scroll.setVisible(False)
            what = f"'{asset.name}' is a {asset.type_name}" if asset else "Nothing selected"
            self.header.setText(f"{what} — select a sound or instrument to edit its recipe")
            self.wave.set_buffer(None, "no synth asset selected")
            return

        self._loading = True
        self._uid = uid
        self._params = asset.params.copy()
        p = self._params

        self.setEnabled(True)
        self.scroll.setVisible(True)
        self.header.setText(f"{asset.name}  —  {asset.type_name}")
        self.osc.setCurrentText(p.oscillator)
        self.duty.setValue(p.duty.value_at(0.0))
        self.duty.setEnabled(p.oscillator in DUTY_OSCILLATORS)
        self.duration.setValue(p.duration)
        self.seed.setValue(int(p.seed))

        simple = len(p.pitch.points) <= 2
        self.pitch_start.setEnabled(simple)
        self.pitch_end.setEnabled(simple)
        self.pitch_curve.setEnabled(simple)
        self.pitch_start.setValue(p.pitch.value_at(0.0))
        self.pitch_end.setValue(p.pitch.value_at(1.0))
        self.pitch_curve.setCurrentText(p.pitch.curve)
        self.pitch_note.setText(
            "" if simple else
            f"Multi-point trajectory ({len(p.pitch.points)} points) — preserved; "
            "edit it from the console to keep every point."
        )

        self.env_a.setValue(p.envelope.attack)
        self.env_d.setValue(p.envelope.decay)
        self.env_s.setValue(p.envelope.sustain)
        self.env_r.setValue(p.envelope.release)

        self.brightness.setValue(int(round(p.brightness * 100)))
        self.brightness_label.setText(f"{p.brightness:.2f}")
        self.amplitude.setValue(p.amplitude)
        self.pan.setValue(p.pan)

        self.drift.setValue(int(round(p.drift.authenticity * 100)))
        self.drift_label.setText(f"{int(round(p.drift.authenticity * 100))}%")
        self.drift_hz.setValue(p.drift.pitch_lfo_hz)
        self.drift_domain.setCurrentText(p.drift.time_domain)

        self._refresh_filters()
        self._loading = False
        self._refresh_wave()

    def _refresh_filters(self) -> None:
        self.filters.clear()
        if self._params is None:
            return
        for node in self._params.filters:
            bits = []
            for k in FILTER_PARAMS.get(node.type, {}):
                val = node.param(k)
                bits.append(f"{k}={'curve' if isinstance(val, Trajectory) else round(float(val), 2)}")
            item = QListWidgetItem(f"{node.type}  ·  {'  '.join(bits)}")
            if not node.enabled:
                item.setText("(off) " + item.text())
            self.filters.addItem(item)

    # --------------------------------------------------------------- editing

    def _changed(self) -> None:
        if self._loading or self._params is None:
            return
        p = self._params
        p.oscillator = self.osc.currentText()
        self.duty.setEnabled(p.oscillator in DUTY_OSCILLATORS)
        if p.duty.is_constant:
            p.duty = Trajectory.constant(self.duty.value())
        p.duration = self.duration.value()
        p.seed = int(self.seed.value())
        if len(p.pitch.points) <= 2:
            p.pitch = Trajectory.ramp(
                self.pitch_start.value(), self.pitch_end.value(), self.pitch_curve.currentText()
            )
        p.envelope.attack = self.env_a.value()
        p.envelope.decay = self.env_d.value()
        p.envelope.sustain = self.env_s.value()
        p.envelope.release = self.env_r.value()
        p.brightness = self.brightness.value() / 100.0
        self.brightness_label.setText(f"{p.brightness:.2f}")
        p.amplitude = self.amplitude.value()
        p.pan = self.pan.value()
        p.drift.authenticity = self.drift.value() / 100.0
        self.drift_label.setText(f"{self.drift.value()}%")
        p.drift.pitch_lfo_hz = self.drift_hz.value()
        p.drift.time_domain = self.drift_domain.currentText()

        # A duration change invalidates a stored structure's absolute lengths;
        # the regions are normalised so only nominal_duration needs updating.
        if p.time_structure is not None:
            p.time_structure.nominal_duration = p.duration

        self._refresh_wave()
        self._commit.start()

    def _push(self) -> None:
        if not self._uid or self._params is None:
            return
        self.c.modify_asset(
            self._uid, SetAssetField(self._uid, "params", self._params.copy(), "Edit sound")
        )

    def _refresh_wave(self) -> None:
        if self._params is None:
            return
        from ..synth import engine

        try:
            result = engine.render(
                self._params, self.c.project.settings.sample_rate, preview=True
            )
        except Exception as exc:
            self.wave.set_buffer(None, f"render error: {exc}")
            return
        self.wave.set_buffer(result.buffer)
        if result.time_map is not None:
            ts = self._params.structure()
            bounds = result.time_map.output_bounds
            self.wave.set_regions(
                [(r.role, float(bounds[i]), float(bounds[i + 1])) for i, r in enumerate(ts.regions)]
            )

    def _preview(self) -> None:
        if self._params is None:
            return
        self._commit.stop()
        self._push()
        self.c.preview()

    # --------------------------------------------------------------- filters

    def _add_filter(self) -> None:
        if self._params is None:
            return
        node = FilterNode("emphasis", {}, True, f"f{len(self._params.filters) + 1}")
        dlg = FilterDialog(node, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._params.filters.append(dlg.result_node())
            self._refresh_filters()
            self._refresh_wave()
            self._push()

    def _edit_filter(self, item) -> None:
        if self._params is None:
            return
        row = self.filters.row(item)
        if not (0 <= row < len(self._params.filters)):
            return
        dlg = FilterDialog(self._params.filters[row], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._params.filters[row] = dlg.result_node()
            self._refresh_filters()
            self._refresh_wave()
            self._push()

    def _remove_filter(self) -> None:
        row = self.filters.currentRow()
        if self._params is None or not (0 <= row < len(self._params.filters)):
            return
        self._params.filters.pop(row)
        self._refresh_filters()
        self._refresh_wave()
        self._push()

    def _move_filter(self, delta: int) -> None:
        row = self.filters.currentRow()
        if self._params is None:
            return
        new = row + delta
        if not (0 <= row < len(self._params.filters)) or not (0 <= new < len(self._params.filters)):
            return
        f = self._params.filters
        f[row], f[new] = f[new], f[row]
        self._refresh_filters()
        self.filters.setCurrentRow(new)
        self._refresh_wave()
        self._push()
