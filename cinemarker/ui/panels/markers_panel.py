"""
CineMarker — Markers-overzicht: alle markers van alle films, filterbaar en afspeelbaar.

Werkbalk:   MARKERS  n | zoek film of acteur | … | reset · vernieuwen | interval · Afspelen | zoom
Filterrij 1: sorteerknoppen (3-staps) · shuffle
Filterrij 2: Markertype · Filmcat · Eigenschap · Kleur · Grootte · Decennia · Film gr. · Sterren
Body:       acteurlijst (driestanden-filter) | grid met MarkerCardDelegate
Filters blijven staan bij refresh (ook na verwijderen).
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QSpinBox, QSplitter,
                             QVBoxLayout, QWidget)

from ...core import database as db, markers_store
from ...core.filters import (MARKER_SORT_FIELDS, MarkerContext, MarkerCriteria, actor_marker_counts,
                             filter_and_sort_markers)
from ...core.formats import DECADE_OPTIONS, GROOTTE_MAX, GROOTTE_MIN, RATING_MAX, RATING_MIN, SIZE_BUCKETS
from ...core.markers_io import load_markers
from ...core.media import marker_frame_cache_name
from ...core.models import ZOOM_MARKERS, ActorMeta, Marker, MarkerEntry
from ...core.paths import MARKER_THUMBS_DIR
from ..delegates.cat_icons import CategoryIcons
from ..delegates.marker_card import MarkerCardDelegate
from ..signals import AppSignals
from ..theme import icons, tokens as T
from ..widgets.buttons import AccentButton, IconButton, SortGroup
from ..widgets.dialogs import confirm
from ..widgets.multiselect import DropdownItem, MultiSelectDropdown
from ..widgets.search_field import SearchField
from ..widgets.section import SectionHeader
from ..widgets.thumb_grid import ThumbGrid
from ..widgets.toolbar import FilterBar, PanelToolbar, vseparator
from ..widgets.zoom import ZoomControl
from ..workers import FrameExtractWorker, stop_worker
from .films_panel import category_dropdown_items

_log = logging.getLogger("cinemarker.markers")

_AID_ROLE = Qt.ItemDataRole.UserRole
NEUTRAL, INCLUDE, EXCLUDE = 0, 1, 2


class MarkersPanel(QWidget):
    scene_jump_requested = pyqtSignal(str, float)
    play_selection_requested = pyqtSignal(list, int)      # list[MarkerEntry], interval_sec
    edit_marker_requested = pyqtSignal(object, str)       # Marker, film_path

    def __init__(self, signals: AppSignals, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        self.cat_icons = CategoryIcons()
        self._entries: list[MarkerEntry] = []
        self._shown: list[MarkerEntry] = []
        self._ctx = MarkerContext()
        self._actors: dict[int, dict] = {}
        self._actor_states: dict[int, int] = {}
        self._sort_key = ""
        self._sort_asc = True
        self._shuffle_seed: int | None = None
        self._dirty = True
        self._active = False
        self._worker: FrameExtractWorker | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self.refresh)
        self._build_ui()
        self._connect_signals()

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.toolbar = PanelToolbar("Markers")
        self.search = SearchField("Zoek film of acteur…", width=220)
        self.search.search_changed.connect(lambda _t: self._apply())
        self.toolbar.add_widget(self.search)
        self.toolbar.add_stretch()
        self.btn_reset = IconButton("filter-x", "Alle filters wissen")
        self.btn_reset.clicked.connect(self.reset_filters)
        self.btn_refresh = IconButton("refresh", "Markers opnieuw inlezen")
        self.btn_refresh.clicked.connect(self.refresh)
        self.toolbar.add_widgets(self.btn_reset, self.btn_refresh)
        self.toolbar.add_separator()
        lbl_int = QLabel("Interval")
        lbl_int.setProperty("role", "muted")
        self.interval = QSpinBox()
        self.interval.setRange(0, 99)
        self.interval.setSuffix(" s")
        self.interval.setSpecialValueText("handmatig")
        self.interval.setFixedWidth(124)
        self.interval.setToolTip("0 = handmatig (O/P), anders automatisch door naar de volgende marker")
        self.btn_play = AccentButton("Afspelen", "play")
        self.btn_play.clicked.connect(self._play)
        self.toolbar.add_widgets(lbl_int, self.interval, self.btn_play)
        self.toolbar.add_separator()
        self.zoom = ZoomControl(ZOOM_MARKERS)
        self.toolbar.add_widget(self.zoom)
        root.addWidget(self.toolbar)

        self.bar1 = FilterBar()
        self.bar1.add_label("Sorteer")
        self.sort_group = SortGroup(cycle_off=True)
        for key, label in MARKER_SORT_FIELDS:
            self.bar1.add_widget(self.sort_group.add(key, label))
        self.sort_group.on_change(self._on_sort)
        self.btn_shuffle = IconButton("shuffle", "Willekeurige volgorde", checkable=True)
        self.btn_shuffle.toggled.connect(self._on_shuffle)
        self.bar1.add_widget(self.btn_shuffle)
        root.addWidget(self.bar1)

        self.bar2 = FilterBar()
        self.dd_type = MultiSelectDropdown("Markertype", tone="purple")
        self.dd_filmcat = MultiSelectDropdown("Filmcat.", tone="teal")
        self.dd_trait = MultiSelectDropdown("Eigenschap", tone="accent")
        self.dd_kleur = MultiSelectDropdown("Kleur", tone="accent")
        self.dd_groo = MultiSelectDropdown("Grootte", tone="accent")
        self.dd_groo.set_items([DropdownItem(str(i), "★" * i, None, 0) for i in range(GROOTTE_MIN, GROOTTE_MAX + 1)])
        self.dd_rating = MultiSelectDropdown("Rating", tone="accent")
        self.dd_rating.set_items([DropdownItem(str(i), str(i), None, 0) for i in range(RATING_MAX, RATING_MIN - 1, -1)])
        self.dd_dec = MultiSelectDropdown("Decennia", tone="blue")
        self.dd_dec.set_items([DropdownItem(k, lbl, None, 0) for k, lbl in DECADE_OPTIONS])
        self.dd_size = MultiSelectDropdown("Film gr.", tone="accent")
        self.dd_size.set_items([DropdownItem(k, lbl, None, 0) for k, lbl in SIZE_BUCKETS])
        self.dd_stars = MultiSelectDropdown("Sterren", tone="accent")
        self.dd_stars.set_items([DropdownItem(str(i), "★" * i, None, 0) for i in range(1, 6)])
        for dd in (self.dd_type, self.dd_filmcat, self.dd_trait, self.dd_kleur, self.dd_groo, self.dd_rating,
                   self.dd_dec, self.dd_size, self.dd_stars):
            dd.selection_changed.connect(lambda _s: self._apply())
            self.bar2.add_widget(dd)
        self.dd_type.selection_changed.connect(lambda _s: self._rebuild_actor_list())
        root.addWidget(self.bar2)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(T.SPACE_MD, T.SPACE_SM, T.SPACE_XS, T.SPACE_SM)
        lv.setSpacing(T.SPACE_XS)
        self.sec_actors = SectionHeader("Acteurs")
        hint = QLabel("klik: ＋ / × / neutraal  •  rechtsklik: ×")
        hint.setProperty("role", "dim")
        self.sec_actors.add_widget(hint)
        lv.addWidget(self.sec_actors)
        self.actor_search = SearchField("Zoek acteur…", debounce_ms=0, width=None, compact=True)
        self.actor_search.textChanged.connect(lambda _t: self._rebuild_actor_list())
        lv.addWidget(self.actor_search)
        self.actor_list = QListWidget()
        self.actor_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.actor_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.actor_list.itemClicked.connect(self._on_actor_click)
        self.actor_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.actor_list.customContextMenuRequested.connect(self._on_actor_right_click)
        lv.addWidget(self.actor_list, 1)
        left.setMinimumWidth(180)
        left.setMaximumWidth(320)
        split.addWidget(left)

        self.grid = ThumbGrid(ZOOM_MARKERS, spacing=T.SPACE_XS)
        self.delegate = MarkerCardDelegate(self.cat_icons, self.grid, show_names=True, remove_tooltip="Marker verwijderen")
        self.delegate.remove_requested.connect(self._delete_entry)
        self.grid.setItemDelegate(self.delegate)
        self.grid.bind_zoom(self.zoom)
        self.grid.itemActivated.connect(self._on_activate)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        split.addWidget(self.grid)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 1200])
        root.addWidget(split, 1)
        self._reload_dropdown_options()

    def _connect_signals(self) -> None:
        s = self.signals
        s.markers_changed.connect(lambda _p: self.mark_dirty())
        s.actors_changed.connect(self.mark_dirty)
        s.film_deleted.connect(lambda _p: self.mark_dirty())
        s.films_rescanned.connect(self.mark_dirty)
        s.taxonomy_changed.connect(self._on_taxonomy_changed)

    # ── laden ───────────────────────────────────────────────────────────
    def mark_dirty(self) -> None:
        self._dirty = True
        if self._active:
            self._refresh_timer.start()

    def refresh_if_dirty(self) -> None:
        if self._dirty:
            self.refresh()

    def on_tab_active(self, active: bool) -> None:
        self._active = active
        if active:
            self.refresh_if_dirty()

    def _reload_dropdown_options(self) -> None:
        self.dd_type.set_items(category_dropdown_items(db.get_all_categories()))
        self.dd_filmcat.set_items([DropdownItem(c["id"], c["naam"], None, 0) for c in db.get_film_categorie_types()])
        self.dd_trait.set_items([DropdownItem(t["id"], t["naam"], None, 0) for t in db.get_actor_trait_types()])
        self.dd_kleur.set_items([DropdownItem(str(k["id"]), k["naam"], None, 0) for k in db.get_actor_kleuren()])

    def _on_taxonomy_changed(self) -> None:
        self.cat_icons.reload()
        self._reload_dropdown_options()
        self.mark_dirty()

    def refresh(self) -> None:
        """Alle markers opnieuw inlezen; filters, sortering en acteur-standen blijven staan."""
        self._dirty = False
        stop_worker(self._worker)
        self._worker = None
        self._actors = {a["id"]: a for a in db.get_all_actors()}
        from ...core.actors import notes_of
        self._ctx = MarkerContext(
            actor_meta={aid: ActorMeta.from_notes(notes_of(a)) for aid, a in self._actors.items()},
            actor_traits=db.get_actor_trait_ids_batch(),
            film_cats=db.get_film_category_ids_batch(),
        )
        films = db.get_all_films()
        self._ctx.film_ids = {f["file_path"]: f["id"] for f in films if f.get("file_path")}
        self._ctx.film_size = {f["file_path"]: int(f.get("file_size") or 0) for f in films if f.get("file_path")}
        self._ctx.film_rating = {f["file_path"]: float(f.get("afgeleide_rating") or 0) for f in films if f.get("file_path")}

        def display(aid: int) -> str:
            a = self._actors.get(aid)
            if not a:
                return ""
            return ActorMeta.from_notes(notes_of(a)).display_name or a["name"]

        MARKER_THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        entries: list[MarkerEntry] = []
        for f in films:
            fp = f.get("file_path") or ""
            if not fp or not os.path.exists(fp):
                continue
            for m in load_markers(fp):
                if m.get("negative") or not m.get("actors"):
                    continue
                marker = Marker.from_dict(m)
                entries.append(MarkerEntry(
                    marker=marker, film_path=fp, film_name=Path(fp).stem,
                    actor_names=[n for n in (display(a) for a in marker.actors) if n],
                    cache_path=str(MARKER_THUMBS_DIR / marker_frame_cache_name(fp, marker.time)),
                ))
        self._entries = entries
        self._rebuild_actor_list()
        self._apply()

    # ── acteurlijst ─────────────────────────────────────────────────────
    def _rebuild_actor_list(self) -> None:
        counts = actor_marker_counts(self._entries, {int(x) for x in self.dd_type.selected_ids()})
        for aid in [a for a in self._actor_states if a not in counts]:
            del self._actor_states[aid]
        q = self.actor_search.text().strip().lower()
        self.actor_list.clear()

        def name_of(aid: int) -> str:
            a = self._actors.get(aid)
            if not a:
                return ""
            from ...core.actors import notes_of
            return ActorMeta.from_notes(notes_of(a)).display_name or a["name"]

        for aid, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], name_of(kv[0]).lower())):
            name = name_of(aid)
            if not name or (q and q not in name.lower()):
                continue
            state = self._actor_states.get(aid, NEUTRAL)
            it = QListWidgetItem(f"{name}  ({cnt})")
            it.setData(_AID_ROLE, aid)
            if state == INCLUDE:
                it.setIcon(icons.icon("plus", T.ICON_SIZE_SM, T.ACCENT))
                it.setForeground(QColor(T.ACCENT))
                it.setBackground(QColor(T.ACCENT_BG))
            elif state == EXCLUDE:
                it.setIcon(icons.icon("x", T.ICON_SIZE_SM, T.RED))
                it.setForeground(QColor(T.RED))
                it.setBackground(QColor(T.RED_BG))
            self.actor_list.addItem(it)
        self.sec_actors.set_count(str(self.actor_list.count()))

    def _on_actor_click(self, item: QListWidgetItem) -> None:
        aid = item.data(_AID_ROLE)
        self._actor_states[aid] = (self._actor_states.get(aid, NEUTRAL) + 1) % 3
        self._rebuild_actor_list()
        self._apply()

    def _on_actor_right_click(self, pos: QPoint) -> None:
        item = self.actor_list.itemAt(pos)
        if item is None:
            return
        aid = item.data(_AID_ROLE)
        self._actor_states[aid] = NEUTRAL if self._actor_states.get(aid) == EXCLUDE else EXCLUDE
        self._rebuild_actor_list()
        self._apply()

    # ── filters / sortering ─────────────────────────────────────────────
    def criteria(self) -> MarkerCriteria:
        return MarkerCriteria(
            search=self.search.text(),
            markertype={int(x) for x in self.dd_type.selected_ids()},
            include_actors={a for a, s in self._actor_states.items() if s == INCLUDE},
            exclude_actors={a for a, s in self._actor_states.items() if s == EXCLUDE},
            traits={int(x) for x in self.dd_trait.selected_ids()},
            film_cats={int(x) for x in self.dd_filmcat.selected_ids()},
            kleur={str(x) for x in self.dd_kleur.selected_ids()},
            grootte={str(x) for x in self.dd_groo.selected_ids()},
            rating={str(x) for x in self.dd_rating.selected_ids()},
            decennia={str(x) for x in self.dd_dec.selected_ids()},
            film_size={str(x) for x in self.dd_size.selected_ids()},
            stars={str(x) for x in self.dd_stars.selected_ids()},
        )

    def _on_sort(self, key: str, state: str) -> None:
        self._sort_key = key
        self._sort_asc = state != "desc"
        if key and self.btn_shuffle.isChecked():
            self.btn_shuffle.setChecked(False)      # roept _apply via _on_shuffle
            return
        self._apply()

    def _on_shuffle(self, on: bool) -> None:
        self._shuffle_seed = random.randrange(1 << 30) if on else None
        if on:
            self.sort_group.reset(emit=False)
            self._sort_key = ""
        self._apply()

    def reset_filters(self) -> None:
        self.search.clear_silently()
        self.actor_search.clear_silently()
        self._actor_states.clear()
        for dd in (self.dd_type, self.dd_filmcat, self.dd_trait, self.dd_kleur, self.dd_groo, self.dd_rating,
                   self.dd_dec, self.dd_size, self.dd_stars):
            dd.clear_selection()
        self._rebuild_actor_list()
        self._apply()

    def _apply(self) -> None:
        stop_worker(self._worker)
        self._worker = None
        crit = self.criteria()
        self._shown = filter_and_sort_markers(self._entries, crit, self._ctx, self._sort_key, self._sort_asc,
                                              self._shuffle_seed)
        self.grid.clear()
        tasks = []
        for i, e in enumerate(self._shown):
            self.grid.add_card(e, tooltip=f"{e.film_name}\n{e.marker.name}\n{', '.join(e.actor_names)}")
            if not os.path.exists(e.cache_path):
                tasks.append((i, e.film_path, e.time, e.cache_path))
        n = len(self._shown)
        self.toolbar.set_count(f"{n} / {len(self._entries)} markers")
        self.btn_play.setEnabled(n > 0)
        self.btn_play.setText(f"Afspelen ({n})" if n else "Afspelen")
        self.btn_reset.setEnabled(not crit.is_empty())
        if tasks:
            self._worker = FrameExtractWorker(tasks, self)
            self._worker.frame_ready.connect(self._on_frame_ready)
            self._worker.start()

    def _on_frame_ready(self, _key, cache_path: str) -> None:
        # gespreid tonen zodat het scherm niet in één keer omklapt
        QTimer.singleShot(random.randint(300, 1200), lambda: self._show_frame(cache_path))

    def _show_frame(self, cache_path: str) -> None:
        self.delegate.invalidate(cache_path)
        self.grid.viewport().update()

    # ── acties ──────────────────────────────────────────────────────────
    def _play(self) -> None:
        if self._shown:
            self.play_selection_requested.emit(list(self._shown), self.interval.value())

    def _on_activate(self, item: QListWidgetItem) -> None:
        e: MarkerEntry = self.grid.data_of(item)
        self.scene_jump_requested.emit(e.film_path, e.time)

    def _context_menu(self, pos: QPoint) -> None:
        item = self.grid.itemAt(pos)
        if item is None:
            return
        e: MarkerEntry = self.grid.data_of(item)
        menu = QMenu(self)
        act_jump = menu.addAction(icons.icon("corner-down-left"), "Spring naar scène")
        act_edit = menu.addAction(icons.icon("pencil"), "Marker bewerken…")
        menu.addSeparator()
        act_del = menu.addAction(icons.icon("trash", color=T.RED), "Marker verwijderen")
        chosen = menu.exec(self.grid.viewport().mapToGlobal(pos))
        if chosen is act_jump:
            self.scene_jump_requested.emit(e.film_path, e.time)
        elif chosen is act_edit:
            self.edit_marker_requested.emit(e.marker, e.film_path)
        elif chosen is act_del:
            self._delete_entry(e)

    def _delete_entry(self, e: MarkerEntry) -> None:
        if not confirm(self, "Marker verwijderen",
                       f"Marker op {e.film_name} ({', '.join(e.actor_names)}) definitief verwijderen?"):
            return
        if markers_store.delete(e.film_path, e.time):
            self.signals.markers_changed.emit(e.film_path)
            self.signals.say("Marker verwijderd")
            self.refresh()

    def shutdown(self) -> None:
        stop_worker(self._worker)
