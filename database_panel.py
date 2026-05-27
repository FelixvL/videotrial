#!/usr/bin/env python3
"""
CineMarker — Database editor tab
"""

import json
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTableWidget, QTableWidgetItem, QFrame,
    QMessageBox, QHeaderView, QAbstractItemView, QSplitter,
    QScrollArea, QGridLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap

import database as db


ICONEN_DIR = Path(__file__).parent / 'iconen'

COLUMNS = [
    ('voornaam',   'Voornaam',     120, True),
    ('achternaam', 'Achternaam',   130, True),
    ('kleur',      'Kleur',         60, True),
    ('grootte',    'Grootte',       70, True),
    ('rating',     'Rating',        60, True),
    ('decennia',   'Decennia',      80, True),
    ('name',       'Bestandsnaam', 180, False),
]
COL_COUNT = len(COLUMNS)
ID_ROLE   = Qt.ItemDataRole.UserRole

CAT_COLS = [
    ('name',      'Naam',           180, True),
    ('icon_file', 'Icoon bestand',  200, True),
]
CAT_COL_COUNT = len(CAT_COLS)

_TABLE_STYLE = """
    QTableWidget {
        background: #0e0e0e;
        alternate-background-color: #111;
        gridline-color: #1e1e1e;
        border: none;
        color: #ccc;
        font-size: 12px;
    }
    QTableWidget::item { padding: 2px 6px; border: none; }
    QTableWidget::item:selected { background: #1e1600; color: #e8b86d; }
    QHeaderView::section {
        background: #0a0a0a;
        color: #555;
        font-size: 10px;
        letter-spacing: 2px;
        padding: 4px 6px;
        border: none;
        border-right: 1px solid #1e1e1e;
        border-bottom: 1px solid #1e1e1e;
    }
    QScrollBar:vertical { background: #0a0a0a; width: 8px; }
    QScrollBar::handle:vertical { background: #2a2a2a; border-radius: 4px; }
"""


def _icon_path(filename: str) -> str:
    return str(ICONEN_DIR / filename) if filename else ''


# ─── Shortcut definitions ──────────────────────────────────────────────────
# Each entry: (action_key, dutch_label, default_keys)
# default_keys is comma-separated — every listed key triggers that action.
SHORTCUT_DEFS = [
    ('play_pause',    'Afspelen / Pauzeren',   'Space'),
    ('marker',        'Marker plaatsen',        'M'),
    ('neg_marker',    'Negatieve marker',       'X'),
    ('thumbnail',     'Thumbnail opslaan',      'T'),
    ('volgende_film', 'Volgende film',          'V'),
    ('marker_voor',   'Volgende marker',        'P'),
    ('marker_achter', 'Vorige marker',          'O'),
    ('skip_voor',     'Vooruitspoelen',         'L'),
    ('skip_achter',   'Terugspoelen',           'N'),
    ('sneller',       'Sneller afspelen',       ']'),
    ('langzamer',     'Langzamer afspelen',     '['),
    ('reset_speed',   'Snelheid resetten',      '\\'),
    ('zoom_in',       'Inzoomen',               '+,='),
    ('zoom_uit',      'Uitzoomen',              '-'),
    ('zoom_reset',    'Zoom resetten',          '0'),
    ('fullscreen',    'Volledig scherm',        'F11'),
    ('open_bestand',  'Bestand openen',         'Ctrl+O'),
    ('acteurs_tonen', 'Acteurs overlay tonen',  'Ctrl+L'),
    ('begin',         'Naar begin',             'Home'),
    ('einde',         'Naar einde',             'End'),
    ('links',         'Links / terug',          'Left'),
    ('rechts',        'Rechts / voor',          'Right'),
    ('ontsnappen',    'Sluiten / annuleren',    'Escape'),
]


