"""
CineMarker — knoppen met één uiterlijk: IconButton, ToolButton, ToggleChip,
SortButton. Varianten en kleurfamilies lopen via dynamic properties die de
stylesheet kent (`variant`, `kind`, `tone`).
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QPushButton, QWidget

from ..theme import icons, tokens as T
from ..theme.stylesheet import repolish


class IconButton(QPushButton):
    """Vierkante knop met alleen een icoon; padding 0 zodat niets wordt afgekapt."""

    def __init__(self, icon_name: str, tooltip: str = "", *, variant: str = "ghost",
                 checkable: bool = False, color: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._color = color
        self.setProperty("kind", "icon")
        self.setProperty("variant", variant)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(T.ICON_SIZE, T.ICON_SIZE))
        self.setFixedSize(T.ICON_BTN, T.ICON_BTN)
        if tooltip:
            self.setToolTip(tooltip)
        self._apply_icon()

    def _apply_icon(self) -> None:
        self.setIcon(icons.icon(self._icon_name, T.ICON_SIZE, self._color))

    def set_icon(self, icon_name: str, color: str | None = None) -> None:
        self._icon_name = icon_name
        self._color = color
        self._apply_icon()

    def set_variant(self, variant: str) -> None:
        self.setProperty("variant", variant)
        repolish(self)

    def set_auto_repeat(self, delay: int = 400, interval: int = 80) -> None:
        self.setAutoRepeat(True)
        self.setAutoRepeatDelay(delay)
        self.setAutoRepeatInterval(interval)


class ToolButton(QPushButton):
    """Tekstknop, optioneel met icoon links."""

    def __init__(self, text: str, icon_name: str | None = None, *, variant: str = "",
                 tooltip: str = "", checkable: bool = False, color: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("variant", variant)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            self.setIcon(icons.icon(icon_name, T.ICON_SIZE_SM + 2, color))
            self.setIconSize(QSize(T.ICON_SIZE_SM + 2, T.ICON_SIZE_SM + 2))
        if tooltip:
            self.setToolTip(tooltip)

    def set_variant(self, variant: str) -> None:
        self.setProperty("variant", variant)
        repolish(self)


class AccentButton(ToolButton):
    def __init__(self, text: str, icon_name: str | None = None, *, tooltip: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, icon_name, variant="accent", tooltip=tooltip,
                         color=T.ACCENT_TEXT_ON, parent=parent)


class ToggleChip(QPushButton):
    """Compacte aan/uit-chip voor filterbalken, met kleurfamilie (`tone`)."""

    def __init__(self, text: str, *, tone: str = "accent", tooltip: str = "",
                 icon_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("kind", "chip")
        self.setProperty("tone", tone)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        if icon_name:
            self.setIcon(icons.icon(icon_name, T.ICON_SIZE_SM))
            self.setIconSize(QSize(T.ICON_SIZE_SM, T.ICON_SIZE_SM))

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)


class SortButton(QPushButton):
    """Sorteerknop met drie standen: uit → oplopend → aflopend.

    `cycle_off=True` laat de derde klik terugvallen op 'uit' (Markers-paneel);
    anders wisselt een klik op de actieve knop alleen de richting.
    """

    state_changed = pyqtSignal(str, str)     # key, state ('', 'asc', 'desc')

    def __init__(self, key: str, label: str, *, cycle_off: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.key = key
        self.label = label
        self.cycle_off = cycle_off
        self._state = ""
        self.setProperty("kind", "sort")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(T.ICON_SIZE_SM, T.ICON_SIZE_SM))
        self.clicked.connect(self._on_click)
        self._render()

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str, emit: bool = False) -> None:
        self._state = state if state in ("asc", "desc") else ""
        self._render()
        if emit:
            self.state_changed.emit(self.key, self._state)

    def _on_click(self) -> None:
        if self._state == "":
            nxt = "asc"
        elif self._state == "asc":
            nxt = "desc"
        else:
            nxt = "" if self.cycle_off else "asc"
        self.set_state(nxt, emit=True)

    def _render(self) -> None:
        self.setChecked(bool(self._state))
        if self._state == "asc":
            self.setIcon(icons.icon("arrow-up", T.ICON_SIZE_SM, T.ACCENT))
        elif self._state == "desc":
            self.setIcon(icons.icon("arrow-down", T.ICON_SIZE_SM, T.ACCENT))
        else:
            self.setIcon(icons.icon("arrow-up-down", T.ICON_SIZE_SM, T.TEXT_DIM))

    def nextCheckState(self) -> None:   # noqa: N802 — checked-staat volgt alleen set_state
        pass


class SortGroup:
    """Houdt een set SortButtons synchroon: maximaal één actief."""

    def __init__(self, cycle_off: bool = False) -> None:
        self.buttons: dict[str, SortButton] = {}
        self.cycle_off = cycle_off
        self._callbacks: list = []

    def add(self, key: str, label: str, parent: QWidget | None = None) -> SortButton:
        btn = SortButton(key, label, cycle_off=self.cycle_off, parent=parent)
        btn.state_changed.connect(self._on_change)
        self.buttons[key] = btn
        return btn

    def on_change(self, callback) -> None:
        """callback(key, state) — state '' betekent: geen sortering."""
        self._callbacks.append(callback)

    def current(self) -> tuple[str, str]:
        for k, b in self.buttons.items():
            if b.state:
                return k, b.state
        return "", ""

    def set(self, key: str, state: str, emit: bool = True) -> None:
        for k, b in self.buttons.items():
            b.set_state(state if k == key else "")
        if emit:
            for cb in self._callbacks:
                cb(key if state else "", state)

    def reset(self, emit: bool = True) -> None:
        self.set("", "", emit)

    def _on_change(self, key: str, state: str) -> None:
        for k, b in self.buttons.items():
            if k != key and b.state:
                b.set_state("")
        for cb in self._callbacks:
            cb(key if state else "", state)
