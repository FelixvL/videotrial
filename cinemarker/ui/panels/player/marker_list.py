"""
CineMarker — rechterpaneel over de video: markerlijst van de huidige film
(of de afspeellijst) en de zoekresultaten voor acteurs.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea, QStackedWidget,
                             QVBoxLayout, QWidget)

from ....core import database as db
from ....core.formats import fmt_compact
from ....core.models import ActorMeta, Marker, MarkerEntry
from ....core.paths import SCALED_ACTOR_GRID_DIR
from ...delegates.cat_icons import CategoryIcons
from ...delegates.pixcache import PixmapCache
from ...theme import icons, tokens as T
from ...widgets.buttons import IconButton, ToolButton
from ...widgets.overlay import GLASS, Anchor, FloatingPanel

PANEL_W = 200
ROW_H = 36
PHOTO = 26
CAT = 22
_ROLE = Qt.ItemDataRole.UserRole


class _ActorCard(QWidget):
    clicked = pyqtSignal(dict)
    W, H = 150, 182

    def __init__(self, actor: dict, photo: str, photos: PixmapCache) -> None:
        super().__init__()
        self.actor = actor
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pm = photos.get(photo, self.W - 8, self.H - 30) if photo else None
        self._name = ActorMeta.from_notes(_notes(actor)).display_name or actor.get("name", "")
        self._hover = False
        self.setToolTip(f"Koppel {self._name} aan deze film")

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.actor)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(42, 47, 54, 230) if self._hover else QColor(34, 38, 44, 190))
        p.drawRoundedRect(self.rect(), T.RADIUS, T.RADIUS)
        if self._pm is not None:
            p.drawPixmap(4, 4, self._pm)
        else:
            p.drawPixmap(self.W // 2 - 16, self.H // 2 - 28, icons.pixmap("user", 32, T.TEXT_DIM))
        p.setPen(QColor(T.TEXT if self._hover else T.TEXT_MUTED))
        fm = p.fontMetrics()
        p.drawText(4, self.H - 24, self.W - 8, 20, Qt.AlignmentFlag.AlignCenter,
                   fm.elidedText(self._name, Qt.TextElideMode.ElideRight, self.W - 8))
        p.end()


def _notes(actor: dict) -> dict:
    import json
    try:
        return json.loads(actor.get("notes") or "{}") or {}
    except (TypeError, ValueError):
        return {}


class MarkerSidePanel(FloatingPanel):
    jump_requested = pyqtSignal(int)          # rij
    edit_requested = pyqtSignal(int)          # index in markers van de huidige film
    delete_requested = pyqtSignal(int)
    export_requested = pyqtSignal()
    actor_clicked = pyqtSignal(dict)

    def __init__(self, video: QWidget, cat_icons: CategoryIcons) -> None:
        super().__init__(video, anchor=Anchor.RIGHT_FULL, mode="tool_window", margin=0, bg=GLASS, radius=0)
        self.cat_icons = cat_icons
        self.photos = PixmapCache(SCALED_ACTOR_GRID_DIR)
        self.cards_cache = PixmapCache(None, write_disk=False)
        self._actor_photo: dict[int, str] = {}
        self.setFixedWidth(PANEL_W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.setProperty("role", "transparent")
        lay.addWidget(self.stack)

        page = QWidget()
        page.setProperty("role", "transparent")
        pv = QVBoxLayout(page)
        pv.setContentsMargins(T.SPACE_XS, T.SPACE_XS, T.SPACE_XS, T.SPACE_XS)
        pv.setSpacing(T.SPACE_XS)
        head = QHBoxLayout()
        self.lbl_title = QLabel("MARKERS")
        self.lbl_title.setProperty("role", "section")
        self.lbl_title.setStyleSheet("background: rgba(18,20,24,170); border-radius: 4px; padding: 2px 6px;")
        head.addWidget(self.lbl_title)
        head.addStretch(1)
        self.btn_csv = IconButton("download", "Markers exporteren als CSV")
        self.btn_csv.clicked.connect(self.export_requested)
        head.addWidget(self.btn_csv)
        pv.addLayout(head)
        self.list = QListWidget()
        self.list.setProperty("role", "glass")
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemDoubleClicked.connect(lambda it: self.jump_requested.emit(self.list.row(it)))
        pv.addWidget(self.list, 1)
        self.stack.addWidget(page)

        search = QWidget()
        search.setProperty("role", "transparent")
        sv = QVBoxLayout(search)
        sv.setContentsMargins(T.SPACE_XS, T.SPACE_XS, T.SPACE_XS, T.SPACE_XS)
        sv.setSpacing(T.SPACE_XS)
        lbl = QLabel("ACTEUR KOPPELEN")
        lbl.setProperty("role", "section")
        lbl.setStyleSheet("background: rgba(18,20,24,170); border-radius: 4px; padding: 2px 6px;")
        sv.addWidget(lbl)
        self.scroll = QScrollArea()
        self.scroll.setProperty("role", "transparent")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._results = QWidget()
        self._results.setProperty("role", "transparent")
        self.scroll.setWidget(self._results)
        sv.addWidget(self.scroll, 1)
        self.stack.addWidget(search)

    # ── acteurfoto's ────────────────────────────────────────────────────
    def reload_actor_photos(self) -> None:
        self._actor_photo = {}
        for a in db.get_all_actors():
            for p in db.get_actor_photos(a["id"]):
                if os.path.exists(p["photo_path"]):
                    self._actor_photo[a["id"]] = p["photo_path"]
                    break

    def _img(self, pm: QPixmap | None, size: int, fallback: str) -> QLabel:
        lbl = QLabel()
        lbl.setFixedSize(size, size)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if pm is not None:
            lbl.setPixmap(pm)
        else:
            lbl.setPixmap(icons.pixmap(fallback, size - 8, T.TEXT_DIM))
            lbl.setStyleSheet(f"background:{T.SURFACE2}; border-radius:3px;")
        return lbl

    # ── pagina's ────────────────────────────────────────────────────────
    def show_search(self, on: bool) -> None:
        self.stack.setCurrentIndex(1 if on else 0)

    def set_results(self, actors: list[dict]) -> None:
        self._results = QWidget()
        self._results.setProperty("role", "transparent")
        v = QVBoxLayout(self._results)
        v.setContentsMargins(T.SPACE_XS, T.SPACE_XS, T.SPACE_XS, T.SPACE_XS)
        v.setSpacing(T.SPACE_XS)
        v.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        for a in actors[:24]:
            card = _ActorCard(a, self._actor_photo.get(a["id"], ""), self.cards_cache)
            card.clicked.connect(self.actor_clicked)
            v.addWidget(card)
        self.scroll.setWidget(self._results)

    def set_markers(self, markers: list[Marker]) -> None:
        """Normale modus: markers van de huidige film."""
        self.lbl_title.setText("MARKERS")
        self.btn_csv.setVisible(True)
        self.list.clear()
        for idx, m in enumerate(markers):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ROW_H))
            self.list.addItem(item)
            row = QWidget()
            row.setProperty("role", "transparent")
            h = QHBoxLayout(row)
            h.setContentsMargins(T.SPACE_XS, 0, T.SPACE_XS, 0)
            h.setSpacing(3)
            if m.negative:
                row.setStyleSheet(f"background: rgba(58, 26, 24, 170); border-radius:{T.RADIUS_SM}px;")
                ic = QLabel()
                ic.setFixedSize(PHOTO, PHOTO)
                ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
                ic.setPixmap(icons.pixmap("ban", 18, T.RED))
                h.addWidget(ic)
                t = QLabel(fmt_compact(m.time) or "0:00")
                t.setProperty("role", "timecode")
                t.setStyleSheet(f"color:{T.RED};")
                h.addWidget(t)
                skip = QLabel("SKIP")
                skip.setProperty("role", "dim")
                h.addWidget(skip)
                h.addStretch(1)
                btn = IconButton("x", "Negatieve marker verwijderen", variant="ghost", color=T.RED)
                btn.setFixedSize(26, 26)
                btn.clicked.connect(lambda _c, i=idx: self.delete_requested.emit(i))
                h.addWidget(btn)
            else:
                for aid in m.actors[:4]:
                    h.addWidget(self._img(self.photos.get(self._actor_photo.get(aid, ""), PHOTO, PHOTO), PHOTO, "user"))
                for cid in m.categories[:3]:
                    h.addWidget(self._img(self.cat_icons.pixmap(cid, CAT), CAT, "tag"))
                t = QLabel(fmt_compact(m.time) or "0:00")
                t.setProperty("role", "timecode")
                t.setStyleSheet(f"color:{T.TEXT_MUTED};")
                h.addWidget(t)
                h.addStretch(1)
                btn = IconButton("pencil", "Categorie, acteurs of score aanpassen")
                btn.setFixedSize(26, 26)
                btn.clicked.connect(lambda _c, i=idx: self.edit_requested.emit(i))
                h.addWidget(btn)
            self.list.setItemWidget(item, row)

    def set_selection(self, entries: list[MarkerEntry], current: int) -> None:
        """Afspeellijst-modus: markers uit meerdere films."""
        self.lbl_title.setText("AFSPEELLIJST")
        self.btn_csv.setVisible(False)
        self.list.clear()
        prev = None
        for e in entries:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ROW_H))
            self.list.addItem(item)
            row = QWidget()
            row.setProperty("role", "transparent")
            if prev is not None and e.film_path != prev:
                row.setStyleSheet("border-top: 1px solid rgba(255,255,255,40); background: transparent;")
            prev = e.film_path
            h = QHBoxLayout(row)
            h.setContentsMargins(T.SPACE_XS, 0, T.SPACE_XS, 0)
            h.setSpacing(3)
            for aid in e.marker.actors[:3]:
                h.addWidget(self._img(self.photos.get(self._actor_photo.get(aid, ""), PHOTO, PHOTO), PHOTO, "user"))
            for cid in e.marker.categories[:2]:
                h.addWidget(self._img(self.cat_icons.pixmap(cid, CAT), CAT, "tag"))
            t = QLabel(fmt_compact(e.time) or "0:00")
            t.setProperty("role", "timecode")
            t.setStyleSheet(f"color:{T.TEXT_MUTED};")
            h.addWidget(t)
            h.addStretch(1)
            f = QLabel(e.film_name[:16])
            f.setProperty("role", "dim")
            f.setToolTip(e.film_name)
            h.addWidget(f)
            self.list.setItemWidget(item, row)
        self.set_current(current)

    def set_current(self, row: int) -> None:
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.item(row))

    def current_row(self) -> int:
        return self.list.currentRow()

    def count(self) -> int:
        return self.list.count()
