"""Project validation (spec sections 57 and 103.4)."""

from __future__ import annotations

from dataclasses import dataclass

from .assets import AudioAsset, InstrumentAsset, PatternAsset, SynthAsset, UnknownAsset
from .ids import is_valid_identifier
from .overrides import PARAM_SPECS, unknown_params
from .project import Project

OK, WARN, ERROR = "ok", "warning", "error"


@dataclass
class Issue:
    level: str
    where: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - display helper
        mark = {OK: "✓", WARN: "⚠", ERROR: "✗"}.get(self.level, "·")
        return f"{mark} {self.where}: {self.message}"


def validate(project: Project) -> list[Issue]:
    issues: list[Issue] = []
    seen_names: dict[str, str] = {}

    for asset in project.assets:
        where = asset.name

        if not is_valid_identifier(asset.name):
            issues.append(Issue(ERROR, where, "name is not a safe identifier"))
        if asset.name in seen_names and seen_names[asset.name] != asset.uid:
            issues.append(Issue(ERROR, where, "duplicate asset name"))
        seen_names[asset.name] = asset.uid

        if isinstance(asset, UnknownAsset):
            issues.append(Issue(WARN, where, asset.summary()))
            continue

        if isinstance(asset, (SynthAsset, InstrumentAsset)):
            issues += _validate_synth(asset, project)
        elif isinstance(asset, AudioAsset):
            if asset.missing or (asset.source_path and not project.resolve_asset_path(asset.source_path).exists()):
                issues.append(Issue(WARN, where, f"source file missing: {asset.source_path}"))
        elif isinstance(asset, PatternAsset):
            issues += _validate_pattern(asset, project)

    for name, slot in sorted(project.slots.items()):
        if slot.bound and project.assets.get(slot.asset) is None:
            issues.append(
                Issue(ERROR, f"slot {name}", "bound to an asset that no longer exists")
            )

    if not len(project.assets):
        issues.append(Issue(WARN, "project", "no assets"))
    return issues


def _validate_synth(asset, project: Project) -> list[Issue]:
    out: list[Issue] = []
    where = asset.name
    p = asset.params
    nyq = 0.5 * project.settings.sample_rate

    if p.duration <= 0:
        out.append(Issue(ERROR, where, "duration must be positive"))
    if p.pitch.max_value >= nyq:
        out.append(Issue(WARN, where, f"pitch reaches {p.pitch.max_value:.0f} Hz, above Nyquist"))
    if p.pitch.min_value <= 0:
        out.append(Issue(ERROR, where, "pitch trajectory contains a non-positive frequency"))

    ts = p.time_structure
    if ts is not None:
        for problem in ts.issues():
            out.append(Issue(ERROR, where, problem))

    for node in p.filters:
        center = node.param("center") if node.type in ("emphasis", "bandpass", "notch") else None
        if isinstance(center, (int, float)) and center >= 0.45 * project.settings.sample_rate:
            out.append(Issue(WARN, where, f"filter {node.id!r} centre is above 0.45 x sample rate"))
        res = node.param("resonance")
        if isinstance(res, (int, float)) and res > 20.0:
            out.append(Issue(WARN, where, f"filter {node.id!r} resonance is above the stability clamp"))
    return out


def _validate_pattern(asset: PatternAsset, project: Project) -> list[Issue]:
    from .slots import unbound_slots

    out: list[Issue] = []
    where = asset.name
    if not asset.tracks:
        out.append(Issue(WARN, where, "pattern has no tracks"))
    unique_overrides = 0

    for name in sorted(set(unbound_slots(asset, project))):
        out.append(
            Issue(WARN, where, f"slot {name!r} is not bound — it will render as a placeholder click")
        )

    for track in asset.tracks:
        ref = track.get("asset")
        if ref and not (project.assets.get(ref) or project.assets.by_name(ref)):
            out.append(Issue(ERROR, where, f"track references unknown asset {ref!r}"))
        for event in track.get("events", []):
            ov = event.get("overrides") or {}
            if ov:
                unique_overrides += 1
            for key in unknown_params(ov):
                out.append(Issue(WARN, where, f"override {key!r} is not a known parameter"))
            for key, value in ov.items():
                spec = PARAM_SPECS.get(key)
                if spec is None or not isinstance(value, (int, float)):
                    continue
                if spec.lo is not None and value < spec.lo:
                    out.append(Issue(WARN, where, f"override {key}={value} is below the allowed range"))
                if spec.hi is not None and value > spec.hi:
                    out.append(Issue(WARN, where, f"override {key}={value} is above the allowed range"))

    if unique_overrides > 512:
        out.append(Issue(WARN, where, f"{unique_overrides} overridden events may render slowly"))
    return out


def summarize(issues: list[Issue]) -> str:
    errors = sum(1 for i in issues if i.level == ERROR)
    warns = sum(1 for i in issues if i.level == WARN)
    if not errors and not warns:
        return "No issues found."
    return f"{errors} error(s), {warns} warning(s)"
