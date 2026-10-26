"""CineMarker — zoekveld met zoekicoon, wisknop en debounce."""

from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QLineEdit, QWidget

from ..theme import icons, tokens as T


class SearchField(QLineEdit):
    search_changed = pyqtSignal(str)     # na debounce

    def __init__(self, placeholder: str = "Zoeken…", *, debounce_ms: int = 150,
                 width: int | None = 200, compact: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icons.icon("search", T.ICON_SIZE_SM), QLineEdit.ActionPosition.LeadingPosition)
        if compact:
            self.setProperty("role", "compact")
        if width:
            self.setFixedWidth(width)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(lambda: self.search_changed.emit(self.text()))
        self.textChanged.connect(lambda _t: self._timer.start())
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def clear_silently(self) -> None:
        self.blockSignals(True)
        self.clear()
        self.blockSignals(False)
        self._timer.stop()
