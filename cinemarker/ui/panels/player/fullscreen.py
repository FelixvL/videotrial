"""
CineMarker — fullscreen in hetzelfde venster: chrome verbergen, overlays weg,
showFullScreen(); één ✕ rechtsboven. Alle acties blijven werken (zelfde venster).
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMainWindow, QWidget

from ...theme import tokens as T
from ...widgets.buttons import IconButton
from PyQt6.QtGui import QColor

from ...widgets.overlay import Anchor, FloatingPanel


class _CloseButton(FloatingPanel):
    clicked = pyqtSignal()

    def __init__(self, video: QWidget) -> None:
        super().__init__(video, anchor=Anchor.TOP_RIGHT, mode="tool_window", margin=T.SPACE_MD,
                         bg=QColor(18, 20, 24, 150), radius=20)
        self.setFixedSize(40, 40)
        btn = IconButton("x", "Volledig scherm sluiten (Esc / F11)", parent=self)
        btn.setFixedSize(40, 40)
        btn.clicked.connect(self.clicked)


class FullscreenMode:
    def __init__(self, window: QMainWindow, chrome: list[QWidget], overlays: list[QWidget], video: QWidget) -> None:
        self._win = window
        self._chrome = chrome
        self._overlays = overlays
        self.active = False
        self._was_maximized = False
        self._restore_visible: list[QWidget] = []
        self.close_btn = _CloseButton(video)

    def enter(self) -> None:
        if self.active:
            return
        self.active = True
        self._was_maximized = self._win.isMaximized()
        self._restore_visible = [w for w in self._overlays if w.isVisible()]
        for w in self._overlays:
            w.hide()
        for w in self._chrome:
            w.hide()
        self._win.showFullScreen()
        self.close_btn.show_over_video()

    def exit(self) -> None:
        if not self.active:
            return
        self.active = False
        self.close_btn.hide()
        for w in self._chrome:
            w.show()
        if self._was_maximized:
            self._win.showMaximized()
        else:
            self._win.showNormal()
        for w in self._restore_visible:
            w.show()
            w.raise_()
        self._restore_visible = []

    def toggle(self) -> None:
        self.exit() if self.active else self.enter()
