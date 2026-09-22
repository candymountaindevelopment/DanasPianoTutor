"""Undo/redo at the project-model level (spec section 44).

Widgets never mutate the model directly; they push a Command. That keeps undo
correct no matter which panel made the change, and it gives the scripting
console the same undo semantics as the GUI for free.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import Callable

from .assets import Asset, asset_from_dict
from .project import Project


class Command(ABC):
    label: str = "command"

    @abstractmethod
    def execute(self, project: Project) -> None: ...

    @abstractmethod
    def undo(self, project: Project) -> None: ...


class CommandStack:
    def __init__(self, project: Project, limit: int = 200) -> None:
        self.project = project
        self.limit = limit
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._clean_depth = 0
        self.on_change: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ state

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    @property
    def dirty(self) -> bool:
        return len(self._undo) != self._clean_depth

    def mark_clean(self) -> None:
        self._clean_depth = len(self._undo)
        self._notify()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._clean_depth = 0
        self._notify()

    def _notify(self) -> None:
        for cb in list(self.on_change):
            cb()

    # ---------------------------------------------------------------- actions

    def push(self, command: Command) -> None:
        command.execute(self.project)
        self._undo.append(command)
        self._redo.clear()
        if len(self._undo) > self.limit:
            drop = len(self._undo) - self.limit
            del self._undo[:drop]
            self._clean_depth = max(0, self._clean_depth - drop)
        self._notify()

    def undo(self) -> None:
        if not self._undo:
            return
        cmd = self._undo.pop()
        cmd.undo(self.project)
        self._redo.append(cmd)
        self._notify()

    def redo(self) -> None:
        if not self._redo:
            return
        cmd = self._redo.pop()
        cmd.execute(self.project)
        self._undo.append(cmd)
        self._notify()


# ------------------------------------------------------------------ commands


class AddAsset(Command):
    def __init__(self, asset: Asset) -> None:
        self.asset = asset
        self.label = f"Add {asset.name}"

    def execute(self, project: Project) -> None:
        project.assets.add(self.asset)
        self.label = f"Add {self.asset.name}"

    def undo(self, project: Project) -> None:
        project.assets.remove(self.asset.uid)


class RemoveAsset(Command):
    def __init__(self, uid: str) -> None:
        self.uid = uid
        self._snapshot: dict | None = None
        self.label = "Delete asset"

    def execute(self, project: Project) -> None:
        asset = project.assets.get(self.uid)
        if asset is None:
            return
        self.label = f"Delete {asset.name}"
        self._snapshot = copy.deepcopy(asset.to_dict())
        project.assets.remove(self.uid)

    def undo(self, project: Project) -> None:
        if self._snapshot is not None:
            project.assets.add(asset_from_dict(self._snapshot), rename_on_clash=False)


class ReplaceAsset(Command):
    """Update an asset in place, keeping its UUID so references survive."""

    def __init__(self, uid: str, new_asset: Asset, label: str | None = None) -> None:
        self.uid = uid
        self.payload = copy.deepcopy(new_asset.to_dict())
        self.payload["uid"] = uid
        self._old: dict | None = None
        self.label = label or f"Update {new_asset.name}"

    def execute(self, project: Project) -> None:
        existing = project.assets.get(self.uid)
        if existing is None:
            return
        self._old = copy.deepcopy(existing.to_dict())
        payload = dict(self.payload)
        payload.setdefault("created", existing.created)
        project.assets.replace(self.uid, asset_from_dict(payload))

    def undo(self, project: Project) -> None:
        if self._old is not None:
            project.assets.replace(self.uid, asset_from_dict(self._old))


class RenameAsset(Command):
    def __init__(self, uid: str, new_name: str) -> None:
        self.uid = uid
        self.new_name = new_name
        self._old: str | None = None
        self.label = f"Rename to {new_name}"

    def execute(self, project: Project) -> None:
        asset = project.assets.get(self.uid)
        if asset is None:
            return
        self._old = asset.name
        project.assets.rename(self.uid, self.new_name)

    def undo(self, project: Project) -> None:
        if self._old is not None:
            project.assets.rename(self.uid, self._old)


class SetAssetField(Command):
    """Generic property edit. Covers synth params, tags, descriptions, tempo..."""

    def __init__(self, uid: str, field_name: str, value, label: str | None = None) -> None:
        self.uid = uid
        self.field_name = field_name
        self.value = value
        self._old = None
        self.label = label or f"Change {field_name}"

    def execute(self, project: Project) -> None:
        asset = project.assets.get(self.uid)
        if asset is None:
            return
        self._old = copy.deepcopy(getattr(asset, self.field_name, None))
        setattr(asset, self.field_name, copy.deepcopy(self.value))
        asset.touch()

    def undo(self, project: Project) -> None:
        asset = project.assets.get(self.uid)
        if asset is None:
            return
        setattr(asset, self.field_name, self._old)
        asset.touch()


class SetSettings(Command):
    def __init__(self, **changes) -> None:
        self.changes = changes
        self._old: dict = {}
        self.label = "Change project settings"

    def execute(self, project: Project) -> None:
        self._old = {k: getattr(project.settings, k) for k in self.changes}
        for k, v in self.changes.items():
            setattr(project.settings, k, v)

    def undo(self, project: Project) -> None:
        for k, v in self._old.items():
            setattr(project.settings, k, v)


class SetSlot(Command):
    """Bind, rebind, or clear a sound slot."""

    def __init__(self, name: str, asset_uid: str | None, overrides: dict | None = None,
                 label: str | None = None) -> None:
        self.name = name
        self.asset_uid = asset_uid or None
        self.overrides = copy.deepcopy(overrides) if overrides is not None else None
        self._old: dict | None = None
        self._existed = False
        self.label = label or (f"Bind {name}" if asset_uid else f"Clear {name}")

    def execute(self, project: Project) -> None:
        existing = project.slots.get(self.name)
        self._existed = existing is not None
        self._old = existing.to_dict() if existing is not None else None
        slot = project.slot(self.name)
        slot.asset = self.asset_uid
        if self.overrides is not None:
            slot.overrides = copy.deepcopy(self.overrides)

    def undo(self, project: Project) -> None:
        from .slots import Slot

        if not self._existed:
            project.slots.pop(self.name, None)
            return
        project.slots[self.name] = Slot.from_dict(self.name, self._old)


class RemoveSlot(Command):
    def __init__(self, name: str) -> None:
        self.name = name
        self._old: dict | None = None
        self.label = f"Remove slot {name}"

    def execute(self, project: Project) -> None:
        slot = project.slots.pop(self.name, None)
        self._old = slot.to_dict() if slot is not None else None

    def undo(self, project: Project) -> None:
        from .slots import Slot

        if self._old is not None:
            project.slots[self.name] = Slot.from_dict(self.name, self._old)


class AddClips(Command):
    """Place timeline clips. Clips must already carry unique ids."""

    def __init__(self, clips: list[dict], label: str | None = None) -> None:
        self.clips = [copy.deepcopy(c) for c in clips]
        self.label = label or (f"Add {len(self.clips)} clip(s)" if len(self.clips) != 1 else "Add clip")

    def execute(self, project: Project) -> None:
        project.timeline.setdefault("clips", []).extend(copy.deepcopy(self.clips))

    def undo(self, project: Project) -> None:
        ids = {c["id"] for c in self.clips}
        clips = project.timeline.get("clips", [])
        project.timeline["clips"] = [c for c in clips if c.get("id") not in ids]


class RemoveClips(Command):
    def __init__(self, clip_ids, label: str | None = None) -> None:
        self.ids = set(clip_ids)
        self._snapshot: list[tuple[int, dict]] = []
        self.label = label or (f"Remove {len(self.ids)} clip(s)" if len(self.ids) != 1 else "Remove clip")

    def execute(self, project: Project) -> None:
        clips = project.timeline.get("clips", [])
        self._snapshot = [
            (i, copy.deepcopy(c)) for i, c in enumerate(clips) if c.get("id") in self.ids
        ]
        project.timeline["clips"] = [c for c in clips if c.get("id") not in self.ids]

    def undo(self, project: Project) -> None:
        clips = project.timeline.setdefault("clips", [])
        for index, clip in self._snapshot:
            clips.insert(min(index, len(clips)), copy.deepcopy(clip))


class MoveClips(Command):
    """Record a completed drag so it can be undone.

    The new positions are already in the model by the time this is pushed, so
    execute() re-applies them idempotently.
    """

    def __init__(self, moves: list[tuple[str, tuple[float, int], tuple[float, int]]]) -> None:
        self.moves = list(moves)
        self.label = f"Move {len(self.moves)} clip(s)" if len(self.moves) != 1 else "Move clip"

    def _apply(self, project: Project, index: int) -> None:
        by_id = {c.get("id"): c for c in project.timeline.get("clips", [])}
        for clip_id, old, new in self.moves:
            clip = by_id.get(clip_id)
            if clip is None:
                continue
            start, track = (old, new)[index]
            clip["start"] = start
            clip["track"] = track

    def execute(self, project: Project) -> None:
        self._apply(project, 1)

    def undo(self, project: Project) -> None:
        self._apply(project, 0)


class Composite(Command):
    """Group several edits into one undo step."""

    def __init__(self, commands: list[Command], label: str = "Edit") -> None:
        self.commands = commands
        self.label = label

    def execute(self, project: Project) -> None:
        for c in self.commands:
            c.execute(project)

    def undo(self, project: Project) -> None:
        for c in reversed(self.commands):
            c.undo(project)
