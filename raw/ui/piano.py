"""A clickable piano keyboard for picking notes and chords."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..patterns.notes import midi_to_note
from .theme import COLORS

# Semitone of each white key within an octave, and the black key that sits
# after it (white index -> semitone), if any.
WHITE_SEMITONES = (0, 2, 4, 5, 7, 9, 11)
BLACK_AFTER = {0: 1, 1: 3, 3: 6, 4: 8, 5: 10}

WHITE_KEY = "#e8ecf2"
WHITE_KEY_EDGE = "#9aa3b0"
BLACK_KEY = "#1a1d23"


class PianoKeyboard(QWidget):
    """Click keys to toggle them. Selected keys are tinted with the accent."""

    selectionChanged = pyqtSignal()
    keyClicked = pyqtSignal(int)

    def __init__(self, low_midi: int = 48, octaves: int = 3, parent=None) -> None:
        super().__init__(parent)
        self.low_midi = low_midi
        self.octaves = octaves
        self._selected: set[int] = set()
        self._hover: int | None = None
        # midi -> (colour, label): keys tinted and labelled by the caller, on top
        # of the selection. Piano Tutor uses this for hand colours and finger numbers.
        self._marks: dict[int, tuple[str, str]] = {}
        self.interactive = True
        self.setMinimumHeight(96)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------------------------------------------------ state

    @property
    def selected(self) -> list[int]:
        return sorted(self._selected)

    def set_selected(self, notes) -> None:
        wanted = {int(n) for n in notes}
        if wanted != self._selected:
            self._selected = wanted
            self.update()
            self.selectionChanged.emit()

    def clear(self) -> None:
        self.set_selected(())

    def set_marks(self, marks: dict) -> None:
        """midi -> (colour, label). Replaces the previous marks."""
        wanted = {int(k): (str(v[0]), str(v[1])) for k, v in dict(marks).items()}
        if wanted != self._marks:
            self._marks = wanted
            self.update()

    def set_range(self, low_midi: int, octaves: int) -> None:
        self.low_midi = int(low_midi)
        self.octaves = max(1, int(octaves))
        self.update()

    @property
    def high_midi(self) -> int:
        return self.low_midi + 12 * self.octaves - 1

    def contains(self, midi: int) -> bool:
        return self.low_midi <= midi <= self.high_midi

    # ---------------------------------------------------------------- layout

    def _white_count(self) -> int:
        return 7 * self.octaves

    def _white_rects(self) -> list[tuple[int, QRectF]]:
        width = self.width() / max(1, self._white_count())
        out = []
        for i in range(self._white_count()):
            midi = self.low_midi + 12 * (i // 7) + WHITE_SEMITONES[i % 7]
            out.append((midi, QRectF(i * width, 0, width, self.height())))
        return out

    def _black_rects(self) -> list[tuple[int, QRectF]]:
        width = self.width() / max(1, self._white_count())
        black_w = width * 0.62
        black_h = self.height() * 0.62
        out = []
        for i in range(self._white_count()):
            offset = BLACK_AFTER.get(i % 7)
            if offset is None or i == self._white_count() - 1:
                continue
            midi = self.low_midi + 12 * (i // 7) + offset
            out.append((midi, QRectF((i + 1) * width - black_w / 2, 0, black_w, black_h)))
        return out

    def note_at(self, pos) -> int | None:
        for midi, rect in self._black_rects():  # black keys sit on top
            if rect.contains(pos):
                return midi
        for midi, rect in self._white_rects():
            if rect.contains(pos):
                return midi
        return None

    # ---------------------------------------------------------------- events

    def mousePressEvent(self, event) -> None:
        midi = self.note_at(event.position())
        if midi is None:
            return
        if not self.interactive:
            self.keyClicked.emit(midi)
            return
        if midi in self._selected:
            self._selected.discard(midi)
        else:
            self._selected.add(midi)
        self.update()
        self.selectionChanged.emit()
        self.keyClicked.emit(midi)

    def mouseMoveEvent(self, event) -> None:
        midi = self.note_at(event.position())
        if midi != self._hover:
            self._hover = midi
            self.setToolTip(midi_to_note(midi) if midi is not None else "")
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    # ----------------------------------------------------------------- paint

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor(COLORS["panel"]))
        accent = QColor(COLORS["accent"])
        font = QFont(self.font())
        font.setPointSizeF(7.5)
        p.setFont(font)

        white = self._white_rects()
        for midi, rect in white:
            if midi in self._marks:
                colour = QColor(self._marks[midi][0])
            elif midi in self._selected:
                colour = accent
            elif midi == self._hover:
                colour = QColor(COLORS["accent_dim"]).lighter(150)
            else:
                colour = QColor(WHITE_KEY)
            p.fillRect(rect, colour)
            p.setPen(QPen(QColor(WHITE_KEY_EDGE), 1))
            p.drawRect(rect)

        # Label every C, and every white key when there is room for it.
        show_all = (self.width() / max(1, self._white_count())) > 22
        for midi, rect in white:
            if midi % 12 != 0 and not show_all:
                continue
            p.setPen(QColor("#20242b" if midi not in self._selected else "#0d1013"))
            p.drawText(
                QRectF(rect.x(), rect.bottom() - 16, rect.width(), 14),
                Qt.AlignmentFlag.AlignHCenter, midi_to_note(midi),
            )

        for midi, rect in self._black_rects():
            if midi in self._marks:
                colour = QColor(self._marks[midi][0]).darker(125)
            elif midi in self._selected:
                colour = QColor(COLORS["accent_dim"])
            elif midi == self._hover:
                colour = QColor(COLORS["accent_dim"]).darker(150)
            else:
                colour = QColor(BLACK_KEY)
            p.fillRect(rect, colour)
            p.setPen(QPen(QColor("#000000"), 1))
            p.drawRect(rect)

        # Mark labels (finger numbers) sit in a disc near the bottom of the key.
        if self._marks:
            label_font = QFont(self.font())
            label_font.setPointSizeF(max(8.0, min(13.0, self.height() / 7.0)))
            label_font.setBold(True)
            p.setFont(label_font)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for midi, rect in list(self._black_rects()) + white:
                mark = self._marks.get(midi)
                if not mark or not mark[1]:
                    continue
                black = not (midi % 12 in WHITE_SEMITONES)
                d = min(rect.width() * 0.8, 22.0)
                cy = rect.bottom() - (34 if not black else 14) - d / 2
                disc = QRectF(rect.center().x() - d / 2, cy, d, d)
                p.setPen(QPen(QColor("#0d1013"), 1))
                p.setBrush(QColor("#f4f6f8"))
                p.drawEllipse(disc)
                p.setPen(QColor("#0d1013"))
                p.drawText(disc, Qt.AlignmentFlag.AlignCenter, mark[1])
