"""Welcome screen shown at launch — a photo on black with the name, version
and a line about the app, in the manner of Blender's splash. It closes on a
click or key press, or by itself after a few seconds."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PyQt6.QtCore import QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from ... import __version__ as RAW_VERSION
from ...teach import __version__ as TUTOR_VERSION, APP_NAME, TAGLINE

SPLASH_IMAGE = Path(__file__).resolve().parents[3] / "assets" / "tutor_splash.jpg"
WIDTH, HEIGHT = 720, 590
IMAGE_HEIGHT = 470
# Where the interesting part of the photo is, as a fraction of its height: the
# crop band is centred here so the face and the hands on the keys both show.
FOCUS_Y = 0.44


class SplashScreen(QWidget):
    def __init__(self, parent=None, timeout_ms: int = 4500) -> None:
        super().__init__(parent, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedSize(WIDTH, HEIGHT)
        self._pixmap = self._cropped_photo()
        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, self.close)

    # ------------------------------------------------------------ image

    @staticmethod
    def _cropped_photo() -> QPixmap | None:
        source = QPixmap(str(SPLASH_IMAGE))
        if source.isNull():
            return None
        scaled = source.scaledToWidth(WIDTH, Qt.TransformationMode.SmoothTransformation)
        if scaled.height() <= IMAGE_HEIGHT:
            return scaled
        centre = int(scaled.height() * FOCUS_Y)
        top = max(0, min(scaled.height() - IMAGE_HEIGHT, centre - IMAGE_HEIGHT // 2))
        return scaled.copy(QRect(0, top, WIDTH, IMAGE_HEIGHT))

    # ------------------------------------------------------------ paint

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#000000"))
        image_rect = QRect(0, 0, WIDTH, IMAGE_HEIGHT)
        if self._pixmap is not None:
            p.drawPixmap(image_rect, self._pixmap)
        # Fade the photo into the black band so the text sits on solid black.
        fade = QLinearGradient(0, IMAGE_HEIGHT - 110, 0, IMAGE_HEIGHT)
        fade.setColorAt(0.0, QColor(0, 0, 0, 0))
        fade.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(QRect(0, IMAGE_HEIGHT - 110, WIDTH, 110), fade)
        # A soft shade top-right so the version block reads over the curtains.
        shade = QLinearGradient(WIDTH - 260, 0, WIDTH, 0)
        shade.setColorAt(0.0, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 150))
        p.fillRect(QRect(WIDTH - 260, 0, 260, 120), shade)

        white = QColor("#ffffff")
        dim = QColor("#b9c0cc")

        # Version block, top right.
        p.setPen(white)
        p.setFont(self._font(13))
        block = QRect(WIDTH - 300, 16, 284, 20)
        p.drawText(block, Qt.AlignmentFlag.AlignRight, f"v {TUTOR_VERSION}")
        p.setFont(self._font(11))
        p.setPen(dim)
        p.drawText(block.translated(0, 22), Qt.AlignmentFlag.AlignRight, f"Date: {date.today().isoformat()}")
        p.drawText(block.translated(0, 42), Qt.AlignmentFlag.AlignRight, f"RAW engine {RAW_VERSION}")

        # Name and tagline in the black band.
        p.setPen(white)
        p.setFont(self._font(30, bold=True))
        p.drawText(QRect(28, IMAGE_HEIGHT - 12, WIDTH - 56, 48), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, APP_NAME)
        p.setFont(self._font(12))
        p.setPen(dim)
        p.drawText(QRectF(30, IMAGE_HEIGHT + 40, WIDTH - 60, 44),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap), TAGLINE)
        p.setFont(self._font(10))
        p.setPen(QColor("#6f7885"))
        p.drawText(QRect(30, HEIGHT - 26, WIDTH - 60, 18), Qt.AlignmentFlag.AlignLeft,
                   "Click anywhere to start")
        p.drawText(QRect(30, HEIGHT - 26, WIDTH - 60, 18), Qt.AlignmentFlag.AlignRight,
                   "Part of Retro Audio Workstation")

    @staticmethod
    def _font(size: int, bold: bool = False) -> QFont:
        font = QFont("Segoe UI")
        font.setPixelSize(size)
        font.setBold(bold)
        return font

    # ----------------------------------------------------------- events

    def mousePressEvent(self, event) -> None:
        self.close()

    def keyPressEvent(self, event) -> None:
        self.close()

    def show_centered_on(self, widget: QWidget | None) -> None:
        if widget is not None and widget.isVisible():
            centre = widget.frameGeometry().center()
        else:
            screen = self.screen() or (widget.screen() if widget else None)
            centre = screen.availableGeometry().center() if screen else self.rect().center()
        self.move(centre.x() - WIDTH // 2, centre.y() - HEIGHT // 2)
        self.show()
        self.raise_()
