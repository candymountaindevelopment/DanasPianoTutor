"""Parameter trajectories defined over normalised nominal time.

Every time-varying generator parameter is a function of u in [0, 1] rather
than of seconds. That is what makes non-destructive stretching possible:
changing a sound's duration changes the *schedule* over which the trajectory
is traversed, never the values it visits (spec section 98).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

CURVES = ("linear", "exponential", "step")


@dataclass
class Trajectory:
    points: list[tuple[float, float]] = field(default_factory=lambda: [(0.0, 0.0)])
    curve: str = "linear"

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("a trajectory needs at least one point")
        if self.curve not in CURVES:
            raise ValueError(f"unknown curve {self.curve!r}, expected one of {CURVES}")
        self.points = sorted(((float(u), float(v)) for u, v in self.points), key=lambda p: p[0])

    # ----------------------------------------------------------- constructors

    @staticmethod
    def constant(value: float) -> "Trajectory":
        return Trajectory([(0.0, float(value))], "linear")

    @staticmethod
    def ramp(start: float, end: float, curve: str = "linear") -> "Trajectory":
        return Trajectory([(0.0, float(start)), (1.0, float(end))], curve)

    @staticmethod
    def coerce(value) -> "Trajectory":
        """Accept a scalar, a dict, or a Trajectory."""
        if isinstance(value, Trajectory):
            return value
        if isinstance(value, dict):
            return Trajectory.from_dict(value)
        return Trajectory.constant(float(value))

    # -------------------------------------------------------------- evaluate

    @property
    def is_constant(self) -> bool:
        return len(self.points) == 1 or len({v for _, v in self.points}) == 1

    def evaluate(self, u: np.ndarray | float) -> np.ndarray:
        """Evaluate at normalised positions u (clamped to [0, 1])."""
        u = np.clip(np.atleast_1d(np.asarray(u, dtype=np.float64)), 0.0, 1.0)
        us = np.array([p[0] for p in self.points])
        vs = np.array([p[1] for p in self.points])
        if len(self.points) == 1:
            return np.full_like(u, vs[0])
        if self.curve == "step":
            idx = np.clip(np.searchsorted(us, u, side="right") - 1, 0, len(vs) - 1)
            return vs[idx]
        if self.curve == "exponential" and np.all(vs > 0):
            # Interpolate in the log domain so a pitch sweep is musically even.
            return np.exp(np.interp(u, us, np.log(vs)))
        return np.interp(u, us, vs)

    def value_at(self, u: float) -> float:
        return float(self.evaluate(np.array([u]))[0])

    def scaled(self, factor: float) -> "Trajectory":
        return Trajectory([(u, v * factor) for u, v in self.points], self.curve)

    def offset(self, delta: float) -> "Trajectory":
        return Trajectory([(u, v + delta) for u, v in self.points], self.curve)

    @property
    def min_value(self) -> float:
        return min(v for _, v in self.points)

    @property
    def max_value(self) -> float:
        return max(v for _, v in self.points)

    # ------------------------------------------------------------- serialise

    def to_dict(self) -> dict:
        return {
            "type": "trajectory",
            "curve": self.curve,
            "points": [{"u": u, "v": v} for u, v in self.points],
        }

    @staticmethod
    def from_dict(d: dict) -> "Trajectory":
        pts = [(float(p["u"]), float(p["v"])) for p in d.get("points", [])]
        return Trajectory(pts or [(0.0, 0.0)], d.get("curve", "linear"))
