"""Chord tables and chord rendering.

A chord is rendered by transposing one recipe to each note and mixing, which
reuses exactly the path pattern playback uses — so a chord sounds like the
instrument, not like a special case.
"""

from __future__ import annotations

import numpy as np

from ..audio.buffer import AudioBuffer
from .notes import hz_to_midi, midi_to_note, note_to_midi

# Semitones above the root.
CHORDS: dict[str, tuple[int, ...]] = {
    "Single note": (0,),
    "Octave": (0, 12),
    "Power (5)": (0, 7, 12),
    "Major": (0, 4, 7),
    "Minor": (0, 3, 7),
    "Sus2": (0, 2, 7),
    "Sus4": (0, 5, 7),
    "Diminished": (0, 3, 6),
    "Augmented": (0, 4, 8),
    "Major 6": (0, 4, 7, 9),
    "Minor 6": (0, 3, 7, 9),
    "Major 7": (0, 4, 7, 11),
    "Minor 7": (0, 3, 7, 10),
    "Dominant 7": (0, 4, 7, 10),
    "Minor 7 b5": (0, 3, 6, 10),
    "Diminished 7": (0, 3, 6, 9),
    "Add 9": (0, 4, 7, 14),
    "Major 9": (0, 4, 7, 11, 14),
    "Minor 9": (0, 3, 7, 10, 14),
    "Dominant 9": (0, 4, 7, 10, 14),
}

ROOT_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Headroom ceiling for a mixed chord.
CEILING = 0.95


def chord_notes(root_midi: int, quality: str, inversion: int = 0) -> list[int]:
    """MIDI notes for a chord. Inversion lifts the lowest notes an octave."""
    intervals = list(CHORDS.get(quality, CHORDS["Major"]))
    notes = [root_midi + i for i in intervals]
    for _ in range(max(0, int(inversion)) % max(1, len(notes))):
        notes.append(notes.pop(0) + 12)
    return sorted(notes)


def chord_name(root_midi: int, quality: str) -> str:
    return f"{ROOT_NAMES[int(root_midi) % 12]} {quality}"


def params_root_midi(params) -> float:
    """The note at which a recipe sounds as written."""
    root = getattr(params, "root_note", None)
    if root:
        try:
            return note_to_midi(root)
        except Exception:
            pass
    return hz_to_midi(params.pitch.value_at(0.0))


def render_chord(
    params,
    midi_notes,
    sample_rate: int = 44100,
    strum: float = 0.0,
    tempo: float = 120.0,
    duration: float | None = None,
    preview: bool = False,
) -> tuple[AudioBuffer, list[str]]:
    """Render each note by transposing `params`, then mix.

    Levels are divided by sqrt(n): summing five notes at full amplitude clips,
    and a straight 1/n makes a big chord noticeably quieter than a single note.

    sqrt(n) assumes the notes sum incoherently, which they do not — every voice
    starts at phase zero with the same envelope, so a plain triad of sines
    reached 1.38. A final ceiling catches that without flattening the level
    differences between chord sizes.
    """
    from ..synth import engine

    notes = sorted({int(round(n)) for n in midi_notes})
    if not notes:
        return AudioBuffer.silence(0.25, sample_rate), ["no notes selected"]

    root = params_root_midi(params)
    scale = 1.0 / np.sqrt(len(notes))
    warnings: list[str] = []
    parts: list[tuple[AudioBuffer, int]] = []

    for index, midi in enumerate(notes):
        overrides = {"pitch_offset": round(midi - root, 6)}
        if duration is not None:
            overrides["duration"] = float(duration)
        result = engine.render(params, sample_rate, overrides, tempo, preview)
        warnings += [w for w in result.warnings if w not in warnings]
        offset = int(round(index * max(0.0, strum) * sample_rate))
        parts.append((result.buffer.gain(scale), offset))

    mixed = AudioBuffer.mix(parts, sample_rate)
    if mixed.peak() > CEILING:
        warnings.append(f"chord peaked at {mixed.peak():.2f}; limited to {CEILING}")
        mixed = mixed.normalized(CEILING)
    return mixed, warnings


def describe(midi_notes) -> str:
    notes = sorted({int(round(n)) for n in midi_notes})
    return " ".join(midi_to_note(n) for n in notes) if notes else "(nothing selected)"
