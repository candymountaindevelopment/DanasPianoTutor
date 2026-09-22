"""Two hands seen from above, as the player sees their own on the keys.

The finger that is playing lights up in its hand's colour; the finger that
plays next is outlined so the student can get it ready. Under each hand the
five-finger position is spelled out ("C position — thumb on C4").
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ...patterns.notes import midi_to_note
from ...teach.score import FINGER_NAMES, HAND_NAMES, LEFT, RIGHT, Lesson
from ..theme import COLORS
from .lane_view import HAND_COLOURS

# Finger lengths relative to the palm width, thumb first.
FINGER_LENGTH = {1: 0.58, 2: 0.92, 3: 1.0, 4: 0.93, 5: 0.74}
SKIN = "#3a4150"
SKIN_EDGE = "#586275"


class HandsView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lesson: Lesson | None = None
        self.position: float = 0.0
        self.dim_hands: tuple[str, ...] = ()
        self.setMinimumSize(300, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_lesson(self, lesson: Lesson | None) -> None:
        self.lesson = lesson
        self.position = 0.0
        self.update()

    def set_position(self, division: float) -> None:
        self.position = float(division)
        self.update()

    def set_dim_hands(self, hands) -> None:
        self.dim_hands = tuple(hands)
        self.update()

    # ------------------------------------------------------------- state

    def _active(self, hand: str) -> dict[int, list[int]]:
        """finger -> midis sounding now for that hand."""
        out: dict[int, list[int]] = {}
        if self.lesson is None:
            return out
        for n in self.lesson.sounding(self.position, (hand,)):
            if n.shown_finger:
                out.setdefault(n.shown_finger, []).append(n.midi)
        return out

    def _next(self, hand: str) -> tuple[int | None, int | None]:
        if self.lesson is None:
            return None, None
        n = self.lesson.upcoming(self.position, hand)
        return (n.shown_finger, n.midi) if n else (None, None)

    # ------------------------------------------------------------- paint

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(COLORS["panel"]))
        w, h = self.width(), self.height()
        half = w / 2
        self._draw_hand(p, QRectF(6, 6, half - 12, h - 12), LEFT)
        self._draw_hand(p, QRectF(half + 6, 6, half - 12, h - 12), RIGHT)

    def _draw_hand(self, p: QPainter, box: QRectF, hand: str) -> None:
        dim = hand in self.dim_hands
        colour = QColor(HAND_COLOURS[hand])
        active = self._active(hand)
        next_finger, next_midi = self._next(hand)

        label_h = 34
        area = QRectF(box.x(), box.y(), box.width(), box.height() - label_h)
        palm_w = min(area.width() * 0.62, area.height() * 0.55)
        palm_h = palm_w * 0.95
        cx = area.center().x()
        palm_top = area.bottom() - palm_h - 4
        palm = QRectF(cx - palm_w / 2, palm_top, palm_w, palm_h)
        finger_w = palm_w / 4.6
        max_len = min(palm_w * 1.0, palm_top - area.top() - 14)

        p.setPen(QPen(QColor(SKIN_EDGE), 1.2))
        p.setBrush(QColor(SKIN))
        p.drawRoundedRect(palm, palm_w * 0.22, palm_w * 0.22)

        # Fingers 2-5 rise from the palm; the thumb angles out from the side.
        order = [2, 3, 4, 5] if hand == RIGHT else [5, 4, 3, 2]
        slots = 4
        for i, finger in enumerate(order):
            fx = palm.left() + palm_w * (i + 0.5) / slots
            length = max_len * FINGER_LENGTH[finger]
            rect = QRectF(fx - finger_w / 2, palm_top - length + finger_w * 0.6, finger_w, length)
            self._finger(p, rect, finger, active.get(finger), next_finger == finger, colour, dim,
                         tip=QPointF(fx, rect.top()), angle=0.0)

        # Thumb.
        thumb_len = max_len * FINGER_LENGTH[1]
        side = 1 if hand == LEFT else -1        # left hand: thumb on the right
        base = QPointF(palm.left() + finger_w * 0.2 if side < 0 else palm.right() - finger_w * 0.2,
                       palm.top() + palm_h * 0.45)
        angle = 50.0 * side                     # lean outward, tip towards the fingers
        p.save()
        p.translate(base)
        p.rotate(angle)
        rect = QRectF(-finger_w / 2, -thumb_len, finger_w, thumb_len + finger_w * 0.4)
        tip_local = QPointF(0, -thumb_len)
        self._finger(p, rect, 1, active.get(1), next_finger == 1, colour, dim, tip=tip_local, angle=angle)
        p.restore()

        # Caption: hand name, what is sounding, and the position.
        font = QFont(self.font())
        font.setPointSizeF(9)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(COLORS["text_dim"] if dim else COLORS["text"]))
        caption = HAND_NAMES[hand]
        if dim:
            caption += " (muted)"
        p.drawText(QRectF(box.x(), box.bottom() - label_h + 2, box.width(), 16),
                   Qt.AlignmentFlag.AlignHCenter, caption)
        font.setBold(False)
        font.setPointSizeF(8.5)
        p.setFont(font)
        if active:
            parts = [f"{f} on {' '.join(midi_to_note(m) for m in sorted(ms))}" for f, ms in sorted(active.items())]
            text = ", ".join(parts)
            p.setPen(colour if not dim else QColor(COLORS["text_dim"]))
        elif next_finger:
            text = f"next: {next_finger} ({FINGER_NAMES[next_finger]}) on {midi_to_note(next_midi)}"
            p.setPen(QColor(COLORS["text_dim"]))
        else:
            text = self.lesson.position_label(hand).split(";")[0] if self.lesson else ""
            p.setPen(QColor(COLORS["text_dim"]))
        p.drawText(QRectF(box.x(), box.bottom() - label_h + 18, box.width(), 16),
                   Qt.AlignmentFlag.AlignHCenter, text)

    def _finger(self, p: QPainter, rect: QRectF, finger: int, midis, upcoming: bool,
                colour: QColor, dim: bool, tip: QPointF, angle: float) -> None:
        radius = rect.width() / 2
        if midis:
            fill = QColor(colour).darker(150) if dim else QColor(colour)
            edge = QPen(fill.lighter(130), 1.5)
        elif upcoming and not dim:
            fill = QColor(SKIN)
            edge = QPen(colour, 2.0, Qt.PenStyle.DashLine)
        else:
            fill = QColor(SKIN)
            edge = QPen(QColor(SKIN_EDGE), 1.2)
        p.setPen(edge)
        p.setBrush(fill)
        p.drawRoundedRect(rect, radius, radius)
        # Finger number at the tip.
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, min(12.0, radius * 1.1)))
        font.setBold(bool(midis))
        p.setFont(font)
        p.setPen(QColor("#0d1013") if midis and not dim else QColor(COLORS["text"]))
        p.drawText(QRectF(rect.x(), rect.y() + 2, rect.width(), radius * 2.2),
                   Qt.AlignmentFlag.AlignCenter, str(finger))
        if midis:
            # Note name just past the tip (the thumb's is rotated with it, fine).
            p.setPen(colour if not dim else QColor(COLORS["text_dim"]))
            font.setBold(False)
            font.setPointSizeF(8)
            p.setFont(font)
            label = " ".join(midi_to_note(m) for m in sorted(midis))
            p.drawText(QRectF(rect.x() - 20, rect.y() - 16, rect.width() + 40, 14),
                       Qt.AlignmentFlag.AlignCenter, label)
