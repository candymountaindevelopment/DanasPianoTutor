"""The lesson model: notes with hands and fingers on a division grid.

Time is counted in *divisions* — `raw.export.sheet.DIVISIONS` (8) per quarter
note — so a lesson converts to seconds at any tempo without rounding and maps
straight onto MusicXML. Everything musical (key signatures, note spelling,
five-finger positions) lives here so the UI, the player and the engraver share
one set of answers.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..export.sheet import DIVISIONS, WHOLE, measure_divisions
from ..patterns.notes import midi_to_note

RIGHT = "R"
LEFT = "L"
HANDS = (RIGHT, LEFT)
HAND_NAMES = {RIGHT: "Right hand", LEFT: "Left hand"}
FINGER_NAMES = {1: "thumb", 2: "index", 3: "middle", 4: "ring", 5: "little"}

# Major keys by number of sharps (positive) or flats (negative).
MAJOR_KEYS = {
    "Cb": -7, "Gb": -6, "Db": -5, "Ab": -4, "Eb": -3, "Bb": -2, "F": -1,
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
}
# Relative minors share the signature of the major a minor third up.
MINOR_KEYS = {
    "Ab": -7, "Eb": -6, "Bb": -5, "F": -4, "C": -3, "G": -2, "D": -1,
    "A": 0, "E": 1, "B": 2, "F#": 3, "C#": 4, "G#": 5, "D#": 6, "A#": 7,
}
SHARP_ORDER = "FCGDAEB"       # order sharps appear in a key signature
FLAT_ORDER = "BEADGCF"
LETTERS = "CDEFGAB"
LETTER_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
WHITE_PITCH_CLASSES = (0, 2, 4, 5, 7, 9, 11)

DEFAULT_TEMPO = 80.0
DEFAULT_STEP = "1/4"


class ScoreError(ValueError):
    pass


# ------------------------------------------------------------------ keys


def parse_key(text) -> tuple[str, int]:
    """'G' -> ('G', 1); 'Am' / 'A minor' -> ('Am', 0); 'Bb' -> ('Bb', -2).

    Returns the normalised key name and the number of fifths (sharps positive,
    flats negative) for the key signature.
    """
    s = str(text or "C").strip().replace("♯", "#").replace("♭", "b")
    if not s:
        return "C", 0
    minor = False
    lowered = s.lower()
    for suffix in (" minor", "minor", "min", "m"):
        if lowered.endswith(suffix) and len(s) > len(suffix):
            minor = True
            s = s[: -len(suffix)].strip()
            break
    else:
        for suffix in (" major", "major", "maj"):
            if lowered.endswith(suffix):
                s = s[: -len(suffix)].strip()
                break
    name = s[:1].upper() + s[1:]
    table = MINOR_KEYS if minor else MAJOR_KEYS
    if name not in table:
        raise ScoreError(f"{text!r} is not a key (try C, G, F, D, Bb, Am, Em)")
    return (name + "m" if minor else name), table[name]


def key_alterations(fifths: int) -> dict[str, int]:
    """Letter -> +1/-1 for the notes altered by a key signature."""
    out: dict[str, int] = {}
    if fifths > 0:
        for letter in SHARP_ORDER[:fifths]:
            out[letter] = 1
    elif fifths < 0:
        for letter in FLAT_ORDER[:-fifths]:
            out[letter] = -1
    return out


def spell(midi: int, fifths: int = 0, prefer: int | None = None) -> tuple[str, int, int]:
    """MIDI -> (letter, alter, octave), preferring the key's own spelling.

    In-key notes are spelled as the key signature spells them. Otherwise a
    written accidental (`prefer` = +1 sharp / -1 flat, as the author typed it)
    wins, then a natural, then sharps in sharp keys and flats in flat keys —
    so a G major piece never shows a Gb and an F major piece never an A#.
    """
    midi = int(midi)
    pc = midi % 12
    octave = midi // 12 - 1
    alts = key_alterations(fifths)

    def find(alter: int):
        for letter in LETTERS:
            if (LETTER_SEMITONE[letter] + alter) % 12 == pc:
                return letter, alter, _octave_for(letter, alter, midi)
        return None

    for letter in LETTERS:
        alter = alts.get(letter, 0)
        if (LETTER_SEMITONE[letter] + alter) % 12 == pc:
            return letter, alter, _octave_for(letter, alter, midi)
    order = [0, -1, 1] if fifths < 0 else [0, 1, -1]
    if prefer in (1, -1):
        order.remove(prefer)
        order.insert(0, prefer)
    for alter in order:
        found = find(alter)
        if found:
            return found
    return "C", 0, octave  # unreachable: every pitch class has a natural or sharp spelling


def _octave_for(letter: str, alter: int, midi: int) -> int:
    """Octave number of the spelled letter (B#3 is MIDI 60, Cb4 is MIDI 59)."""
    natural = midi - alter
    return natural // 12 - 1


def diatonic_index(letter: str, octave: int) -> int:
    """Position on the diatonic ladder: C4 -> 35, D4 -> 36, ... (7 per octave)."""
    return (int(octave) + 1) * 7 + LETTERS.index(letter)


def written_alter(token: str) -> int | None:
    """+1 / -1 when a note name carries a sharp or flat, else None."""
    m = re.match(r"^[a-gA-G]([#bs]*)-?\d+$", str(token).strip())
    if not m or not m.group(1):
        return None
    return -1 if "b" in m.group(1) else 1


def spelled_name(midi: int, fifths: int = 0, prefer: int | None = None) -> str:
    letter, alter, octave = spell(midi, fifths, prefer)
    return f"{letter}{'#' if alter > 0 else 'b' if alter < 0 else ''}{octave}"


# ----------------------------------------------------------- white keys


def is_white(midi: int) -> bool:
    return int(midi) % 12 in WHITE_PITCH_CLASSES


MIN_SPAN, MAX_SPAN, DEFAULT_SPAN = 5, 8, 5


def finger_offsets(span: int) -> list[int]:
    """White-key offset of each of the five fingers from the hand's lowest
    key, for a hand that comfortably covers `span` white keys. Span 5 is the
    five-finger position (0 1 2 3 4); a wider span spreads the fingers out."""
    span = max(MIN_SPAN, min(MAX_SPAN, int(span)))
    return [int(i * (span - 1) / 4 + 0.5) for i in range(5)]


def white_key_offset(midi: int, steps: int) -> int:
    """The white key `steps` white keys away (negative = downwards)."""
    m = int(midi)
    if not is_white(m):
        m -= 1  # a black key sits just above a white one
    direction = 1 if steps >= 0 else -1
    remaining = abs(int(steps))
    while remaining:
        m += direction
        if is_white(m):
            remaining -= 1
    return m


# ------------------------------------------------------------------ model


@dataclass
class Note:
    start: int                 # divisions from the start of the lesson
    duration: int              # divisions
    midi: int
    hand: str = RIGHT          # RIGHT or LEFT
    finger: int | None = None  # 1 (thumb) .. 5 (little finger), as written
    velocity: float = 0.85
    alter: int | None = None   # +1 / -1 if the author wrote a sharp / flat, for spelling
    # Filled by Lesson.resolve_fingers() for notes without a written finger
    # that sit under the hand's current position.
    inferred: int | None = None

    @property
    def shown_finger(self) -> int | None:
        return self.finger if self.finger else self.inferred

    @property
    def end(self) -> int:
        return self.start + self.duration

    def sounding_at(self, division: float) -> bool:
        return self.start <= division < self.end

    @property
    def name(self) -> str:
        return midi_to_note(self.midi)


@dataclass
class Lesson:
    name: str = "lesson"
    title: str = "Untitled"
    composer: str = ""
    tempo: float = DEFAULT_TEMPO
    time_signature: tuple[int, int] = (4, 4)
    key: str = "C"
    fifths: int = 0
    level: int = 1
    instructions: str = ""
    tips: list[str] = field(default_factory=list)
    voice: str = "piano"                   # built-in voice name or instrument name
    voice_params: object = None            # SynthParams when the document defines one
    notes: list[Note] = field(default_factory=list)
    # The lowest key of each hand's five-finger span, when the author gave one.
    position: dict[str, int | None] = field(default_factory=lambda: {RIGHT: None, LEFT: None})
    # White keys each hand comfortably reaches (5 = one per finger).
    span: dict[str, int] = field(default_factory=lambda: {RIGHT: DEFAULT_SPAN, LEFT: DEFAULT_SPAN})
    # Hand -> (notes string, fingers string) as written, kept for round-tripping.
    source: dict[str, dict] = field(default_factory=dict)
    step: str = DEFAULT_STEP

    # ------------------------------------------------------------ timing

    @property
    def measure_divisions(self) -> int:
        return measure_divisions(self.time_signature)

    @property
    def beat_divisions(self) -> int:
        """One beat as the time signature's denominator counts it."""
        return max(1, WHOLE // max(1, int(self.time_signature[1])))

    @property
    def length(self) -> int:
        """Total length in divisions, padded to whole measures (at least one)."""
        end = max((n.end for n in self.notes), default=0)
        m = self.measure_divisions
        return max(1, math.ceil(end / m)) * m

    @property
    def measures(self) -> int:
        return self.length // self.measure_divisions

    def seconds_per_division(self, tempo: float | None = None) -> float:
        bpm = float(tempo or self.tempo)
        return 60.0 / bpm / DIVISIONS

    def duration_seconds(self, tempo: float | None = None) -> float:
        return self.length * self.seconds_per_division(tempo)

    def measure_range(self, first: int, last: int) -> tuple[int, int]:
        """Division span of 1-based measures `first`..`last` inclusive, clamped."""
        m = self.measure_divisions
        first = max(1, min(int(first), self.measures))
        last = max(first, min(int(last), self.measures))
        return (first - 1) * m, last * m

    def bar_beat(self, division: float) -> tuple[int, int]:
        """1-based (bar, beat) at a division; positions before 0 count down."""
        m = self.measure_divisions
        b = self.beat_divisions
        bar = int(math.floor(division / m))
        beat = int(math.floor((division - bar * m) / b))
        return bar + 1, beat + 1

    # ------------------------------------------------------------- notes

    def hand_notes(self, hand: str) -> list[Note]:
        return [n for n in self.notes if n.hand == hand]

    def sounding(self, division: float, hands=HANDS) -> list[Note]:
        return [n for n in self.notes if n.hand in hands and n.sounding_at(division)]

    def upcoming(self, division: float, hand: str) -> Note | None:
        """The next note of a hand that has not started yet."""
        later = [n for n in self.notes if n.hand == hand and n.start > division]
        return min(later, key=lambda n: n.start) if later else None

    @property
    def spellings(self) -> dict[int, int]:
        """midi -> written accidental preference, for the engraver and MusicXML."""
        out: dict[int, int] = {}
        for n in self.notes:
            if n.alter is not None:
                out[n.midi] = n.alter
        return out

    def midi_range(self) -> tuple[int, int]:
        if not self.notes:
            return 60, 72
        return min(n.midi for n in self.notes), max(n.midi for n in self.notes)

    # ------------------------------------------------------- positions

    def hand_position(self, hand: str) -> int | None:
        """Lowest key of the hand's five-finger span.

        Uses the author's `position` if given, else derives it from the first
        fingered note: a right-hand finger n on key k puts the thumb n-1 white
        keys below; a left-hand finger n puts the little finger 5-n below.
        """
        given = self.position.get(hand)
        if given is not None:
            return int(given)
        offsets = finger_offsets(self.span.get(hand, DEFAULT_SPAN))
        for note in sorted(self.hand_notes(hand), key=lambda n: n.start):
            if note.finger:
                index = note.finger - 1 if hand == RIGHT else 5 - note.finger
                return white_key_offset(note.midi, -offsets[index])
        return None

    def position_keys(self, hand: str) -> list[int] | None:
        """The five keys under the fingers, thumb first for the right hand and
        little finger first for the left (i.e. always ascending)."""
        low = self.hand_position(hand)
        if low is None:
            return None
        return [white_key_offset(low, i) for i in finger_offsets(self.span.get(hand, DEFAULT_SPAN))]

    def position_label(self, hand: str) -> str:
        keys = self.position_keys(hand)
        if keys is None:
            return "no fixed position"
        names = [midi_to_note(k) for k in keys]
        finger = "thumb (1)" if hand == RIGHT else "little finger (5)"
        return f"{names[0][:-1]} position — {finger} on {names[0]}; keys {' '.join(names)}"

    def finger_for(self, hand: str, midi: int) -> int | None:
        """Which finger sits on a key in the hand's position, if any."""
        keys = self.position_keys(hand)
        if keys is None or midi not in keys:
            return None
        index = keys.index(midi)
        return index + 1 if hand == RIGHT else 5 - index

    def resolve_fingers(self) -> list[str]:
        """Infer fingers for unfingered notes from the hand's current position.

        Beginner books mark a finger on the first note and wherever the hand
        moves; the notes in between are played by whichever finger sits on the
        key. This walks each hand in time order, moving the position whenever a
        written finger says so, and returns the notes it could not place.
        """
        unplaced: list[str] = []
        for hand in HANDS:
            low = self.position.get(hand)
            offsets = finger_offsets(self.span.get(hand, DEFAULT_SPAN))
            missing = 0
            for note in sorted(self.hand_notes(hand), key=lambda n: (n.start, n.midi)):
                note.inferred = None
                if note.finger:
                    index = note.finger - 1 if hand == RIGHT else 5 - note.finger
                    low = white_key_offset(note.midi, -offsets[index])
                    continue
                if low is None:
                    missing += 1
                    continue
                keys = [white_key_offset(low, i) for i in offsets]
                if note.midi in keys:
                    index = keys.index(note.midi)
                    note.inferred = index + 1 if hand == RIGHT else 5 - index
                else:
                    missing += 1
            if missing:
                unplaced.append(f"{HAND_NAMES[hand]}: {missing} note(s) have no finger number and "
                                "are outside the hand position — add a finger where the hand moves")
        return unplaced

    # ------------------------------------------------------------ checks

    def problems(self) -> list[str]:
        """Things a teacher would want flagged: fingering gaps and overlaps."""
        out: list[str] = []
        for hand in HANDS:
            notes = self.hand_notes(hand)
            by_finger: dict[int, list[Note]] = {}
            for n in notes:
                if n.finger:
                    by_finger.setdefault(n.finger, []).append(n)
            for finger, group in by_finger.items():
                group.sort(key=lambda n: n.start)
                for a, b in zip(group, group[1:]):
                    if a.end > b.start and a.midi != b.midi:
                        bar, beat = self.bar_beat(b.start)
                        out.append(f"{HAND_NAMES[hand]}: finger {finger} plays {a.name} and "
                                   f"{b.name} at the same time (bar {bar}, beat {beat})")
        return out
