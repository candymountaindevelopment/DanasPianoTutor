"""Pattern rendering: structured events -> audio, through the instrument system.

A note does not replace an instrument's pitch trajectory — it transposes it.
The event resolves to a `pitch_offset` override of
`note_midi - instrument_root_midi`, so a laser played at C5 is the same sweep
one octave up, not a flat tone. Everything else the event carries becomes an
override too, which means pattern playback and the timeline share one code path
(spec sections 39 and 101).
"""

from __future__ import annotations

import math

from ..audio.buffer import AudioBuffer
from ..core.overrides import musical_to_seconds, resolve
from ..core.slots import placeholder_params, resolve_track_source
from .notes import hz_to_midi, note_to_midi, parse_pitch_token

DEFAULT_STEP = "1/16"


class _Voice:
    """Just enough of an asset for the transposition maths."""

    def __init__(self, params) -> None:
        self.params = params


def _placeholder_voice(slot_name: str) -> _Voice:
    return _Voice(placeholder_params(slot_name))


def instrument_root_midi(asset) -> float:
    """The note at which an instrument sounds exactly as authored."""
    params = getattr(asset, "params", None)
    if params is None:
        return 69.0
    root = getattr(params, "root_note", None)
    if root:
        try:
            return note_to_midi(root)
        except Exception:
            pass
    return hz_to_midi(params.pitch.value_at(0.0))


def step_seconds(pattern, tempo: float) -> float:
    division = getattr(pattern, "step_division", None) or DEFAULT_STEP
    return musical_to_seconds(division, tempo)


def event_overrides(event: dict, instrument, pattern, track: dict, tempo: float) -> dict:
    """Build the override dict for one pattern event."""
    ov: dict = {}

    note = event.get("note")
    if note is not None:
        midi = parse_pitch_token(note)
        ov["pitch_offset"] = round(midi - instrument_root_midi(instrument), 6)

    length = event.get("length")
    if length is not None:
        if isinstance(length, str):
            ov["duration"] = {"musical": length}
        else:
            ov["duration"] = float(length) * step_seconds(pattern, tempo)

    velocity = event.get("velocity")
    if velocity is not None:
        v = max(float(velocity), 1e-3)
        ov["volume_db"] = round(20.0 * math.log10(v), 4)

    return ov


def _layer_chain(pattern, track: dict, event: dict, base_ov: dict,
                 instance_ov: dict, slot_ov: dict) -> list[tuple[str, dict]]:
    """Precedence, lowest first. The note itself sits between the track and the
    event so an event can still override the pitch the note implied.

    Slot overrides sit below the track: a slot is the role's default level and
    tuning, which a specific pattern or note may still push against.
    """
    return [
        ("pattern", dict(pattern.defaults.get("overrides", {}))),
        ("instance", dict(instance_ov)),
        ("slot", dict(slot_ov)),
        ("track", dict(track.get("overrides", {}))),
        ("note", base_ov),
        ("event", dict(event.get("overrides", {}))),
    ]


def render_pattern(pattern, project, renderer, tempo: float | None = None,
                   overrides: dict | None = None,
                   preview: bool = False) -> tuple[AudioBuffer, list[str]]:
    """Render a PatternAsset to a mixed buffer.

    `overrides` are instance-level overrides on the pattern as a whole; they
    apply to every event. `duration` is excluded because a pattern's length is
    set by its steps and tempo, not by an event duration.
    """
    warnings: list[str] = []
    sr = project.settings.sample_rate
    instance_ov = {k: v for k, v in (overrides or {}).items() if k != "duration"}
    bpm = float(tempo or pattern.tempo or project.settings.tempo)
    step_len = step_seconds(pattern, bpm)
    swing = float(getattr(pattern, "swing", 0.0))
    parts: list[tuple[AudioBuffer, int]] = []

    for track in pattern.tracks:
        if track.get("mute"):
            continue
        source = resolve_track_source(track, pattern, project)
        instrument = source.asset
        placeholder_for = None

        if instrument is None:
            if source.slot:
                # Unbound slot: audible stand-in, so the groove can be judged
                # before any sound has been chosen.
                placeholder_for = source.slot
                warnings.append(
                    f"slot {source.slot!r} is not bound — using a placeholder click"
                )
            else:
                ref = track.get("instrument") or track.get("asset") or ""
                warnings.append(
                    f"track {track.get('name', '?')!r} references unknown instrument {ref!r}"
                )
                continue
        elif not hasattr(instrument, "params"):
            warnings.append(
                f"track {track.get('name', '?')!r} instrument {instrument.name!r} is not playable"
            )
            continue

        for event in track.get("events", []):
            step = float(event.get("step", 0))
            start = step * step_len
            if swing > 0 and int(step) % 2 == 1:
                start += swing * step_len * 0.5

            voice = instrument if instrument is not None else _placeholder_voice(placeholder_for)
            base = event_overrides(event, voice, pattern, track, bpm)
            merged = resolve(
                {}, _layer_chain(pattern, track, event, base, instance_ov, source.overrides)
            )
            try:
                if placeholder_for is not None:
                    buf = renderer.render_placeholder(placeholder_for, project, merged, preview)
                else:
                    buf = renderer.render_asset(instrument, project, merged, preview=preview)
            except Exception as exc:
                warnings.append(f"event at step {step:g} failed to render: {exc}")
                continue
            parts.append((buf, int(round(start * sr))))

    if not parts:
        length = max(1, int(pattern.steps)) * step_len
        return AudioBuffer.silence(length, sr), warnings or ["pattern produced no events"]

    mixed = AudioBuffer.mix(parts, sr)

    # Pad out to the pattern's declared length so loops line up.
    declared = max(1, int(pattern.steps)) * step_len
    if mixed.duration < declared:
        pad = AudioBuffer.silence(declared - mixed.duration, sr, mixed.channels)
        mixed = AudioBuffer.concat([mixed, pad])

    if mixed.peak() > 1.0:
        warnings.append(f"pattern mix peaks at {mixed.peak():.2f}; normalized")
        mixed = mixed.normalized()
    return mixed, warnings
