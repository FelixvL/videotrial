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
    QMessageBox, QHeaderView, QAbstractItemView, QSplitter
)
from PyQt6.QtCore import Qt
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


class DatabasePanel(QWidget):

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
        splitter.setSizes([500, 260])
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
        self.cat_table.setColumnCount(CAT_COL_COUNT + 1)  # +1 for icon preview
        self.cat_table.setHorizontalHeaderLabels(['Naam', 'Icoon bestand', 'Voorbeeld'])
        self.cat_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cat_table.setAlternatingRowColors(True)
        self.cat_table.setSortingEnabled(False)
        self.cat_table.verticalHeader().setDefaultSectionSize(36)
        self.cat_table.verticalHeader().hide()
        self.cat_table.setShowGrid(True)
        self.cat_table.setStyleSheet(_TABLE_STYLE)

        hdr = self.cat_table.horizontalHeader()
        self.cat_table.setColumnWidth(0, 200)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.cat_table.setColumnWidth(1, 260)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.cat_table.setColumnWidth(2, 48)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)

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

    # ── Data ─────────────────────────────────────

    def load_data(self):
        self._load_actors()
        self._load_categories()

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

        for cat in db.get_all_categories():
            self._append_cat_row(cat)

        self._loading_cats = False
        self._update_cat_count()

    def _append_cat_row(self, cat: dict):
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
