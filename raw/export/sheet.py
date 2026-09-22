"""Sheet-music export: patterns and chords to MusicXML for piano.

MusicXML is the interchange format every notation program reads — MuseScore
(free), Sibelius, Finale, Dorico — so exporting it is what makes RAW's
structured note data printable. It is plain XML, so this needs no libraries.

The piano mapping:

* One part, a grand staff: treble (staff 1) and bass (staff 2), split at
  middle C by default.
* Every pitched event from every track goes onto that staff pair; tracks are
  a sequencer concept, not a notation one.
* Overlapping notes are allocated to voices, up to four per staff. Notes that
  start together with the same length become a chord.
* Durations longer than one note value, or crossing a bar line, are written
  as tied notes. Gaps become rests.
* Events without a pitch (drums, slot triggers) are skipped — they are not
  piano notes.

Swing is a performance nuance and is not notated. Triplet step divisions are
not representable on this straight grid and are rounded with a warning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from ..patterns.notes import parse_pitch_token

DIVISIONS = 8            # per quarter note: 32nd-note resolution
WHOLE = DIVISIONS * 4
MAX_VOICES_PER_STAFF = 4
DEFAULT_SPLIT = 60       # middle C: below goes to the bass clef

STEP_NAMES = ("C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B")
STEP_ALTER = (0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0)

# (divisions, MusicXML type, dots). Dotted values first so an exact dotted
# length is written as one note rather than two tied ones.
NOTE_TYPES = (
    (32, "whole", 0),
    (24, "half", 1),
    (16, "half", 0),
    (12, "quarter", 1),
    (8, "quarter", 0),
    (6, "eighth", 1),
    (4, "eighth", 0),
    (3, "16th", 1),
    (2, "16th", 0),
    (1, "32nd", 0),
)


@dataclass
class NoteEvent:
    start: int
    duration: int
    midi: int
    velocity: float = 0.8
    finger: int | None = None      # piano fingering 1-5, written above/below the note
    staff: int | None = None       # force a staff (1 treble, 2 bass) instead of splitting by pitch


@dataclass
class Item:
    """One notated thing in a voice: a chord (one or more pitches) with a length."""

    start: int
    duration: int
    midis: list[int] = field(default_factory=list)
    fingers: dict[int, int] = field(default_factory=dict)   # midi -> finger


@dataclass
class Piece:
    """A note or rest as it appears inside one measure, after ties are resolved."""

    start: int                 # absolute division
    divs: int
    type_name: str             # "quarter", "eighth", ...
    dots: int
    midis: list[int] = field(default_factory=list)   # empty for a rest
    fingers: dict[int, int] = field(default_factory=dict)
    tie_start: bool = False
    tie_stop: bool = False

    @property
    def is_rest(self) -> bool:
        return not self.midis


@dataclass
class SheetResult:
    xml: str
    warnings: list[str] = field(default_factory=list)
    note_count: int = 0
    measures: int = 0


# ----------------------------------------------------------------- durations


def musical_to_divisions(spec: str, warnings: list[str] | None = None) -> int:
    """'1/8' -> 4, '1/4.' -> 12. Triplets do not fit the grid and are rounded."""
    s = str(spec).strip().lower()
    dotted = s.endswith(".")
    triplet = s.endswith("t")
    s = s.rstrip(".t")
    try:
        if "/" in s:
            num, den = s.split("/")
            frac = float(num) / float(den)
        else:
            frac = float(s)
    except (ValueError, ZeroDivisionError):
        frac = 0.25
    divs = frac * WHOLE
    if dotted:
        divs *= 1.5
    if triplet:
        divs *= 2.0 / 3.0
        if warnings is not None:
            warnings.append(f"triplet division {spec!r} rounded to the straight grid")
    return max(1, int(round(divs)))


def decompose(duration: int) -> list[tuple[int, str, int]]:
    """Split a length into notatable values, largest first."""
    out = []
    remaining = int(duration)
    while remaining > 0:
        for divs, name, dots in NOTE_TYPES:
            if divs <= remaining:
                out.append((divs, name, dots))
                remaining -= divs
                break
    return out


def measure_divisions(time_signature) -> int:
    numerator, denominator = int(time_signature[0]), int(time_signature[1])
    return max(1, numerator * WHOLE // max(1, denominator))


# ------------------------------------------------------------------ events


def pattern_events(pattern, warnings: list[str]) -> tuple[list[NoteEvent], int]:
    """Pitched events from every track, in divisions. Returns (events, length)."""
    step_divs = musical_to_divisions(getattr(pattern, "step_division", "1/16") or "1/16", warnings)
    events: list[NoteEvent] = []
    skipped = 0
    for track in pattern.tracks:
        for event in track.get("events", []):
            note = event.get("note")
            if note is None:
                skipped += 1
                continue
            try:
                midi = int(round(parse_pitch_token(note)))
            except Exception:
                warnings.append(f"unreadable note {note!r} skipped")
                continue
            start = int(round(float(event.get("step", 0)) * step_divs))
            length = event.get("length")
            if length is None:
                duration = step_divs
            elif isinstance(length, str):
                duration = musical_to_divisions(length, warnings)
            else:
                duration = max(1, int(round(float(length) * step_divs)))
            events.append(NoteEvent(start, duration, midi, float(event.get("velocity", 0.8))))
    if skipped:
        warnings.append(f"{skipped} unpitched event(s) (drums / triggers) left off the piano part")
    return events, int(pattern.steps) * step_divs


def allocate_voices(events: list[NoteEvent], warnings: list[str], label: str) -> list[list[Item]]:
    """Give overlapping notes their own voices; co-starting equal lengths chord."""
    voices: list[dict] = []
    for ev in sorted(events, key=lambda e: (e.start, -e.duration, e.midi)):
        placed = False
        for voice in voices:
            last = voice["items"][-1] if voice["items"] else None
            if last is not None and last.start == ev.start and last.duration == ev.duration:
                if ev.midi not in last.midis:
                    last.midis.append(ev.midi)
                if ev.finger:
                    last.fingers[ev.midi] = int(ev.finger)
                placed = True
                break
            if voice["busy_until"] <= ev.start:
                voice["items"].append(_item(ev))
                voice["busy_until"] = ev.start + ev.duration
                placed = True
                break
        if placed:
            continue
        if len(voices) >= MAX_VOICES_PER_STAFF:
            warnings.append(
                f"{label}: more than {MAX_VOICES_PER_STAFF} simultaneous voices; a note was dropped"
            )
            continue
        voices.append({"items": [_item(ev)], "busy_until": ev.start + ev.duration})
    return [v["items"] for v in voices]


def _item(ev: NoteEvent) -> Item:
    return Item(ev.start, ev.duration, [ev.midi], {ev.midi: int(ev.finger)} if ev.finger else {})


def voice_measure_pieces(items: list[Item], m_start: int, m_end: int) -> list[Piece]:
    """One voice's content for one measure as notes and rests, with ties.

    Items crossing the bar line are cut at it and tied; lengths that are not a
    single note value are split into tied standard values; gaps become rests.
    Shared by the MusicXML writer and the on-screen engraver so both agree.
    """
    out: list[Piece] = []
    cursor = m_start

    def rests(until: int) -> None:
        nonlocal cursor
        for divs, name, dots in decompose(until - cursor):
            out.append(Piece(cursor, divs, name, dots))
            cursor += divs

    for item in items:
        item_end = item.start + item.duration
        if item_end <= m_start or item.start >= m_end:
            continue
        chunk_start = max(item.start, m_start)
        chunk_end = min(item_end, m_end)
        if chunk_start > cursor:
            rests(chunk_start)
        pieces = decompose(chunk_end - chunk_start)
        continues_before = item.start < m_start
        continues_after = item_end > m_end
        for index, (divs, name, dots) in enumerate(pieces):
            out.append(Piece(
                cursor, divs, name, dots, sorted(item.midis), dict(item.fingers),
                tie_start=continues_after or index < len(pieces) - 1,
                tie_stop=continues_before or index > 0,
            ))
            cursor += divs
    if cursor < m_end:
        rests(m_end)
    return out


# ------------------------------------------------------------------- xml


def spell_pitch(midi: int, fifths: int = 0, prefer: int | None = None) -> tuple[str, int, int]:
    """(step, alter, octave). Sharp keys spell chromatics with sharps, flat keys
    with flats; `prefer` (+1/-1) is an accidental the author wrote explicitly."""
    if fifths == 0 and prefer is None:
        step = STEP_NAMES[midi % 12]
        alter = STEP_ALTER[midi % 12]
        return step, alter, (midi - alter) // 12 - 1
    from ..teach.score import spell  # lazy: teach imports this module

    return spell(midi, fifths, prefer)


def _pitch_xml(midi: int, fifths: int = 0, prefer: int | None = None) -> str:
    step, alter, octave = spell_pitch(midi, fifths, prefer)
    alter_xml = f"<alter>{alter}</alter>" if alter else ""
    return f"<pitch><step>{step}</step>{alter_xml}<octave>{octave}</octave></pitch>"


def _note_xml(midis, divs: int, name: str, dots: int, voice: int, staff: int,
              tie_start: bool, tie_stop: bool, fingers: dict | None = None,
              fifths: int = 0, spellings: dict | None = None) -> str:
    out = []
    fingers = fingers or {}
    spellings = spellings or {}
    for index, midi in enumerate(sorted(midis)):
        parts = ["<note>"]
        if index:
            parts.append("<chord/>")
        parts.append(_pitch_xml(midi, fifths, spellings.get(midi)))
        parts.append(f"<duration>{divs}</duration>")
        if tie_stop:
            parts.append('<tie type="stop"/>')
        if tie_start:
            parts.append('<tie type="start"/>')
        parts.append(f"<voice>{voice}</voice><type>{name}</type>")
        parts.append("<dot/>" * dots)
        parts.append(f"<staff>{staff}</staff>")
        notations = ""
        if tie_stop:
            notations += '<tied type="stop"/>'
        if tie_start:
            notations += '<tied type="start"/>'
        finger = fingers.get(midi)
        if finger and not tie_stop:
            placement = "above" if staff == 1 else "below"
            notations += f'<technical><fingering placement="{placement}">{int(finger)}</fingering></technical>'
        if notations:
            parts.append(f"<notations>{notations}</notations>")
        parts.append("</note>")
        out.append("".join(parts))
    return "".join(out)


def _rest_xml(divs: int, name: str, dots: int, voice: int, staff: int) -> str:
    return (f"<note><rest/><duration>{divs}</duration><voice>{voice}</voice>"
            f"<type>{name}</type>{'<dot/>' * dots}<staff>{staff}</staff></note>")


def _voice_measure_xml(items: list[Item], m_start: int, m_end: int, voice: int, staff: int,
                       fifths: int = 0, spellings: dict | None = None) -> str:
    """One voice's content for one measure: rests, notes, ties at the edges."""
    out = []
    for piece in voice_measure_pieces(items, m_start, m_end):
        if piece.is_rest:
            out.append(_rest_xml(piece.divs, piece.type_name, piece.dots, voice, staff))
        else:
            out.append(_note_xml(piece.midis, piece.divs, piece.type_name, piece.dots, voice, staff,
                                 piece.tie_start, piece.tie_stop, piece.fingers, fifths, spellings))
    return "".join(out)


