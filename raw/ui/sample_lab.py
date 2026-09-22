"""Sample Lab — slice an imported sample into named assets (spec section 15).

Each splice bar is a start position and a length, in seconds to the
millisecond, on two synchronised sliders. Store turns the bar into its own
asset.

Storing is **non-destructive**: the new asset points at the same source file
and records its bounds, so no audio is copied, the slice stays editable, and
re-slicing later costs nothing. That is the same principle as keeping a synth
recipe instead of a rendered waveform.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..audio.buffer import AudioBuffer
from ..core.assets import AudioAsset
from ..core.commands import AddAsset, Composite
from ..core.ids import slugify
from .theme import COLORS
from .waveform import WaveformView

# Distinct colours so overlapping bars stay readable on the waveform.
SLICE_COLORS = [
    COLORS["region_body"],
    COLORS["region_tail"],
    COLORS["region_transient"],
    COLORS["region_custom"],
    COLORS["region_attack"],
]
MIN_LENGTH = 0.001


def _seconds_spin(maximum: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setDecimals(3)
    spin.setSingleStep(0.010)
    spin.setSuffix(" s")
    spin.setRange(0.0, max(maximum, MIN_LENGTH))
    spin.setKeyboardTracking(False)
    spin.setMinimumWidth(92)
    return spin


class SpliceBar(QFrame):
    """One slice: start, length, play, store."""

    changed = pyqtSignal()
    playRequested = pyqtSignal(object)
    storeRequested = pyqtSignal(object)
    removeRequested = pyqtSignal(object)
    focused = pyqtSignal(object)

    def __init__(self, index: int, source_duration: float, default_name: str, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.source_duration = max(source_duration, MIN_LENGTH)
        self._syncing = False
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ border: 1px solid {COLORS['border']}; border-radius: 3px; }}"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(6)

        self.swatch = QLabel("■")
        self.swatch.setStyleSheet(f"color: {self.color}; border: none; font-size: 15px;")
        row.addWidget(self.swatch)

        self.name = QLineEdit(default_name)
        self.name.setMinimumWidth(120)
        self.name.setMaximumWidth(190)
        self.name.setToolTip("Name for the stored slice")
        row.addWidget(self.name)

        row.addWidget(self._tag("start"))
        self.start_slider = QSlider(Qt.Orientation.Horizontal)
        self.start_slider.setRange(0, self._ms(self.source_duration))
        self.start_slider.valueChanged.connect(self._start_slider_moved)
        self.start_spin = _seconds_spin(self.source_duration)
        self.start_spin.valueChanged.connect(self._start_spin_changed)
        row.addWidget(self.start_slider, 3)
        row.addWidget(self.start_spin)

        row.addWidget(self._tag("length"))
        self.length_slider = QSlider(Qt.Orientation.Horizontal)
        self.length_slider.setRange(self._ms(MIN_LENGTH), self._ms(self.source_duration))
        self.length_slider.setValue(self._ms(self.source_duration))
        self.length_slider.valueChanged.connect(self._length_slider_moved)
        self.length_spin = _seconds_spin(self.source_duration)
        self.length_spin.setMinimum(MIN_LENGTH)
        self.length_spin.setValue(self.source_duration)
        self.length_spin.valueChanged.connect(self._length_spin_changed)
        row.addWidget(self.length_slider, 3)
        row.addWidget(self.length_spin)

        self.end_label = QLabel()
        self.end_label.setStyleSheet(f"color: {COLORS['text_dim']}; border: none;")
        self.end_label.setMinimumWidth(74)
        row.addWidget(self.end_label)

        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(30)
        self.play_btn.setCheckable(True)
        self.play_btn.setToolTip("Loop this slice — press again to stop")
        self.play_btn.toggled.connect(lambda _=False: self.playRequested.emit(self))
        self.store_btn = QPushButton("Store")
        self.store_btn.setObjectName("primary")
        self.store_btn.setToolTip("Create an asset from this slice")
        self.store_btn.clicked.connect(lambda: self.storeRequested.emit(self))
        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedWidth(26)
        self.remove_btn.setToolTip("Remove this splice bar")
        self.remove_btn.clicked.connect(lambda: self.removeRequested.emit(self))
        for b in (self.play_btn, self.store_btn, self.remove_btn):
            row.addWidget(b)

        self._refresh_end()

    # ------------------------------------------------------------- helpers

    @property
    def color(self) -> str:
        return SLICE_COLORS[self.index % len(SLICE_COLORS)]

    @staticmethod
    def _ms(seconds: float) -> int:
        return int(round(seconds * 1000.0))

    def _tag(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {COLORS['text_dim']}; border: none;")
        return label

    @property
    def start(self) -> float:
        return self.start_spin.value()

    @property
    def length(self) -> float:
        return self.length_spin.value()

    @property
    def end(self) -> float:
        return min(self.start + self.length, self.source_duration)

    def set_bounds(self, start: float, length: float) -> None:
        self._syncing = True
        self.start_spin.setValue(start)
        self.start_slider.setValue(self._ms(start))
        self.length_spin.setValue(length)
        self.length_slider.setValue(self._ms(length))
        self._syncing = False
        self._clamp_length()
        self._refresh_end()

    def set_index(self, index: int) -> None:
        self.index = index
        self.swatch.setStyleSheet(f"color: {self.color}; border: none; font-size: 15px;")

    @property
    def is_playing(self) -> bool:
        return self.play_btn.isChecked()

    def set_playing(self, on: bool) -> None:
        """Set the button state without re-triggering the toggle handler."""
        self.play_btn.blockSignals(True)
        self.play_btn.setChecked(on)
        self.play_btn.setText("■" if on else "▶")
        self.play_btn.blockSignals(False)

    # -------------------------------------------------------------- events

    def _clamp_length(self) -> None:
        """Length can never run past the end of the source."""
        available = max(MIN_LENGTH, self.source_duration - self.start)
        self._syncing = True
        self.length_spin.setMaximum(available)
        self.length_slider.setMaximum(self._ms(available))
        if self.length > available:
            self.length_spin.setValue(available)
            self.length_slider.setValue(self._ms(available))
        self._syncing = False

    def _refresh_end(self) -> None:
        self.end_label.setText(f"→ {self.end:.3f} s")

    def _emit(self) -> None:
        self._clamp_length()
        self._refresh_end()
        self.focused.emit(self)
        self.changed.emit()

    def _start_slider_moved(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.start_spin.setValue(value / 1000.0)
        self._syncing = False
        self._emit()

    def _start_spin_changed(self, value: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.start_slider.setValue(self._ms(value))
        self._syncing = False
        self._emit()

    def _length_slider_moved(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.length_spin.setValue(max(MIN_LENGTH, value / 1000.0))
        self._syncing = False
        self._emit()

    def _length_spin_changed(self, value: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.length_slider.setValue(self._ms(value))
        self._syncing = False
        self._emit()


class SampleLab(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.asset: AudioAsset | None = None
        self.source: AudioBuffer | None = None
        self.bars: list[SpliceBar] = []
        self._loaded_uid = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self.header = QLabel("Select an imported sample to slice it")
        self.header.setStyleSheet(f"color: {COLORS['text_dim']}; font-weight: 600;")
        outer.addWidget(self.header)

        self.wave = WaveformView()
        self.wave.setMinimumHeight(96)
        self.wave.setMaximumHeight(150)
        self.wave.clicked.connect(self._waveform_clicked)
        outer.addWidget(self.wave)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        self.rows = QVBoxLayout(holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(4)
        self.rows.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        bar = QHBoxLayout()
        self.add_btn = QPushButton("+  Add Splice Bar")
        self.add_btn.clicked.connect(lambda: self.add_bar())
        self.split_btn = QPushButton("Split Evenly…")
        self.split_btn.setToolTip("Replace the bars with N equal slices across the sample")
        self.split_btn.clicked.connect(self.split_evenly)
        self.store_all_btn = QPushButton("Store All")
        self.store_all_btn.clicked.connect(self.store_all)
        self.play_source_btn = QPushButton("▶  Loop Whole Sample")
        self.play_source_btn.setCheckable(True)
        self.play_source_btn.setToolTip("Loop the whole sample — press again to stop")
        self.play_source_btn.toggled.connect(lambda _=False: self.play_source())
        for b in (self.add_btn, self.split_btn, self.store_all_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.play_source_btn)
        outer.addLayout(bar)

        self.hint = QLabel(
            "Slices are stored non-destructively — they point at the same file with "
            "their own bounds, so nothing is copied and they stay editable."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {COLORS['text_dim']};")
        outer.addWidget(self.hint)

        self._syncing_play = False
        self.c.selectionChanged.connect(self._load)
        self.c.projectChanged.connect(lambda: self._load(""))
        self.c.playbackStarted.connect(self._on_external_playback)
        self.c.playbackStopped.connect(lambda origin: self._set_playing(None))
        self._set_enabled(False)

    # ----------------------------------------------------------------- load

    def _set_enabled(self, on: bool) -> None:
        for w in (self.add_btn, self.split_btn, self.store_all_btn, self.play_source_btn):
            w.setEnabled(on)

    def _clear_bars(self) -> None:
        if any(bar.is_playing for bar in self.bars):
            self.c.stop()
        for bar in self.bars:
            self.rows.removeWidget(bar)
            # setParent(None) removes it from the layout now; deleteLater alone
            # leaves a zombie row on screen until the event loop catches up.
            bar.setParent(None)
            bar.deleteLater()
        self.bars = []

    def _load(self, uid: str) -> None:
        asset = self.c.project.assets.get(uid) if uid else None
        if not isinstance(asset, AudioAsset):
            self.asset = None
            self.source = None
            self._loaded_uid = ""
            self._clear_bars()
            self._set_enabled(False)
            self.header.setText("Select an imported sample to slice it")
            self.wave.set_buffer(None, "no sample selected")
            return

        # Selection is re-emitted after every undo/redo. Rebuilding the bars
        # then would throw away slice work in progress, so only rebuild when
        # the sample actually changed.
        if uid == getattr(self, "_loaded_uid", "") and self.source is not None:
            return

        self.asset = asset
        self._loaded_uid = uid
        try:
            self.source = self.c.renderer.render_asset(asset, self.c.project)
        except Exception as exc:
            self.source = None
            self._loaded_uid = ""
            self._clear_bars()
            self._set_enabled(False)
            self.header.setText(f"{asset.name} — cannot read source: {exc}")
            self.wave.set_buffer(None, "source unavailable")
            return

        origin = "" if not asset.is_slice else f"  (slice of {asset.source_path})"
        self.header.setText(
            f"{asset.name} — {self.source.duration:.3f}s · {self.source.sample_rate} Hz{origin}"
        )
        self.wave.set_buffer(self.source)
        self._clear_bars()
        self._set_enabled(True)
        self.add_bar(0.0, self.source.duration)

    # ----------------------------------------------------------------- bars

    def add_bar(self, start: float | None = None, length: float | None = None) -> SpliceBar | None:
        if self.source is None:
            return None
        duration = self.source.duration
        if start is None:
            # Start a new bar after the last one, so bars tile by default.
            start = min(max((b.end for b in self.bars), default=0.0), max(0.0, duration - MIN_LENGTH))
        if length is None:
            length = max(MIN_LENGTH, duration - start)

        bar = SpliceBar(len(self.bars), duration, self._default_name(), self)
        bar.set_bounds(start, length)
        bar.changed.connect(self._refresh_regions)
        bar.playRequested.connect(self.play_bar)
        bar.storeRequested.connect(self.store_bar)
        bar.removeRequested.connect(self.remove_bar)
        self.rows.insertWidget(self.rows.count() - 1, bar)
        self.bars.append(bar)
        self._refresh_regions()
        return bar

    def _default_name(self) -> str:
        base = self.asset.name if self.asset else "slice"
        return f"{base}_{len(self.bars) + 1}"

    def remove_bar(self, bar: SpliceBar) -> None:
        if bar not in self.bars:
            return
        self.bars.remove(bar)
        self.rows.removeWidget(bar)
        bar.deleteLater()
        for i, remaining in enumerate(self.bars):
            remaining.set_index(i)
        self._refresh_regions()

    def split_evenly(self, count: int | None = None) -> None:
        if self.source is None:
            return
        if count is None:
            from PyQt6.QtWidgets import QInputDialog

            count, ok = QInputDialog.getInt(self, "Split Evenly", "Number of slices:", 4, 2, 64)
            if not ok:
                return
        self._clear_bars()
        span = self.source.duration / count
        for i in range(count):
            self.add_bar(i * span, span)

    def _refresh_regions(self) -> None:
        self.wave.set_regions(
            [(bar.name.text() or f"{i + 1}", bar.start, bar.end, bar.color)
             for i, bar in enumerate(self.bars)]
        )

    def _waveform_clicked(self, seconds: float) -> None:
        """Clicking the waveform moves the last-touched bar's start."""
        if self.bars:
            self.bars[-1].set_bounds(seconds, self.bars[-1].length)
            self._refresh_regions()

    # -------------------------------------------------------------- actions

    def _set_playing(self, active: SpliceBar | None, source: bool = False) -> None:
        """Exactly one thing loops at a time; reflect that in the buttons."""
        self._syncing_play = True
        for bar in self.bars:
            bar.set_playing(bar is active)
        self.play_source_btn.setChecked(source)
        self.play_source_btn.setText("■  Stop Sample" if source else "▶  Loop Whole Sample")
        self._syncing_play = False

    def _on_external_playback(self, origin: str, *_: object) -> None:
        """Anything else taking over the device clears our loop buttons.

        sounddevice stops the current stream when a new one starts, so a
        preview elsewhere really has ended our loop.
        """
        if origin != "sample_lab":
            self._set_playing(None)

    def play_source(self) -> None:
        if self._syncing_play or self.source is None:
            return
        if not self.play_source_btn.isChecked():
            self.c.stop()
            return
        self._set_playing(None, source=True)
        self.c.play(self.source, loop=True, origin="sample_lab")
        self.c.status(f"Looping {self.asset.name} ({self.source.duration:.3f}s)")

    def play_bar(self, bar: SpliceBar) -> None:
        if self._syncing_play or self.source is None:
            return
        if not bar.is_playing:
            self.c.stop()
            return
        segment = self.source.slice_seconds(bar.start, bar.end)
        if segment.num_frames == 0:
            self.c.status("That slice is empty")
            self._set_playing(None)
            return
        self._set_playing(bar)
        self.c.play(segment, loop=True, origin="sample_lab")
        self.c.status(
            f"Looping {bar.start:.3f}s → {bar.end:.3f}s  ({bar.length:.3f}s) — press again to stop"
        )

    def _slice_asset(self, bar: SpliceBar) -> AudioAsset:
        parent = self.asset
        return AudioAsset(
            name=slugify(bar.name.text() or self._default_name()),
            source_path=parent.source_path,
            sample_rate=parent.sample_rate,
            channels=parent.channels,
            duration=round(bar.length, 6),
            stretch_method=parent.stretch_method,
            # Bounds compose: slicing a slice stays relative to the real file.
            trim_start=round(float(parent.trim_start) + bar.start, 6),
            trim_length=round(bar.length, 6),
            tags=list(parent.tags),
            description=f"slice of {parent.name}",
        )

    def store_bar(self, bar: SpliceBar) -> None:
        if self.asset is None:
            return
        asset = self._slice_asset(bar)
        self.c.push(AddAsset(asset))
        self.c.assetsChanged.emit()
        self.c.status(f"Stored {asset.name} ({asset.duration:.3f}s)")

    def store_all(self) -> None:
        if self.asset is None or not self.bars:
            return
        assets = [self._slice_asset(bar) for bar in self.bars]
        self.c.push(Composite([AddAsset(a) for a in assets], f"Store {len(assets)} slices"))
        self.c.assetsChanged.emit()
        self.c.status(f"Stored {len(assets)} slices from {self.asset.name}")
