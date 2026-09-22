"""Asset types and the asset registry.

New asset types register themselves with @asset_type and are picked up by
serialisation, the asset manager tree, and validation automatically.

Unknown types encountered on load are preserved verbatim rather than dropped,
so a project saved by a newer build survives a round trip through an older one.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from ..synth.engine import SynthParams
from ..synth.filters import FilterNode
from ..synth.timestructure import TimeStructure
from .ids import new_uid, slugify, unique_name

ASSET_TYPES: dict[str, type] = {}


def asset_type(name: str):
    def deco(cls):
        cls.TYPE_NAME = name
        ASSET_TYPES[name] = cls
        return cls

    return deco


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


@dataclass
class Asset:
    TYPE_NAME = "Asset"

    uid: str = field(default_factory=new_uid)
    name: str = "asset"
    tags: list[str] = field(default_factory=list)
    description: str = ""
    created: str = field(default_factory=_now)
    modified: str = field(default_factory=_now)
    meta: dict = field(default_factory=dict)

    @property
    def type_name(self) -> str:
        return type(self).TYPE_NAME

    def touch(self) -> None:
        self.modified = _now()

    def base_dict(self) -> dict:
        return {
            "uid": self.uid,
            "type": self.type_name,
            "name": self.name,
            "tags": list(self.tags),
            "description": self.description,
            "created": self.created,
            "modified": self.modified,
            "meta": dict(self.meta),
        }

    def to_dict(self) -> dict:
        return self.base_dict()

    @staticmethod
    def _base_kwargs(d: dict) -> dict:
        return {
            "uid": d.get("uid") or new_uid(),
            "name": d.get("name", "asset"),
            "tags": list(d.get("tags", [])),
            "description": d.get("description", ""),
            "created": d.get("created", _now()),
            "modified": d.get("modified", _now()),
            "meta": dict(d.get("meta", {})),
        }

    def summary(self) -> str:
        return self.type_name

    def dependencies(self, project) -> list[str]:
        """UIDs of other assets this one needs in order to render."""
        return []

    def render_key(self) -> dict:
        """The part of the asset that affects audio, for cache hashing.

        Excludes name, tags, and timestamps so that renaming or re-tagging an
        asset does not throw away its cached render.
        """
        d = self.to_dict()
        for k in ("name", "tags", "description", "created", "modified", "meta"):
            d.pop(k, None)
        return d


@asset_type("SynthAsset")
@dataclass
class SynthAsset(Asset):
    """A mathematical sound definition. The recipe, never the waveform."""

    params: SynthParams = field(default_factory=SynthParams)

    def to_dict(self) -> dict:
        d = self.base_dict()
        d["params"] = self.params.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict) -> "SynthAsset":
        return SynthAsset(params=SynthParams.from_dict(d.get("params", {})), **Asset._base_kwargs(d))

    def summary(self) -> str:
        return f"{self.params.oscillator} · {self.params.duration:.3f}s"


@asset_type("AudioAsset")
@dataclass
class AudioAsset(Asset):
    """An imported waveform. Stretching this is approximate (spec section 99)."""

    source_path: str = ""
    sample_rate: int = 44100
    channels: int = 1
    duration: float = 0.0
    stretch_method: str = "tape"
    time_structure: TimeStructure | None = None
    missing: bool = False
    # A slice of the source file, in source-file seconds. Slicing is
    # non-destructive: many assets can point at one file with different
    # bounds, and the bounds stay editable.
    trim_start: float = 0.0
    trim_length: float | None = None
    # Non-destructive processing, applied at render time in a fixed order:
    #   trim -> reverse -> fades -> gain -> filters -> normalize
    # Stored as data, so it is undoable, saved with the project, and the
    # source file is never touched.
    reverse: bool = False
    fade_in: float = 0.0
    fade_out: float = 0.0
    gain_db: float = 0.0
    normalize: float | None = None  # target peak, or None for off
    filters: list = field(default_factory=list)
    # Stretch the sample to exactly this many bars at the project tempo.
    # Because it is resolved at render time, changing the tempo re-fits the
    # loop instead of leaving it stale. An explicit duration override wins.
    fit_bars: float | None = None

    @property
    def is_slice(self) -> bool:
        return self.trim_start > 0.0 or self.trim_length is not None

    @property
    def has_effects(self) -> bool:
        return bool(
            self.reverse
            or self.fade_in
            or self.fade_out
            or abs(self.gain_db) > 1e-9
            or self.normalize is not None
            or self.filters
        )

    def to_dict(self) -> dict:
        d = self.base_dict()
        d.update(
            {
                "source_path": self.source_path,
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "duration": self.duration,
                "stretch_method": self.stretch_method,
                "time_structure": self.time_structure.to_dict() if self.time_structure else None,
                "trim_start": self.trim_start,
                "trim_length": self.trim_length,
                "reverse": self.reverse,
                "fade_in": self.fade_in,
                "fade_out": self.fade_out,
                "gain_db": self.gain_db,
                "normalize": self.normalize,
                "filters": [f.to_dict() for f in self.filters],
                "fit_bars": self.fit_bars,
            }
        )
        return d

    @staticmethod
    def from_dict(d: dict) -> "AudioAsset":
        ts = d.get("time_structure")
        return AudioAsset(
            source_path=d.get("source_path", ""),
            sample_rate=int(d.get("sample_rate", 44100)),
            channels=int(d.get("channels", 1)),
            duration=float(d.get("duration", 0.0)),
            stretch_method=d.get("stretch_method", "tape"),
            time_structure=TimeStructure.from_dict(ts) if ts else None,
            trim_start=float(d.get("trim_start", 0.0)),
            trim_length=(None if d.get("trim_length") is None else float(d["trim_length"])),
            reverse=bool(d.get("reverse", False)),
            fade_in=float(d.get("fade_in", 0.0)),
            fade_out=float(d.get("fade_out", 0.0)),
            gain_db=float(d.get("gain_db", 0.0)),
            normalize=(None if d.get("normalize") is None else float(d["normalize"])),
            filters=[FilterNode.from_dict(f) for f in d.get("filters", [])],
            fit_bars=(None if d.get("fit_bars") is None else float(d["fit_bars"])),
            **Asset._base_kwargs(d),
        )

    def summary(self) -> str:
        if self.missing:
            return "sample · MISSING"
        kind = "slice" if self.is_slice else "sample"
        fx = " · fx" if self.has_effects else ""
        return f"{kind} · {self.duration:.3f}s{fx}"


@asset_type("InstrumentAsset")
@dataclass
class InstrumentAsset(Asset):
    """A reusable synthesis configuration referenced by pattern events."""

    params: SynthParams = field(default_factory=SynthParams)

    def to_dict(self) -> dict:
        d = self.base_dict()
        d["params"] = self.params.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict) -> "InstrumentAsset":
        return InstrumentAsset(
            params=SynthParams.from_dict(d.get("params", {})), **Asset._base_kwargs(d)
        )

    def summary(self) -> str:
        return f"instrument · {self.params.oscillator}"


@asset_type("PatternAsset")
@dataclass
class PatternAsset(Asset):
    """Structured musical events. Rendered through instruments, never baked in."""

    tempo: float = 120.0
    steps: int = 16
    step_division: str = "1/16"
    swing: float = 0.0
    tracks: list[dict] = field(default_factory=list)
    defaults: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.base_dict()
        d.update(
            {
                "tempo": self.tempo,
                "steps": self.steps,
                "step_division": self.step_division,
                "swing": self.swing,
                "tracks": self.tracks,
                "defaults": self.defaults,
            }
        )
        return d

    @staticmethod
    def from_dict(d: dict) -> "PatternAsset":
        return PatternAsset(
            tempo=float(d.get("tempo", 120.0)),
            steps=int(d.get("steps", 16)),
            step_division=d.get("step_division", "1/16"),
            swing=float(d.get("swing", 0.0)),
            tracks=list(d.get("tracks", [])),
            defaults=dict(d.get("defaults", {})),
            **Asset._base_kwargs(d),
        )

    def summary(self) -> str:
        events = sum(len(t.get("events", [])) for t in self.tracks)
        slots = sum(1 for t in self.tracks if t.get("slot"))
        extra = f" · {slots} slot(s)" if slots else ""
        return f"pattern · {len(self.tracks)} tracks · {self.steps} steps · {events} notes{extra}"

    def dependencies(self, project) -> list[str]:
        from .slots import resolve_track_source

        out = []
        for track in self.tracks:
            source = resolve_track_source(track, self, project)
            if source.asset is not None:
                out.append(source.asset.uid)
        return out


@dataclass
class UnknownAsset(Asset):
    """Preserves an asset type this build does not understand."""

    raw: dict = field(default_factory=dict)
    declared_type: str = "Unknown"

    @property
    def type_name(self) -> str:
        return self.declared_type

    def to_dict(self) -> dict:
        return dict(self.raw)

    def summary(self) -> str:
        return f"unrecognised type {self.declared_type!r} (preserved)"


def asset_from_dict(d: dict) -> Asset:
    tname = d.get("type", "")
    cls = ASSET_TYPES.get(tname)
    if cls is None:
        return UnknownAsset(raw=dict(d), declared_type=tname or "Unknown", **Asset._base_kwargs(d))
    return cls.from_dict(d)


# ----------------------------------------------------------------- registry


class AssetRegistry:
    """UUID-keyed asset store with a unique variable-name index."""

    def __init__(self) -> None:
        self._by_uid: dict[str, Asset] = {}

    def __len__(self) -> int:
        return len(self._by_uid)

    def __iter__(self):
        return iter(self._by_uid.values())

    def __contains__(self, uid: str) -> bool:
        return uid in self._by_uid

    @property
    def names(self) -> set[str]:
        return {a.name for a in self._by_uid.values()}

    def add(self, asset: Asset, rename_on_clash: bool = True) -> Asset:
        if rename_on_clash:
            taken = self.names - {asset.name} if asset.uid in self._by_uid else self.names
            asset.name = unique_name(asset.name, taken)
        self._by_uid[asset.uid] = asset
        return asset

    def remove(self, uid: str) -> Asset | None:
        return self._by_uid.pop(uid, None)

    def get(self, uid: str) -> Asset | None:
        return self._by_uid.get(uid)

    def by_name(self, name: str) -> Asset | None:
        return next((a for a in self._by_uid.values() if a.name == name), None)

    def by_type(self, type_name: str) -> list[Asset]:
        return [a for a in self._by_uid.values() if a.type_name == type_name]

    def replace(self, uid: str, asset: Asset) -> Asset:
        """Swap an asset's content while keeping its UUID.

        Everything that points at an asset — timeline clips, pattern tracks,
        slot bindings — points at the UUID, so replacing in place is what makes
        re-importing an edited document non-destructive.
        """
        if uid not in self._by_uid:
            raise KeyError(uid)
        asset.uid = uid
        self._by_uid[uid] = asset
        return asset

    def rename(self, uid: str, new_name: str) -> str:
        a = self._by_uid.get(uid)
        if a is None:
            raise KeyError(uid)
        a.name = unique_name(slugify(new_name), self.names - {a.name})
        a.touch()
        return a.name

    def sorted(self) -> list[Asset]:
        return sorted(self._by_uid.values(), key=lambda a: (a.type_name, a.name))

    def to_dict(self) -> dict:
        return {uid: a.to_dict() for uid, a in self._by_uid.items()}

    @staticmethod
    def from_dict(d: dict) -> "AssetRegistry":
        reg = AssetRegistry()
        for uid, raw in (d or {}).items():
            raw = dict(raw)
            raw.setdefault("uid", uid)
            a = asset_from_dict(raw)
            reg._by_uid[a.uid] = a
        return reg