def build_musicxml(
    events: list[NoteEvent],
    title: str,
    tempo: float,
    time_signature=(4, 4),
    minimum_length: int = 0,
    split: int = DEFAULT_SPLIT,
    warnings: list[str] | None = None,
    fifths: int = 0,
    composer: str = "",
    spellings: dict | None = None,
) -> SheetResult:
    warnings = warnings if warnings is not None else []
    m_divs = measure_divisions(time_signature)
    total = max(minimum_length, max((e.start + e.duration for e in events), default=0))
    measures = max(1, math.ceil(total / m_divs))

    def staff_of(e: NoteEvent) -> int:
        return int(e.staff) if e.staff in (1, 2) else (1 if e.midi >= split else 2)

    treble = allocate_voices([e for e in events if staff_of(e) == 1], warnings, "treble")
    bass = allocate_voices([e for e in events if staff_of(e) == 2], warnings, "bass")
    # A grand staff always shows both staves, even if one is silent.
    staves = [(1, treble or [[]]), (2, bass or [[]])]

    body = []
    for m in range(measures):
        m_start, m_end = m * m_divs, (m + 1) * m_divs
        chunks = [f'<measure number="{m + 1}">']
        if m == 0:
            chunks.append(
                f"<attributes><divisions>{DIVISIONS}</divisions>"
                f"<key><fifths>{int(fifths)}</fifths></key>"
                f"<time><beats>{time_signature[0]}</beats><beat-type>{time_signature[1]}</beat-type></time>"
                f"<staves>2</staves>"
                f'<clef number="1"><sign>G</sign><line>2</line></clef>'
                f'<clef number="2"><sign>F</sign><line>4</line></clef>'
                f"</attributes>"
                f'<direction placement="above"><direction-type><metronome>'
                f"<beat-unit>quarter</beat-unit><per-minute>{tempo:g}</per-minute>"
                f'</metronome></direction-type><sound tempo="{tempo:g}"/></direction>'
            )
        first = True
        for staff, voices in staves:
            for v_index, items in enumerate(voices):
                if not first:
                    chunks.append(f"<backup><duration>{m_divs}</duration></backup>")
                first = False
                voice_number = (staff - 1) * MAX_VOICES_PER_STAFF + v_index + 1
                chunks.append(_voice_measure_xml(items, m_start, m_end, voice_number, staff, fifths, spellings))
        chunks.append("</measure>")
        body.append("".join(chunks))

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">\n'
        '<score-partwise version="4.0">'
        f"<work><work-title>{escape(title)}</work-title></work>"
        "<identification>"
        + (f'<creator type="composer">{escape(composer)}</creator>' if composer else "")
        + "<encoding><software>Retro Audio Workstation</software></encoding></identification>"
        '<part-list><score-part id="P1"><part-name>Piano</part-name>'
        '<score-instrument id="P1-I1"><instrument-name>Piano</instrument-name></score-instrument>'
        '<midi-instrument id="P1-I1"><midi-channel>1</midi-channel><midi-program>1</midi-program></midi-instrument>'
        "</score-part></part-list>"
        '<part id="P1">' + "".join(body) + "</part>"
        "</score-partwise>\n"
    )
    return SheetResult(xml, warnings, note_count=len(events), measures=measures)


