"""The lesson dialect of the RAW authoring format.

A lesson document is a `raw.author` document with a `lessons` list. Each
lesson is written per hand in the same compact note string patterns use, with
two additions a piano teacher needs:

* chords in square brackets — `[C3 E3 G3]`
* finger numbers in parentheses — `C4(1)`, `[C3 E3 G3](5,3,1)`

or, if the author prefers, a separate `fingers` line with one number (or
group) per note. See docs/LESSON_FORMAT.md for the specification.

Like the rest of the authoring format, parsing is strict about structure and
forgiving about values: every problem is reported with a path, nothing is
silently dropped, and one bad token does not lose the piece.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..core.authoring import (
    FORMAT_NAME,
    FORMAT_VERSION,
    _clamp,
    _unknown_keys,
    parse_sound,
    sound_to_author,
)
from ..core.ids import slugify
from ..export.sheet import WHOLE, musical_to_divisions
from ..patterns.notes import parse_pitch_token, token_kind
from . import voice as voices
from .score import (
    DEFAULT_SPAN,
    DEFAULT_STEP,
    MAX_SPAN,
    MIN_SPAN,
    DEFAULT_TEMPO,
    LEFT,
    RIGHT,
    Lesson,
    Note,
    ScoreError,
    parse_key,
    spelled_name,
    written_alter,
)

LESSON_KEYS = {
    "name", "title", "composer", "tempo", "time", "key", "level", "step",
    "instructions", "tips", "voice", "right", "left", "position", "span", "tags", "description",
}
HAND_KEYS = {"notes", "fingers", "velocity"}
TOP_KEYS = {"format", "version", "project", "slots", "sounds", "instruments", "patterns", "lessons"}

_TOKEN_RE = re.compile(r"\[[^\]]*\][^\s]*|\S+")
_SUFFIX_RE = re.compile(r"^(?P<body>\[[^\]]*\]|[^:(]+)(?P<rest>.*)$")
_LENGTH_RE = re.compile(r":([^\s(]+)")
_FINGER_RE = re.compile(r"\(([^)]*)\)")
_FINGER_SPLIT = re.compile(r"[,\s]+")


@dataclass
class LessonResult:
    lessons: list[Lesson] = field(default_factory=list)
    instruments: dict = field(default_factory=dict)   # name -> SynthParams
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    document: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> str:
        lines = [f"{len(self.lessons)} lesson(s)"]
        lines += [f"error: {e}" for e in self.errors]
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)


# ------------------------------------------------------------------ tokens


def _parse_fingers(text: str, count: int, label: str, warnings: list[str]) -> list[int | None]:
    """'1,3,5' or '135' or '3' -> finger numbers, padded to `count` with None."""
    raw = str(text).strip()
    if not raw:
        return [None] * count
    parts = [p for p in _FINGER_SPLIT.split(raw) if p]
    if len(parts) == 1 and len(parts[0]) > 1 and parts[0].isdigit():
        parts = list(parts[0])
    out: list[int | None] = []
    for part in parts:
        if part in ("-", "_", "."):
            out.append(None)
            continue
        try:
            value = int(part)
        except ValueError:
            warnings.append(f"{label}: finger {part!r} is not a number 1-5; ignored")
            out.append(None)
            continue
        if not 1 <= value <= 5:
            warnings.append(f"{label}: finger {value} is outside 1-5; ignored")
            out.append(None)
        else:
            out.append(value)
    if len(out) > count:
        warnings.append(f"{label}: {len(out)} finger numbers for {count} note(s); extra ignored")
        out = out[:count]
    return out + [None] * (count - len(out))


def _length_divisions(text: str, step_divs: int, label: str, warnings: list[str]) -> int | None:
    """':2' -> two steps, ':1/8' -> an eighth, ':1/4.' -> dotted quarter."""
    s = text.strip()
    if not s:
        return None
    if "/" in s:
        return musical_to_divisions(s, warnings)
    try:
        return max(1, int(round(float(s.rstrip(".")) * step_divs * (1.5 if s.endswith(".") else 1.0))))
    except ValueError:
        warnings.append(f"{label}: length {text!r} is not valid; ignored")
        return None


def expand_hand(text: str, hand: str, step: str, label: str, warnings: list[str],
                fingers_text: str = "", velocity: float = 0.85,
                measure: int | None = None) -> list[Note]:
    """Expand a hand's note string into Notes on the division grid."""
    step_divs = musical_to_divisions(step or DEFAULT_STEP, warnings)
    notes: list[Note] = []
    last_group: list[Note] = []
    cursor = 0
    bar_start = 0
    bar_index = 1

    for token in _TOKEN_RE.findall(str(text)):
        if token == "|":
            if measure and cursor - bar_start != measure and cursor > bar_start:
                have = (cursor - bar_start) / (WHOLE / 4)
                want = measure / (WHOLE / 4)
                warnings.append(f"{label}: bar {bar_index} has {have:g} beat(s), "
                                f"the time signature wants {want:g}")
            bar_start = cursor
            bar_index += 1
            continue
        # A token may carry a trailing '|' glued on: "C4|" — treat it as C4 then bar.
        trailing_bar = token.endswith("|") and len(token) > 1
        if trailing_bar:
            token = token[:-1]
        m = _SUFFIX_RE.match(token)
        if not m:
            warnings.append(f"{label}: cannot read {token!r}; skipped")
            continue
        body, rest = m.group("body"), m.group("rest")
        length_m = _LENGTH_RE.search(rest)
        finger_m = _FINGER_RE.search(rest)
        leftover = _LENGTH_RE.sub("", _FINGER_RE.sub("", rest)).strip()
        if leftover:
            warnings.append(f"{label}: {leftover!r} after {body!r} not understood; ignored")
        length = _length_divisions(length_m.group(1), step_divs, label, warnings) if length_m else None
        span = length if length is not None else step_divs

        if body.startswith("["):
            pitches = body[1:-1].split()
            kind = "chord" if pitches else "rest"
        else:
            pitches = [body]
            kind = token_kind(body)

        if kind == "rest":
            cursor += span
            last_group = []
        elif kind == "hold":
            if not last_group:
                warnings.append(f"{label}: hold '-' at bar {bar_index} has nothing to extend; treated as a rest")
            else:
                for n in last_group:
                    n.duration += span
            cursor += span
        elif kind in ("trigger", "accent"):
            warnings.append(f"{label}: {body!r} is a drum trigger, not a piano note; treated as a rest")
            cursor += span
            last_group = []
        else:
            group: list[Note] = []
            for pitch in pitches:
                try:
                    midi = int(round(parse_pitch_token(pitch)))
                except Exception:
                    warnings.append(f"{label}: {pitch!r} at bar {bar_index} is not a note; skipped")
                    continue
                if not 21 <= midi <= 108:
                    warnings.append(f"{label}: {pitch!r} is outside the piano (A0-C8); skipped")
                    continue
                group.append(Note(cursor, span, midi, hand, None, velocity, written_alter(pitch)))
            if group:
                # Fingers pair with the pitches in the order they were written:
                # [C3 E3 G3](5,3,1) puts 5 on C3, 3 on E3, 1 on G3.
                if finger_m is not None:
                    fingers = _parse_fingers(finger_m.group(1), len(group), label, warnings)
                    for n, f in zip(group, fingers):
                        n.finger = f
                notes.extend(group)
                last_group = group
            cursor += span
        if trailing_bar:
            bar_start = cursor
            bar_index += 1

    # The parallel fingers line fills in whatever the inline suffixes left open.
    if str(fingers_text).strip():
        groups = str(fingers_text).replace("|", " ").split()
        event_groups: list[list[Note]] = []
        i = 0
        while i < len(notes):
            j = i
            while j < len(notes) and notes[j].start == notes[i].start and notes[j].hand == notes[i].hand:
                j += 1
            event_groups.append(notes[i:j])
            i = j
        if len(groups) != len(event_groups):
            warnings.append(f"{label}.fingers: {len(groups)} entries for {len(event_groups)} note event(s)")
        for group, text_item in zip(event_groups, groups):
            fingers = _parse_fingers(text_item, len(group), f"{label}.fingers", warnings)
            for n, f in zip(group, fingers):
                if n.finger is None:
                    n.finger = f
    return notes


