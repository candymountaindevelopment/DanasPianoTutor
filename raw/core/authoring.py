"""The authoring format: a compact, LLM-writable JSON document that expands
into project assets.

This is the surface a chatbot writes to. It is deliberately different from the
project file: names instead of UUIDs, note names instead of hertz, shorthand
instead of full trajectories, and defaults for nearly everything. The importer
resolves names to UUIDs on the way in, so references stay hot-swap safe once
inside the project.

Parsing is strict about structure and forgiving about values: out-of-range
numbers are clamped and reported rather than rejected, because a document that
is 95% right should still import.

See docs/AUTHORING_FORMAT.md — that file is the specification and is meant to
be pasted into a chatbot verbatim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..patterns.notes import hz_to_midi, midi_to_note, note_to_hz, parse_pitch_token, token_kind
from ..synth import engine
from ..synth.drift import Drift
from ..synth.envelope import ADSR
from ..synth.filters import FILTER_PARAMS, FILTER_TYPES, FilterNode
from ..synth.oscillator import OSCILLATORS
from ..synth.timestructure import REGION_MODES, STRETCH_MODES, Region, TimeStructure
from ..synth.trajectory import CURVES, Trajectory
from .assets import InstrumentAsset, PatternAsset, SynthAsset
from .ids import slugify

FORMAT_NAME = "raw.author"
FORMAT_VERSION = 1

SOUND_KEYS = {
    "name", "from_preset", "oscillator", "duration", "pitch", "duty", "amplitude",
    "pan", "brightness", "envelope", "drift", "filters", "stretch", "regions",
    "seed", "root_note", "tags", "description",
}
PATTERN_KEYS = {
    "name", "tempo", "steps", "step", "swing", "tracks", "overrides", "slots",
    "tags", "description",
}
TRACK_KEYS = {"name", "instrument", "slot", "notes", "events", "mute", "overrides"}
EVENT_KEYS = {"step", "note", "length", "velocity", "overrides"}
TOP_KEYS = {"format", "version", "project", "slots", "sounds", "instruments", "patterns", "lessons"}


@dataclass
class AuthorResult:
    assets: list = field(default_factory=list)
    # Assets that already exist by name and carry the existing UUID, so
    # importing an edited document updates in place instead of duplicating.
    updated: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    # slot name -> {"asset": uid or None, "overrides": {...}}
    slots: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def all_assets(self) -> list:
        return list(self.assets) + list(self.updated)

    def report(self) -> str:
        lines = [f"{len(self.assets)} new, {len(self.updated)} updated"]
        lines += [f"error: {e}" for e in self.errors]
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)


class AuthorError(ValueError):
    pass


# --------------------------------------------------------------------- helpers


def _clamp(value, lo, hi, label, warnings, default=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        warnings.append(f"{label}: {value!r} is not a number; using {default}")
        return default
    if v < lo or v > hi:
        warnings.append(f"{label}: {v} is outside {lo}..{hi}; clamped")
        return max(lo, min(hi, v))
    return v


def _hz(value, label, warnings, default=440.0) -> float:
    """Accept a number (Hz), a note name, or 'hz:900'."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        text = str(value).strip()
        if text.lower().startswith("hz:"):
            return float(text[3:])
        return note_to_hz(text)
    except Exception:
        warnings.append(f"{label}: {value!r} is not a frequency or note name; using {default}")
        return default


