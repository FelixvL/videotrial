"""
CineMarker — ThumbGrid: QListWidget in icoonmodus met uniforme zoom.

- celgrootte uit een ZoomSpec; koppel een ZoomControl met `bind_zoom()`
- Ctrl+scroll zoomt; toepassen is gedebounced (120 ms)
- animatietimer (33 ms, voor crossfades) loopt alleen als het grid zichtbaar is
  én `set_animated(True)` is gezet
- itemdata: DATA_ROLE = dataclass van de kaart, CELL_ROLE = (w, h)
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QListView, QListWidget, QListWidgetItem, QWidget

from ...core.models import ZoomSpec
from .zoom import ZoomControl

DATA_ROLE = Qt.ItemDataRole.UserRole
CELL_ROLE = Qt.ItemDataRole.UserRole + 1

ANIM_INTERVAL_MS = 33


class ThumbGrid(QListWidget):
    cell_size_changed = pyqtSignal(int, int)

    def __init__(self, spec: ZoomSpec, *, spacing: int = 0, horizontal: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._zoom: ZoomControl | None = None
        self._level = 0
        self._cell = spec.size(0)
        self._animated = False
        self._horizontal = horizontal

        self.setProperty("role", "grid")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(not horizontal)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(spacing)
        self.setIconSize(QSize(1, 1))
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        if horizontal:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(120)
        self._apply_timer.timeout.connect(self._apply_cell)

        self._anim = QTimer(self)
        self._anim.setInterval(ANIM_INTERVAL_MS)
        self._anim.timeout.connect(self.viewport().update)

        self._apply_cell(immediate=True)

    # ── zoom ────────────────────────────────────────────────────────────
    def bind_zoom(self, zoom: ZoomControl) -> None:
        self._zoom = zoom
        self._level = zoom.level
        zoom.level_changed.connect(self.set_level)
        self._apply_cell(immediate=True)

    def set_level(self, level: int) -> None:
        self._level = self.spec.clamp(level)
        self._apply_timer.start()

    @property
    def level(self) -> int:
        return self._level

    def cell_size(self) -> tuple[int, int]:
        return self._cell

    def _apply_cell(self, immediate: bool = False) -> None:
        w, h = self.spec.size(self._level)
        # nooit breder dan het zichtbare gebied (kaarten blijven altijd volledig in beeld)
        avail = self.viewport().width() - self.spacing() * 2 - 2
        if avail > 64 and w > avail:
            w = avail
            h = int(round(w * self.spec.aspect))
        self._cell = (w, h)
        self.setGridSize(QSize(w + self.spacing() * 2, h + self.spacing() * 2))
        for i in range(self.count()):
            it = self.item(i)
            it.setSizeHint(QSize(w, h))
            it.setData(CELL_ROLE, (w, h))
        if self._horizontal:
            self.setFixedHeight(h + self.spacing() * 2 + 12)
        self.cell_size_changed.emit(w, h)
        self.doItemsLayout()
        self.viewport().update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        spec_w = self.spec.size(self._level)[0]
        if spec_w != self._cell[0] or spec_w > self.viewport().width() - self.spacing() * 2 - 2:
            self._apply_timer.start()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and self._zoom is not None:
            dy = event.angleDelta().y()
            if dy > 0:
                self._zoom.zoom_in()
            elif dy < 0:
                self._zoom.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    # ── items ───────────────────────────────────────────────────────────
    def add_card(self, data: object, *, tooltip: str = "") -> QListWidgetItem:
        it = QListWidgetItem()
        it.setData(DATA_ROLE, data)
        it.setData(CELL_ROLE, self._cell)
        it.setSizeHint(QSize(*self._cell))
        if tooltip:
            it.setToolTip(tooltip)
        self.addItem(it)
        return it

    def cards(self) -> list[QListWidgetItem]:
        return [self.item(i) for i in range(self.count())]

    def visible_cards(self) -> list[QListWidgetItem]:
        return [self.item(i) for i in range(self.count()) if not self.item(i).isHidden()]

    @staticmethod
    def data_of(item: QListWidgetItem):
        return item.data(DATA_ROLE)

    # ── animatie ────────────────────────────────────────────────────────
    def set_animated(self, on: bool) -> None:
        self._animated = on
        self._sync_anim()

    def _sync_anim(self) -> None:
        if self._animated and self.isVisible():
            if not self._anim.isActive():
                self._anim.start()
        else:
            self._anim.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_anim()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._anim.stop()
