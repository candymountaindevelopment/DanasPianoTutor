"""The synthesis engine.

The render path is the whole thesis of the product in one function:

    time structure -> time map -> trajectories evaluated on remapped time
    -> phase integral -> oscillator -> envelope -> filters -> buffer

Because the pitch trajectory is evaluated at remapped positions and phase is
integrated from it, stretching a sound changes how long it spends at each
frequency but never which frequencies it visits. Pitch identity is exact, not
approximate (spec section 98.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from ..audio.buffer import AudioBuffer
from ..core.overrides import resolve_duration
from .drift import Drift
from .envelope import ADSR
from .filters import FilterNode, apply_stack, brightness_chain
from .oscillator import generate
from .timestructure import STRETCH_MODES, TimeMap, TimeStructure
from .trajectory import Trajectory

MIN_HZ = 1.0


@dataclass
class SynthParams:
    oscillator: str = "square"
    duration: float = 0.25
    pitch: Trajectory = field(default_factory=lambda: Trajectory.ramp(880.0, 220.0, "exponential"))
    duty: Trajectory = field(default_factory=lambda: Trajectory.constant(0.5))
    amplitude: float = 0.8
    pan: float = 0.0
    brightness: float = 0.5
    envelope: ADSR = field(default_factory=ADSR)
    drift: Drift = field(default_factory=Drift)
    filters: list[FilterNode] = field(default_factory=list)
    seed: int = 0
    stretch_mode: str = "preserve_impact"
    time_structure: TimeStructure | None = None
    # The note at which this recipe sounds exactly as authored. Patterns
    # transpose relative to it. None means "derive from the pitch at u=0".
    root_note: str | None = None

    def copy(self) -> "SynthParams":
        return SynthParams.from_dict(self.to_dict())

    def structure(self, stretch_mode: str | None = None) -> TimeStructure:
        """A fresh structure synced to the current duration, deriving one if absent.

        Never mutates self: rendering must be free of side effects, or the
        parameter hash changes between the first and second render of the same
        asset and the cache can never hit.
        """
        if self.time_structure is not None:
            ts = TimeStructure.from_dict(self.time_structure.to_dict())
        else:
            ts = TimeStructure.from_envelope(
                self.envelope.attack, self.envelope.decay, self.envelope.release, self.duration
            )
        ts.nominal_duration = self.duration
        mode = stretch_mode or self.stretch_mode
        if mode in STRETCH_MODES and mode != "custom":
            ts.apply_preset(mode)
        return ts

    def ensure_structure(self) -> TimeStructure:
        """Materialise the derived structure onto the asset so the GUI can edit it."""
        if self.time_structure is None:
            self.time_structure = self.structure()
        else:
            self.time_structure.nominal_duration = self.duration
        return self.time_structure

    def to_dict(self) -> dict:
        return {
            "oscillator": self.oscillator,
            "duration": self.duration,
            "pitch": self.pitch.to_dict(),
            "duty": self.duty.to_dict(),
            "amplitude": self.amplitude,
            "pan": self.pan,
            "brightness": self.brightness,
            "envelope": self.envelope.to_dict(),
            "drift": self.drift.to_dict(),
            "filters": [f.to_dict() for f in self.filters],
            "seed": self.seed,
            "stretch_mode": self.stretch_mode,
            "time_structure": self.time_structure.to_dict() if self.time_structure else None,
            "root_note": self.root_note,
        }

    @staticmethod
    def from_dict(d: dict) -> "SynthParams":
        # Missing keys fall back to the dataclass defaults, not to separate
        # literals here: a project dict without a key must load exactly what a
        # fresh SynthParams() would produce.
        base = SynthParams()
        ts = d.get("time_structure")
        return SynthParams(
            oscillator=d.get("oscillator", base.oscillator),
            duration=float(d.get("duration", base.duration)),
            pitch=Trajectory.coerce(d["pitch"]) if "pitch" in d else base.pitch,
            duty=Trajectory.coerce(d["duty"]) if "duty" in d else base.duty,
            amplitude=float(d.get("amplitude", base.amplitude)),
            pan=float(d.get("pan", base.pan)),
            brightness=float(d.get("brightness", base.brightness)),
            envelope=ADSR.from_dict(d["envelope"]) if "envelope" in d else base.envelope,
            drift=Drift.from_dict(d["drift"]) if "drift" in d else base.drift,
            filters=[FilterNode.from_dict(f) for f in d.get("filters", [])],
            seed=int(d.get("seed", base.seed)),
            stretch_mode=d.get("stretch_mode", base.stretch_mode),
            time_structure=TimeStructure.from_dict(ts) if ts else None,
            root_note=d.get("root_note", base.root_note),
        )


@dataclass
class RenderResult:
    buffer: AudioBuffer
    warnings: list[str] = field(default_factory=list)
    time_map: TimeMap | None = None
    effective: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.buffer.duration


def _apply_spectral_overrides(nodes: list[FilterNode], ov: dict) -> list[FilterNode]:
    """Route spectral_* overrides onto the first emphasis node, creating one if needed."""
    keys = ("spectral_center", "spectral_width", "spectral_amount")
    if not any(k in ov for k in keys):
        return nodes
    nodes = [FilterNode(n.type, dict(n.params), n.enabled, n.id) for n in nodes]
    target = next((n for n in nodes if n.type == "emphasis"), None)
    if target is None:
        target = FilterNode("emphasis", dict(), True, "_instance_emphasis")
        nodes.append(target)
    if "spectral_center" in ov:
        base = target.param("center")
        base = base if isinstance(base, (int, float)) else 1800.0
        target.params["center"] = float(base) * float(ov["spectral_center"])
    if "spectral_width" in ov:
        target.params["width"] = float(ov["spectral_width"])
    if "spectral_amount" in ov:
        base = target.param("amount")
        base = base if isinstance(base, (int, float)) else 0.0
        target.params["amount"] = float(base) + float(ov["spectral_amount"])
    return nodes


def render(
    params: SynthParams,
    sample_rate: int = 44100,
    overrides: dict | None = None,
    tempo: float = 120.0,
    preview: bool = False,
) -> RenderResult:
    """Render a synth recipe to audio, applying instance overrides if given."""
    ov = dict(overrides or {})
    warnings: list[str] = []
    p = params

    # --- structure and target length -------------------------------------
    mode = str(ov["stretch_mode"]) if "stretch_mode" in ov else p.stretch_mode
    ts = p.structure(mode)
    target = resolve_duration(ov.get("duration"), p.duration, tempo)
    target = max(1.0 / sample_rate, float(target))
    time_map, w = ts.build_map(target)
    warnings += w

    n = max(1, int(round(target * sample_rate)))
    t = np.arange(n, dtype=np.float64) / sample_rate
    u = time_map.to_normalized(t)

    # --- deterministic randomness ----------------------------------------
    seed = int(p.seed) + int(ov.get("seed_offset", 0))
    rng = np.random.default_rng(seed & 0xFFFFFFFF)

    # --- pitch: evaluated on remapped time, then integrated into phase ----
    f = p.pitch.evaluate(u)
    if "pitch_offset" in ov:
        f = f * (2.0 ** (float(ov["pitch_offset"]) / 12.0))

    drift = p.drift
    if "drift" in ov:
        drift = replace(drift, authenticity=drift.authenticity * float(ov["drift"]))
    if drift.active:
        f = f * drift.pitch_multiplier(t, u, rng)

    nyquist = 0.5 * sample_rate
    if np.any(f >= nyquist):
        warnings.append("pitch trajectory exceeded Nyquist; clamped")
    f = np.clip(f, MIN_HZ, 0.98 * nyquist)

    phase = np.cumsum(2.0 * np.pi * f / sample_rate)

    # --- oscillator -------------------------------------------------------
    duty = p.duty.evaluate(u)
    if "duty" in ov:
        duty = np.full(n, float(ov["duty"]))
    wave = generate(p.oscillator, phase, duty, rng)

    # --- amplitude --------------------------------------------------------
    amp = p.envelope.evaluate(u, p.duration)
    if drift.active:
        amp = amp * drift.amp_multiplier(t, u, rng)
    y = wave * amp * float(p.amplitude)
    if drift.active:
        y = y + drift.noise(n, rng) * amp

    # --- filters ----------------------------------------------------------
    brightness = float(ov.get("brightness", p.brightness))
    nodes = brightness_chain(brightness, sample_rate) + list(p.filters)
    nodes = _apply_spectral_overrides(nodes, ov)
    if preview:
        nodes = [nd for nd in nodes if nd.type not in ("sr_reduce",)]
    y, fw = apply_stack(y, nodes, sample_rate, u)
    warnings += fw

    if not np.all(np.isfinite(y)):
        warnings.append("render produced non-finite samples; they were zeroed")
        y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)

    # --- post-process overrides ------------------------------------------
    buf = AudioBuffer(y, sample_rate)
    if "volume_db" in ov:
        buf = buf.gain_db(float(ov["volume_db"]))
    pan = float(p.pan) + float(ov.get("pan", 0.0))
    if abs(pan) > 1e-6:
        buf = buf.panned(pan)

    if buf.peak() > 1.0:
        warnings.append(f"output exceeds 0 dBFS (peak {buf.peak():.2f})")

    effective = {
        "duration": target,
        "stretch_mode": mode,
        "brightness": brightness,
        "seed": seed,
        "factors": time_map.factors.tolist(),
    }
    return RenderResult(buf, warnings, time_map, effective)


# ------------------------------------------------------------------ presets

def preset(name: str) -> SynthParams:
    """Starting points that demonstrate the model. Used by the New Asset menu."""
    if name == "laser":
        return SynthParams(
            oscillator="square",
            duration=0.18,
            pitch=Trajectory.ramp(900.0, 120.0, "exponential"),
            duty=Trajectory.constant(0.25),
            envelope=ADSR(0.001, 0.02, 0.7, 0.06),
            drift=Drift(0.2),
            brightness=0.6,
            stretch_mode="preserve_attack",
        )
    if name == "coin":
        return SynthParams(
            oscillator="square",
            duration=0.16,
            pitch=Trajectory([(0.0, 988.0), (0.18, 988.0), (0.2, 1319.0), (1.0, 1319.0)], "step"),
            duty=Trajectory.constant(0.5),
            envelope=ADSR(0.002, 0.02, 0.8, 0.09),
            drift=Drift(0.12),
            brightness=0.62,
            stretch_mode="preserve_impact",
        )
    if name == "jump":
        return SynthParams(
            oscillator="square",
            duration=0.14,
            pitch=Trajectory.ramp(220.0, 660.0, "exponential"),
            duty=Trajectory.constant(0.125),
            envelope=ADSR(0.001, 0.03, 0.6, 0.05),
            drift=Drift(0.15),
            brightness=0.55,
            stretch_mode="preserve_attack",
        )
    if name == "hit":
        return SynthParams(
            oscillator="noise",
            duration=0.20,
            amplitude=0.6,  # the emphasis filter adds ~5 dB; leave headroom
            pitch=Trajectory.ramp(1800.0, 200.0, "exponential"),
            envelope=ADSR(0.001, 0.05, 0.25, 0.12),
            drift=Drift(0.3),
            brightness=0.45,
            stretch_mode="preserve_impact",
            filters=[FilterNode("emphasis", {"center": 900.0, "width": 1.2, "amount": 5.0}, True, "f1")],
        )
    if name == "explosion":
        return SynthParams(
            oscillator="white",
            duration=0.70,
            pitch=Trajectory.ramp(400.0, 60.0, "exponential"),
            envelope=ADSR(0.004, 0.20, 0.35, 0.45),
            drift=Drift(0.4),
            brightness=0.35,
            stretch_mode="preserve_impact",
            filters=[
                FilterNode("lowpass", {"cutoff": Trajectory.ramp(6000.0, 400.0), "resonance": 0.9}, True, "f1"),
                FilterNode("bitcrush", {"bits": 7.0}, True, "f2"),
            ],
        )
    if name == "pad":
        # A slow swell for background beds. The cutoff trajectory is what keeps
        # it from sounding static over several seconds; drift adds a lazy
        # detune on top. Deliberately no reverb — a lengthening effect in a
        # preset would make the rendered length disagree with the requested
        # duration, which confuses stretching and the Time Structure view.
        return SynthParams(
            oscillator="pulse_pair",
            duration=4.0,
            amplitude=0.5,
            pitch=Trajectory.constant(130.8128),  # C3
            root_note="C3",
            duty=Trajectory.constant(0.42),
            envelope=ADSR(0.9, 0.7, 0.85, 1.4),
            drift=Drift(0.3, 0.22, 0.10, 0.12, 0.0015),
            brightness=0.42,
            stretch_mode="uniform",
            filters=[
                FilterNode(
                    "lowpass",
                    {"cutoff": Trajectory([(0.0, 650.0), (0.45, 2300.0), (1.0, 900.0)]),
                     "resonance": 0.9},
                    True, "f1",
                ),
                FilterNode("emphasis", {"center": 260.0, "width": 1.4, "amount": 4.5}, True, "f2"),
            ],
        )
    if name == "drone":
        # Flat-topped so it loops: near-zero attack and release, sustain at 1.0,
        # constant pitch. Movement comes from very slow drift rather than the
        # envelope, so the start and end sit at the same level.
        return SynthParams(
            oscillator="saw",
            duration=3.0,
            amplitude=0.45,
            pitch=Trajectory.constant(65.4064),  # C2
            root_note="C2",
            envelope=ADSR(0.03, 0.0, 1.0, 0.03),
            drift=Drift(0.35, 0.13, 0.07, 0.10, 0.002),
            brightness=0.28,
            stretch_mode="uniform",
            filters=[
                FilterNode("lowpass", {"cutoff": 520.0, "resonance": 1.3}, True, "f1"),
                FilterNode("emphasis", {"center": 130.0, "width": 1.2, "amount": 5.0}, True, "f2"),
            ],
        )
    if name == "blip":
        return SynthParams(
            oscillator="triangle",
            duration=0.08,
            pitch=Trajectory.constant(1046.5),
            envelope=ADSR(0.001, 0.01, 0.5, 0.04),
            drift=Drift(0.1),
            brightness=0.6,
            stretch_mode="preserve_attack",
        )
    return SynthParams()


PRESET_NAMES = ("laser", "coin", "jump", "hit", "explosion", "blip", "pad", "drone")