def _parse_pitch(value, label, warnings, default: Trajectory) -> Trajectory:
    if value is None:
        return default
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        return Trajectory.constant(_hz(value, label, warnings))
    if isinstance(value, dict):
        if "points" in value:
            points = []
            for i, p in enumerate(value["points"]):
                at = _clamp(p.get("at", p.get("u", 0.0)), 0.0, 1.0, f"{label}.points[{i}].at",
                            warnings, 0.0)
                hz = _hz(p.get("hz", p.get("note", 440.0)), f"{label}.points[{i}]", warnings)
                points.append((at, hz))
            if not points:
                warnings.append(f"{label}: empty points list; using default")
                return default
            curve = value.get("curve", "exponential")
            if curve not in CURVES:
                warnings.append(f"{label}: unknown curve {curve!r}; using exponential")
                curve = "exponential"
            return Trajectory(points, curve)
        if "from" in value or "to" in value:
            start = _hz(value.get("from", 440.0), f"{label}.from", warnings)
            end = _hz(value.get("to", start), f"{label}.to", warnings)
            curve = value.get("curve", "exponential")
            if curve not in CURVES:
                warnings.append(f"{label}: unknown curve {curve!r}; using exponential")
                curve = "exponential"
            return Trajectory.ramp(start, end, curve)
        if "note" in value or "hz" in value:
            return Trajectory.constant(_hz(value.get("note", value.get("hz")), label, warnings))
    warnings.append(f"{label}: unrecognised pitch {value!r}; using default")
    return default


def _parse_envelope(value, label, warnings, default: ADSR) -> ADSR:
    if value is None:
        return default
    if isinstance(value, (list, tuple)) and len(value) == 4:
        value = dict(zip(("attack", "decay", "sustain", "release"), value))
    if not isinstance(value, dict):
        warnings.append(f"{label}: expected an object or [a,d,s,r]; ignored")
        return default
    return ADSR(
        _clamp(value.get("attack", default.attack), 0.0, 10.0, f"{label}.attack", warnings, default.attack),
        _clamp(value.get("decay", default.decay), 0.0, 10.0, f"{label}.decay", warnings, default.decay),
        _clamp(value.get("sustain", default.sustain), 0.0, 1.0, f"{label}.sustain", warnings, default.sustain),
        _clamp(value.get("release", default.release), 0.0, 10.0, f"{label}.release", warnings, default.release),
    )


def _parse_drift(value, label, warnings, default: Drift) -> Drift:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Drift(
            _clamp(value, 0.0, 1.0, label, warnings, default.authenticity),
            default.pitch_lfo_hz, default.pitch_depth, default.amp_depth,
            default.noise_floor, default.time_domain,
        )
    if not isinstance(value, dict):
        warnings.append(f"{label}: expected a number 0..1 or an object; ignored")
        return default
    domain = value.get("time_domain", default.time_domain)
    if domain not in ("absolute", "normalized"):
        warnings.append(f"{label}.time_domain: {domain!r} unknown; using absolute")
        domain = "absolute"
    return Drift(
        _clamp(value.get("amount", default.authenticity), 0.0, 1.0, f"{label}.amount", warnings, default.authenticity),
        _clamp(value.get("lfo_hz", default.pitch_lfo_hz), 0.05, 40.0, f"{label}.lfo_hz", warnings, default.pitch_lfo_hz),
        _clamp(value.get("pitch_depth", default.pitch_depth), 0.0, 12.0, f"{label}.pitch_depth", warnings, default.pitch_depth),
        _clamp(value.get("amp_depth", default.amp_depth), 0.0, 1.0, f"{label}.amp_depth", warnings, default.amp_depth),
        _clamp(value.get("noise_floor", default.noise_floor), 0.0, 0.5, f"{label}.noise_floor", warnings, default.noise_floor),
        domain,
    )


def _parse_filters(value, label, warnings) -> list[FilterNode]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{label}: expected a list of filters; ignored")
        return []
    nodes: list[FilterNode] = []
    for i, spec in enumerate(value):
        if not isinstance(spec, dict):
            warnings.append(f"{label}[{i}]: expected an object; skipped")
            continue
        kind = spec.get("type")
        if kind not in FILTER_TYPES:
            warnings.append(f"{label}[{i}]: unknown filter type {kind!r}; skipped "
                            f"(valid: {', '.join(FILTER_TYPES)})")
            continue
        params = {}
        allowed = FILTER_PARAMS.get(kind, {})
        for key, raw in spec.items():
            if key in ("type", "enabled", "id"):
                continue
            if key not in allowed:
                warnings.append(f"{label}[{i}]: {kind} has no parameter {key!r}; ignored "
                                f"(valid: {', '.join(allowed)})")
                continue
            if isinstance(raw, dict):
                params[key] = _parse_pitch(raw, f"{label}[{i}].{key}", warnings,
                                           Trajectory.constant(allowed[key]))
            else:
                params[key] = float(raw)
        nodes.append(FilterNode(kind, params, bool(spec.get("enabled", True)), spec.get("id", f"f{i + 1}")))
    return nodes