class DatabasePanel(QWidget):

    shortcuts_saved = pyqtSignal()   # emitted after shortcut keys are changed

    def __init__(self):
        super().__init__()
        self._loading_actors = False
        self._loading_cats   = False
        self._build_ui()
        self.load_data()

    # ── UI ───────────────────────────────────────

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet("QSplitter::handle { background: #1e1e1e; height: 4px; }")
        splitter.addWidget(self._build_actors_widget())
        splitter.addWidget(self._build_categories_widget())
        splitter.addWidget(self._build_traits_widget())
        splitter.addWidget(self._build_film_cats_widget())
        splitter.addWidget(self._build_kleuren_widget())
        splitter.addWidget(self._build_shortcuts_widget())
        splitter.setSizes([400, 200, 160, 160, 120, 260])
        v.addWidget(splitter)

    # ── Actors widget ────────────────────────────

    def _build_actors_widget(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; }")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(10)

        lbl = QLabel("ACTEURS")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px;")
        b.addWidget(lbl)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: #444; font-size: 10px;")
        b.addWidget(self.lbl_count)

        b.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Zoeken...")
        self.search_input.setFixedWidth(200)
        self.search_input.textChanged.connect(self._filter_actors)
        b.addWidget(self.search_input)

        btn_refresh = QPushButton("↻  Vernieuwen")
        btn_refresh.setFixedHeight(28)
        btn_refresh.clicked.connect(self.load_data)
        b.addWidget(btn_refresh)

        btn_del = QPushButton("✕  Verwijder rij")
        btn_del.setObjectName("danger")
        btn_del.setFixedHeight(28)
        btn_del.clicked.connect(self._delete_selected_actor)
        b.addWidget(btn_del)

        v.addWidget(bar)

        self.table = QTableWidget()
        self.table.setColumnCount(COL_COUNT)
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(True)
        self.table.setStyleSheet(_TABLE_STYLE)

        hdr = self.table.horizontalHeader()
        for i, (_, _, width, _) in enumerate(COLUMNS):
            if i == COL_COUNT - 1:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                self.table.setColumnWidth(i, width)
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

        self.table.itemChanged.connect(self._on_actor_cell_changed)
        v.addWidget(self.table)

        status_bar = QFrame()
        status_bar.setFixedHeight(26)
        status_bar.setStyleSheet("QFrame { background: #080808; border-top: 1px solid #1a1a1a; }")
        sb = QHBoxLayout(status_bar)
        sb.setContentsMargins(12, 0, 12, 0)
        self.lbl_status = QLabel("Klik een cel om te bewerken · wijzigingen worden direct opgeslagen")
        self.lbl_status.setStyleSheet("color: #333; font-size: 10px;")
        sb.addWidget(self.lbl_status)
        sb.addStretch()
        v.addWidget(status_bar)

        return w

    # ── Categories widget ────────────────────────

    def _build_categories_widget(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; border-top: 1px solid #1e1e1e; }")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(10)

        lbl = QLabel("CATEGORIEËN")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px;")
        b.addWidget(lbl)

        self.lbl_cat_count = QLabel("")
        self.lbl_cat_count.setStyleSheet("color: #444; font-size: 10px;")
        b.addWidget(self.lbl_cat_count)

        b.addStretch()

        hint = QLabel("Iconen uit map:  iconen/")
        hint.setStyleSheet("color: #2a2a2a; font-size: 10px;")
        b.addWidget(hint)

        btn_add = QPushButton("＋  Toevoegen")
        btn_add.setFixedHeight(28)
        btn_add.clicked.connect(self._add_category)
        b.addWidget(btn_add)

        btn_del_cat = QPushButton("✕  Verwijder")
        btn_del_cat.setObjectName("danger")
        btn_del_cat.setFixedHeight(28)
        btn_del_cat.clicked.connect(self._delete_selected_category)
        b.addWidget(btn_del_cat)

        v.addWidget(bar)

        self.cat_table = QTableWidget()
        self.cat_table.setColumnCount(4)  # naam, icoon, voorbeeld, bovencat
        self.cat_table.setHorizontalHeaderLabels(['Naam', 'Icoon bestand', 'Voorbeeld', 'Bovencat.'])
        self.cat_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cat_table.setAlternatingRowColors(True)
        self.cat_table.setSortingEnabled(False)
        self.cat_table.verticalHeader().setDefaultSectionSize(36)
        self.cat_table.verticalHeader().hide()
        self.cat_table.setShowGrid(True)
        self.cat_table.setStyleSheet(_TABLE_STYLE)

        hdr = self.cat_table.horizontalHeader()
        self.cat_table.setColumnWidth(0, 180)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.cat_table.setColumnWidth(1, 220)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.cat_table.setColumnWidth(2, 48)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.cat_table.setColumnWidth(3, 160)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)

        self.cat_table.itemChanged.connect(self._on_cat_cell_changed)
        v.addWidget(self.cat_table)

        status_bar = QFrame()
        status_bar.setFixedHeight(26)
        status_bar.setStyleSheet("QFrame { background: #080808; border-top: 1px solid #1a1a1a; }")
        sb = QHBoxLayout(status_bar)
        sb.setContentsMargins(12, 0, 12, 0)
        self.lbl_cat_status = QLabel("Typ alleen de bestandsnaam, bijv.  introshot.png")
        self.lbl_cat_status.setStyleSheet("color: #333; font-size: 10px;")
        sb.addWidget(self.lbl_cat_status)
        sb.addStretch()
        v.addWidget(status_bar)

        return w

    # ── Traits widget ────────────────────────────

    def _build_traits_widget(self):
        from PyQt6.QtWidgets import QComboBox
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; border-top: 1px solid #1e1e1e; }")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(10)

        lbl = QLabel("ACTOR TRAITS  (sterke / zwakke kanten)")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 3px;")
        b.addWidget(lbl)
        b.addStretch()

        self._cmb_trait_type = QComboBox()
        self._cmb_trait_type.addItem("beide",    "beide")
        self._cmb_trait_type.addItem("positief", "positief")
        self._cmb_trait_type.addItem("negatief", "negatief")
        self._cmb_trait_type.setFixedWidth(80)
        b.addWidget(self._cmb_trait_type)

        self._inp_trait = QLineEdit()
        self._inp_trait.setPlaceholderText("Naam nieuw trait…")
        self._inp_trait.setFixedWidth(180)
        self._inp_trait.returnPressed.connect(self._add_trait)
        b.addWidget(self._inp_trait)

        btn_add = QPushButton("＋")
        btn_add.setFixedHeight(28)
        btn_add.clicked.connect(self._add_trait)
        b.addWidget(btn_add)

        btn_del = QPushButton("✕")
        btn_del.setObjectName("danger")
        btn_del.setFixedHeight(28)
        btn_del.clicked.connect(self._delete_trait)
        b.addWidget(btn_del)

        v.addWidget(bar)

        self.traits_table = QTableWidget()
        self.traits_table.setColumnCount(2)
        self.traits_table.setHorizontalHeaderLabels(['Naam', 'Weergave'])
        self.traits_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.traits_table.setAlternatingRowColors(True)
        self.traits_table.verticalHeader().setDefaultSectionSize(28)
        self.traits_table.verticalHeader().hide()
        self.traits_table.setShowGrid(True)
        self.traits_table.setSortingEnabled(True)
        self.traits_table.setStyleSheet(_TABLE_STYLE)
        hdr = self.traits_table.horizontalHeader()
        self.traits_table.setColumnWidth(0, 220)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.traits_table.setColumnWidth(1, 60)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.traits_table.itemChanged.connect(self._on_trait_cell_changed)
        v.addWidget(self.traits_table)
        return w

    def _build_film_cats_widget(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; border-top: 1px solid #1e1e1e; }")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(8)

        lbl = QLabel("FILM CATEGORIEËN")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 3px;")
        b.addWidget(lbl)
        b.addStretch()

        self._inp_filmcat = QLineEdit()
        self._inp_filmcat.setPlaceholderText("Naam…")
        self._inp_filmcat.setFixedWidth(130)
        self._inp_filmcat.returnPressed.connect(self._add_filmcat)
        b.addWidget(self._inp_filmcat)

        btn_add = QPushButton("＋")
        btn_add.setFixedHeight(28)
        btn_add.clicked.connect(self._add_filmcat)
        b.addWidget(btn_add)

        btn_del = QPushButton("✕")
        btn_del.setObjectName("danger")
        btn_del.setFixedHeight(28)
        btn_del.clicked.connect(self._delete_filmcat)
        b.addWidget(btn_del)

        v.addWidget(bar)

        self.filmcat_table = QTableWidget()
        self.filmcat_table.setColumnCount(3)
        self.filmcat_table.setHorizontalHeaderLabels(['Naam', 'Icoon bestand', 'Voorbeeld'])
        self.filmcat_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.filmcat_table.setAlternatingRowColors(True)
        self.filmcat_table.verticalHeader().setDefaultSectionSize(36)
        self.filmcat_table.verticalHeader().hide()
        self.filmcat_table.setShowGrid(True)
        self.filmcat_table.setSortingEnabled(False)
        self.filmcat_table.setStyleSheet(_TABLE_STYLE)
        fhdr = self.filmcat_table.horizontalHeader()
        self.filmcat_table.setColumnWidth(0, 160)
        fhdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.filmcat_table.setColumnWidth(1, 220)
        fhdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.filmcat_table.setColumnWidth(2, 48)
        fhdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.filmcat_table.itemChanged.connect(self._on_filmcat_changed)
        self._loading_filmcats = False
        v.addWidget(self.filmcat_table)

        fc_status = QFrame()
        fc_status.setFixedHeight(26)
        fc_status.setStyleSheet("QFrame { background: #080808; border-top: 1px solid #1a1a1a; }")
        fcs_h = QHBoxLayout(fc_status)
        fcs_h.setContentsMargins(12, 0, 12, 0)
        self.lbl_filmcat_status = QLabel("Typ alleen de bestandsnaam, bijv.  genre_drama.png")
        self.lbl_filmcat_status.setStyleSheet("color: #333; font-size: 10px;")
        fcs_h.addWidget(self.lbl_filmcat_status)
        fcs_h.addStretch()
        v.addWidget(fc_status)

        return w

    def _build_kleuren_widget(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; border-top: 1px solid #1e1e1e; }")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(8)

        lbl = QLabel("ACTOR KLEUREN")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 3px;")
        b.addWidget(lbl)
        b.addStretch()

        self._inp_kleur = QLineEdit()
        self._inp_kleur.setPlaceholderText("Naam…")
        self._inp_kleur.setFixedWidth(100)
        self._inp_kleur.returnPressed.connect(self._add_kleur)
        b.addWidget(self._inp_kleur)

        self._inp_kleur_hex = QLineEdit()
        self._inp_kleur_hex.setPlaceholderText("#rrggbb")
        self._inp_kleur_hex.setFixedWidth(70)
        b.addWidget(self._inp_kleur_hex)

        btn_add = QPushButton("＋")
        btn_add.setFixedHeight(28)
        btn_add.clicked.connect(self._add_kleur)
        b.addWidget(btn_add)

        btn_del = QPushButton("✕")
        btn_del.setObjectName("danger")
        btn_del.setFixedHeight(28)
        btn_del.clicked.connect(self._delete_kleur)
        b.addWidget(btn_del)

        v.addWidget(bar)

        self.kleuren_table = QTableWidget()
        self.kleuren_table.setColumnCount(2)
        self.kleuren_table.setHorizontalHeaderLabels(['Naam', 'Hex'])
        self.kleuren_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.kleuren_table.setAlternatingRowColors(True)
        self.kleuren_table.verticalHeader().setDefaultSectionSize(28)
        self.kleuren_table.verticalHeader().hide()
        self.kleuren_table.setShowGrid(True)
        self.kleuren_table.setStyleSheet(_TABLE_STYLE)
        hdr = self.kleuren_table.horizontalHeader()
        self.kleuren_table.setColumnWidth(0, 130)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.kleuren_table.setColumnWidth(1, 70)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.kleuren_table.itemChanged.connect(self._on_kleur_changed)
        self._loading_kleuren = False
        v.addWidget(self.kleuren_table)
        return w

    # ── Data ─────────────────────────────────────

    def load_data(self):
        self._load_actors()
        self._load_categories()
        self._load_traits()
        self._load_filmcats()
        self._load_kleuren()

    def _load_actors(self):
        self._loading_actors = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for actor in db.get_all_actors():
            meta = {}
            if actor.get('notes'):
                try:
                    meta = json.loads(actor['notes'])
                except (ValueError, TypeError):
                    pass

            row = self.table.rowCount()
            self.table.insertRow(row)

            for col, (field, _, _, editable) in enumerate(COLUMNS):
                val = actor.get('name', '') if field == 'name' else meta.get(field, '')
                cell = QTableWidgetItem(str(val))
                cell.setData(ID_ROLE, actor['id'])
                if not editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    cell.setForeground(QColor('#444'))
                self.table.setItem(row, col, cell)

        self._loading_actors = False
        self.table.setSortingEnabled(True)
        self._update_actor_count()

    def _load_categories(self):
        self._loading_cats = True
        self.cat_table.setRowCount(0)

        cats = db.get_all_categories()
        name_map = {c['id']: c['name'] for c in cats}
        # Sort: roots first (no parent), then subcategories grouped under their parent
        roots = [c for c in cats if not c.get('parent_id')]
        subs  = [c for c in cats if c.get('parent_id')]
        ordered = []
        for root in sorted(roots, key=lambda x: x['name'].lower()):
            ordered.append((root, ''))
            for sub in sorted(
                [s for s in subs if s['parent_id'] == root['id']],
                key=lambda x: x['name'].lower()
            ):
                ordered.append((sub, root['name']))
        # Orphaned subcategories (parent deleted) at end
        known_root_ids = {r['id'] for r in roots}
        for sub in subs:
            if sub['parent_id'] not in known_root_ids:
                ordered.append((sub, f"? (id {sub['parent_id']})"))

        for cat, parent_name in ordered:
            self._append_cat_row(cat, parent_name)

        self._loading_cats = False
        self._update_cat_count()

    def _append_cat_row(self, cat: dict, parent_name: str = ''):
        icon_path = cat.get('icon_path', '')
        icon_file = Path(icon_path).name if icon_path else ''

        row = self.cat_table.rowCount()
        self.cat_table.insertRow(row)

        name_cell = QTableWidgetItem(cat.get('name', ''))
        name_cell.setData(ID_ROLE, cat['id'])
        self.cat_table.setItem(row, 0, name_cell)

        file_cell = QTableWidgetItem(icon_file)
        file_cell.setData(ID_ROLE, cat['id'])
        self.cat_table.setItem(row, 1, file_cell)

        self._set_icon_preview(row, icon_file)

        parent_cell = QTableWidgetItem(parent_name)
        parent_cell.setFlags(parent_cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
        parent_cell.setForeground(QColor('#666' if parent_name else '#2a2a2a'))
        self.cat_table.setItem(row, 3, parent_cell)

    def _set_icon_preview(self, row: int, icon_file: str):
        preview_cell = QTableWidgetItem()
        preview_cell.setFlags(preview_cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if icon_file:
            full = ICONEN_DIR / icon_file
            if full.exists():
                pix = QPixmap(str(full)).scaled(
                    28, 28,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                preview_cell.setData(Qt.ItemDataRole.DecorationRole, pix)
            else:
                preview_cell.setForeground(QColor('#6b2a2a'))
                preview_cell.setText('?')
        self.cat_table.setItem(row, 2, preview_cell)

    # ── Counts ───────────────────────────────────

    def _update_actor_count(self):
        visible = sum(1 for r in range(self.table.rowCount()) if not self.table.isRowHidden(r))
        self.lbl_count.setText(f"{visible} / {self.table.rowCount()} acteurs")

    def _update_cat_count(self):
        n = self.cat_table.rowCount()
        self.lbl_cat_count.setText(f"{n} {'categorie' if n == 1 else 'categorieën'}")

    # ── Actor edit ───────────────────────────────

    def _on_actor_cell_changed(self, item):
        if self._loading_actors:
            return
        row = item.row()
        actor_id = self._get_actor_id(row)
        if actor_id is None:
            return
        meta = {}
        for col, (field, _, _, _) in enumerate(COLUMNS):
            if field == 'name':
                continue
            cell = self.table.item(row, col)
            val = cell.text().strip() if cell else ''
            if val:
                meta[field] = val
        db.update_actor_meta(actor_id, meta)
        self.lbl_status.setText(f"Opgeslagen — rij {row + 1}")

    def _get_actor_id(self, row):
        for col in range(COL_COUNT):
            cell = self.table.item(row, col)
            if cell:
                return cell.data(ID_ROLE)
        return None

    # ── Actor delete ─────────────────────────────

    def _delete_selected_actor(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        names = []
        for r in rows:
            c0 = self.table.item(r, 0)
            c1 = self.table.item(r, 1)
            names.append(f"{c0.text() if c0 else ''} {c1.text() if c1 else ''}".strip() or f"rij {r + 1}")
        reply = QMessageBox.question(
            self, "Verwijder",
            f"Verwijder {len(rows)} acteur(s)?\n" + "\n".join(f"• {n}" for n in names),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._loading_actors = True
        for r in rows:
            actor_id = self._get_actor_id(r)
            if actor_id:
                db.delete_actor(actor_id)
            self.table.removeRow(r)
        self._loading_actors = False
        self._update_actor_count()
        self.lbl_status.setText(f"{len(rows)} acteur(s) verwijderd")

    # ── Actor filter ─────────────────────────────

    def _filter_actors(self, query: str):
        q = query.lower()
        for r in range(self.table.rowCount()):
            if not q:
                self.table.setRowHidden(r, False)
                continue
            match = any(
                self.table.item(r, c) and q in self.table.item(r, c).text().lower()
                for c in range(COL_COUNT - 1)
            )
            self.table.setRowHidden(r, not match)
        self._update_actor_count()

    # ── Category edit ────────────────────────────

    def _on_cat_cell_changed(self, item):
        if self._loading_cats:
            return
        row = item.row()
        cat_id = self._get_cat_id(row)
        if cat_id is None:
            return

        name_cell = self.cat_table.item(row, 0)
        file_cell = self.cat_table.item(row, 1)
        name      = name_cell.text().strip() if name_cell else ''
        icon_file = file_cell.text().strip()  if file_cell else ''
        icon_path = _icon_path(icon_file)

        db.update_category(cat_id, name, icon_path)
        self._loading_cats = True
        self._set_icon_preview(row, icon_file)
        self._loading_cats = False
        self.lbl_cat_status.setText(f"Opgeslagen — '{name}'")

    def _get_cat_id(self, row) -> int | None:
        for col in range(2):
            cell = self.cat_table.item(row, col)
            if cell:
                v = cell.data(ID_ROLE)
                if v is not None:
                    return v
        return None

    # ── Category add ─────────────────────────────

    def _add_category(self):
        cat_id = db.create_category('Nieuwe categorie', '')
        cat = {'id': cat_id, 'name': 'Nieuwe categorie', 'icon_path': ''}
        self._loading_cats = True
        self._append_cat_row(cat)
        self._loading_cats = False
        self._update_cat_count()
        row = self.cat_table.rowCount() - 1
        self.cat_table.scrollToItem(self.cat_table.item(row, 0))
        self.cat_table.editItem(self.cat_table.item(row, 0))

    # ── Category delete ──────────────────────────

    def _delete_selected_category(self):
        rows = sorted({idx.row() for idx in self.cat_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        names = []
        for r in rows:
            c = self.cat_table.item(r, 0)
            names.append(c.text() if c else f"rij {r + 1}")
        reply = QMessageBox.question(
            self, "Verwijder",
            f"Verwijder {len(rows)} categorie(ën)?\n" + "\n".join(f"• {n}" for n in names),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._loading_cats = True
        for r in rows:
            cat_id = self._get_cat_id(r)
            if cat_id:
                db.delete_category(cat_id)
            self.cat_table.removeRow(r)
        self._loading_cats = False
        self._update_cat_count()
        self.lbl_cat_status.setText(f"{len(rows)} categorie(ën) verwijderd")

    # ── Traits ───────────────────────────────────

    def _load_traits(self):
        self.traits_table.blockSignals(True)
        self.traits_table.setSortingEnabled(False)
        self.traits_table.setRowCount(0)
        for tt in db.get_actor_trait_types():
            row = self.traits_table.rowCount()
            self.traits_table.insertRow(row)
            n = QTableWidgetItem(tt['naam'])
            n.setData(ID_ROLE, tt['id'])
            self.traits_table.setItem(row, 0, n)
            t = QTableWidgetItem(tt['type'])
            t.setData(ID_ROLE, tt['id'])
            self.traits_table.setItem(row, 1, t)
        self.traits_table.setSortingEnabled(True)
        self.traits_table.blockSignals(False)

    def _on_trait_cell_changed(self, item):
        row    = item.row()
        tid    = item.data(ID_ROLE)
        if tid is None:
            return
        naam_c = self.traits_table.item(row, 0)
        type_c = self.traits_table.item(row, 1)
        naam   = naam_c.text().strip() if naam_c else ''
        ttype  = type_c.text().strip() if type_c else 'beide'
        if ttype not in ('beide', 'positief', 'negatief'):
            ttype = 'beide'
        if naam:
            with db._db() as conn:
                conn.execute(
                    "UPDATE actor_trait_types SET naam=?, type=? WHERE id=?",
                    (naam, ttype, tid)
                )
                conn.commit()

    def _add_trait(self):
        naam  = self._inp_trait.text().strip()
        ttype = self._cmb_trait_type.currentData()
        if not naam:
            return
        db.create_actor_trait_type(naam, ttype)
        self._inp_trait.clear()
        self._load_traits()

    def _delete_trait(self):
        rows = sorted({idx.row() for idx in self.traits_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            cell = self.traits_table.item(r, 0)
            tid  = cell.data(ID_ROLE) if cell else None
            if tid:
                db.delete_actor_trait_type(tid)
        self._load_traits()

    # ── Film Categorieën ──────────────────────────

    def _load_filmcats(self):
        self._loading_filmcats = True
        self.filmcat_table.blockSignals(True)
        self.filmcat_table.setRowCount(0)
        for fc in db.get_film_categorie_types():
            row = self.filmcat_table.rowCount()
            self.filmcat_table.insertRow(row)

            naam_c = QTableWidgetItem(fc['naam'])
            naam_c.setData(ID_ROLE, fc['id'])
            self.filmcat_table.setItem(row, 0, naam_c)

            icon_path = fc.get('icon_path', '') or ''
            icon_file = Path(icon_path).name if icon_path else ''
            file_c = QTableWidgetItem(icon_file)
            file_c.setData(ID_ROLE, fc['id'])
            self.filmcat_table.setItem(row, 1, file_c)

            self._set_filmcat_icon_preview(row, icon_file)

        self.filmcat_table.blockSignals(False)
        self._loading_filmcats = False

    def _set_filmcat_icon_preview(self, row: int, icon_file: str):
        preview = QTableWidgetItem()
        preview.setFlags(preview.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if icon_file:
            full = ICONEN_DIR / icon_file
            if full.exists():
                pix = QPixmap(str(full)).scaled(
                    28, 28,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                preview.setData(Qt.ItemDataRole.DecorationRole, pix)
            else:
                preview.setForeground(QColor('#6b2a2a'))
                preview.setText('?')
        self.filmcat_table.setItem(row, 2, preview)

    def _on_filmcat_changed(self, item):
        if self._loading_filmcats:
            return
        row     = item.row()
        fid     = item.data(ID_ROLE)
        if fid is None:
            return
        naam_c  = self.filmcat_table.item(row, 0)
        file_c  = self.filmcat_table.item(row, 1)
        naam      = naam_c.text().strip() if naam_c else ''
        icon_file = file_c.text().strip()  if file_c else ''
        icon_path = _icon_path(icon_file)
        if naam:
            db.update_film_categorie_type(fid, naam, icon_path)
            self._loading_filmcats = True
            self._set_filmcat_icon_preview(row, icon_file)
            self._loading_filmcats = False
            self.lbl_filmcat_status.setText(f"Opgeslagen — '{naam}'")

    def _add_filmcat(self):
        naam = self._inp_filmcat.text().strip()
        if not naam:
            return
        db.create_film_categorie_type(naam)
        self._inp_filmcat.clear()
        self._load_filmcats()

    def _delete_filmcat(self):
        rows = sorted({idx.row() for idx in self.filmcat_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            cell = self.filmcat_table.item(r, 0)
            fid  = cell.data(ID_ROLE) if cell else None
            if fid:
                db.delete_film_categorie_type(fid)
        self._load_filmcats()

    # ── Kleuren ───────────────────────────────────

    def _load_kleuren(self):
        self._loading_kleuren = True
        self.kleuren_table.blockSignals(True)
        self.kleuren_table.setRowCount(0)
        for k in db.get_actor_kleuren():
            row = self.kleuren_table.rowCount()
            self.kleuren_table.insertRow(row)
            n = QTableWidgetItem(k['naam'])
            n.setData(ID_ROLE, k['id'])
            self.kleuren_table.setItem(row, 0, n)
            h = QTableWidgetItem(k.get('hex', ''))
            h.setData(ID_ROLE, k['id'])
            self.kleuren_table.setItem(row, 1, h)
        self.kleuren_table.blockSignals(False)
        self._loading_kleuren = False

    def _on_kleur_changed(self, item):
        if self._loading_kleuren:
            return
        row   = item.row()
        kid   = item.data(ID_ROLE)
        if kid is None:
            return
        naam_c = self.kleuren_table.item(row, 0)
        hex_c  = self.kleuren_table.item(row, 1)
        naam   = naam_c.text().strip() if naam_c else ''
        hex_v  = hex_c.text().strip()  if hex_c  else ''
        if naam:
            with db._db() as conn:
                conn.execute(
                    "UPDATE actor_kleuren SET naam=?, hex=? WHERE id=?",
                    (naam, hex_v, kid)
                )
                conn.commit()

    def _add_kleur(self):
        naam = self._inp_kleur.text().strip()
        hex_v = self._inp_kleur_hex.text().strip()
        if not naam:
            return
        db.create_actor_kleur(naam, hex_v)
        self._inp_kleur.clear()
        self._inp_kleur_hex.clear()
        self._load_kleuren()

    def _delete_kleur(self):
        rows = sorted({idx.row() for idx in self.kleuren_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            cell = self.kleuren_table.item(r, 0)
            kid  = cell.data(ID_ROLE) if cell else None
            if kid:
                db.delete_actor_kleur(kid)
        self._load_kleuren()

    # ── Sneltoetsen ───────────────────────────────

    def _build_shortcuts_widget(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Header bar
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet(
            "QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e;"
            "  border-top: 1px solid #1e1e1e; }"
        )
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(10)

        lbl_hdr = QLabel("SNELTOETSEN")
        lbl_hdr.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px;")
        b.addWidget(lbl_hdr)

        hint = QLabel("Meerdere toetsen per actie: komma-gescheiden  (bijv. M,K)")
        hint.setStyleSheet("color: #2a2a2a; font-size: 10px;")
        b.addWidget(hint)

        b.addStretch()

        btn_reset = QPushButton("↺  Standaard")
        btn_reset.setFixedHeight(28)
        btn_reset.setStyleSheet(
            "QPushButton { background: #111; border: 1px solid #252525; border-radius: 3px;"
            "  color: #444; font-size: 10px; padding: 0 8px; }"
            "QPushButton:hover { border-color: #888; color: #aaa; }"
        )
        btn_reset.clicked.connect(self._reset_shortcuts)
        b.addWidget(btn_reset)

        v.addWidget(bar)

        # Scrollable grid of label + line-edit
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #0e0e0e; }"
            "QScrollBar:vertical { background: #0a0a0a; width: 8px; }"
            "QScrollBar::handle:vertical { background: #2a2a2a; border-radius: 4px; }"
        )

        inner = QWidget()
        inner.setStyleSheet("background: #0e0e0e;")
        grid = QGridLayout(inner)
        grid.setContentsMargins(12, 6, 12, 6)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)

        # Column headers
        _HDR = "color: #333; font-size: 9px; letter-spacing: 3px; padding-bottom: 4px;"
        for col_i, txt in enumerate(('ACTIE', 'TOETS(EN)')):
            h = QLabel(txt)
            h.setStyleSheet(_HDR)
            grid.addWidget(h, 0, col_i)

        _EDIT_SS = (
            "QLineEdit { background: #111; border: 1px solid #1e1e1e; border-radius: 3px;"
            "  color: #aaa; font-size: 11px; padding: 2px 5px; }"
            "QLineEdit:focus { border-color: #e8b86d; color: #e8b86d; }"
        )
        _LBL_SS = "color: #555; font-size: 11px; padding: 1px 0;"

        self._sc_edits: list = []   # (action, QLineEdit)

        for row_i, (action, label, default) in enumerate(SHORTCUT_DEFS, start=1):
            lbl = QLabel(label)
            lbl.setStyleSheet(_LBL_SS)

            current = db.get_setting(f'shortcut_{action}', default)
            edit = QLineEdit(current)
            edit.setStyleSheet(_EDIT_SS)
            edit.setPlaceholderText(default)
            edit.setToolTip(f"Standaard: {default}  •  meerdere toetsen: komma-gescheiden")
            edit.setFixedHeight(24)
            edit.editingFinished.connect(
                lambda a=action, e=edit: self._save_shortcut(a, e)
            )

            grid.addWidget(lbl,  row_i, 0)
            grid.addWidget(edit, row_i, 1)
            self._sc_edits.append((action, edit))

        scroll.setWidget(inner)
        v.addWidget(scroll, stretch=1)
        return w

    def _save_shortcut(self, action: str, edit: QLineEdit):
        """Sla één sneltoets op en signaleer herladen."""
        db.set_setting(f'shortcut_{action}', edit.text().strip())
        self.shortcuts_saved.emit()

    def _reset_shortcuts(self):
        """Herstel alle sneltoetsen naar standaard."""
        for action, edit in self._sc_edits:
            default = next(d for a, _, d in SHORTCUT_DEFS if a == action)
            edit.blockSignals(True)
            edit.setText(default)
            edit.blockSignals(False)
            db.set_setting(f'shortcut_{action}', default)
        self.shortcuts_saved.emit()
