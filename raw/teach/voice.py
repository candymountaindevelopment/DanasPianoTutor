"""Built-in voices for lessons and the metronome, as RAW synth recipes.

These are deliberately filter-free: without scipy a biquad runs in pure
Python, and a lesson renders dozens of notes. Character comes from the
oscillator, the envelope and a little drive instead. Every voice keeps
`stretch_mode = "uniform"` so a long note decays over its whole value, the
way a held piano key sounds to a beginner, rather than dying early.
"""

from __future__ import annotations

from ..synth.drift import Drift
from ..synth.engine import SynthParams
from ..synth.envelope import ADSR
from ..synth.filters import FilterNode
from ..synth.trajectory import Trajectory

C4_HZ = 261.6256

VOICE_NAMES = ("piano", "music_box", "organ", "chip")


def voice(name: str) -> SynthParams:
    """A melodic recipe rooted at C4. Unknown names fall back to the piano."""
    if name == "music_box":
        return SynthParams(
            oscillator="sine", duration=0.5, amplitude=0.75,
            pitch=Trajectory.constant(C4_HZ * 2), root_note="C5",
            envelope=ADSR(0.002, 0.30, 0.15, 0.15),
            drift=Drift(0.0), stretch_mode="uniform",
            filters=[FilterNode("drive", {"amount": 0.6}, True, "v1")],
        )
    if name == "organ":
        return SynthParams(
            oscillator="square", duration=0.5, amplitude=0.45,
            pitch=Trajectory.constant(C4_HZ), root_note="C4",
            duty=Trajectory.constant(0.5),
            envelope=ADSR(0.02, 0.02, 1.0, 0.06),
            drift=Drift(0.05, 5.0, 0.04, 0.02, 0.0), stretch_mode="uniform",
        )
    if name == "chip":
        return SynthParams(
            oscillator="square", duration=0.5, amplitude=0.55,
            pitch=Trajectory.constant(C4_HZ), root_note="C4",
            duty=Trajectory.constant(0.25),
            envelope=ADSR(0.004, 0.12, 0.55, 0.08),
            drift=Drift(0.0), stretch_mode="uniform",
        )
    # piano: a triangle with a quick bright onset, decaying to a soft hold.
    return SynthParams(
        oscillator="triangle", duration=0.5, amplitude=0.8,
        pitch=Trajectory.constant(C4_HZ), root_note="C4",
        envelope=ADSR(0.003, 0.32, 0.28, 0.12),
        drift=Drift(0.0), stretch_mode="uniform",
        filters=[FilterNode("drive", {"amount": 0.9}, True, "v1")],
    )


def click(accent: bool = False, unpitched: bool = False) -> SynthParams:
    """A metronome tick; the accent is higher and slightly longer.

    `unpitched` returns the click used while the microphone is listening
    (Practice mode): a short high noise burst instead of a sine. A sine
    click is perfectly periodic and the pitch detector reports it as a
    played note; raising its pitch above the detector's 2 kHz ceiling would
    not help either, because a periodic tone dips at every multiple of its
    period, so the detector would lock onto a sub-harmonic inside the range.
    Noise has no period to find, and the high-pass keeps its energy above
    the notes a lesson uses.
    """
    if unpitched:
        # Noise, because nothing with a pitch can be made invisible to a pitch
        # detector. A sine is found at once; raising it above the detector's
        # ceiling only moves the find to a sub-harmonic; and two partials beat
        # against each other, which is found at their difference (measured:
        # 2637 + 3349 Hz reads as E5 at clarity 0.91 — right among the notes).
        # Noise has no period at all, and only a light high-pass, because
        # narrowing the band is what throws its loudness away. The player adds
        # make-up gain (`_clicks`); the accent is longer and a little darker.
        return SynthParams(
            oscillator="white", duration=0.06 if accent else 0.045, amplitude=1.0,
            pitch=Trajectory.constant(C4_HZ),          # noise ignores it; the field is required
            envelope=ADSR(0.0008, 0.026 if accent else 0.020, 0.0, 0.02),
            drift=Drift(0.0), stretch_mode="preserve_attack",
            filters=[FilterNode("highpass",
                                {"cutoff": 900.0 if accent else 1200.0, "resonance": 0.8},
                                True, "v1")],
        )
    return SynthParams(
        oscillator="sine", duration=0.045 if accent else 0.03, amplitude=0.7,
        pitch=Trajectory.constant(2200.0 if accent else 1500.0),
        envelope=ADSR(0.001, 0.02, 0.0, 0.01),
        drift=Drift(0.0), stretch_mode="preserve_attack",
    )
