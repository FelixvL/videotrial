"""
CineMarker — markerdialoog (M-toets en bewerken).

Toont tijd + vastgelegd frame, acteurs (uit te sluiten), score (1–5 sterren),
categorieën per hoofdcategorie (klik = kiezen = opslaan), filmcategorieën.
Toetsen: ↑/↓ hoofdcategorie, ←/→ subcategorie, Enter = kiezen, M = tijd/frame
bijwerken naar de huidige positie, Esc = annuleren. Heeft een echte achtergrond.
"""

from __future__ import annotations

import os
from typing import Callable

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ....core import database as db
from ....core.formats import format_time
from ...theme import icons, tokens as T
from ...theme.stylesheet import repolish
from ...widgets.buttons import IconButton, ToggleChip, ToolButton
from ...widgets.flow_layout import FlowLayout
from ...widgets.toolbar import hseparator

FRAME_W = 300


class MarkerQuickDialog(QDialog):
    def __init__(self, parent: QWidget | None, actors: list[dict], pos: float, frame: QPixmap | None,
                 categories: list[dict], *, initial_stars: int = 0, editing: bool = False,
                 initial_cat_id: int | None = None, film_id: int | None = None,
                 pos_fn: Callable[[], float] | None = None,
                 frame_fn: Callable[[], QPixmap | None] | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._actors = list(actors)
        self._excluded: set[int] = set()
        self._pos = pos
        self._pos_fn = pos_fn
        self._frame_fn = frame_fn
        self._stars = 0
        self._chosen: dict | None = next((c for c in categories if c["id"] == initial_cat_id), None)
        self._deleted = False
        self._wants_thumbnail = False
        self._film_id = film_id
        self._film_cat_sel: set[int] = db.get_film_category_ids(film_id) if film_id else set()
        self._cat_btns: dict[int, ToolButton] = {}
        self._roots = [c for c in categories if not c.get("parent_id")]
        self._subs: dict[int, list[dict]] = {r["id"]: [] for r in self._roots}
        for c in categories:
            pid = c.get("parent_id")
            if pid in self._subs:
                self._subs[pid].append(c)
        self._nav_row, self._nav_col = -1, -1
        self._build(frame, editing)
        if initial_stars:
            self._set_stars(initial_stars)
        self._place()

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build(self, frame: QPixmap | None, editing: bool) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setProperty("role", "glass")
        outer.addWidget(panel)
        v = QVBoxLayout(panel)
        v.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_MD)
        v.setSpacing(T.SPACE_SM)

        head = QHBoxLayout()
        title = QLabel("MARKER BEWERKEN" if editing else "NIEUWE MARKER")
        title.setProperty("role", "pill-section")
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_pos = QLabel(format_time(self._pos))
        self.lbl_pos.setProperty("role", "pill-accent")
        head.addWidget(self.lbl_pos)
        v.addLayout(head)

        self.lbl_frame: QLabel | None = None
        if frame is not None and not frame.isNull():
            self.lbl_frame = QLabel()
            self.lbl_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_frame.setPixmap(frame.scaledToWidth(FRAME_W, Qt.TransformationMode.SmoothTransformation))
            v.addWidget(self.lbl_frame)
            row = QHBoxLayout()
            row.addStretch(1)
            self.chip_thumb = ToggleChip("Ook als filmthumbnail opslaan", tone="teal", icon_name="camera")
            self.chip_thumb.toggled.connect(lambda on: setattr(self, "_wants_thumbnail", on))
            row.addWidget(self.chip_thumb)
            v.addLayout(row)

        # acteurs
        actors_row = FlowLayout(h_spacing=T.SPACE_XS, v_spacing=T.SPACE_XS)
        lbl_a = QLabel("Acteurs")
        lbl_a.setProperty("role", "pill-muted")
        actors_row.addWidget(lbl_a)
        self._actor_chips: dict[int, ToggleChip] = {}
        for a in self._actors:
            chip = ToggleChip(a.get("name", ""), tone="red", icon_name="x",
                              tooltip="Klik om deze acteur niet mee te nemen")
            chip.setChecked(False)
            chip.toggled.connect(lambda on, aid=a["id"]: self._toggle_actor(aid, on))
            self._actor_chips[a["id"]] = chip
            actors_row.addWidget(chip)
        v.addLayout(actors_row)

        # score
        stars_row = QHBoxLayout()
        lbl_s = QLabel("Score")
        lbl_s.setProperty("role", "pill-muted")
        stars_row.addWidget(lbl_s)
        self._star_btns: list[IconButton] = []
        for n in range(1, 6):
            b = IconButton("star", f"{n} ster{'ren' if n > 1 else ''}", color=T.TEXT_DIM)
            b.clicked.connect(lambda _c, num=n: self._set_stars(num))
            stars_row.addWidget(b)
            self._star_btns.append(b)
        stars_row.addStretch(1)
        v.addLayout(stars_row)
        v.addWidget(hseparator())

        lbl_c = QLabel("Kies een categorie  (↑↓ hoofd · ←→ sub · Enter)")
        lbl_c.setProperty("role", "pill-muted")
        v.addWidget(lbl_c)
        for root in self._roots:
            row = QHBoxLayout()
            row.setSpacing(T.SPACE_XS)
            row.addWidget(self._cat_btn(root, is_root=True))
            for sub in self._subs.get(root["id"], []):
                row.addWidget(self._cat_btn(sub, is_root=False))
            row.addStretch(1)
            v.addLayout(row)

        fc_types = db.get_film_categorie_types()
        if fc_types:
            v.addWidget(hseparator())
            fc_flow = FlowLayout(h_spacing=T.SPACE_XS, v_spacing=T.SPACE_XS)
            lbl_f = QLabel("Filmcategorie")
            lbl_f.setProperty("role", "pill-muted")
            fc_flow.addWidget(lbl_f)
            for fc in fc_types:
                chip = ToggleChip(fc["naam"], tone="teal")
                chip.setChecked(fc["id"] in self._film_cat_sel)
                chip.toggled.connect(lambda on, fid=fc["id"]: (self._film_cat_sel.add(fid) if on
                                                              else self._film_cat_sel.discard(fid)))
                fc_flow.addWidget(chip)
            v.addLayout(fc_flow)

        bottom = QHBoxLayout()
        bottom.setSpacing(T.SPACE_SM)
        if editing:
            btn_del = ToolButton("Marker verwijderen", "trash", variant="danger", color=T.RED)
            btn_del.clicked.connect(self._delete)
            bottom.addWidget(btn_del)
        bottom.addStretch(1)
        btn_cancel = ToolButton("Annuleren")
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)
        if editing:
            btn_save = ToolButton("Opslaan", "save", variant="accent", color=T.ACCENT_TEXT_ON)
            btn_save.clicked.connect(self._save)
            bottom.addWidget(btn_save)
        v.addLayout(bottom)

        for b in self.findChildren(QPushButton):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _cat_btn(self, cat: dict, *, is_root: bool) -> ToolButton:
        btn = ToolButton(cat["name"], variant="")
        if not is_root:
            btn.setProperty("kind", "segment")
        ip = cat.get("icon_path") or ""
        if ip and os.path.exists(ip):
            btn.setIcon(icons.file_icon(ip, 20) or btn.icon())
            btn.setIconSize(QSize(20, 20))
        if self._chosen and cat["id"] == self._chosen["id"]:
            btn.setProperty("variant", "accent")
            repolish(btn)
        btn.clicked.connect(lambda _c, c=cat: self._choose(c))
        self._cat_btns[cat["id"]] = btn
        return btn

    # ── logica ──────────────────────────────────────────────────────────
    def _active_actors(self) -> list[dict]:
        return [a for a in self._actors if a["id"] not in self._excluded]

    def _toggle_actor(self, aid: int, excluded: bool) -> None:
        if excluded and len(self._active_actors()) <= 1:
            self._actor_chips[aid].setChecked(False)        # laatste acteur blijft
            return
        (self._excluded.add if excluded else self._excluded.discard)(aid)

    def _set_stars(self, n: int) -> None:
        self._stars = 0 if self._stars == n else n
        for i, b in enumerate(self._star_btns):
            on = i < self._stars
            b.set_icon("star-filled" if on else "star", T.ACCENT if on else T.TEXT_DIM)

    def _choose(self, cat: dict) -> None:
        if not self._active_actors():
            return
        self._chosen = cat
        self.accept()

    def _save(self) -> None:
        if self._chosen is not None and self._active_actors():
            self.accept()

    def _delete(self) -> None:
        self._deleted = True
        self.accept()

    def accept(self) -> None:  # noqa: D102
        if self._film_id is not None:
            db.set_film_categories(self._film_id, sorted(self._film_cat_sel))
        super().accept()

    # resultaten
    def result_actors(self) -> list[dict]:
        return self._active_actors()

    def result_category(self) -> dict | None:
        return self._chosen

    def result_stars(self) -> int:
        return self._stars

    def result_pos(self) -> float:
        return self._pos

    def was_deleted(self) -> bool:
        return self._deleted

    def wants_thumbnail(self) -> bool:
        return self._wants_thumbnail

    # ── toetsen ─────────────────────────────────────────────────────────
    def keyPressEvent(self, event) -> None:  # noqa: N802
        k = event.key()
        if k == Qt.Key.Key_Down:
            self._nav(1, 0)
        elif k == Qt.Key.Key_Up:
            self._nav(-1, 0)
        elif k == Qt.Key.Key_Right:
            self._nav(0, 1)
        elif k == Qt.Key.Key_Left:
            self._nav(0, -1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._nav_activate()
        elif k == Qt.Key.Key_M:
            self._update_pos()
        elif k == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def _nav(self, drow: int, dcol: int) -> None:
        if not self._roots:
            return
        if drow:
            self._nav_row = max(0, min(len(self._roots) - 1, self._nav_row + drow))
            self._nav_col = -1
        elif self._nav_row >= 0:
            subs = self._subs.get(self._roots[self._nav_row]["id"], [])
            self._nav_col = max(-1, min(len(subs) - 1, self._nav_col + dcol))
        self._highlight()

    def _nav_target(self) -> dict | None:
        if self._nav_row < 0:
            return None
        root = self._roots[self._nav_row]
        if self._nav_col == -1:
            return root
        subs = self._subs.get(root["id"], [])
        return subs[self._nav_col] if 0 <= self._nav_col < len(subs) else None

    def _nav_activate(self) -> None:
        target = self._nav_target()
        if target is not None:
            self._choose(target)

    def _highlight(self) -> None:
        target = self._nav_target()
        for cid, btn in self._cat_btns.items():
            btn.setProperty("focused", "true" if target and cid == target["id"] else "")
            repolish(btn)

    def _update_pos(self) -> None:
        if self._pos_fn is None:
            return
        self._pos = float(self._pos_fn())
        self.lbl_pos.setText(format_time(self._pos))
        if self._frame_fn is not None and self.lbl_frame is not None:
            pm = self._frame_fn()
            if pm is not None and not pm.isNull():
                self.lbl_frame.setPixmap(pm.scaledToWidth(FRAME_W, Qt.TransformationMode.SmoothTransformation))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _place(self) -> None:
        self.adjustSize()
        margin = 24
        parent = self.parentWidget()
        geo = parent.window().geometry() if parent else QApplication.primaryScreen().availableGeometry()
        self.move(geo.x() + geo.width() - self.width() - margin, geo.y() + geo.height() - self.height() - margin)