# ----------------------------------------------------------------- entry points


def pretty_title(name: str) -> str:
    return " ".join(w.capitalize() for w in str(name).replace("_", " ").split()) or "Untitled"


def pattern_to_musicxml(pattern, project, split: int = DEFAULT_SPLIT) -> SheetResult:
    warnings: list[str] = []
    events, length = pattern_events(pattern, warnings)
    if getattr(pattern, "swing", 0.0):
        warnings.append("swing is a performance feel and is not notated")
    tempo = float(pattern.tempo or project.settings.tempo)
    return build_musicxml(events, pretty_title(pattern.name), tempo,
                          project.settings.time_signature, length, split, warnings)


def chord_to_musicxml(midi_notes, title: str, project, split: int = DEFAULT_SPLIT) -> SheetResult:
    """A single chord as one whole-note bar."""
    m_divs = measure_divisions(project.settings.time_signature)
    events = [NoteEvent(0, m_divs, int(round(n))) for n in sorted(set(midi_notes))]
    return build_musicxml(events, title, float(project.settings.tempo),
                          project.settings.time_signature, m_divs, split)


def write_musicxml(result: SheetResult, path: str | Path) -> Path:
    path = Path(path)
    if path.suffix.lower() not in (".musicxml", ".xml"):
        path = path.with_suffix(".musicxml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.xml, encoding="utf-8")
    return path
