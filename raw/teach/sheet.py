"""Lesson -> MusicXML. Right hand on the treble staff, left on the bass,
fingering written above / below, key signature from the lesson."""

from __future__ import annotations

from ..export.sheet import NoteEvent, SheetResult, build_musicxml, write_musicxml
from .score import LEFT, RIGHT, Lesson

__all__ = ["lesson_events", "lesson_to_musicxml", "write_musicxml"]


def lesson_events(lesson: Lesson, include_inferred: bool = False) -> list[NoteEvent]:
    """The lesson's notes as sheet events, staff fixed by hand."""
    events = []
    for n in lesson.notes:
        finger = n.finger if not include_inferred else n.shown_finger
        events.append(NoteEvent(n.start, n.duration, n.midi, n.velocity, finger,
                                1 if n.hand == RIGHT else 2 if n.hand == LEFT else None))
    return events


def lesson_to_musicxml(lesson: Lesson, include_inferred: bool = False) -> SheetResult:
    warnings: list[str] = []
    result = build_musicxml(
        lesson_events(lesson, include_inferred),
        lesson.title,
        float(lesson.tempo),
        lesson.time_signature,
        minimum_length=lesson.length,
        warnings=warnings,
        fifths=lesson.fifths,
        composer=lesson.composer,
        spellings=lesson.spellings,
    )
    return result
