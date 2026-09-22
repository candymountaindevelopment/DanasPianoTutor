"""Time Structure editor (spec section 103.5).

The point of this panel is the status line: it tells you whether the stretch
you are asking for is exact (a recipe re-evaluated) or approximate (a sample
processed). That distinction is the product's argument for recipes.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.assets import AudioAsset, InstrumentAsset, SynthAsset
from ..core.commands import SetAssetField
from ..core.overrides import musical_to_seconds
from ..synth.timestructure import REGION_MODES, STRETCH_MODES
from .theme import COLORS
from .waveform import WaveformView

MUSICAL = ["—", "1/32", "1/16", "1/8", "1/4", "1/2", "1", "2"]

MODE_HELP = {
    "uniform": "Every region scales together. For tones and drones.",
    "preserve_attack": "The onset stays put; everything else stretches.",
    "preserve_impact": "Onset and impact stay put; the body absorbs the change.",
    "custom": "Per-region elasticity as authored below.",
}


class TimeStructureDock(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self._uid = ""
        self._asset = None
        self._loading = False
        self._region_widgets: list[tuple] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.wave = WaveformView()
        self.wave.setMinimumHeight(100)
        outer.addWidget(self.wave, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMaximumHeight(330)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        v = QVBoxLayout(body)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        self.header = QLabel("No asset selected")
        self.header.setStyleSheet(f"color: {COLORS['text_dim']}; font-weight: 600;")
        v.addWidget(self.header)

        controls = QGroupBox("Stretch")
        form = QFormLayout(controls)
        self.mode = QComboBox()
        self.mode.addItems(list(STRETCH_MODES))
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.target = QDoubleSpinBox()
        self.target.setRange(0.005, 60.0)
        self.target.setDecimals(3)
        self.target.setSingleStep(0.01)
        self.target.setSuffix(" s")
        self.target.setKeyboardTracking(False)
        self.target.valueChanged.connect(self._refresh)
        self.musical = QComboBox()
        self.musical.addItems(MUSICAL)
        self.musical.currentTextChanged.connect(self._musical_changed)
        reset = QPushButton("1x")
        reset.setMaximumWidth(40)
        reset.clicked.connect(self._reset_target)
        trow = QHBoxLayout()
        trow.setContentsMargins(0, 0, 0, 0)
        trow.addWidget(self.target, 2)
        trow.addWidget(self.musical, 1)
        trow.addWidget(reset)
        twrap = QWidget()
        twrap.setLayout(trow)
        form.addRow("Mode", self.mode)
        form.addRow("Length", twrap)
        self.mode_help = QLabel("")
        self.mode_help.setWordWrap(True)
        self.mode_help.setStyleSheet(f"color: {COLORS['text_dim']};")
        form.addRow(self.mode_help)
        v.addWidget(controls)

        self.regions_box = QGroupBox("Regions")
        self.regions_grid = QGridLayout(self.regions_box)
        self.regions_grid.setColumnStretch(1, 1)
        v.addWidget(self.regions_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        actions = QHBoxLayout()
        self.btn_nominal = QPushButton("Preview 1x")
        self.btn_nominal.clicked.connect(lambda: self._preview(nominal=True))
        self.btn_stretched = QPushButton("Preview Stretched")
        self.btn_stretched.setObjectName("primary")
        self.btn_stretched.clicked.connect(lambda: self._preview(nominal=False))
        actions.addWidget(self.btn_nominal)
        actions.addWidget(self.btn_stretched)
        v.addLayout(actions)
        v.addStretch(1)

        self.c.selectionChanged.connect(self._load)
        self.c.assetModified.connect(self._external_change)
        self.c.projectChanged.connect(lambda: self._load(""))
        self.setEnabled(False)

    # ------------------------------------------------------------------ load

    def _load(self, uid: str) -> None:
        asset = self.c.project.assets.get(uid) if uid else None
        if not isinstance(asset, (SynthAsset, InstrumentAsset, AudioAsset)):
            self._uid = ""
            self._asset = None
            self.setEnabled(False)
            self.header.setText("Select a sound to edit its time structure")
            self.wave.set_buffer(None, "no asset selected")
            self._clear_regions()
            self.status.setText("")
            return

        self._loading = True
        self._uid = uid
        self._asset = asset
        self.setEnabled(True)

        nominal = self._nominal_duration()
        self.header.setText(f"{asset.name}  —  nominal {nominal:.3f}s")
        if isinstance(asset, AudioAsset):
            self.mode.setCurrentText("preserve_impact")
            self.mode.setEnabled(False)
        else:
            self.mode.setEnabled(True)
            self.mode.setCurrentText(asset.params.stretch_mode)
        self.target.setValue(nominal)
        self.musical.setCurrentText("—")
        self._loading = False
        self._rebuild_regions()
        self._refresh()

    def _nominal_duration(self) -> float:
        a = self._asset
        if isinstance(a, AudioAsset):
            return max(a.duration, 0.01)
        return a.params.duration

    def _structure(self):
        a = self._asset
        if isinstance(a, AudioAsset):
            from ..synth.timestructure import TimeStructure

            if a.time_structure is None:
                a.time_structure = TimeStructure.single_body(max(a.duration, 0.01))
            a.time_structure.nominal_duration = max(a.duration, 0.01)
            return a.time_structure
        return a.params.structure()

    # --------------------------------------------------------------- regions

    def _clear_regions(self) -> None:
        while self.regions_grid.count():
            item = self.regions_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._region_widgets = []

    def _external_change(self, uid: str) -> None:
        """Another dock edited this asset. Refresh without destroying widgets
        the user may currently be dragging."""
        if uid != self._uid or self._asset is None or self._loading:
            return
        ts = self._structure()
        ids = [r.id for r in ts.regions]
        if ids != [rid for rid, *_ in self._region_widgets]:
            self._rebuild_regions()
        else:
            self._loading = True
            by_id = {r.id: r for r in ts.regions}
            for rid, slider, value, mode in self._region_widgets:
                region = by_id[rid]
                slider.setValue(int(round(region.elasticity * 100)))
                value.setText(f"{region.elasticity:.2f}")
                mode.setCurrentText(region.mode)
            self._loading = False
        self._refresh()

    def _rebuild_regions(self) -> None:
        self._clear_regions()
        if self._asset is None:
            return
        ts = self._structure()
        for row, region in enumerate(ts.regions):
            name = QLabel(region.role)
            name.setStyleSheet(f"color: {COLORS['text']};")
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 200)
            slider.setValue(int(round(region.elasticity * 100)))
            slider.setToolTip("Elasticity: 0 keeps this region rigid, 1.0 stretches fully.")
            value = QLabel(f"{region.elasticity:.2f}")
            value.setMinimumWidth(34)
            mode = QComboBox()
            mode.addItems(list(REGION_MODES))
            mode.setCurrentText(region.mode)
            slider.valueChanged.connect(self._region_changed)
            mode.currentTextChanged.connect(self._region_changed)
            self.regions_grid.addWidget(name, row, 0)
            self.regions_grid.addWidget(slider, row, 1)
            self.regions_grid.addWidget(value, row, 2)
            self.regions_grid.addWidget(mode, row, 3)
            self._region_widgets.append((region.id, slider, value, mode))

    def _region_changed(self) -> None:
        if self._loading or self._asset is None:
            return
        ts = self._structure()
        by_id = {r.id: r for r in ts.regions}
        for rid, slider, value, mode in self._region_widgets:
            region = by_id.get(rid)
            if region is None:
                continue
            region.elasticity = slider.value() / 100.0
            region.mode = mode.currentText()
            value.setText(f"{region.elasticity:.2f}")

        if isinstance(self._asset, AudioAsset):
            self.c.modify_asset(
                self._uid, SetAssetField(self._uid, "time_structure", ts, "Edit time structure")
            )
        else:
            params = self._asset.params.copy()
            params.stretch_mode = "custom"
            params.time_structure = ts
            self._loading = True
            self.mode.setCurrentText("custom")
            self._loading = False
            self.c.modify_asset(
                self._uid, SetAssetField(self._uid, "params", params, "Edit time structure")
            )
        self._refresh()

    # --------------------------------------------------------------- actions

    def _mode_changed(self, mode: str) -> None:
        self.mode_help.setText(MODE_HELP.get(mode, ""))
        if self._loading or self._asset is None or isinstance(self._asset, AudioAsset):
            return
        params = self._asset.params.copy()
        params.stretch_mode = mode
        if mode != "custom":
            ts = params.structure(mode)
            params.time_structure = ts
        self.c.modify_asset(self._uid, SetAssetField(self._uid, "params", params, f"Stretch: {mode}"))
        self._rebuild_regions()
        self._refresh()

    def _musical_changed(self, text: str) -> None:
        if self._loading or text == "—":
            return
        self.target.setValue(musical_to_seconds(text, self.c.project.settings.tempo))

    def _reset_target(self) -> None:
        self.musical.setCurrentText("—")
        self.target.setValue(self._nominal_duration())

    def _overrides(self, nominal: bool) -> dict:
        if nominal:
            return {}
        return {"duration": float(self.target.value())}

    def _preview(self, nominal: bool) -> None:
        if self._asset is None:
            return
        self.c.preview(self._asset, self._overrides(nominal))

    # --------------------------------------------------------------- display

    def _refresh(self) -> None:
        if self._asset is None:
            return
        target = float(self.target.value())
        nominal = self._nominal_duration()
        self.header.setText(f"{self._asset.name}  —  nominal {nominal:.3f}s")
        at_nominal = abs(target - nominal) < 1e-9
        buf = self.c.render(self._asset, self._overrides(at_nominal), preview=True)
        if buf is None:
            return
        self.wave.set_buffer(buf)
        self.wave.set_ghost_duration(nominal if target > nominal else None)

        ts = self._structure()
        tmap, warnings = ts.build_map(target)
        bounds = tmap.output_bounds
        self.wave.set_regions(
            [(r.role, float(bounds[i]), float(bounds[i + 1])) for i, r in enumerate(ts.regions)]
        )

        if isinstance(self._asset, AudioAsset):
            head = (
                f"<span style='color:{COLORS['warn']}'>~ Approximate</span> — imported audio is "
                f"processed with '{self._asset.stretch_method}'. Convert to a recipe for exact stretching."
            )
        else:
            head = (
                f"<span style='color:{COLORS['accent']}'>✓ Exact</span> — the recipe is "
                "re-evaluated, so pitch identity is preserved."
            )
        factors = "  ".join(f"{r.role}×{f:.2f}" for r, f in zip(ts.regions, tmap.factors))
        warn = ""
        if warnings:
            warn = f"<br><span style='color:{COLORS['warn']}'>⚠ {'; '.join(warnings)}</span>"
        self.status.setText(f"{head}<br><span style='color:{COLORS['text_dim']}'>{factors}</span>{warn}")
