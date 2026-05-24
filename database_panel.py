#!/usr/bin/env python3
"""
CineMarker — Database editor tab
"""

import json

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTableWidget, QTableWidgetItem, QFrame,
    QMessageBox, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QSortFilterProxyModel
from PyQt6.QtGui import QColor, QFont

import database as db


COLUMNS = [
    ('voornaam',   'Voornaam',     120, True),
    ('achternaam', 'Achternaam',   130, True),
    ('kleur',      'Kleur',         60, True),
    ('grootte',    'Grootte',       70, True),
    ('rating',     'Rating',        60, True),
    ('decennia',   'Decennia',      80, True),
    ('name',       'Bestandsnaam', 180, False),  # read-only
]
COL_COUNT = len(COLUMNS)
ID_ROLE   = Qt.ItemDataRole.UserRole


class DatabasePanel(QWidget):

    def __init__(self):
        super().__init__()
        self._loading = False
        self._build_ui()
        self.load_data()

    # ── UI ───────────────────────────────────────

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Toolbar
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; }")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(10)

        lbl = QLabel("DATABASE")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px;")
        b.addWidget(lbl)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: #444; font-size: 10px;")
        b.addWidget(self.lbl_count)

        b.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Zoeken...")
        self.search_input.setFixedWidth(200)
        self.search_input.textChanged.connect(self._filter)
        b.addWidget(self.search_input)

        btn_refresh = QPushButton("↻  Vernieuwen")
        btn_refresh.setFixedHeight(28)
        btn_refresh.clicked.connect(self.load_data)
        b.addWidget(btn_refresh)

        btn_del = QPushButton("✕  Verwijder rij")
        btn_del.setObjectName("danger")
        btn_del.setFixedHeight(28)
        btn_del.clicked.connect(self._delete_selected)
        b.addWidget(btn_del)

        v.addWidget(bar)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(COL_COUNT)
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(True)
        self.table.setStyleSheet("""
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
        """)

        # Column widths
        hdr = self.table.horizontalHeader()
        for i, (_, _, width, _) in enumerate(COLUMNS):
            if i == COL_COUNT - 1:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                self.table.setColumnWidth(i, width)
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

        self.table.itemChanged.connect(self._on_cell_changed)
        v.addWidget(self.table)

        # Status bar
        status_bar = QFrame()
        status_bar.setFixedHeight(28)
        status_bar.setStyleSheet("QFrame { background: #080808; border-top: 1px solid #1a1a1a; }")
        sb = QHBoxLayout(status_bar)
        sb.setContentsMargins(12, 0, 12, 0)
        self.lbl_status = QLabel("Klik een cel om te bewerken · wijzigingen worden direct opgeslagen")
        self.lbl_status.setStyleSheet("color: #333; font-size: 10px;")
        sb.addWidget(self.lbl_status)
        sb.addStretch()
        v.addWidget(status_bar)

    # ── Data ─────────────────────────────────────

    def load_data(self):
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        actors = db.get_all_actors()
        for actor in actors:
            meta = {}
            if actor.get('notes'):
                try:
                    meta = json.loads(actor['notes'])
                except (ValueError, TypeError):
                    pass

            row = self.table.rowCount()
            self.table.insertRow(row)

            for col, (field, _, _, editable) in enumerate(COLUMNS):
                if field == 'name':
                    val = actor.get('name', '')
                else:
                    val = meta.get(field, '')

                cell = QTableWidgetItem(str(val))
                cell.setData(ID_ROLE, actor['id'])

                if not editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    cell.setForeground(QColor('#444'))

                self.table.setItem(row, col, cell)

        self._loading = False
        self.table.setSortingEnabled(True)
        self._update_count()

    def _update_count(self):
        visible = sum(
            1 for r in range(self.table.rowCount())
            if not self.table.isRowHidden(r)
        )
        total = self.table.rowCount()
        self.lbl_count.setText(f"{visible} / {total} acteurs")

    # ── Edit ─────────────────────────────────────

    def _on_cell_changed(self, item):
        if self._loading:
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

    # ── Delete ───────────────────────────────────

    def _delete_selected(self):
        rows = sorted(
            {idx.row() for idx in self.table.selectedIndexes()},
            reverse=True
        )
        if not rows:
            return

        names = []
        for r in rows:
            cell = self.table.item(r, 0)
            n0 = cell.text() if cell else ''
            cell2 = self.table.item(r, 1)
            n1 = cell2.text() if cell2 else ''
            names.append(f"{n0} {n1}".strip() or f"rij {r + 1}")

        reply = QMessageBox.question(
            self, "Verwijder",
            f"Verwijder {len(rows)} acteur(s)?\n" + "\n".join(f"• {n}" for n in names),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._loading = True
        for r in rows:
            actor_id = self._get_actor_id(r)
            if actor_id:
                db.delete_actor(actor_id)
            self.table.removeRow(r)
        self._loading = False
        self._update_count()
        self.lbl_status.setText(f"{len(rows)} acteur(s) verwijderd")

    # ── Filter ───────────────────────────────────

    def _filter(self, query: str):
        q = query.lower()
        for r in range(self.table.rowCount()):
            if not q:
                self.table.setRowHidden(r, False)
                continue
            match = False
            for col in range(COL_COUNT - 1):  # skip bestandsnaam col
                cell = self.table.item(r, col)
                if cell and q in cell.text().lower():
                    match = True
                    break
            self.table.setRowHidden(r, not match)
        self._update_count()
