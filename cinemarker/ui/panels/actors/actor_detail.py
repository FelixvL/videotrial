"""
CineMarker — acteur-detail: velden, traits, films-grid, markers-grid, grote bestanden.

Kop:   Terug · ◀ i/n ▶ · bestandsnaam · … · Opslaan
Links: foto + velden + traits (scrollbaar).  Rechts: FILMS (zoom) boven MARKERS (zoom),
       daaronder de strip GROTE BESTANDEN (volgt de films-zoom).
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem,
                             QMessageBox, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from ....core import database as db, markers_store
from ....core.formats import DECADE_OPTIONS, GROOTTE_MAX, RATING_MAX
from ....core.markers_io import load_markers
from ....core.media import marker_frame_cache_name
from ....core.models import (ZOOM_DETAIL_FILMS, ZOOM_DETAIL_MARKERS, ActorCard, ActorMeta, FilmCard, Marker,
                             MarkerEntry)
from ....core.paths import MARKER_THUMBS_DIR
from ...delegates.cat_icons import CategoryIcons
from ...delegates.film_card import FilmCardDelegate
from ...delegates.marker_card import MarkerCardDelegate
from ...signals import AppSignals
from ...theme import icons, tokens as T
from ...widgets.buttons import AccentButton, IconButton, ToggleChip, ToolButton
from ...widgets.dialogs import confirm
from ...widgets.flow_layout import FlowLayout
from ...widgets.section import SectionHeader
from ...widgets.thumb_grid import ThumbGrid
from ...widgets.toolbar import PanelToolbar
from ...widgets.zoom import ZoomControl
from ...workers import FrameExtractWorker, stop_worker

PHOTO_W, PHOTO_H = 220, 284


class ActorDetailView(QWidget):
    back_requested = pyqtSignal()
    saved = pyqtSignal()
    open_film_requested = pyqtSignal(str)
    marker_jump_requested = pyqtSignal(str, float)
    navigate_requested = pyqtSignal(int)

    def __init__(self, signals: AppSignals, cat_icons: CategoryIcons, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        self.cat_icons = cat_icons
        self._card: ActorCard | None = None
        self._actor: dict | None = None
        self._snapshot: tuple | None = None
        self._traits: dict[int, bool] = {}
        self._trait_chips: dict[int, list[ToggleChip]] = {}
        self._film_filter = ""
        self._cat_filter: set[int] = set()
        self._cat_chips: dict[int, ToggleChip] = {}
        self._frame_worker: FrameExtractWorker | None = None
        self._entries: list[MarkerEntry] = []
        self._build_ui()

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.toolbar = PanelToolbar("Acteur")
        self.btn_back = ToolButton("Terug", "arrow-left")
        self.btn_back.clicked.connect(self._on_back)
        self.btn_prev = IconButton("chevron-left", "Vorige acteur")
        self.btn_prev.clicked.connect(lambda: self._navigate(-1))
        self.lbl_nav = QLabel("")
        self.lbl_nav.setProperty("role", "muted")
        self.lbl_nav.setMinimumWidth(56)
        self.lbl_nav.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_next = IconButton("chevron-right", "Volgende acteur")
        self.btn_next.clicked.connect(lambda: self._navigate(1))
        self.toolbar._lay.insertWidget(0, self.btn_back)
        self.toolbar._lay.insertWidget(1, self.btn_prev)
        self.toolbar._lay.insertWidget(2, self.lbl_nav)
        self.toolbar._lay.insertWidget(3, self.btn_next)
        self.toolbar.add_stretch()
        self.btn_save = AccentButton("Opslaan", "save")
        self.btn_save.clicked.connect(self.save)
        self.toolbar.add_widget(self.btn_save)
        root.addWidget(self.toolbar)

        body = QHBoxLayout()
        body.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_MD)
        body.setSpacing(T.SPACE_LG)
        root.addLayout(body, 1)

        # Links: foto + velden + traits
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(PHOTO_W + 2 * T.SPACE_MD + 16)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, T.SPACE_SM, 0)
        lv.setSpacing(T.SPACE_MD)
        self.lbl_photo = QLabel()
        self.lbl_photo.setFixedSize(PHOTO_W, PHOTO_H)
        self.lbl_photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_photo.setStyleSheet(f"background:{T.SURFACE}; border-radius:{T.RADIUS}px;")
        lv.addWidget(self.lbl_photo)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(T.SPACE_SM)
        form.setVerticalSpacing(T.SPACE_XS + 2)
        self.inp_voornaam = QLineEdit()
        self.inp_achternaam = QLineEdit()
        self.cmb_kleur = QComboBox()
        self.cmb_grootte = QComboBox()
        self.cmb_grootte.addItem("—", "")
        for i in range(1, GROOTTE_MAX + 1):
            self.cmb_grootte.addItem("★" * i, str(i))
        self.cmb_rating = QComboBox()
        self.cmb_rating.addItem("—", "")
        for i in range(1, RATING_MAX + 1):
            self.cmb_rating.addItem(str(i), str(i))
        self.cmb_dec = QComboBox()
        self.cmb_dec.addItem("—", "")
        for key, label in DECADE_OPTIONS:
            self.cmb_dec.addItem(label, key)
        self.inp_piek_start = QLineEdit()
        self.inp_piek_start.setPlaceholderText("JJJJ-MM")
        self.inp_piek_eind = QLineEdit()
        self.inp_piek_eind.setPlaceholderText("JJJJ-MM")
        for label, w in (("Voornaam", self.inp_voornaam), ("Achternaam", self.inp_achternaam),
                         ("Kleur", self.cmb_kleur), ("Grootte", self.cmb_grootte), ("Rating", self.cmb_rating),
                         ("Decennia", self.cmb_dec), ("Piek start", self.inp_piek_start),
                         ("Piek eind", self.inp_piek_eind)):
            lbl = QLabel(label)
            lbl.setProperty("role", "muted")
            form.addRow(lbl, w)
        lv.addLayout(form)

        self.sec_traits = SectionHeader("Kanten")
        lv.addWidget(self.sec_traits)
        lbl_s = QLabel("Sterk")
        lbl_s.setProperty("role", "dim")
        lv.addWidget(lbl_s)
        self.flow_sterk = FlowLayout(h_spacing=T.SPACE_XS, v_spacing=T.SPACE_XS)
        lv.addLayout(self.flow_sterk)
        lbl_z = QLabel("Zwak")
        lbl_z.setProperty("role", "dim")
        lv.addWidget(lbl_z)
        self.flow_zwak = FlowLayout(h_spacing=T.SPACE_XS, v_spacing=T.SPACE_XS)
        lv.addLayout(self.flow_zwak)
        lv.addStretch(1)
        left_scroll.setWidget(left)
        body.addWidget(left_scroll)

        # Rechts: films / markers / bigfiles
        right = QVBoxLayout()
        right.setSpacing(T.SPACE_SM)
        split = QSplitter(Qt.Orientation.Vertical)

        films_box = QWidget()
        fv = QVBoxLayout(films_box)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(T.SPACE_XS)
        self.sec_films = SectionHeader("Films")
        self.zoom_films = ZoomControl(ZOOM_DETAIL_FILMS)
        self.sec_films.add_widget(self.zoom_films)
        fv.addWidget(self.sec_films)
        self.films_grid = ThumbGrid(ZOOM_DETAIL_FILMS, spacing=2)
        self.films_delegate = FilmCardDelegate(self.films_grid)
        self.films_delegate.show_actors = False
        self.films_grid.setItemDelegate(self.films_delegate)
        self.films_grid.bind_zoom(self.zoom_films)
        self.films_grid.itemClicked.connect(self._on_film_clicked)
        self.films_grid.itemActivated.connect(self._on_film_activated)
        fv.addWidget(self.films_grid, 1)
        split.addWidget(films_box)

        markers_box = QWidget()
        mv = QVBoxLayout(markers_box)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(T.SPACE_XS)
        self.sec_markers = SectionHeader("Markers")
        self.cat_row = QWidget()
        self.cat_flow = FlowLayout(self.cat_row, h_spacing=T.SPACE_XS, v_spacing=T.SPACE_XS)
        self.zoom_markers = ZoomControl(ZOOM_DETAIL_MARKERS)
        self.sec_markers.add_widget(self.zoom_markers)
        mv.addWidget(self.sec_markers)
        mv.addWidget(self.cat_row)
        self.markers_grid = ThumbGrid(ZOOM_DETAIL_MARKERS, spacing=2)
        self.markers_delegate = MarkerCardDelegate(self.cat_icons, self.markers_grid, show_names=False,
                                                   remove_tooltip="Deze acteur uit de marker halen")
        self.markers_delegate.remove_requested.connect(self._remove_from_marker)
        self.markers_grid.setItemDelegate(self.markers_delegate)
        self.markers_grid.bind_zoom(self.zoom_markers)
        self.markers_grid.itemActivated.connect(self._on_marker_activated)
        mv.addWidget(self.markers_grid, 1)
        split.addWidget(markers_box)
        split.setSizes([400, 400])
        right.addWidget(split, 1)

        self.bf_box = QWidget()
        bv = QVBoxLayout(self.bf_box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(T.SPACE_XS)
        self.sec_bf = SectionHeader("Grote bestanden")
        bv.addWidget(self.sec_bf)
        self.bf_strip = ThumbGrid(ZOOM_DETAIL_FILMS, spacing=2, horizontal=True)
        self.bf_delegate = FilmCardDelegate(self.bf_strip)
        self.bf_delegate.show_actors = False
        self.bf_delegate.show_rating = False
        self.bf_strip.setItemDelegate(self.bf_delegate)
        self.bf_strip.bind_zoom(self.zoom_films)
        self.bf_strip.itemActivated.connect(self._on_bigfile_activated)
        bv.addWidget(self.bf_strip)
        self.bf_box.hide()
        right.addWidget(self.bf_box)
        body.addLayout(right, 1)

    # ── laden ───────────────────────────────────────────────────────────
    def load(self, card: ActorCard) -> None:
        stop_worker(self._frame_worker)
        self._frame_worker = None
        self._card = card
        self._actor = db.get_actor(card.actor_id) if card.actor_id else None
        meta = card.meta
        self.toolbar.title_label.setText(card.stem)
        pm = QPixmap(card.photo_path) if card.photo_path and os.path.exists(card.photo_path) else QPixmap()
        if pm.isNull():
            self.lbl_photo.setPixmap(icons.pixmap("user", 64, T.TEXT_DIM))
        else:
            self.lbl_photo.setPixmap(pm.scaled(PHOTO_W, PHOTO_H, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
        self.inp_voornaam.setText(meta.voornaam)
        self.inp_achternaam.setText(meta.achternaam)
        self.inp_piek_start.setText(meta.piek_start)
        self.inp_piek_eind.setText(meta.piek_eind)
        self.reload_kleur_combo(meta.kleur)
        self._set_combo(self.cmb_grootte, str(meta.grootte) if meta.grootte else "")
        self._set_combo(self.cmb_rating, str(meta.rating) if meta.rating else "")
        self._set_combo(self.cmb_dec, meta.decennia)
        active = db.get_actor_trait_ids(self._actor["id"]) if self._actor else set()
        self.reload_traits(active)
        self._film_filter = ""
        self._cat_filter = set()
        self._refresh_films()
        self._refresh_markers()
        self._snapshot = self._state()

    def set_nav_info(self, idx: int, total: int) -> None:
        self.lbl_nav.setText(f"{idx + 1} / {total}" if total else "")
        self.btn_prev.setEnabled(idx > 0)
        self.btn_next.setEnabled(idx < total - 1)

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    def reload_kleur_combo(self, current: str | None = None) -> None:
        current = self.cmb_kleur.currentData() if current is None else current
        self.cmb_kleur.blockSignals(True)
        self.cmb_kleur.clear()
        self.cmb_kleur.addItem("—", "")
        for k in db.get_actor_kleuren():
            hexv = k.get("hex") or ""
            if hexv:
                self.cmb_kleur.addItem(icons.icon("dot", 14, hexv), k["naam"], str(k["id"]))
            else:
                self.cmb_kleur.addItem(k["naam"], str(k["id"]))
        self.cmb_kleur.blockSignals(False)
        self._set_combo(self.cmb_kleur, current or "")

    def reload_traits(self, active_ids: set[int] | None = None) -> None:
        if active_ids is None:
            active_ids = {tid for tid, on in self._traits.items() if on}
        self.flow_sterk.clear()
        self.flow_zwak.clear()
        self._traits = {}
        self._trait_chips = {}
        for tt in db.get_actor_trait_types():
            tid, naam, kind = tt["id"], tt["naam"], tt.get("type") or "beide"
            self._traits[tid] = tid in active_ids
            chips = []
            if kind in ("positief", "beide"):
                chips.append(self._trait_chip(tid, naam, "green", self.flow_sterk))
            if kind in ("negatief", "beide"):
                chips.append(self._trait_chip(tid, naam, "red", self.flow_zwak))
            self._trait_chips[tid] = chips

    def _trait_chip(self, tid: int, naam: str, tone: str, flow: FlowLayout) -> ToggleChip:
        chip = ToggleChip(naam, tone=tone)
        chip.setChecked(self._traits.get(tid, False))
        chip.toggled.connect(lambda on, t=tid: self._on_trait_toggled(t, on))
        flow.addWidget(chip)
        return chip

    def _on_trait_toggled(self, tid: int, on: bool) -> None:
        self._traits[tid] = on
        for chip in self._trait_chips.get(tid, []):     # 'beide' → beide chips synchroon
            if chip.isChecked() != on:
                chip.blockSignals(True)
                chip.setChecked(on)
                chip.blockSignals(False)

    # ── films ───────────────────────────────────────────────────────────
    def _refresh_films(self) -> None:
        self.films_grid.clear()
        self.films_delegate.invalidate()
        if not self._actor:
            self.sec_films.set_count("")
            self.bf_box.hide()
            return
        films = db.get_films_for_actor(self._actor["id"])
        all_thumbs = db.get_all_film_thumbnails_batch()
        for f in films:
            fid = f.get("id")
            thumbs = [p for p in all_thumbs.get(fid, []) if os.path.exists(p)]
            if not thumbs and f.get("thumbnail") and os.path.exists(f["thumbnail"]):
                thumbs = [f["thumbnail"]]
            fp = f.get("file_path") or ""
            try:
                size = os.path.getsize(fp) if fp else 0
            except OSError:
                size = 0
            card = FilmCard(path=fp, name=f.get("title") or Path(fp).stem, film_id=fid, thumbnails=thumbs,
                            duration=float(f.get("duration") or 0), size=size,
                            markers=int(f.get("marker_count") or 0), neg_markers=int(f.get("neg_marker_count") or 0),
                            rating=float(f.get("afgeleide_rating") or 0), always_show_name=True)
            self.films_grid.add_card(card, tooltip=card.name)
        self.sec_films.set_count(f"{len(films)}")
        self.films_grid.set_animated(any(len(self.films_grid.data_of(it).thumbnails) > 1 for it in self.films_grid.cards()))
        self._refresh_bigfiles()

    def _refresh_bigfiles(self) -> None:
        self.bf_strip.clear()
        records = db.get_bigfiles_for_actor(self._actor["id"]) if self._actor else []
        if not records:
            self.bf_box.hide()
            return
        for bf in records:
            path = bf["full_path"]
            available = os.path.exists(path)
            thumb = bf.get("thumbnail_path") or ""
            card = FilmCard(path=path, name=Path(path).name, thumbnails=[thumb] if thumb and os.path.exists(thumb) else [],
                            dimmed=not available, always_show_name=True)
            self.bf_strip.add_card(card, tooltip=path + ("" if available else "  (niet bereikbaar)"))
        self.sec_bf.set_count(f"{len(records)}")
        self.bf_box.show()

    def _on_film_clicked(self, item: QListWidgetItem) -> None:
        card: FilmCard = self.films_grid.data_of(item)
        if self._film_filter == card.path:
            self._film_filter = ""
            self.films_grid.clearSelection()
        else:
            self._film_filter = card.path
        self._apply_marker_filter()

    def _on_film_activated(self, item: QListWidgetItem) -> None:
        card: FilmCard = self.films_grid.data_of(item)
        if card.path:
            self.open_film_requested.emit(card.path)

    def _on_bigfile_activated(self, item: QListWidgetItem) -> None:
        card: FilmCard = self.bf_strip.data_of(item)
        if card.path and not card.dimmed:
            self.open_film_requested.emit(card.path)

    # ── markers ─────────────────────────────────────────────────────────
    def _refresh_markers(self) -> None:
        stop_worker(self._frame_worker)
        self._frame_worker = None
        self.markers_grid.clear()
        self.markers_delegate.invalidate()
        self._entries = []
        self.cat_flow.clear()
        self._cat_chips = {}
        if not self._actor:
            self.sec_markers.set_count("")
            return
        aid = self._actor["id"]
        MARKER_THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        tasks = []
        used_cats: set[int] = set()
        for film in db.get_films_for_actor(aid):
            fp = film["file_path"]
            for m in load_markers(fp):
                if aid not in (m.get("actors") or []):
                    continue
                marker = Marker.from_dict(m)
                cache = str(MARKER_THUMBS_DIR / marker_frame_cache_name(fp, marker.time))
                entry = MarkerEntry(marker=marker, film_path=fp, film_name=Path(fp).stem, cache_path=cache)
                self._entries.append(entry)
                used_cats.update(marker.categories)
                self.markers_grid.add_card(entry, tooltip=f"{entry.film_name}  •  {marker.name}")
                if not os.path.exists(cache):
                    tasks.append((len(self._entries) - 1, fp, marker.time, cache))
        for cid in sorted(used_cats, key=lambda c: self.cat_icons.name(c).lower()):
            name = self.cat_icons.name(cid) or f"? ({cid})"
            chip = ToggleChip(name, tone="accent", tooltip=name)
            ic = self.cat_icons.icon(cid, T.ICON_SIZE_SM)
            if ic is not None:
                chip.setIcon(ic)
                chip.setIconSize(self.cat_icons.icon_size(T.ICON_SIZE_SM))
            chip.setChecked(cid in self._cat_filter)
            chip.toggled.connect(lambda on, c=cid: self._toggle_cat(c, on))
            self.cat_flow.addWidget(chip)
            self._cat_chips[cid] = chip
        self.cat_row.setVisible(bool(used_cats))
        self._apply_marker_filter()
        if tasks:
            self._frame_worker = FrameExtractWorker(tasks, self)
            self._frame_worker.frame_ready.connect(self._on_frame_ready)
            self._frame_worker.start()

    def _toggle_cat(self, cid: int, on: bool) -> None:
        if on:
            self._cat_filter.add(cid)
        else:
            self._cat_filter.discard(cid)
        self._apply_marker_filter()

    def _apply_marker_filter(self) -> None:
        visible = 0
        for it in self.markers_grid.cards():
            e: MarkerEntry = self.markers_grid.data_of(it)
            hide = bool(self._film_filter and e.film_path != self._film_filter) or \
                bool(self._cat_filter and not (set(e.marker.categories) & self._cat_filter))
            it.setHidden(hide)
            visible += 0 if hide else 1
        self.sec_markers.set_count(f"{visible} / {len(self._entries)}" if self._entries else "")

    def _on_frame_ready(self, _key, cache_path: str) -> None:
        self.markers_delegate.invalidate(cache_path)
        self.markers_grid.viewport().update()

    def _on_marker_activated(self, item: QListWidgetItem) -> None:
        e: MarkerEntry = self.markers_grid.data_of(item)
        self.marker_jump_requested.emit(e.film_path, e.time)

    def _remove_from_marker(self, entry: MarkerEntry) -> None:
        if not self._actor:
            return
        if not confirm(self, "Acteur uit marker halen",
                       f"{self._card.display_name if self._card else 'Deze acteur'} verwijderen uit de marker op "
                       f"{entry.film_name}?", ok_text="Verwijderen"):
            return
        if markers_store.remove_actor(entry.film_path, entry.time, self._actor["id"]):
            self.signals.markers_changed.emit(entry.film_path)
            self._refresh_markers()

    # ── opslaan / navigatie ─────────────────────────────────────────────
    def current_meta(self) -> ActorMeta:
        return ActorMeta(
            voornaam=self.inp_voornaam.text().strip(), achternaam=self.inp_achternaam.text().strip(),
            kleur=self.cmb_kleur.currentData() or "",
            grootte=int(self.cmb_grootte.currentData() or 0), rating=int(self.cmb_rating.currentData() or 0),
            decennia=self.cmb_dec.currentData() or "",
            piek_start=self.inp_piek_start.text().strip(), piek_eind=self.inp_piek_eind.text().strip(),
        )

    def _state(self) -> tuple:
        return (tuple(sorted(self.current_meta().to_notes().items())),
                frozenset(t for t, on in self._traits.items() if on))

    def is_dirty(self) -> bool:
        return self._snapshot is not None and self._state() != self._snapshot

    def save(self) -> bool:
        if self._card is None:
            return False
        actor_id = self._actor["id"] if self._actor else db.create_actor(self._card.stem)
        db.update_actor_meta(actor_id, self.current_meta().to_notes())
        db.set_actor_traits(actor_id, [t for t, on in self._traits.items() if on])
        self._actor = db.get_actor(actor_id)
        self._card.actor_id = actor_id
        self._card.meta = self.current_meta()
        self._snapshot = self._state()
        self.signals.actors_changed.emit()
        self.signals.say(f"{self._card.display_name} opgeslagen")
        self.saved.emit()
        return True

    def _confirm_leave(self) -> bool:
        """True = doorgaan (eventueel na opslaan), False = blijven."""
        if not self.is_dirty():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Niet-opgeslagen wijzigingen")
        box.setText("Er zijn wijzigingen die nog niet zijn opgeslagen.")
        save = box.addButton("Opslaan", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Niet opslaan", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Annuleren", QMessageBox.ButtonRole.RejectRole)
        save.setProperty("variant", "accent")
        box.setDefaultButton(save)
        box.exec()
        if box.clickedButton() is save:
            return self.save()
        return box.clickedButton() is discard

    def _on_back(self) -> None:
        if self._confirm_leave():
            self.back_requested.emit()

    def _navigate(self, direction: int) -> None:
        if self._confirm_leave():
            self.navigate_requested.emit(direction)

    def shutdown(self) -> None:
        stop_worker(self._frame_worker)
