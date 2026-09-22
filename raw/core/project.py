"""The project model — the source of truth for the whole application."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import PROJECT_VERSION, __version__
from .assets import AssetRegistry
from .migrations import migrate
from .slots import Slot


@dataclass
class Settings:
    sample_rate: int = 44100
    tempo: float = 120.0
    time_signature: tuple[int, int] = (4, 4)
    fps: int = 60
    auto_normalize: bool = False
    master_gain_db: float = 0.0

    def to_dict(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "tempo": self.tempo,
            "time_signature": list(self.time_signature),
            "fps": self.fps,
            "auto_normalize": self.auto_normalize,
            "master_gain_db": self.master_gain_db,
        }

    @staticmethod
    def from_dict(d: dict) -> "Settings":
        ts = d.get("time_signature", [4, 4])
        return Settings(
            sample_rate=int(d.get("sample_rate", 44100)),
            tempo=float(d.get("tempo", 120.0)),
            time_signature=(int(ts[0]), int(ts[1])),
            fps=int(d.get("fps", 60)),
            auto_normalize=bool(d.get("auto_normalize", False)),
            master_gain_db=float(d.get("master_gain_db", 0.0)),
        )


@dataclass
class Project:
    metadata: dict = field(
        default_factory=lambda: {
            "name": "Untitled",
            "author": "",
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "modified": "",
        }
    )
    settings: Settings = field(default_factory=Settings)
    assets: AssetRegistry = field(default_factory=AssetRegistry)
    # Named roles a pattern can reference before a sound has been chosen.
    slots: dict = field(default_factory=dict)
    timeline: dict = field(default_factory=lambda: {"tracks": [], "clips": []})
    generators: dict = field(default_factory=dict)
    export_profiles: dict = field(default_factory=dict)
    editor_state: dict = field(default_factory=dict)

    path: Path | None = None

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Untitled")

    def to_dict(self) -> dict:
        return {
            "project_version": PROJECT_VERSION,
            "application_version": __version__,
            "metadata": dict(self.metadata),
            "settings": self.settings.to_dict(),
            "assets": self.assets.to_dict(),
            "slots": {name: slot.to_dict() for name, slot in self.slots.items()},
            "timeline": self.timeline,
            "generators": self.generators,
            "export_profiles": self.export_profiles,
            "editor_state": self.editor_state,
        }

    @staticmethod
    def from_dict(d: dict) -> tuple["Project", list[str]]:
        d, notes = migrate(dict(d))
        p = Project(
            metadata=dict(d.get("metadata", {})),
            settings=Settings.from_dict(d.get("settings", {})),
            assets=AssetRegistry.from_dict(d.get("assets", {})),
            slots={
                name: Slot.from_dict(name, value)
                for name, value in (d.get("slots") or {}).items()
            },
            timeline=d.get("timeline", {"tracks": [], "clips": []}),
            generators=d.get("generators", {}),
            export_profiles=d.get("export_profiles", {}),
            editor_state=d.get("editor_state", {}),
        )
        p.metadata.setdefault("name", "Untitled")
        return p, notes

    # -------------------------------------------------------------------- io

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata["modified"] = _dt.datetime.now().isoformat(timespec="seconds")
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        self.path = path
        return path

    @staticmethod
    def load(path: str | Path) -> tuple["Project", list[str]]:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        project, notes = Project.from_dict(data)
        project.path = path
        return project, notes

    # ------------------------------------------------------------ directories

    @property
    def root(self) -> Path:
        return self.path.parent if self.path else Path.cwd()

    def resolve_asset_path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (self.root / p)

    # ---------------------------------------------------------------- slots

    def slot(self, name: str) -> Slot:
        """Get or create a slot by name."""
        if name not in self.slots:
            self.slots[name] = Slot(name)
        return self.slots[name]

    def bind_slot(self, name: str, asset_uid: str | None) -> Slot:
        slot = self.slot(name)
        slot.asset = asset_uid or None
        return slot

    @property
    def unbound_slot_names(self) -> list[str]:
        return sorted(n for n, s in self.slots.items() if not s.bound)
