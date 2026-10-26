"""CineMarker — Ctrl+L: acteur zoeken en aan de huidige film koppelen (paneel rechtsboven)."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ....core import database as db
from ....core.models import ActorMeta
from ...theme import tokens as T
from ...widgets.buttons import IconButton
from ...widgets.overlay import GLASS_DENSE, Anchor, FloatingPanel
from ...widgets.search_field import SearchField

_ROLE = Qt.ItemDataRole.UserRole


class ActorLinkPanel(FloatingPanel):
    link_requested = pyqtSignal(dict)

    def __init__(self, video: QWidget) -> None:
        from .marker_list import PANEL_W as SIDE_W
        super().__init__(video, anchor=Anchor.TOP_RIGHT, mode="tool_window", margin=T.SPACE_SM,
                         offset=QPoint(-(SIDE_W + T.SPACE_SM), 0), bg=GLASS_DENSE)
        self.setFixedSize(280, 340)
        v = QVBoxLayout(self)
        v.setContentsMargins(T.SPACE_SM, T.SPACE_SM, T.SPACE_SM, T.SPACE_SM)
        v.setSpacing(T.SPACE_XS)
        head = QHBoxLayout()
        title = QLabel("ACTEUR KOPPELEN")
        title.setProperty("role", "section")
        head.addWidget(title)
        head.addStretch(1)
        btn_x = IconButton("x", "Sluiten (Esc)")
        btn_x.setFixedSize(26, 26)
        btn_x.clicked.connect(self.hide)
        head.addWidget(btn_x)
        v.addLayout(head)
        self.search = SearchField("Naam zoeken…", debounce_ms=0, width=None, compact=True)
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)
        self.list = QListWidget()
        self.list.setProperty("role", "glass")
        self.list.itemClicked.connect(self._on_click)
        v.addWidget(self.list, 1)
        self._actors: list[dict] = []

    def open(self) -> None:
        self._actors = db.get_all_actors()
        self.list.clear()
        for a in sorted(self._actors, key=lambda x: (ActorMeta.from_notes(_notes(x)).display_name or x["name"]).lower()):
            it = QListWidgetItem(ActorMeta.from_notes(_notes(a)).display_name or a["name"])
            it.setData(_ROLE, a)
            self.list.addItem(it)
        self.search.clear()
        self.show_over_video()
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _on_click(self, item: QListWidgetItem) -> None:
        self.link_requested.emit(item.data(_ROLE))
        self.hide()


def _notes(actor: dict) -> dict:
    import json
    try:
        return json.loads(actor.get("notes") or "{}") or {}
    except (TypeError, ValueError):
        return {}
