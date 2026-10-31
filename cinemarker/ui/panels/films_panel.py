"""
CineMarker — Films-paneel.

Werkbalk:  FILMS  map  n/m | zoek | … | reset · herlaad · map · presets · marker-modus | zoom
Filterrij 1: sorteerknoppen | thumb/marker-toggles | duur min–max | MB min–max
Filterrij 2: Act.grootte · Filmcat · Kleur · Decennia · Film gr. · Markertype · Acteur (+chips)
Grid: FilmCardDelegate; dubbelklik = afspelen; rechtsklik = afspelen / bewerken / verplaatsen.
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path

from PyQt6.QtCore import QPoint, QStringListModel, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QCompleter, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget)

from ...core import database as db, media, settings
from ...core.filters import (FILM_SORT_FIELDS, FilmCriteria, compute_cross_film_ids, film_matches,
                             sort_film_cards)
from ...core.formats import DECADE_OPTIONS, GROOTTE_MAX, GROOTTE_MIN, RATING_MAX, RATING_MIN, SIZE_BUCKETS
from ...core.markers_io import load_markers
from ...core.models import ActorMeta, FilmCard, ZOOM_FILMS
from ...core.paths import MARKER_THUMBS_DIR, ensure_volume_id
from ..delegates.film_card import FilmCardDelegate
from ..signals import AppSignals
from ..theme import icons, tokens as T
from ..widgets.buttons import IconButton, SortGroup, ToggleChip, ToolButton
from ..widgets.dialogs import BaseDialog, ask_text, confirm, warning
from ..widgets.multiselect import DropdownItem, MultiSelectDropdown
from ..widgets.search_field import SearchField
from ..widgets.thumb_grid import ThumbGrid
from ..widgets.toolbar import FilterBar, PanelToolbar, vseparator
from ..widgets.zoom import ZoomControl
from ..workers import DurationWorker, stop_worker

_log = logging.getLogger("cinemarker.films")


def category_dropdown_items(cats: list[dict]) -> list[DropdownItem]:
    """Hiërarchische lijst (hoofd → sub) met iconen voor marker-categorieën."""
    by_parent: dict[int | None, list[dict]] = {}
    for c in cats:
        by_parent.setdefault(c.get("parent_id"), []).append(c)
    for lst in by_parent.values():
        lst.sort(key=lambda c: c["name"].lower())
    out: list[DropdownItem] = []
    seen: set[int] = set()

    def icon_of(c: dict):
        p = c.get("icon_path") or ""
        return icons.file_icon(p) if p and os.path.exists(p) else None

    for root in by_parent.get(None, []):
        out.append(DropdownItem(root["id"], root["name"], icon_of(root), 0))
        seen.add(root["id"])
        for sub in by_parent.get(root["id"], []):
            out.append(DropdownItem(sub["id"], sub["name"], icon_of(sub), 1))
            seen.add(sub["id"])
    for c in cats:                                   # wezen (ouder bestaat niet meer)
        if c["id"] not in seen:
            out.append(DropdownItem(c["id"], c["name"], icon_of(c), 0))
    return out


def actor_display_names() -> dict[str, int]:
    """weergavenaam → actor_id (voornaam achternaam, anders DB-naam)."""
    out: dict[str, int] = {}
    for a in db.get_all_actors():
        meta = ActorMeta.from_notes(_notes(a))
        out[meta.display_name or a["name"]] = a["id"]
    return out


def _notes(actor: dict) -> dict:
    import json
    try:
        return json.loads(actor.get("notes") or "{}") or {}
    except (TypeError, ValueError):
        return {}


class FilmsPanel(QWidget):
    play_requested = pyqtSignal(str)

    def __init__(self, signals: AppSignals, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        self._cards: list[FilmCard] = []
        self._items: dict[str, QListWidgetItem] = {}
        self._sort_key = "name"
        self._sort_asc = True
        self._cross_cache: tuple[tuple | None, set[int] | None] = (None, None)
        self._actor_names: dict[str, int] = {}
        self._actor_chips: dict[int, ToggleChip] = {}
        self._dur_worker: DurationWorker | None = None
        self._taxonomy_dirty = False
        self._active = False
        self._suspend_apply = False

        self._build_ui()
        self._connect_signals()
        QTimer.singleShot(0, self._initial_load)

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Werkbalk
        self.toolbar = PanelToolbar("Films")
        self.lbl_folder = QLabel("Geen map")
        self.lbl_folder.setProperty("role", "dim")
        self.lbl_folder.setMaximumWidth(260)
        self.toolbar.add_widget(self.lbl_folder)
        self.search = SearchField("Zoeken in films…", width=220)
        self.search.search_changed.connect(lambda _t: self._apply())
        self.toolbar.add_widget(self.search)
        self.toolbar.add_stretch()
        self.btn_reset = IconButton("filter-x", "Alle filters wissen")
        self.btn_reset.clicked.connect(self.reset_filters)
        self.btn_refresh = IconButton("refresh", "Map opnieuw inlezen")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_folder = IconButton("folder-plus", "Filmmap kiezen")
        self.btn_folder.clicked.connect(self._pick_folder)
        self.btn_presets = IconButton("star", "Filterpresets")
        self.btn_presets.clicked.connect(self._show_presets_menu)
        self.btn_marker_mode = IconButton("bookmark", "Marker-modus: toon markerframes i.p.v. thumbnails",
                                          checkable=True)
        self.btn_marker_mode.toggled.connect(self._toggle_marker_mode)
        self.toolbar.add_widgets(self.btn_reset, self.btn_refresh, self.btn_folder, self.btn_presets,
                                 self.btn_marker_mode)
        self.toolbar.add_separator()
        self.zoom = ZoomControl(ZOOM_FILMS)
        self.toolbar.add_widget(self.zoom)
        root.addWidget(self.toolbar)

        # Filterrij 1: sorteren + toggles + duur + grootte
        self.bar1 = FilterBar()
        self.bar1.add_label("Sorteer")
        self.sort_group = SortGroup(cycle_off=False)
        for key, label in FILM_SORT_FIELDS:
            self.bar1.add_widget(self.sort_group.add(key, label))
        self.sort_group.set("name", "asc", emit=False)
        self.sort_group.on_change(self._on_sort)
        self.bar1.add_widget(vseparator())
        self.chip_1thumb = ToggleChip("1 thumb", tone="teal")
        self.chip_multi = ToggleChip("Meer thumbs", tone="teal")
        self.chip_nothumb = ToggleChip("Geen thumb", tone="teal")
        self.chip_markers = ToggleChip("Met markers", tone="teal")
        self.chip_nomarkers = ToggleChip("Geen markers", tone="teal")
        for chip in (self.chip_1thumb, self.chip_multi, self.chip_nothumb, self.chip_markers, self.chip_nomarkers):
            chip.toggled.connect(lambda _c: self._apply())
            self.bar1.add_widget(chip)
        self.bar1.add_widget(vseparator())
        self.bar1.add_widget(self._icon_label("clock", "Duur (min, m:ss of u:mm:ss)"))
        self.dur_min = self._range_input("min")
        self.dur_max = self._range_input("max")
        self.bar1.add_widget(self.dur_min)
        self.bar1.add_label("–")
        self.bar1.add_widget(self.dur_max)
        self.bar1.add_widget(vseparator())
        self.bar1.add_label("MB")
        self.sz_min = self._range_input("min")
        self.sz_max = self._range_input("max")
        self.bar1.add_widget(self.sz_min)
        self.bar1.add_label("–")
        self.bar1.add_widget(self.sz_max)
        root.addWidget(self.bar1)

        # Filterrij 2: dropdowns + acteur
        self.bar2 = FilterBar()
        self.dd_groo = MultiSelectDropdown("Act. grootte", tone="accent")
        self.dd_groo.set_items([DropdownItem(str(i), "★" * i, None, 0) for i in range(GROOTTE_MIN, GROOTTE_MAX + 1)])
        self.dd_rating = MultiSelectDropdown("Act. rating", tone="accent")
        self.dd_rating.set_items([DropdownItem(str(i), str(i), None, 0) for i in range(RATING_MAX, RATING_MIN - 1, -1)])
        self.dd_filmcat = MultiSelectDropdown("Filmcat.", tone="teal")
        self.dd_kleur = MultiSelectDropdown("Kleur", tone="accent")
        self.dd_dec = MultiSelectDropdown("Decennia", tone="blue")
        self.dd_dec.set_items([DropdownItem(k, lbl, None, 0) for k, lbl in DECADE_OPTIONS])
        self.dd_size = MultiSelectDropdown("Film gr.", tone="accent")
        self.dd_size.set_items([DropdownItem(k, lbl, None, 0) for k, lbl in SIZE_BUCKETS])
        self.dd_markercat = MultiSelectDropdown("Markertype", tone="purple")
        for dd in (self.dd_groo, self.dd_rating, self.dd_filmcat, self.dd_kleur, self.dd_dec, self.dd_size,
                   self.dd_markercat):
            dd.selection_changed.connect(lambda _s: self._apply())
            self.bar2.add_widget(dd)
        self.bar2.add_widget(vseparator())
        self.bar2.add_label("Acteur")
        self.actor_input = QLineEdit()
        self.actor_input.setProperty("role", "compact")
        self.actor_input.setPlaceholderText("naam…")
        self.actor_input.setFixedWidth(150)
        self.actor_input.returnPressed.connect(self._confirm_actor_input)
        self._completer_model = QStringListModel([])
        self._completer = QCompleter(self._completer_model)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.activated.connect(self._add_actor_filter_by_name)
        self.actor_input.setCompleter(self._completer)
        self.bar2.add_widget(self.actor_input)
        root.addWidget(self.bar2)

        # Grid
        self.grid = ThumbGrid(ZOOM_FILMS, spacing=0)
        self.delegate = FilmCardDelegate(self.grid)
        self.grid.setItemDelegate(self.delegate)
        self.grid.bind_zoom(self.zoom)
        self.grid.itemActivated.connect(self._on_activate)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        root.addWidget(self.grid, 1)

        self.reload_filter_options()

    def _icon_label(self, name: str, tooltip: str) -> QLabel:
        lbl = QLabel()
        lbl.setPixmap(icons.pixmap(name, T.ICON_SIZE_SM, T.TEXT_DIM))
        lbl.setToolTip(tooltip)
        return lbl

    def _range_input(self, placeholder: str) -> QLineEdit:
        e = QLineEdit()
        e.setProperty("role", "compact")
        e.setPlaceholderText(placeholder)
        e.setFixedWidth(58)
        e.textChanged.connect(lambda _t: self._apply())
        return e

    def _connect_signals(self) -> None:
        s = self.signals
        s.film_thumbnail_changed.connect(self._on_thumbnail_changed)
        s.film_rating_changed.connect(self._on_rating_changed)
        s.markers_changed.connect(self._on_markers_changed)
        s.film_actors_changed.connect(self._on_film_actors_changed)
        s.film_deleted.connect(self._on_film_deleted)
        s.taxonomy_changed.connect(self._on_taxonomy_changed)
        s.actors_changed.connect(self._load_actor_names)

    # ── laden ───────────────────────────────────────────────────────────
    def _initial_load(self) -> None:
        folder = settings.get_str(settings.FILM_FOLDER, "")
        self._set_folder_label(folder)
        if folder:
            ensure_volume_id(folder)
            self._scan_folder(folder)

    def _set_folder_label(self, folder: str) -> None:
        self.lbl_folder.setText(Path(folder).name if folder else "Geen map")
        self.lbl_folder.setToolTip(folder)

    def _pick_folder(self) -> None:
        start = settings.get_str(settings.FILM_FOLDER, "") or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Kies filmmap", start)
        if not folder:
            return
        settings.set_str(settings.FILM_FOLDER, folder)
        ensure_volume_id(folder)
        self._set_folder_label(folder)
        self._scan_folder(folder)

    def refresh(self) -> None:
        folder = settings.get_str(settings.FILM_FOLDER, "")
        if folder:
            self._scan_folder(folder)

    def _scan_folder(self, folder: str) -> None:
        stop_worker(self._dur_worker)
        self._dur_worker = None
        self.grid.clear()
        self._items.clear()
        self._cards = []
        self.delegate.invalidate()

        folder_path = Path(folder)
        if not folder_path.is_dir():
            self._update_count()
            return

        db_films = {f["file_path"]: f for f in db.get_all_films()}
        all_thumbs = db.get_all_film_thumbnails_batch()
        all_actor_counts = db.get_actor_counts_batch()
        all_actor_photos = db.get_actor_photos_for_films_batch()
        marker_frames = media.marker_frames_by_stem(MARKER_THUMBS_DIR)

        try:
            files = sorted((f for f in folder_path.iterdir() if media.is_video(f)), key=lambda f: f.name.lower())
        except OSError as e:
            _log.warning("Filmmap niet leesbaar: %s", e)
            files = []

        dur_tasks: list[tuple[int | None, str]] = []
        cards: list[FilmCard] = []
        for fp in files:
            row = db_films.get(str(fp), {})
            film_id = row.get("id")
            duration = float(row.get("duration") or 0)
            if duration == 0:
                dur_tasks.append((film_id, str(fp)))
            thumbs = [p for p in all_thumbs.get(film_id, [])] if film_id else []
            thumbs = [p for p in thumbs if os.path.exists(p)]
            single = row.get("thumbnail") or ""
            if not thumbs and single and os.path.exists(single):
                thumbs = [single]
            size = int(row.get("file_size") or 0)
            mtime = float(row.get("file_mtime") or 0)
            if size == 0 or mtime == 0:
                try:
                    st = fp.stat()
                    size, mtime = st.st_size, st.st_mtime
                    if film_id:
                        db.update_film_file_stats(film_id, size, mtime)
                except OSError:
                    pass
            markers = load_markers(fp)
            cards.append(FilmCard(
                path=str(fp), name=fp.stem, film_id=film_id, thumbnails=thumbs,
                marker_thumbs=marker_frames.get(fp.stem, []),
                duration=duration, size=size, mtime=mtime,
                markers=len(markers), neg_markers=sum(1 for m in markers if m.get("negative")),
                marker_cat_ids={int(c) for m in markers if not m.get("negative") for c in (m.get("categories") or [])},
                actor_count=all_actor_counts.get(film_id, 0) if film_id else 0,
                actor_photos=all_actor_photos.get(film_id, []) if film_id else [],
                rating=float(row.get("afgeleide_rating") or 0),
                thumb_phase=random.uniform(0.0, 10.0), marker_period=random.uniform(2.0, 4.0),
            ))
        self._cards = cards
        self._repopulate()
        self.signals.films_rescanned.emit()

        if dur_tasks:
            self._dur_worker = DurationWorker(dur_tasks, self)
            self._dur_worker.duration_ready.connect(self._on_duration_ready)
            self._dur_worker.start()

    def _repopulate(self) -> None:
        """Sorteer de kaarten en bouw het grid opnieuw op."""
        self.grid.clear()
        self._items.clear()
        self._cards = sort_film_cards(self._cards, self._sort_key, self._sort_asc)
        for c in self._cards:
            self._items[c.path] = self.grid.add_card(c, tooltip=c.name)
        self._apply()
        self._sync_animation()

    # ── filters ─────────────────────────────────────────────────────────
    def criteria(self) -> FilmCriteria:
        return FilmCriteria(
            search=self.search.text(), dur_min=self.dur_min.text(), dur_max=self.dur_max.text(),
            sz_min=self.sz_min.text(), sz_max=self.sz_max.text(),
            one_thumb=self.chip_1thumb.isChecked(), multi_thumb=self.chip_multi.isChecked(),
            no_thumb=self.chip_nothumb.isChecked(), with_markers=self.chip_markers.isChecked(),
            no_markers=self.chip_nomarkers.isChecked(),
            marker_cats={int(x) for x in self.dd_markercat.selected_ids()},
            film_size={str(x) for x in self.dd_size.selected_ids()},
            actor_groo={str(x) for x in self.dd_groo.selected_ids()},
            actor_rating={str(x) for x in self.dd_rating.selected_ids()},
            film_cats={int(x) for x in self.dd_filmcat.selected_ids()},
            actor_kleur={str(x) for x in self.dd_kleur.selected_ids()},
            actor_dec={str(x) for x in self.dd_dec.selected_ids()},
            actors={aid: chip.text() for aid, chip in self._actor_chips.items()},
        )

    def _apply(self) -> None:
        if self._suspend_apply:
            return
        crit = self.criteria()
        cross: set[int] | None = None
        if crit.needs_cross():
            key = crit.cross_key()
            if self._cross_cache[0] != key:
                self._cross_cache = (key, compute_cross_film_ids(crit))
            cross = self._cross_cache[1]
        for card in self._cards:
            item = self._items.get(card.path)
            if item is not None:
                item.setHidden(not film_matches(card, crit, cross))
        self._update_count()
        self.btn_reset.setEnabled(not crit.is_empty())

    def _update_count(self) -> None:
        total = len(self._cards)
        visible = sum(1 for c in self._cards if (it := self._items.get(c.path)) is not None and not it.isHidden())
        self.toolbar.set_count(f"{visible} / {total} films")

    def reset_filters(self) -> None:
        self._suspend_apply = True
        try:
            self.search.clear_silently()
            for e in (self.dur_min, self.dur_max, self.sz_min, self.sz_max):
                e.clear()
            for chip in (self.chip_1thumb, self.chip_multi, self.chip_nothumb, self.chip_markers, self.chip_nomarkers):
                chip.setChecked(False)
            for dd in (self.dd_groo, self.dd_rating, self.dd_filmcat, self.dd_kleur, self.dd_dec, self.dd_size,
                       self.dd_markercat):
                dd.clear_selection()
            for aid in list(self._actor_chips):
                self._remove_actor_chip(aid)
            self.actor_input.clear()
        finally:
            self._suspend_apply = False
        self._apply()

    def apply_criteria(self, crit: FilmCriteria) -> None:
        """Herstel een (preset-)filterstatus in één keer."""
        self.reset_filters()
        self._suspend_apply = True
        try:
            self.search.blockSignals(True)
            self.search.setText(crit.search)
            self.search.blockSignals(False)
            self.dur_min.setText(crit.dur_min)
            self.dur_max.setText(crit.dur_max)
            self.sz_min.setText(crit.sz_min)
            self.sz_max.setText(crit.sz_max)
            self.chip_1thumb.setChecked(crit.one_thumb)
            self.chip_multi.setChecked(crit.multi_thumb)
            self.chip_nothumb.setChecked(crit.no_thumb)
            self.chip_markers.setChecked(crit.with_markers)
            self.chip_nomarkers.setChecked(crit.no_markers)
            self.dd_markercat.set_selection(set(crit.marker_cats))
            self.dd_size.set_selection(set(crit.film_size))
            self.dd_groo.set_selection(set(crit.actor_groo))
            self.dd_rating.set_selection(set(crit.actor_rating))
            self.dd_filmcat.set_selection(set(crit.film_cats))
            self.dd_kleur.set_selection(set(crit.actor_kleur))
            self.dd_dec.set_selection(set(crit.actor_dec))
            for aid, name in crit.actors.items():
                # op id herstellen; naam alleen als label (hernoemde acteurs blijven werken)
                current = {v: k for k, v in self._actor_names.items()}.get(aid, name)
                self._add_actor_chip(aid, current or name)
        finally:
            self._suspend_apply = False
        self._apply()

    # ── acteur-filter ───────────────────────────────────────────────────
    def _load_actor_names(self) -> None:
        self._actor_names = actor_display_names()
        self._completer_model.setStringList(sorted(self._actor_names))

    def _confirm_actor_input(self) -> None:
        text = self.actor_input.text().strip().lower()
        if not text:
            return
        exact = next((n for n in self._actor_names if n.lower() == text), None)
        partial = exact or next((n for n in self._actor_names if text in n.lower()), None)
        if partial:
            self._add_actor_filter_by_name(partial)

    def _add_actor_filter_by_name(self, name: str) -> None:
        aid = self._actor_names.get(name)
        if aid is None:
            return
        self._add_actor_chip(aid, name)
        self.actor_input.clear()
        self._apply()

    def _add_actor_chip(self, aid: int, name: str) -> None:
        if aid in self._actor_chips:
            return
        chip = ToggleChip(name, tone="blue", tooltip="Klik om dit acteurfilter te verwijderen", icon_name="x")
        chip.setChecked(True)
        chip.clicked.connect(lambda _c, a=aid: (self._remove_actor_chip(a), self._apply()))
        self.bar2.add_widget(chip)
        self._actor_chips[aid] = chip

    def _remove_actor_chip(self, aid: int) -> None:
        chip = self._actor_chips.pop(aid, None)
        if chip is not None:
            self.bar2.remove_widget(chip)

    # ── sorteren / zoom / modus ─────────────────────────────────────────
    def _on_sort(self, key: str, state: str) -> None:
        if not key:
            key, state = "name", "asc"
            self.sort_group.set("name", "asc", emit=False)
        self._sort_key = key
        self._sort_asc = state != "desc"
        self._repopulate()

    def _toggle_marker_mode(self, on: bool) -> None:
        self.delegate.set_marker_mode(on)
        self.grid.viewport().update()
        self._sync_animation()

    def _sync_animation(self) -> None:
        needs = self.btn_marker_mode.isChecked() or any(len(c.thumbnails) > 1 for c in self._cards)
        self.grid.set_animated(self._active and needs)

    def on_tab_active(self, active: bool) -> None:
        self._active = active
        if active and self._taxonomy_dirty:
            self.reload_filter_options()
        self._sync_animation()

    # ── opties (taxonomie) ──────────────────────────────────────────────
    def reload_filter_options(self) -> None:
        self._taxonomy_dirty = False
        self.dd_filmcat.set_items([DropdownItem(c["id"], c["naam"], None, 0) for c in db.get_film_categorie_types()])
        self.dd_kleur.set_items([DropdownItem(str(k["id"]), k["naam"], None, 0) for k in db.get_actor_kleuren()])
        self.dd_markercat.set_items(category_dropdown_items(db.get_all_categories()))
        self._load_actor_names()

    def _on_taxonomy_changed(self) -> None:
        if self._active:
            self.reload_filter_options()
        else:
            self._taxonomy_dirty = True
        self._cross_cache = (None, None)

    # ── live updates via AppSignals ─────────────────────────────────────
    def _card(self, path: str) -> FilmCard | None:
        item = self._items.get(path)
        return self.grid.data_of(item) if item is not None else None

    def _on_thumbnail_changed(self, path: str, thumb: str) -> None:
        card = self._card(path)
        if card is None:
            return
        if thumb and thumb not in card.thumbnails and os.path.exists(thumb):
            card.thumbnails.append(thumb)
        self.delegate.invalidate(thumb)
        self._sync_animation()
        self._apply()
        self.grid.viewport().update()

    def _on_rating_changed(self, path: str, rating: float) -> None:
        card = self._card(path)
        if card is not None:
            card.rating = rating
            self.grid.viewport().update()

    def _on_markers_changed(self, path: str) -> None:
        card = self._card(path)
        if card is None:
            return
        markers = load_markers(path)
        card.markers = len(markers)
        card.neg_markers = sum(1 for m in markers if m.get("negative"))
        card.marker_cat_ids = {int(c) for m in markers if not m.get("negative") for c in (m.get("categories") or [])}
        card.marker_thumbs = media.marker_frames_by_stem(MARKER_THUMBS_DIR).get(card.name, card.marker_thumbs)
        self._apply()
        self.grid.viewport().update()

    def _on_film_actors_changed(self, film_id: int) -> None:
        card = next((c for c in self._cards if c.film_id == film_id), None)
        if card is None:
            return
        card.actor_count = db.get_actor_counts_batch().get(film_id, 0)
        card.actor_photos = db.get_actor_photos_for_films_batch().get(film_id, [])
        self._cross_cache = (None, None)
        self._apply()
        self.grid.viewport().update()

    def _on_film_deleted(self, path: str) -> None:
        item = self._items.pop(path, None)
        if item is not None:
            self.grid.takeItem(self.grid.row(item))
        self._cards = [c for c in self._cards if c.path != path]
        self._update_count()

    def _on_duration_ready(self, film_id, path: str, duration: float) -> None:
        if film_id:
            db.set_film_duration(film_id, duration)
        card = self._card(path)
        if card is not None:
            card.duration = duration
            self._apply()
            self.grid.viewport().update()

    # ── publieke API voor de speler ─────────────────────────────────────
    def visible_paths(self) -> list[str]:
        return [c.path for c in self._cards if (it := self._items.get(c.path)) is not None and not it.isHidden()]

    def delete_film(self, path: str) -> tuple[bool, str]:
        ok, msg = media.move_film_to_deleted(path)
        if not ok:
            return False, msg
        db.delete_film_by_path(path)
        self.signals.film_deleted.emit(path)
        return True, ""

    def shutdown(self) -> None:
        stop_worker(self._dur_worker)

    # ── interactie ──────────────────────────────────────────────────────
    def _on_activate(self, item: QListWidgetItem) -> None:
        card: FilmCard = self.grid.data_of(item)
        if card:
            self.play_requested.emit(card.path)

    def _context_menu(self, pos: QPoint) -> None:
        item = self.grid.itemAt(pos)
        if item is None:
            return
        card: FilmCard = self.grid.data_of(item)
        menu = QMenu(self)
        act_play = menu.addAction(icons.icon("play"), "Afspelen")
        act_edit = menu.addAction(icons.icon("pencil"), "Film bewerken…")
        menu.addSeparator()
        act_del = menu.addAction(icons.icon("trash", color=T.RED), "Verplaats naar map 'deleted'")
        chosen = menu.exec(self.grid.viewport().mapToGlobal(pos))
        if chosen is act_play:
            self.play_requested.emit(card.path)
        elif chosen is act_edit:
            self._edit_film(card)
        elif chosen is act_del:
            if confirm(self, "Film verplaatsen", f"'{card.name}' en bijbehorende markers verplaatsen naar de map 'deleted'?",
                       ok_text="Verplaatsen"):
                ok, msg = self.delete_film(card.path)
                if not ok:
                    warning(self, "Verplaatsen mislukt", msg)

    def _edit_film(self, card: FilmCard) -> None:
        if not card.film_id:
            film = db.get_or_create_film(card.path)
            card.film_id = film["id"] if film else None
        if not card.film_id:
            return
        dlg = FilmEditDialog(card.film_id, card.name, self)
        if dlg.exec():
            self.signals.film_actors_changed.emit(card.film_id)
            self._cross_cache = (None, None)
            self._apply()

    # ── presets ─────────────────────────────────────────────────────────
    def _show_presets_menu(self) -> None:
        menu = QMenu(self)
        act_save = menu.addAction(icons.icon("save"), "Huidige filters opslaan als preset…")
        presets = db.get_all_film_filter_presets()
        if presets:
            menu.addSeparator()
            for p in presets:
                a = menu.addAction(icons.icon("filter"), p["name"])
                a.setData(p["id"])
            menu.addSeparator()
            act_del = menu.addAction(icons.icon("trash", color=T.RED), "Presets verwijderen…")
        else:
            act_del = None
        chosen = menu.exec(self.btn_presets.mapToGlobal(QPoint(0, self.btn_presets.height() + 2)))
        if chosen is None:
            return
        if chosen is act_save:
            name = ask_text(self, "Preset opslaan", "Naam van de preset:")
            if name:
                db.save_film_filter_preset(name, self.criteria().to_preset())
                self.signals.say(f"Preset '{name}' opgeslagen")
        elif act_del is not None and chosen is act_del:
            self._delete_presets_dialog(presets)
        elif chosen.data() is not None:
            self.apply_criteria(FilmCriteria.from_preset(db.load_film_filter_preset(int(chosen.data()))))

    def _delete_presets_dialog(self, presets: list[dict]) -> None:
        dlg = BaseDialog("Presets verwijderen", self, min_width=360)
        checks: dict[int, QCheckBox] = {}
        for p in presets:
            cb = QCheckBox(p["name"])
            dlg.body.addWidget(cb)
            checks[p["id"]] = cb
        dlg.add_buttons("Verwijderen", ok_variant="danger")
        if dlg.exec():
            for pid, cb in checks.items():
                if cb.isChecked():
                    db.delete_film_filter_preset(pid)


class FilmEditDialog(BaseDialog):
    """Publicatiedatum, filmcategorieën en gekoppelde acteurs van één film."""

    def __init__(self, film_id: int, film_name: str, parent: QWidget | None = None) -> None:
        super().__init__(f"Film bewerken", parent, min_width=460)
        self.film_id = film_id
        row = db.get_film(film_id) or {}
        self._active_actor_ids = {a["id"] for a in db.get_actors_for_film(film_id)}

        name = QLabel(film_name)
        name.setProperty("role", "muted")
        name.setWordWrap(True)
        self.body.addWidget(name)

        grp = QGroupBox("PUBLICATIEDATUM")
        gl = QVBoxLayout(grp)
        hint = QLabel("Formaat: JJJJ, JJJJ-MM of JJJJ-MM-DD")
        hint.setProperty("role", "dim")
        self.date_input = QLineEdit(row.get("publicatiedatum") or "")
        self.date_input.setPlaceholderText("bijv. 2019 of 2019-03")
        self.date_input.returnPressed.connect(self.accept)
        gl.addWidget(hint)
        gl.addWidget(self.date_input)
        self.body.addWidget(grp)

        self._cat_checks: dict[int, QCheckBox] = {}
        cats = db.get_film_categorie_types()
        if cats:
            active = db.get_film_category_ids(film_id)
            grp_c = QGroupBox("FILMCATEGORIEËN")
            gc = QVBoxLayout(grp_c)
            for c in cats:
                cb = QCheckBox(c["naam"])
                cb.setChecked(c["id"] in active)
                gc.addWidget(cb)
                self._cat_checks[c["id"]] = cb
            self.body.addWidget(grp_c)

        grp_a = QGroupBox("ACTEURS")
        ga = QVBoxLayout(grp_a)
        self.actor_search = SearchField("Zoeken op naam…", debounce_ms=0, width=None, compact=True)
        self.actor_search.textChanged.connect(self._filter_actors)
        self.actor_list = QListWidget()
        self.actor_list.setFixedHeight(220)
        for a in db.get_all_actors():
            meta = ActorMeta.from_notes(_notes(a))
            it = QListWidgetItem(meta.display_name or a["name"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if a["id"] in self._active_actor_ids else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, a["id"])
            self.actor_list.addItem(it)
        ga.addWidget(self.actor_search)
        ga.addWidget(self.actor_list)
        self.body.addWidget(grp_a)
        self.add_buttons("Opslaan", ok_icon="save")

    def _filter_actors(self, text: str) -> None:
        q = text.strip().lower()
        for i in range(self.actor_list.count()):
            it = self.actor_list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def accept(self) -> None:  # noqa: D102
        db.update_film_publicatiedatum(self.film_id, self.date_input.text().strip())
        if self._cat_checks:
            db.set_film_categories(self.film_id, [cid for cid, cb in self._cat_checks.items() if cb.isChecked()])
        chosen = {self.actor_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.actor_list.count())
                  if self.actor_list.item(i).checkState() == Qt.CheckState.Checked}
        for aid in chosen - self._active_actor_ids:
            db.link_actor_film(aid, self.film_id)
        for aid in self._active_actor_ids - chosen:
            db.unlink_actor_film(aid, self.film_id)
        super().accept()
