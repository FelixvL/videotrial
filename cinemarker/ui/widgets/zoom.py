"""
CineMarker — ZoomControl: één zoomwidget voor alle grids.

Uiterlijk: [−] [n/N] [+], altijd rechts in de werkbalk. Dubbelklik op het
nummer = reset naar het basisniveau. Het niveau wordt onder de bestaande
setting-sleutel opgeslagen (ZoomSpec), geklemd op de toegestane range.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ...core import settings
from ...core.models import ZoomSpec
from ..theme import tokens as T
from .buttons import IconButton


class _LevelLabel(QLabel):
    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class ZoomControl(QWidget):
    level_changed = pyqtSignal(int)

    def __init__(self, spec: ZoomSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._level = spec.clamp(settings.get_int(spec.setting_key, 0))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._btn_out = IconButton("zoom-out", "Kleiner (Ctrl+scroll)")
        self._btn_out.set_auto_repeat()
        self._btn_out.clicked.connect(self.zoom_out)

        self._lbl = _LevelLabel()
        self._lbl.setProperty("role", "muted")
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl.setFixedWidth(40)
        self._lbl.setToolTip("Dubbelklik = standaardgrootte")
        self._lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl.double_clicked.connect(self.reset)

        self._btn_in = IconButton("zoom-in", "Groter (Ctrl+scroll)")
        self._btn_in.set_auto_repeat()
        self._btn_in.clicked.connect(self.zoom_in)

        lay.addWidget(self._btn_out)
        lay.addWidget(self._lbl)
        lay.addWidget(self._btn_in)
        self._render()

    # ── API ─────────────────────────────────────────────────────────────
    @property
    def level(self) -> int:
        return self._level

    def cell_size(self) -> tuple[int, int]:
        return self.spec.size(self._level)

    def set_level(self, level: int, *, emit: bool = True) -> None:
        level = self.spec.clamp(level)
        if level == self._level:
            self._render()
            return
        self._level = level
        settings.set_int(self.spec.setting_key, level)
        self._render()
        if emit:
            self.level_changed.emit(level)

    def zoom_in(self) -> None:
        self.set_level(self._level + 1)

    def zoom_out(self) -> None:
        self.set_level(self._level - 1)

    def reset(self) -> None:
        self.set_level(0)

    # ── intern ──────────────────────────────────────────────────────────
    def _render(self) -> None:
        self._lbl.setText(f"{self.spec.display_index(self._level)}/{self.spec.level_count}")
        self._btn_out.setEnabled(self._level > self.spec.min_level)
        self._btn_in.setEnabled(self._level < self.spec.max_level)
