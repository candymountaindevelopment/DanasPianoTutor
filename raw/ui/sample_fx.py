"""Sample FX — reverse, fades, gain, normalize and EQ for imported samples.

Everything here is stored as data on the asset and applied at render time, so
the source file is never modified, every change is undoable, and the settings
travel with the project. Bypass exists to A/B against the raw sample.

The EQ reuses the same filter stack the synthesizer uses, so a new filter type
appears here automatically.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.assets import AudioAsset
from ..core.commands import AddAsset, Composite, SetAssetField
from ..core.ids import slugify
from ..core.renderer import bar_seconds, bars_for, implied_tempo
from ..synth.filters import FILTER_PARAMS, FilterNode
from ..synth.trajectory import Trajectory
from .synth_dock import FilterDialog
from .theme import COLORS
from .waveform import WaveformView

FX_FIELDS = ("reverse", "fade_in", "fade_out", "gain_db", "normalize", "filters")

# Bar counts the autofit buttons step through. Fractions below one bar so
# short one-shots can still be fitted sensibly.
BAR_LADDER = [0.125, 0.25, 0.5] + [float(n) for n in range(1, 65)]

PRESETS = {
    "Telephone": [FilterNode("bandpass", {"center": 1400.0, "width": 1.2}, True, "p1")],
    "Radio": [FilterNode("bandpass", {"center": 1800.0, "width": 2.0}, True, "p1"),
              FilterNode("bitcrush", {"bits": 7.0}, True, "p2")],
    "Warm": [FilterNode("low_shelf", {"corner": 220.0, "gain_db": 5.0}, True, "p1"),
             FilterNode("lowpass", {"cutoff": 6500.0, "resonance": 0.7}, True, "p2")],
    "Bright": [FilterNode("high_shelf", {"corner": 3500.0, "gain_db": 6.0}, True, "p1")],
    "Underwater": [FilterNode("lowpass", {"cutoff": 700.0, "resonance": 2.5}, True, "p1")],
    "Crushed": [FilterNode("bitcrush", {"bits": 5.0}, True, "p1"),
                FilterNode("sr_reduce", {"target_rate": 8000.0}, True, "p2")],
    "Slapback": [FilterNode("delay", {"time": 0.11, "feedback": 0.15, "mix": 0.4}, True, "p1")],
    "Cavern": [FilterNode("reverb", {"size": 3.0, "damping": 0.35, "mix": 0.45,
                                     "predelay": 0.04}, True, "p1")],
    "Small Room": [FilterNode("reverb", {"size": 0.5, "damping": 0.6, "mix": 0.25,
                                         "predelay": 0.008}, True, "p1")],
    "Clean Up": [FilterNode("denoise", {"amount": 0.7, "floor": 0.06}, True, "p1"),
                 FilterNode("highpass", {"cutoff": 60.0, "resonance": 0.7}, True, "p2")],
    "Sustained": [FilterNode("sustain", {"amount": 0.7, "attack": 0.01,
                                         "release": 0.4}, True, "p1")],
    "Dub Echo": [FilterNode("delay", {"time": 0.375, "feedback": 0.55, "mix": 0.5}, True, "p1"),
                 FilterNode("lowpass", {"cutoff": 2200.0, "resonance": 0.9}, True, "p2")],
}


def _spin(lo, hi, step, decimals, suffix="") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class SampleFx(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.asset: AudioAsset | None = None
        self._loading = False
        self._bypass = False
        # Working copy of the filter chain. The asset is only ever changed
        # through a command, so undo has a real previous value to restore.
        self._filters: list[FilterNode] = []

        self._commit = QTimer(self)
        self._commit.setSingleShot(True)
        self._commit.setInterval(350)
        self._commit.timeout.connect(self._push)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self.header = QLabel("Select an imported sample to process it")
        self.header.setStyleSheet(f"color: {COLORS['text_dim']}; font-weight: 600;")
        outer.addWidget(self.header)

        self.wave = WaveformView()
        self.wave.setMinimumHeight(90)
        self.wave.setMaximumHeight(150)
        outer.addWidget(self.wave)

        body = QHBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body, 1)

        # --- shape -----------------------------------------------------
        shape = QGroupBox("Shape")
        form = QFormLayout(shape)
        self.reverse = QCheckBox("Play backwards")
        self.reverse.stateChanged.connect(self._changed)
        self.fade_in = _spin(0.0, 30.0, 0.01, 3, " s")
        self.fade_in.valueChanged.connect(self._changed)
        self.fade_out = _spin(0.0, 30.0, 0.01, 3, " s")
        self.fade_out.valueChanged.connect(self._changed)
        form.addRow(self.reverse)
        form.addRow("Fade in", self.fade_in)
        form.addRow("Fade out", self.fade_out)
        body.addWidget(shape, 1)

        # --- level -----------------------------------------------------
        level = QGroupBox("Level")
        lform = QFormLayout(level)
        self.gain = _spin(-48.0, 24.0, 0.5, 2, " dB")
        self.gain.valueChanged.connect(self._changed)
        self.normalize = QCheckBox("Normalize to")
        self.normalize.stateChanged.connect(self._changed)
        self.norm_target = _spin(0.05, 1.0, 0.01, 2)
        self.norm_target.setValue(0.98)
        self.norm_target.valueChanged.connect(self._changed)
        norm_row = QHBoxLayout()
        norm_row.setContentsMargins(0, 0, 0, 0)
        norm_row.addWidget(self.normalize)
        norm_row.addWidget(self.norm_target)
        norm_wrap = QWidget()
        norm_wrap.setLayout(norm_row)
        self.level_info = QLabel("")
        self.level_info.setStyleSheet(f"color: {COLORS['text_dim']};")
        lform.addRow("Gain", self.gain)
        lform.addRow(norm_wrap)
        lform.addRow(self.level_info)
        body.addWidget(level, 1)

        # --- eq --------------------------------------------------------
        eq = QGroupBox("EQ / Character")
        eq_v = QVBoxLayout(eq)
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        self.preset = QComboBox()
        self.preset.addItem("Preset…", "")
        for name in PRESETS:
            self.preset.addItem(name, name)
        self.preset.currentIndexChanged.connect(self._apply_preset)
        preset_row.addWidget(self.preset)
        eq_v.addLayout(preset_row)

        self.filters = QListWidget()
        self.filters.itemDoubleClicked.connect(self._edit_filter)
        eq_v.addWidget(self.filters, 1)

        frow = QHBoxLayout()
        for label, slot, width in (
            ("Add", self._add_filter, None),
            ("Edit", lambda: self._edit_filter(self.filters.currentItem()), None),
            ("Remove", self._remove_filter, None),
            ("↑", lambda: self._move_filter(-1), 30),
            ("↓", lambda: self._move_filter(1), 30),
        ):
            b = QPushButton(label)
            if width:
                b.setMaximumWidth(width)
            b.clicked.connect(slot)
            frow.addWidget(b)
        eq_v.addLayout(frow)
        body.addWidget(eq, 2)

        # --- timing ----------------------------------------------------
        timing = QGroupBox("Timing")
        tform = QFormLayout(timing)
        self.method = QComboBox()
        self.method.addItems(["tape", "wsola", "loop_body"])
        self.method.setToolTip(
            "tape: resample, like changing a tape's speed — pitch moves with length.\n"
            "wsola: keeps the pitch, smears transients a little.\n"
            "loop_body: loops the body region, for steady sounds."
        )
        self.method.currentTextChanged.connect(self._changed)
        self.fit_enabled = QCheckBox("Fit to")
        self.fit_enabled.stateChanged.connect(self._changed)
        self.fit_bars = _spin(0.125, 64.0, 0.5, 3, " bars")
        self.fit_bars.setValue(1.0)
        self.fit_bars.valueChanged.connect(self._changed)
        fit_row = QHBoxLayout()
        fit_row.setContentsMargins(0, 0, 0, 0)
        fit_row.addWidget(self.fit_enabled)
        fit_row.addWidget(self.fit_bars)
        fit_wrap = QWidget()
        fit_wrap.setLayout(fit_row)
        self.fit_down = QPushButton("◄ Shorter")
        self.fit_down.setToolTip(
            "Autofit to the next bar count DOWN — compresses the sample.\n"
            "1.11 bars becomes 1 bar; an exact 2 bars becomes 1."
        )
        self.fit_down.clicked.connect(lambda: self._fit_step(-1))
        self.fit_nearest = QPushButton("Nearest")
        self.fit_nearest.setToolTip("Pick the bar count closest to the natural length")
        self.fit_nearest.clicked.connect(self._fit_nearest)
        self.fit_up = QPushButton("Longer ►")
        self.fit_up.setToolTip(
            "Autofit to the next bar count UP — stretches the sample.\n"
            "1.11 bars becomes 2 bars; an exact 2 bars becomes 3."
        )
        self.fit_up.clicked.connect(lambda: self._fit_step(1))
        autofit_row = QHBoxLayout()
        autofit_row.setContentsMargins(0, 0, 0, 0)
        for b in (self.fit_down, self.fit_nearest, self.fit_up):
            autofit_row.addWidget(b)
        autofit_wrap = QWidget()
        autofit_wrap.setLayout(autofit_row)
        self.timing_info = QLabel("")
        self.timing_info.setWordWrap(True)
        self.timing_info.setStyleSheet(f"color: {COLORS['text_dim']};")
        tform.addRow("Stretch", self.method)
        tform.addRow(fit_wrap)
        tform.addRow("Autofit", autofit_wrap)
        tform.addRow(self.timing_info)
        body.addWidget(timing, 1)

        # --- actions ---------------------------------------------------
        actions = QHBoxLayout()
        self.play_btn = QPushButton("▶  Preview")
        self.play_btn.setObjectName("primary")
        self.play_btn.clicked.connect(self._preview)
        self.bypass_btn = QPushButton("Bypass (A/B)")
        self.bypass_btn.setCheckable(True)
        self.bypass_btn.toggled.connect(self._toggle_bypass)
        self.reset_btn = QPushButton("Reset FX")
        self.reset_btn.clicked.connect(self._reset)
        self.store_btn = QPushButton("Store as New Asset")
        self.store_btn.clicked.connect(self._store_copy)
        for b in (self.play_btn, self.bypass_btn, self.reset_btn, self.store_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        self.note = QLabel("Processing is non-destructive — the source file is never modified.")
        self.note.setStyleSheet(f"color: {COLORS['text_dim']};")
        actions.addWidget(self.note)
        outer.addLayout(actions)

        self.c.selectionChanged.connect(self._load)
        self.c.projectChanged.connect(lambda: self._load(""))
        self.setEnabled(False)

    # ------------------------------------------------------------------ load

    def _load(self, uid: str) -> None:
        asset = self.c.project.assets.get(uid) if uid else None
        if not isinstance(asset, AudioAsset):
            self.asset = None
            self.setEnabled(False)
            self.header.setText("Select an imported sample to process it")
            self.wave.set_buffer(None, "no sample selected")
            return

        self._loading = True
        self.asset = asset
        self.setEnabled(True)
        self.header.setText(f"{asset.name} — {asset.duration:.3f}s")
        self.reverse.setChecked(bool(asset.reverse))
        self.fade_in.setValue(float(asset.fade_in))
        self.fade_out.setValue(float(asset.fade_out))
        self.gain.setValue(float(asset.gain_db))
        self.normalize.setChecked(asset.normalize is not None)
        if asset.normalize is not None:
            self.norm_target.setValue(float(asset.normalize))
        self.norm_target.setEnabled(asset.normalize is not None)
        self.method.setCurrentText(asset.stretch_method or "tape")
        self.fit_enabled.setChecked(asset.fit_bars is not None)
        if asset.fit_bars:
            self.fit_bars.setValue(float(asset.fit_bars))
        self.fit_bars.setEnabled(asset.fit_bars is not None)
        self._filters = [
            FilterNode(n.type, dict(n.params), n.enabled, n.id) for n in asset.filters
        ]
        self._refresh_filters()
        self._loading = False
        self._refresh_wave()

    def _refresh_filters(self) -> None:
        self.filters.clear()
        for node in self._filters:
            bits = []
            for key in FILTER_PARAMS.get(node.type, {}):
                value = node.param(key)
                bits.append(
                    f"{key}={'curve' if isinstance(value, Trajectory) else round(float(value), 2)}"
                )
            self.filters.addItem(f"{node.type}  ·  {'  '.join(bits)}")

    # --------------------------------------------------------------- editing

    def _changed(self) -> None:
        if self._loading or self.asset is None:
            return
        self.norm_target.setEnabled(self.normalize.isChecked())
        self.fit_bars.setEnabled(self.fit_enabled.isChecked())
        self._refresh_wave()
        self._commit.start()

    def _values(self) -> dict:
        return {
            "reverse": self.reverse.isChecked(),
            "fade_in": self.fade_in.value(),
            "fade_out": self.fade_out.value(),
            "gain_db": self.gain.value(),
            "normalize": self.norm_target.value() if self.normalize.isChecked() else None,
            "filters": list(self._filters),
            "stretch_method": self.method.currentText(),
            "fit_bars": self.fit_bars.value() if self.fit_enabled.isChecked() else None,
        }

    def _natural_length(self) -> float:
        """Length after trim and FX but before any bar fitting."""
        staged = self._staged()
        staged.fit_bars = None
        return self.c.renderer.render_asset(
            staged, self.c.project, use_cache=False
        ).duration

    def _current_bars(self) -> float:
        """What the sample occupies now — the fit if one is set, else natural."""
        if self.fit_enabled.isChecked():
            return float(self.fit_bars.value())
        return bars_for(self._natural_length(), self.c.project)

    def _fit_nearest(self) -> None:
        if self.asset is None:
            return
        bars = bars_for(self._natural_length(), self.c.project)
        self._set_fit(min(BAR_LADDER, key=lambda rung: abs(rung - bars)))

    def _fit_step(self, direction: int) -> None:
        """Autofit one rung down or up the bar ladder.

        Comparing strictly against the current value means a sample already
        sitting on an exact bar count still moves — otherwise the button would
        appear dead on precisely the loops people most want to nudge.
        """
        if self.asset is None:
            return
        base = self._current_bars()
        eps = 1e-6
        if direction < 0:
            candidates = [rung for rung in BAR_LADDER if rung < base - eps]
            target = candidates[-1] if candidates else BAR_LADDER[0]
        else:
            candidates = [rung for rung in BAR_LADDER if rung > base + eps]
            target = candidates[0] if candidates else BAR_LADDER[-1]
        self._set_fit(target)

    def _set_fit(self, bars: float) -> None:
        self._loading = True
        self.fit_bars.setValue(bars)
        self.fit_enabled.setChecked(True)
        self.fit_bars.setEnabled(True)
        self._loading = False
        self._changed()
        one_bar = bar_seconds(self.c.project)
        self.c.status(f"Fitted to {bars:g} bar(s) = {bars * one_bar:.4f}s")

    def _refresh_timing(self) -> None:
        if self.asset is None:
            return
        project = self.c.project
        natural = self._natural_length()
        bars = bars_for(natural, project)
        one_bar = bar_seconds(project)
        lines = [
            f"bar = {one_bar:.4f}s at {project.settings.tempo:g} BPM "
            f"{project.settings.time_signature[0]}/{project.settings.time_signature[1]}",
            f"natural {natural:.4f}s = {bars:.3f} bars "
            f"(would be {implied_tempo(natural, round(bars) or 1, project):.2f} BPM "
            f"at {round(bars) or 1} bar(s))",
        ]
        if self.fit_enabled.isChecked():
            target = self.fit_bars.value() * one_bar
            ratio = target / max(natural, 1e-9)
            note = ("pitch moves " f"{-12 * math.log2(ratio):+.2f} st"
                    if self.method.currentText() == "tape" else "pitch preserved")
            lines.append(f"→ {target:.4f}s, ×{ratio:.4f} ({note})")
        self.timing_info.setText("\n".join(lines))

    def _staged(self, values: dict | None = None):
        """A throwaway asset carrying the current settings.

        Previews render from this rather than from the real asset. Writing the
        values onto the asset first would let SetAssetField snapshot the new
        value as the old one, and undo would restore nothing.
        """
        from copy import copy

        staged = copy(self.asset)
        for key, value in (values if values is not None else self._values()).items():
            setattr(staged, key, list(value) if key == "filters" else value)
        return staged

    def _push(self) -> None:
        if self.asset is None:
            return
        values = self._values()
        uid = self.asset.uid
        if all(getattr(self.asset, k) == v for k, v in values.items()):
            return
        commands = [SetAssetField(uid, k, v, "Sample FX") for k, v in values.items()]
        self.c.modify_asset(uid, Composite(commands, "Sample FX"))

    def _refresh_wave(self) -> None:
        if self.asset is None:
            return
        try:
            target = self._dry_asset() if self._bypass else self._staged()
            buf = self.c.renderer.render_asset(target, self.c.project, use_cache=False)
        except Exception as exc:
            self.wave.set_buffer(None, f"render error: {exc}")
            return
        self.wave.set_buffer(buf)
        self.level_info.setText(f"peak {buf.peak():.3f}   rms {buf.rms():.4f}")
        self._refresh_timing()

    def _dry_asset(self):
        """The sample with trim applied but no FX, for A/B."""
        return self._staged({
            "reverse": False, "fade_in": 0.0, "fade_out": 0.0,
            "gain_db": 0.0, "normalize": None, "filters": [],
        })

    def _dry(self):
        return self.c.renderer.render_asset(self._dry_asset(), self.c.project, use_cache=False)

    def _toggle_bypass(self, on: bool) -> None:
        self._bypass = on
        self.bypass_btn.setText("Bypassed — hearing raw" if on else "Bypass (A/B)")
        self._refresh_wave()

    def _preview(self) -> None:
        if self.asset is None:
            return
        self._commit.stop()
        self._push()
        buf = self._dry() if self._bypass else self.c.render(self.asset, preview=False)
        if buf is not None:
            self.c.play(buf)

    def _reset(self) -> None:
        if self.asset is None:
            return
        self._loading = True
        self.reverse.setChecked(False)
        self.fade_in.setValue(0.0)
        self.fade_out.setValue(0.0)
        self.gain.setValue(0.0)
        self.normalize.setChecked(False)
        self._filters = []
        self._refresh_filters()
        self._loading = False
        self._changed()

    def _store_copy(self) -> None:
        if self.asset is None:
            return
        self._commit.stop()
        self._push()
        name, ok = QInputDialog.getText(
            self, "Store as New Asset", "Name:", text=f"{self.asset.name}_fx"
        )
        if not ok or not name.strip():
            return
        payload = self.asset.to_dict()
        payload.pop("uid", None)
        copy_asset = AudioAsset.from_dict(payload)
        copy_asset.name = slugify(name)
        self.c.push(AddAsset(copy_asset))
        self.c.assetsChanged.emit()
        self.c.status(f"Stored {copy_asset.name}")

    # --------------------------------------------------------------- filters

    def _apply_preset(self, index: int) -> None:
        if self._loading or self.asset is None:
            return
        name = self.preset.currentData()
        if not name:
            return
        self._filters = [
            FilterNode(n.type, dict(n.params), n.enabled, n.id) for n in PRESETS[name]
        ]
        self.preset.setCurrentIndex(0)
        self._refresh_filters()
        self._changed()

    def _add_filter(self) -> None:
        if self.asset is None:
            return
        node = FilterNode("emphasis", {}, True, f"f{len(self._filters) + 1}")
        dlg = FilterDialog(node, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._filters.append(dlg.result_node())
            self._refresh_filters()
            self._changed()

    def _edit_filter(self, item) -> None:
        if self.asset is None or item is None:
            return
        row = self.filters.row(item)
        if not (0 <= row < len(self._filters)):
            return
        dlg = FilterDialog(self._filters[row], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._filters[row] = dlg.result_node()
            self._refresh_filters()
            self._changed()

    def _remove_filter(self) -> None:
        row = self.filters.currentRow()
        if self.asset is None or not (0 <= row < len(self._filters)):
            return
        self._filters.pop(row)
        self._refresh_filters()
        self._changed()

    def _move_filter(self, delta: int) -> None:
        if self.asset is None:
            return
        row = self.filters.currentRow()
        new = row + delta
        if not (0 <= row < len(self._filters)) or not (0 <= new < len(self._filters)):
            return
        self._filters[row], self._filters[new] = self._filters[new], self._filters[row]
        self._refresh_filters()
        self.filters.setCurrentRow(new)
        self._changed()
