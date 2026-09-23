"""Render a lesson to audio through the RAW synth engine.

Every note is a recipe render with a `pitch_offset` from the voice's root and
a `duration` in seconds derived from the chosen tempo, so slowing a piece down
for practice lengthens the notes musically instead of resampling them. The
metronome is a second layer of tiny renders on the beat grid. Renders are
cached by (voice, pitch, length) — a beginner piece has a handful of distinct
notes, so a full re-render after a tempo change is near-instant.

Nothing here touches Qt; the transport that plays the result lives in the UI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..audio.buffer import AudioBuffer
from ..patterns.notes import note_to_midi
from ..synth import engine
from ..synth.engine import SynthParams
from . import voice as voices
from .score import HANDS, LEFT, RIGHT, Lesson, Note

HAND_MODES = {"both": HANDS, "right": (RIGHT,), "left": (LEFT,)}


@dataclass
class PlayOptions:
    tempo: float = 80.0                 # BPM actually used for playback
    hands: str = "both"                 # both | right | left
    metronome: bool = True
    count_in_bars: int = 1
    loop_bars: tuple[int, int] | None = None   # 1-based inclusive measure range, None = whole piece
    voice_db: float = -12.0             # six-note stacks stay under 0 dBFS without normalising
    metronome_db: float = -12.0
    metronome_unpitched: bool = False   # noise click, for when the microphone is listening
    sample_rate: int = 44100
    tail: float = 0.35                  # seconds of silence after the last note so releases finish

    @property
    def hand_set(self) -> tuple[str, ...]:
        return HAND_MODES.get(self.hands, HANDS)


@dataclass
class Rendered:
    """A rendered section plus the mapping back to lesson time."""

    buffer: AudioBuffer                # count-in followed by the section
    section: AudioBuffer               # the section alone (what loops)
    count_in_seconds: float
    start_division: int
    end_division: int
    seconds_per_division: float
    warnings: list[str] = field(default_factory=list)

    @property
    def section_seconds(self) -> float:
        return (self.end_division - self.start_division) * self.seconds_per_division

    def division_at(self, seconds: float) -> float:
        """Lesson position for a playback time measured from the buffer start."""
        return self.start_division + (seconds - self.count_in_seconds) / self.seconds_per_division

    def seconds_at(self, division: float) -> float:
        return self.count_in_seconds + (division - self.start_division) * self.seconds_per_division


class NoteCache:
    """Renders of (voice, midi, seconds, velocity), reused across re-renders."""

    def __init__(self) -> None:
        self._cache: dict[tuple, AudioBuffer] = {}

    def clear(self) -> None:
        self._cache.clear()

    def render(self, key: str, params: SynthParams, overrides: dict, sample_rate: int) -> AudioBuffer:
        cache_key = (key, sample_rate, tuple(sorted((k, round(float(v), 4)) for k, v in overrides.items())))
        buf = self._cache.get(cache_key)
        if buf is None:
            buf = engine.render(params, sample_rate, overrides).buffer
            self._cache[cache_key] = buf
        return buf


_shared_cache = NoteCache()


def lesson_voice(lesson: Lesson) -> tuple[str, SynthParams]:
    if lesson.voice_params is not None:
        return f"doc:{lesson.voice}", lesson.voice_params
    return f"voice:{lesson.voice}", voices.voice(lesson.voice)


def _root_midi(params: SynthParams) -> float:
    if params.root_note:
        return note_to_midi(params.root_note)
    from ..patterns.chords import params_root_midi

    return params_root_midi(params)


def note_overrides(note: Note, root_midi: float, seconds_per_division: float, gain_db: float) -> dict:
    # A touch shorter than the written value so repeated notes articulate.
    seconds = max(0.03, note.duration * seconds_per_division * 0.95)
    velocity_db = 20.0 * math.log10(max(0.05, float(note.velocity)))
    return {
        "pitch_offset": float(note.midi) - float(root_midi),
        "duration": seconds,
        "volume_db": velocity_db + gain_db,
    }


# Noise carries less loudness than a sine at the same peak, so the practice
# click gets make-up gain; measured A-weighted, this brings it level with the
# ordinary click instead of 6 dB under it.
UNPITCHED_MAKEUP_DB = 6.0


def _clicks(cache, options: "PlayOptions", sr: int):
    """The beat and downbeat clicks, cached under their own keys."""
    suffix = "_np" if options.metronome_unpitched else ""
    db = options.metronome_db + (UNPITCHED_MAKEUP_DB if options.metronome_unpitched else 0.0)
    gain = {"volume_db": db}
    tick = cache.render("click" + suffix, voices.click(False, options.metronome_unpitched), gain, sr)
    accent = cache.render("click_accent" + suffix, voices.click(True, options.metronome_unpitched), gain, sr)
    return tick, accent


def metronome_events(lesson: Lesson, start_division: int, end_division: int) -> list[tuple[int, bool]]:
    """(division, accent) for every beat in [start, end)."""
    out = []
    beat = lesson.beat_divisions
    measure = lesson.measure_divisions
    d = start_division
    while d < end_division:
        out.append((d, d % measure == 0))
        d += beat
    return out


def render_lesson(lesson: Lesson, options: PlayOptions, cache: NoteCache | None = None) -> Rendered:
    cache = cache or _shared_cache
    sr = int(options.sample_rate)
    spd = lesson.seconds_per_division(options.tempo)
    if options.loop_bars:
        start, end = lesson.measure_range(*options.loop_bars)
    else:
        start, end = 0, lesson.length
    warnings: list[str] = []

    voice_key, params = lesson_voice(lesson)
    root = _root_midi(params)
    hands = options.hand_set

    layers: list[tuple[AudioBuffer, int]] = []
    section_frames = int(round((end - start) * spd * sr))
    for note in lesson.notes:
        if note.hand not in hands or note.start < start or note.start >= end:
            continue
        ov = note_overrides(note, root, spd, options.voice_db)
        buf = cache.render(voice_key, params, ov, sr)
        layers.append((buf, int(round((note.start - start) * spd * sr))))

    if options.metronome:
        tick, accent = _clicks(cache, options, sr)
        for division, is_accent in metronome_events(lesson, start, end):
            layers.append((accent if is_accent else tick, int(round((division - start) * spd * sr))))

    total_frames = section_frames + int(options.tail * sr)
    if layers:
        section = AudioBuffer.mix(layers, sr)
        if section.num_frames < total_frames:
            section = AudioBuffer.concat([section, AudioBuffer.silence((total_frames - section.num_frames) / sr, sr)])
        else:
            section = section.slice_frames(0, total_frames)
    else:
        section = AudioBuffer.silence(max(1, total_frames) / sr, sr)
        warnings.append("nothing to play in this range")
    if section.peak() > 0.98:
        section = section.normalized(0.98)
        warnings.append("mix was normalised to avoid clipping")

    count_in_seconds = 0.0
    if options.count_in_bars > 0:
        bars = int(options.count_in_bars)
        beats = metronome_events(lesson, 0, bars * lesson.measure_divisions)
        count_in_seconds = bars * lesson.measure_divisions * spd
        tick, accent = _clicks(cache, options, sr)
        count_layers = [(accent if a else tick, int(round(d * spd * sr))) for d, a in beats]
        count_in = AudioBuffer.mix(count_layers, sr)
        frames = int(round(count_in_seconds * sr))
        if count_in.num_frames < frames:
            count_in = AudioBuffer.concat([count_in, AudioBuffer.silence((frames - count_in.num_frames) / sr, sr)])
        else:
            count_in = count_in.slice_frames(0, frames)
        buffer = AudioBuffer.concat([count_in, section])
    else:
        buffer = section

    return Rendered(buffer, section, count_in_seconds, start, end, spd, warnings)


def render_note(lesson: Lesson, midi: int, seconds: float = 0.6, sample_rate: int = 44100,
                cache: NoteCache | None = None) -> AudioBuffer:
    """One key, for clicking the on-screen keyboard."""
    cache = cache or _shared_cache
    voice_key, params = lesson_voice(lesson)
    root = _root_midi(params)
    return cache.render(voice_key, params, {"pitch_offset": float(midi) - root, "duration": seconds}, sample_rate)


def silence_like(buffer: AudioBuffer) -> AudioBuffer:
    return AudioBuffer(np.zeros_like(buffer.samples), buffer.sample_rate)
