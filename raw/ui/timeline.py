"""Timeline dock — arrangement of asset instances on tracks.

Clips reference assets by UUID and carry their own override dict, so the same
asset can appear many times with different duration, pitch, and volume without
being duplicated (spec section 101).

Every clip edit — add, move, copy, paste, delete — goes through a Command, so
Ctrl+Z covers arranging just as it covers editing a sound.
"""

from __future__ import annotations

import copy
import math
import uuid

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..audio.buffer import AudioBuffer
from ..core.assets import PatternAsset
from ..core.commands import AddClips, MoveClips, RemoveClips, SetAssetField, SetSettings
from .dnd import ASSET_MIME, asset_uids_from_mime
from .theme import COLORS

TRACK_HEIGHT = 46
RULER_HEIGHT = 22
DEFAULT_TRACKS = [
    {"name": "MUSIC", "type": "pattern"},
    {"name": "SFX 1", "type": "audio"},
    {"name": "SFX 2", "type": "audio"},
    {"name": "EVENTS", "type": "event"},
]


def new_clip(asset_uid: str, track: int, start: float, duration: float, overrides=None) -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "asset": asset_uid,
        "track": int(track),
        "start": float(start),
        "duration": float(duration),
        "overrides": dict(overrides or {}),
    }


class ClipItem(QGraphicsRectItem):
    def __init__(self, clip: dict, view: "TimelineView") -> None:
        super().__init__()
        self.clip = clip
        self.view = view
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.refresh()

    def refresh(self) -> None:
        pps = self.view.pps
        dur = max(0.02, float(self.clip.get("duration", 0.25)))
        self.setRect(0, 0, dur * pps, TRACK_HEIGHT - 8)
        self.setPos(
            float(self.clip.get("start", 0.0)) * pps,
            RULER_HEIGHT + int(self.clip.get("track", 0)) * TRACK_HEIGHT + 4,
        )
        self.setBrush(QBrush(QColor(COLORS["accent_dim"])))
        self.setPen(QPen(QColor(COLORS["accent"]), 1))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            pos: QPointF = value
            x = max(0.0, self.view.snap(pos.x() / self.view.pps) * self.view.pps)
            track = int(round((pos.y() - RULER_HEIGHT - 4) / TRACK_HEIGHT))
            track = max(0, min(len(self.view.tracks) - 1, track))
            self.clip["start"] = x / self.view.pps
            self.clip["track"] = track
            return QPointF(x, RULER_HEIGHT + track * TRACK_HEIGHT + 4)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        r = self.rect()
        if self.isSelected():
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawRect(r.adjusted(1, 1, -1, -1))
        painter.setPen(QColor("#ffffff"))
        f = QFont(painter.font())
        f.setPointSizeF(8.5)
        painter.setFont(f)
        name = self.clip.get("label", "clip")
        if self.clip.get("overrides"):
            name += " *"
        painter.drawText(QRectF(r.x() + 4, r.y() + 2, r.width() - 8, r.height() - 4),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)


