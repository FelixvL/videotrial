"""
CineMarker — Acteurs-paneel.

Werkbalk:   ACTEURS  map  n/m | zoek | … | Buiten DB · Naam splitsen · Map · Import | zoom
Filterrij 1: Sorteer-knoppen + reset
Filterrij 2: DB ✓/✗ · Kleur · Rating · Grootte · Decennia  (chips; in BUITEN DB-modus radio-gedrag)
Pagina's:   grid · detail · naam splitsen
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QFileDialog, QLabel, QListWidgetItem, QMessageBox, QStackedWidget,
                             QVBoxLayout, QWidget)

from ....core import database as db, importing, settings
from ....core.filters import ACTOR_SORT_FIELDS, ActorCriteria, actor_matches, sort_actor_cards
from ....core.formats import DECADE_OPTIONS, GROOTTE_MAX, GROOTTE_MIN
from ....core.models import ZOOM_ACTORS, ActorCard
from ....core.paths import ACTEURFOTOS_DIR
from ...delegates.actor_card import ActorCardDelegate
from ...delegates.cat_icons import CategoryIcons
from ...signals import AppSignals
from ...theme import icons, tokens as T
from ...widgets.buttons import IconButton, SortGroup, ToggleChip, ToolButton
from ...widgets.dialogs import info, warning
from ...widgets.search_field import SearchField
from ...widgets.thumb_grid import ThumbGrid
from ...widgets.toolbar import FilterBar, PanelToolbar, vseparator
from ...widgets.zoom import ZoomControl
from ...workers import ActorScanWorker, stop_worker
from .actor_detail import ActorDetailView
from .split_names import SplitNamesPanel

_log = logging.getLogger("cinemarker.actors")

PAGE_GRID, PAGE_DETAIL, PAGE_SPLIT = 0, 1, 2


class ActorsPanel(QWidget):
    open_film_requested = pyqtSignal(str)
    scene_jump_requested = pyqtSignal(str, float)

    def __init__(self, signals: AppSignals, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        self.cat_icons = CategoryIcons()
        self._cards: list[ActorCard] = []
        self._items: dict[str, QListWidgetItem] = {}
        self._sort_key = ""
        self._sort_desc = False
        self._buiten_db = False
        self._suspend = False
        self._worker: ActorScanWorker | None = None
        self._last_photo_mtime = -1.0
        self._detail_stem = ""
        self._chips: dict[str, dict[str, ToggleChip]] = {}
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(800)
        self._rescan_timer.timeout.connect(self.refresh)

        self._build_ui()
        self._connect_signals()
        QTimer.singleShot(0, self.refresh)

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        self.toolbar = PanelToolbar("Acteurs")
        self.lbl_folder = QLabel("Geen map")
        self.lbl_folder.setProperty("role", "dim")
        self.lbl_folder.setMaximumWidth(220)
        self.toolbar.add_widget(self.lbl_folder)
        self.search = SearchField("Zoeken op naam…", width=220)
        self.search.search_changed.connect(lambda _t: self._apply())
        self.toolbar.add_widget(self.search)
        self.toolbar.add_stretch()
        self.btn_mode = ToolButton("Buiten DB", "user-x", checkable=True,
                                   tooltip="Tag-modus: kies één eigenschap, toon acteurs die die nog missen en "
                                           "klik een kaart om de waarde toe te kennen")
        self.btn_mode.toggled.connect(self._toggle_mode)
        self.btn_split = ToolButton("Naam splitsen", "scissors", tooltip="Voor- en achternaam uit bestandsnaam halen")
        self.btn_split.clicked.connect(self._open_split)
        self.btn_folder = IconButton("folder-plus", "Fotomap kiezen")
        self.btn_folder.clicked.connect(self._pick_folder)
        self.btn_import = IconButton("upload", "Acteurs importeren (CSV / TSV / MySQL-dump)")
        self.btn_import.clicked.connect(self._import)
        self.btn_refresh = IconButton("refresh", "Fotomap opnieuw inlezen")
        self.btn_refresh.clicked.connect(self.refresh)
        self.toolbar.add_widgets(self.btn_mode, self.btn_split, self.btn_folder, self.btn_import, self.btn_refresh)
        self.toolbar.add_separator()
        self.zoom = ZoomControl(ZOOM_ACTORS)
        self.toolbar.add_widget(self.zoom)
        pv.addWidget(self.toolbar)

        self.bar1 = FilterBar()
        self.bar1.add_label("Sorteer")
        self.sort_group = SortGroup(cycle_off=True)
        for key, label in ACTOR_SORT_FIELDS:
            self.bar1.add_widget(self.sort_group.add(key, label))
        self.sort_group.on_change(self._on_sort)
        self.btn_sort_reset = IconButton("rotate-ccw", "Standaardvolgorde (rating, films, markers, naam)")
        self.btn_sort_reset.clicked.connect(lambda: self.sort_group.reset())
        self.bar1.add_widget(self.btn_sort_reset)
        self.bar1.add_widget(vseparator())
        self.btn_reset = IconButton("filter-x", "Alle filters wissen")
        self.btn_reset.clicked.connect(self.reset_filters)
        self.bar1.add_widget(self.btn_reset)
        pv.addWidget(self.bar1)

        self.grid = ThumbGrid(ZOOM_ACTORS, spacing=0)
        self.delegate = ActorCardDelegate(self.grid)
        self.delegate.detail_requested.connect(self.open_detail)
        self.grid.setItemDelegate(self.delegate)
        self.grid.bind_zoom(self.zoom)
        self.grid.itemClicked.connect(self._on_click)
        self.grid.itemActivated.connect(self._on_activate)

        self.bar2 = FilterBar()
        self._build_filter_chips()
        pv.addWidget(self.bar2)
        pv.addWidget(self.grid, 1)
        self.stack.addWidget(page)

        self.detail = ActorDetailView(self.signals, self.cat_icons)
        self.detail.back_requested.connect(lambda: self.stack.setCurrentIndex(PAGE_GRID))
        self.detail.saved.connect(self._on_detail_saved)
        self.detail.open_film_requested.connect(self._on_open_film)
        self.detail.marker_jump_requested.connect(self.scene_jump_requested)
        self.detail.navigate_requested.connect(self._navigate)
        self.stack.addWidget(self.detail)

        self.split_panel = SplitNamesPanel(self.signals)
        self.split_panel.back_requested.connect(lambda: self.stack.setCurrentIndex(PAGE_GRID))
        self.stack.addWidget(self.split_panel)

    def _build_filter_chips(self) -> None:
        self.bar2.clear()
        self._chips = {}
        self._db_widgets: list[QWidget] = []

        def group(title: str, name: str, items: list[tuple[str, str, str | None]], tone: str) -> None:
            lbl = self.bar2.add_label(title)
            self._chips[name] = {}
            widgets = [lbl]
            for value, label, color in items:
                chip = ToggleChip(label, tone=tone)
                if color:
                    chip.setIcon(icons.icon("dot", 12, color))
                    chip.setIconSize(QSize(12, 12))
                chip.toggled.connect(lambda on, n=name, c=chip: self._on_chip(n, c, on))
                self.bar2.add_widget(chip)
                self._chips[name][value] = chip
                widgets.append(chip)
            sep = vseparator()
            self.bar2.add_widget(sep)
            widgets.append(sep)
            if name == "db":
                self._db_widgets = widgets

        group("DB", "db", [("in", "In database", None), ("out", "Niet in database", None)], "teal")
        group("Kleur", "kleur", [(str(k["id"]), k["naam"], k.get("hex") or None) for k in db.get_actor_kleuren()], "accent")
        group("Rating", "rating", [(str(i), str(i), None) for i in range(9, 4, -1)], "accent")
        group("Grootte", "grootte", [(str(i), "★" * i, None) for i in range(GROOTTE_MIN, GROOTTE_MAX + 1)], "accent")
        group("Decennia", "decennia", [(k, lbl, None) for k, lbl in DECADE_OPTIONS], "blue")
        self.delegate.set_kleur_map({str(k["id"]): (k.get("hex") or "") for k in db.get_actor_kleuren()})
        for w in self._db_widgets:
            w.setVisible(not self._buiten_db)

    def _connect_signals(self) -> None:
        s = self.signals
        s.actors_changed.connect(self._rescan_timer.start)
        s.film_actors_changed.connect(lambda _fid: self._rescan_timer.start())
        s.markers_changed.connect(lambda _p: self._rescan_timer.start())
        s.taxonomy_changed.connect(self._on_taxonomy_changed)

    # ── scan ────────────────────────────────────────────────────────────
    def photo_folder(self) -> str:
        return settings.get_str(settings.PHOTO_FOLDER, "") or str(ACTEURFOTOS_DIR)

    def refresh(self) -> None:
        folder = self.photo_folder()
        self.lbl_folder.setText(Path(folder).name or folder)
        self.lbl_folder.setToolTip(folder)
        try:
            mtime = ACTEURFOTOS_DIR.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._last_photo_mtime:
            db.auto_link_actor_photos()
            self._last_photo_mtime = mtime
        stop_worker(self._worker)
        self._worker = ActorScanWorker(folder, settings.get_str(settings.FILM_FOLDER, ""), self)
        self._worker.scan_ready.connect(self._on_scan_ready)
        self._worker.start()

    def _on_scan_ready(self, cards: list) -> None:
        self._cards = list(cards)
        self.delegate.invalidate()
        self._repopulate()
        if self.stack.currentIndex() == PAGE_DETAIL and self._detail_stem:
            self._update_nav()

    def _repopulate(self) -> None:
        self.grid.clear()
        self._items.clear()
        self._cards = sort_actor_cards(self._cards, self._sort_key, self._sort_desc)
        for c in self._cards:
            self._items[c.stem] = self.grid.add_card(c, tooltip=c.display_name)
        self._apply()

    # ── filters ─────────────────────────────────────────────────────────
    def _selected(self, name: str) -> set[str]:
        return {v for v, chip in self._chips.get(name, {}).items() if chip.isChecked()}

    def criteria(self) -> ActorCriteria:
        return ActorCriteria(search=self.search.text(), db=self._selected("db"), kleur=self._selected("kleur"),
                             grootte=self._selected("grootte"), rating=self._selected("rating"),
                             decennia=self._selected("decennia"), buiten_db=self._buiten_db)

    def _on_chip(self, name: str, chip: ToggleChip, on: bool) -> None:
        if self._buiten_db and on:                    # radio-gedrag over alle groepen
            self._suspend = True
            for gname, chips in self._chips.items():
                if gname == "db":
                    continue
                for c in chips.values():
                    if c is not chip and c.isChecked():
                        c.setChecked(False)
            self._suspend = False
        self._apply()

    def _apply(self) -> None:
        if self._suspend:
            return
        crit = self.criteria()
        visible = 0
        for c in self._cards:
            it = self._items.get(c.stem)
            if it is None:
                continue
            show = actor_matches(c, crit)
            it.setHidden(not show)
            visible += int(show)
        self.toolbar.set_count(f"{visible} / {len(self._cards)} acteurs")
        self.btn_reset.setEnabled(not crit.is_empty())

    def reset_filters(self) -> None:
        self._suspend = True
        self.search.clear_silently()
        for chips in self._chips.values():
            for c in chips.values():
                c.setChecked(False)
        self._suspend = False
        self._apply()

    def _toggle_mode(self, on: bool) -> None:
        self._buiten_db = on
        for w in self._db_widgets:
            w.setVisible(not on)
        self.reset_filters()

    def _on_sort(self, key: str, state: str) -> None:
        self._sort_key = key
        self._sort_desc = state == "desc"
        self._repopulate()

    def _on_taxonomy_changed(self) -> None:
        keep = {n: self._selected(n) for n in self._chips}
        self._build_filter_chips()
        self._suspend = True
        for n, vals in keep.items():
            for v in vals:
                if v in self._chips.get(n, {}):
                    self._chips[n][v].setChecked(True)
        self._suspend = False
        self.cat_icons.reload()
        self.detail.reload_kleur_combo()
        self.detail.reload_traits()
        self._apply()
        self.grid.viewport().update()

    def on_tab_active(self, active: bool) -> None:
        if active and self.stack.currentIndex() == PAGE_GRID:
            self.search.setFocus()

    # ── interactie ──────────────────────────────────────────────────────
    def _on_click(self, item: QListWidgetItem) -> None:
        card: ActorCard = self.grid.data_of(item)
        if self._buiten_db:
            active = self.criteria().buiten_db_active()
            if active is None:
                self.signals.say("Kies eerst één eigenschap om toe te kennen")
                return
            field_name, value = active
            actor_id = card.actor_id or db.create_actor(card.stem)
            db.update_actor_meta(actor_id, {field_name: value})
            card.actor_id = actor_id
            setattr(card.meta, field_name, int(value) if field_name in ("grootte", "rating") else value)
            self.signals.say(f"{card.display_name}: {field_name} = {value}")
            self._apply()
            self.grid.viewport().update()
            return
        if card.in_db:
            QApplication.clipboard().setText(card.display_name)
            self.signals.say(f"Naam gekopieerd: {card.display_name}", 2000)

    def _on_activate(self, item: QListWidgetItem) -> None:
        if self._buiten_db:
            return
        card: ActorCard = self.grid.data_of(item)
        if card.in_db:
            self.open_detail(card)

    def _visible_cards(self) -> list[ActorCard]:
        return [c for c in self._cards if (it := self._items.get(c.stem)) is not None and not it.isHidden()]

    def open_detail(self, card: ActorCard) -> None:
        self._detail_stem = card.stem
        self.detail.load(card)
        self._update_nav()
        self.stack.setCurrentIndex(PAGE_DETAIL)

    def _update_nav(self) -> None:
        visible = self._visible_cards()
        idx = next((i for i, c in enumerate(visible) if c.stem == self._detail_stem), 0)
        self.detail.set_nav_info(idx, len(visible))

    def _navigate(self, direction: int) -> None:
        visible = self._visible_cards()
        idx = next((i for i, c in enumerate(visible) if c.stem == self._detail_stem), -1)
        new = idx + direction
        if 0 <= new < len(visible):
            self.open_detail(visible[new])

    def _on_detail_saved(self) -> None:
        self.grid.viewport().update()

    def _on_open_film(self, path: str) -> None:
        self.search.clear_silently()
        self.open_film_requested.emit(path)

    def _open_split(self) -> None:
        self.split_panel.refresh(self.photo_folder())
        self.stack.setCurrentIndex(PAGE_SPLIT)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecteer fotomap", self.photo_folder())
        if folder:
            settings.set_str(settings.PHOTO_FOLDER, folder)
            self.refresh()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importeer acteurs", "", "Bestanden (*.csv *.txt *.tsv);;Alle bestanden (*.*)")
        if not path:
            return
        try:
            records = importing.parse_import_file(path)
        except OSError as e:
            warning(self, "Importfout", f"Kan bestand niet lezen:\n{e}")
            return
        if not records:
            info(self, "Importeren", "Geen records gevonden in het bestand.")
            return
        preview = [f"•  {r['name']}" for r in records[:15]]
        if len(records) > 15:
            preview.append(f"… en {len(records) - 15} meer")
        box = QMessageBox(self)
        box.setWindowTitle("Importeren")
        box.setText(f"{len(records)} acteurs gevonden. Importeren?")
        box.setDetailedText("\n".join(preview))
        ok = box.addButton("Importeer", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Annuleren", QMessageBox.ButtonRole.RejectRole)
        ok.setProperty("variant", "accent")
        box.exec()
        if box.clickedButton() is not ok:
            return
        inserted, updated = db.import_actors_from_records(records)
        self.signals.actors_changed.emit()
        info(self, "Klaar", f"{inserted} nieuwe acteurs toegevoegd.\n{updated} bestaande acteurs bijgewerkt.")

    def shutdown(self) -> None:
        stop_worker(self._worker)
        self.detail.shutdown()
