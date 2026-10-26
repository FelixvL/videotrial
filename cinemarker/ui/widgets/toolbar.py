"""
CineMarker — PanelToolbar en FilterBar: dezelfde opbouw in elk paneel.

PanelToolbar (44px):  TITEL  teller | zoekveld | … | stretch | acties | zoom
FilterBar:            chips/dropdowns die doorlopen naar een volgende regel.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ..theme import tokens as T
from .flow_layout import FlowLayout


def vseparator(parent: QWidget | None = None) -> QFrame:
    f = QFrame(parent)
    f.setProperty("role", "vseparator")
    f.setFixedSize(1, 22)
    return f


def hseparator(parent: QWidget | None = None) -> QFrame:
    f = QFrame(parent)
    f.setProperty("role", "separator")
    f.setFixedHeight(1)
    return f


class PanelToolbar(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "toolbar")
        self.setFixedHeight(T.TOOLBAR_H)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(T.SPACE_MD, 0, T.SPACE_MD, 0)
        self._lay.setSpacing(T.SPACE_SM)

        self.title_label = QLabel(title.upper())
        self.title_label.setProperty("role", "title")
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "muted")
        self._lay.addWidget(self.title_label)
        self._lay.addWidget(self.count_label)
        self._lay.addSpacing(T.SPACE_SM)

    def set_count(self, text: str) -> None:
        self.count_label.setText(text)

    def add_widget(self, w: QWidget, stretch: int = 0) -> QWidget:
        self._lay.addWidget(w, stretch)
        return w

    def add_widgets(self, *widgets: QWidget) -> None:
        for w in widgets:
            self._lay.addWidget(w)

    def add_separator(self) -> None:
        self._lay.addWidget(vseparator(self))

    def add_spacing(self, px: int = T.SPACE_SM) -> None:
        self._lay.addSpacing(px)

    def add_stretch(self) -> None:
        self._lay.addStretch(1)


class FilterBar(QFrame):
    """Tweede rij onder de werkbalk; wrapt automatisch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "filterbar")
        self._flow = FlowLayout(self, margin=0, h_spacing=T.SPACE_SM, v_spacing=T.SPACE_XS)
        self._flow.setContentsMargins(T.SPACE_MD, T.SPACE_XS + 2, T.SPACE_MD, T.SPACE_XS + 2)
        self.setMinimumHeight(T.FILTERBAR_MIN_H)

    def add_widget(self, w: QWidget) -> QWidget:
        self._flow.addWidget(w)
        return w

    def add_widgets(self, *widgets: QWidget) -> None:
        for w in widgets:
            self._flow.addWidget(w)

    def add_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("role", "dim")
        lbl.setContentsMargins(T.SPACE_XS, 0, 0, 0)
        self._flow.addWidget(lbl)
        return lbl

    def remove_widget(self, w: QWidget) -> None:
        self._flow.removeWidget(w)
        w.setParent(None)
        w.deleteLater()
        self._flow.invalidate()

    def clear(self) -> None:
        self._flow.clear()
