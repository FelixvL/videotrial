"""
CineMarker — acteurs-overlay linksonder in de video.

Rij 1: foto's van de aan de film gekoppelde acteurs (klik = (de)selecteren, standaard allemaal aan).
Rij 2: hoofdcategorieën (klik = direct een marker met de geselecteerde acteurs; rechtsklik = verwijderen),
       bewerk-knop (film-edit-paneel) en de thumbnail-knop (toont de filmthumbnails, klik = vastleggen).
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QMenu, QPushButton, QWidget

from ....core import database as db
from ....core.models import ActorMeta
from ...delegates.pixcache import PixmapCache
from ...theme import icons, tokens as T
from ...widgets.buttons import IconButton
from ...widgets.overlay import GLASS, Anchor, FloatingPanel

TW, TH = 52, 62          # acteurfoto
CW, CH = 42, 42          # categorie-icoon
SPACING = 6
PAD = 8
ROW_GAP = 8
LABEL_H = 14
THUMB_W, THUMB_H = 80, 52
CELL_A = TH + LABEL_H + 2
CELL_C = CH + LABEL_H + 2
TOTAL_H = PAD + CELL_A + ROW_GAP + CELL_C + PAD


class FilmActorsOverlay(FloatingPanel):
    marker_requested = pyqtSignal(list, list)      # actors (dicts), categories (dicts)
    thumbnail_requested = pyqtSignal()
    edit_requested = pyqtSignal(int)               # film_id
    category_deleted = pyqtSignal()

    def __init__(self, video: QWidget) -> None:
        super().__init__(video, anchor=Anchor.BOTTOM_LEFT, mode="tool_window", margin=T.SPACE_SM, bg=GLASS)
        self.setFixedHeight(TOTAL_H)
        self.setMouseTracking(True)
        self._photos = PixmapCache(None, write_disk=False)
        self._film_id: int | None = None
        self._actors: list[dict] = []
        self._names: list[str] = []
        self._selected: set[int] = set()
        self._cats: list[dict] = []
        self._hover: tuple[str, int] | None = None
        self._thumbs: list[str] = []
        self._thumb_idx = 0
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(2000)
        self._thumb_timer.timeout.connect(self._cycle_thumb)

        self.btn_edit = IconButton("pencil", "Acteurs, thumbnails en categorieën van deze film beheren", parent=self)
        self.btn_edit.clicked.connect(lambda: self.edit_requested.emit(self._film_id) if self._film_id else None)
        self.btn_thumb = QPushButton(self)
        self.btn_thumb.setFixedSize(THUMB_W, THUMB_H)
        self.btn_thumb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_thumb.setToolTip("Huidig beeld als filmthumbnail opslaan")
        self.btn_thumb.setStyleSheet(
            f"QPushButton {{ background:{T.TEAL_BG}; border:1px solid {T.TEAL}; border-radius:{T.RADIUS_SM}px; padding:0; }}"
            f"QPushButton:hover {{ border:2px solid {T.TEAL}; }}")
        self.btn_thumb.clicked.connect(self.thumbnail_requested)
        self._set_thumb_preview("")

    # ── afmetingen ──────────────────────────────────────────────────────
    def _row_a_w(self) -> int:
        n = len(self._actors)
        return n * (TW + SPACING) - (SPACING if n else 0)

    def _row_c_w(self) -> int:
        n = len(self._cats)
        return n * (CW + SPACING) - (SPACING if n else 0)

    def _total_w(self) -> int:
        cat_total = self._row_c_w() + SPACING + T.ICON_BTN + SPACING + THUMB_W
        return PAD + max(self._row_a_w(), cat_total) + PAD

    def desired_size(self, video_size: QSize) -> QSize:
        w = min(self._total_w(), max(200, video_size.width() - 2 * T.SPACE_SM))
        return QSize(w, TOTAL_H)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        cat_y = PAD + CELL_A + ROW_GAP
        tx = self.width() - PAD - THUMB_W
        self.btn_thumb.move(tx, cat_y + (CELL_C - THUMB_H) // 2)
        self.btn_edit.move(tx - SPACING - T.ICON_BTN, cat_y + (CELL_C - T.ICON_BTN) // 2)

    # ── data ────────────────────────────────────────────────────────────
    def refresh(self, film_id: int | None, *, select_only: int | None = None, keep_selection: bool = False) -> None:
        """Acteurs van de film herladen.

        Standaard (nieuwe film) zijn alle acteurs geselecteerd. `select_only` = alleen
        die acteur (na koppelen); `keep_selection` = bestaande selectie behouden
        (na een wijziging in het bewerkpaneel)."""
        previous = set(self._selected)
        self._film_id = film_id
        self._actors, self._names, self._selected = [], [], set()
        if film_id is None:
            self.hide()
            return
        for a in db.get_actors_for_film(film_id):
            self._actors.append(a)
            self._names.append(ActorMeta.from_notes(_notes(a)).display_name or a.get("name", ""))
        ids = {a["id"] for a in self._actors}
        if select_only is not None and select_only in ids:
            self._selected = {select_only}
        elif keep_selection:
            self._selected = previous & ids
        else:
            self._selected = set(ids)
        self.reload_categories()
        self.load_thumbnails(film_id)
        self.updateGeometry()
        self.update()
        self.show_over_video()

    def reload_categories(self) -> None:
        self._cats = [c for c in db.get_all_categories() if not c.get("parent_id")]
        self._cats.sort(key=lambda c: c["name"].lower())
        self._photos.invalidate()
        self.update()

    def selected_actors(self) -> list[dict]:
        return [a for a in self._actors if a["id"] in self._selected]

    def _photo_of(self, actor: dict) -> str:
        for p in db.get_actor_photos(actor["id"]):
            if os.path.exists(p["photo_path"]):
                return p["photo_path"]
        return ""

    # ── thumbnails ──────────────────────────────────────────────────────
    def load_thumbnails(self, film_id: int) -> None:
        self._thumbs = [r["path"] for r in db.get_film_thumbnails(film_id) if os.path.exists(r["path"])]
        self._thumb_idx = 0
        self._thumb_timer.stop()
        self._set_thumb_preview(self._thumbs[0] if self._thumbs else "")
        if len(self._thumbs) > 1:
            self._thumb_timer.start()

    def _cycle_thumb(self) -> None:
        if len(self._thumbs) > 1:
            self._thumb_idx = (self._thumb_idx + 1) % len(self._thumbs)
            self._set_thumb_preview(self._thumbs[self._thumb_idx])

    def _set_thumb_preview(self, path: str) -> None:
        pm = self._photos.get(path, THUMB_W - 2, THUMB_H - 2) if path else None
        if pm is not None:
            self.btn_thumb.setIcon(QIcon(pm))
            self.btn_thumb.setIconSize(QSize(THUMB_W - 2, THUMB_H - 2))
        else:
            self.btn_thumb.setIcon(icons.icon("camera", 22, T.TEAL))
            self.btn_thumb.setIconSize(QSize(22, 22))

    # ── tekenen ─────────────────────────────────────────────────────────
    def _actor_rect(self, i: int) -> QRect:
        return QRect(PAD + i * (TW + SPACING), PAD, TW, TH)

    def _cat_rect(self, i: int) -> QRect:
        return QRect(PAD + i * (CW + SPACING), PAD + CELL_A + ROW_GAP, CW, CH)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.bg is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.bg)
            p.drawRoundedRect(self.rect(), T.RADIUS, T.RADIUS)
        f = QFont(p.font())
        f.setPixelSize(T.FONT_SIZE_XS)
        p.setFont(f)
        fm = p.fontMetrics()

        for i, (a, name) in enumerate(zip(self._actors, self._names)):
            r = self._actor_rect(i)
            sel = a["id"] in self._selected
            hov = self._hover == ("actor", i)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.ACCENT_BG) if sel else QColor(34, 38, 44, 170))
            p.drawRoundedRect(r, 4, 4)
            pm = self._photos.get(self._photo_of(a), TW, TH)
            if pm is not None:
                p.drawPixmap(r.x(), r.y(), pm)
            else:
                p.drawPixmap(r.center().x() - 11, r.center().y() - 11, icons.pixmap("user", 22, T.TEXT_DIM))
            if sel:
                p.setPen(QPen(QColor(T.ACCENT), 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
            elif hov:
                p.setPen(QPen(QColor(T.TEXT_DIM), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
            if not sel:
                p.fillRect(r, QColor(*T.OVERLAY_DIM))
            label = fm.elidedText(name.split(" ")[0] if fm.horizontalAdvance(name) > TW + 4 else name,
                                  Qt.TextElideMode.ElideRight, TW + 4)
            lr = QRect(r.x() - 2, r.bottom() + 2, TW + 4, LABEL_H)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(18, 20, 24, 170))
            p.drawRoundedRect(lr, 3, 3)
            p.setPen(QColor(T.TEXT if sel else T.TEXT_MUTED))
            p.drawText(lr, Qt.AlignmentFlag.AlignHCenter, label)

        for i, c in enumerate(self._cats):
            r = self._cat_rect(i)
            hov = self._hover == ("cat", i)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(42, 47, 54, 230) if hov else QColor(34, 38, 44, 190))
            p.drawRoundedRect(r, 4, 4)
            ip = c.get("icon_path") or ""
            pm = self._photos.get(ip, CW, CH) if ip and os.path.exists(ip) else None
            if pm is not None:
                p.drawPixmap(r.x(), r.y(), pm)
            else:
                p.drawPixmap(r.center().x() - 10, r.center().y() - 10, icons.pixmap("tag", 20, T.ACCENT if hov else T.TEXT_MUTED))
            lr = QRect(r.x() - 6, r.bottom() + 2, CW + 12, LABEL_H)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(18, 20, 24, 170))
            p.drawRoundedRect(lr, 3, 3)
            p.setPen(QColor(T.TEXT if hov else T.TEXT_MUTED))
            p.drawText(lr, Qt.AlignmentFlag.AlignHCenter, fm.elidedText(c["name"], Qt.TextElideMode.ElideRight, CW + 12))
        p.end()

    # ── muis ────────────────────────────────────────────────────────────
    def _hit(self, pos: QPoint) -> tuple[str, int] | None:
        for i in range(len(self._actors)):
            if self._actor_rect(i).contains(pos):
                return "actor", i
        for i in range(len(self._cats)):
            if self._cat_rect(i).contains(pos):
                return "cat", i
        return None

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        hit = self._hit(e.position().toPoint())
        if hit != self._hover:
            self._hover = hit
            self.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        hit = self._hit(e.position().toPoint())
        if hit is None:
            return
        kind, i = hit
        if kind == "actor" and e.button() == Qt.MouseButton.LeftButton:
            aid = self._actors[i]["id"]
            self._selected.symmetric_difference_update({aid})
            self.update()
        elif kind == "cat":
            cat = self._cats[i]
            if e.button() == Qt.MouseButton.LeftButton:
                self.marker_requested.emit(self.selected_actors(), [cat])
            elif e.button() == Qt.MouseButton.RightButton:
                menu = QMenu(self)
                act = menu.addAction(icons.icon("trash", color=T.RED), f"Categorie '{cat['name']}' verwijderen")
                if menu.exec(e.globalPosition().toPoint()) is act:
                    from ...widgets.dialogs import confirm
                    if confirm(self, "Categorie verwijderen",
                               f"Categorie '{cat['name']}' (en eventuele subcategorieën) verwijderen?"):
                        db.delete_category(cat["id"])
                        self.category_deleted.emit()


def _notes(actor: dict) -> dict:
    import json
    try:
        return json.loads(actor.get("notes") or "{}") or {}
    except (TypeError, ValueError):
        return {}

