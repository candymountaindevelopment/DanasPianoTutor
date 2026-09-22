"""Waveform display with min/max peak reduction and time-structure overlay."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..audio.buffer import AudioBuffer
from .theme import COLORS, REGION_COLORS


class WaveformView(QWidget):
    """Draws a buffer, optional region bands, and a playhead.

    Long files stay responsive because the widget never draws more columns
    than it has pixels (spec section 16).
    """

    clicked = pyqtSignal(float)  # position in seconds

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._buffer: AudioBuffer | None = None
        self._peaks: tuple = ()
        self._peaks_width = -1
        self._regions: list[tuple[str, float, float]] = []  # (role, t0, t1) in seconds
        self._playhead: float | None = None
        self._ghost_duration: float | None = None
        self._label = "no asset selected"
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------- api

    def set_buffer(self, buffer: AudioBuffer | None, label: str = "") -> None:
        self._buffer = buffer
        self._peaks_width = -1
        self._label = label or ("empty" if buffer is None else "")
        self.update()

    def set_regions(self, regions) -> None:
        """Bands drawn behind the waveform.

        Each entry is (label, start_s, end_s) — coloured by role — or
        (label, start_s, end_s, colour) to pick the colour explicitly, which
        the Sample Lab uses to tell slices apart.
        """
        self._regions = [tuple(r) for r in regions]
        self.update()

    @staticmethod
    def _region_colour(region) -> str:
        if len(region) >= 4 and region[3]:
            return region[3]
        return REGION_COLORS.get(region[0], COLORS["region_custom"])

    def set_ghost_duration(self, duration: float | None) -> None:
        """Draw a marker at the un-stretched (1x) length for A/B comparison."""
        self._ghost_duration = duration
        self.update()

    def set_playhead(self, seconds: float | None) -> None:
        self._playhead = seconds
        self.update()

    # --------------------------------------------------------------- events

    def mousePressEvent(self, event) -> None:
        if self._buffer and self._buffer.duration > 0 and self.width() > 0:
            frac = max(0.0, min(1.0, event.position().x() / self.width()))
            self.clicked.emit(frac * self._buffer.duration)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        mid = h / 2

        p.fillRect(0, 0, w, h, QColor(COLORS["panel"]))

        if self._buffer is None or self._buffer.num_frames == 0:
            p.setPen(QColor(COLORS["text_dim"]))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, self._label or "no audio")
            return

        duration = self._buffer.duration

        # Region bands behind the waveform.
        for region in self._regions:
            _, t0, t1 = region[0], region[1], region[2]
            x0 = (t0 / duration) * w
            x1 = (t1 / duration) * w
            base = self._region_colour(region)
            color = QColor(base)
            color.setAlpha(34)
            p.fillRect(QRectF(x0, 0, max(1.0, x1 - x0), h), color)
            edge = QColor(base)
            edge.setAlpha(140)
            p.setPen(QPen(edge, 1))
            p.drawLine(int(x0), 0, int(x0), h)
            p.drawLine(int(x1), 0, int(x1), h)

        # Centre line and grid.
        p.setPen(QPen(QColor(COLORS["grid"]), 1))
        p.drawLine(0, int(mid), w, int(mid))

        # Waveform.
        if self._peaks_width != w:
            self._peaks = self._buffer.peaks(max(1, w))
            self._peaks_width = w
        lo, hi = self._peaks
        p.setPen(QPen(QColor(COLORS["wave"]), 1))
        for x in range(min(w, len(lo))):
            y0 = mid - hi[x] * mid * 0.92
            y1 = mid - lo[x] * mid * 0.92
            if abs(y1 - y0) < 1:
                y1 = y0 + 1
            p.drawLine(x, int(y0), x, int(y1))

        # Region labels.
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        p.setFont(font)
        for region in self._regions:
            label, t0, t1 = region[0], region[1], region[2]
            x0 = (t0 / duration) * w
            x1 = (t1 / duration) * w
            if x1 - x0 < 34:
                continue
            p.setPen(QColor(self._region_colour(region)))
            p.drawText(QRectF(x0 + 3, 2, x1 - x0 - 6, 14),
                       Qt.AlignmentFlag.AlignLeft, label.upper())

        # 1x ghost marker.
        if self._ghost_duration and self._ghost_duration < duration:
            gx = (self._ghost_duration / duration) * w
            pen = QPen(QColor(COLORS["text_dim"]), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(int(gx), 0, int(gx), h)
            p.drawText(QRectF(gx + 4, h - 16, 40, 14), Qt.AlignmentFlag.AlignLeft, "1x")

        # Playhead.
        if self._playhead is not None and duration > 0:
            px = (self._playhead / duration) * w
            p.setPen(QPen(QColor(COLORS["playhead"]), 1))
            p.drawLine(int(px), 0, int(px), h)

        # Footer.
        p.setPen(QColor(COLORS["text_dim"]))
        info = f"{duration:.3f}s · {self._buffer.sample_rate} Hz · peak {self._buffer.peak():.2f}"
        p.drawText(QRectF(4, h - 16, w - 8, 14), Qt.AlignmentFlag.AlignRight, info)
