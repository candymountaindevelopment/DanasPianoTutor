"""Instance-level parameter overrides (spec sections 101-102).

An override is a sparse dict. Only the keys present are overridden; everything
else inherits. Values are either a bare scalar (using the parameter's default
mode) or an explicit {"mode": "set"|"add"|"mul", "value": x}.

Resolution runs lowest precedence first:
    chip profile -> instrument -> asset -> pattern defaults -> event -> clip
"""

from __future__ import annotations

from dataclasses import dataclass

MODES = ("set", "add", "mul")

# Layer names in precedence order, lowest first. Used for explain() output.
LAYERS = ("chip", "instrument", "asset", "pattern", "event", "clip")


@dataclass(frozen=True)
class ParamSpec:
    name: str
    default_mode: str
    lo: float | None = None
    hi: float | None = None
    kind: str = "number"
    label: str = ""
    unit: str = ""


PARAM_SPECS: dict[str, ParamSpec] = {
    s.name: s
    for s in (
        ParamSpec("duration", "set", 0.001, 600.0, "duration", "Duration", "s"),
        ParamSpec("stretch_mode", "set", None, None, "enum", "Stretch Mode"),
        ParamSpec("pitch_offset", "add", -48.0, 48.0, "number", "Pitch Offset", "st"),
        ParamSpec("volume_db", "add", -60.0, 12.0, "number", "Volume", "dB"),
        ParamSpec("pan", "add", -1.0, 1.0, "number", "Pan"),
        ParamSpec("brightness", "set", 0.0, 1.0, "number", "Brightness"),
        ParamSpec("drift", "mul", 0.0, 4.0, "number", "Drift"),
        ParamSpec("spectral_center", "mul", 0.05, 20.0, "number", "Spectral Center", "x"),
        ParamSpec("spectral_width", "set", 0.05, 6.0, "number", "Spectral Width", "oct"),
        ParamSpec("spectral_amount", "add", -24.0, 24.0, "number", "Spectral Amount", "dB"),
        ParamSpec("duty", "set", 0.01, 0.99, "number", "Duty Cycle"),
        ParamSpec("seed_offset", "add", -(2**31), 2**31, "int", "Seed Offset"),
    )
}


def normalize_entry(param: str, value) -> tuple[str, object]:
    """Return (mode, value) for an override entry."""
    spec = PARAM_SPECS.get(param)
    default_mode = spec.default_mode if spec else "set"
    if isinstance(value, dict) and "value" in value and "mode" in value:
        mode = value["mode"]
        if mode not in MODES:
            mode = default_mode
        return mode, value["value"]
    return default_mode, value


def _clamp(param: str, value):
    spec = PARAM_SPECS.get(param)
    if spec is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    if spec.lo is not None:
        value = max(spec.lo, value)
    if spec.hi is not None:
        value = min(spec.hi, value)
    if spec.kind == "int":
        value = int(round(value))
    return value


def resolve(base: dict, layers: list[tuple[str, dict]]) -> dict:
    """Resolve the override chain into final parameter values.

    `base` supplies the inherited starting value per parameter.
    `layers` is [(layer_name, override_dict)], lowest precedence first.
    """
    values = dict(base)
    for _, ov in layers:
        if not ov:
            continue
        for param, raw in ov.items():
            mode, v = normalize_entry(param, raw)
            cur = values.get(param)
            if mode == "set" or cur is None or not isinstance(v, (int, float)) or isinstance(v, bool):
                values[param] = v
            elif mode == "add":
                values[param] = cur + v
            elif mode == "mul":
                values[param] = cur * v
    return {k: _clamp(k, v) for k, v in values.items()}


def explain(base: dict, layers: list[tuple[str, dict]], param: str) -> list[tuple[str, str, object, object]]:
    """Trace one parameter through the chain: [(layer, mode, value, running)]."""
    running = base.get(param)
    trace: list[tuple[str, str, object, object]] = [("base", "set", running, running)]
    for name, ov in layers:
        if not ov or param not in ov:
            continue
        mode, v = normalize_entry(param, ov[param])
        if mode == "set" or running is None or not isinstance(v, (int, float)):
            running = v
        elif mode == "add":
            running = running + v
        elif mode == "mul":
            running = running * v
        trace.append((name, mode, v, running))
    if trace:
        last = trace[-1]
        trace[-1] = (last[0], last[1], last[2], _clamp(param, last[3]))
    return trace


# Overrides that can be applied to an already-rendered buffer without a
# re-render (spec section 102.2). Everything else is structural.
POST_PROCESS_PARAMS = frozenset({"volume_db", "pan"})


def is_post_process_only(ov: dict) -> bool:
    return bool(ov) and all(k in POST_PROCESS_PARAMS for k in ov)


def structural_subset(ov: dict) -> dict:
    return {k: v for k, v in (ov or {}).items() if k not in POST_PROCESS_PARAMS}


def unknown_params(ov: dict) -> list[str]:
    return [k for k in (ov or {}) if k not in PARAM_SPECS]


# ------------------------------------------------------------------ duration


def musical_to_seconds(spec: str, tempo: float) -> float:
    """'1/8', '1/8.', '1/8t', '1', '2' -> seconds at the given tempo."""
    s = str(spec).strip().lower()
    dotted = s.endswith(".")
    triplet = s.endswith("t")
    s = s.rstrip(".t")
    try:
        if "/" in s:
            num, den = s.split("/")
            frac = float(num) / float(den)
        else:
            frac = float(s)
    except (ValueError, ZeroDivisionError):
        frac = 0.25
    whole = 4.0 * 60.0 / max(tempo, 1e-6)
    seconds = frac * whole
    if dotted:
        seconds *= 1.5
    if triplet:
        seconds *= 2.0 / 3.0
    return seconds


def resolve_duration(value, nominal: float, tempo: float = 120.0) -> float:
    """Interpret a duration override in absolute, ratio, or musical form."""
    if value is None:
        return nominal
    if isinstance(value, dict):
        if "ratio" in value:
            return nominal * float(value["ratio"])
        if "musical" in value:
            return musical_to_seconds(value["musical"], tempo)
        if "absolute" in value:
            return float(value["absolute"])
        mode = value.get("mode")
        v = value.get("value", nominal)
        if mode == "ratio":
            return nominal * float(v)
        if mode == "musical":
            return musical_to_seconds(v, tempo)
        return float(v)
    if isinstance(value, str):
        return musical_to_seconds(value, tempo)
    return float(value)
