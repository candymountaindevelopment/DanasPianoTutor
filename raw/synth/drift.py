"""Hardware Drift — the imperfections that make synthesis sound like hardware.

Drift modulators run in ABSOLUTE time by default: a 3 Hz LFO stays 3 Hz
whether the note is short or long, because it models the machine and not the
note (spec section 98.4).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Drift:
    authenticity: float = 0.25  # master intensity, 0..1
    pitch_lfo_hz: float = 3.0
    pitch_depth: float = 0.15  # semitones at authenticity 1.0
    amp_depth: float = 0.10
    noise_floor: float = 0.002
    time_domain: str = "absolute"  # "absolute" | "normalized"

    @property
    def active(self) -> bool:
        return self.authenticity > 1e-6

    def _phase(self, t: np.ndarray, u: np.ndarray) -> np.ndarray:
        if self.time_domain == "normalized":
            # Rate is interpreted as cycles across the whole sound.
            return 2.0 * np.pi * self.pitch_lfo_hz * u
        return 2.0 * np.pi * self.pitch_lfo_hz * t

    def pitch_multiplier(self, t: np.ndarray, u: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Multiplicative frequency modulation from pitch drift."""
        if not self.active:
            return np.ones_like(t)
        phase0 = rng.uniform(0.0, 2.0 * np.pi)
        semis = (
            self.pitch_depth
            * self.authenticity
            * np.sin(self._phase(t, u) + phase0)
        )
        return 2.0 ** (semis / 12.0)

    def amp_multiplier(self, t: np.ndarray, u: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        if not self.active or self.amp_depth <= 0:
            return np.ones_like(t)
        phase0 = rng.uniform(0.0, 2.0 * np.pi)
        # A second, slower LFO so amplitude drift does not lock to pitch drift.
        slow = np.sin(self._phase(t, u) * 0.37 + phase0)
        return 1.0 - self.amp_depth * self.authenticity * 0.5 * (1.0 - slow)

    def noise(self, n: int, rng: np.random.Generator) -> np.ndarray:
        if not self.active or self.noise_floor <= 0:
            return np.zeros(n)
        return rng.normal(0.0, self.noise_floor * self.authenticity, n)

    def to_dict(self) -> dict:
        return {
            "authenticity": self.authenticity,
            "pitch_lfo_hz": self.pitch_lfo_hz,
            "pitch_depth": self.pitch_depth,
            "amp_depth": self.amp_depth,
            "noise_floor": self.noise_floor,
            "time_domain": self.time_domain,
        }

    @staticmethod
    def from_dict(d: dict) -> "Drift":
        return Drift(
            float(d.get("authenticity", 0.25)),
            float(d.get("pitch_lfo_hz", 3.0)),
            float(d.get("pitch_depth", 0.15)),
            float(d.get("amp_depth", 0.10)),
            float(d.get("noise_floor", 0.002)),
            d.get("time_domain", "absolute"),
        )
