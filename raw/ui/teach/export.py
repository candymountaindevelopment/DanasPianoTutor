"""Printable output: the engraved score to PDF or straight to a printer."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QMarginsF
from PyQt6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter

from ...teach.engrave import Layout, engrave
from ...teach.score import Lesson
from .painter import PRINT_PALETTE, LayoutPainter

PAGE_WIDTH_SP = 105.0     # staff spaces across an A4 page: ~2 mm per space, a normal print size
MARGIN_SP = 3.0


def paged_layout(lesson: Lesson, paint_width: float, paint_height: float,
                 show_inferred: bool = True) -> tuple[Layout, float]:
    """Lay the lesson out for a device paint area (in device pixels). Returns (layout, scale)."""
    scale = paint_width / PAGE_WIDTH_SP
    page_height_sp = paint_height / scale
    layout = engrave(lesson, PAGE_WIDTH_SP, page_height_sp, MARGIN_SP, header=True,
                     show_inferred=show_inferred)
    return layout, scale


def paint_pages(device, lesson: Lesson, show_inferred: bool = True) -> int:
    """Paint every page onto a QPagedPaintDevice. Returns the page count."""
    painter = QPainter(device)
    try:
        rect = device.pageLayout().paintRectPixels(device.resolution())
        layout, scale = paged_layout(lesson, rect.width(), rect.height(), show_inferred)
        for index, page in enumerate(layout.pages):
            if index:
                device.newPage()
            LayoutPainter(painter, scale, PRINT_PALETTE).draw_page(page)
        _paint_handout(painter, lesson, layout, scale, rect)
        return len(layout.pages)
    finally:
        painter.end()


def _paint_handout(painter: QPainter, lesson: Lesson, layout: Layout, scale: float, rect) -> None:
    """Instructions, hand positions and tips under the last system, if they fit."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QFont

    from ...teach.score import HAND_NAMES, HANDS

    last = layout.pages[-1].systems[-1] if layout.pages and layout.pages[-1].systems else None
    if last is None:
        return
    top = (last.bottom + 4.0) * scale
    if rect.height() - top < 12 * scale:
        return
    sections: list[tuple[str, str]] = []
    if lesson.instructions:
        sections.append(("Instructions", lesson.instructions))
    positions = [f"{HAND_NAMES[h]}: {lesson.position_label(h)}" for h in HANDS if lesson.hand_notes(h)]
    if positions:
        sections.append(("Hand positions", "\n".join(positions)))
    if lesson.tips:
        sections.append(("Tips", "\n".join(f"• {t}" for t in lesson.tips)))
    x = MARGIN_SP * scale
    width = rect.width() - 2 * MARGIN_SP * scale
    y = top
    head = QFont("Segoe UI")
    head.setPixelSize(int(1.5 * scale))
    head.setBold(True)
    body = QFont("Segoe UI")
    body.setPixelSize(int(1.3 * scale))
    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
    painter.setPen(QColor("#111111"))
    for title, text in sections:
        painter.setFont(head)
        painter.drawText(QRectF(x, y, width, 2.2 * scale), flags, title)
        y += 2.0 * scale
        painter.setFont(body)
        needed = painter.boundingRect(QRectF(x, y, width, rect.height() - y), flags, text)
        if y + needed.height() > rect.height():
            break
        painter.drawText(QRectF(x, y, width, needed.height()), flags, text)
        y += needed.height() + 1.6 * scale


def _a4_layout() -> QPageLayout:
    return QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                       QMarginsF(14, 14, 14, 14), QPageLayout.Unit.Millimeter)


def write_pdf(lesson: Lesson, path: str | Path, show_inferred: bool = True) -> tuple[Path, int]:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    writer.setPageLayout(_a4_layout())
    writer.setResolution(300)
    writer.setTitle(lesson.title)
    writer.setCreator("Retro Audio Workstation — Piano Tutor")
    pages = paint_pages(writer, lesson, show_inferred)
    return path, pages


def print_lesson(lesson: Lesson, parent=None, show_inferred: bool = True) -> bool:
    """Open the system print dialog and print. Returns False if unavailable or cancelled."""
    try:
        from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
    except ImportError:
        return False
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageLayout(_a4_layout())
    printer.setDocName(lesson.title)
    dialog = QPrintDialog(printer, parent)
    if dialog.exec() != QPrintDialog.DialogCode.Accepted:
        return False
    paint_pages(printer, lesson, show_inferred)
    return True
