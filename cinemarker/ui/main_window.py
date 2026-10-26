"""
CineMarker — hoofdvenster: tabbalk, corner-knoppen, statusbalk en de bedrading
tussen panelen (AppSignals) en acties (ActionRegistry → dispatcher).

Panelen worden via `register_panel()` toegevoegd; zolang een paneel nog niet
bestaat, staat er een placeholder. De speler wordt door `set_player()` gezet;
tot die tijd vangt een `PlayerStub` de afspeelverzoeken op (alleen loggen).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PyQt6.QtCore import QSize, QTimer, Qt
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QMainWindow,
                             QStatusBar, QTabWidget, QVBoxLayout, QWidget)

from ..core import settings
from .actions import ActionRegistry
from .signals import AppSignals
from .theme import icons, tokens as T
from .widgets.buttons import IconButton, ToolButton

_log = logging.getLogger("cinemarker.ui")


@dataclass(frozen=True)
class TabDef:
    key: str
    title: str
    icon: str


TABS: tuple[TabDef, ...] = (
    TabDef("player", "Speler", "play"),
    TabDef("films", "Films", "clapperboard"),
    TabDef("markers", "Markers", "bookmark"),
    TabDef("actors", "Acteurs", "users"),
    TabDef("data", "Data", "hard-drive"),
    TabDef("manage", "Beheer", "database"),
    TabDef("sorter", "Sorteren", "images"),
)
DEFAULT_TAB = "actors"


class _Placeholder(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel(f"{title} — in opbouw")
        lbl.setProperty("role", "dim")
        lay.addWidget(lbl)


class PlayerStub:
    """Vangt afspeelverzoeken op zolang de speler nog niet gebouwd is."""

    def load_video(self, path: str, start_time: float | None = None) -> None:
        _log.info("PlayerStub.load_video(%s, %s)", path, start_time)

    def jump_to(self, path: str, time_sec: float) -> None:
        _log.info("PlayerStub.jump_to(%s, %.2f)", path, time_sec)

    def play_selection(self, entries: list, interval_sec: int) -> None:
        _log.info("PlayerStub.play_selection(%d entries, %ds)", len(entries), interval_sec)

    def edit_marker(self, marker: dict, film_path: str) -> None:
        _log.info("PlayerStub.edit_marker(%s)", film_path)

    def handle_action(self, key: str) -> None:
        _log.info("PlayerStub.handle_action(%s)", key)

    def open_file(self) -> None:
        _log.info("PlayerStub.open_file()")

    def toggle_fullscreen(self) -> None:
        _log.info("PlayerStub.toggle_fullscreen()")

    def shutdown(self) -> None:
        pass


class MainWindow(QMainWindow):
    def __init__(self, signals: AppSignals, actions: ActionRegistry) -> None:
        super().__init__()
        self.signals = signals
        self.action_registry = actions
        self.player = PlayerStub()
        self.panels: dict[str, QWidget] = {}
        self._tab_index: dict[str, int] = {}
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(500)
        self._geometry_timer.timeout.connect(self._save_geometry)

        self.setWindowTitle("CineMarker")
        self.setWindowIcon(icons.icon("clapperboard", 32, T.ACCENT))
        self._build_ui()
        self._wire()
        self._restore_geometry()

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(T.ICON_SIZE, T.ICON_SIZE))
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for td in TABS:
            idx = self.tabs.addTab(_Placeholder(td.title), icons.icon(td.icon, T.ICON_SIZE), td.title.upper())
            self._tab_index[td.key] = idx
        self.setCentralWidget(self.tabs)

        corner = QWidget()
        corner.setProperty("role", "tabbar-row")
        cl = QHBoxLayout(corner)
        cl.setContentsMargins(T.SPACE_SM, 0, T.SPACE_MD, 0)
        cl.setSpacing(T.SPACE_XS)
        self.btn_open = ToolButton("Open video", "folder", variant="accent", tooltip="Videobestand openen  (Ctrl+O)",
                                   color=T.ACCENT_TEXT_ON)
        self.btn_open.clicked.connect(lambda: self.player.open_file())
        self.btn_fullscreen = IconButton("maximize", "Volledig scherm  (F11)")
        self.btn_fullscreen.clicked.connect(lambda: self.player.toggle_fullscreen())
        self.btn_settings = IconButton("settings", "Instellingen en sneltoetsen")
        self.btn_settings.clicked.connect(self.open_settings)
        cl.addWidget(self.btn_open)
        cl.addWidget(self.btn_fullscreen)
        cl.addWidget(self.btn_settings)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self._corner = corner

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Open een videobestand om te beginnen")

    def _wire(self) -> None:
        self.action_registry.attach(self)
        self.action_registry.triggered.connect(self.dispatch_action)
        self.signals.status_message.connect(self._on_status)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ── panelen ─────────────────────────────────────────────────────────
    def register_panel(self, key: str, widget: QWidget) -> None:
        idx = self._tab_index[key]
        old = self.tabs.widget(idx)
        td = next(t for t in TABS if t.key == key)
        self.tabs.removeTab(idx)
        self.tabs.insertTab(idx, widget, icons.icon(td.icon, T.ICON_SIZE), td.title.upper())
        old.deleteLater()
        self.panels[key] = widget

    def panel(self, key: str) -> QWidget | None:
        return self.panels.get(key)

    def set_player(self, player) -> None:
        self.player = player

    def current_tab_key(self) -> str:
        idx = self.tabs.currentIndex()
        for k, i in self._tab_index.items():
            if i == idx:
                return k
        return ""

    def show_tab(self, key: str) -> None:
        self.tabs.setCurrentIndex(self._tab_index[key])

    def _on_tab_changed(self, _idx: int) -> None:
        key = self.current_tab_key()
        settings.set_str(settings.LAST_TAB, key)
        for k, w in self.panels.items():
            if hasattr(w, "on_tab_active"):
                w.on_tab_active(k == key)

    # ── acties ──────────────────────────────────────────────────────────
    SORTER_KEYS = {"play_pause": "p", "marker_voor": "p", "marker": "m", "links": "prev", "rechts": "next"}

    def dispatch_action(self, key: str) -> None:
        """Context bepaalt wie de actie krijgt: fullscreen → Sorteren → lijst → speler."""
        if key == "open_bestand":
            self.player.open_file()
            return
        if key == "fullscreen":
            self.player.toggle_fullscreen()
            return
        tab = self.current_tab_key()
        sorter = self.panels.get("sorter")
        if tab == "sorter" and sorter is not None:
            if key in self.SORTER_KEYS:
                sorter.handle(self.SORTER_KEYS[key])
                return
            if key in ("skip_voor", "skip_achter", "marker_achter"):
                return
        if key in ("links", "rechts"):
            fw = QApplication.focusWidget()
            if isinstance(fw, QListWidget) and fw.count():
                row = fw.currentRow()
                fw.setCurrentRow(max(0, row - 1) if key == "links" else min(fw.count() - 1, row + 1))
                return
        self.player.handle_action(key)

    def open_settings(self) -> None:
        from .panels.settings_dialog import SettingsDialog   # lazy: vermijdt importcirkel
        dlg = SettingsDialog(self, self.action_registry, self.signals)
        dlg.exec()

    # ── status / venster ────────────────────────────────────────────────
    def _on_status(self, text: str, msec: int) -> None:
        self.status.showMessage(text, msec)

    def say(self, text: str, msec: int = 4000) -> None:
        self.status.showMessage(text, msec)

    def _restore_geometry(self) -> None:
        geo = settings.get_window_geometry()
        if geo == "max":
            self.resize(1400, 900)
            self.showMaximized()
        elif isinstance(geo, tuple):
            x, y, w, h = geo
            self.setGeometry(x, y, max(w, 900), max(h, 600))
        else:
            self.resize(1400, 900)
        last = settings.get_str(settings.LAST_TAB, DEFAULT_TAB)
        self.tabs.setCurrentIndex(self._tab_index.get(last, self._tab_index[DEFAULT_TAB]))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._geometry_timer.start()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._geometry_timer.start()

    def _save_geometry(self) -> None:
        if self.isFullScreen():
            return
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        settings.set_window_geometry(self.isMaximized(), g.x(), g.y(), g.width(), g.height())

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_geometry()
        for w in self.panels.values():
            if hasattr(w, "shutdown"):
                w.shutdown()
        self.player.shutdown()
        super().closeEvent(event)
