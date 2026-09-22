"""JSON-in / JSON-out façade over the tutor core, for the browser build.

The web app runs this module inside Pyodide in a Web Worker. Every function
takes and returns plain strings (JSON) or bytes, so the JavaScript side never
touches Python objects, and the same functions are testable on the desktop
with the normal test suite. Parsed lessons are kept in a module-level
registry and referred to by index.

Nothing here is Qt- or file-system-specific: `parse` takes the document
text, `render` returns raw float32 samples, `engrave` returns the layout as
plain dicts, `musicxml` returns the XML text.
"""

from __future__ import annotations

import dataclasses
import json

import numpy as np

from .. import __version__ as RAW_VERSION
from . import APP_NAME, TAGLINE
from . import __version__ as TUTOR_VERSION
from .authoring import LessonResult, lesson_to_author, parse_lesson_document
from .engrave import engrave
from .player import NoteCache, PlayOptions, Rendered, render_lesson, render_note
from .score import HAND_NAMES, HANDS, LEFT, RIGHT, Lesson
from .sheet import lesson_to_musicxml

_result: LessonResult | None = None
_cache = NoteCache()
_last_render: Rendered | None = None
_last_preview = None


def about() -> str:
    return json.dumps({"name": APP_NAME, "version": TUTOR_VERSION, "engine": RAW_VERSION, "tagline": TAGLINE})


# ----------------------------------------------------------------- lessons


def _note_dict(n) -> dict:
    return {
        "start": n.start, "duration": n.duration, "midi": n.midi, "hand": n.hand,
        "finger": n.finger, "inferred": n.inferred, "shown": n.shown_finger, "velocity": n.velocity,
    }


def lesson_summary(lesson: Lesson, warnings: list[str]) -> dict:
    lo, hi = lesson.midi_range()
    positions = {}
    for hand in HANDS:
        keys = lesson.position_keys(hand)
        positions[hand] = {
            "keys": keys,
            "fingers": [lesson.finger_for(hand, k) for k in keys] if keys else None,
            "label": lesson.position_label(hand),
            "name": HAND_NAMES[hand],
        }
    return {
        "name": lesson.name, "title": lesson.title, "composer": lesson.composer,
        "tempo": lesson.tempo, "time": list(lesson.time_signature), "key": lesson.key,
        "fifths": lesson.fifths, "level": lesson.level, "instructions": lesson.instructions,
        "tips": list(lesson.tips), "voice": lesson.voice, "step": lesson.step,
        "measures": lesson.measures, "length": lesson.length,
        "measure_divisions": lesson.measure_divisions, "beat_divisions": lesson.beat_divisions,
        "midi_range": [lo, hi], "positions": positions,
        "span": {hand: lesson.span.get(hand, 5) for hand in HANDS},
        "notes": [_note_dict(n) for n in lesson.notes],
        "warnings": [w for w in warnings if f"[{lesson.name}]" in w],
    }


def parse(text: str) -> str:
    """Parse a lesson document; keeps the lessons for the other calls."""
    global _result, _last_render
    _result = parse_lesson_document(text)
    _last_render = None
    return json.dumps({
        "ok": _result.ok,
        "errors": list(_result.errors),
        "warnings": list(_result.warnings),
        "lessons": [lesson_summary(lesson, _result.warnings) for lesson in _result.lessons],
    })


def _lesson(index: int) -> Lesson:
    if _result is None or not _result.lessons:
        raise ValueError("no document parsed")
    return _result.lessons[int(index)]


def to_author(index: int) -> str:
    return json.dumps(lesson_to_author(_lesson(index)), ensure_ascii=False, indent=2)


# ----------------------------------------------------------------- audio


def render(index: int, options_json: str) -> str:
    """Render for playback. Returns JSON metadata; fetch samples with last_samples()."""
    global _last_render
    opts = json.loads(options_json or "{}")
    loop = opts.get("loop_bars")
    options = PlayOptions(
        tempo=float(opts.get("tempo", _lesson(index).tempo)),
        hands=str(opts.get("hands", "both")),
        metronome=bool(opts.get("metronome", True)),
        count_in_bars=int(opts.get("count_in_bars", 1)),
        loop_bars=(int(loop[0]), int(loop[1])) if loop else None,
        voice_db=float(opts.get("voice_db", -12.0)),
        metronome_db=float(opts.get("metronome_db", -12.0)),
        metronome_unpitched=bool(opts.get("metronome_unpitched", False)),
        sample_rate=int(opts.get("sample_rate", 44100)),
    )
    _last_render = render_lesson(_lesson(index), options, _cache)
    r = _last_render
    return json.dumps({
        "sample_rate": r.buffer.sample_rate,
        "channels": r.buffer.channels,
        "frames": r.buffer.num_frames,
        "section_frames": r.section.num_frames,
        "count_in_seconds": r.count_in_seconds,
        "start_division": r.start_division,
        "end_division": r.end_division,
        "seconds_per_division": r.seconds_per_division,
        "warnings": list(r.warnings),
    })


def last_samples() -> bytes:
    """Mono float32 samples of the last render (count-in + section)."""
    if _last_render is None:
        return b""
    return np.ascontiguousarray(_last_render.buffer.mono().samples.reshape(-1), dtype=np.float32).tobytes()


def note_preview(index: int, midi: int, seconds: float = 0.7) -> bytes:
    buf = render_note(_lesson(index), int(midi), float(seconds), 44100, _cache)
    return np.ascontiguousarray(buf.mono().samples.reshape(-1) * 0.5, dtype=np.float32).tobytes()


# --------------------------------------------------------------- notation


def engrave_json(index: int, width_sp: float, page_height_sp: float | None = None,
                 show_inferred: bool = True, margin: float = 2.0) -> str:
    layout = engrave(_lesson(index), float(width_sp),
                     float(page_height_sp) if page_height_sp else None,
                     margin=float(margin), header=True, show_inferred=bool(show_inferred))
    return json.dumps(dataclasses.asdict(layout))


def musicxml(index: int, include_inferred: bool = False) -> str:
    return lesson_to_musicxml(_lesson(index), bool(include_inferred)).xml
