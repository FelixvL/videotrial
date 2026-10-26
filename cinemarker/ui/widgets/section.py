"""CineMarker — SectionHeader: kop met titel, teller en actieknoppen rechts."""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme import tokens as T


class SectionHeader(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(T.BUTTON_H + T.SPACE_XS)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(T.SPACE_SM)
        self.title_label = QLabel(title.upper())
        self.title_label.setProperty("role", "section")
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "dim")
        self._lay.addWidget(self.title_label)
        self._lay.addWidget(self.count_label)
        self._lay.addStretch(1)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title.upper())

    def set_count(self, text: str) -> None:
        self.count_label.setText(text)

    def add_widget(self, w: QWidget) -> QWidget:
        self._lay.addWidget(w)
        return w
