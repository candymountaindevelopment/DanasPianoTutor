"""Playback transport for a lesson: render, play, loop, and report position.

Position is not read back from the audio device; like RAW's controller it is
derived from a wall clock started when playback began. That is accurate to a
few milliseconds, which is plenty for moving a cursor and lighting keys.

A looped practice section plays as two stages: the count-in plus the first
pass once, then the section alone with the backend looping it, so the student
hears the count-in exactly once.
"""

from __future__ import annotations

import time
from dataclasses import replace

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ...audio.buffer import AudioBuffer
from ...audio.playback import get_backend
from ...teach.player import NoteCache, PlayOptions, Rendered, render_lesson, render_note
from ...teach.score import Lesson


class LessonTransport(QObject):
    positionChanged = pyqtSignal(float)      # lesson division; negative while counting in
    stateChanged = pyqtSignal(bool)          # playing?
    renderedChanged = pyqtSignal()           # a new render is ready (options changed)
    message = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.backend = get_backend()
        self.cache = NoteCache()
        self.lesson: Lesson | None = None
        self.options = PlayOptions()
        self.loop = False
        self.rendered: Rendered | None = None
        self._dirty = True
        self._playing = False
        self._phase = "first"
        self._origin = 0.0
        self._offset = 0.0           # seconds into the buffer at which playback started
        self._position = 0.0         # last reported division
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # ---------------------------------------------------------------- state

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> float:
        return self._position

    @property
    def available(self) -> bool:
        return bool(self.backend.available)

    def set_lesson(self, lesson: Lesson | None) -> None:
        self.stop()
        self.lesson = lesson
        self._position = 0.0
        self._dirty = True
        if lesson is not None:
            self.options = replace(self.options, tempo=float(lesson.tempo), loop_bars=None)
        self.renderedChanged.emit()
        self.positionChanged.emit(0.0)

    def update_options(self, **changes) -> None:
        """Change playback options; a running playback restarts from its current bar."""
        new = replace(self.options, **changes)
        if new == self.options:
            return
        self.options = new
        self._dirty = True
        self.renderedChanged.emit()
        if self._playing:
            self.play(from_division=self._bar_start(self._position), count_in=False)

    def set_loop(self, enabled: bool) -> None:
        self.loop = bool(enabled)

    # ------------------------------------------------------------ rendering

    def ensure_rendered(self) -> Rendered | None:
        if self.lesson is None:
            return None
        if self._dirty or self.rendered is None:
            self.rendered = render_lesson(self.lesson, self.options, self.cache)
            self._dirty = False
            for w in self.rendered.warnings:
                self.message.emit(w)
        return self.rendered

    def section_range(self) -> tuple[int, int]:
        if self.lesson is None:
            return 0, 0
        if self.options.loop_bars:
            return self.lesson.measure_range(*self.options.loop_bars)
        return 0, self.lesson.length

    def _bar_start(self, division: float) -> int:
        if self.lesson is None:
            return 0
        m = self.lesson.measure_divisions
        start, end = self.section_range()
        d = int(max(start, min(division, end - 1)) // m * m)
        return max(start, d)

    # ------------------------------------------------------------- playback

    def toggle(self) -> None:
        if self._playing:
            self.stop()
        else:
            self.play()

    def play(self, from_division: float | None = None, count_in: bool = True) -> None:
        rendered = self.ensure_rendered()
        if rendered is None:
            return
        if self._playing:
            self.backend.stop()
            self._timer.stop()
        start, _end = self.section_range()
        if from_division is None or from_division <= start:
            division = start
        else:
            division = self._bar_start(from_division)
        if count_in and division == start:
            offset = 0.0
            buffer = rendered.buffer
        else:
            offset = rendered.seconds_at(division)
            buffer = rendered.buffer
        self._phase = "first"
        self._offset = offset
        self._start_backend(buffer.slice_seconds(offset, buffer.duration) if offset > 0 else buffer, loop=False)
        self._origin = time.perf_counter()
        self._playing = True
        self._timer.start()
        self.stateChanged.emit(True)
        self._tick()

    def stop(self) -> None:
        if not self._playing:
            return
        self.backend.stop()
        self._timer.stop()
        self._playing = False
        start, _ = self.section_range()
        self._position = float(start)
        self.stateChanged.emit(False)
        self.positionChanged.emit(self._position)

    def seek(self, division: float) -> None:
        """Move the cursor; restarts playback from that bar if playing."""
        if self._playing:
            self.play(from_division=division, count_in=False)
        else:
            self._position = float(self._bar_start(division)) if self.lesson else 0.0
            self.positionChanged.emit(self._position)

    def preview_note(self, midi: int) -> None:
        if self.lesson is None or self._playing:
            return
        buf = render_note(self.lesson, midi, 0.7, self.options.sample_rate, self.cache)
        self._start_backend(buf.gain_db(self.options.voice_db + 4.0), loop=False)

    def _start_backend(self, buffer: AudioBuffer, loop: bool) -> None:
        if not self.backend.available:
            self.message.emit("No audio device — playback is silent, the cursor still moves")
            return
        try:
            self.backend.play(buffer, loop)
        except Exception as exc:
            self.message.emit(f"Playback failed: {exc}")

    # ---------------------------------------------------------------- clock

    def _tick(self) -> None:
        rendered = self.rendered
        if rendered is None or not self._playing:
            return
        elapsed = time.perf_counter() - self._origin
        if self._phase == "first":
            pos = self._offset + elapsed
            if pos >= rendered.buffer.duration:
                if self.loop:
                    self._phase = "loop"
                    self._origin = time.perf_counter()
                    self._start_backend(rendered.section, loop=True)
                    pos = rendered.count_in_seconds
                else:
                    self.stop()
                    return
        if self._phase == "loop":
            pos = rendered.count_in_seconds + (elapsed % max(1e-6, rendered.section.duration))
        self._position = rendered.division_at(pos)
        self.positionChanged.emit(self._position)

    def count_in_beat(self, division: float) -> int | None:
        """1-based beat number while the count-in is sounding, else None."""
        if self.lesson is None or self.rendered is None:
            return None
        start = self.rendered.start_division
        if division >= start:
            return None
        beats_before = (start - division) / self.lesson.beat_divisions
        per_bar = self.lesson.measure_divisions / self.lesson.beat_divisions
        total = self.options.count_in_bars * per_bar
        beat = int(total - beats_before) % int(per_bar) + 1
        return beat
