"""Amplitude envelopes, evaluated over normalised nominal time."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ADSR:
    attack: float = 0.005
    decay: float = 0.04
    sustain: float = 0.6
    release: float = 0.08

    def fitted(self, duration: float) -> "ADSR":
        """Shrink the timed stages proportionally if they exceed the duration."""
        d = max(duration, 1e-6)
        total = self.attack + self.decay + self.release
        if total <= d:
            return self
        s = d / total * 0.999
        return ADSR(self.attack * s, self.decay * s, self.sustain, self.release * s)

    def evaluate(self, u: np.ndarray, duration: float) -> np.ndarray:
        """Amplitude at normalised nominal positions u in [0, 1]."""
        d = max(duration, 1e-6)
        e = self.fitted(d)
        a, dec, rel = e.attack / d, e.decay / d, e.release / d
        sus = float(np.clip(e.sustain, 0.0, 1.0))

        us = [0.0]
        vs = [0.0]
        if a > 0:
            us.append(a)
            vs.append(1.0)
        else:
            vs[0] = 1.0
        if dec > 0:
            us.append(min(1.0, a + dec))
            vs.append(sus)
        rel_start = max(us[-1], 1.0 - rel)
        if rel_start > us[-1]:
            us.append(rel_start)
            vs.append(sus)
        us.append(1.0)
        vs.append(0.0 if rel > 0 else sus)

        # Enforce strict monotonicity for np.interp.
        for i in range(1, len(us)):
            if us[i] <= us[i - 1]:
                us[i] = us[i - 1] + 1e-9
        return np.interp(np.clip(u, 0.0, 1.0), us, vs)

    def to_dict(self) -> dict:
        return {
            "attack": self.attack,
            "decay": self.decay,
            "sustain": self.sustain,
            "release": self.release,
        }

    @staticmethod
    def from_dict(d: dict) -> "ADSR":
        return ADSR(
            float(d.get("attack", 0.005)),
            float(d.get("decay", 0.04)),
            float(d.get("sustain", 0.6)),
            float(d.get("release", 0.08)),
        )
