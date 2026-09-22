"""Drag-and-drop payload shared between the asset manager and the timeline."""

from __future__ import annotations

from PyQt6.QtCore import QMimeData

ASSET_MIME = "application/x-raw-asset-uids"


def asset_mime_data(uids: list[str], labels: list[str] | None = None) -> QMimeData:
    md = QMimeData()
    md.setData(ASSET_MIME, ",".join(uids).encode("utf-8"))
    md.setText(", ".join(labels or uids))
    return md


def asset_uids_from_mime(mime: QMimeData) -> list[str]:
    if not mime.hasFormat(ASSET_MIME):
        return []
    raw = bytes(mime.data(ASSET_MIME)).decode("utf-8", "replace")
    return [u for u in raw.split(",") if u]
