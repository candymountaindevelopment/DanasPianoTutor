"""Asset Manager dock — the central browser for every project object."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.assets import UnknownAsset
from .dnd import asset_mime_data
from .theme import COLORS


class AssetTree(QTreeWidget):
    """Tree that can be dragged onto the timeline.

    The payload is a list of asset UUIDs, never names or file paths, so a drop
    survives a later rename (spec section 11).
    """

    def mimeTypes(self):
        from .dnd import ASSET_MIME

        return [ASSET_MIME, "text/plain"]

    def mimeData(self, items):
        uids, labels = [], []
        for item in items:
            uid = item.data(0, Qt.ItemDataRole.UserRole)
            if uid:
                uids.append(uid)
                labels.append(item.text(0))
        return asset_mime_data(uids, labels)

TYPE_ORDER = ["SynthAsset", "AudioAsset", "InstrumentAsset", "PatternAsset"]
TYPE_LABELS = {
    "SynthAsset": "SFX / SYNTH",
    "AudioAsset": "SAMPLES",
    "InstrumentAsset": "INSTRUMENTS",
    "PatternAsset": "PATTERNS",
}


class AssetManager(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self._building = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        filters = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search name or tag…")
        self.search.textChanged.connect(self.rebuild)
        self.type_filter = QComboBox()
        self.type_filter.addItem("All types", "")
        for t in TYPE_ORDER:
            self.type_filter.addItem(TYPE_LABELS[t].title(), t)
        self.type_filter.currentIndexChanged.connect(self.rebuild)
        filters.addWidget(self.search, 3)
        filters.addWidget(self.type_filter, 2)
        root.addLayout(filters)

        self.tree = AssetTree()
        self.tree.setHeaderLabels(["Asset", "Detail"])
        self.tree.setColumnWidth(0, 170)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.tree.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.c.preview())
        root.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.btn_preview = QPushButton("Preview")
        self.btn_preview.setObjectName("primary")
        self.btn_preview.clicked.connect(lambda: self.c.preview())
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.c.stop)
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self._delete)
        for b in (self.btn_preview, self.btn_stop, self.btn_delete):
            buttons.addWidget(b)
        root.addLayout(buttons)

        self.c.assetsChanged.connect(self.rebuild)
        self.c.assetModified.connect(lambda _: self.rebuild())
        self.c.projectChanged.connect(self.rebuild)
        self.c.selectionChanged.connect(self._sync_selection)
        self.rebuild()

    # ------------------------------------------------------------------ view

    def rebuild(self) -> None:
        self._building = True
        self.tree.clear()
        query = self.search.text().strip().lower()
        wanted = self.type_filter.currentData()

        groups: dict[str, QTreeWidgetItem] = {}
        for asset in self.c.project.assets.sorted():
            tname = asset.type_name
            if wanted and tname != wanted:
                continue
            if query and query not in asset.name.lower() and not any(
                query in t.lower() for t in asset.tags
            ):
                continue
            if tname not in groups:
                header = QTreeWidgetItem([TYPE_LABELS.get(tname, tname.upper()), ""])
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)
                header.setForeground(0, QColor(COLORS["text_dim"]))
                groups[tname] = header
                self.tree.addTopLevelItem(header)
                header.setExpanded(True)
            item = QTreeWidgetItem([asset.name, asset.summary()])
            item.setData(0, Qt.ItemDataRole.UserRole, asset.uid)
            item.setForeground(1, QColor(COLORS["text_dim"]))
            if isinstance(asset, UnknownAsset) or getattr(asset, "missing", False):
                item.setForeground(0, QColor(COLORS["warn"]))
            if asset.tags:
                item.setToolTip(0, "tags: " + ", ".join(asset.tags))
            groups[tname].addChild(item)

        self._building = False
        self._sync_selection(self.c.selected_uid)

    def _sync_selection(self, uid: str) -> None:
        if self._building:
            return
        self._building = True
        it = self.tree.topLevelItemCount()
        for i in range(it):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == uid:
                    self.tree.setCurrentItem(child)
                    self._building = False
                    return
        self.tree.clearSelection()
        self._building = False

    def selected_uids(self) -> list[str]:
        return [
            uid
            for item in self.tree.selectedItems()
            if (uid := item.data(0, Qt.ItemDataRole.UserRole))
        ]

    def _selection_changed(self) -> None:
        if self._building:
            return
        uids = self.selected_uids()
        self.c.select(uids[0] if uids else "")

    # --------------------------------------------------------------- actions

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None or item.data(0, Qt.ItemDataRole.UserRole) is None:
            return
        uid = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        actions = [
            ("Preview", lambda: self.c.preview()),
            ("Rename…", lambda: self._rename(uid)),
            ("Duplicate", lambda: self.c.duplicate_asset(uid)),
            ("Edit Tags…", lambda: self._tags(uid)),
            (None, None),
            ("Delete", lambda: self._delete()),
        ]
        for label, fn in actions:
            if label is None:
                menu.addSeparator()
                continue
            act = QAction(label, self)
            act.triggered.connect(fn)
            menu.addAction(act)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename(self, uid: str) -> None:
        asset = self.c.project.assets.get(uid)
        if asset is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Asset", "Variable name:", text=asset.name)
        if ok and name.strip():
            self.c.rename_asset(uid, name.strip())

    def _tags(self, uid: str) -> None:
        from ..core.commands import SetAssetField

        asset = self.c.project.assets.get(uid)
        if asset is None:
            return
        text, ok = QInputDialog.getText(
            self, "Edit Tags", "Comma-separated tags:", text=", ".join(asset.tags)
        )
        if ok:
            tags = [t.strip() for t in text.split(",") if t.strip()]
            self.c.modify_asset(uid, SetAssetField(uid, "tags", tags, "Edit tags"))
            self.rebuild()

    def _delete(self) -> None:
        uids = self.selected_uids() or ([self.c.selected_uid] if self.c.selected_uid else [])
        names = [a.name for u in uids if (a := self.c.project.assets.get(u))]
        if not names:
            return
        listing = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
        ok = QMessageBox.question(
            self,
            "Delete Asset" if len(names) == 1 else "Delete Assets",
            f"Delete {listing}?\n\nThis can be undone with Ctrl+Z.",
        )
        if ok == QMessageBox.StandardButton.Yes:
            self.c.delete_assets(uids)
