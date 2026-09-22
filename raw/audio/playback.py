"""Playback abstraction.

Nothing outside this module imports an audio backend. Swapping sounddevice for
Qt Multimedia or PortAudio later means adding one class here (spec section 47).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from .buffer import AudioBuffer


class PlaybackBackend(ABC):
    name = "none"
    available = False

    @abstractmethod
    def play(self, buffer: AudioBuffer, loop: bool = False) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    def is_playing(self) -> bool:
        return False

    def set_volume(self, volume: float) -> None:
        self._volume = float(np.clip(volume, 0.0, 1.0))

    def close(self) -> None:
        self.stop()


class NullBackend(PlaybackBackend):
    """Used when no audio device is available. Renders still work."""

    name = "null"
    available = False

    def __init__(self) -> None:
        self._volume = 1.0

    def play(self, buffer: AudioBuffer, loop: bool = False) -> None:
        return

    def stop(self) -> None:
        return


class SoundDeviceBackend(PlaybackBackend):
    name = "sounddevice"
    available = True

    def __init__(self) -> None:
        import sounddevice as sd  # imported lazily so the app starts without it

        self._sd = sd
        self._volume = 1.0

    def play(self, buffer: AudioBuffer, loop: bool = False) -> None:
        data = np.clip(buffer.samples * self._volume, -1.0, 1.0).astype(np.float32)
        self._sd.stop()
        self._sd.play(data, buffer.sample_rate, loop=loop)

    def stop(self) -> None:
        try:
            self._sd.stop()
        except Exception:
            pass

    @property
    def is_playing(self) -> bool:
        try:
            return bool(self._sd.get_stream().active)
        except Exception:
            return False


def get_backend() -> PlaybackBackend:
    """Best available backend, degrading silently to null."""
    try:
        return SoundDeviceBackend()
    except Exception:
        return NullBackend()