# ------------------------------------------------------------------ lesson


def _parse_time(value, label: str, warnings: list[str]) -> tuple[int, int]:
    default = (4, 4)
    if value is None:
        return default
    try:
        if isinstance(value, str):
            num, den = value.replace(" ", "").split("/")
        else:
            num, den = value[0], value[1]
        num, den = int(num), int(den)
        if num < 1 or den not in (1, 2, 4, 8, 16):
            raise ValueError
        return num, den
    except (ValueError, TypeError, IndexError):
        warnings.append(f"{label}.time: {value!r} is not a time signature like 4/4 or 3/4; using 4/4")
        return default


def _parse_position(value, label: str, warnings: list[str]) -> dict[str, int | None]:
    out: dict[str, int | None] = {RIGHT: None, LEFT: None}
    if value is None:
        return out
    if isinstance(value, str):
        value = {"right": value, "left": value}
    if not isinstance(value, dict):
        warnings.append(f"{label}.position: expected an object like {{\"right\": \"C4\", \"left\": \"C3\"}}")
        return out
    for key, hand in (("right", RIGHT), ("left", LEFT)):
        if value.get(key) is None:
            continue
        try:
            midi = int(round(parse_pitch_token(value[key])))
        except Exception:
            warnings.append(f"{label}.position.{key}: {value[key]!r} is not a note; ignored")
            continue
        if midi % 12 not in (0, 2, 4, 5, 7, 9, 11):
            warnings.append(f"{label}.position.{key}: {value[key]!r} is a black key; positions start on white keys")
            continue
        out[hand] = midi
    return out