def _parse_regions(value, duration, label, warnings) -> TimeStructure | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        warnings.append(f"{label}: expected a non-empty list of regions; ignored")
        return None

    use_ends = any("end" in r for r in value if isinstance(r, dict))
    regions: list[Region] = []
    if use_ends:
        cursor = 0.0
        for i, spec in enumerate(value):
            end = _clamp(spec.get("end", 1.0), 0.0, 1.0, f"{label}[{i}].end", warnings, 1.0)
            regions.append(_region(spec, i, cursor, max(end, cursor + 1e-4), warnings, label))
            cursor = regions[-1].end
    else:
        lengths = [max(1e-6, float(r.get("length", 1.0))) for r in value]
        total = sum(lengths)
        cursor = 0.0
        for i, spec in enumerate(value):
            end = cursor + lengths[i] / total
            regions.append(_region(spec, i, cursor, end, warnings, label))
            cursor = end
    regions[0].start = 0.0
    regions[-1].end = 1.0
    ts = TimeStructure(duration, regions)
    ts.normalize()
    return ts


def _region(spec: dict, i: int, start: float, end: float, warnings, label) -> Region:
    role = spec.get("role", "body")
    if role not in ("attack", "transient", "body", "tail", "custom"):
        warnings.append(f"{label}[{i}].role: {role!r} unknown; using custom")
        role = "custom"
    mode = spec.get("mode", "stretch")
    if mode not in REGION_MODES:
        warnings.append(f"{label}[{i}].mode: {mode!r} unknown; using stretch")
        mode = "stretch"
    return Region(
        id=spec.get("id", f"r_{role}_{i}"),
        role=role,
        start=start,
        end=end,
        elasticity=_clamp(spec.get("elasticity", 1.0), 0.0, 4.0, f"{label}[{i}].elasticity", warnings, 1.0),
        mode=mode,
    )


def _unknown_keys(spec: dict, allowed: set, label: str, warnings: list[str]) -> None:
    extra = [k for k in spec if k not in allowed]
    if extra:
        warnings.append(f"{label}: ignored unknown key(s) {', '.join(sorted(extra))}")


# ----------------------------------------------------------------------- sound