class TimelineView(QGraphicsView):
    playheadMoved = pyqtSignal(float)
    statusHint = pyqtSignal(str)

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.pps = 120.0
        self.grid = "1/4"
        self.playhead = 0.0
        self._drag_before: dict[str, tuple[float, int]] = {}
        self._drop_preview: tuple[float, int, int] | None = None  # start, track, count
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setMinimumHeight(RULER_HEIGHT + 4 * TRACK_HEIGHT + 8)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------ data

    @property
    def timeline(self) -> dict:
        tl = self.c.project.timeline
        if not tl.get("tracks"):
            tl["tracks"] = [dict(t) for t in DEFAULT_TRACKS]
        tl.setdefault("clips", [])
        return tl

    @property
    def tracks(self) -> list:
        return self.timeline["tracks"]

    def beat_seconds(self) -> float:
        return 60.0 / max(1e-6, self.c.project.settings.tempo)

    def grid_seconds(self) -> float:
        beat = self.beat_seconds()
        table = {
            "bar": beat * self.c.project.settings.time_signature[0],
            "1/4": beat,
            "1/8": beat / 2,
            "1/16": beat / 4,
            "1/32": beat / 8,
            "frame": 1.0 / max(1, self.c.project.settings.fps),
            "off": 0.0,
        }
        return table.get(self.grid, beat)

    def snap(self, seconds: float) -> float:
        g = self.grid_seconds()
        return seconds if g <= 0 else round(seconds / g) * g

    def snap_up(self, seconds: float) -> float:
        """Snap forward to the next grid line.

        Laying clips out end to end must use this rather than snap(): nearest
        rounding can pull the cursor back to where the previous clip started,
        stacking short sounds on top of each other.
        """
        g = self.grid_seconds()
        if g <= 0:
            return seconds
        return math.ceil(seconds / g - 1e-9) * g

    def content_duration(self) -> float:
        clips = self.timeline["clips"]
        if not clips:
            return 8.0
        return max(8.0, max(c.get("start", 0) + c.get("duration", 0.25) for c in clips) + 2.0)

    def asset_duration(self, asset) -> float:
        buf = self.c.render(asset, preview=True)
        return buf.duration if buf else 0.25

    def track_at(self, scene_y: float) -> int:
        return max(0, min(len(self.tracks) - 1,
                          int((scene_y - RULER_HEIGHT) / TRACK_HEIGHT)))

    # ------------------------------------------------------------------ view

    def sync_clip_durations(self) -> None:
        """Make each clip's drawn width match what it actually renders to.

        The rectangle used to be decorative: a duration override, or a sample
        fitted to a bar count, changed the audio but not the block on screen.
        Renders are cached, so this is a hash lookup per clip after the first
        pass.
        """
        for clip in self.timeline.get("clips", []):
            asset = self.c.project.assets.get(clip.get("asset", ""))
            if asset is None:
                continue
            try:
                buf = self.c.renderer.render_asset(
                    asset, self.c.project, clip.get("overrides") or {}, preview=True
                )
            except Exception:
                continue
            if buf.duration > 0:
                clip["duration"] = buf.duration

    def rebuild(self) -> None:
        selected = {i.clip.get("id") for i in self.scene().selectedItems() if isinstance(i, ClipItem)}
        self.sync_clip_durations()
        scene = self.scene()
        scene.clear()
        height = RULER_HEIGHT + len(self.tracks) * TRACK_HEIGHT
        scene.setSceneRect(0, 0, self.content_duration() * self.pps, height)
        for clip in self.timeline["clips"]:
            asset = self.c.project.assets.get(clip.get("asset", ""))
            clip["label"] = asset.name if asset else "(missing)"
            item = ClipItem(clip, self)
            scene.addItem(item)
            if clip.get("id") in selected:
                item.setSelected(True)
        self.viewport().update()

    def selected_clips(self) -> list[dict]:
        return [i.clip for i in self.scene().selectedItems() if isinstance(i, ClipItem)]

    def drawBackground(self, painter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor(COLORS["panel"]))
        height = RULER_HEIGHT + len(self.tracks) * TRACK_HEIGHT

        for i, track in enumerate(self.tracks):
            y = RULER_HEIGHT + i * TRACK_HEIGHT
            if i % 2 == 0:
                painter.fillRect(QRectF(rect.left(), y, rect.width(), TRACK_HEIGHT),
                                 QColor(COLORS["panel_alt"]))
            painter.setPen(QPen(QColor(COLORS["border"]), 1))
            painter.drawLine(int(rect.left()), y, int(rect.right()), y)

        g = self.grid_seconds()
        if g > 0:
            step = g * self.pps
            if step >= 6:
                x = (int(rect.left() / step)) * step
                painter.setPen(QPen(QColor(COLORS["grid"]), 1))
                while x < rect.right():
                    painter.drawLine(int(x), RULER_HEIGHT, int(x), height)
                    x += step

        beat = self.beat_seconds() * self.pps
        bar = beat * self.c.project.settings.time_signature[0]
        painter.fillRect(QRectF(rect.left(), 0, rect.width(), RULER_HEIGHT),
                         QColor(COLORS["bg"]))
        painter.setPen(QColor(COLORS["text_dim"]))
        f = QFont(painter.font())
        f.setPointSizeF(8.0)
        painter.setFont(f)
        if bar > 12:
            n = int(rect.left() / bar)
            x = n * bar
            while x < rect.right():
                painter.drawLine(int(x), 0, int(x), height)
                painter.drawText(QRectF(x + 3, 2, 60, 14), Qt.AlignmentFlag.AlignLeft, f"{n + 1}")
                x += bar
                n += 1

        px = self.playhead * self.pps
        painter.setPen(QPen(QColor(COLORS["playhead"]), 1))
        painter.drawLine(int(px), 0, int(px), height)

    def drawForeground(self, painter, rect: QRectF) -> None:
        # Track names pinned to the left edge of the viewport, not the scene.
        left = self.mapToScene(0, 0).x()
        f = QFont(painter.font())
        f.setPointSizeF(8.0)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(COLORS["text_dim"]))
        for i, track in enumerate(self.tracks):
            y = RULER_HEIGHT + i * TRACK_HEIGHT
            painter.drawText(QRectF(left + 6, y + 3, 90, 14),
                             Qt.AlignmentFlag.AlignLeft, track.get("name", f"TRACK {i + 1}"))

        if self._drop_preview is not None:
            start, track, count = self._drop_preview
            x = start * self.pps
            y = RULER_HEIGHT + track * TRACK_HEIGHT + 4
            pen = QPen(QColor(COLORS["accent"]), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x, y, max(30.0, 0.5 * self.pps), TRACK_HEIGHT - 8))
            painter.setPen(QColor(COLORS["accent"]))
            painter.drawText(QRectF(x + 5, y + 4, 200, 14), Qt.AlignmentFlag.AlignLeft,
                             f"drop {count} here")

    # ---------------------------------------------------------------- events

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.pps = max(8.0, min(4000.0, self.pps * factor))
            self.rebuild()
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        if pos.y() < RULER_HEIGHT:
            self.playhead = max(0.0, pos.x() / self.pps)
            self.playheadMoved.emit(self.playhead)
            self.viewport().update()
            return
        super().mousePressEvent(event)
        # Snapshot positions so a completed drag becomes one undo step.
        self._drag_before = {
            c["id"]: (float(c.get("start", 0.0)), int(c.get("track", 0)))
            for c in self.selected_clips()
        }

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self._drag_before:
            return
        moves = []
        by_id = {c["id"]: c for c in self.timeline["clips"]}
        for clip_id, before in self._drag_before.items():
            clip = by_id.get(clip_id)
            if clip is None:
                continue
            after = (float(clip.get("start", 0.0)), int(clip.get("track", 0)))
            if after != before:
                moves.append((clip_id, before, after))
        self._drag_before = {}
        if moves:
            self.c.push(MoveClips(moves))
            self.rebuild()

    def follow_playhead(self) -> None:
        """Keep a running playhead on screen without fighting manual scrolling."""
        x = self.playhead * self.pps
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        if x < visible.left() or x > visible.right() - 40:
            self.centerOn(x + visible.width() * 0.3, visible.center().y())
        self.viewport().update()

    def select_all_clips(self) -> None:
        for item in self.scene().items():
            if isinstance(item, ClipItem):
                item.setSelected(True)

    # ------------------------------------------------------------------ dnd

    def _drop_target(self, position) -> tuple[float, int]:
        pos = self.mapToScene(position.toPoint())
        return max(0.0, self.snap(pos.x() / self.pps)), self.track_at(pos.y())

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(ASSET_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(ASSET_MIME):
            start, track = self._drop_target(event.position())
            count = len(asset_uids_from_mime(event.mimeData()))
            self._drop_preview = (start, track, count)
            self.viewport().update()
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._drop_preview = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        uids = asset_uids_from_mime(event.mimeData())
        self._drop_preview = None
        if not uids:
            super().dropEvent(event)
            return
        start, track = self._drop_target(event.position())
        self.place_assets(uids, start, track)
        event.acceptProposedAction()

    def place_assets(self, uids: list[str], start: float, track: int) -> list[dict]:
        """Lay assets out end to end from `start` on `track`."""
        clips: list[dict] = []
        cursor = start
        names: list[str] = []
        for uid in uids:
            asset = self.c.project.assets.get(uid)
            if asset is None:
                continue
            duration = self.asset_duration(asset)
            clips.append(new_clip(uid, track, cursor, duration))
            names.append(asset.name)
            cursor = self.snap_up(cursor + duration)
        if not clips:
            self.statusHint.emit("Nothing placed — those assets are not renderable")
            return []
        self.c.push(AddClips(clips, f"Place {', '.join(names[:3])}"
                                    + (" …" if len(names) > 3 else "")))
        self.rebuild()
        self.statusHint.emit(
            f"Placed {len(clips)} clip(s) on {self.tracks[track].get('name', track)} at {start:.3f}s"
        )
        return self._live(clips)

    # --------------------------------------------------------------- editing

    def copy_clips(self) -> int:
        selected = self.selected_clips()
        if not selected:
            self.statusHint.emit("Select clips on the timeline first")
            return 0
        origin = min(float(c.get("start", 0.0)) for c in selected)
        base_track = min(int(c.get("track", 0)) for c in selected)
        self.c.clip_clipboard = [
            {
                "asset": c.get("asset"),
                "duration": c.get("duration", 0.25),
                "overrides": copy.deepcopy(c.get("overrides", {})),
                "rel_start": float(c.get("start", 0.0)) - origin,
                "rel_track": int(c.get("track", 0)) - base_track,
            }
            for c in selected
        ]
        self.c.clip_clipboard_track = base_track
        self.statusHint.emit(f"Copied {len(selected)} clip(s)")
        return len(selected)

    def cut_clips(self) -> int:
        count = self.copy_clips()
        if count:
            self.delete_clips()
        return count

    def paste_clips(self, at: float | None = None, track: int | None = None) -> list[dict]:
        if not self.c.clip_clipboard:
            self.statusHint.emit("Clipboard is empty — copy some clips first")
            return []
        start = self.snap(self.playhead if at is None else at)
        base_track = getattr(self.c, "clip_clipboard_track", 0) if track is None else track
        clips = []
        for entry in self.c.clip_clipboard:
            target = max(0, min(len(self.tracks) - 1, base_track + entry["rel_track"]))
            clips.append(
                new_clip(entry["asset"], target, start + entry["rel_start"],
                         entry["duration"], entry["overrides"])
            )
        self.c.push(AddClips(clips, f"Paste {len(clips)} clip(s)"))
        self.rebuild()
        self._select_ids({c["id"] for c in clips})
        self.statusHint.emit(f"Pasted {len(clips)} clip(s) at {start:.3f}s")
        return self._live(clips)

    def duplicate_clips(self) -> list[dict]:
        selected = self.selected_clips()
        if not selected:
            self.statusHint.emit("Select clips on the timeline first")
            return []
        end = max(float(c.get("start", 0.0)) + float(c.get("duration", 0.0)) for c in selected)
        origin = min(float(c.get("start", 0.0)) for c in selected)
        offset = self.snap_up(end) - origin
        clips = [
            new_clip(c.get("asset"), int(c.get("track", 0)),
                     float(c.get("start", 0.0)) + offset,
                     float(c.get("duration", 0.25)),
                     copy.deepcopy(c.get("overrides", {})))
            for c in selected
        ]
        self.c.push(AddClips(clips, f"Duplicate {len(clips)} clip(s)"))
        self.rebuild()
        self._select_ids({c["id"] for c in clips})
        self.statusHint.emit(f"Duplicated {len(clips)} clip(s)")
        return self._live(clips)

    def delete_clips(self) -> int:
        selected = self.selected_clips()
        if not selected:
            self.statusHint.emit("Select clips on the timeline first")
            return 0
        self.c.push(RemoveClips([c["id"] for c in selected]))
        self.rebuild()
        self.statusHint.emit(f"Removed {len(selected)} clip(s)")
        return len(selected)

    def _live(self, clips: list[dict]) -> list[dict]:
        """The project's own dicts for clips just added.

        Commands deep-copy on execute, so the list handed to AddClips is not
        what ends up in the model. Callers need the live objects or their edits
        quietly go nowhere.
        """
        ids = {c["id"] for c in clips}
        return [c for c in self.timeline["clips"] if c.get("id") in ids]

    def _select_ids(self, ids: set[str]) -> None:
        for item in self.scene().items():
            if isinstance(item, ClipItem):
                item.setSelected(item.clip.get("id") in ids)


class TimelineDock(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        transport = QHBoxLayout()
        self.play_btn = QPushButton("▶  Play")
        self.play_btn.setObjectName("primary")
        self.play_btn.clicked.connect(self.play_timeline)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.clicked.connect(self.c.stop)
        self.time_label = QLabel("0:00.0 / 0:00.0")
        self.time_label.setMinimumWidth(120)
        self.bpm_target = QLabel("project")
        self.bpm = QDoubleSpinBox()
        self.bpm.setRange(20.0, 400.0)
        self.bpm.setDecimals(1)
        self.bpm.setSingleStep(1.0)
        self.bpm.setSuffix(" BPM")
        self.bpm.setKeyboardTracking(False)
        self.bpm.setToolTip(
            "Sets the selected pattern's tempo, or the project tempo when no "
            "pattern is selected. Undoable."
        )
        self.bpm.valueChanged.connect(self._bpm_changed)
        transport.addWidget(self.play_btn)
        transport.addWidget(self.stop_btn)
        transport.addWidget(self.time_label)
        transport.addStretch(1)
        transport.addWidget(self.bpm_target)
        transport.addWidget(self.bpm)
        v.addLayout(transport)

        bar = QHBoxLayout()
        self.add_btn = QPushButton("Add Selected Asset")
        self.add_btn.clicked.connect(self.add_clip)
        self.dup_btn = QPushButton("Duplicate")
        self.dup_btn.clicked.connect(lambda: self.view.duplicate_clips())
        self.del_btn = QPushButton("Remove Clip")
        self.del_btn.clicked.connect(lambda: self.view.delete_clips())
        self.render_btn = QPushButton("Render Timeline")
        self.render_btn.setObjectName("primary")
        self.render_btn.clicked.connect(self.render_timeline)
        self.grid_combo = QComboBox()
        self.grid_combo.addItems(["bar", "1/4", "1/8", "1/16", "1/32", "frame", "off"])
        self.grid_combo.setCurrentText("1/4")
        self.grid_combo.currentTextChanged.connect(self._set_grid)
        for b in (self.add_btn, self.dup_btn, self.del_btn, self.render_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel("Snap"))
        bar.addWidget(self.grid_combo)
        v.addLayout(bar)

        self.view = TimelineView(controller)
        v.addWidget(self.view, 1)

        # Widget-scoped shortcuts: they fire only when the timeline has focus,
        # so Ctrl+C still copies text in the console and the property fields.
        self.clip_actions = [
            self._clip_action("Copy Clips", "Ctrl+C", self.view.copy_clips),
            self._clip_action("Cut Clips", "Ctrl+X", self.view.cut_clips),
            self._clip_action("Paste Clips at Playhead", "Ctrl+V", self.view.paste_clips),
            self._clip_action("Select All Clips", "Ctrl+A", self.view.select_all_clips),
        ]

        self.info = QLabel(
            "Drag assets here from the Asset Manager · Ctrl+C/X/V copy, cut, paste · "
            "Ctrl+D duplicate · Del remove · Ctrl+Wheel zoom · click the ruler to move the playhead"
        )
        self.info.setWordWrap(True)
        self.info.setStyleSheet(f"color: {COLORS['text_dim']};")
        v.addWidget(self.info)

        self.view.statusHint.connect(lambda m: self.c.status(m))
        self.c.projectChanged.connect(self.view.rebuild)
        self.c.assetsChanged.connect(self.view.rebuild)
        self.c.timelineChanged.connect(self.view.rebuild)
        self.c.assetModified.connect(lambda _: self.view.rebuild())
        self.c.projectChanged.connect(self._sync_bpm)
        self.c.selectionChanged.connect(lambda _: self._sync_bpm())
        self.c.assetModified.connect(lambda _: self._sync_bpm())
        self.c.playbackPosition.connect(self._on_position)
        self.c.playbackStarted.connect(self._on_started)
        self.c.playbackStopped.connect(self._on_stopped)
        self._syncing_bpm = False
        self._playing = False
        self._play_from = 0.0
        self._total = 0.0
        self._sync_bpm()
        self.view.rebuild()

    # ------------------------------------------------------------- transport

    def _bpm_owner(self):
        """The pattern whose tempo the control edits, or None for the project."""
        asset = self.c.selected
        return asset if isinstance(asset, PatternAsset) else None

    def _sync_bpm(self) -> None:
        owner = self._bpm_owner()
        self._syncing_bpm = True
        if owner is not None:
            self.bpm.setValue(float(owner.tempo))
            self.bpm_target.setText(owner.name)
        else:
            self.bpm.setValue(float(self.c.project.settings.tempo))
            self.bpm_target.setText("project")
        self._syncing_bpm = False

    def _bpm_changed(self, value: float) -> None:
        if self._syncing_bpm:
            return
        owner = self._bpm_owner()
        if owner is not None:
            if abs(float(owner.tempo) - value) < 1e-9:
                return
            self.c.modify_asset(
                owner.uid, SetAssetField(owner.uid, "tempo", float(value), f"Tempo {value:g}")
            )
        else:
            if abs(float(self.c.project.settings.tempo) - value) < 1e-9:
                return
            self.c.push(SetSettings(tempo=float(value)))
            self.c.projectChanged.emit()
        self.view.viewport().update()

    @staticmethod
    def _clock(seconds: float) -> str:
        seconds = max(0.0, seconds)
        return f"{int(seconds // 60)}:{seconds % 60:04.1f}"

    def _on_started(self, origin: str, total: float) -> None:
        self._playing = origin == "timeline"
        self.time_label.setText(f"{self._clock(0)} / {self._clock(total)}")
        self._total = total

    def _on_position(self, origin: str, seconds: float) -> None:
        self.time_label.setText(
            f"{self._clock(seconds)} / {self._clock(getattr(self, '_total', 0.0))}"
        )
        if origin != "timeline":
            return
        self.view.playhead = seconds
        self.view.follow_playhead()

    def _on_stopped(self, origin: str) -> None:
        self.play_btn.setText("▶  Play")
        if origin != "timeline":
            return
        self._playing = False
        # Return the playhead to where playback began, so pressing Play again
        # repeats the same thing instead of starting from the end.
        self.view.playhead = self._play_from
        self.view.follow_playhead()
        self.time_label.setText(
            f"{self._clock(self._play_from)} / {self._clock(getattr(self, '_total', 0.0))}"
        )

    def play_timeline(self) -> None:
        self.play_btn.setText("▶  Playing")
        self.render_timeline(play=True)

    def _clip_action(self, label: str, shortcut: str, slot) -> QAction:
        action = QAction(label, self)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        action.triggered.connect(lambda _=False: slot())
        self.view.addAction(action)
        return action

    def _set_grid(self, value: str) -> None:
        self.view.grid = value
        self.view.viewport().update()

    def add_clip(self) -> None:
        asset = self.c.selected
        if asset is None:
            self.c.status("Select an asset first")
            return
        self.view.place_assets([asset.uid], self.view.snap(self.view.playhead), 1)

    def remove_clip(self) -> None:
        self.view.delete_clips()

    def render_timeline(self, play: bool = True) -> AudioBuffer | None:
        clips = self.view.timeline["clips"]
        if not clips:
            self.c.status("Timeline is empty")
            self.play_btn.setText("▶  Play")
            return None
        sr = self.c.project.settings.sample_rate
        parts = []
        for clip in clips:
            asset = self.c.project.assets.get(clip.get("asset", ""))
            if asset is None:
                continue
            buf = self.c.render(asset, clip.get("overrides") or {}, preview=False)
            if buf is None:
                continue
            parts.append((buf, int(round(float(clip.get("start", 0.0)) * sr))))
        if not parts:
            self.c.status("Nothing renderable on the timeline")
            self.play_btn.setText("▶  Play")
            return None
        mixed = AudioBuffer.mix(parts, sr)
        if mixed.peak() > 1.0:
            self.c.status(f"Timeline mix peaks at {mixed.peak():.2f} — normalizing for preview")
            mixed = mixed.normalized()
        if not play:
            return mixed

        # Start from the playhead rather than always from zero, so clicking the
        # ruler and hitting Play does what it looks like it should.
        start = max(0.0, min(self.view.playhead, max(0.0, mixed.duration - 0.01)))
        self._play_from = start
        segment = mixed.slice_seconds(start, mixed.duration) if start > 0 else mixed
        self.c.play(segment, origin="timeline", offset=start)
        return mixed