def _parse_span(value, label: str, warnings: list[str]) -> dict[str, int]:
    """`span`: white keys a hand comfortably covers — a number for both hands
    or {"right": 6, "left": 5}. 5 (one key per finger) when absent."""
    out = {RIGHT: DEFAULT_SPAN, LEFT: DEFAULT_SPAN}
    if value is None:
        return out
    if not isinstance(value, dict):
        value = {"right": value, "left": value}
    for key, hand in (("right", RIGHT), ("left", LEFT)):
        if value.get(key) is None:
            continue
        try:
            span = int(value[key])
        except (TypeError, ValueError):
            warnings.append(f"{label}.span.{key}: {value[key]!r} is not a number of white keys; using {DEFAULT_SPAN}")
            continue
        if not MIN_SPAN <= span <= MAX_SPAN:
            warnings.append(f"{label}.span.{key}: {span} is outside {MIN_SPAN}..{MAX_SPAN}; clamped")
            span = max(MIN_SPAN, min(MAX_SPAN, span))
        out[hand] = span
    return out


def _hand_spec(value, label: str, warnings: list[str]) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        return {"notes": value}
    if isinstance(value, dict):
        _unknown_keys(value, HAND_KEYS, label, warnings)
        return value
    warnings.append(f"{label}: expected a note string or an object with 'notes'; hand skipped")
    return {}


