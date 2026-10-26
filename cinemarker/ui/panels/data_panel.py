"""
CineMarker — Data-paneel: grote bestanden (bigfiles) op externe schijven.

OP SCHIJF: alleen bestanden die nu bereikbaar zijn (afspelen, archiveren naar 'deleted' met CMARCH-naam).
ARCHIEF:   alle geïndexeerde bestanden (offline gedimd), zoeken op acteur, verwijderen uit het archief.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from ...core import database as db, media, settings
from ...core.models import BigFile
from ..delegates.pixcache import PixmapCache
from ..signals import AppSignals
from ..theme import icons, tokens as T
from ..widgets.buttons import IconButton, ToolButton
from ..widgets.dialogs import BaseDialog, confirm, info, warning
from ..widgets.flow_layout import FlowLayout
from ..widgets.search_field import SearchField
from ..widgets.section import SectionHeader
from ..widgets.toolbar import PanelToolbar

_log = logging.getLogger("cinemarker.data")

CARD_W, CARD_THUMB_H = 220, 124
ARCH_W, ARCH_THUMB_H = 180, 101
_THUMBS = PixmapCache(None, write_disk=False)


class BigFileCard(QFrame):
    play_requested = pyqtSignal(str)
    archive_requested = pyqtSignal(int)

    def __init__(self, bf: BigFile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bf = bf
        self.setProperty("role", "card")
        self.setFixedWidth(CARD_W)
        v = QVBoxLayout(self)
        v.setContentsMargins(T.SPACE_XS, T.SPACE_XS, T.SPACE_XS, T.SPACE_XS)
        v.setSpacing(T.SPACE_XS)
        self.thumb = QLabel()
        self.thumb.setFixedSize(CARD_W - 2 * T.SPACE_XS, CARD_THUMB_H)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(f"background:{T.BG}; border-radius:{T.RADIUS_SM}px;")
        self._set_thumb(bf.thumbnail_path)
        v.addWidget(self.thumb)
        name = QLabel(Path(bf.full_path).name)
        name.setToolTip(bf.full_path)
        name.setWordWrap(True)
        name.setMaximumHeight(36)
        v.addWidget(name)
        self.lbl_actors = QLabel(", ".join(bf.actor_names) or "—")
        self.lbl_actors.setProperty("role", "dim")
        self.lbl_actors.setWordWrap(True)
        v.addWidget(self.lbl_actors)
        row = QHBoxLayout()
        btn_play = ToolButton("Afspelen", "play", variant="success", color=T.GREEN)
        btn_play.clicked.connect(lambda: self.play_requested.emit(bf.full_path))
        btn_arch = IconButton("package", "Archiveren: verplaatsen naar 'deleted' met CMARCH-naam", color=T.ORANGE)
        btn_arch.clicked.connect(lambda: self.archive_requested.emit(bf.id))
        row.addWidget(btn_play, 1)
        row.addWidget(btn_arch)
        v.addLayout(row)

    def _set_thumb(self, path: str) -> None:
        pm = _THUMBS.get(path, self.thumb.width(), self.thumb.height()) if path else None
        self.thumb.setPixmap(pm if pm is not None else icons.pixmap("play", 32, T.TEXT_DIM))


class ArchiveCard(QFrame):
    play_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(int)

    def __init__(self, bf: BigFile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bf = bf
        self.setProperty("role", "card")
        self.setFixedWidth(ARCH_W)
        v = QVBoxLayout(self)
        v.setContentsMargins(3, 3, 3, 3)
        v.setSpacing(2)
        self.thumb = QLabel()
        self.thumb.setFixedSize(ARCH_W - 6, ARCH_THUMB_H)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(f"background:{T.BG}; border-radius:{T.RADIUS_SM}px;")
        pm = _THUMBS.get(bf.thumbnail_path, ARCH_W - 6, ARCH_THUMB_H) if bf.thumbnail_path else None
        if pm is not None and not bf.available:
            dim = QPixmap(pm)
            from PyQt6.QtGui import QPainter
            p = QPainter(dim)
            p.fillRect(dim.rect(), QColor(*T.OVERLAY_DARK))
            p.end()
            pm = dim
        self.thumb.setPixmap(pm if pm is not None else icons.pixmap("play", 24, T.TEXT_DIM))
        v.addWidget(self.thumb)
        name = QLabel(Path(bf.full_path).name)
        name.setProperty("role", "muted" if bf.available else "dim")
        name.setToolTip(bf.full_path + ("" if bf.available else "  (niet bereikbaar)"))
        v.addWidget(name)
        btn = IconButton("x", "Uit archief verwijderen (bestand blijft staan)", variant="danger")
        btn.setParent(self)
        btn.setFixedSize(22, 22)
        btn.move(ARCH_W - 26, 5)
        btn.clicked.connect(lambda: self.delete_requested.emit(bf.id))
        if bf.available:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.bf.available:
            self.play_requested.emit(self.bf.full_path)


class DataPanel(QWidget):
    play_file_requested = pyqtSignal(str)

    def __init__(self, signals: AppSignals, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        self._scanned = False
        self._archive_cards: list[ArchiveCard] = []
        self._build_ui()
        signals.film_thumbnail_changed.connect(lambda _p, _t: self._refresh())

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.toolbar = PanelToolbar("Data")
        self.toolbar.add_stretch()
        self.btn_refresh = IconButton("refresh", "Bekende mappen opnieuw doorzoeken")
        self.btn_refresh.clicked.connect(self.rescan)
        self.btn_repair = ToolButton("Paden herstellen", "link", tooltip="Gebroken paden herstellen na een andere stationsletter")
        self.btn_repair.clicked.connect(self._repair_dialog)
        self.btn_add = ToolButton("Map toevoegen", "folder-plus", variant="accent", color=T.ACCENT_TEXT_ON)
        self.btn_add.clicked.connect(self._pick_folder)
        self.toolbar.add_widgets(self.btn_refresh, self.btn_repair, self.btn_add)
        root.addWidget(self.toolbar)

        split = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(T.SPACE_MD, T.SPACE_SM, T.SPACE_MD, T.SPACE_SM)
        tv.setSpacing(T.SPACE_XS)
        self.sec_active = SectionHeader("Op schijf")
        hint = QLabel("afspelen  •  archiveren naar 'deleted'-map")
        hint.setProperty("role", "dim")
        self.sec_active.add_widget(hint)
        tv.addWidget(self.sec_active)
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(180)
        self.list.setMaximumWidth(340)
        self.list.itemDoubleClicked.connect(lambda it: self._play(it.data(Qt.ItemDataRole.UserRole)))
        hsplit.addWidget(self.list)
        self.active_scroll = QScrollArea()
        self.active_scroll.setWidgetResizable(True)
        self._active_inner = QWidget()
        self.active_scroll.setWidget(self._active_inner)
        hsplit.addWidget(self.active_scroll)
        hsplit.setStretchFactor(1, 1)
        hsplit.setSizes([240, 1000])
        tv.addWidget(hsplit, 1)
        split.addWidget(top)

        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(T.SPACE_MD, T.SPACE_SM, T.SPACE_MD, T.SPACE_SM)
        bv.setSpacing(T.SPACE_XS)
        self.sec_archive = SectionHeader("Archief")
        self.arch_search = SearchField("Zoek op acteur…", debounce_ms=0, width=220, compact=True)
        self.arch_search.textChanged.connect(self._filter_archive)
        self.sec_archive.add_widget(self.arch_search)
        bv.addWidget(self.sec_archive)
        self.arch_scroll = QScrollArea()
        self.arch_scroll.setWidgetResizable(True)
        self._arch_inner = QWidget()
        self.arch_scroll.setWidget(self._arch_inner)
        bv.addWidget(self.arch_scroll, 1)
        split.addWidget(bottom)
        split.setSizes([450, 350])
        root.addWidget(split, 1)

    # ── laden ───────────────────────────────────────────────────────────
    def on_tab_active(self, active: bool) -> None:
        if active:
            if not self._scanned:
                self.rescan()
            else:
                self._refresh()

    def rescan(self) -> None:
        self._scanned = True
        blocked = db.get_blocked_bigfile_paths()
        folders = {Path(r["full_path"]).parent for r in db.get_all_bigfiles()}
        last = settings.get_str(settings.DATA_PANEL_FOLDER, "")
        if last:
            folders.add(Path(last))
        for folder in folders:
            if not folder.is_dir():
                continue
            try:
                for f in folder.iterdir():
                    if f.is_file() and media.is_video(f) and str(f) not in blocked:
                        db.get_or_create_bigfile(str(f))
            except OSError:
                continue
        self._refresh()

    def _records(self) -> list[BigFile]:
        rows = db.get_all_bigfiles()
        names = db.get_actor_display_names_batch([r["full_path"] for r in rows])
        out = []
        for r in rows:
            thumb = r.get("thumbnail_path") or db.get_best_film_thumbnail(r["full_path"]) or ""
            out.append(BigFile(id=r["id"], full_path=r["full_path"], thumbnail_path=thumb,
                               actor_names=names.get(r["full_path"], []), available=Path(r["full_path"]).exists()))
        return out

    def _refresh(self) -> None:
        records = self._records()
        _THUMBS.invalidate()
        active = [b for b in records if b.available]
        self.sec_active.set_count(f"{len(active)} bestanden bereikbaar")
        self.toolbar.set_count(f"{len(active)} / {len(records)}")
        self.list.clear()
        for b in active:
            it = QListWidgetItem(Path(b.full_path).name)
            it.setData(Qt.ItemDataRole.UserRole, b.full_path)
            it.setToolTip(b.full_path)
            self.list.addItem(it)
        self._active_inner = QWidget()
        flow = FlowLayout(self._active_inner, margin=T.SPACE_XS, h_spacing=T.SPACE_SM, v_spacing=T.SPACE_SM)
        for b in active:
            card = BigFileCard(b)
            card.play_requested.connect(self._play)
            card.archive_requested.connect(self._archive)
            flow.addWidget(card)
        self.active_scroll.setWidget(self._active_inner)

        with_thumb = sum(1 for b in records if b.thumbnail_path)
        self.sec_archive.set_count(f"{len(records)} totaal  •  {with_thumb} met thumbnail")
        self._arch_inner = QWidget()
        aflow = FlowLayout(self._arch_inner, margin=T.SPACE_XS, h_spacing=T.SPACE_SM, v_spacing=T.SPACE_SM)
        self._archive_cards = []
        for b in records:
            card = ArchiveCard(b)
            card.play_requested.connect(self._play)
            card.delete_requested.connect(self._delete_archive)
            aflow.addWidget(card)
            self._archive_cards.append(card)
        self.arch_scroll.setWidget(self._arch_inner)
        self._filter_archive(self.arch_search.text())

    def _filter_archive(self, text: str) -> None:
        q = text.strip().lower()
        for card in self._archive_cards:
            card.setVisible(not q or any(q in n.lower() for n in card.bf.actor_names))
        self._arch_inner.updateGeometry()

    # ── acties ──────────────────────────────────────────────────────────
    def _play(self, path: str) -> None:
        rec = db.get_or_create_bigfile(path)
        db.update_bigfile_last_seen(rec["id"])
        self.play_file_requested.emit(path)

    def _pick_folder(self) -> None:
        start = settings.get_str(settings.DATA_PANEL_FOLDER, "") or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Map met grote bestanden", start)
        if not folder:
            return
        settings.set_str(settings.DATA_PANEL_FOLDER, folder)
        blocked = db.get_blocked_bigfile_paths()
        n = 0
        try:
            for f in Path(folder).iterdir():
                if f.is_file() and media.is_video(f) and str(f) not in blocked:
                    db.get_or_create_bigfile(str(f))
                    n += 1
        except OSError as e:
            warning(self, "Map niet leesbaar", str(e))
        self.signals.say(f"{n} bestanden toegevoegd uit {Path(folder).name}")
        self._refresh()

    def _delete_archive(self, bigfile_id: int) -> None:
        rec = next((r for r in db.get_all_bigfiles() if r["id"] == bigfile_id), None)
        if not rec:
            return
        if not confirm(self, "Uit archief verwijderen",
                       f"'{Path(rec['full_path']).name}' uit het archief verwijderen?\n\nHet bestand zelf blijft staan "
                       "en wordt bij een volgende scan niet opnieuw toegevoegd."):
            return
        db.delete_bigfile(bigfile_id)
        self._refresh()

    def _deleted_folder_for(self, source: Path) -> Path | None:
        parent = source.parent
        try:
            for d in parent.iterdir():
                if d.is_dir() and d.name.lower() == "deleted":
                    return d
        except OSError:
            pass
        if not confirm(self, "Map 'deleted' niet gevonden", f"Er is geen 'deleted'-map in:\n{parent}\n\nAanmaken?",
                       ok_text="Aanmaken", danger=False):
            return None
        new_dir = parent / "deleted"
        try:
            new_dir.mkdir()
            return new_dir
        except OSError as e:
            warning(self, "Fout", f"Kan map niet aanmaken:\n{e}")
            return None

    def _archive(self, bigfile_id: int) -> None:
        rec = next((r for r in db.get_all_bigfiles() if r["id"] == bigfile_id), None)
        if not rec:
            return
        source = Path(rec["full_path"])
        if not source.exists():
            warning(self, "Bestand niet gevonden", f"Het bestand staat niet meer op schijf:\n{source}")
            self._refresh()
            return
        names = db.get_actor_display_names_for_film_path(rec["full_path"])
        deleted_dir = self._deleted_folder_for(source)
        if deleted_dir is None:
            return
        dest = media.unique_path(deleted_dir / media.archive_filename(source, names), style="paren")
        if not confirm(self, "Bestand archiveren",
                       f"Verplaatsen naar:\n{dest}\n\nActeurs: {', '.join(names) or '— geen —'}\n\n"
                       "Thumbnails en acteurskoppelingen blijven bewaard in het archief.", ok_text="Archiveren", danger=False):
            return
        try:
            shutil.move(str(source), str(dest))
        except OSError as e:
            warning(self, "Fout bij archiveren", str(e))
            return
        self.signals.say(f"Gearchiveerd: {dest.name}")
        self._refresh()

    def _repair_dialog(self) -> None:
        dlg = RepairPathsDialog(self)
        if dlg.exec() or dlg.changed:
            self._refresh()


class RepairPathsDialog(BaseDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Paden herstellen", parent, min_width=640)
        self.changed = False
        self._map: dict[str, str] = {}
        info_lbl = QLabel("Scant een map recursief en koppelt gevonden bestanden opnieuw aan records op basis van "
                          "bestandsnaam. Gebruik dit als een schijf een andere stationsletter heeft gekregen.")
        info_lbl.setWordWrap(True)
        info_lbl.setProperty("role", "muted")
        self.body.addWidget(info_lbl)
        row = QHBoxLayout()
        self.inp = QLineEdit()
        self.inp.setReadOnly(True)
        self.inp.setPlaceholderText("Kies de map (of schijfhoofdmap) om te scannen…")
        btn_pick = ToolButton("Kies map", "folder")
        btn_pick.clicked.connect(self._pick)
        row.addWidget(self.inp, 1)
        row.addWidget(btn_pick)
        self.body.addLayout(row)
        self.results = QListWidget()
        self.results.setMinimumHeight(260)
        self.body.addWidget(self.results, 1)
        self.lbl_stats = QLabel("Kies een map om de scan te starten.")
        self.lbl_stats.setProperty("role", "dim")
        self.body.addWidget(self.lbl_stats)
        btn_cleanup = ToolButton("Duplicaten opruimen", "trash",
                                 tooltip="Verwijder dubbele records met dezelfde bestandsnaam; het rijkste record blijft")
        btn_cleanup.clicked.connect(self._cleanup)
        self.add_buttons("Toepassen", "Sluiten", ok_icon="check", extra_left=[btn_cleanup])
        self.ok_button.setEnabled(False)

    def _pick(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Kies de map om te scannen")
        if folder:
            self.inp.setText(folder)
            self._scan(folder)

    def _scan(self, folder: str) -> None:
        self._map = {}
        self.results.clear()
        self.lbl_stats.setText("Bezig met scannen…")
        self.lbl_stats.repaint()
        available: dict[str, str] = {}
        try:
            for p in Path(folder).rglob("*"):
                if any(part.lower() == "deleted" for part in p.parts):
                    continue
                if media.is_video(p):
                    available[p.name] = str(p)
        except OSError:
            pass
        matched = not_found = unchanged = 0
        for rec in db.get_all_bigfiles():
            old = rec["full_path"]
            name = Path(old).name
            if Path(old).exists():
                unchanged += 1
                continue
            new = available.get(name)
            it = QListWidgetItem(name)
            if new and new != old:
                self._map[old] = new
                it.setIcon(icons.icon("check", T.ICON_SIZE_SM, T.GREEN))
                it.setToolTip(f"Was:   {old}\nWordt: {new}")
                matched += 1
            else:
                it.setIcon(icons.icon("x", T.ICON_SIZE_SM, T.RED))
                it.setForeground(QColor(T.TEXT_DIM))
                it.setToolTip(f"Niet gevonden in gescande map:\n{old}")
                not_found += 1
            self.results.addItem(it)
        for rec in db.get_all_films():
            old = rec["file_path"]
            if old in self._map or Path(old).exists():
                continue
            new = available.get(Path(old).name)
            if new and new != old:
                self._map[old] = new
                matched += 1
        parts = []
        if matched:
            parts.append(f"{matched} te herstellen")
        if not_found:
            parts.append(f"{not_found} niet gevonden")
        if unchanged:
            parts.append(f"{unchanged} ongewijzigd")
        self.lbl_stats.setText("  •  ".join(parts) or "Niets gevonden.")
        self.ok_button.setEnabled(bool(self._map))

    def _cleanup(self) -> None:
        n = db.cleanup_duplicate_bigfiles()
        info(self, "Opruimen klaar", f"{n} dubbele record(s) verwijderd." if n else "Geen duplicaten gevonden.")
        self.changed = self.changed or n > 0

    def accept(self) -> None:  # noqa: D102
        if not self._map:
            return
        try:
            bf_n, fm_n = db.remap_file_paths(self._map)
        except Exception as e:  # noqa: BLE001
            warning(self, "Fout bij opslaan", str(e))
            return
        info(self, "Klaar", f"Hersteld:\n•  {bf_n} bigfile-pad(en)\n•  {fm_n} film-pad(en)")
        self.changed = True
        super().accept()
