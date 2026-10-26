"""CineMarker — tijdlijn onder de video: voortgang, negatieve zones, markerstreepjes."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QSlider, QWidget

from ...theme import tokens as T

RANGE = 10000


class TimelineSlider(QSlider):
    scrubbed = pyqtSignal(float)      # tijdens slepen: fractie 0–1 (keyframe-seek)
    released = pyqtSignal(float)      # bij loslaten: fractie (exacte seek)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, RANGE)
        self.setFixedHeight(10)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("")
        self._neg_zones: list[tuple[float, float]] = []
        self._marker_fracs: list[float] = []
        self._hover_x: int | None = None
        self.setMouseTracking(True)

    def set_neg_zones(self, zones: list[tuple[float, float]]) -> None:
        self._neg_zones = zones
        self.update()

    def set_marker_fracs(self, fracs: list[float]) -> None:
        self._marker_fracs = fracs
        self.update()

    def _frac(self, x: float) -> float:
        return max(0.0, min(1.0, x / max(1, self.width())))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            f = self._frac(event.position().x())
            self.setValue(int(f * RANGE))
            self.scrubbed.emit(f)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._hover_x = int(event.position().x())
        if event.buttons() & Qt.MouseButton.LeftButton:
            f = self._frac(event.position().x())
            self.setValue(int(f * RANGE))
            self.scrubbed.emit(f)
            event.accept()
            return
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_x = None
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.released.emit(self._frac(event.position().x()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(self)
        played = int(self.value() / RANGE * w) if RANGE else 0
        p.fillRect(0, 0, w, h, QColor(T.SURFACE2))
        if played > 0:
            p.fillRect(0, 0, played, h, QColor(T.ACCENT))
        for s, e in self._neg_zones:
            x0, x1 = int(s * w), int(e * w)
            if x1 > x0:
                p.fillRect(x0, 0, max(3, x1 - x0), h, QColor(T.RED))
        if played > 0:
            for s, e in self._neg_zones:
                x0, x1 = int(s * w), min(int(e * w), played)
                if x1 > x0:
                    p.fillRect(x0, 0, x1 - x0, h, QColor(T.ORANGE))
        for f in self._marker_fracs:
            x = int(f * w)
            p.fillRect(max(0, x - 1), 0, 2, h, QColor(T.BLUE))
        if self._hover_x is not None:
            p.fillRect(self._hover_x, 0, 1, h, QColor(T.TEXT))
        p.end()
