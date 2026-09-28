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


# --------------------------------------------------------------- struck string

_STRING_SIZE = 2048          # samples in one cycle
_STRING_LEVELS = 9           # brightness steps, blended per sample
_STRING_PARTIALS = 20
_string_bank_cache: np.ndarray | None = None


def _string_bank() -> np.ndarray:
    """Single-cycle waveforms from dark (nearly a sine) to bright (a full stack).

    A struck string is a stack of partials whose upper members die away first,
    so the note is bright at the hammer and dark a moment later. Sweeping a
    rolloff exponent gives exactly that, but summing twenty sines per sample is
    dear when a lesson renders a hundred notes. So the stack is precomputed at
    nine brightnesses and read as a wavetable, two lookups and a blend per
    sample — the cost of the noise oscillator, not of additive synthesis.

    Every level is normalised to the same RMS, so a note that darkens does not
    also swell, and the whole bank is then scaled to peak at 1.0.
    """
    global _string_bank_cache
    if _string_bank_cache is None:
        n = np.arange(1, _STRING_PARTIALS + 1, dtype=np.float64)
        pos = np.arange(_STRING_SIZE, dtype=np.float64) / _STRING_SIZE
        sines = np.sin(_TAU * n[:, None] * pos[None, :])
        # A raised cosine over the top of the stack: a hard edge at partial 20
        # rings as a buzz, and nothing that is struck has one.
        taper = 0.5 * (1.0 + np.cos(np.pi * (n - 1.0) / _STRING_PARTIALS))
        bank = np.empty((_STRING_LEVELS, _STRING_SIZE))
        for k in range(_STRING_LEVELS):
            b = k / (_STRING_LEVELS - 1.0)
            amps = n ** -(3.4 - 2.5 * b) * taper
            wave = amps @ sines
            bank[k] = wave / np.sqrt(np.mean(wave * wave))
        _string_bank_cache = bank / np.max(np.abs(bank))
    return _string_bank_cache


@oscillator("string")
def _string(phase, duty, rng):
    """A struck or plucked string: a harmonic stack whose brightness is `duty`.

    0 is nearly a sine, 1 the full stack. Sweep the duty trajectory downwards
    and the note darkens as it decays, which is what tells an ear that
    something was struck rather than switched on.
    """
    bank = _string_bank()
    levels, size = bank.shape
    pos = _cycle_position(phase) * size
    i0 = np.floor(pos).astype(np.int64)
    frac = pos - i0
    i0 = np.mod(i0, size)
    i1 = np.mod(i0 + 1, size)
    b = np.clip(np.broadcast_to(np.atleast_1d(duty), phase.shape), 0.0, 1.0) * (levels - 1)
    k0 = np.floor(b).astype(np.int64)
    k1 = np.minimum(k0 + 1, levels - 1)
    fk = b - k0
    lo = bank[k0, i0] * (1.0 - frac) + bank[k0, i1] * frac
    hi = bank[k1, i0] * (1.0 - frac) + bank[k1, i1] * frac
    return lo * (1.0 - fk) + hi * fk
