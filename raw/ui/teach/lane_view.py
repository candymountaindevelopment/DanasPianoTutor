"""A scrolling note lane: time runs left to right, pitch bottom to top, the
playhead stays put and the notes slide past it. Each note carries its finger
number, so a student can watch this strip alone and still know what to press."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ...teach.score import LEFT, RIGHT, Lesson, is_white
from ..theme import COLORS

HAND_COLOURS = {RIGHT: "#4fd1a5", LEFT: "#5c9ce0"}
HAND_DIM = {RIGHT: "#2c6b56", LEFT: "#31527a"}


class LaneView(QWidget):
    divisionClicked = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lesson: Lesson | None = None
        self.position: float = 0.0
        self.dim_hands: tuple[str, ...] = ()
        self.loop_range: tuple[int, int] | None = None   # divisions
        self.px_per_division = 5.0
        self.playhead_fraction = 0.28
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_lesson(self, lesson: Lesson | None) -> None:
        self.lesson = lesson
        self.position = 0.0
        self.loop_range = None
        self.update()

    def set_position(self, division: float) -> None:
        self.position = float(division)
        self.update()

    def set_dim_hands(self, hands) -> None:
        self.dim_hands = tuple(hands)
        self.update()

    def set_loop_range(self, divisions: tuple[int, int] | None) -> None:
        self.loop_range = divisions
        self.update()

    def set_zoom(self, px_per_division: float) -> None:
        self.px_per_division = max(1.5, min(20.0, float(px_per_division)))
        self.update()

    # ----------------------------------------------------------------- geometry

    def _x(self, division: float) -> float:
        return self.width() * self.playhead_fraction + (division - self.position) * self.px_per_division

    def _division_at(self, x: float) -> float:
        return self.position + (x - self.width() * self.playhead_fraction) / self.px_per_division

    def _rows(self) -> tuple[int, int, float]:
        lo, hi = self.lesson.midi_range() if self.lesson else (60, 72)
        lo, hi = lo - 2, hi + 2
        row_h = max(4.0, (self.height() - 22) / max(8, hi - lo + 1))
        return lo, hi, row_h

    def _y(self, midi: int, lo: int, row_h: float) -> float:
        return self.height() - 4 - (midi - lo + 1) * row_h

    # ------------------------------------------------------------------- paint

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(COLORS["panel"]))
        if self.lesson is None:
            return
        lesson = self.lesson
        lo, hi, row_h = self._rows()
        top = 18

        # Pitch rows: white keys light, black keys dark, C lines a shade brighter.
        for midi in range(lo, hi + 1):
            y = self._y(midi, lo, row_h)
            colour = QColor(COLORS["panel_alt"]) if is_white(midi) else QColor(COLORS["bg"])
            p.fillRect(QRectF(0, y, self.width(), row_h), colour)
            if midi % 12 == 0:
                p.setPen(QPen(QColor(COLORS["border"]).lighter(140), 1))
                p.drawLine(0, int(y + row_h), self.width(), int(y + row_h))

        # Loop range shading.
        if self.loop_range:
            x0, x1 = self._x(self.loop_range[0]), self._x(self.loop_range[1])
            shade = QColor(COLORS["accent_dim"])
            shade.setAlpha(40)
            p.fillRect(QRectF(x0, top, x1 - x0, self.height() - top), shade)

        # Beat and bar lines with bar numbers along the top.
        m = lesson.measure_divisions
        beat = lesson.beat_divisions
        first = int(max(0, self._division_at(0)) // beat * beat)
        last = int(self._division_at(self.width()) // beat * beat) + beat
        font = QFont(self.font())
        font.setPointSizeF(8)
        p.setFont(font)
        d = first
        while d <= max(last, lesson.length):
            x = self._x(d)
            if 0 <= x <= self.width():
                is_bar = d % m == 0
                p.setPen(QPen(QColor(COLORS["border"]).lighter(160 if is_bar else 100), 1))
                p.drawLine(int(x), top, int(x), self.height())
                if is_bar and d < lesson.length:
                    p.setPen(QColor(COLORS["text_dim"]))
                    p.drawText(QRectF(x + 3, 2, 40, 14), Qt.AlignmentFlag.AlignLeft, str(d // m + 1))
            d += beat
            if d > lesson.length + m and d > last:
                break
        end_x = self._x(lesson.length)
        if end_x < self.width():
            p.setPen(QPen(QColor(COLORS["text_dim"]), 2))
            p.drawLine(int(end_x), top, int(end_x), self.height())

        # Notes.
        label_font = QFont(self.font())
        label_font.setPointSizeF(max(7.0, min(10.0, row_h * 0.9)))
        label_font.setBold(True)
        p.setFont(label_font)
        for n in lesson.notes:
            x0, x1 = self._x(n.start), self._x(n.end)
            if x1 < 0 or x0 > self.width():
                continue
            y = self._y(n.midi, lo, row_h)
            sounding = n.start <= self.position < n.end
            if n.hand in self.dim_hands:
                colour = QColor(HAND_DIM[n.hand]).darker(140)
            elif sounding:
                colour = QColor(HAND_COLOURS[n.hand]).lighter(125)
            elif n.end <= self.position:
                colour = QColor(HAND_DIM[n.hand])
            else:
                colour = QColor(HAND_COLOURS[n.hand])
            rect = QRectF(x0 + 1, y + 0.5, max(3.0, x1 - x0 - 2), row_h - 1)
            p.setPen(QPen(QColor(COLORS["bg"]), 1))
            p.setBrush(colour)
            p.drawRoundedRect(rect, 2.5, 2.5)
            finger = n.shown_finger
            if finger and rect.width() > 10 and row_h >= 7:
                p.setPen(QColor("#0d1013") if not n.hand in self.dim_hands else QColor(COLORS["text_dim"]))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(finger))

        # Playhead.
        x = self.width() * self.playhead_fraction
        p.setPen(QPen(QColor(COLORS["playhead"]), 2))
        p.drawLine(int(x), 0, int(x), self.height())

    def mousePressEvent(self, event) -> None:
        if self.lesson is None:
            return
        self.divisionClicked.emit(max(0.0, self._division_at(event.position().x())))

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.px_per_division * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15))
            event.accept()
        else:
            super().wheelEvent(event)
