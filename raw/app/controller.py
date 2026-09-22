"""The controller: the only thing the GUI is allowed to mutate the model through.

Every edit goes through a Command, so undo works identically whether the change
came from a dock, a menu, or the scripting console.
"""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal

from ..audio.buffer import AudioBuffer
from ..audio.io import read_audio
from ..audio.playback import get_backend
from ..core.assets import Asset, AudioAsset, SynthAsset
from ..core.authoring import AuthorResult, document_from_project, parse_document
from ..core.commands import (
    AddAsset,
    Command,
    CommandStack,
    Composite,
    RemoveAsset,
    RemoveSlot,
    RenameAsset,
    ReplaceAsset,
    SetSettings,
    SetSlot,
)
from ..core.project import Project
from ..core.renderer import Renderer
from ..core.validation import validate
from ..synth import engine


class _WorkerSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)


class Worker(QRunnable):
    """Runs a callable off the GUI thread (spec section 49)."""

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _WorkerSignals()

    def run(self) -> None:  # pragma: no cover - thread body
        try:
            self.signals.finished.emit(self.fn(*self.args, **self.kwargs))
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


class Controller(QObject):
    projectChanged = pyqtSignal()
    assetsChanged = pyqtSignal()
    timelineChanged = pyqtSignal()
    slotsChanged = pyqtSignal()
    # origin ("timeline" / "preview"), then seconds. Origin lets the timeline
    # follow only its own playback and ignore one-off asset previews.
    playbackStarted = pyqtSignal(str, float)
    playbackPosition = pyqtSignal(str, float)
    playbackStopped = pyqtSignal(str)
    assetModified = pyqtSignal(str)
    selectionChanged = pyqtSignal(str)
    dirtyChanged = pyqtSignal(bool)
    statusMessage = pyqtSignal(str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project = Project()
        self.stack = CommandStack(self.project)
        self.renderer = Renderer()
        self.playback = get_backend()
        self.pool = QThreadPool.globalInstance()
        self._selected: str = ""
        # Clips cut or copied from the timeline, stored relative to the
        # earliest one so a paste keeps the group's internal spacing.
        self.clip_clipboard: list[dict] = []
        # The track the copied clips came from, so a paste lands back on it
        # rather than jumping to the top of the timeline.
        self.clip_clipboard_track: int = 0

        # Playback progress. sounddevice gives no position callback, so the
        # position is wall-clock from the moment playback starts. Good enough
        # to drive a playhead; it is not sample-accurate.
        self._play_origin = ""
        self._play_offset = 0.0
        self._play_started_at = 0.0
        self._play_duration = 0.0
        self._play_loop = False
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(33)
        self._play_timer.timeout.connect(self._tick_playback)
        self.stack.on_change.append(self._on_stack_change)

    # ------------------------------------------------------------- lifecycle

    def _on_stack_change(self) -> None:
        self.dirtyChanged.emit(self.stack.dirty)

    def _rebind(self, project: Project) -> None:
        self.project = project
        self.stack = CommandStack(project)
        self.stack.on_change.append(self._on_stack_change)
        self.renderer.cache.clear()
        self._selected = ""
        self.projectChanged.emit()
        self.assetsChanged.emit()
        self.timelineChanged.emit()
        self.selectionChanged.emit("")
        self.dirtyChanged.emit(False)

    def new_project(self) -> None:
        self._rebind(Project())
        self.status("New project")

    def open_project(self, path: str | Path) -> None:
        project, notes = Project.load(path)
        self._rebind(project)
        self.status(f"Opened {Path(path).name}" + (f" — {'; '.join(notes)}" if notes else ""))

    def save_project(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.project.path
        if target is None:
            raise ValueError("no path")
        self.project.save(target)
        self.stack.mark_clean()
        self.status(f"Saved {target.name}")
        return target

    @property
    def dirty(self) -> bool:
        return self.stack.dirty

    def status(self, message: str, timeout: int = 5000) -> None:
        self.statusMessage.emit(message, timeout)

    # ------------------------------------------------------------- selection

    @property
    def selected_uid(self) -> str:
        return self._selected

    @property
    def selected(self) -> Asset | None:
        return self.project.assets.get(self._selected) if self._selected else None

    def select(self, uid: str) -> None:
        if uid != self._selected:
            self._selected = uid or ""
            self.selectionChanged.emit(self._selected)

    # ---------------------------------------------------------------- edits

    def push(self, command: Command) -> None:
        self.stack.push(command)

    def undo(self) -> None:
        label = self.stack.undo_label
        self.stack.undo()
        self.assetsChanged.emit()
        self.timelineChanged.emit()
        self.selectionChanged.emit(self._selected)
        self.status(f"Undo: {label}" if label else "Undo")

    def redo(self) -> None:
        label = self.stack.redo_label
        self.stack.redo()
        self.assetsChanged.emit()
        self.timelineChanged.emit()
        self.selectionChanged.emit(self._selected)
        self.status(f"Redo: {label}" if label else "Redo")

    def add_synth_asset(self, name: str, params=None) -> SynthAsset:
        asset = SynthAsset(name=name, params=params or engine.SynthParams())
        self.push(AddAsset(asset))
        self.assetsChanged.emit()
        self.select(asset.uid)
        self.status(f"Created {asset.name}")
        return asset

    def duplicate_asset(self, uid: str) -> Asset | None:
        from ..core.assets import asset_from_dict
        from ..core.ids import new_uid

        src = self.project.assets.get(uid)
        if src is None:
            return None
        d = src.to_dict()
        d["uid"] = new_uid()
        copy = asset_from_dict(d)
        self.push(AddAsset(copy))
        self.assetsChanged.emit()
        self.select(copy.uid)
        return copy

    def delete_asset(self, uid: str) -> None:
        self.delete_assets([uid])

    def delete_assets(self, uids: list[str]) -> None:
        names = [a.name for u in uids if (a := self.project.assets.get(u))]
        live = [u for u in uids if self.project.assets.get(u)]
        if not live:
            return
        if len(live) == 1:
            self.push(RemoveAsset(live[0]))
        else:
            self.push(Composite([RemoveAsset(u) for u in live], f"Delete {len(live)} assets"))
        if self._selected in live:
            self.select("")
        self.assetsChanged.emit()
        self.status("Deleted " + ", ".join(names[:3]) + (" …" if len(names) > 3 else ""))

    def rename_asset(self, uid: str, name: str) -> None:
        self.push(RenameAsset(uid, name))
        self.assetsChanged.emit()

    def modify_asset(self, uid: str, command: Command) -> None:
        self.push(command)
        self.assetModified.emit(uid)

    def import_sample(self, path: str | Path) -> AudioAsset | None:
        from ..core.ids import slugify

        path = Path(path)
        buf = read_audio(path)
        try:
            rel = str(path.relative_to(self.project.root))
        except ValueError:
            rel = str(path)
        asset = AudioAsset(
            name=slugify(path.stem),
            source_path=rel,
            sample_rate=buf.sample_rate,
            channels=buf.channels,
            duration=buf.duration,
        )
        self.push(AddAsset(asset))
        self.assetsChanged.emit()
        self.select(asset.uid)
        self.status(f"Imported {path.name} as {asset.name}")
        return asset

    # -------------------------------------------------------- authored JSON

    def import_authored(self, document, merge: bool = True) -> AuthorResult:
        """Import an authoring-format document (see docs/AUTHORING_FORMAT.md).

        The whole import is one undo step, so a bad chatbot document is one
        Ctrl+Z away from gone.
        """
        result = parse_document(document, self.project, merge=merge)
        if not result.all_assets and not result.slots:
            self.status("Nothing imported — see the report", 8000)
            return result

        commands: list[Command] = [AddAsset(a) for a in result.assets]
        commands += [ReplaceAsset(a.uid, a) for a in result.updated]
        for name, entry in result.slots.items():
            commands.append(
                SetSlot(name, entry.get("asset"), entry.get("overrides"), f"Slot {name}")
            )
        changes = {}
        if "tempo" in result.settings:
            changes["tempo"] = float(result.settings["tempo"])
        if "sample_rate" in result.settings:
            changes["sample_rate"] = int(result.settings["sample_rate"])
        if "fps" in result.settings:
            changes["fps"] = int(result.settings["fps"])
        if changes:
            commands.append(SetSettings(**changes))

        self.push(Composite(commands, f"Import {len(result.all_assets)} authored asset(s)"))
        if "name" in result.settings:
            self.project.metadata["name"] = str(result.settings["name"])
        self.assetsChanged.emit()
        self.slotsChanged.emit()
        self.projectChanged.emit()
        if result.assets:
            self.select(result.assets[0].uid)
        parts = []
        if result.assets:
            parts.append(f"{len(result.assets)} new")
        if result.updated:
            parts.append(f"{len(result.updated)} updated")
        if result.slots:
            unbound = sum(1 for e in result.slots.values() if not e.get("asset"))
            parts.append(f"{len(result.slots)} slot(s)" + (f", {unbound} unbound" if unbound else ""))
        if result.warnings:
            parts.append(f"{len(result.warnings)} warning(s)")
        self.status("Imported " + " · ".join(parts), 8000)
        return result

    # ---------------------------------------------------------------- slots

    def bind_slot(self, name: str, asset_uid: str | None, overrides: dict | None = None) -> None:
        asset = self.project.assets.get(asset_uid) if asset_uid else None
        self.push(
            SetSlot(name, asset_uid, overrides,
                    f"Bind {name} to {asset.name}" if asset else f"Clear slot {name}")
        )
        self.slotsChanged.emit()
        self.status(f"{name} → {asset.name}" if asset else f"{name} unbound")

    def remove_slot(self, name: str) -> None:
        if name not in self.project.slots:
            return
        self.push(RemoveSlot(name))
        self.slotsChanged.emit()
        self.status(f"Removed slot {name}")

    def export_authored(self) -> dict:
        return document_from_project(self.project)

    # -------------------------------------------------------------- audio

    def render(self, asset: Asset | None = None, overrides: dict | None = None,
               preview: bool = True) -> AudioBuffer | None:
        asset = asset or self.selected
        if asset is None:
            return None
        try:
            buf = self.renderer.render_asset(asset, self.project, overrides, preview=preview)
        except Exception as exc:
            self.status(f"Render failed: {type(exc).__name__}: {exc}", 8000)
            return None
        for w in self.renderer.last_warnings:
            self.status(f"{asset.name}: {w}", 8000)
        return buf

    def preview(self, asset: Asset | None = None, overrides: dict | None = None) -> AudioBuffer | None:
        buf = self.render(asset, overrides, preview=True)
        if buf is not None:
            self.play(buf)
        return buf

    def play(self, buffer: AudioBuffer, loop: bool = False,
             origin: str = "preview", offset: float = 0.0) -> None:
        if self.playback.available:
            try:
                self.playback.play(buffer, loop)
            except Exception as exc:
                self.status(f"Playback failed: {exc}", 6000)
        else:
            self.status("No audio device — rendering works, playback is disabled", 6000)
        self._start_progress(origin, buffer.duration, offset, loop)

    def stop(self) -> None:
        self.playback.stop()
        self._stop_progress()

    # ------------------------------------------------------- play progress

    def _start_progress(self, origin: str, duration: float, offset: float = 0.0,
                        loop: bool = False) -> None:
        self._play_origin = origin
        self._play_offset = float(offset)
        self._play_duration = float(duration)
        self._play_loop = bool(loop)
        self._play_started_at = time.perf_counter()
        self.playbackStarted.emit(origin, self._play_offset + self._play_duration)
        self.playbackPosition.emit(origin, self._play_offset)
        self._play_timer.start()

    def _stop_progress(self) -> None:
        if self._play_timer.isActive():
            self._play_timer.stop()
            self.playbackStopped.emit(self._play_origin)

    def _tick_playback(self) -> None:
        elapsed = time.perf_counter() - self._play_started_at
        if self._play_loop and self._play_duration > 0:
            # Looping playback only ends when something stops it, so the
            # position wraps instead of running off the end.
            wrapped = elapsed % self._play_duration
            self.playbackPosition.emit(self._play_origin, self._play_offset + wrapped)
            return
        if elapsed >= self._play_duration:
            self.playbackPosition.emit(self._play_origin, self._play_offset + self._play_duration)
            self._stop_progress()
            return
        self.playbackPosition.emit(self._play_origin, self._play_offset + elapsed)

    @property
    def is_playing(self) -> bool:
        return self._play_timer.isActive()

    # ----------------------------------------------------------- background

    def run_async(self, fn, on_done=None, on_fail=None, *args, **kwargs) -> Worker:
        worker = Worker(fn, *args, **kwargs)
        if on_done:
            worker.signals.finished.connect(on_done)
        worker.signals.failed.connect(on_fail or (lambda m: self.status(m, 8000)))
        self.pool.start(worker)
        return worker

    # ----------------------------------------------------------- validation

    def validate(self):
        return validate(self.project)