def parse_sound(spec: dict, label: str, warnings: list[str]) -> engine.SynthParams:
    _unknown_keys(spec, SOUND_KEYS, label, warnings)

    preset = spec.get("from_preset")
    if preset is not None and preset not in engine.PRESET_NAMES:
        warnings.append(f"{label}.from_preset: {preset!r} unknown "
                        f"(valid: {', '.join(engine.PRESET_NAMES)}); starting from defaults")
        preset = None
    p = engine.preset(preset) if preset else engine.SynthParams()

    osc = spec.get("oscillator")
    if osc is not None:
        if osc in OSCILLATORS:
            p.oscillator = osc
        else:
            warnings.append(f"{label}.oscillator: {osc!r} unknown "
                            f"(valid: {', '.join(sorted(OSCILLATORS))}); kept {p.oscillator}")

    if "duration" in spec:
        p.duration = _clamp(spec["duration"], 0.005, 60.0, f"{label}.duration", warnings, p.duration)
    p.pitch = _parse_pitch(spec.get("pitch"), f"{label}.pitch", warnings, p.pitch)
    if "duty" in spec:
        p.duty = Trajectory.constant(
            _clamp(spec["duty"], 0.01, 0.99, f"{label}.duty", warnings, 0.5)
        )
    if "amplitude" in spec:
        p.amplitude = _clamp(spec["amplitude"], 0.0, 1.5, f"{label}.amplitude", warnings, p.amplitude)
    if "pan" in spec:
        p.pan = _clamp(spec["pan"], -1.0, 1.0, f"{label}.pan", warnings, p.pan)
    if "brightness" in spec:
        p.brightness = _clamp(spec["brightness"], 0.0, 1.0, f"{label}.brightness", warnings, p.brightness)
    p.envelope = _parse_envelope(spec.get("envelope"), f"{label}.envelope", warnings, p.envelope)
    p.drift = _parse_drift(spec.get("drift"), f"{label}.drift", warnings, p.drift)
    if "filters" in spec:
        p.filters = _parse_filters(spec["filters"], f"{label}.filters", warnings)
    if "seed" in spec:
        p.seed = int(_clamp(spec["seed"], 0, 2**31 - 1, f"{label}.seed", warnings, 0))

    stretch = spec.get("stretch")
    if stretch is not None:
        if stretch in STRETCH_MODES:
            p.stretch_mode = stretch
        else:
            warnings.append(f"{label}.stretch: {stretch!r} unknown "
                            f"(valid: {', '.join(STRETCH_MODES)}); kept {p.stretch_mode}")

    ts = _parse_regions(spec.get("regions"), p.duration, f"{label}.regions", warnings)
    if ts is not None:
        p.time_structure = ts
        p.stretch_mode = "custom"

    root = spec.get("root_note")
    if root is not None:
        try:
            parse_pitch_token(root)
            p.root_note = str(root)
        except Exception:
            warnings.append(f"{label}.root_note: {root!r} is not a note name; ignored")
    return p


# --------------------------------------------------------------------- patterns


def expand_notes(text: str, label: str, warnings: list[str]) -> list[dict]:
    """Expand a compact tracker string into explicit events.

    'C4 . E4 - . x X'  ->  notes, rests, a hold extending the previous note,
    a trigger at the instrument's own pitch, and an accented trigger.
    """
    events: list[dict] = []
    step = 0
    last: dict | None = None
    for raw in str(text).replace("|", " ").split():
        token = raw.strip()
        if not token:
            continue
        length = None
        if ":" in token:
            token, _, length_text = token.partition(":")
            length = length_text
        kind = token_kind(token)

        if kind == "rest":
            last = None
        elif kind == "hold":
            if last is None:
                warnings.append(f"{label}: hold '-' at step {step} has nothing to extend; treated as a rest")
            else:
                last["length"] = float(last.get("length", 1)) + 1 if not isinstance(
                    last.get("length"), str) else last["length"]
        elif kind in ("trigger", "accent"):
            last = {"step": step}
            if kind == "accent":
                last["velocity"] = 1.0
            events.append(last)
        else:
            try:
                parse_pitch_token(token)
            except Exception:
                warnings.append(f"{label}: {token!r} at step {step} is not a note; skipped")
                last = None
                step += 1
                continue
            last = {"step": step, "note": token}
            events.append(last)

        if length is not None and last is not None:
            try:
                last["length"] = length if "/" in length else float(length)
            except ValueError:
                warnings.append(f"{label}: length {length!r} at step {step} is not valid; ignored")
        step += 1
    return events