def parse_lesson(spec: dict, label: str, warnings: list[str], instruments: dict | None = None) -> Lesson:
    _unknown_keys(spec, LESSON_KEYS, label, warnings)
    lesson = Lesson(
        name=slugify(spec.get("name") or spec.get("title") or "lesson"),
        title=str(spec.get("title") or spec.get("name") or "Untitled"),
        composer=str(spec.get("composer", "")),
        tempo=_clamp(spec.get("tempo", DEFAULT_TEMPO), 20.0, 300.0, f"{label}.tempo", warnings, DEFAULT_TEMPO),
        time_signature=_parse_time(spec.get("time"), label, warnings),
        level=int(_clamp(spec.get("level", 1), 1, 5, f"{label}.level", warnings, 1)),
        instructions=str(spec.get("instructions", "")),
        step=str(spec.get("step", DEFAULT_STEP)),
    )
    if spec.get("description") and not lesson.instructions:
        lesson.instructions = str(spec["description"])
    tips = spec.get("tips", [])
    if isinstance(tips, str):
        tips = [tips]
    if not isinstance(tips, list):
        warnings.append(f"{label}.tips: expected a list of strings")
        tips = []
    lesson.tips = [str(t) for t in tips]

    try:
        lesson.key, lesson.fifths = parse_key(spec.get("key", "C"))
    except ScoreError as exc:
        warnings.append(f"{label}.key: {exc}; using C major")
        lesson.key, lesson.fifths = "C", 0

    try:
        musical_to_divisions(lesson.step, [])
    except Exception:
        warnings.append(f"{label}.step: {lesson.step!r} is not a note value; using {DEFAULT_STEP}")
        lesson.step = DEFAULT_STEP

    voice_name = str(spec.get("voice", "piano"))
    if instruments and voice_name in instruments:
        lesson.voice = voice_name
        lesson.voice_params = instruments[voice_name]
    elif voice_name in voices.VOICE_NAMES:
        lesson.voice = voice_name
    else:
        warnings.append(f"{label}.voice: {voice_name!r} is not a built-in voice "
                        f"({', '.join(voices.VOICE_NAMES)}) or an instrument in this document; using piano")
        lesson.voice = "piano"

    lesson.position = _parse_position(spec.get("position"), label, warnings)
    lesson.span = _parse_span(spec.get("span"), label, warnings)

    for key, hand in (("right", RIGHT), ("left", LEFT)):
        hspec = _hand_spec(spec.get(key), f"{label}.{key}", warnings)
        if not hspec:
            continue
        velocity = _clamp(hspec.get("velocity", 0.85), 0.05, 1.0, f"{label}.{key}.velocity", warnings, 0.85)
        lesson.source[hand] = {"notes": str(hspec.get("notes", "")), "fingers": str(hspec.get("fingers", ""))}
        lesson.notes += expand_hand(
            hspec.get("notes", ""), hand, lesson.step, f"{label}.{key}", warnings,
            fingers_text=hspec.get("fingers", ""), velocity=velocity,
            measure=lesson.measure_divisions,
        )
    if not lesson.notes:
        warnings.append(f"{label}: the lesson has no notes")
    lesson.notes.sort(key=lambda n: (n.start, n.hand, n.midi))
    for problem in lesson.resolve_fingers() + lesson.problems():
        warnings.append(f"{label}: {problem}")
    return lesson


# ---------------------------------------------------------------- document


def parse_lesson_document(doc) -> LessonResult:
    """Parse a document's `lessons` (and the `instruments` they may use)."""
    result = LessonResult()
    if isinstance(doc, (str, bytes)):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError as exc:
            result.errors.append(f"not valid JSON: {exc}")
            return result
    if not isinstance(doc, dict):
        result.errors.append("the document must be a JSON object")
        return result
    result.document = doc
    if doc.get("format") != FORMAT_NAME:
        result.warnings.append(f'missing or wrong "format" field (expected "{FORMAT_NAME}"); parsing anyway')
    if doc.get("version", FORMAT_VERSION) != FORMAT_VERSION:
        result.warnings.append(f"document version {doc.get('version')} != {FORMAT_VERSION}; parsing anyway")
    _unknown_keys(doc, TOP_KEYS, "document", result.warnings)

    for section in ("instruments", "sounds"):
        entries = doc.get(section) or []
        if not isinstance(entries, list):
            result.warnings.append(f"{section}: expected a list")
            continue
        for i, spec in enumerate(entries):
            if not isinstance(spec, dict) or not spec.get("name"):
                result.warnings.append(f"{section}[{i}]: missing name; skipped")
                continue
            name = slugify(spec["name"])
            params = parse_sound(spec, f"{section}[{name}]", result.warnings)
            if params.root_note is None:
                result.warnings.append(f"{section}[{name}]: set root_note so notes transpose correctly")
            result.instruments[name] = params

    lessons = doc.get("lessons")
    if lessons is None:
        result.errors.append('no "lessons" list in the document')
        return result
    if not isinstance(lessons, list):
        result.errors.append('"lessons" must be a list')
        return result
    seen: set[str] = set()
    for i, spec in enumerate(lessons):
        if not isinstance(spec, dict):
            result.warnings.append(f"lessons[{i}]: expected an object; skipped")
            continue
        label = f"lessons[{slugify(spec.get('name') or spec.get('title') or str(i))}]"
        lesson = parse_lesson(spec, label, result.warnings, result.instruments)
        base = lesson.name
        n = 1
        while lesson.name in seen:
            n += 1
            lesson.name = f"{base}_{n}"
        seen.add(lesson.name)
        result.lessons.append(lesson)
    if not result.lessons:
        result.errors.append("the document defines no lessons")
    return result


def load_lesson_file(path) -> LessonResult:
    from pathlib import Path

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        result = LessonResult()
        result.errors.append(f"cannot read {p}: {exc}")
        return result
    return parse_lesson_document(text)


