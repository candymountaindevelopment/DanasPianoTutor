"""Sound slots — a named role in a pattern, bound to an asset later.

A pattern track may reference a slot ("kick", "lead") instead of a concrete
asset. Bindings live on the project, so binding `kick` once fills it in every
pattern that uses it; a pattern may override a binding locally when it needs
something different.

An unbound slot is not silent. It renders a short placeholder blip whose pitch
is derived from the slot name, so a whole groove is audible — and each role
distinguishable — before a single sound has been chosen. Silence here would
defeat the point of sketching structure first.

This is the same shape as the Event Map in spec section 76: a logical name
bound to an asset.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# Major scale degrees. Placeholders land on a scale so a sketch is listenable
# rather than a bag of arbitrary frequencies.
PLACEHOLDER_SCALE = (0, 2, 4, 5, 7, 9, 11)
PLACEHOLDER_ROOT_MIDI = 60  # C4
PLACEHOLDER_OCTAVES = 2


@dataclass
class Slot:
    name: str
    asset: str | None = None  # asset uid, or None while unbound
    overrides: dict = field(default_factory=dict)
    description: str = ""

    @property
    def bound(self) -> bool:
        return bool(self.asset)

    def to_dict(self) -> dict:
        out: dict = {"asset": self.asset}
        if self.overrides:
            out["overrides"] = dict(self.overrides)
        if self.description:
            out["description"] = self.description
        return out

    @staticmethod
    def from_dict(name: str, data) -> "Slot":
        if data is None or isinstance(data, str):
            return Slot(name, data or None)
        return Slot(
            name=name,
            asset=data.get("asset") or None,
            overrides=dict(data.get("overrides", {})),
            description=data.get("description", ""),
        )


# ------------------------------------------------------------------ resolving


def pattern_slot_overrides(pattern) -> dict:
    """Per-pattern slot bindings, which win over the project's."""
    if pattern is None:
        return {}
    return (getattr(pattern, "defaults", {}) or {}).get("slots") or {}


def resolve_slot(name: str, pattern, project) -> Slot | None:
    local = pattern_slot_overrides(pattern)
    if name in local:
        return Slot.from_dict(name, local[name])
    return project.slots.get(name)


@dataclass
class TrackSource:
    """What a pattern track will actually play."""

    asset = None
    overrides: dict = field(default_factory=dict)
    slot: str | None = None

    def __init__(self, asset=None, overrides=None, slot=None) -> None:
        self.asset = asset
        self.overrides = dict(overrides or {})
        self.slot = slot

    @property
    def is_placeholder(self) -> bool:
        return self.asset is None and self.slot is not None

    @property
    def label(self) -> str:
        if self.asset is not None:
            return self.asset.name
        if self.slot:
            return f"{self.slot} (unbound)"
        return "(missing)"


def resolve_track_source(track: dict, pattern, project) -> TrackSource:
    """Resolve a track to an asset, via its slot if it has one.

    A track carrying a direct `instrument` uid keeps working untouched — that
    is simply a slot which is already bound.
    """
    slot_name = track.get("slot")
    if slot_name:
        slot = resolve_slot(slot_name, pattern, project)
        asset = project.assets.get(slot.asset) if (slot and slot.asset) else None
        return TrackSource(asset, slot.overrides if slot else {}, slot_name)

    ref = track.get("instrument") or track.get("asset") or ""
    asset = project.assets.get(ref) or project.assets.by_name(ref)
    return TrackSource(asset, {}, None)


def slots_used_by(pattern) -> list[str]:
    return [t["slot"] for t in pattern.tracks if t.get("slot")]


def unbound_slots(pattern, project) -> list[str]:
    out = []
    for name in slots_used_by(pattern):
        slot = resolve_slot(name, pattern, project)
        if slot is None or not slot.bound or project.assets.get(slot.asset) is None:
            out.append(name)
    return out


# --------------------------------------------------------------- placeholders


def placeholder_midi(name: str) -> int:
    """A stable pitch per slot name, so roles stay distinguishable by ear."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    degree = PLACEHOLDER_SCALE[digest[0] % len(PLACEHOLDER_SCALE)]
    octave = digest[1] % PLACEHOLDER_OCTAVES
    return PLACEHOLDER_ROOT_MIDI + degree + 12 * octave


def placeholder_params(name: str):
    """A short, dry blip standing in for an unbound slot."""
    from ..patterns.notes import midi_to_hz, midi_to_note
    from ..synth.drift import Drift
    from ..synth.engine import SynthParams
    from ..synth.envelope import ADSR
    from ..synth.filters import FilterNode
    from ..synth.trajectory import Trajectory

    midi = placeholder_midi(name)
    return SynthParams(
        oscillator="triangle",
        duration=0.09,
        pitch=Trajectory.constant(midi_to_hz(midi)),
        amplitude=0.45,
        envelope=ADSR(0.001, 0.03, 0.25, 0.03),
        brightness=0.5,
        drift=Drift(0.0),
        stretch_mode="preserve_attack",
        root_note=midi_to_note(midi),
        # Deliberately dull, so a placeholder never gets mistaken for a
        # finished sound.
        filters=[FilterNode("lowpass", {"cutoff": 2600.0, "resonance": 0.6}, True, "_ph")],
    )
