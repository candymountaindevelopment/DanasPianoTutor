"""Time Structure and the Time Map (spec sections 95-97).

A sound's nominal timeline is partitioned into regions with an elasticity
weight. Stretching solves for a per-region factor and builds a monotonic,
piecewise map from output time back to nominal time. Everything downstream
consumes that map, so every stretch behaviour shares one code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

ROLES = ("attack", "transient", "body", "tail", "custom")
REGION_MODES = ("stretch", "sustain", "loop")
STRETCH_MODES = ("uniform", "preserve_attack", "preserve_impact", "custom")

# Elasticity presets. Roles absent from a preset keep elasticity 1.0.
_PRESETS: dict[str, dict[str, float]] = {
    "uniform": {"attack": 1.0, "transient": 1.0, "body": 1.0, "tail": 1.0},
    "preserve_attack": {"attack": 0.0, "transient": 1.0, "body": 1.0, "tail": 1.0},
    "preserve_impact": {"attack": 0.0, "transient": 0.0, "body": 1.0, "tail": 0.35},
}

MIN_FACTOR = 0.05
MAX_FACTOR = 20.0


class StructureWarning(UserWarning):
    """Raised through the validation channel, never fatal."""


@dataclass
class Region:
    id: str
    role: str = "body"
    start: float = 0.0
    end: float = 1.0
    elasticity: float = 1.0
    min_duration: float = 0.0
    max_duration: float | None = None
    mode: str = "stretch"

    @property
    def extent(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "start": self.start,
            "end": self.end,
            "elasticity": self.elasticity,
            "min_duration": self.min_duration,
            "max_duration": self.max_duration,
            "mode": self.mode,
        }

    @staticmethod
    def from_dict(d: dict) -> "Region":
        return Region(
            id=d.get("id", "r"),
            role=d.get("role", "body"),
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 1.0)),
            elasticity=float(d.get("elasticity", 1.0)),
            min_duration=float(d.get("min_duration", 0.0)),
            max_duration=(None if d.get("max_duration") is None else float(d["max_duration"])),
            mode=d.get("mode", "stretch"),
        )


class TimeMap:
    """Monotonic piecewise map from output time to nominal time."""

    def __init__(
        self,
        nominal_bounds: np.ndarray,
        output_bounds: np.ndarray,
        modes: list[str],
    ) -> None:
        self.nominal_bounds = np.asarray(nominal_bounds, dtype=np.float64)
        self.output_bounds = np.asarray(output_bounds, dtype=np.float64)
        self.modes = list(modes)

    @property
    def nominal_duration(self) -> float:
        return float(self.nominal_bounds[-1])

    @property
    def output_duration(self) -> float:
        return float(self.output_bounds[-1])

    @property
    def factors(self) -> np.ndarray:
        nom = np.diff(self.nominal_bounds)
        out = np.diff(self.output_bounds)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(nom > 0, out / nom, 1.0)

    def to_nominal(self, t: np.ndarray | float) -> np.ndarray:
        """Map output-time seconds to nominal-time seconds."""
        t = np.atleast_1d(np.asarray(t, dtype=np.float64))
        out = np.empty_like(t)
        n = len(self.modes)
        idx = np.clip(np.searchsorted(self.output_bounds, t, side="right") - 1, 0, n - 1)
        for i in range(n):
            m = idx == i
            if not np.any(m):
                continue
            local = t[m] - self.output_bounds[i]
            nom_len = self.nominal_bounds[i + 1] - self.nominal_bounds[i]
            out_len = self.output_bounds[i + 1] - self.output_bounds[i]
            base = self.nominal_bounds[i]
            mode = self.modes[i]
            if nom_len <= 0 or out_len <= 0:
                out[m] = base
            elif mode == "sustain" and out_len > nom_len:
                # Hold at the region's entry value, then traverse at rate 1.
                hold = out_len - nom_len
                out[m] = base + np.maximum(0.0, local - hold)
            elif mode == "loop" and out_len > nom_len:
                out[m] = base + np.mod(local, nom_len)
            else:
                out[m] = base + local * (nom_len / out_len)
        return np.clip(out, 0.0, self.nominal_duration)

    def to_normalized(self, t: np.ndarray | float) -> np.ndarray:
        """Map output-time seconds to normalised nominal position u in [0, 1]."""
        d = self.nominal_duration
        if d <= 0:
            return np.zeros_like(np.atleast_1d(np.asarray(t, dtype=np.float64)))
        return self.to_nominal(t) / d


@dataclass
class TimeStructure:
    nominal_duration: float = 0.25
    regions: list[Region] = field(default_factory=list)

    # ------------------------------------------------------------ derivation

    @staticmethod
    def single_body(duration: float) -> "TimeStructure":
        return TimeStructure(duration, [Region("r_body", "body", 0.0, 1.0, 1.0)])

    @staticmethod
    def from_envelope(attack: float, decay: float, release: float, duration: float) -> "TimeStructure":
        """Default derivation for synthesised sounds (spec section 95.6)."""
        d = max(duration, 1e-6)
        a = max(0.0, min(attack, d))
        dec = max(0.0, min(decay, 0.3 * d))
        rel = max(0.0, min(release, d - a))
        b0 = a / d
        b1 = min(1.0, (a + dec) / d)
        b2 = max(b1, (d - rel) / d)
        raw = [
            ("r_attack", "attack", 0.0, b0, 0.0, 0.002),
            ("r_impact", "transient", b0, b1, 0.0, 0.004),
            ("r_body", "body", b1, b2, 1.0, 0.0),
            ("r_tail", "tail", b2, 1.0, 0.35, 0.0),
        ]
        regions = [
            Region(rid, role, s, e, el, mn)
            for rid, role, s, e, el, mn in raw
            if e - s > 1e-9
        ]
        if not regions:
            return TimeStructure.single_body(d)
        regions[0].start = 0.0
        regions[-1].end = 1.0
        return TimeStructure(d, regions)

    # ------------------------------------------------------------ validation

    def issues(self) -> list[str]:
        out: list[str] = []
        if not self.regions:
            return ["time structure has no regions"]
        if abs(self.regions[0].start) > 1e-9:
            out.append("first region does not start at 0.0")
        if abs(self.regions[-1].end - 1.0) > 1e-9:
            out.append("last region does not end at 1.0")
        seen = set()
        for i, r in enumerate(self.regions):
            if r.id in seen:
                out.append(f"duplicate region id {r.id!r}")
            seen.add(r.id)
            if r.extent <= 0:
                out.append(f"region {r.id!r} has non-positive extent")
            if r.elasticity < 0:
                out.append(f"region {r.id!r} has negative elasticity")
            if r.max_duration is not None and r.max_duration < r.min_duration:
                out.append(f"region {r.id!r} has max_duration below min_duration")
            if r.mode not in REGION_MODES:
                out.append(f"region {r.id!r} has unknown mode {r.mode!r}")
            if i + 1 < len(self.regions) and abs(r.end - self.regions[i + 1].start) > 1e-9:
                out.append(f"regions {r.id!r} and {self.regions[i + 1].id!r} do not tile")
        return out

    def normalize(self) -> None:
        """Repair tiling in place, keeping the ordering the user authored."""
        if not self.regions:
            self.regions = [Region("r_body", "body", 0.0, 1.0, 1.0)]
            return
        self.regions.sort(key=lambda r: r.start)
        self.regions[0].start = 0.0
        for i in range(len(self.regions) - 1):
            edge = max(self.regions[i].end, self.regions[i].start + 1e-6)
            self.regions[i].end = edge
            self.regions[i + 1].start = edge
        self.regions[-1].end = 1.0

    # --------------------------------------------------------------- presets

    def apply_preset(self, mode: str) -> None:
        preset = _PRESETS.get(mode)
        if preset is None:
            return
        for r in self.regions:
            r.elasticity = preset.get(r.role, 1.0)

    # ---------------------------------------------------------------- solver

    def nominal_durations(self) -> np.ndarray:
        return np.array([r.extent * self.nominal_duration for r in self.regions])

    def solve(self, target_duration: float) -> tuple[np.ndarray, list[str]]:
        """Solve for per-region stretch factors (spec section 96.2).

        Returns (factors, warnings). Never raises for an unreachable target:
        it degrades to uniform compression and warns instead.
        """
        warnings: list[str] = []
        d = self.nominal_durations()
        n = len(d)
        if n == 0:
            return np.ones(0), ["no regions to solve"]
        target = max(float(target_duration), 1e-6)
        elast = np.array([max(0.0, r.elasticity) for r in self.regions])

        floor = float(
            sum(
                (d[i] if elast[i] == 0 else max(self.regions[i].min_duration, d[i] * MIN_FACTOR))
                for i in range(n)
            )
        )
        if target < floor - 1e-9:
            warnings.append(
                f"requested {target:.3f}s but rigid regions require {floor:.3f}s; "
                "compressing uniformly (structure violated)"
            )
            return np.full(n, target / max(d.sum(), 1e-12)), warnings

        factors = np.ones(n)
        free = elast > 0.0
        for _ in range(n + 1):
            W = float(np.sum(elast[free] * d[free]))
            fixed = float(np.sum(factors[~free] * d[~free]))
            if W <= 1e-12:
                if abs(fixed - target) > 1e-6 and not free.any():
                    scale = target / max(fixed, 1e-12)
                    factors = factors * scale
                    warnings.append(
                        "no elastic regions available; scaled all regions uniformly"
                    )
                break
            k = (target - fixed - float(np.sum(d[free]))) / W
            trial = factors.copy()
            trial[free] = 1.0 + elast[free] * k

            clamped = False
            for i in np.flatnonzero(free):
                lo = max(MIN_FACTOR, self.regions[i].min_duration / max(d[i], 1e-12))
                hi = MAX_FACTOR
                if self.regions[i].max_duration is not None:
                    hi = min(hi, self.regions[i].max_duration / max(d[i], 1e-12))
                hi = max(hi, lo)
                if trial[i] < lo - 1e-12 or trial[i] > hi + 1e-12:
                    factors[i] = float(np.clip(trial[i], lo, hi))
                    free[i] = False
                    clamped = True
                else:
                    factors[i] = trial[i]
            if not clamped:
                break

        achieved = float(np.sum(factors * d))
        if abs(achieved - target) > 1e-4:
            warnings.append(
                f"clamped regions prevented an exact fit: {achieved:.3f}s vs {target:.3f}s requested"
            )
            if achieved > 1e-12:
                factors = factors * (target / achieved)
        return factors, warnings

    def build_map(self, target_duration: float | None = None) -> tuple[TimeMap, list[str]]:
        target = self.nominal_duration if target_duration is None else target_duration
        factors, warnings = self.solve(target)
        d = self.nominal_durations()
        nominal_bounds = np.concatenate([[0.0], np.cumsum(d)])
        output_bounds = np.concatenate([[0.0], np.cumsum(factors * d)])
        modes = [r.mode for r in self.regions]
        return TimeMap(nominal_bounds, output_bounds, modes), warnings

    # ------------------------------------------------------------- serialise

    def to_dict(self) -> dict:
        return {
            "nominal_duration": self.nominal_duration,
            "regions": [r.to_dict() for r in self.regions],
        }

    @staticmethod
    def from_dict(d: dict) -> "TimeStructure":
        ts = TimeStructure(
            float(d.get("nominal_duration", 0.25)),
            [Region.from_dict(r) for r in d.get("regions", [])],
        )
        if not ts.regions:
            ts.regions = [Region("r_body", "body", 0.0, 1.0, 1.0)]
        return ts