# --------------------------------------------------------------- serialise


def hand_to_author(lesson: Lesson, hand: str) -> dict | None:
    """Regenerate a hand's note string (with inline fingers) from its notes."""
    notes = lesson.hand_notes(hand)
    if not notes and hand not in lesson.source:
        return None
    step_divs = musical_to_divisions(lesson.step, [])
    measure = lesson.measure_divisions
    groups: list[list[Note]] = []
    for n in sorted(notes, key=lambda n: (n.start, n.midi)):
        if groups and groups[-1][0].start == n.start:
            groups[-1].append(n)
        else:
            groups.append([n])
    tokens: list[str] = []
    cursor = 0

    def emit_rest(divs: int) -> None:
        nonlocal cursor
        while divs > 0:
            take = min(divs, measure - cursor % measure) if measure else divs
            tokens.append("." if take == step_divs else f".:{_length_text(take, step_divs)}")
            cursor += take
            divs -= take
            if measure and cursor % measure == 0:
                tokens.append("|")

    for group in groups:
        if group[0].start > cursor:
            emit_rest(group[0].start - cursor)
        duration = max(n.duration for n in group)
        pitches = [spelled_name(n.midi, lesson.fifths, n.alter) for n in group]
        fingers = [n.finger for n in group]
        body = pitches[0] if len(group) == 1 else f"[{' '.join(pitches)}]"
        if duration != step_divs:
            body += f":{_length_text(duration, step_divs)}"
        if any(fingers):
            if len(group) == 1:
                body += f"({fingers[0]})"
            else:
                body += "(" + ",".join(str(f) if f else "-" for f in fingers) + ")"
        tokens.append(body)
        cursor += duration
        if measure and cursor % measure == 0:
            tokens.append("|")
    if tokens and tokens[-1] == "|":
        tokens.pop()
    return {"notes": " ".join(tokens)}


def _length_text(divs: int, step_divs: int) -> str:
    if divs % step_divs == 0:
        return str(divs // step_divs)
    if divs * 2 % (3 * step_divs) == 0 and divs > step_divs:
        return f"{divs * 2 // (3 * step_divs)}."
    return f"{divs}/{WHOLE}"


def lesson_to_author(lesson: Lesson) -> dict:
    out = {
        "name": lesson.name,
        "title": lesson.title,
        "tempo": lesson.tempo,
        "time": f"{lesson.time_signature[0]}/{lesson.time_signature[1]}",
        "key": lesson.key,
        "level": lesson.level,
        "step": lesson.step,
    }
    if lesson.composer:
        out["composer"] = lesson.composer
    if lesson.voice != "piano":
        out["voice"] = lesson.voice
    if lesson.instructions:
        out["instructions"] = lesson.instructions
    if lesson.tips:
        out["tips"] = list(lesson.tips)
    position = {k: spelled_name(v) for k, v in (("right", lesson.position.get(RIGHT)),
                                                  ("left", lesson.position.get(LEFT))) if v is not None}
    if position:
        out["position"] = position
    if any(v != DEFAULT_SPAN for v in lesson.span.values()):
        r, l = lesson.span.get(RIGHT, DEFAULT_SPAN), lesson.span.get(LEFT, DEFAULT_SPAN)
        out["span"] = r if r == l else {"right": r, "left": l}
    for key, hand in (("right", RIGHT), ("left", LEFT)):
        hand_doc = hand_to_author(lesson, hand)
        if hand_doc is not None:
            out[key] = hand_doc
    return out


def document_from_lessons(lessons: list[Lesson], instruments: dict | None = None,
                          project_name: str = "") -> dict:
    doc: dict = {"format": FORMAT_NAME, "version": FORMAT_VERSION}
    if project_name:
        doc["project"] = {"name": project_name}
    if instruments:
        doc["instruments"] = []
        for name, params in instruments.items():
            entry = sound_to_author(_ParamsHolder(name, params))
            doc["instruments"].append(entry)
    doc["lessons"] = [lesson_to_author(lesson) for lesson in lessons]
    return doc


class _ParamsHolder:
    """Just enough of an asset for sound_to_author."""

    def __init__(self, name: str, params) -> None:
        self.name = name
        self.params = params
        self.tags: list[str] = []
        self.description = ""