def parse_pattern(spec: dict, label: str, warnings: list[str], resolve_name) -> PatternAsset:
    _unknown_keys(spec, PATTERN_KEYS, label, warnings)
    pattern = PatternAsset(
        name=slugify(spec.get("name", "pattern")),
        tempo=_clamp(spec.get("tempo", 120.0), 20.0, 400.0, f"{label}.tempo", warnings, 120.0),
        steps=int(_clamp(spec.get("steps", 16), 1, 4096, f"{label}.steps", warnings, 16)),
        step_division=str(spec.get("step", "1/16")),
        swing=_clamp(spec.get("swing", 0.0), 0.0, 1.0, f"{label}.swing", warnings, 0.0),
        tags=list(spec.get("tags", [])),
        description=spec.get("description", ""),
    )
    if spec.get("overrides"):
        pattern.defaults["overrides"] = dict(spec["overrides"])
    if spec.get("slots"):
        local = {}
        for name, value in dict(spec["slots"]).items():
            uid = resolve_name(value) if isinstance(value, str) else None
            if isinstance(value, str) and uid is None:
                warnings.append(f"{label}.slots[{name}]: {value!r} is not a known sound; left unbound")
            local[str(name)] = {"asset": uid}
        pattern.defaults["slots"] = local

    tracks_spec = spec.get("tracks")
    if not isinstance(tracks_spec, list) or not tracks_spec:
        warnings.append(f"{label}: pattern has no tracks")
        return pattern

    for ti, track_spec in enumerate(tracks_spec):
        tlabel = f"{label}.tracks[{ti}]"
        if not isinstance(track_spec, dict):
            warnings.append(f"{tlabel}: expected an object; skipped")
            continue
        _unknown_keys(track_spec, TRACK_KEYS, tlabel, warnings)

        slot_name = track_spec.get("slot")
        ref = track_spec.get("instrument", "")
        uid = None
        if slot_name:
            if ref:
                warnings.append(f"{tlabel}: has both 'slot' and 'instrument'; the slot wins")
        else:
            uid = resolve_name(ref)
            if uid is None:
                warnings.append(f"{tlabel}: instrument {ref!r} is not defined in this document "
                                "or the project; the track is kept but will not sound")
                uid = ref

        events: list[dict] = []
        if "notes" in track_spec:
            events = expand_notes(track_spec["notes"], f"{tlabel}.notes", warnings)
        for ei, ev in enumerate(track_spec.get("events", []) or []):
            if not isinstance(ev, dict):
                warnings.append(f"{tlabel}.events[{ei}]: expected an object; skipped")
                continue
            _unknown_keys(ev, EVENT_KEYS, f"{tlabel}.events[{ei}]", warnings)
            clean = {"step": _clamp(ev.get("step", 0), 0, 4096, f"{tlabel}.events[{ei}].step", warnings, 0)}
            for key in ("note", "length", "velocity", "overrides"):
                if key in ev:
                    clean[key] = ev[key]
            events.append(clean)

        overflow = [e for e in events if float(e["step"]) >= pattern.steps]
        if overflow:
            warnings.append(f"{tlabel}: {len(overflow)} event(s) fall past step {pattern.steps - 1} "
                            "and will not be heard in a loop")

        track = {
            "name": track_spec.get("name", f"track_{ti + 1}"),
            "events": events,
            "mute": bool(track_spec.get("mute", False)),
            "overrides": dict(track_spec.get("overrides", {})),
        }
        if slot_name:
            track["slot"] = str(slot_name)
        else:
            track["instrument"] = uid
        pattern.tracks.append(track)
    return pattern


# --------------------------------------------------------------------- document


