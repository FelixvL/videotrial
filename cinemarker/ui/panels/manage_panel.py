"""
CineMarker — Beheer: acteurs, markercategorieën, acteur-eigenschappen (traits),
filmcategorieën en kleuren. De enige plek voor dit beheer; elke wijziging zendt
`taxonomy_changed` of `actors_changed`.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QColorDialog, QComboBox, QFileDialog, QHeaderView, QLineEdit,
                             QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import database as db
from ...core.actors import notes_of
from ...core.formats import DECADE_OPTIONS, clamp_grootte, to_int
from ...core.paths import ICONEN_DIR
from ..signals import AppSignals
from ..theme import icons, tokens as T
from ..widgets.buttons import IconButton, ToolButton
from ..widgets.dialogs import ask_text, confirm, warning
from ..widgets.search_field import SearchField
from ..widgets.toolbar import PanelToolbar

_log = logging.getLogger("cinemarker.manage")
ID_ROLE = Qt.ItemDataRole.UserRole

ACTOR_COLUMNS: tuple[tuple[str, str, int, bool], ...] = (
    ("voornaam", "Voornaam", 140, True),
    ("achternaam", "Achternaam", 150, True),
    ("kleur", "Kleur", 70, True),
    ("grootte", "Grootte", 70, True),
    ("rating", "Rating", 70, True),
    ("decennia", "Decennia", 80, True),
    ("piek_start", "Piek start", 90, True),
    ("piek_eind", "Piek eind", 90, True),
    ("name", "Bestandsnaam", 200, False),
)


def _table(headers: list[str], *, sortable: bool = True, row_h: int = 30) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(row_h)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                      | QAbstractItemView.EditTrigger.AnyKeyPressed)
    t.setSortingEnabled(sortable)
    t.horizontalHeader().setStretchLastSection(True)
    t.setAlternatingRowColors(True)
    return t


def _row_id(table: QTableWidget, row: int) -> int | None:
    for col in range(table.columnCount()):
        it = table.item(row, col)
        if it is not None and it.data(ID_ROLE) is not None:
            return int(it.data(ID_ROLE))
    return None


def _selected_rows(table: QTableWidget) -> list[int]:
    return sorted({i.row() for i in table.selectedIndexes()}, reverse=True)


def _copy_icon_to_store(src: str) -> str:
    """Kopieer een gekozen icoonbestand naar TE_KOPIEREN/iconen/ en geef het nieuwe pad terug."""
    ICONEN_DIR.mkdir(parents=True, exist_ok=True)
    dest = ICONEN_DIR / Path(src).name
    if Path(src).resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return str(dest)


class _Section(QWidget):
    """Werkbalk + tabel; de subklassen vullen de werkbalk en de tabel."""

    def __init__(self, title: str, headers: list[str], *, sortable: bool = True) -> None:
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.toolbar = PanelToolbar(title)
        self.toolbar.add_stretch()
        v.addWidget(self.toolbar)
        self.table = _table(headers, sortable=sortable)
        v.addWidget(self.table, 1)
        self._loading = False

    def _delete_rows(self, label: str, delete_fn) -> int:
        rows = _selected_rows(self.table)
        if not rows:
            return 0
        names = [self.table.item(r, 0).text() if self.table.item(r, 0) else f"rij {r + 1}" for r in rows]
        if not confirm(self, f"{label} verwijderen", f"{len(rows)} {label.lower()} verwijderen?\n\n" +
                       "\n".join(f"•  {n}" for n in names)):
            return 0
        self._loading = True
        for r in rows:
            rid = _row_id(self.table, r)
            if rid is not None:
                delete_fn(rid)
            self.table.removeRow(r)
        self._loading = False
        return len(rows)


class ActorsSection(_Section):
    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__("Acteurs", [h for _, h, _, _ in ACTOR_COLUMNS])
        for i, (_, _, w, _) in enumerate(ACTOR_COLUMNS):
            self.table.setColumnWidth(i, w)
        self.search = SearchField("Zoeken…", debounce_ms=0, width=220, compact=True)
        self.search.textChanged.connect(self._filter)
        self.toolbar.add_widget(self.search)
        btn_del = ToolButton("Verwijderen", "trash", variant="danger", color=T.RED)
        btn_del.clicked.connect(self._delete)
        self.toolbar.add_widget(btn_del)
        self.table.itemChanged.connect(self._on_changed)
        self.load()

    def load(self) -> None:
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for a in db.get_all_actors():
            meta = notes_of(a)
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, (field, _, _, editable) in enumerate(ACTOR_COLUMNS):
                val = a.get("name", "") if field == "name" else str(meta.get(field) or "")
                cell = QTableWidgetItem(val)
                cell.setData(ID_ROLE, a["id"])
                if not editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    cell.setForeground(QColor(T.TEXT_DIM))
                self.table.setItem(row, col, cell)
        self.table.setSortingEnabled(True)
        self._loading = False
        self._update_count()

    def _update_count(self) -> None:
        visible = sum(1 for r in range(self.table.rowCount()) if not self.table.isRowHidden(r))
        self.toolbar.set_count(f"{visible} / {self.table.rowCount()}")

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        for r in range(self.table.rowCount()):
            hit = not q or any(q in (self.table.item(r, c).text().lower() if self.table.item(r, c) else "")
                               for c in range(self.table.columnCount()))
            self.table.setRowHidden(r, not hit)
        self._update_count()

    def _on_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        aid = _row_id(self.table, item.row())
        field = ACTOR_COLUMNS[item.column()][0]
        if aid is None or field == "name":
            return
        val = item.text().strip()
        if field == "grootte" and val:
            val = str(clamp_grootte(val) or "")
        if field == "rating" and val:
            v = to_int(val, 0)
            val = str(v) if 1 <= v <= 9 else ""
        if field == "decennia" and val and val not in {k for k, _ in DECADE_OPTIONS}:
            val = ""
        if val != item.text().strip():
            self._loading = True
            item.setText(val)
            self._loading = False
        db.update_actor_meta(aid, {field: val})          # merge: andere velden blijven
        self.changed.emit()

    def _delete(self) -> None:
        if self._delete_rows("Acteurs", db.delete_actor):
            self._update_count()
            self.changed.emit()


class CategoriesSection(_Section):
    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__("Markercategorieën", ["Naam", "Icoon", "Bovencategorie"], sortable=False)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 260)
        self.table.setIconSize(icons.pixmap("tag", 24).size())
        btn_root = ToolButton("Hoofdcategorie", "plus")
        btn_root.clicked.connect(lambda: self._add(parent=None))
        btn_sub = ToolButton("Subcategorie", "corner-down-left", tooltip="Subcategorie onder de geselecteerde hoofdcategorie")
        btn_sub.clicked.connect(self._add_sub)
        btn_icon = ToolButton("Icoon kiezen…", "image", tooltip="Afbeelding kiezen voor de geselecteerde categorie")
        btn_icon.clicked.connect(self._pick_icon)
        btn_del = ToolButton("Verwijderen", "trash", variant="danger", color=T.RED)
        btn_del.clicked.connect(self._delete)
        self.toolbar.add_widgets(btn_root, btn_sub, btn_icon, btn_del)
        self.table.itemChanged.connect(self._on_changed)
        self.load()

    def load(self) -> None:
        self._loading = True
        self.table.setRowCount(0)
        cats = db.get_all_categories()
        roots = sorted([c for c in cats if not c.get("parent_id")], key=lambda c: c["name"].lower())
        subs = [c for c in cats if c.get("parent_id")]
        ordered: list[tuple[dict, str]] = []
        for r in roots:
            ordered.append((r, ""))
            for s in sorted([x for x in subs if x["parent_id"] == r["id"]], key=lambda c: c["name"].lower()):
                ordered.append((s, r["name"]))
        known = {r["id"] for r in roots}
        for s in subs:
            if s["parent_id"] not in known:
                ordered.append((s, f"? (id {s['parent_id']})"))
        for c, parent in ordered:
            self._append(c, parent)
        self._loading = False
        self.toolbar.set_count(str(self.table.rowCount()))

    def _append(self, c: dict, parent: str) -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        name = QTableWidgetItem(("    ↳  " if parent else "") + c["name"])
        name.setData(ID_ROLE, c["id"])
        self.table.setItem(row, 0, name)
        ip = c.get("icon_path") or ""
        icon_cell = QTableWidgetItem(Path(ip).name if ip else "")
        icon_cell.setData(ID_ROLE, c["id"])
        icon_cell.setFlags(icon_cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
        ic = icons.file_icon(ip, 24) if ip else None
        if ic is not None:
            icon_cell.setIcon(ic)
        elif ip:
            icon_cell.setIcon(icons.icon("alert", 16, T.RED))
            icon_cell.setToolTip(f"Bestand niet gevonden: {ip}")
        self.table.setItem(row, 1, icon_cell)
        parent_cell = QTableWidgetItem(parent)
        parent_cell.setFlags(parent_cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
        parent_cell.setForeground(QColor(T.TEXT_DIM))
        self.table.setItem(row, 2, parent_cell)
        return row

    def _on_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        cid = _row_id(self.table, item.row())
        if cid is None:
            return
        cat = next((c for c in db.get_all_categories() if c["id"] == cid), None)
        name = item.text().replace("↳", "").strip()
        if cat and name:
            db.update_category(cid, name, cat.get("icon_path") or "")
            self.changed.emit()
        self.load()

    def _add(self, parent: int | None) -> None:
        name = ask_text(self, "Nieuwe categorie", "Naam:")
        if not name:
            return
        db.create_category(name, "", parent)
        self.load()
        self.changed.emit()

    def _add_sub(self) -> None:
        rows = _selected_rows(self.table)
        if not rows:
            warning(self, "Subcategorie", "Selecteer eerst een hoofdcategorie.")
            return
        cid = _row_id(self.table, rows[-1])
        cat = next((c for c in db.get_all_categories() if c["id"] == cid), None)
        if not cat or cat.get("parent_id"):
            warning(self, "Subcategorie", "Selecteer een hoofdcategorie (geen subcategorie).")
            return
        self._add(parent=cid)

    def _pick_icon(self) -> None:
        rows = _selected_rows(self.table)
        if not rows:
            warning(self, "Icoon kiezen", "Selecteer eerst een categorie.")
            return
        cid = _row_id(self.table, rows[-1])
        cat = next((c for c in db.get_all_categories() if c["id"] == cid), None)
        if not cat:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Icoon kiezen", str(ICONEN_DIR),
                                              "Afbeeldingen (*.png *.svg *.jpg *.jpeg *.webp);;Alle bestanden (*)")
        if not path:
            return
        try:
            stored = _copy_icon_to_store(path)
        except OSError as e:
            warning(self, "Icoon", f"Kan icoon niet kopiëren:\n{e}")
            return
        db.update_category(cid, cat["name"], stored)
        self.load()
        self.changed.emit()

    def _delete(self) -> None:
        if self._delete_rows("Categorieën", db.delete_category):
            self.load()
            self.changed.emit()


class TraitsSection(_Section):
    changed = pyqtSignal()
    TYPES = (("beide", "Beide"), ("positief", "Sterk"), ("negatief", "Zwak"))

    def __init__(self) -> None:
        super().__init__("Acteur-eigenschappen", ["Naam", "Weergave"])
        self.table.setColumnWidth(0, 300)
        self.inp = QLineEdit()
        self.inp.setProperty("role", "compact")
        self.inp.setPlaceholderText("Nieuwe eigenschap…")
        self.inp.setFixedWidth(200)
        self.inp.returnPressed.connect(self._add)
        self.cmb = QComboBox()
        for key, label in self.TYPES:
            self.cmb.addItem(label, key)
        btn_add = IconButton("plus", "Toevoegen")
        btn_add.clicked.connect(self._add)
        btn_del = ToolButton("Verwijderen", "trash", variant="danger", color=T.RED)
        btn_del.clicked.connect(self._delete)
        self.toolbar.add_widgets(self.inp, self.cmb, btn_add, btn_del)
        self.table.itemChanged.connect(self._on_changed)
        self.load()

    def load(self) -> None:
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for t in db.get_actor_trait_types():
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(t["naam"])
            name.setData(ID_ROLE, t["id"])
            self.table.setItem(row, 0, name)
            combo = QComboBox()
            for key, label in self.TYPES:
                combo.addItem(label, key)
            combo.setCurrentIndex(max(0, [k for k, _ in self.TYPES].index(t.get("type") or "beide")))
            combo.currentIndexChanged.connect(lambda _i, tid=t["id"], c=combo, r=row: self._set_type(tid, c))
            self.table.setCellWidget(row, 1, combo)
        self.table.setSortingEnabled(True)
        self._loading = False
        self.toolbar.set_count(str(self.table.rowCount()))

    def _set_type(self, tid: int, combo: QComboBox) -> None:
        name = next((t["naam"] for t in db.get_actor_trait_types() if t["id"] == tid), "")
        db.update_actor_trait_type(tid, name, combo.currentData())
        self.changed.emit()

    def _on_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        tid = _row_id(self.table, item.row())
        name = item.text().strip()
        if tid is None or not name:
            return
        cur = next((t for t in db.get_actor_trait_types() if t["id"] == tid), None)
        db.update_actor_trait_type(tid, name, (cur or {}).get("type") or "beide")
        self.changed.emit()

    def _add(self) -> None:
        name = self.inp.text().strip()
        if not name:
            return
        db.create_actor_trait_type(name, self.cmb.currentData())
        self.inp.clear()
        self.load()
        self.changed.emit()

    def _delete(self) -> None:
        if self._delete_rows("Eigenschappen", db.delete_actor_trait_type):
            self.load()
            self.changed.emit()


class FilmCatsSection(_Section):
    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__("Filmcategorieën", ["Naam", "Icoon"])
        self.table.setColumnWidth(0, 300)
        self.table.setIconSize(icons.pixmap("tag", 24).size())
        self.inp = QLineEdit()
        self.inp.setProperty("role", "compact")
        self.inp.setPlaceholderText("Nieuwe filmcategorie…")
        self.inp.setFixedWidth(200)
        self.inp.returnPressed.connect(self._add)
        btn_add = IconButton("plus", "Toevoegen")
        btn_add.clicked.connect(self._add)
        btn_icon = ToolButton("Icoon kiezen…", "image")
        btn_icon.clicked.connect(self._pick_icon)
        btn_del = ToolButton("Verwijderen", "trash", variant="danger", color=T.RED)
        btn_del.clicked.connect(self._delete)
        self.toolbar.add_widgets(self.inp, btn_add, btn_icon, btn_del)
        self.table.itemChanged.connect(self._on_changed)
        self.load()

    def load(self) -> None:
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for c in db.get_film_categorie_types():
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(c["naam"])
            name.setData(ID_ROLE, c["id"])
            self.table.setItem(row, 0, name)
            ip = c.get("icon_path") or ""
            cell = QTableWidgetItem(Path(ip).name if ip else "")
            cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            ic = icons.file_icon(ip, 24) if ip else None
            if ic is not None:
                cell.setIcon(ic)
            self.table.setItem(row, 1, cell)
        self.table.setSortingEnabled(True)
        self._loading = False
        self.toolbar.set_count(str(self.table.rowCount()))

    def _on_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        cid = _row_id(self.table, item.row())
        name = item.text().strip()
        if cid is None or not name:
            return
        cur = next((c for c in db.get_film_categorie_types() if c["id"] == cid), None)
        db.update_film_categorie_type(cid, name, (cur or {}).get("icon_path") or "")
        self.changed.emit()

    def _add(self) -> None:
        name = self.inp.text().strip()
        if name:
            db.create_film_categorie_type(name, "")
            self.inp.clear()
            self.load()
            self.changed.emit()

    def _pick_icon(self) -> None:
        rows = _selected_rows(self.table)
        if not rows:
            warning(self, "Icoon kiezen", "Selecteer eerst een filmcategorie.")
            return
        cid = _row_id(self.table, rows[-1])
        cur = next((c for c in db.get_film_categorie_types() if c["id"] == cid), None)
        if not cur:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Icoon kiezen", str(ICONEN_DIR),
                                              "Afbeeldingen (*.png *.svg *.jpg *.jpeg *.webp);;Alle bestanden (*)")
        if not path:
            return
        try:
            stored = _copy_icon_to_store(path)
        except OSError as e:
            warning(self, "Icoon", f"Kan icoon niet kopiëren:\n{e}")
            return
        db.update_film_categorie_type(cid, cur["naam"], stored)
        self.load()
        self.changed.emit()

    def _delete(self) -> None:
        if self._delete_rows("Filmcategorieën", db.delete_film_categorie_type):
            self.load()
            self.changed.emit()


class KleurenSection(_Section):
    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__("Acteur-kleuren", ["Naam", "Hex", "Staal"])
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 110)
        self.inp = QLineEdit()
        self.inp.setProperty("role", "compact")
        self.inp.setPlaceholderText("Naam…")
        self.inp.setFixedWidth(160)
        self.inp.returnPressed.connect(self._add)
        self.hex = QLineEdit()
        self.hex.setProperty("role", "compact")
        self.hex.setPlaceholderText("#rrggbb")
        self.hex.setFixedWidth(90)
        btn_pick = IconButton("palette", "Kleur kiezen")
        btn_pick.clicked.connect(self._pick_new)
        btn_add = IconButton("plus", "Toevoegen")
        btn_add.clicked.connect(self._add)
        btn_del = ToolButton("Verwijderen", "trash", variant="danger", color=T.RED)
        btn_del.clicked.connect(self._delete)
        self.toolbar.add_widgets(self.inp, self.hex, btn_pick, btn_add, btn_del)
        self.table.itemChanged.connect(self._on_changed)
        self.table.cellDoubleClicked.connect(self._on_double)
        self.load()

    def load(self) -> None:
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for k in db.get_actor_kleuren():
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(k["naam"])
            name.setData(ID_ROLE, k["id"])
            self.table.setItem(row, 0, name)
            hx = QTableWidgetItem(k.get("hex") or "")
            self.table.setItem(row, 1, hx)
            swatch = QTableWidgetItem("")
            swatch.setFlags(swatch.flags() & ~Qt.ItemFlag.ItemIsEditable)
            col = QColor(k.get("hex") or "")
            if col.isValid():
                swatch.setBackground(col)
            self.table.setItem(row, 2, swatch)
        self.table.setSortingEnabled(True)
        self._loading = False
        self.toolbar.set_count(str(self.table.rowCount()))

    def _on_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() > 1:
            return
        kid = _row_id(self.table, item.row())
        if kid is None:
            return
        name = self.table.item(item.row(), 0).text().strip()
        hx = self.table.item(item.row(), 1).text().strip()
        if name:
            db.update_actor_kleur(kid, name, hx)
            self.changed.emit()
        self.load()

    def _on_double(self, row: int, col: int) -> None:
        if col != 2:
            return
        kid = _row_id(self.table, row)
        cur = QColor(self.table.item(row, 1).text() if self.table.item(row, 1) else "")
        chosen = QColorDialog.getColor(cur if cur.isValid() else QColor(T.ACCENT), self, "Kleur kiezen")
        if chosen.isValid() and kid is not None:
            db.update_actor_kleur(kid, self.table.item(row, 0).text().strip(), chosen.name())
            self.load()
            self.changed.emit()

    def _pick_new(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.hex.text() or T.ACCENT), self, "Kleur kiezen")
        if chosen.isValid():
            self.hex.setText(chosen.name())

    def _add(self) -> None:
        name = self.inp.text().strip()
        if not name:
            return
        db.create_actor_kleur(name, self.hex.text().strip())
        self.inp.clear()
        self.hex.clear()
        self.load()
        self.changed.emit()

    def _delete(self) -> None:
        if self._delete_rows("Kleuren", db.delete_actor_kleur):
            self.load()
            self.changed.emit()


class ManagePanel(QWidget):
    def __init__(self, signals: AppSignals, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        split = QSplitter(Qt.Orientation.Vertical)
        self.actors = ActorsSection()
        self.categories = CategoriesSection()
        self.traits = TraitsSection()
        self.filmcats = FilmCatsSection()
        self.kleuren = KleurenSection()
        for sec in (self.actors, self.categories, self.traits, self.filmcats, self.kleuren):
            split.addWidget(sec)
        split.setSizes([420, 260, 180, 180, 160])
        root.addWidget(split, 1)
        self.actors.changed.connect(self.signals.actors_changed)
        for sec in (self.categories, self.traits, self.filmcats, self.kleuren):
            sec.changed.connect(self.signals.taxonomy_changed)
        self.signals.actors_changed.connect(self._reload_actors_if_external)
        self._self_emit = False

    def _reload_actors_if_external(self) -> None:
        if not self.actors.table.hasFocus():
            self.actors.load()

    def on_tab_active(self, active: bool) -> None:
        if active:
            self.actors.load()
            self.categories.load()
            self.traits.load()
            self.filmcats.load()
            self.kleuren.load()
