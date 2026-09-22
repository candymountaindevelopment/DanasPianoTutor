"""Project format migrations.

A migration must be behaviour-preserving: a project loaded through it must
render identically to how it rendered before. Register new ones by appending
to MIGRATIONS; the chain runs in order.
"""

from __future__ import annotations

from typing import Callable

from .. import PROJECT_VERSION


def _v1_to_v2(data: dict) -> dict:
    """Introduce time structures, filter stacks, and instance overrides.

    brightness 0.5 is the identity point of the Brightness mapping and empty
    override dicts resolve to the base parameters, so v1 renders are unchanged.
    """
    for asset in (data.get("assets") or {}).values():
        atype = asset.get("type")
        if atype in ("SynthAsset", "InstrumentAsset"):
            params = asset.setdefault("params", {})
            params.setdefault("filters", [])
            params.setdefault("brightness", 0.5)
            params.setdefault("stretch_mode", "preserve_impact")
            params.setdefault("time_structure", None)  # derived lazily from the envelope
        elif atype == "AudioAsset":
            asset.setdefault("stretch_method", "tape")
            asset.setdefault("time_structure", None)
        elif atype == "PatternAsset":
            asset.setdefault("defaults", {})
            for track in asset.get("tracks", []):
                for event in track.get("events", []):
                    event.setdefault("overrides", {})

    timeline = data.setdefault("timeline", {})
    for clip in timeline.get("clips", []) or []:
        clip.setdefault("overrides", {})

    data["project_version"] = 2
    return data


# (from_version, migrate_fn)
MIGRATIONS: list[tuple[int, Callable[[dict], dict]]] = [
    (1, _v1_to_v2),
]


def migrate(data: dict) -> tuple[dict, list[str]]:
    """Bring a project dict up to the current version. Returns (data, notes)."""
    notes: list[str] = []
    version = int(data.get("project_version", 1))
    if version > PROJECT_VERSION:
        notes.append(
            f"project was saved by a newer build (v{version} > v{PROJECT_VERSION}); "
            "unknown data is preserved but may not be editable"
        )
        return data, notes
    for from_version, fn in MIGRATIONS:
        if version == from_version:
            data = fn(data)
            version = int(data.get("project_version", from_version + 1))
            notes.append(f"migrated project v{from_version} -> v{version}")
    data["project_version"] = max(version, PROJECT_VERSION)
    return data, notes
