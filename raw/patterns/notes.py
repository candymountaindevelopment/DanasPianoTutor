"""Note names, MIDI numbers, and frequencies.

Convention: C4 = MIDI 60, A4 = MIDI 69 = 440 Hz.
"""

from __future__ import annotations

import math
import re

NOTE_OFFSETS = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_NOTE_RE = re.compile(r"^([a-gA-G])([#b s]*)(-?\d+)$")

REST_TOKENS = {".", "..", "-.", "_", "rest", "r"}
HOLD_TOKENS = {"-", "~"}
TRIGGER_TOKENS = {"x", "o"}
ACCENT_TOKENS = {"X", "O"}


class NoteError(ValueError):
    pass


def note_to_midi(name: str) -> float:
    """'C4' -> 60, 'A#3' -> 58, 'Bb3' -> 58."""
    m = _NOTE_RE.match(str(name).strip().replace(" ", ""))
    if not m:
        raise NoteError(f"{name!r} is not a note name like C4, F#3 or Bb5")
    letter, accidentals, octave = m.groups()
    semitone = NOTE_OFFSETS[letter.lower()]
    for ch in accidentals:
        if ch in "#s":
            semitone += 1
        elif ch == "b":
            semitone -= 1
    return float((int(octave) + 1) * 12 + semitone)


def midi_to_note(midi: float) -> str:
    m = int(round(midi))
    return f"{SHARP_NAMES[m % 12]}{m // 12 - 1}"


def midi_to_hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((float(midi) - 69.0) / 12.0))


def hz_to_midi(hz: float) -> float:
    return 69.0 + 12.0 * math.log2(max(float(hz), 1e-6) / 440.0)


def note_to_hz(name: str) -> float:
    return midi_to_hz(note_to_midi(name))


def parse_pitch_token(value) -> float:
    """Accept a note name, a MIDI number, or 'hz:900' and return MIDI."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if text.lower().startswith("hz:"):
        return hz_to_midi(float(text[3:]))
    if text.lower().endswith("hz"):
        return hz_to_midi(float(text[:-2]))
    return note_to_midi(text)


def token_kind(token: str) -> str:
    """Classify a token from a compact note string."""
    t = token.strip()
    if not t or t in REST_TOKENS:
        return "rest"
    if t in HOLD_TOKENS:
        return "hold"
    if t in TRIGGER_TOKENS:
        return "trigger"
    if t in ACCENT_TOKENS:
        return "accent"
    return "note"
