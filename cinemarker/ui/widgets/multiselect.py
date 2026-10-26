"""
CineMarker — MultiSelectDropdown: één multi-select voor alle filterbalken.

- blijft open tot je ernaast klikt (meerdere keuzes zonder heropenen)
- zoekveld zodra er meer dan 6 items zijn
- items kunnen een icoon en een inspringniveau hebben (hiërarchie)
- knoplabel toont "Label" of "Label · n"; kleurfamilie via `tone`
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QApplication, QFrame, QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout, QWidget)

from ..theme import icons, tokens as T
from .search_field import SearchField

_SEARCH_THRESHOLD = 6
_ID_ROLE = Qt.ItemDataRole.UserRole


@dataclass(frozen=True)
class DropdownItem:
    id: object
    label: str
    icon: QIcon | None = None
    depth: int = 0


class _Popup(QFrame):
    def __init__(self, owner: "MultiSelectDropdown") -> None:
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setProperty("role", "panel")
        self._owner = owner
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE_XS, T.SPACE_XS, T.SPACE_XS, T.SPACE_XS)
        lay.setSpacing(T.SPACE_XS)
        self.search = SearchField("Filter…", debounce_ms=0, width=None, compact=True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setIconSize(QSize(T.ICON_SIZE, T.ICON_SIZE))
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.itemClicked.connect(self._toggle)
        lay.addWidget(self.list)

    def _filter(self, text: str) -> None:
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _toggle(self, item: QListWidgetItem) -> None:
        new = (Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
               else Qt.CheckState.Checked)
        item.setCheckState(new)
        self._owner._on_item_changed()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._owner._sync_label()


class MultiSelectDropdown(QPushButton):
    selection_changed = pyqtSignal(set)

    def __init__(self, base_label: str, *, tone: str = "accent", tooltip: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(base_label, parent)
        self.base_label = base_label
        self.setProperty("kind", "chip")
        self.setProperty("tone", tone)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(icons.icon("chevron-down", T.ICON_SIZE_SM))
        self.setIconSize(QSize(T.ICON_SIZE_SM, T.ICON_SIZE_SM))
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)   # icoon rechts
        if tooltip:
            self.setToolTip(tooltip)
        self._items: list[DropdownItem] = []
        self._selected: set = set()
        self._popup = _Popup(self)
        self.clicked.connect(self._toggle_popup)
        self._sync_label()

    # ── API ─────────────────────────────────────────────────────────────
    def set_items(self, items: list[DropdownItem]) -> None:
        self._items = list(items)
        ids = {it.id for it in self._items}
        self._selected &= ids
        self._rebuild_list()
        self._sync_label()

    def selected_ids(self) -> set:
        return set(self._selected)

    def set_selection(self, ids: set, *, emit: bool = False) -> None:
        valid = {it.id for it in self._items}
        self._selected = set(ids) & valid
        self._rebuild_list()
        self._sync_label()
        if emit:
            self.selection_changed.emit(set(self._selected))

    def clear_selection(self, *, emit: bool = False) -> None:
        self.set_selection(set(), emit=emit)

    def is_active(self) -> bool:
        return bool(self._selected)

    def nextCheckState(self) -> None:   # noqa: N802 — checked = "heeft selectie", niet "geklikt"
        pass

    # ── intern ──────────────────────────────────────────────────────────
    def _rebuild_list(self) -> None:
        lst = self._popup.list
        lst.clear()
        for it in self._items:
            indent = "      " * it.depth
            prefix = "↳ " if it.depth else ""
            w = QListWidgetItem(f"{indent}{prefix}{it.label}")
            w.setData(_ID_ROLE, it.id)
            w.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            w.setCheckState(Qt.CheckState.Checked if it.id in self._selected else Qt.CheckState.Unchecked)
            if it.icon is not None:
                w.setIcon(it.icon)
            lst.addItem(w)
        self._popup.search.setVisible(len(self._items) > _SEARCH_THRESHOLD)

    def _on_item_changed(self) -> None:
        lst = self._popup.list
        self._selected = {lst.item(i).data(_ID_ROLE) for i in range(lst.count())
                          if lst.item(i).checkState() == Qt.CheckState.Checked}
        self._sync_label()
        self.selection_changed.emit(set(self._selected))

    def _sync_label(self) -> None:
        n = len(self._selected)
        self.setText(f"{self.base_label} · {n}" if n else self.base_label)
        self.setChecked(n > 0)

    def _toggle_popup(self) -> None:
        if self._popup.isVisible():
            self._popup.hide()
            return
        width = max(220, self.width())
        rows = max(1, min(self._popup.list.count(), 12))
        row_h = self._popup.list.sizeHintForRow(0) if self._popup.list.count() else 24
        list_h = min(300, rows * max(row_h, 22) + 8)
        self._popup.list.setFixedHeight(list_h)
        self._popup.setFixedWidth(width)
        self._popup.adjustSize()
        pos = self.mapToGlobal(QPoint(0, self.height() + 2))
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            if pos.x() + width > geo.right():
                pos.setX(geo.right() - width)
            if pos.y() + self._popup.height() > geo.bottom():
                pos.setY(self.mapToGlobal(QPoint(0, 0)).y() - self._popup.height() - 2)
        self._popup.move(pos)
        self._popup.search.clear()
        self._popup.show()
        if self._popup.search.isVisible():
            self._popup.search.setFocus()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._popup.hide()
        super().changeEvent(event)
