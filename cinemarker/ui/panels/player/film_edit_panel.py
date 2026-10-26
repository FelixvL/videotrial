"""
CineMarker — film-edit-paneel boven de acteurs-overlay: acteurs ontkoppelen,
thumbnails verwijderen, filmcategorieën aan/uit, acteurs uit markers halen.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ....core import database as db, markers_store
from ....core.formats import fmt_hms
from ....core.markers_io import load_markers
from ....core.models import ActorMeta
from ....core.paths import SCALED_ACTOR_GRID_DIR
from ...delegates.cat_icons import CategoryIcons
from ...delegates.pixcache import PixmapCache
from ...theme import icons, tokens as T
from ...widgets.buttons import IconButton, ToggleChip
from ...widgets.dialogs import confirm
from ...widgets.flow_layout import FlowLayout
from ...widgets.overlay import GLASS_DENSE, Anchor, FloatingPanel
from ...widgets.toolbar import hseparator
from .actors_overlay import TOTAL_H

PANEL_W = 300
MAX_H = 520


def _display(actor: dict) -> str:
    import json
    try:
        notes = json.loads(actor.get("notes") or "{}") or {}
    except (TypeError, ValueError):
        notes = {}
    return ActorMeta.from_notes(notes).display_name or actor.get("name", "")


class FilmEditPanel(FloatingPanel):
    actors_changed = pyqtSignal(int)        # film_id
    thumbnails_changed = pyqtSignal(int)
    markers_changed = pyqtSignal(str)       # film_path
    film_cats_changed = pyqtSignal(int)

    def __init__(self, video: QWidget, cat_icons: CategoryIcons) -> None:
        super().__init__(video, anchor=Anchor.BOTTOM_LEFT, mode="tool_window", margin=T.SPACE_SM,
                         offset=QPoint(0, -(TOTAL_H + T.SPACE_SM)), bg=GLASS_DENSE)
        self.cat_icons = cat_icons
        self.photos = PixmapCache(SCALED_ACTOR_GRID_DIR)
        self.thumbs = PixmapCache(None, write_disk=False)
        self._film_id: int | None = None
        self.setFixedWidth(PANEL_W)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.SPACE_SM, T.SPACE_SM, T.SPACE_SM, T.SPACE_SM)
        outer.setSpacing(T.SPACE_XS)
        head = QHBoxLayout()
        title = QLabel("FILM BEWERKEN")
        title.setProperty("role", "pill-section")
        head.addWidget(title)
        head.addStretch(1)
        btn_x = IconButton("x", "Sluiten")
        btn_x.setFixedSize(26, 26)
        btn_x.clicked.connect(self.hide)
        head.addWidget(btn_x)
        outer.addLayout(head)
        sep = hseparator()
        sep.setProperty("role", "separator-glass")
        outer.addWidget(sep)
        self.scroll = QScrollArea()
        self.scroll.setProperty("role", "transparent")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._inner = QWidget()
        self._inner.setProperty("role", "transparent")
        self.scroll.setWidget(self._inner)
        outer.addWidget(self.scroll, 1)

    def desired_size(self, video_size: QSize) -> QSize:
        return QSize(PANEL_W, min(MAX_H, video_size.height() - TOTAL_H - 3 * T.SPACE_SM, self._content_h + 70))

    _content_h = 200

    # ── opbouw ──────────────────────────────────────────────────────────
    def open_for_film(self, film_id: int) -> None:
        self._film_id = film_id
        self._rebuild()
        self.show_over_video()
        self.updateGeometry()

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setProperty("role", "pill-section")
        return lbl

    def _dim(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("role", "pill-muted")
        return lbl

    def _rebuild(self) -> None:
        self._inner = QWidget()
        self._inner.setProperty("role", "transparent")
        v = QVBoxLayout(self._inner)
        v.setContentsMargins(0, 0, T.SPACE_XS, 0)
        v.setSpacing(T.SPACE_XS)
        fid = self._film_id
        if not fid:
            return

        v.addWidget(self._section("Acteurs"))
        actors = db.get_actors_for_film(fid)
        if actors:
            for a in actors:
                v.addWidget(self._actor_row(a))
        else:
            v.addWidget(self._dim("Geen acteurs gekoppeld"))
        v.addWidget(hseparator())

        v.addWidget(self._section("Thumbnails"))
        thumbs = db.get_film_thumbnails(fid)
        if thumbs:
            grid = QGridLayout()
            grid.setSpacing(T.SPACE_XS)
            for i, t in enumerate(thumbs):
                grid.addWidget(self._thumb_cell(t), i // 3, i % 3)
            v.addLayout(grid)
        else:
            v.addWidget(self._dim("Geen thumbnails opgeslagen"))
        v.addWidget(hseparator())

        v.addWidget(self._section("Filmcategorieën"))
        cats = db.get_film_categorie_types()
        if cats:
            active = db.get_film_category_ids(fid)
            flow = FlowLayout(h_spacing=T.SPACE_XS, v_spacing=T.SPACE_XS)
            for c in cats:
                chip = ToggleChip(c["naam"], tone="teal")
                chip.setChecked(c["id"] in active)
                chip.toggled.connect(lambda on, cid=c["id"]: self._toggle_film_cat(cid, on))
                flow.addWidget(chip)
            v.addLayout(flow)
        else:
            v.addWidget(self._dim("Geen filmcategorieën aangemaakt"))
        v.addWidget(hseparator())

        v.addWidget(self._section("Markers"))
        film = db.get_film(fid)
        path = film["file_path"] if film else ""
        if path:
            ms = [(i, m) for i, m in enumerate(load_markers(path)) if not m.get("negative") and m.get("actors")]
            if ms:
                for idx, m in ms:
                    v.addWidget(self._marker_block(m, idx, path))
            else:
                v.addWidget(self._dim("Geen markers met acteurs"))
        else:
            v.addWidget(self._dim("Filmpad niet gevonden"))
        v.addStretch(1)
        self.scroll.setWidget(self._inner)
        self._inner.adjustSize()
        self._content_h = self._inner.sizeHint().height()

    def _actor_row(self, actor: dict) -> QWidget:
        row = QWidget()
        row.setProperty("role", "transparent")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(T.SPACE_SM)
        photo = QLabel()
        photo.setFixedSize(22, 28)
        p = next((x["photo_path"] for x in db.get_actor_photos(actor["id"]) if os.path.exists(x["photo_path"])), "")
        pm = self.photos.get(p, 22, 28) if p else None
        if pm is not None:
            photo.setPixmap(pm)
        else:
            photo.setPixmap(icons.pixmap("user", 16, T.TEXT_DIM))
            photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(photo)
        name = QLabel(_display(actor))
        name.setProperty("role", "pill")
        h.addWidget(name, 1)
        btn = IconButton("user-x", "Acteur van deze film ontkoppelen", color=T.RED)
        btn.setFixedSize(26, 26)
        btn.clicked.connect(lambda _c, a=actor: self._unlink_actor(a))
        h.addWidget(btn)
        return row

    def _thumb_cell(self, thumb: dict) -> QWidget:
        tw, th = 84, 47
        cell = QWidget()
        cell.setProperty("role", "transparent")
        cell.setFixedSize(tw, th)
        lbl = QLabel(cell)
        lbl.setGeometry(0, 0, tw, th)
        pm = self.thumbs.get(thumb.get("path") or "", tw, th)
        if pm is not None:
            lbl.setPixmap(pm)
        else:
            lbl.setStyleSheet(f"background:{T.SURFACE2}; border-radius:3px;")
        btn = IconButton("x", "Thumbnail verwijderen", variant="danger")
        btn.setParent(cell)
        btn.setFixedSize(20, 20)
        btn.move(tw - 22, 2)
        btn.clicked.connect(lambda _c, t=thumb: self._delete_thumb(t))
        return cell

    def _marker_block(self, m: dict, idx: int, path: str) -> QWidget:
        block = QWidget()
        block.setProperty("role", "transparent")
        v = QVBoxLayout(block)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(T.SPACE_XS)
        t = QLabel(fmt_hms(float(m.get("time") or 0)))
        t.setProperty("role", "pill-muted")
        top.addWidget(t)
        for cid in m.get("categories") or []:
            pm = self.cat_icons.pixmap(int(cid), 14)
            if pm is not None:
                ic = QLabel()
                ic.setPixmap(pm)
                top.addWidget(ic)
            else:
                top.addWidget(self._dim(self.cat_icons.name(int(cid))))
        top.addStretch(1)
        v.addLayout(top)
        for aid in m.get("actors") or []:
            actor = db.get_actor(int(aid))
            if not actor:
                continue
            row = QWidget()
            row.setProperty("role", "transparent")
            h = QHBoxLayout(row)
            h.setContentsMargins(T.SPACE_MD, 0, 0, 0)
            h.setSpacing(T.SPACE_SM)
            name = QLabel(_display(actor))
            name.setProperty("role", "pill-muted")
            h.addWidget(name, 1)
            btn = IconButton("x", "Deze acteur uit de marker halen", color=T.RED)
            btn.setFixedSize(22, 22)
            btn.clicked.connect(lambda _c, a=int(aid), i=idx, mt=float(m.get("time") or 0), p=path:
                                self._remove_from_marker(a, i, mt, p))
            h.addWidget(btn)
            v.addWidget(row)
        return block

    # ── acties ──────────────────────────────────────────────────────────
    def _unlink_actor(self, actor: dict) -> None:
        if not self._film_id:
            return
        if not confirm(self, "Acteur ontkoppelen", f"{_display(actor)} van deze film ontkoppelen?", ok_text="Ontkoppelen"):
            return
        db.unlink_actor_film(actor["id"], self._film_id)
        self.actors_changed.emit(self._film_id)
        self._rebuild()

    def _delete_thumb(self, thumb: dict) -> None:
        if not confirm(self, "Thumbnail verwijderen", "Deze thumbnail verwijderen?"):
            return
        db.delete_film_thumbnail(thumb["id"])
        if self._film_id:
            self.thumbnails_changed.emit(self._film_id)
        self._rebuild()

    def _toggle_film_cat(self, cid: int, on: bool) -> None:
        if not self._film_id:
            return
        cur = db.get_film_category_ids(self._film_id)
        (cur.add if on else cur.discard)(cid)
        db.set_film_categories(self._film_id, sorted(cur))
        self.film_cats_changed.emit(self._film_id)

    def _remove_from_marker(self, actor_id: int, idx: int, time_sec: float, path: str) -> None:
        if not confirm(self, "Acteur uit marker halen", "Deze acteur uit de marker verwijderen?"):
            return
        if markers_store.remove_actor(path, time_sec, actor_id, index_hint=idx):
            self.markers_changed.emit(path)
            self._rebuild()
