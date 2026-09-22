"""Draw an engraved layout with QPainter — on screen, to PDF, or to a printer.

The layout is in staff spaces; `scale` is pixels (or points) per space. The
same code draws the practice view and the printed page, so the two agree.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QPainter, QPainterPath, QPen

from ...teach.engrave import NOTEHEAD_W, STAFF_HEIGHT, Page, System
from ...teach.score import RIGHT

TREBLE_CLEF = "\U0001D11E"
BASS_CLEF = "\U0001D122"
QUARTER_REST = "\U0001D13D"
EIGHTH_REST = "\U0001D13E"
SIXTEENTH_REST = "\U0001D13F"

_MUSIC_FONT_CANDIDATES = ("Bravura Text", "Bravura", "Segoe UI Symbol", "Noto Music", "Noto Sans Symbols2",
                          "Symbola", "DejaVu Sans", "Apple Symbols")


@dataclass
class Palette:
    ink: str = "#111111"
    staff: str = "#333333"
    text: str = "#111111"
    right: str = "#1f8f6a"      # highlight colour for sounding right-hand notes
    left: str = "#2f6fc0"
    inferred: str = "#777777"   # fingers derived from the hand position
    cursor: str = "#e0b341"
    dim: str = "#9a9a9a"        # a hand that is muted in playback
    background: str | None = None


PRINT_PALETTE = Palette()


def screen_palette(colors: dict) -> Palette:
    return Palette(
        ink=colors["text"], staff=colors["text_dim"], text=colors["text"],
        right=colors["accent"], left="#5c9ce0", inferred=colors["text_dim"],
        cursor=colors["playhead"], dim="#4a515c", background=colors["panel"],
    )


_music_font_name: str | None = None


def music_font_family() -> str | None:
    """A font that has the musical symbols block, if any is installed."""
    global _music_font_name
    if _music_font_name is not None:
        return _music_font_name or None
    families = set(QFontDatabase.families())
    for name in _MUSIC_FONT_CANDIDATES:
        if name in families:
            font = QFont(name)
            font.setPixelSize(40)
            if QFontMetricsF(font).inFontUcs4(ord(TREBLE_CLEF)):
                _music_font_name = name
                return name
    _music_font_name = ""
    return None


class LayoutPainter:
    def __init__(self, painter: QPainter, scale: float, palette: Palette,
                 origin: tuple[float, float] = (0.0, 0.0)) -> None:
        self.p = painter
        self.s = float(scale)
        self.pal = palette
        self.ox, self.oy = origin
        self.music_family = music_font_family()
        self.p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    # ------------------------------------------------------------ units

    def X(self, x: float) -> float:
        return self.ox + x * self.s

    def Y(self, y: float) -> float:
        return self.oy + y * self.s

    def pen(self, colour: str, width_sp: float) -> QPen:
        pen = QPen(QColor(colour), max(0.8, width_sp * self.s))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        return pen

    def text_font(self, size_sp: float, bold: bool = False, italic: bool = False) -> QFont:
        font = QFont("Segoe UI")
        font.setPixelSize(max(6, int(round(size_sp * self.s * 1.15))))
        font.setBold(bold)
        font.setItalic(italic)
        return font

    def music_font(self, size_sp: float) -> QFont | None:
        if not self.music_family:
            return None
        font = QFont(self.music_family)
        font.setPixelSize(max(6, int(round(size_sp * self.s))))
        return font

    # ------------------------------------------------------------- page

    def draw_page(self, page: Page, highlight: tuple[float, float] | None = None,
                  cursor: float | None = None, dim_hands: tuple[str, ...] = ()) -> None:
        if self.pal.background:
            self.p.fillRect(QRectF(self.X(0), self.Y(0), page.width * self.s, page.height * self.s),
                            QColor(self.pal.background))
        for text in page.texts:
            self.draw_text(text.x, text.y, text.text, text.size, text.align, text.style)
        for system in page.systems:
            self.draw_system(system, highlight, cursor, dim_hands)

    def draw_system(self, system: System, highlight=None, cursor=None, dim_hands=()) -> None:
        p = self.p
        # Staff lines and bar lines.
        p.setPen(self.pen(self.pal.staff, 0.1))
        for top in (system.treble_top, system.bass_top):
            for i in range(5):
                y = self.Y(top + i)
                p.drawLine(QPointF(self.X(system.x0), y), QPointF(self.X(system.x1), y))
        y0, y1 = self.Y(system.treble_top), self.Y(system.bass_top + STAFF_HEIGHT)
        p.setPen(self.pen(self.pal.ink, 0.14))
        p.drawLine(QPointF(self.X(system.x0), y0), QPointF(self.X(system.x0), y1))
        for i, x in enumerate(system.barlines):
            last = i == len(system.barlines) - 1
            if last and system.final:
                p.setPen(self.pen(self.pal.ink, 0.14))
                p.drawLine(QPointF(self.X(x - 0.55), y0), QPointF(self.X(x - 0.55), y1))
                p.setPen(self.pen(self.pal.ink, 0.45))
                p.drawLine(QPointF(self.X(x - 0.1), y0), QPointF(self.X(x - 0.1), y1))
            else:
                p.setPen(self.pen(self.pal.ink, 0.14))
                p.drawLine(QPointF(self.X(x), y0), QPointF(self.X(x), y1))
        # Brace-ish bracket joining the two staves.
        p.setPen(self.pen(self.pal.ink, 0.35))
        p.drawLine(QPointF(self.X(system.x0 - 0.5), y0), QPointF(self.X(system.x0 - 0.5), y1))

        for sym in system.symbols:
            self.draw_symbol(sym)
        for text in system.texts:
            self.draw_text(text.x, text.y, text.text, text.size, text.align, text.style)

        # Cursor behind the notes.
        if cursor is not None and system.contains(cursor):
            x = self.X(system.x_at(cursor))
            p.setPen(self.pen(self.pal.cursor, 0.18))
            p.drawLine(QPointF(x, self.Y(system.treble_top - 2.0)), QPointF(x, self.Y(system.bass_top + STAFF_HEIGHT + 2.0)))

        for rest in system.rests:
            self.draw_rest(rest)
        for stem in system.stems:
            self.draw_stem(stem)
        for head in system.noteheads:
            colour = self.pal.ink
            if head.hand in dim_hands:
                colour = self.pal.dim
            elif highlight is not None and head.start < highlight[1] and head.end > highlight[0]:
                colour = self.pal.right if head.hand == RIGHT else self.pal.left
            self.draw_notehead(head, colour)
        for tie in system.ties:
            self.draw_tie(tie)
        for finger in system.fingers:
            colour = self.pal.ink if finger.written else self.pal.inferred
            if finger.hand in dim_hands:
                colour = self.pal.dim
            self.draw_finger(finger, colour)

    # ------------------------------------------------------------ pieces

    def draw_notehead(self, head, colour: str) -> None:
        p = self.p
        s = self.s
        cx, cy = self.X(head.x), self.Y(head.y)
        for ly in head.ledger:
            p.setPen(self.pen(self.pal.staff, 0.12))
            p.drawLine(QPointF(cx - 0.9 * s, self.Y(ly)), QPointF(cx + 0.9 * s, self.Y(ly)))
        p.save()
        p.translate(cx, cy)
        p.rotate(-22)
        rect = QRectF(-NOTEHEAD_W / 2 * s, -0.47 * s, NOTEHEAD_W * s, 0.94 * s)
        if head.filled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(colour)))
            p.drawEllipse(rect)
        else:
            p.setPen(self.pen(colour, 0.22))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(rect.adjusted(0.08 * s, 0.1 * s, -0.08 * s, -0.1 * s))
        p.restore()
        if head.dots:
            dy = -0.5 if head.on_line else 0.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(colour)))
            for i in range(head.dots):
                p.drawEllipse(QPointF(self.X(head.x + 1.0 + i * 0.5), self.Y(head.y + dy)), 0.18 * s, 0.18 * s)
        if head.accidental:
            self.draw_glyph_text(head.x - 0.85, head.y, head.accidental, 2.3, colour, align="right")

    def draw_stem(self, stem) -> None:
        p = self.p
        s = self.s
        x = self.X(stem.x)
        p.setPen(self.pen(self.pal.ink, 0.13))
        p.drawLine(QPointF(x, self.Y(stem.y0)), QPointF(x, self.Y(stem.y1)))
        for i in range(stem.flags):
            path = QPainterPath()
            tip = self.Y(stem.y1) + (i * 0.75 * s) * (1 if stem.up else -1)
            d = 1 if stem.up else -1
            path.moveTo(x, tip)
            path.cubicTo(x + 0.1 * s, tip + 1.0 * s * d, x + 1.25 * s, tip + 1.3 * s * d, x + 0.95 * s, tip + 2.7 * s * d)
            path.cubicTo(x + 1.05 * s, tip + 1.7 * s * d, x + 0.45 * s, tip + 1.25 * s * d, x, tip + 0.75 * s * d)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(self.pal.ink)))
            p.drawPath(path)

    def draw_rest(self, rest) -> None:
        p = self.p
        s = self.s
        x, y = self.X(rest.x), self.Y(rest.y)
        colour = self.pal.ink
        if rest.type_name == "whole":
            p.fillRect(QRectF(x - 0.65 * s, y, 1.3 * s, 0.5 * s), QColor(colour))
        elif rest.type_name == "half":
            p.fillRect(QRectF(x - 0.65 * s, y - 0.5 * s, 1.3 * s, 0.5 * s), QColor(colour))
        elif rest.type_name == "quarter":
            if not self.draw_glyph_text(rest.x, rest.y, QUARTER_REST, 3.6, colour, music=True):
                path = QPainterPath()
                path.moveTo(x - 0.35 * s, y - 1.6 * s)
                path.lineTo(x + 0.45 * s, y - 0.5 * s)
                path.lineTo(x - 0.25 * s, y + 0.3 * s)
                path.lineTo(x + 0.35 * s, y + 1.3 * s)
                p.setPen(self.pen(colour, 0.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        else:
            glyph = EIGHTH_REST if rest.type_name == "eighth" else SIXTEENTH_REST
            if not self.draw_glyph_text(rest.x, rest.y, glyph, 3.6, colour, music=True):
                p.setPen(self.pen(colour, 0.22))
                p.drawLine(QPointF(x + 0.5 * s, y - 0.8 * s), QPointF(x - 0.3 * s, y + 1.2 * s))
                p.setBrush(QBrush(QColor(colour)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(x - 0.1 * s, y - 0.75 * s), 0.3 * s, 0.3 * s)
        if rest.dots:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(colour)))
            for i in range(rest.dots):
                p.drawEllipse(QPointF(x + (1.0 + i * 0.5) * s, y - 0.5 * s), 0.18 * s, 0.18 * s)

    def draw_tie(self, tie) -> None:
        s = self.s
        d = 1 if tie.below else -1
        x0, x1 = self.X(tie.x0), self.X(tie.x1)
        y0, y1 = self.Y(tie.y0) + 0.55 * s * d, self.Y(tie.y1) + 0.55 * s * d
        bulge = min(1.1 * s, 0.25 * abs(x1 - x0) + 0.4 * s) * d
        path = QPainterPath()
        path.moveTo(x0, y0)
        path.cubicTo(x0 + (x1 - x0) * 0.3, y0 + bulge, x0 + (x1 - x0) * 0.7, y1 + bulge, x1, y1)
        path.cubicTo(x0 + (x1 - x0) * 0.7, y1 + bulge * 0.7, x0 + (x1 - x0) * 0.3, y0 + bulge * 0.7, x0, y0)
        self.p.setPen(self.pen(self.pal.ink, 0.08))
        self.p.setBrush(QBrush(QColor(self.pal.ink)))
        self.p.drawPath(path)

    def draw_finger(self, finger, colour: str) -> None:
        font = self.text_font(1.25, bold=finger.written)
        self.p.setFont(font)
        self.p.setPen(QColor(colour))
        rect = QRectF(self.X(finger.x) - 1.5 * self.s, self.Y(finger.y) - 0.8 * self.s, 3.0 * self.s, 1.6 * self.s)
        self.p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(finger.number))

    def draw_fitted_glyph(self, x: float, top: float, height: float, glyph: str, colour: str) -> bool:
        """Draw a music glyph scaled so its ink is `height` spaces tall with its
        top at `top`. Fonts disagree wildly about clef metrics; measuring the
        ink is the only placement that works across them."""
        if not self.music_family:
            return False
        probe = QFont(self.music_family)
        probe.setPixelSize(100)
        tight = QFontMetricsF(probe).tightBoundingRect(glyph)
        if tight.height() <= 0:
            return False
        px = 100.0 * (height * self.s) / tight.height()
        font = QFont(self.music_family)
        font.setPixelSize(max(6, int(round(px))))
        ratio = font.pixelSize() / 100.0
        self.p.setFont(font)
        self.p.setPen(QColor(colour))
        self.p.drawText(QPointF(self.X(x) - tight.x() * ratio, self.Y(top) - tight.y() * ratio), glyph)
        return True

    def draw_symbol(self, sym) -> None:
        if sym.kind == "treble_clef":
            # Ink from 2.6 spaces above the top line to 1.4 below the bottom one.
            if not self.draw_fitted_glyph(sym.x, sym.y - 2.6, 8.0, TREBLE_CLEF, self.pal.ink):
                self.draw_text(sym.x, sym.y + 3.2, "G", 3.6, "left", "bold")
        elif sym.kind == "bass_clef":
            if not self.draw_fitted_glyph(sym.x + 0.3, sym.y - 0.05, 3.35, BASS_CLEF, self.pal.ink):
                self.draw_text(sym.x, sym.y + 3.2, "F", 3.6, "left", "bold")
        elif sym.kind in ("sharp", "flat", "natural"):
            glyph = {"sharp": "♯", "flat": "♭", "natural": "♮"}[sym.kind]
            self.draw_glyph_text(sym.x, sym.y, glyph, 2.3, self.pal.ink, align="left")
        elif sym.kind == "digit":
            self.draw_text(sym.x, sym.y, sym.text, 2.0, "left", "bold", vcenter=True)

    # -------------------------------------------------------------- text

    def draw_glyph_text(self, x: float, y: float, text: str, size_sp: float, colour: str,
                        music: bool = False, align: str = "center", baseline: bool = False) -> bool:
        font = self.music_font(size_sp) if music else None
        if music and font is None:
            return False
        if font is None:
            font = QFont("Segoe UI Symbol")
            font.setPixelSize(max(6, int(round(size_sp * self.s))))
        self.p.setFont(font)
        self.p.setPen(QColor(colour))
        fm = QFontMetricsF(font)
        w = fm.horizontalAdvance(text)
        px = self.X(x)
        if align == "center":
            px -= w / 2
        elif align == "right":
            px -= w
        if baseline:
            self.p.drawText(QPointF(px, self.Y(y)), text)
        else:
            rect = QRectF(px, self.Y(y) - fm.height() / 2, w, fm.height())
            self.p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        return True

    def draw_text(self, x: float, y: float, text: str, size_sp: float, align: str = "left",
                  style: str = "normal", vcenter: bool = False) -> None:
        font = self.text_font(size_sp, bold=(style == "bold"), italic=(style == "italic"))
        if style == "small":
            font.setPixelSize(max(6, int(round(size_sp * self.s))))
        self.p.setFont(font)
        self.p.setPen(QColor(self.pal.text))
        fm = QFontMetricsF(font)
        w = fm.horizontalAdvance(text)
        px = self.X(x)
        if align == "center":
            px -= w / 2
        elif align == "right":
            px -= w
        if vcenter:
            rect = QRectF(px, self.Y(y) - fm.height() / 2, w + 2, fm.height())
            self.p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        else:
            self.p.drawText(QPointF(px, self.Y(y)), text)
