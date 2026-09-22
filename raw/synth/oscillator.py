"""Oscillators.

Every oscillator is a pure function of accumulated phase, which is what makes
non-destructive time stretching exact: the caller integrates the (possibly
remapped) pitch trajectory into phase, and the oscillator never sees time.

Register new oscillators with the @oscillator decorator; the GUI and the
scripting API pick them up automatically.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

OSCILLATORS: dict[str, Callable] = {}
_TAU = 2.0 * np.pi


def oscillator(name: str):
    def deco(fn: Callable) -> Callable:
        OSCILLATORS[name] = fn
        return fn

    return deco


def oscillator_names() -> list[str]:
    return list(OSCILLATORS)


def generate(name: str, phase: np.ndarray, duty: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    fn = OSCILLATORS.get(name)
    if fn is None:
        raise KeyError(f"unknown oscillator {name!r}; available: {sorted(OSCILLATORS)}")
    return np.asarray(fn(phase, duty, rng), dtype=np.float64)


def _cycle_position(phase: np.ndarray) -> np.ndarray:
    return np.mod(phase / _TAU, 1.0)


@oscillator("sine")
def _sine(phase, duty, rng):
    return np.sin(phase)


@oscillator("square")
def _square(phase, duty, rng):
    p = _cycle_position(phase)
    return np.where(p < np.clip(duty, 0.01, 0.99), 1.0, -1.0)


@oscillator("triangle")
def _triangle(phase, duty, rng):
    p = _cycle_position(phase)
    return 1.0 - 4.0 * np.abs(np.mod(p + 0.25, 1.0) - 0.5)


@oscillator("saw")
def _saw(phase, duty, rng):
    return 2.0 * _cycle_position(phase) - 1.0


@oscillator("noise")
def _noise(phase, duty, rng):
    """Pitched noise: a value held for each oscillator cycle, as a chip LFSR does."""
    table = rng.uniform(-1.0, 1.0, 4096)
    idx = np.mod(np.floor(phase / _TAU).astype(np.int64), 4096)
    return table[idx]


@oscillator("white")
def _white(phase, duty, rng):
    return rng.uniform(-1.0, 1.0, phase.shape[0])


@oscillator("pulse_pair")
def _pulse_pair(phase, duty, rng):
    """Two detuned pulses. A cheap way to thicken a lead."""
    d = np.clip(duty, 0.01, 0.99)
    a = np.where(_cycle_position(phase) < d, 1.0, -1.0)
    b = np.where(_cycle_position(phase * 1.005) < d, 1.0, -1.0)
    return 0.5 * (a + b)