def parse_document(doc, project=None, merge: bool = False) -> AuthorResult:
    """Turn an authoring document into assets. Does not modify the project.

    With `merge`, an entry whose name already exists in the project — and is
    the same kind of asset — reuses that asset's UUID and lands in
    `result.updated`. That makes re-importing an edited document update it
    rather than pile up `name_1`, `name_2` copies, and keeps every timeline
    clip, slot binding and pattern reference pointing at the right thing.
    """
    result = AuthorResult()
    if isinstance(doc, (str, bytes)):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError as exc:
            result.errors.append(f"not valid JSON: {exc}")
            return result
    if not isinstance(doc, dict):
        result.errors.append("the document must be a JSON object")
        return result

    if doc.get("format") != FORMAT_NAME:
        result.warnings.append(
            f'missing or wrong "format" field (expected "{FORMAT_NAME}"); parsing anyway'
        )
    version = doc.get("version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        result.warnings.append(f"document version {version} != {FORMAT_VERSION}; parsing anyway")
    _unknown_keys(doc, TOP_KEYS, "document", result.warnings)

    settings = doc.get("project") or {}
    if isinstance(settings, dict):
        result.settings = {
            k: settings[k] for k in ("name", "tempo", "sample_rate", "fps") if k in settings
        }

    created: dict[str, object] = {}

    def resolve_name(ref: str):
        if not ref:
            return None
        if ref in created:
            return created[ref].uid
        if project is not None:
            existing = project.assets.by_name(ref) or project.assets.get(ref)
            if existing is not None:
                return existing.uid
        return None

    taken = set(project.assets.names) if project is not None else set()

    def claim(name: str, label: str, cls) -> tuple[str, str | None]:
        """Return (name, uid-to-replace-or-None)."""
        clean = slugify(name)
        existing = project.assets.by_name(clean) if project is not None else None
        if existing is not None and clean not in created:
            if merge and type(existing) is cls:
                return clean, existing.uid
            if merge:
                result.warnings.append(
                    f"{label}: {clean!r} already exists as a {existing.type_name} but this "
                    f"document defines a {cls.__name__}; imported as a copy"
                )
            else:
                result.warnings.append(
                    f"{label}: name {clean!r} already exists; it will be suffixed on import"
                )
        elif clean in created:
            result.warnings.append(f"{label}: {clean!r} is defined twice in this document")
        taken.add(clean)
        return clean, None

    def record(asset, replaces: str | None) -> None:
        if replaces:
            asset.uid = replaces
            result.updated.append(asset)
        else:
            result.assets.append(asset)

    for kind, cls in (("sounds", SynthAsset), ("instruments", InstrumentAsset)):
        entries = doc.get(kind) or []
        if not isinstance(entries, list):
            result.errors.append(f'"{kind}" must be a list')
            continue
        for i, spec in enumerate(entries):
            label = f"{kind}[{i}]"
            if not isinstance(spec, dict):
                result.errors.append(f"{label}: expected an object")
                continue
            if not spec.get("name"):
                result.errors.append(f"{label}: missing required field 'name'")
                continue
            label = f"{kind}[{spec['name']}]"
            params = parse_sound(spec, label, result.warnings)
            name, replaces = claim(spec["name"], label, cls)
            asset = cls(
                name=name,
                params=params,
                tags=list(spec.get("tags", [])),
                description=spec.get("description", ""),
            )
            created[spec["name"]] = asset
            record(asset, replaces)

    slots_spec = doc.get("slots")
    if slots_spec is not None:
        if isinstance(slots_spec, list):
            slots_spec = {
                s.get("name"): s.get("sound", s.get("asset"))
                for s in slots_spec
                if isinstance(s, dict) and s.get("name")
            }
        if not isinstance(slots_spec, dict):
            result.errors.append('"slots" must be an object mapping names to sounds, or a list')
            slots_spec = {}
        for name, value in slots_spec.items():
            entry: dict = {"asset": None, "overrides": {}}
            target = value
            if isinstance(value, dict):
                target = value.get("sound", value.get("asset"))
                entry["overrides"] = dict(value.get("overrides", {}))
            if isinstance(target, str) and target:
                uid = resolve_name(target)
                if uid is None:
                    result.warnings.append(
                        f"slots[{name}]: {target!r} is not defined in this document or the "
                        "project; the slot is created but left unbound"
                    )
                entry["asset"] = uid
            result.slots[str(name)] = entry

    for i, spec in enumerate(doc.get("patterns") or []):
        label = f"patterns[{i}]"
        if not isinstance(spec, dict):
            result.errors.append(f"{label}: expected an object")
            continue
        if not spec.get("name"):
            result.errors.append(f"{label}: missing required field 'name'")
            continue
        label = f"patterns[{spec['name']}]"
        pattern = parse_pattern(spec, label, result.warnings, resolve_name)
        pattern.name, replaces = claim(pattern.name, label, PatternAsset)
        created[spec["name"]] = pattern
        record(pattern, replaces)

    if not result.all_assets and not result.slots and not result.errors:
        result.errors.append("the document defined no sounds, instruments, patterns, or slots")
    return result


# ----------------------------------------------------------------------- export


def _pitch_to_author(traj: Trajectory):
    if traj.is_constant:
        return round(traj.value_at(0.0), 3)
    if len(traj.points) == 2:
        return {
            "from": round(traj.points[0][1], 3),
            "to": round(traj.points[1][1], 3),
            "curve": traj.curve,
        }
    return {
        "points": [{"at": round(u, 4), "hz": round(v, 3)} for u, v in traj.points],
        "curve": traj.curve,
    }


def sound_to_author(asset) -> dict:
    p = asset.params
    out = {
        "name": asset.name,
        "oscillator": p.oscillator,
        "duration": round(p.duration, 4),
        "pitch": _pitch_to_author(p.pitch),
        "envelope": {k: round(v, 4) for k, v in p.envelope.to_dict().items()},
        "brightness": round(p.brightness, 3),
        "drift": round(p.drift.authenticity, 3),
        "stretch": p.stretch_mode,
        "seed": int(p.seed),
    }
    if p.oscillator in ("square", "pulse_pair"):
        out["duty"] = round(p.duty.value_at(0.0), 3)
    if abs(p.amplitude - 0.8) > 1e-9:
        out["amplitude"] = round(p.amplitude, 3)
    if abs(p.pan) > 1e-9:
        out["pan"] = round(p.pan, 3)
    if p.filters:
        out["filters"] = [
            {"type": n.type, **{k: (round(float(v), 3) if isinstance(v, (int, float)) else _pitch_to_author(v))
                                for k, v in n.params.items()}}
            for n in p.filters
        ]
    if p.root_note:
        out["root_note"] = p.root_note
    else:
        out["root_note"] = midi_to_note(hz_to_midi(p.pitch.value_at(0.0)))
    if asset.tags:
        out["tags"] = list(asset.tags)
    return out


def pattern_to_author(pattern, project) -> dict:
    tracks = []
    for track in pattern.tracks:
        entry = {"name": track.get("name", "track")}
        if track.get("slot"):
            entry["slot"] = track["slot"]
        else:
            inst = project.assets.get(track.get("instrument", "")) if project else None
            entry["instrument"] = inst.name if inst else track.get("instrument", "")
        entry["events"] = track.get("events", [])
        if track.get("mute"):
            entry["mute"] = True
        if track.get("overrides"):
            entry["overrides"] = track["overrides"]
        tracks.append(entry)
    return {
        "name": pattern.name,
        "tempo": pattern.tempo,
        "steps": pattern.steps,
        "step": pattern.step_division,
        **({"swing": pattern.swing} if pattern.swing else {}),
        "tracks": tracks,
    }


def document_from_project(project) -> dict:
    """Export a project as an authoring document — useful as a worked example
    to show a chatbot alongside the specification."""
    doc = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "project": {
            "name": project.metadata.get("name", "Untitled"),
            "tempo": project.settings.tempo,
            "sample_rate": project.settings.sample_rate,
        },
        "slots": {},
        "sounds": [],
        "instruments": [],
        "patterns": [],
    }
    for name, slot in sorted(project.slots.items()):
        bound = project.assets.get(slot.asset) if slot.asset else None
        if slot.overrides:
            doc["slots"][name] = {
                "sound": bound.name if bound else None,
                "overrides": dict(slot.overrides),
            }
        else:
            doc["slots"][name] = bound.name if bound else None
    for asset in project.assets.sorted():
        if isinstance(asset, SynthAsset):
            doc["sounds"].append(sound_to_author(asset))
        elif isinstance(asset, InstrumentAsset):
            doc["instruments"].append(sound_to_author(asset))
        elif isinstance(asset, PatternAsset):
            doc["patterns"].append(pattern_to_author(asset, project))
    return {k: v for k, v in doc.items() if v not in ([], {})}
