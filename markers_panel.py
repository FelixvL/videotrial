#!/usr/bin/env python3
"""
CineMarker — Markers Overzicht
Toont alle markers van alle acteurs in alle films, filterbaar op categorie en acteur.
"""

import os
import json
import random
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QSplitter, QFrame, QMenu,
    QLineEdit,
)
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal, QPoint
from PyQt6.QtGui import QPixmap, QColor, QIcon

import database as db
from actors_panel import FrameExtractWorker, MarkerGridDelegate
from paths import MARKER_THUMBS_DIR


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _fmt_hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _load_cat_pix(cat: dict, size: int) -> QPixmap | None:
    ip = cat.get('icon_path', '')
    if ip and os.path.exists(ip):
        raw = QPixmap(ip)
        if not raw.isNull():
            return raw.scaled(size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
    return None


def _film_size_bucket(size_bytes: int) -> str:
    """Categoriseer bestandsgrootte in S/M/L/XL."""
    gb = size_bytes / 1_073_741_824
    if gb < 0.5:  return 'S'
    if gb < 2.0:  return 'M'
    if gb < 5.0:  return 'L'
    return 'XL'


# ─────────────────────────────────────────────
#  Multi-select dropdown filter button
# ─────────────────────────────────────────────

class _MultiDropdown(QPushButton):
    """Button that opens a floating checkable list for multi-select filtering.
    Stays open while the user checks/unchecks items; closes on click-outside."""

    filter_changed = pyqtSignal(set)   # emits set of selected ids

    _BTN_SS = (
        "QPushButton { background:#141414; border:1px solid #2a2a2a;"
        "  border-radius:4px; padding:3px 10px; color:#666; font-size:11px; }"
        "QPushButton:hover { border-color:#555; color:#aaa; }"
        "QPushButton[active=true] { border-color:#e8b86d; color:#e8b86d;"
        "  background:#1a1500; }"
    )
    _POPUP_SS = (
        "QFrame { background:#141414; border:1px solid #333; border-radius:4px; }"
        "QListWidget { background:transparent; border:none; color:#bbb;"
        "  font-size:11px; outline:none; }"
        "QListWidget::item { padding:4px 8px; border-bottom:1px solid #1e1e1e; }"
        "QListWidget::item:hover { background:#1e1e1e; }"
        "QLineEdit { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:3px;"
        "  padding:3px 7px; color:#ccc; font-size:11px; }"
        "QLineEdit:focus { border-color:#555; }"
    )
    _SEARCH_THRESHOLD = 6   # only show search field if more than N items

    def __init__(self, base_label: str, items: list, parent=None):
        """items: list of (id, display_name)"""
        super().__init__(base_label, parent)
        self._base  = base_label
        self._items = items   # [(id, name), ...]
        self._sel:  set = set()
        self._popup: QFrame | None = None
        self.setStyleSheet(self._BTN_SS)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clicked.connect(self._toggle_popup)

    def set_items(self, items: list):
        """Replace items (e.g. after DB refresh). Preserves selection."""
        self._items = items
        self._sel &= {i for i, _ in items}
        self._update_label()
        if self._popup and self._popup.isVisible():
            self._popup.hide()

    def selected_ids(self) -> set:
        return self._sel.copy()

    def clear_selection(self):
        self._sel.clear()
        self._update_label()

    # ── Private ──────────────────────────────────────────────────

    def _toggle_popup(self):
        if self._popup and self._popup.isVisible():
            self._popup.hide()
            return
        if not self._items:
            return
        # Build popup as a top-level Popup window (auto-closes on click-outside)
        popup = QFrame(self.window(),
                       Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setStyleSheet(self._POPUP_SS)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        v = QVBoxLayout(popup)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        # Zoekbalk — alleen tonen als er genoeg items zijn
        search_box: QLineEdit | None = None
        if len(self._items) > self._SEARCH_THRESHOLD:
            search_box = QLineEdit()
            search_box.setPlaceholderText("Zoeken…")
            search_box.setFixedHeight(26)
            v.addWidget(search_box)

        lw = QListWidget()
        for id_, name in self._items:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, id_)
            item.setCheckState(
                Qt.CheckState.Checked if id_ in self._sel else Qt.CheckState.Unchecked
            )
            lw.addItem(item)
        lw.itemChanged.connect(self._on_item_changed)
        v.addWidget(lw)

        if search_box is not None:
            def _filter_list(text: str):
                q = text.lower()
                for i in range(lw.count()):
                    it = lw.item(i)
                    it.setHidden(bool(q) and q not in it.text().lower())
            search_box.textChanged.connect(_filter_list)

        row_h  = 24
        extra  = 34 if search_box is not None else 0   # height of search field + spacing
        list_h = min(len(self._items) * row_h + 8, 260)
        popup.setFixedSize(max(200, self.width()), list_h + extra + 8)

        # Position below button
        global_pos = self.mapToGlobal(QPoint(0, self.height()))
        popup.move(global_pos)
        popup.show()
        self._popup = popup

        # Auto-focus search field for immediate typing
        if search_box is not None:
            search_box.setFocus()

    def _on_item_changed(self, item: QListWidgetItem):
        id_ = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._sel.add(id_)
        else:
            self._sel.discard(id_)
        self._update_label()
        self.filter_changed.emit(self._sel.copy())

    def _update_label(self):
        if self._sel:
            self.setText(f"{self._base}  ·{len(self._sel)}")
            self.setProperty('active', 'true')
        else:
            self.setText(self._base)
            self.setProperty('active', 'false')
        # Force stylesheet re-evaluation after property change
        self.style().unpolish(self)
        self.style().polish(self)


# ─────────────────────────────────────────────
#  MarkersPanel
# ─────────────────────────────────────────────

THUMB_W = FrameExtractWorker.THUMB_W   # 320
THUMB_H = FrameExtractWorker.THUMB_H   # 180
CELL_W  = THUMB_W + 4
CELL_H  = THUMB_H + 4


class MarkersPanel(QWidget):
    scene_jump_requested    = pyqtSignal(str, float)   # film_path, time_sec
    play_selection_requested = pyqtSignal(list)         # list of filtered entries
    edit_marker_requested   = pyqtSignal(dict, str)    # marker dict, film_path

    _SS_SORT_OFF = (
        "QPushButton { background:#111; border:1px solid #252525; border-radius:4px;"
        "  padding:3px 10px; color:#555; font-size:11px; }"
        "QPushButton:hover { border-color:#444; color:#888; }"
    )
    _SS_SORT_ON = (
        "QPushButton { background:#1a1500; border:1px solid #e8b86d; border-radius:4px;"
        "  padding:3px 10px; color:#e8b86d; font-size:11px; }"
        "QPushButton:hover { background:#2a2200; }"
    )

    def __init__(self, mpv_player=None):
        super().__init__()
        self._player      = mpv_player
        self._all_entries: list = []    # alle geladen markers (dicts)

        # Actor three-state: 0=neutral, 1=include, 2=exclude  {actor_id: int}
        self._actor_states: dict = {}

        # Filter sets (leeg = alle)
        self._markertype_filter:  set = set()   # marker categorie-id's
        self._trait_filter:       set = set()   # trait-id's
        self._film_cat_filter:    set = set()   # film-cat-id's
        self._kleur_filter:       set = set()   # kleur-id strings
        self._grootte_filter:     set = set()   # grootte strings 5-9
        self._decennia_filter:    set = set()   # decennia strings
        self._filmgrootte_filter: set = set()   # size buckets: 'S','M','L','XL'
        self._stars_filter:       set = set()   # sterren strings '1'-'5'

        # Sort state
        self._sort_field: str = ''   # '' = bestandsvolgorde, 'film', 'tijd', 'sterren'
        self._sort_asc:   bool = True

        # Text search
        self._search_text: str = ''

        # Internals
        self._all_items:   list = []
        self._worker: FrameExtractWorker | None = None
        # Batch lookup maps — filled in refresh()
        self._actor_trait_map:  dict = {}   # {actor_id: set(trait_id)}
        self._actor_meta_map:   dict = {}   # {actor_id: {kleur, grootte, decennia}}
        self._film_cat_map:     dict = {}   # {film_id:  set(cat_id)}
        self._film_path_to_id:  dict = {}   # {file_path: film_id}
        self._film_size_map:    dict = {}   # {file_path: file_size_bytes}
        self._actor_search: str = ''
        self._sort_btns: dict = {}          # {field: QPushButton}
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Topbalk ─────────────────────────────────────────────
        top = QWidget()
        top.setFixedHeight(42)
        top.setStyleSheet("background: #111; border-bottom: 1px solid #1e1e1e;")
        th = QHBoxLayout(top)
        th.setContentsMargins(12, 0, 10, 0)
        th.setSpacing(8)

        lbl_title = QLabel("ALLE MARKERS")
        lbl_title.setStyleSheet(
            "color: #444; font-size: 9px; letter-spacing: 4px;"
        )
        th.addWidget(lbl_title)

        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet("color: #444; font-size: 11px;")
        th.addWidget(self._lbl_count)

        th.addStretch()

        # Zoekbalk
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Zoek film of acteur…")
        self._search_input.setFixedHeight(28)
        self._search_input.setFixedWidth(200)
        self._search_input.setStyleSheet(
            "QLineEdit { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:4px;"
            "  padding:0 8px; color:#bbb; font-size:11px; }"
            "QLineEdit:focus { border-color:#555; }"
        )
        self._search_input.textChanged.connect(self._on_search_changed)
        th.addWidget(self._search_input)

        # Sorteerknopen
        for field, label in [('film', 'Film'), ('tijd', 'Tijd'), ('sterren', 'Sterren')]:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet(self._SS_SORT_OFF)
            btn.clicked.connect(lambda checked, f=field: self._on_sort_click(f))
            self._sort_btns[field] = btn
            th.addWidget(btn)

        # Reset knop
        btn_reset = QPushButton("⊘")
        btn_reset.setFixedSize(28, 28)
        btn_reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_reset.setToolTip("Reset alle filters")
        btn_reset.setStyleSheet(
            "QPushButton { background:transparent; border:1px solid #2a2a2a;"
            "  border-radius:4px; color:#555; font-size:13px; }"
            "QPushButton:hover { border-color:#c04040; color:#c04040; }"
        )
        btn_reset.clicked.connect(self._reset_filters)
        th.addWidget(btn_reset)

        self._btn_play = QPushButton("▶  Afspelen")
        self._btn_play.setFixedHeight(28)
        self._btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_play.setEnabled(False)
        self._btn_play.setToolTip(
            "Open de speler met alle gefilterde markers als afspeellijst"
        )
        self._btn_play.setStyleSheet(
            "QPushButton { background: #1a1500; border: 1px solid #3a3000;"
            "  border-radius: 4px; color: #665500; font-size: 11px;"
            "  padding: 0 14px; }"
            "QPushButton:enabled { border-color: #e8b86d; color: #e8b86d; }"
            "QPushButton:enabled:hover { background: #2a2200; }"
            "QPushButton:enabled:pressed { background: #e8b86d; color: #000; }"
        )
        self._btn_play.clicked.connect(self._emit_play_selection)
        th.addWidget(self._btn_play)

        btn_refresh = QPushButton("↺")
        btn_refresh.setFixedSize(28, 28)
        btn_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_refresh.setToolTip("Vernieuwen")
        btn_refresh.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #2a2a2a;"
            "  border-radius: 4px; color: #555; font-size: 14px; }"
            "QPushButton:hover { border-color: #e8b86d; color: #e8b86d; }"
        )
        btn_refresh.clicked.connect(self.refresh)
        th.addWidget(btn_refresh)

        root.addWidget(top)

        # ── Filterbalk ───────────────────────────────────────────
        filt = QWidget()
        filt.setFixedHeight(38)
        filt.setStyleSheet("background: #0a0a0a; border-bottom: 1px solid #1a1a1a;")
        fh = QHBoxLayout(filt)
        fh.setContentsMargins(8, 0, 8, 0)
        fh.setSpacing(6)

        _GROOTTE_ITEMS  = [(str(i), str(i)) for i in range(5, 10)]
        _DECENNIA_ITEMS = [('7','70s'), ('8','80s'), ('9','90s'), ('0','00s'), ('1','10s')]
        _FILMGR_ITEMS   = [('S','< 500 MB'), ('M','500 MB – 2 GB'),
                           ('L','2 – 5 GB'),  ('XL','> 5 GB')]
        _STARS_ITEMS    = [('1','★'), ('2','★★'), ('3','★★★'), ('4','★★★★'), ('5','★★★★★')]

        # Markertype (vervangt de oude categorie-scrollbalk)
        self._dd_markertype = _MultiDropdown("Markertype", [])
        self._dd_markertype.setFixedHeight(26)
        self._dd_markertype.filter_changed.connect(self._on_markertype_filter)
        fh.addWidget(self._dd_markertype)

        self._dd_filmcat = _MultiDropdown("Filmcat.", [])
        self._dd_filmcat.setFixedHeight(26)
        self._dd_filmcat.filter_changed.connect(
            lambda s: self._set_filter('film_cat', s))
        fh.addWidget(self._dd_filmcat)

        self._dd_trait = _MultiDropdown("Eigenschap", [])
        self._dd_trait.setFixedHeight(26)
        self._dd_trait.filter_changed.connect(
            lambda s: self._set_filter('trait', s))
        fh.addWidget(self._dd_trait)

        self._dd_kleur = _MultiDropdown("Kleur", [])
        self._dd_kleur.setFixedHeight(26)
        self._dd_kleur.filter_changed.connect(
            lambda s: self._set_filter('kleur', s))
        fh.addWidget(self._dd_kleur)

        self._dd_grootte = _MultiDropdown("Grootte", _GROOTTE_ITEMS)
        self._dd_grootte.setFixedHeight(26)
        self._dd_grootte.filter_changed.connect(
            lambda s: self._set_filter('grootte', s))
        fh.addWidget(self._dd_grootte)

        self._dd_dec = _MultiDropdown("Decennia", _DECENNIA_ITEMS)
        self._dd_dec.setFixedHeight(26)
        self._dd_dec.filter_changed.connect(
            lambda s: self._set_filter('decennia', s))
        fh.addWidget(self._dd_dec)

        self._dd_filmgr = _MultiDropdown("Film gr.", _FILMGR_ITEMS)
        self._dd_filmgr.setFixedHeight(26)
        self._dd_filmgr.filter_changed.connect(
            lambda s: self._set_filter('filmgrootte', s))
        fh.addWidget(self._dd_filmgr)

        self._dd_stars = _MultiDropdown("Sterren", _STARS_ITEMS)
        self._dd_stars.setFixedHeight(26)
        self._dd_stars.filter_changed.connect(
            lambda s: self._set_filter('stars', s))
        fh.addWidget(self._dd_stars)

        fh.addStretch()
        root.addWidget(filt)

        # ── Body: acteurlijst | markersgrid ──────────────────────
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet("QSplitter::handle { background: #1e1e1e; width: 2px; }")
        body.setHandleWidth(2)

        # Acteurlijst
        actor_wrap = QWidget()
        actor_wrap.setStyleSheet("background: #0d0d0d;")
        av = QVBoxLayout(actor_wrap)
        av.setContentsMargins(8, 6, 6, 8)
        av.setSpacing(4)

        hdr_a = QHBoxLayout()
        lbl_a = QLabel("ACTEURS")
        lbl_a.setStyleSheet("color: #444; font-size: 9px; letter-spacing: 3px;")
        hdr_a.addWidget(lbl_a)
        lbl_hint = QLabel("klik=±  rechts=×")
        lbl_hint.setStyleSheet("color: #2a2a2a; font-size: 8px;")
        hdr_a.addStretch()
        hdr_a.addWidget(lbl_hint)
        av.addLayout(hdr_a)

        # Zoekbalk acteurs
        self._actor_search_box = QLineEdit()
        self._actor_search_box.setPlaceholderText("Zoek acteur…")
        self._actor_search_box.setFixedHeight(24)
        self._actor_search_box.setStyleSheet(
            "QLineEdit { background:#111; border:1px solid #222; border-radius:3px;"
            "  padding:0 6px; color:#bbb; font-size:11px; }"
            "QLineEdit:focus { border-color:#555; }"
        )
        self._actor_search_box.textChanged.connect(self._on_actor_search)
        av.addWidget(self._actor_search_box)

        self._actor_list = QListWidget()
        self._actor_list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                color: #aaa;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 5px 6px;
                border-bottom: 1px solid #181818;
            }
            QListWidget::item:hover { background: #161616; }
        """)
        self._actor_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._actor_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._actor_list.itemClicked.connect(self._on_actor_left_click)
        self._actor_list.customContextMenuRequested.connect(
            self._on_actor_right_click)
        av.addWidget(self._actor_list)

        actor_wrap.setMinimumWidth(140)
        actor_wrap.setMaximumWidth(230)
        body.addWidget(actor_wrap)

        # Markersgrid
        self._grid = QListWidget()
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setSpacing(8)
        self._grid.setUniformItemSizes(True)
        self._grid.setIconSize(QSize(THUMB_W, THUMB_H))
        self._grid.setStyleSheet("""
            QListWidget {
                background: #0a0a0a;
                border: none;
            }
            QListWidget::item {
                background: #111;
                border-radius: 4px;
            }
            QListWidget::item:hover    { background: #181818; }
            QListWidget::item:selected { background: #2a2200; }
        """)

        self._delegate = MarkerGridDelegate(self._grid)
        self._grid.setItemDelegate(self._delegate)
        self._grid.itemActivated.connect(self._on_item_jump)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._grid.customContextMenuRequested.connect(self._on_grid_context_menu)
        body.addWidget(self._grid)

        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([180, 9999])
        root.addWidget(body, stretch=1)

    # ── Sorteren ─────────────────────────────────────────────────

    def _on_sort_click(self, field: str):
        if self._sort_field == field:
            if self._sort_asc:
                self._sort_asc = False
            else:
                # Derde klik: zet sortering uit
                self._sort_field = ''
                self._sort_asc = True
        else:
            self._sort_field = field
            self._sort_asc = True
        self._update_sort_buttons()
        self._apply_filters()

    def _update_sort_buttons(self):
        labels = {'film': 'Film', 'tijd': 'Tijd', 'sterren': 'Sterren'}
        for f, btn in self._sort_btns.items():
            if f == self._sort_field:
                btn.setText(labels[f] + (' ↑' if self._sort_asc else ' ↓'))
                btn.setStyleSheet(self._SS_SORT_ON)
            else:
                btn.setText(labels[f])
                btn.setStyleSheet(self._SS_SORT_OFF)

    # ── Reset ────────────────────────────────────────────────────

    def _reset_filters(self):
        """Reset alle filters, sortering en zoektekst."""
        self._actor_states.clear()
        # Zoekbalk
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._search_text = ''
        # Acteur-zoekvak
        self._actor_search_box.blockSignals(True)
        self._actor_search_box.clear()
        self._actor_search_box.blockSignals(False)
        self._actor_search = ''
        # Sortering
        self._sort_field = ''
        self._sort_asc = True
        self._update_sort_buttons()
        # Dropdowns (UI)
        for dd in (self._dd_markertype, self._dd_filmcat, self._dd_trait,
                   self._dd_kleur, self._dd_grootte, self._dd_dec,
                   self._dd_filmgr, self._dd_stars):
            dd.clear_selection()
        # Filtersets (staat)
        self._markertype_filter.clear()
        self._film_cat_filter.clear()
        self._trait_filter.clear()
        self._kleur_filter.clear()
        self._grootte_filter.clear()
        self._decennia_filter.clear()
        self._filmgrootte_filter.clear()
        self._stars_filter.clear()
        self._rebuild_actor_list()
        self._apply_filters()

    # ── Zoeken ───────────────────────────────────────────────────

    def _on_search_changed(self, text: str):
        self._search_text = text.strip()
        self._apply_filters()

    # ── Filter helpers ───────────────────────────────────────────

    def _on_markertype_filter(self, sel: set):
        """Markertype-filter past ook de acteurlijst aan."""
        self._markertype_filter = sel
        self._rebuild_actor_list()
        self._apply_filters()

    def _set_filter(self, name: str, sel: set):
        """Generieke handler voor set-filters."""
        _attr = {
            'film_cat':    '_film_cat_filter',
            'trait':       '_trait_filter',
            'kleur':       '_kleur_filter',
            'grootte':     '_grootte_filter',
            'decennia':    '_decennia_filter',
            'filmgrootte': '_filmgrootte_filter',
            'stars':       '_stars_filter',
        }
        setattr(self, _attr[name], sel)
        self._apply_filters()

    # ── Data laden ───────────────────────────────────────────────

    def refresh(self):
        """Laad alle markers van alle films opnieuw."""
        self._stop_worker()
        self._all_entries.clear()

        # Reset alle filterstate
        self._actor_states.clear()
        self._markertype_filter.clear()
        self._trait_filter.clear()
        self._film_cat_filter.clear()
        self._kleur_filter.clear()
        self._grootte_filter.clear()
        self._decennia_filter.clear()
        self._filmgrootte_filter.clear()
        self._stars_filter.clear()
        self._search_text = ''
        self._sort_field = ''
        self._sort_asc = True

        # Reset UI
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._actor_search_box.blockSignals(True)
        self._actor_search_box.clear()
        self._actor_search_box.blockSignals(False)
        self._actor_search = ''
        self._update_sort_buttons()
        for dd in (self._dd_filmcat, self._dd_trait, self._dd_kleur,
                   self._dd_grootte, self._dd_dec, self._dd_filmgr, self._dd_stars):
            dd.clear_selection()
        # _dd_markertype wordt hersteld door _update_markertype_dropdown()

        actors_dict = {a['id']: a for a in db.get_all_actors()}
        cats_dict   = {c['id']: c for c in db.get_all_categories()}

        # ── Batch maps ───────────────────────────────────────────
        self._actor_trait_map = db.get_actor_trait_ids_batch()
        self._actor_meta_map  = db.get_actor_metadata_batch()
        self._film_cat_map    = db.get_film_category_ids_batch()
        all_films             = db.get_all_films()
        self._film_path_to_id = {f['file_path']: f['id']
                                 for f in all_films if f.get('file_path')}
        self._film_size_map   = {f['file_path']: (f.get('file_size') or 0)
                                 for f in all_films if f.get('file_path')}

        # ── Populate dynamische dropdowns ────────────────────────
        trait_items   = [(t['id'], t['naam'])  for t in db.get_actor_trait_types()]
        filmcat_items = [(c['id'], c['naam'])  for c in db.get_film_categorie_types()]
        kleur_items   = [(str(k['id']), k['naam']) for k in db.get_actor_kleuren()]
        self._dd_trait.set_items(trait_items)
        self._dd_filmcat.set_items(filmcat_items)
        self._dd_kleur.set_items(kleur_items)

        for film in db.get_all_films():
            fp = film.get('file_path', '')
            if not fp or not os.path.exists(fp):
                continue
            p  = Path(fp)
            mf = p.parent / f".{p.stem}_markers.json"
            if not mf.exists():
                continue
            try:
                markers = json.loads(mf.read_text('utf-8'))
            except Exception:
                continue

            for m in markers:
                if m.get('negative'):
                    continue
                actor_ids = m.get('actors') or []
                cat_ids   = m.get('categories') or []
                if not actor_ids:
                    continue  # markers zonder acteur overslaan
                self._all_entries.append({
                    'marker':     m,
                    'film_path':  fp,
                    'film_name':  p.stem,
                    'actor_ids':  actor_ids,
                    'cat_ids':    cat_ids,
                    'actor_objs': [actors_dict[a] for a in actor_ids if a in actors_dict],
                    'cat_objs':   [cats_dict[c] for c in cat_ids if c in cats_dict],
                })

        self._update_markertype_dropdown()
        self._rebuild_actor_list()
        self._apply_filters()

    def _update_markertype_dropdown(self):
        """Zet Markertype-dropdown met categorieën die voorkomen in de geladen entries."""
        used_ids = set()
        for e in self._all_entries:
            used_ids.update(e['cat_ids'])
        cats = [c for c in db.get_all_categories() if c['id'] in used_ids]
        items = [(c['id'], c['name']) for c in cats]
        self._dd_markertype.set_items(items)

    # ── Gefilterde entries (gedeelde logica) ─────────────────────

    def _filtered_entries(self) -> list:
        """Pas alle actieve filters toe en geef de overeenkomende entries terug,
        gesorteerd volgens de actieve sorteerinstelling."""
        include_actors = {aid for aid, s in self._actor_states.items() if s == 1}
        exclude_actors = {aid for aid, s in self._actor_states.items() if s == 2}

        entries = self._all_entries

        if self._markertype_filter:
            entries = [e for e in entries
                       if self._markertype_filter & set(e['cat_ids'])]
        if include_actors:
            entries = [e for e in entries
                       if include_actors & set(e['actor_ids'])]
        if exclude_actors:
            entries = [e for e in entries
                       if not (exclude_actors & set(e['actor_ids']))]
        if self._trait_filter:
            entries = [e for e in entries
                       if any(self._trait_filter &
                              self._actor_trait_map.get(aid, set())
                              for aid in e['actor_ids'])]
        if self._film_cat_filter:
            entries = [e for e in entries
                       if self._film_cat_filter &
                          self._film_cat_map.get(
                              self._film_path_to_id.get(e['film_path'], -1), set()
                          )]
        if self._kleur_filter:
            entries = [e for e in entries
                       if any(self._actor_meta_map.get(aid, {}).get('kleur', '')
                              in self._kleur_filter
                              for aid in e['actor_ids'])]
        if self._grootte_filter:
            entries = [e for e in entries
                       if any(str(self._actor_meta_map.get(aid, {}).get('grootte', ''))
                              in self._grootte_filter
                              for aid in e['actor_ids'])]
        if self._decennia_filter:
            entries = [e for e in entries
                       if any(self._actor_meta_map.get(aid, {}).get('decennia', '')
                              in self._decennia_filter
                              for aid in e['actor_ids'])]
        if self._filmgrootte_filter:
            entries = [e for e in entries
                       if _film_size_bucket(
                              self._film_size_map.get(e['film_path'], 0)
                          ) in self._filmgrootte_filter]
        if self._stars_filter:
            entries = [e for e in entries
                       if str(int(e['marker'].get('stars') or 0))
                          in self._stars_filter]
        if self._search_text:
            q = self._search_text.lower()
            entries = [e for e in entries
                       if q in e['film_name'].lower() or
                          any(q in a.get('name', '').lower()
                              for a in e['actor_objs'])]

        # Sortering
        if self._sort_field == 'film':
            entries = sorted(
                entries,
                key=lambda e: (e['film_name'].lower(), e['marker'].get('time', 0.0)),
                reverse=not self._sort_asc,
            )
        elif self._sort_field == 'tijd':
            entries = sorted(
                entries,
                key=lambda e: e['marker'].get('time', 0.0),
                reverse=not self._sort_asc,
            )
        elif self._sort_field == 'sterren':
            entries = sorted(
                entries,
                key=lambda e: int(e['marker'].get('stars') or 0),
                reverse=not self._sort_asc,
            )
        # Geen sorteerveld → behoud bestandsvolgorde

        return entries

    # ── Acteur-filter ────────────────────────────────────────────

    def _rebuild_actor_list(self):
        """Bouw de acteurlijst opnieuw — alleen acteurs die markers hebben
        na de actieve markertype-filter. Houdt drie-state kleuren bij."""
        if self._markertype_filter:
            visible = [e for e in self._all_entries
                       if self._markertype_filter & set(e['cat_ids'])]
        else:
            visible = self._all_entries

        actor_counts: dict = {}
        for e in visible:
            for aid in e['actor_ids']:
                actor_counts[aid] = actor_counts.get(aid, 0) + 1

        # Verwijder states van acteurs die niet meer voorkomen
        gone = set(self._actor_states) - set(actor_counts)
        for aid in gone:
            del self._actor_states[aid]

        q = self._actor_search.lower()

        self._actor_list.blockSignals(True)
        self._actor_list.clear()

        actors_dict = {a['id']: a for a in db.get_all_actors()}
        for aid, cnt in sorted(
            actor_counts.items(),
            key=lambda x: (-x[1], actors_dict.get(x[0], {}).get('name', '').lower())
        ):
            actor = actors_dict.get(aid)
            if not actor:
                continue
            name = actor.get('name', '')
            # Zoekfilter
            if q and q not in name.lower():
                continue
            state = self._actor_states.get(aid, 0)
            suffix = '  ＋' if state == 1 else ('  ×' if state == 2 else '')
            item = QListWidgetItem(f"{name}  ({cnt}){suffix}")
            item.setData(Qt.ItemDataRole.UserRole, aid)
            if state == 1:
                item.setForeground(QColor('#e8b86d'))
                item.setBackground(QColor('#1a1600'))
            elif state == 2:
                item.setForeground(QColor('#cc4444'))
                item.setBackground(QColor('#1a0808'))
            self._actor_list.addItem(item)

        self._actor_list.blockSignals(False)

    def _on_actor_search(self, text: str):
        self._actor_search = text
        self._rebuild_actor_list()

    def _on_actor_left_click(self, item: QListWidgetItem):
        """Links klikken op acteur: neutral → include(＋) → exclude(×) → neutral."""
        aid = item.data(Qt.ItemDataRole.UserRole)
        if aid is None:
            return
        state = self._actor_states.get(aid, 0)
        self._actor_states[aid] = (state + 1) % 3
        self._rebuild_actor_list()
        self._apply_filters()

    def _on_actor_right_click(self, pos):
        """Rechts klikken: direct naar exclude(×), of terug naar neutral."""
        item = self._actor_list.itemAt(pos)
        if not item:
            return
        aid = item.data(Qt.ItemDataRole.UserRole)
        if aid is None:
            return
        state = self._actor_states.get(aid, 0)
        # Toggle: als al exclude → neutral; anders direct naar exclude
        self._actor_states[aid] = 0 if state == 2 else 2
        self._rebuild_actor_list()
        self._apply_filters()

    # ── Markersgrid ──────────────────────────────────────────────

    def _apply_filters(self):
        """Herbouw het grid op basis van actieve filters."""
        self._stop_worker()
        self._delegate.invalidate_cache()
        self._grid.clear()
        self._all_items.clear()

        entries = self._filtered_entries()

        cache_dir = MARKER_THUMBS_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)

        pending_tasks: list = []   # (row_idx, film_path, time_sec, cache_path)

        for row_idx, entry in enumerate(entries):
            m         = entry['marker']
            time_sec  = m.get('time', 0.0)
            time_ms   = int(time_sec * 1000)
            film_path = entry['film_path']

            cache_name = (
                f"{Path(film_path).stem}_{time_ms}"
                f"_w{THUMB_W}.jpg"
            )
            cache_path = str(cache_dir / cache_name)

            # Categorie-iconen voor de delegate
            cat_pixmaps = []
            for c in entry['cat_objs']:
                px = _load_cat_pix(c, 20)
                if px:
                    cat_pixmaps.append(px)

            actor_names = ', '.join(a['name'] for a in entry['actor_objs'])

            item = QListWidgetItem()
            item.setSizeHint(QSize(CELL_W, CELL_H))
            item.setData(Qt.ItemDataRole.UserRole, {
                'cache_path':  cache_path,
                'time_str':    f"{_fmt_hms(time_sec)}  •  {actor_names}",
                'cat_pixmaps': cat_pixmaps,
                'cell_size':   QSize(CELL_W, CELL_H),
                'film_path':   film_path,
                'time':        time_sec,
                'stars':       int(m.get('stars') or 0),
                'marker':      m,          # volledig marker-dict voor bewerken/verwijderen
            })
            item.setToolTip(
                f"{entry['film_name']}\n"
                f"{_fmt_hms(time_sec)}\n"
                f"{actor_names}"
            )

            self._grid.addItem(item)
            self._all_items.append(item)

            if not os.path.exists(cache_path):
                pending_tasks.append(
                    (row_idx, film_path, time_sec, cache_path)
                )

        # Start één worker voor alle ontbrekende thumbnails
        if pending_tasks:
            self._worker = FrameExtractWorker(pending_tasks)
            self._worker.frame_ready.connect(self._on_frame_ready)
            self._worker.start()

        count = len(entries)
        self._lbl_count.setText(
            f"{count} marker{'s' if count != 1 else ''}"
        )
        self._btn_play.setEnabled(count > 0)
        self._btn_play.setText(
            f"▶  {count} afspelen" if count > 0 else "▶  Afspelen"
        )

    def _on_frame_ready(self, row_idx: int, cache_path: str):
        """Plan de visuele update met een willekeurige vertraging zodat thumbnails
        niet allemaal tegelijk binnenkomen en het scherm flipt."""
        delay = 1000 + random.randint(0, 1000)
        QTimer.singleShot(delay, lambda: self._show_thumb(row_idx, cache_path))

    def _show_thumb(self, row_idx: int, cache_path: str):
        """Pas het thumbnail aan voor één item, zonder de volledige cache te wissen."""
        if row_idx >= len(self._all_items):
            return
        item = self._all_items[row_idx]
        try:
            d = item.data(Qt.ItemDataRole.UserRole)
            if d:
                d['cache_path'] = cache_path
                item.setData(Qt.ItemDataRole.UserRole, d)
                self._grid.update(self._grid.model().index(row_idx, 0))
        except RuntimeError:
            pass  # item al verwijderd door nieuwe filter

    def _emit_play_selection(self):
        """Stuur de gefilterde entries naar de speler als afspeellijst."""
        entries = self._filtered_entries()
        if entries:
            self.play_selection_requested.emit(entries)

    def _on_item_jump(self, item: QListWidgetItem):
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            self.scene_jump_requested.emit(d['film_path'], d['time'])

    # ── Context menu ─────────────────────────────────────────────

    def _on_grid_context_menu(self, pos):
        item = self._grid.itemAt(pos)
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not d:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#1a1a1a; border:1px solid #333; color:#ccc; "
            "        font-size:12px; }"
            "QMenu::item { padding:6px 20px; }"
            "QMenu::item:selected { background:#2a2200; color:#e8b86d; }"
        )
        act_jump  = menu.addAction("↵  Spring naar scène")
        menu.addSeparator()
        act_edit  = menu.addAction("✎  Bewerk marker")
        act_del   = menu.addAction("✕  Verwijder marker")

        chosen = menu.exec(self._grid.mapToGlobal(pos))
        if chosen == act_jump:
            self.scene_jump_requested.emit(d['film_path'], d['time'])
        elif chosen == act_edit:
            self.edit_marker_requested.emit(d['marker'], d['film_path'])
        elif chosen == act_del:
            self._delete_marker_in_file(d['marker'], d['film_path'])

    def _delete_marker_in_file(self, marker: dict, film_path: str):
        """Verwijder een marker uit het JSON-bestand en vernieuw het grid."""
        import json as _json
        p  = Path(film_path)
        mf = p.parent / f".{p.stem}_markers.json"
        if not mf.exists():
            return
        try:
            markers = _json.loads(mf.read_text('utf-8'))
        except Exception:
            return
        target_time = marker.get('time')
        markers = [m for m in markers if m.get('time') != target_time]
        mf.write_text(_json.dumps(markers, ensure_ascii=False, indent=2), 'utf-8')
        # Herbereken afgeleide_rating na verwijderen
        try:
            total = sum(int(m.get('stars') or 0)
                        for m in markers
                        if not m.get('negative'))
            db.update_afgeleide_rating(film_path, min(total, 10))
        except Exception:
            pass
        self.refresh()

    # ── Worker cleanup ───────────────────────────────────────────

    def _stop_worker(self):
        if self._worker is not None:
            self._worker.stop()
            try:
                self._worker.frame_ready.disconnect()
            except Exception:
                pass
            self._worker = None
