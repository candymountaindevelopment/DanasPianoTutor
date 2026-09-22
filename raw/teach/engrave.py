"""Lay a lesson out as a grand staff: positions for every notehead, stem,
rest, tie, clef and finger number, in staff-space units.

This is the part of sheet music a beginner actually needs — single notes and
simple chords, standard note values, ties across bar lines, accidentals and
fingering — rather than a general engraver. The output is geometry only; the
screen view and the PDF exporter draw it with the same painter, so what the
student practises from is what they print.

Units: one staff space (the gap between two staff lines) is 1.0. A system is
a treble staff over a bass staff; a page is a stack of systems. All y values
grow downwards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..export.sheet import Piece, allocate_voices, voice_measure_pieces
from .score import LEFT, RIGHT, Lesson, diatonic_index, key_alterations, spell
from .sheet import lesson_events

# ------------------------------------------------------------ geometry

STAFF_HEIGHT = 4.0            # four spaces between five lines
TREBLE_TOP = 5.0              # y of the treble staff's top line inside a system
STAFF_GAP = 7.0               # bass top line sits this far below the treble top line + height
BASS_TOP = TREBLE_TOP + STAFF_HEIGHT + STAFF_GAP
SYSTEM_HEIGHT = BASS_TOP + STAFF_HEIGHT + 5.5
SYSTEM_SPACING = 2.0
HEADER_HEIGHT = 9.0           # title block on the first page
NOTEHEAD_W = 1.25
STEM_LENGTH = 3.5
FLAG_COUNT = {"eighth": 1, "16th": 2, "32nd": 3}
FILLED = {"quarter", "eighth", "16th", "32nd"}

# Diatonic index of each staff's bottom line: E4 for treble, G2 for bass.
TREBLE_BOTTOM = diatonic_index("E", 4)
BASS_BOTTOM = diatonic_index("G", 2)
# Where the sharps and flats of a key signature sit, as diatonic indices.
TREBLE_SHARPS = [diatonic_index(*p) for p in (("F", 5), ("C", 5), ("G", 5), ("D", 5), ("A", 4), ("E", 5), ("B", 4))]
TREBLE_FLATS = [diatonic_index(*p) for p in (("B", 4), ("E", 5), ("A", 4), ("D", 5), ("G", 4), ("C", 5), ("F", 4))]
BASS_SHARPS = [i - 14 for i in TREBLE_SHARPS]  # same staff positions, one line lower
BASS_FLATS = [i - 14 for i in TREBLE_FLATS]
CLEF_WIDTH = 4.2

ACCIDENTAL_GLYPH = {1: "♯", -1: "♭", 0: "♮"}


@dataclass
class Notehead:
    x: float
    y: float
    midi: int
    filled: bool
    hand: str
    start: int                 # division of the tied group's first piece
    end: int                   # division where the tied group ends
    piece_start: int           # this piece's own start (for highlighting)
    piece_end: int
    accidental: str | None = None
    finger: int | None = None
    finger_written: bool = True
    dots: int = 0
    ledger: list[float] = field(default_factory=list)   # y of each ledger line
    tie_stop: bool = False
    on_line: bool = False      # sits on a staff/ledger line (dots then go in the space above)


@dataclass
class Stem:
    x: float
    y0: float
    y1: float                  # y1 is the free end (tip)
    up: bool
    flags: int = 0


@dataclass
class Rest:
    x: float
    y: float
    type_name: str
    dots: int = 0
    whole_measure: bool = False


@dataclass
class Tie:
    x0: float
    y0: float
    x1: float
    y1: float
    below: bool


@dataclass
class Finger:
    x: float
    y: float
    number: int
    written: bool              # False for fingers inferred from the hand position
    hand: str


@dataclass
class Text:
    x: float
    y: float
    text: str
    size: float = 1.0          # in staff spaces (cap height-ish)
    align: str = "left"        # left | center | right
    style: str = "normal"      # normal | bold | italic | small


@dataclass
class Symbol:
    """A music-font glyph: clef, accidental in a key signature, time digit."""

    x: float
    y: float
    kind: str                  # treble_clef | bass_clef | sharp | flat | natural | digit
    text: str = ""
    size: float = 1.0


@dataclass
class Measure:
    number: int                # 1-based
    x0: float
    x1: float
    start: int
    end: int
    slots: list[tuple[int, float]] = field(default_factory=list)   # (division, x) onsets

    def x_at(self, division: float) -> float:
        """Cursor position inside the measure, linear between onsets."""
        pts = list(self.slots) + [(self.end, self.x1)]
        if not pts or division <= pts[0][0]:
            return pts[0][1] if pts else self.x0
        for (d0, x0), (d1, x1) in zip(pts, pts[1:]):
            if d0 <= division < d1:
                t = (division - d0) / max(1, d1 - d0)
                return x0 + (x1 - x0) * t
        return self.x1


@dataclass
class System:
    y: float                   # y of the system's top inside the page
    x0: float
    x1: float
    measures: list[Measure] = field(default_factory=list)
    noteheads: list[Notehead] = field(default_factory=list)
    stems: list[Stem] = field(default_factory=list)
    rests: list[Rest] = field(default_factory=list)
    ties: list[Tie] = field(default_factory=list)
    fingers: list[Finger] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    barlines: list[float] = field(default_factory=list)
    final: bool = False

    @property
    def start(self) -> int:
        return self.measures[0].start if self.measures else 0

    @property
    def end(self) -> int:
        return self.measures[-1].end if self.measures else 0

    @property
    def treble_top(self) -> float:
        return self.y + TREBLE_TOP

    @property
    def bass_top(self) -> float:
        return self.y + BASS_TOP

    @property
    def bottom(self) -> float:
        return self.y + SYSTEM_HEIGHT

    def contains(self, division: float) -> bool:
        return self.start <= division < self.end

    def x_at(self, division: float) -> float:
        for m in self.measures:
            if m.start <= division < m.end:
                return m.x_at(division)
        return self.x1 if division >= self.end else self.x0


@dataclass
class Page:
    width: float
    height: float
    systems: list[System] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)


@dataclass
class Layout:
    pages: list[Page]
    warnings: list[str] = field(default_factory=list)

    @property
    def systems(self) -> list[System]:
        return [s for p in self.pages for s in p.systems]

    def system_at(self, division: float) -> System | None:
        for s in self.systems:
            if s.contains(division):
                return s
        return self.systems[-1] if self.systems and division >= self.systems[-1].end else None


# ----------------------------------------------------------- helpers


def staff_y(staff_top: float, index: int, bottom_index: int) -> float:
    """y of a diatonic index on a staff whose bottom line has `bottom_index`."""
    return staff_top + STAFF_HEIGHT - (index - bottom_index) * 0.5


def ledger_lines(staff_top: float, index: int, bottom_index: int) -> list[float]:
    out = []
    top_index = bottom_index + 8
    if index <= bottom_index - 2:
        i = bottom_index - 2
        while i >= index:
            out.append(staff_y(staff_top, i, bottom_index))
            i -= 2
    elif index >= top_index + 2:
        i = top_index + 2
        while i <= index:
            out.append(staff_y(staff_top, i, bottom_index))
            i += 2
    return out


def slot_width(divs: int) -> float:
    """Horizontal room for a note of a given length. Longer notes get more,
    but far less than proportionally, like printed music."""
    return max(2.4, 1.35 * math.sqrt(max(1, divs) / 2.0) + 1.0)


def _hand_of(staff: int) -> str:
    return RIGHT if staff == 1 else LEFT


# ------------------------------------------------------------ layout


class Engraver:
    def __init__(self, lesson: Lesson, show_inferred: bool = True) -> None:
        self.lesson = lesson
        self.show_inferred = show_inferred
        self.warnings: list[str] = []
        events = lesson_events(lesson, include_inferred=False)
        self.inferred = {(n.start, n.midi, n.hand): n.inferred for n in lesson.notes}
        self.spellings = lesson.spellings
        self.voices = {
            1: allocate_voices([e for e in events if e.staff == 1], self.warnings, "right hand") or [[]],
            2: allocate_voices([e for e in events if e.staff == 2], self.warnings, "left hand") or [[]],
        }
        for staff in (1, 2):
            if len(self.voices[staff]) > 2:
                self.warnings.append("more than two voices in one hand; extra voices are drawn without stems")
        # (hand, midi) -> finger of the last note drawn, to skip repeating an
        # inferred number on a repeated note the way beginner books do.
        self._last_finger: dict[tuple[str, int], int | None] = {}
        m = lesson.measure_divisions
        self.measure_pieces: dict[tuple[int, int, int], list[Piece]] = {}
        for index in range(lesson.measures):
            for staff in (1, 2):
                for v, items in enumerate(self.voices[staff]):
                    self.measure_pieces[(index, staff, v)] = voice_measure_pieces(items, index * m, (index + 1) * m)

    # ---------------------------------------------------------- widths

    def measure_slots(self, index: int) -> list[tuple[int, float, bool]]:
        """(division, width, needs_accidental_room) for every onset in a measure."""
        m = self.lesson.measure_divisions
        onsets: dict[int, int] = {}
        for staff in (1, 2):
            for v in range(len(self.voices[staff])):
                for piece in self.measure_pieces[(index, staff, v)]:
                    if piece.is_rest and piece.divs == m:
                        continue
                    onsets[piece.start] = min(onsets.get(piece.start, 10**9), piece.divs)
        if not onsets:
            return [(index * m, 4.0, False)]
        return [(d, slot_width(divs), False) for d, divs in sorted(onsets.items())]

    def measure_min_width(self, index: int) -> float:
        return 1.2 + sum(w for _, w, _ in self.measure_slots(index)) + 0.4

    def prefix_width(self, first_system: bool) -> float:
        fifths = abs(self.lesson.fifths)
        return 0.5 + CLEF_WIDTH + fifths * 0.95 + (2.8 if first_system else 0.0) + 0.8

    # --------------------------------------------------------- systems

    def layout(self, width: float, page_height: float | None = None, margin: float = 2.0,
               header: bool = True) -> Layout:
        lesson = self.lesson
        x0, x1 = margin, width - margin
        usable = x1 - x0
        widths = [self.measure_min_width(i) for i in range(lesson.measures)]

        # Greedy line breaking on minimum widths.
        rows: list[list[int]] = []
        row: list[int] = []
        used = 0.0
        for i, w in enumerate(widths):
            prefix = self.prefix_width(first_system=not rows)
            if row and used + w > usable - prefix:
                rows.append(row)
                row, used = [], 0.0
            row.append(i)
            used += w
        if row:
            rows.append(row)

        pages: list[Page] = []
        y = HEADER_HEIGHT + margin if header else margin
        page = Page(width, page_height or 0.0)
        if header:
            self._header(page, width, margin)
        for r, indices in enumerate(rows):
            if page_height is not None and y + SYSTEM_HEIGHT > page_height - margin and page.systems:
                pages.append(page)
                page = Page(width, page_height)
                y = margin
            system = self._system(indices, y, x0, x1, first=(r == 0), last=(r == len(rows) - 1), widths=widths)
            page.systems.append(system)
            y += SYSTEM_HEIGHT + SYSTEM_SPACING
        if page_height is None:
            page.height = y + margin
        pages.append(page)
        return Layout(pages, list(self.warnings))

    def _header(self, page: Page, width: float, margin: float) -> None:
        lesson = self.lesson
        page.texts.append(Text(width / 2, margin + 2.6, lesson.title, 2.6, "center", "bold"))
        sub = []
        if lesson.composer:
            sub.append(lesson.composer)
        sub.append(f"Level {lesson.level}")
        page.texts.append(Text(width - margin, margin + 5.2, " · ".join(sub), 1.1, "right", "italic"))
        page.texts.append(Text(margin, margin + 5.2, f"♩ = {lesson.tempo:g}", 1.2, "left", "normal"))

    def _system(self, indices: list[int], y: float, x0: float, x1: float,
                first: bool, last: bool, widths: list[float]) -> System:
        lesson = self.lesson
        system = System(y, x0, x1, final=last)
        prefix = self.prefix_width(first)
        self._prefix_symbols(system, first)

        avail = (x1 - x0) - prefix
        natural = sum(widths[i] for i in indices)
        # Stretch to fill the line, except a short final line which stays natural.
        stretch = avail / natural if (natural > 0 and (not last or natural > 0.6 * avail)) else 1.0
        stretch = max(1.0, stretch)

        x = x0 + prefix
        m_divs = lesson.measure_divisions
        for i in indices:
            w = widths[i] * stretch
            measure = Measure(i + 1, x, x + w, i * m_divs, (i + 1) * m_divs)
            slots = self.measure_slots(i)
            cursor = x + 1.2 * stretch
            for d, sw, _ in slots:
                measure.slots.append((d, cursor + 0.45))
                cursor += sw * stretch
            system.measures.append(measure)
            x += w
        if not first:
            system.texts.append(Text(system.x0, system.treble_top - 2.2,
                                     str(system.measures[0].number), 0.9, "left", "small"))
        system.barlines = [m.x1 for m in system.measures]
        self._fill(system)
        return system

    def _prefix_symbols(self, system: System, first: bool) -> None:
        lesson = self.lesson
        x = system.x0 + 0.5
        t_top, b_top = system.treble_top, system.bass_top
        system.symbols.append(Symbol(x, t_top, "treble_clef"))
        system.symbols.append(Symbol(x, b_top, "bass_clef"))
        x += CLEF_WIDTH
        f = lesson.fifths
        if f:
            kind = "sharp" if f > 0 else "flat"
            t_pos = (TREBLE_SHARPS if f > 0 else TREBLE_FLATS)[: abs(f)]
            b_pos = (BASS_SHARPS if f > 0 else BASS_FLATS)[: abs(f)]
            for i, (ti, bi) in enumerate(zip(t_pos, b_pos)):
                system.symbols.append(Symbol(x + i * 0.95, staff_y(t_top, ti, TREBLE_BOTTOM), kind))
                system.symbols.append(Symbol(x + i * 0.95, staff_y(b_top, bi, BASS_BOTTOM), kind))
            x += abs(f) * 0.95 + 0.3
        if first:
            num, den = lesson.time_signature
            for top in (t_top, b_top):
                system.symbols.append(Symbol(x, top + 1.0, "digit", str(num), 2.0))
                system.symbols.append(Symbol(x, top + 3.0, "digit", str(den), 2.0))

    # ----------------------------------------------------------- notes

    def _fill(self, system: System) -> None:
        lesson = self.lesson
        key_alts = key_alterations(lesson.fifths)
        # Ties that started in an earlier measure of this system (or the previous one).
        pending: dict[tuple[int, int, int], Notehead] = {}
        for measure in system.measures:
            index = measure.number - 1
            for staff in (1, 2):
                staff_top = system.treble_top if staff == 1 else system.bass_top
                bottom_index = TREBLE_BOTTOM if staff == 1 else BASS_BOTTOM
                accidentals: dict[tuple[str, int], int] = {}
                for v, _items in enumerate(self.voices[staff]):
                    pieces = self.measure_pieces[(index, staff, v)]
                    slot_x = dict(measure.slots)
                    for piece in pieces:
                        if piece.is_rest:
                            if piece.divs == lesson.measure_divisions and len(pieces) == 1:
                                system.rests.append(Rest((measure.x0 + measure.x1) / 2, staff_top + 1.0,
                                                         "whole", 0, whole_measure=True))
                            else:
                                x = slot_x.get(piece.start, measure.x_at(piece.start))
                                y = staff_top + 2.0
                                if len(self.voices[staff]) > 1:
                                    y += -1.0 if v == 0 else 1.0
                                system.rests.append(Rest(x, y, piece.type_name, piece.dots))
                            continue
                        x = slot_x.get(piece.start, measure.x_at(piece.start))
                        self._chord(system, piece, x, staff, staff_top, bottom_index, v,
                                    accidentals, key_alts, pending, measure)

    def _chord(self, system: System, piece: Piece, x: float, staff: int, staff_top: float,
               bottom_index: int, voice: int, accidentals: dict, key_alts: dict,
               pending: dict, measure: Measure) -> None:
        lesson = self.lesson
        hand = _hand_of(staff)
        heads: list[Notehead] = []
        indices: list[int] = []
        for midi in piece.midis:
            letter, alter, octave = spell(midi, lesson.fifths, self.spellings.get(midi))
            idx = diatonic_index(letter, octave)
            indices.append(idx)
            y = staff_y(staff_top, idx, bottom_index)
            shown = accidentals.get((letter, octave), key_alts.get(letter, 0))
            accidental = None
            if shown != alter and not piece.tie_stop:
                accidental = ACCIDENTAL_GLYPH[alter]
                accidentals[(letter, octave)] = alter
            elif shown != alter:
                accidentals[(letter, octave)] = alter
            finger = piece.fingers.get(midi)
            written = finger is not None
            if finger is None and self.show_inferred and not piece.tie_stop:
                finger = self.inferred.get((piece.start, midi, hand))
                if finger is not None and self._last_finger.get((hand, midi)) == finger:
                    finger = None   # same key, same finger as last time: no need to repeat it
            if not piece.tie_stop:
                shown = piece.fingers.get(midi) or self.inferred.get((piece.start, midi, hand))
                self._last_finger[(hand, midi)] = shown
            head = Notehead(
                x, y, midi, piece.type_name in FILLED, hand,
                piece.start, piece.start + piece.divs, piece.start, piece.start + piece.divs,
                accidental, finger, written, piece.dots,
                ledger_lines(staff_top, idx, bottom_index), piece.tie_stop,
                on_line=(idx - bottom_index) % 2 == 0,
            )
            heads.append(head)
        if not heads:
            return
        # Adjacent seconds in a chord: shift every other head to the right.
        for a, b in zip(heads, heads[1:]):
            if abs(b.y - a.y) < 0.55 and abs(a.x - x) < 1e-6:
                b.x = x + NOTEHEAD_W * 0.9
        system.noteheads.extend(heads)

        # Stem: direction from the note farthest from the middle line; second
        # voices are forced apart (voice 1 up, voice 2 down).
        middle = staff_y(staff_top, bottom_index + 4, bottom_index)
        top_y = min(h.y for h in heads)
        bottom_y = max(h.y for h in heads)
        if len(self.voices[staff]) > 1 and voice < 2:
            up = voice == 0
        else:
            up = (bottom_y - middle) >= (middle - top_y)
        has_stem = piece.type_name != "whole"
        stem_tip = None
        if has_stem:
            flags = FLAG_COUNT.get(piece.type_name, 0)
            if up:
                sx = x + NOTEHEAD_W / 2 - 0.06
                tip = top_y - STEM_LENGTH - (0.5 * max(0, flags - 1))
                system.stems.append(Stem(sx, bottom_y, tip, True, flags))
            else:
                sx = x - NOTEHEAD_W / 2 + 0.06
                tip = bottom_y + STEM_LENGTH + (0.5 * max(0, flags - 1))
                system.stems.append(Stem(sx, top_y, tip, False, flags))
            stem_tip = tip

        # Fingering: right hand above the treble staff, left hand below the bass.
        fingered = [h for h in heads if h.finger and not h.tie_stop]
        if fingered:
            if hand == RIGHT:
                limit = min(staff_top - 1.4, top_y - 1.4, (stem_tip - 0.9) if (stem_tip is not None and up) else 99.0)
                # Nearest the staff is the lowest note's finger; higher notes stack upwards.
                for i, h in enumerate(sorted(fingered, key=lambda h: -h.y)):
                    system.fingers.append(Finger(h.x, limit - i * 1.2, h.finger, h.finger_written, hand))
            else:
                base = max(staff_top + STAFF_HEIGHT + 1.9, bottom_y + 1.7,
                           (stem_tip + 1.3) if (stem_tip is not None and not up) else -99.0)
                # Nearest the staff is the highest note's finger; lower notes stack downwards.
                for i, h in enumerate(sorted(fingered, key=lambda h: h.y)):
                    system.fingers.append(Finger(h.x, base + i * 1.2, h.finger, h.finger_written, hand))

        # Ties.
        for h in heads:
            key = (staff, voice, h.midi)
            if h.tie_stop and key in pending:
                a = pending.pop(key)
                below = not up if has_stem else h.y >= middle
                system.ties.append(Tie(a.x + 0.5, a.y, h.x - 0.5, h.y, below))
            elif h.tie_stop:
                # Continues from the previous system: a short tie into this note.
                below = not up if has_stem else h.y >= middle
                system.ties.append(Tie(measure.x0 - 0.2, h.y, h.x - 0.5, h.y, below))
            if piece.tie_start:
                pending[key] = h
        # Ties left open at the end of the system trail off the right edge.
        if measure is system.measures[-1] and piece.tie_start:
            for h in heads:
                key = (staff, voice, h.midi)
                if pending.get(key) is h and h.piece_end >= measure.end:
                    below = not up if has_stem else h.y >= middle
                    system.ties.append(Tie(h.x + 0.5, h.y, system.x1 + 0.3, h.y, below))
                    pending.pop(key, None)


def engrave(lesson: Lesson, width: float, page_height: float | None = None,
            margin: float = 2.0, header: bool = True, show_inferred: bool = True) -> Layout:
    """Lay out a lesson. `width`/`page_height` are in staff spaces; a `None`
    height gives one tall page (for scrolling on screen)."""
    return Engraver(lesson, show_inferred).layout(width, page_height, margin, header)
