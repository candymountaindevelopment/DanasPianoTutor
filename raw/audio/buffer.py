"""The internal audio representation.

All audio inside the application is float64 in [-1.0, +1.0], shape
(num_frames, channels). Quantisation to PCM happens only at export.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class AudioBuffer:
    samples: np.ndarray
    sample_rate: int = 44100

    def __post_init__(self) -> None:
        a = np.asarray(self.samples, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        elif a.ndim != 2:
            raise ValueError(f"samples must be 1-D or 2-D, got {a.ndim}-D")
        if a.shape[1] > 2:
            raise ValueError(f"at most 2 channels supported, got {a.shape[1]}")
        self.samples = a
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")

    # ------------------------------------------------------------------ info

    @property
    def num_frames(self) -> int:
        return int(self.samples.shape[0])

    @property
    def channels(self) -> int:
        return int(self.samples.shape[1])

    @property
    def duration(self) -> float:
        return self.num_frames / self.sample_rate

    def peak(self) -> float:
        return float(np.max(np.abs(self.samples))) if self.num_frames else 0.0

    def rms(self) -> float:
        if not self.num_frames:
            return 0.0
        return float(np.sqrt(np.mean(np.square(self.samples))))

    def dc_offset(self) -> float:
        return float(np.mean(self.samples)) if self.num_frames else 0.0

    def is_clipping(self) -> bool:
        return self.peak() > 1.0

    # ------------------------------------------------------------- transform
    # Every transform returns a new buffer. Buffers are treated as immutable
    # by the rest of the application so they can be cached safely.

    def copy(self) -> "AudioBuffer":
        return AudioBuffer(self.samples.copy(), self.sample_rate)

    def mono(self) -> "AudioBuffer":
        if self.channels == 1:
            return self.copy()
        return AudioBuffer(self.samples.mean(axis=1, keepdims=True), self.sample_rate)

    def stereo(self) -> "AudioBuffer":
        if self.channels == 2:
            return self.copy()
        return AudioBuffer(np.repeat(self.samples, 2, axis=1), self.sample_rate)

    def gain(self, factor: float) -> "AudioBuffer":
        return AudioBuffer(self.samples * factor, self.sample_rate)

    def gain_db(self, db: float) -> "AudioBuffer":
        return self.gain(10.0 ** (db / 20.0))

    def normalized(self, target_peak: float = 0.98) -> "AudioBuffer":
        p = self.peak()
        if p <= 1e-12:
            return self.copy()
        return self.gain(target_peak / p)

    def panned(self, pan: float) -> "AudioBuffer":
        """Equal-power pan. -1 hard left, 0 centre, +1 hard right."""
        pan = float(np.clip(pan, -1.0, 1.0))
        src = self.mono().samples[:, 0]
        angle = (pan + 1.0) * (np.pi / 4.0)
        return AudioBuffer(
            np.stack([src * np.cos(angle), src * np.sin(angle)], axis=1),
            self.sample_rate,
        )

    def reversed(self) -> "AudioBuffer":
        return AudioBuffer(self.samples[::-1].copy(), self.sample_rate)

    def slice_frames(self, start: int, end: int) -> "AudioBuffer":
        start = max(0, int(start))
        end = min(self.num_frames, int(end))
        return AudioBuffer(self.samples[start:end].copy(), self.sample_rate)

    def slice_seconds(self, start: float, end: float) -> "AudioBuffer":
        return self.slice_frames(
            round(start * self.sample_rate), round(end * self.sample_rate)
        )

    def faded(self, fade_in: float = 0.0, fade_out: float = 0.0) -> "AudioBuffer":
        out = self.samples.copy()
        n_in = min(round(fade_in * self.sample_rate), self.num_frames)
        n_out = min(round(fade_out * self.sample_rate), self.num_frames)
        if n_in > 0:
            out[:n_in] *= np.linspace(0.0, 1.0, n_in)[:, None]
        if n_out > 0:
            out[-n_out:] *= np.linspace(1.0, 0.0, n_out)[:, None]
        return AudioBuffer(out, self.sample_rate)

    # ------------------------------------------------------------- combining

    @staticmethod
    def silence(duration: float, sample_rate: int = 44100, channels: int = 1) -> "AudioBuffer":
        n = max(0, round(duration * sample_rate))
        return AudioBuffer(np.zeros((n, channels)), sample_rate)

    @staticmethod
    def concat(buffers: list["AudioBuffer"]) -> "AudioBuffer":
        if not buffers:
            return AudioBuffer.silence(0.0)
        sr = buffers[0].sample_rate
        ch = max(b.channels for b in buffers)
        parts = [(b.stereo() if ch == 2 else b).samples for b in buffers]
        return AudioBuffer(np.concatenate(parts, axis=0), sr)

    @staticmethod
    def mix(buffers: list[tuple["AudioBuffer", int]], sample_rate: int = 44100) -> "AudioBuffer":
        """Mix (buffer, frame_offset) pairs onto a common timeline."""
        if not buffers:
            return AudioBuffer.silence(0.0, sample_rate)
        ch = max(b.channels for b, _ in buffers)
        total = max(off + b.num_frames for b, off in buffers)
        acc = np.zeros((total, ch))
        for b, off in buffers:
            src = (b.stereo() if ch == 2 else b).samples
            acc[off : off + src.shape[0]] += src
        return AudioBuffer(acc, sample_rate)

    def resampled_frames(self, n_out: int) -> "AudioBuffer":
        """Linear resample to an exact frame count, keeping the sample rate.

        Going through an integer sample rate lands a sample or two off the
        requested length, which matters when fitting a loop to a bar.
        """
        n_out = max(1, int(n_out))
        if self.num_frames == 0:
            return AudioBuffer(np.zeros((n_out, self.channels)), self.sample_rate)
        if n_out == self.num_frames:
            return self.copy()
        src_idx = np.linspace(0.0, self.num_frames - 1, n_out)
        grid = np.arange(self.num_frames)
        cols = [np.interp(src_idx, grid, self.samples[:, c]) for c in range(self.channels)]
        return AudioBuffer(np.stack(cols, axis=1), self.sample_rate)

    def resampled(self, new_rate: int) -> "AudioBuffer":
        """Linear-interpolation resample. Changes pitch (tape behaviour)."""
        if new_rate == self.sample_rate or self.num_frames == 0:
            return AudioBuffer(self.samples.copy(), new_rate)
        ratio = new_rate / self.sample_rate
        n_out = max(1, round(self.num_frames * ratio))
        src_idx = np.linspace(0, self.num_frames - 1, n_out)
        cols = [
            np.interp(src_idx, np.arange(self.num_frames), self.samples[:, c])
            for c in range(self.channels)
        ]
        return AudioBuffer(np.stack(cols, axis=1), new_rate)

    # ------------------------------------------------------------ inspection

    def peaks(self, columns: int) -> tuple[np.ndarray, np.ndarray]:
        """Min/max peak reduction for waveform display (see spec section 16)."""
        n = self.num_frames
        if n == 0 or columns <= 0:
            return np.zeros(0), np.zeros(0)
        mono = self.samples.mean(axis=1)
        if n <= columns:
            return mono.copy(), mono.copy()
        edges = np.linspace(0, n, columns + 1).astype(np.int64)
        lo = np.empty(columns)
        hi = np.empty(columns)
        for i in range(columns):
            seg = mono[edges[i] : max(edges[i] + 1, edges[i + 1])]
            lo[i] = seg.min()
            hi[i] = seg.max()
        return lo, hi

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"AudioBuffer({self.num_frames} frames, {self.channels}ch, "
            f"{self.sample_rate}Hz, {self.duration:.3f}s, peak={self.peak():.3f})"
        )
