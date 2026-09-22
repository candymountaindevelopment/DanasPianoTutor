"""The engraved score on screen, with a playback cursor and highlighted notes."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QScrollArea, QSizePolicy, QWidget

from ...teach.engrave import Layout, engrave
from ...teach.score import Lesson
from ..theme import COLORS
from .painter import LayoutPainter, screen_palette

DEFAULT_SCALE = 8.0     # pixels per staff space
MIN_WIDTH_SP = 60.0


class StaffCanvas(QWidget):
    measureClicked = pyqtSignal(int)      # 1-based bar number
    divisionClicked = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lesson: Lesson | None = None
        self.layout_: Layout | None = None
        self.scale = DEFAULT_SCALE
        self._layout_width = -1
        self.position: float | None = None
        self.dim_hands: tuple[str, ...] = ()
        self.show_inferred = True
        self.palette_ = screen_palette(COLORS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(200)
        self.setMouseTracking(False)

    # ------------------------------------------------------------------ api

    def set_lesson(self, lesson: Lesson | None) -> None:
        self.lesson = lesson
        self.position = 0.0 if lesson else None
        self.invalidate()

    def set_scale(self, scale: float) -> None:
        self.scale = max(4.0, min(16.0, float(scale)))
        self.invalidate()

    def set_show_inferred(self, show: bool) -> None:
        self.show_inferred = bool(show)
        self.invalidate()

    def set_dim_hands(self, hands) -> None:
        self.dim_hands = tuple(hands)
        self.update()

    def set_position(self, division: float | None) -> None:
        self.position = division
        self.update()

    def invalidate(self) -> None:
        self._layout_width = -1
        self.updateGeometry()
        self.update()

    # --------------------------------------------------------------- layout

    def _ensure_layout(self) -> None:
        width = max(1, self.width())
        if self.layout_ is not None and self._layout_width == width:
            return
        self._layout_width = width
        if self.lesson is None:
            self.layout_ = None
            self.setMinimumHeight(200)
            return
        width_sp = max(MIN_WIDTH_SP, width / self.scale)
        self.layout_ = engrave(self.lesson, width_sp, None, margin=2.0, header=True,
                               show_inferred=self.show_inferred)
        height = int(self.layout_.pages[0].height * self.scale) + 8
        self.setMinimumHeight(height)
        self.setFixedHeight(height)

    def system_rect(self, division: float) -> QRectF | None:
        self._ensure_layout()
        if self.layout_ is None:
            return None
        system = self.layout_.system_at(division)
        if system is None:
            return None
        s = self.scale
        return QRectF(0, (system.y - 1) * s, self.width(), (system.bottom - system.y + 2) * s)

    # --------------------------------------------------------------- events

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._ensure_layout()

    def paintEvent(self, event) -> None:
        self._ensure_layout()
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().color(self.backgroundRole()))
        if self.layout_ is None:
            p.setPen(Qt.GlobalColor.gray)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Open a lesson to see its sheet music")
            return
        painter = LayoutPainter(p, self.scale, self.palette_)
        highlight = None
        cursor = None
        if self.position is not None and self.position >= 0:
            highlight = (self.position, self.position + 1e-6)
            cursor = self.position
        painter.draw_page(self.layout_.pages[0], highlight, cursor, self.dim_hands)

    def mousePressEvent(self, event) -> None:
        if self.layout_ is None:
            return
        x = event.position().x() / self.scale
        y = event.position().y() / self.scale
        for system in self.layout_.systems:
            if system.y <= y <= system.bottom:
                for measure in system.measures:
                    if measure.x0 <= x < measure.x1:
                        self.measureClicked.emit(measure.number)
                        self.divisionClicked.emit(float(measure.start))
                        return


class StaffView(QScrollArea):
    """Scrollable score that keeps the current system in view during playback."""

    measureClicked = pyqtSignal(int)
    divisionClicked = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.canvas = StaffCanvas()
        self.canvas.measureClicked.connect(self.measureClicked)
        self.canvas.divisionClicked.connect(self.divisionClicked)
        self.setWidget(self.canvas)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self._follow = True

    def set_lesson(self, lesson: Lesson | None) -> None:
        self.canvas.set_lesson(lesson)
        self.verticalScrollBar().setValue(0)

    def set_position(self, division: float | None) -> None:
        self.canvas.set_position(division)
        if division is None or not self._follow:
            return
        rect = self.canvas.system_rect(max(0.0, division))
        if rect is None:
            return
        bar = self.verticalScrollBar()
        top, bottom = bar.value(), bar.value() + self.viewport().height()
        if rect.top() < top or rect.bottom() > bottom:
            bar.setValue(int(max(0, rect.top() - 12)))

    def set_dim_hands(self, hands) -> None:
        self.canvas.set_dim_hands(hands)

    def set_scale(self, scale: float) -> None:
        self.canvas.set_scale(scale)

    def set_show_inferred(self, show: bool) -> None:
        self.canvas.set_show_inferred(show)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 0.5 if event.angleDelta().y() > 0 else -0.5
            self.canvas.set_scale(self.canvas.scale + step)
            event.accept()
            return
        super().wheelEvent(event)
