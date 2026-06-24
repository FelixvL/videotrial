#!/usr/bin/env python3
"""
CineMarker — Data tabblad
Overzicht van grote/zelden-beschikbare bestanden.
Thumbnail en acteurskoppeling verlopen via de gewone speler — niet hier.
"""

import re
import shutil
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QFrame, QScrollArea, QListWidget,
    QListWidgetItem, QSplitter, QGridLayout,
    QMessageBox, QLineEdit, QDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QPainter, QFont

import database as db

VIDEO_EXTS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv',
    '.m4v', '.ts', '.mts', '.m2ts', '.webm', '.mpg', '.mpeg',
}

CARD_W       = 220
CARD_THUMB_H = 130
GRID_COLS    = 4


def _build_archive_filename(source: Path, actor_names: list) -> str:
    """Bouw de gearchiveerde bestandsnaam op.

    Formaat:  CMARCH Voornaam Achternaam Voornaam2 Achternaam2 originelebestandsnaam.ext
    Zonder acteurs:  CMARCH originelebestandsnaam.ext
    """
    # Verwijder Windows-onveilige tekens uit acteursnamen
    safe = [re.sub(r'[\\/:*?"<>|]', '', n).strip() for n in actor_names]
    safe = [n for n in safe if n]
    prefix = f"CMARCH {' '.join(safe)} " if safe else "CMARCH "
    return prefix + source.name


# ── BigFile card (actieve sectie — alleen play-knop) ─────────────────────────

class _BigFileCard(QFrame):
    """Kaart voor bestanden die NU op schijf staan.

    Alleen thumbnail tonen + naam + acteurs (read-only) + ▶ afspelen.
    Thumbnail en acteurs worden beheerd via de gewone speler, niet hier.
    """

    play_requested    = pyqtSignal(str)   # full_path
    archive_requested = pyqtSignal(int)   # bigfile_id

    def __init__(self, record: dict, actor_names: list):
        super().__init__()
        self._record      = record
        self._actor_names = actor_names
        self._thumb_lbl: QLabel | None = None
        self._lbl_actors: QLabel | None = None
        self._build()

    # ── Build ────────────────────────────────────────────────────────────

    def _build(self):
        self.setFixedWidth(CARD_W)
        self.setStyleSheet(
            "QFrame { background:#141414; border:1px solid #262626;"
            " border-radius:6px; }"
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        # Thumbnail
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(CARD_W - 12, CARD_THUMB_H)
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setStyleSheet(
            "border-radius:4px; background:#0a0a0a; border:none;"
        )
        self._reload_thumb()
        v.addWidget(self._thumb_lbl)

        # Filename
        name = Path(self._record['full_path']).name
        lbl_name = QLabel(name)
        lbl_name.setWordWrap(True)
        lbl_name.setFixedWidth(CARD_W - 12)
        lbl_name.setStyleSheet("color:#bbb; font-size:10px; border:none;")
        lbl_name.setToolTip(self._record['full_path'])
        v.addWidget(lbl_name)

        # Actors (read-only)
        self._lbl_actors = QLabel(
            ", ".join(self._actor_names) if self._actor_names else "—"
        )
        self._lbl_actors.setWordWrap(True)
        self._lbl_actors.setFixedWidth(CARD_W - 12)
        self._lbl_actors.setStyleSheet("color:#555; font-size:9px; border:none;")
        v.addWidget(self._lbl_actors)

        # ── Knoppen-rij ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        # ▶  Afspelen
        btn_play = QPushButton("▶  Afspelen")
        btn_play.setFixedHeight(28)
        btn_play.setToolTip("Openen in de speler — maak daar de thumbnail en koppel acteurs")
        btn_play.setStyleSheet(
            "QPushButton{background:#1a2a1a;border:1px solid #2a5a2a;"
            "border-radius:4px;color:#55e055;font-size:11px;}"
            "QPushButton:hover{background:#1f3f1f;}"
            "QPushButton:pressed{background:#55e055;color:#000;}"
        )
        btn_play.clicked.connect(lambda: self.play_requested.emit(self._record['full_path']))
        btn_row.addWidget(btn_play, stretch=1)

        # 📦  Archiveer
        btn_arch = QPushButton("📦")
        btn_arch.setFixedSize(28, 28)
        btn_arch.setToolTip(
            "Verplaats naar 'deleted'-map met nieuwe naam:\n"
            "CMARCH Voornaam Achternaam originelebestandsnaam.ext\n\n"
            "Thumbnails en acteurskoppelingen blijven bewaard in het archief."
        )
        btn_arch.setStyleSheet(
            "QPushButton{background:#1e1200;border:1px solid #4a2800;"
            "border-radius:4px;color:#c07020;font-size:14px;}"
            "QPushButton:hover{background:#2a1800;border-color:#8a4800;}"
            "QPushButton:pressed{background:#c07020;color:#000;}"
        )
        btn_arch.clicked.connect(lambda: self.archive_requested.emit(self._record['id']))
        btn_row.addWidget(btn_arch)

        v.addLayout(btn_row)

    # ── Thumbnail helpers ────────────────────────────────────────────────

    def _reload_thumb(self):
        if self._thumb_lbl is None:
            return
        tp = self._record.get('thumbnail_path')
        if tp and Path(tp).exists():
            pix = QPixmap(tp).scaled(
                CARD_W - 12, CARD_THUMB_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumb_lbl.setPixmap(pix)
        else:
            self._thumb_lbl.setPixmap(self._placeholder_pixmap())

    @staticmethod
    def _placeholder_pixmap() -> QPixmap:
        pix = QPixmap(CARD_W - 12, CARD_THUMB_H)
        pix.fill(QColor(18, 18, 18))
        p = QPainter(pix)
        p.setPen(QColor(50, 50, 50))
        p.setFont(QFont("Segoe UI", 26))
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "▶")
        p.end()
        return pix

    def update_thumbnail(self, thumb_path: str):
        self._record['thumbnail_path'] = thumb_path
        self._reload_thumb()

    def update_actors(self, names: list):
        self._actor_names = names
        if self._lbl_actors is not None:
            self._lbl_actors.setText(", ".join(names) if names else "—")


# ── Archive card (compact, read-only) ────────────────────────────────────────

ARCHIVE_W       = 180
ARCHIVE_THUMB_H = 101   # 16:9 voor 180px breed
ARCHIVE_COLS    = 5


class _ArchiveCard(QFrame):
    """Compacte thumbnailkaart voor de archiefsectie — geen actieknoppen.

    Toont thumbnail + bestandsnaam. Grijs/dimmed als bestand niet beschikbaar.
    Dubbelklik speelt af als bestand bereikbaar is.
    """

    play_requested   = pyqtSignal(str)   # full_path
    delete_requested = pyqtSignal(int)   # bigfile_id

    def __init__(self, record: dict):
        super().__init__()
        self._record    = record
        self._available = Path(record['full_path']).exists()
        self._thumb_lbl: QLabel | None = None
        self._build()

    def _build(self):
        self.setFixedWidth(ARCHIVE_W)
        self.setStyleSheet(
            f"QFrame {{ background:{'#141414' if self._available else '#0d0d0d'};"
            f"  border:1px solid {'#232323' if self._available else '#181818'};"
            f"  border-radius:5px; }}"
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(3, 3, 3, 3)
        v.setSpacing(3)

        # Thumbnail
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(ARCHIVE_W - 6, ARCHIVE_THUMB_H)
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setStyleSheet("border-radius:3px; background:#0a0a0a; border:none;")
        self._load_thumb()
        v.addWidget(self._thumb_lbl)

        # Filename
        name = Path(self._record['full_path']).name
        name_lbl = QLabel(name)
        name_lbl.setWordWrap(True)
        name_lbl.setFixedWidth(ARCHIVE_W - 6)
        name_lbl.setStyleSheet(
            f"color:{'#888' if self._available else '#383838'};"
            f"  font-size:9px; border:none;"
        )
        name_lbl.setToolTip(self._record['full_path'])
        v.addWidget(name_lbl)

        # × knopje — rechtsboven, absoluut gepositioneerd over de kaart
        del_btn = QPushButton("×", self)
        del_btn.setFixedSize(16, 16)
        del_btn.move(ARCHIVE_W - 19, 3)
        del_btn.setToolTip("Verwijder uit archief")
        del_btn.setStyleSheet(
            "QPushButton { background:#1a0a0a; border:1px solid #3a1a1a;"
            " border-radius:3px; color:#4a2020; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#3a1010; border-color:#cc3333; color:#cc3333; }"
            "QPushButton:pressed { background:#cc3333; color:#fff; }"
        )
        del_btn.raise_()
        del_btn.clicked.connect(
            lambda: self.delete_requested.emit(self._record['id'])
        )

        if self._available:
            path = self._record['full_path']
            self.mouseDoubleClickEvent = lambda _e, p=path: self.play_requested.emit(p)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _load_thumb(self):
        if self._thumb_lbl is None:
            return
        tp = self._record.get('thumbnail_path')
        if tp and Path(tp).exists():
            pix = QPixmap(tp).scaled(
                ARCHIVE_W - 6, ARCHIVE_THUMB_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            # Dim offline thumbnails with a dark overlay
            if not self._available:
                overlay = QPainter(pix)
                overlay.fillRect(pix.rect(), QColor(0, 0, 0, 130))
                overlay.end()
            self._thumb_lbl.setPixmap(pix)
        else:
            ph = QPixmap(ARCHIVE_W - 6, ARCHIVE_THUMB_H)
            ph.fill(QColor(14, 14, 14))
            p = QPainter(ph)
            p.setPen(QColor(35, 35, 35))
            p.setFont(QFont("Segoe UI", 18))
            p.drawText(ph.rect(), Qt.AlignmentFlag.AlignCenter, "▶")
            p.end()
            self._thumb_lbl.setPixmap(ph)

    def update_thumbnail(self, thumb_path: str):
        self._record['thumbnail_path'] = thumb_path
        self._load_thumb()


# ── DataPanel ────────────────────────────────────────────────────────────────

class DataPanel(QWidget):
    """Tabblad voor grote/zelden-beschikbare videobestanden.

    Twee gescheiden vensters:
      • Boven  — bestanden die NU op een aangesloten schijf staan (interactief)
      • Onder  — archief van ALLE geïndexeerde bestanden + thumbnails (ook offline)
    """

    play_file_requested = pyqtSignal(str)   # pad naar videobestand

    def __init__(self):
        super().__init__()
        self._cards:               dict[int, _BigFileCard]  = {}  # bigfile_id → actieve kaart
        self._archive_cards:       dict[int, _ArchiveCard]  = {}  # bigfile_id → archiefkaart
        self._actor_map:           dict[int, str]           = {}  # actor_id → naam (cache)
        self._archive_actor_names: dict[int, list[str]]     = {}  # bigfile_id → display-namen
        self._initial_load_done:   bool                     = False
        self._build_ui()
        # Geen _refresh() bij opstart — showEvent laadt het paneel zodra de
        # gebruiker het tabblad voor het eerst opent.

    def showEvent(self, event):
        """Eerste bezoek: volledige map-scan + refresh.
        Daarna: alleen DB-gebaseerde refresh — geen trage filesystem-iteratie."""
        super().showEvent(event)
        if not self._initial_load_done:
            self._initial_load_done = True
            self._rescan_and_refresh()
        else:
            self._refresh()

    # ── Rescan ───────────────────────────────────────────────────────────

    def _rescan_and_refresh(self):
        self._rescan_known_folders()
        self._refresh()

    def _rescan_known_folders(self):
        """Doorzoek alle bekende parent-mappen op nieuwe videobestanden.

        Slaat mappen over die niet bereikbaar zijn (externe schijf weg, etc.).
        Geblokkeerde paden (handmatig verwijderd) worden overgeslagen.
        """
        blocked = db.get_blocked_bigfile_paths()

        folders: set[Path] = set()
        for rec in db.get_all_bigfiles():
            folders.add(Path(rec['full_path']).parent)
        last = db.get_setting('data_panel_folder', '')
        if last:
            folders.add(Path(last))
        for folder in folders:
            if not folder.exists():
                continue
            try:
                for f in folder.iterdir():
                    if (f.is_file()
                            and f.suffix.lower() in VIDEO_EXTS
                            and str(f) not in blocked):
                        db.get_or_create_bigfile(str(f))
            except OSError:
                pass

    # ── UI build ─────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ───────────────────────────────────────────────────────
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet(
            "QFrame { background:#0d0d0d; border-bottom:1px solid #1e1e1e; }"
        )
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(12, 0, 12, 0)
        bh.setSpacing(10)

        lbl = QLabel("DATA")
        lbl.setStyleSheet("color:#555; font-size:10px; letter-spacing:4px;")
        bh.addWidget(lbl)

        self._lbl_active_count = QLabel("")
        self._lbl_active_count.setStyleSheet("color:#444; font-size:11px;")
        bh.addWidget(self._lbl_active_count)

        bh.addStretch()

        btn_refresh = QPushButton("↺")
        btn_refresh.setFixedSize(28, 28)
        btn_refresh.setToolTip(
            "Herscant alle bekende mappen op nieuwe bestanden\n"
            "en vernieuwt de beschikbaarheidsstatus."
        )
        btn_refresh.setStyleSheet(
            "QPushButton { font-size:16px; }"
            "QPushButton:hover { color:#aaa; }"
        )
        btn_refresh.clicked.connect(self._rescan_and_refresh)
        bh.addWidget(btn_refresh)

        btn_repair = QPushButton("🔗  Paden herstellen")
        btn_repair.setFixedHeight(28)
        btn_repair.setToolTip(
            "Scan een map en herstel gebroken paden op basis van bestandsnaam.\n"
            "Gebruik dit als een schijf een andere stationsletter gekregen heeft."
        )
        btn_repair.setStyleSheet(
            "QPushButton{background:#1a1a2a;border:1px solid #2a2a4a;"
            "border-radius:4px;color:#6699cc;font-size:11px;padding:0 8px;}"
            "QPushButton:hover{background:#1f1f3a;border-color:#6699cc;}"
            "QPushButton:pressed{background:#6699cc;color:#000;}"
        )
        btn_repair.clicked.connect(self._repair_paths_dialog)
        bh.addWidget(btn_repair)

        btn_folder = QPushButton("📁  Map toevoegen")
        btn_folder.setFixedHeight(28)
        btn_folder.setToolTip(
            "Scan een nieuwe map en voeg alle videobestanden toe.\n"
            "Bestanden die niet bereikbaar zijn blijven in het archief."
        )
        btn_folder.clicked.connect(self._pick_folder)
        bh.addWidget(btn_folder)

        root.addWidget(bar)

        # ── Verticale splitter: actief (boven) + archief (onder) ──────────
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setHandleWidth(6)
        vsplit.setStyleSheet(
            "QSplitter::handle { background:#181818; }"
            "QSplitter::handle:hover { background:#2a2a2a; }"
        )

        # ── Bovenste sectie: bestanden OP SCHIJF ──────────────────────────
        top_w = QWidget()
        top_w.setStyleSheet("background:#0a0a0a;")
        tv = QVBoxLayout(top_w)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)

        top_hdr = QFrame()
        top_hdr.setFixedHeight(28)
        top_hdr.setStyleSheet(
            "QFrame { background:#0d0d0d; border-bottom:1px solid #181818; }"
        )
        th = QHBoxLayout(top_hdr)
        th.setContentsMargins(12, 0, 12, 0)
        th.setSpacing(8)
        lbl_top = QLabel("OP SCHIJF")
        lbl_top.setStyleSheet("color:#3a5a3a; font-size:9px; letter-spacing:3px;")
        th.addWidget(lbl_top)
        self._lbl_active_count = QLabel("")
        self._lbl_active_count.setStyleSheet("color:#2a3a2a; font-size:9px;")
        th.addWidget(self._lbl_active_count)
        th.addStretch()
        hint_top = QLabel("▶ = afspelen in speler  •  📦 = archiveer naar 'deleted'-map")
        hint_top.setStyleSheet("color:#1e2e1e; font-size:9px;")
        th.addWidget(hint_top)
        tv.addWidget(top_hdr)

        # Horizontale splitter: lijst (links) | kaartgrid (rechts)
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.setHandleWidth(4)
        hsplit.setStyleSheet("QSplitter::handle { background:#141414; }")

        self._list = QListWidget()
        self._list.setMinimumWidth(180)
        self._list.setMaximumWidth(320)
        self._list.setStyleSheet("""
            QListWidget {
                background:#0b0b0b;
                border:none;
                border-right:1px solid #181818;
            }
            QListWidget::item {
                padding:5px 10px;
                border-bottom:1px solid #141414;
                color:#7a9a7a;
                font-size:11px;
            }
            QListWidget::item:selected { background:#1a2a1a; color:#aacaaa; }
            QListWidget::item:hover:!selected { background:#111611; }
        """)
        self._list.itemDoubleClicked.connect(self._on_list_dblclick)
        hsplit.addWidget(self._list)

        card_scroll = QScrollArea()
        card_scroll.setWidgetResizable(True)
        card_scroll.setStyleSheet("border:none; background:#0a0a0a;")
        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background:#0a0a0a;")
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setContentsMargins(12, 12, 12, 12)
        self._grid_layout.setSpacing(10)
        self._grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        card_scroll.setWidget(self._grid_widget)
        hsplit.addWidget(card_scroll)
        hsplit.setSizes([240, 860])
        tv.addWidget(hsplit, stretch=1)

        vsplit.addWidget(top_w)

        # ── Onderste sectie: ARCHIEF ──────────────────────────────────────
        bot_w = QWidget()
        bot_w.setStyleSheet("background:#080808;")
        bv = QVBoxLayout(bot_w)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(0)

        arch_hdr = QFrame()
        arch_hdr.setFixedHeight(36)
        arch_hdr.setStyleSheet(
            "QFrame { background:#0a0a0a; border-bottom:1px solid #161616; }"
        )
        ah = QHBoxLayout(arch_hdr)
        ah.setContentsMargins(12, 0, 12, 0)
        ah.setSpacing(8)
        lbl_arch = QLabel("ARCHIEF")
        lbl_arch.setStyleSheet("color:#3a3a5a; font-size:9px; letter-spacing:3px;")
        ah.addWidget(lbl_arch)
        self._lbl_arch_count = QLabel("")
        self._lbl_arch_count.setStyleSheet("color:#252535; font-size:9px;")
        ah.addWidget(self._lbl_arch_count)
        ah.addStretch()
        self._arch_search = QLineEdit()
        self._arch_search.setPlaceholderText("zoek op acteur…")
        self._arch_search.setFixedWidth(200)
        self._arch_search.setFixedHeight(24)
        self._arch_search.setStyleSheet(
            "QLineEdit { background:#111; border:1px solid #2a2a3a;"
            " border-radius:3px; color:#888; font-size:11px; padding:0 6px; }"
            "QLineEdit:focus { border-color:#5555aa; color:#bbb; }"
        )
        self._arch_search.textChanged.connect(self._filter_archive)
        ah.addWidget(self._arch_search)
        bv.addWidget(arch_hdr)

        arch_scroll = QScrollArea()
        arch_scroll.setWidgetResizable(True)
        arch_scroll.setStyleSheet("border:none; background:#080808;")
        self._arch_widget = QWidget()
        self._arch_widget.setStyleSheet("background:#080808;")
        self._arch_layout = QGridLayout(self._arch_widget)
        self._arch_layout.setContentsMargins(12, 12, 12, 12)
        self._arch_layout.setSpacing(8)
        self._arch_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        arch_scroll.setWidget(self._arch_widget)
        bv.addWidget(arch_scroll, stretch=1)

        vsplit.addWidget(bot_w)

        vsplit.setSizes([400, 300])
        root.addWidget(vsplit, stretch=1)

    # ── Data refresh ──────────────────────────────────────────────────────

    def _refresh(self):
        """Herlaad alle bigfiles en vul beide secties opnieuw."""
        self._actor_map = {a['id']: a['name'] for a in db.get_all_actors()}
        all_records = db.get_all_bigfiles()

        # Splits in beschikbaar (boven) vs archief (onder)
        available_recs = [r for r in all_records if Path(r['full_path']).exists()]
        n_all  = len(all_records)
        n_avail = len(available_recs)

        self._lbl_active_count.setText(
            f"{n_avail} bestand{'en' if n_avail != 1 else ''} bereikbaar"
        )
        self._lbl_arch_count.setText(
            f"{n_all} totaal  •  "
            f"{sum(1 for r in all_records if r.get('thumbnail_path') and Path(r['thumbnail_path']).exists())} "
            f"met thumbnail"
        )

        self._refresh_active(available_recs)
        self._refresh_archive(all_records)

    def _refresh_active(self, records: list):
        """Vul de bovenste sectie met alleen bereikbare bestanden."""
        self._list.clear()
        self._cards.clear()

        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, rec in enumerate(records):
            # Lijst
            li = QListWidgetItem(Path(rec['full_path']).name)
            li.setData(Qt.ItemDataRole.UserRole, rec['id'])
            li.setToolTip(rec['full_path'])
            self._list.addItem(li)

            # Thumbnail fallback
            display_rec = dict(rec)
            bf_thumb = rec.get('thumbnail_path') or ''
            if not bf_thumb or not Path(bf_thumb).exists():
                film_thumb = db.get_best_film_thumbnail(rec['full_path'])
                if film_thumb:
                    display_rec['thumbnail_path'] = film_thumb

            # Acteurs
            actor_ids   = db.get_bigfile_actor_ids(rec['id'])
            actor_names = [self._actor_map[aid] for aid in actor_ids if aid in self._actor_map]
            if not actor_names:
                actor_names = db.get_actor_names_for_film_path(rec['full_path'])

            card = _BigFileCard(display_rec, actor_names)
            card.play_requested.connect(self._on_play)
            card.archive_requested.connect(self._do_archive)
            self._cards[rec['id']] = card

            row, col = divmod(i, GRID_COLS)
            self._grid_layout.addWidget(card, row, col)

    def _refresh_archive(self, records: list | None = None):
        """Vul de onderste archiefsectie met ALLE geïndexeerde bestanden."""
        if records is None:
            records = db.get_all_bigfiles()

        self._archive_cards.clear()
        self._archive_actor_names.clear()

        while self._arch_layout.count():
            item = self._arch_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Acteursnamen voor alle bigfiles in één batch-query ophalen
        all_paths = [r['full_path'] for r in records]
        names_batch = db.get_actor_display_names_batch(all_paths)

        for i, rec in enumerate(records):
            # Thumbnail fallback
            display_rec = dict(rec)
            bf_thumb = rec.get('thumbnail_path') or ''
            if not bf_thumb or not Path(bf_thumb).exists():
                film_thumb = db.get_best_film_thumbnail(rec['full_path'])
                if film_thumb:
                    display_rec['thumbnail_path'] = film_thumb

            # Acteursnamen opslaan voor zoekfilter (uit batch)
            names = names_batch.get(rec['full_path'], [])
            self._archive_actor_names[rec['id']] = [n.lower() for n in names]

            card = _ArchiveCard(display_rec)
            card.play_requested.connect(self._on_play)
            card.delete_requested.connect(self._do_delete_archive)
            self._archive_cards[rec['id']] = card

            row, col = divmod(i, ARCHIVE_COLS)
            self._arch_layout.addWidget(card, row, col)

        # Zoekfilter opnieuw toepassen na verversing
        self._filter_archive(self._arch_search.text())

    # ── Archief verwijderen ───────────────────────────────────────────────

    def _do_delete_archive(self, bigfile_id: int):
        """Verwijder een bigfile-record uit de DB (het bestand zelf blijft onaangeroerd)."""
        rec = next(
            (r for r in db.get_all_bigfiles() if r['id'] == bigfile_id), None
        )
        name = Path(rec['full_path']).name if rec else str(bigfile_id)

        ans = QMessageBox.question(
            self,
            "Verwijder uit archief",
            f"Verwijder\n{name}\nuit het archief?\n\n"
            "Het bestand zelf wordt niet aangeraakt.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        try:
            db.delete_bigfile(bigfile_id)
        except Exception as e:
            QMessageBox.warning(self, "Fout bij verwijderen", str(e))
            return
        self._refresh()

    # ── Archief zoekfilter ────────────────────────────────────────────────

    def _filter_archive(self, query: str):
        """Toon alleen archiefkaarten waarvan een acteur overeenkomt met de zoekopdracht."""
        q = query.strip().lower()

        # Herpositioneer zichtbare kaarten in het grid zodat er geen gaten vallen
        visible_ids = []
        for bf_id, card in self._archive_cards.items():
            if not q:
                match = True
            else:
                names = self._archive_actor_names.get(bf_id, [])
                match = any(q in name for name in names)
            card.setVisible(match)
            if match:
                visible_ids.append(bf_id)

        # Grid herindelen: verwijder alle kaarten en voeg alleen zichtbare toe
        while self._arch_layout.count():
            self._arch_layout.takeAt(0)

        for i, bf_id in enumerate(visible_ids):
            row, col = divmod(i, ARCHIVE_COLS)
            self._arch_layout.addWidget(self._archive_cards[bf_id], row, col)

    # ── Pad-herstel dialoog ───────────────────────────────────────────────

    def _repair_paths_dialog(self):
        """Scan een map recursief en herstel gebroken DB-paden op bestandsnaam."""

        dlg = QDialog(self)
        dlg.setWindowTitle("Paden herstellen")
        dlg.setMinimumSize(600, 500)
        dlg.setStyleSheet("QDialog { background: #0e0e0e; }")

        v = QVBoxLayout(dlg)
        v.setSpacing(10)
        v.setContentsMargins(16, 16, 16, 16)

        # ── Uitleg ───────────────────────────────────────────────────────
        lbl_info = QLabel(
            "Scant een map recursief en koppelt gevonden bestanden opnieuw aan "
            "DB-records op basis van bestandsnaam.\n"
            "Gebruik dit als een schijf een andere stationsletter gekregen heeft."
        )
        lbl_info.setWordWrap(True)
        lbl_info.setStyleSheet("color:#555; font-size:11px;")
        v.addWidget(lbl_info)

        # ── Mappenkiezer ─────────────────────────────────────────────────
        pick_row = QHBoxLayout()
        inp_folder = QLineEdit()
        inp_folder.setPlaceholderText("Kies de map (of schiijfhoofdmap) om te scannen…")
        inp_folder.setReadOnly(True)
        inp_folder.setFixedHeight(30)
        pick_row.addWidget(inp_folder, stretch=1)
        btn_pick = QPushButton("📁  Kies map")
        btn_pick.setFixedHeight(30)
        pick_row.addWidget(btn_pick)
        v.addLayout(pick_row)

        # ── Resultatenlijst ───────────────────────────────────────────────
        result_list = QListWidget()
        result_list.setStyleSheet(
            "QListWidget { background:#080808; border:1px solid #1a1a1a;"
            "  border-radius:4px; }"
            "QListWidget::item { padding:5px 10px; border-bottom:1px solid #111; }"
        )
        v.addWidget(result_list, stretch=1)

        # ── Samenvatting ─────────────────────────────────────────────────
        lbl_stats = QLabel("Kies een map om de scan te starten.")
        lbl_stats.setStyleSheet("color:#444; font-size:10px; padding:2px 0;")
        v.addWidget(lbl_stats)

        # ── Knoppenrij ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_apply = QPushButton("✓  Toepassen")
        btn_apply.setObjectName("accent")
        btn_apply.setFixedHeight(32)
        btn_apply.setEnabled(False)
        btn_close = QPushButton("Sluiten")
        btn_close.setFixedHeight(32)
        btn_row.addStretch()
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_close)
        v.addLayout(btn_row)

        _path_map: dict = {}   # {oud_pad: nieuw_pad}

        def _scan(folder: str):
            nonlocal _path_map
            _path_map = {}
            result_list.clear()
            lbl_stats.setText("Bezig met scannen…")
            lbl_stats.repaint()

            # Bouw naam→nieuw_pad kaart van alle video's op schijf
            available: dict[str, str] = {}
            try:
                for p in Path(folder).rglob('*'):
                    if p.suffix.lower() in VIDEO_EXTS:
                        available[p.name] = str(p)
            except OSError:
                pass

            all_bf    = db.get_all_bigfiles()
            all_films = db.get_all_films()

            matched   = 0
            not_found = 0
            unchanged = 0

            # ── Bigfiles ─────────────────────────────────────────────────
            for rec in all_bf:
                old_path = rec['full_path']
                filename = Path(old_path).name

                if Path(old_path).exists():
                    unchanged += 1
                    continue

                new_path = available.get(filename)
                if new_path and new_path != old_path:
                    _path_map[old_path] = new_path
                    item = QListWidgetItem(f"✓  {filename}")
                    item.setForeground(QColor('#55cc55'))
                    item.setToolTip(f"Was:  {old_path}\nWordt: {new_path}")
                    result_list.addItem(item)
                    matched += 1
                else:
                    item = QListWidgetItem(f"✗  {filename}")
                    item.setForeground(QColor('#884444'))
                    item.setToolTip(f"Niet gevonden in gescande map:\n{old_path}")
                    result_list.addItem(item)
                    not_found += 1

            # ── Films (die op dezelfde schijf stonden maar niet als bigfile) ─
            for rec in all_films:
                old_path = rec['file_path']
                if old_path in _path_map:
                    continue   # al verwerkt via bigfiles
                if Path(old_path).exists():
                    continue

                new_path = available.get(Path(old_path).name)
                if new_path and new_path != old_path:
                    _path_map[old_path] = new_path
                    matched += 1

            parts = []
            if matched:
                parts.append(f"{matched} te herstellen")
            if not_found:
                parts.append(f"{not_found} niet gevonden in gescande map")
            if unchanged:
                parts.append(f"{unchanged} ongewijzigd")
            lbl_stats.setText("  •  ".join(parts) or "Niets gevonden.")
            btn_apply.setEnabled(bool(_path_map))

        def _pick():
            folder = QFileDialog.getExistingDirectory(
                dlg, "Kies de map (of schijfhoofdmap) om te scannen"
            )
            if folder:
                inp_folder.setText(folder)
                _scan(folder)

        def _apply():
            if not _path_map:
                return
            try:
                bf_n, fm_n = db.remap_file_paths(_path_map)
            except Exception as e:
                QMessageBox.warning(dlg, "Fout bij opslaan", str(e))
                return
            QMessageBox.information(
                dlg, "Klaar",
                f"Hersteld:\n• {bf_n} bigfile-pad{'en' if bf_n != 1 else ''}\n"
                f"• {fm_n} film-pad{'en' if fm_n != 1 else ''}"
            )
            self._initial_load_done = False   # forceer volledige herlaad
            dlg.accept()
            self._rescan_and_refresh()

        btn_pick.clicked.connect(_pick)
        btn_apply.clicked.connect(_apply)
        btn_close.clicked.connect(dlg.reject)
        dlg.exec()

    # ── Folder scanning ───────────────────────────────────────────────────

    def _pick_folder(self):
        last = db.get_setting('data_panel_folder', '')
        folder = QFileDialog.getExistingDirectory(
            self, "Selecteer map met videobestanden", last or ''
        )
        if not folder:
            return
        db.set_setting('data_panel_folder', folder)
        self._scan_folder(folder)

    def _scan_folder(self, folder: str):
        p = Path(folder)
        if not p.exists():
            return
        blocked = db.get_blocked_bigfile_paths()
        added = 0
        for f in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if (f.is_file()
                    and f.suffix.lower() in VIDEO_EXTS
                    and str(f) not in blocked):
                db.get_or_create_bigfile(str(f))
                added += 1
        self._refresh()
        try:
            if added and hasattr(self.window(), 'statusBar'):
                self.window().statusBar().showMessage(
                    f"  {added} bestand{'en' if added != 1 else ''} toegevoegd uit {p.name}"
                )
        except Exception:
            pass

    # ── Card signal handlers ──────────────────────────────────────────────

    def _on_play(self, full_path: str):
        rec = db.get_or_create_bigfile(full_path)
        db.update_bigfile_last_seen(rec['id'])
        self.play_file_requested.emit(full_path)

    # ── List double-click (actieve sectie) ────────────────────────────────

    def _on_list_dblclick(self, item: QListWidgetItem):
        bigfile_id = item.data(Qt.ItemDataRole.UserRole)
        for rec in db.get_all_bigfiles():
            if rec['id'] == bigfile_id:
                self._on_play(rec['full_path'])
                return

    # ── Archiveren ────────────────────────────────────────────────────────

    def _deleted_folder_for(self, source: Path) -> Path | None:
        """Zoek de 'deleted'-map naast het bronbestand.

        Controleert hoofdletterongevoelig (Windows).
        Vraagt om aanmaken als de map er niet is.
        """
        parent = source.parent
        try:
            for d in parent.iterdir():
                if d.is_dir() and d.name.lower() == 'deleted':
                    return d
        except OSError:
            pass

        # Niet gevonden — vraag om aanmaken
        ans = QMessageBox.question(
            self,
            "Map 'deleted' niet gevonden",
            f"Er bestaat geen 'deleted'-map in:\n{parent}\n\n"
            "Aanmaken?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return None
        new_dir = parent / "deleted"
        try:
            new_dir.mkdir()
            return new_dir
        except OSError as e:
            QMessageBox.warning(self, "Fout", f"Kan map niet aanmaken:\n{e}")
            return None

    def _do_archive(self, bigfile_id: int):
        """Verplaats een bigfile naar de 'deleted'-map met CMARCH-naam."""
        # Haal record op
        rec = next(
            (r for r in db.get_all_bigfiles() if r['id'] == bigfile_id), None
        )
        if not rec:
            return

        source = Path(rec['full_path'])
        if not source.exists():
            QMessageBox.warning(
                self, "Bestand niet gevonden",
                f"Het bestand staat niet meer op schijf:\n{source}"
            )
            self._refresh()
            return

        # Acteursnamen als "Voornaam Achternaam" — altijd vers uit DB
        actor_names = db.get_actor_display_names_for_film_path(rec['full_path'])

        # Bouw nieuwe bestandsnaam
        new_name = _build_archive_filename(source, actor_names)

        # Doelmap
        deleted_dir = self._deleted_folder_for(source)
        if deleted_dir is None:
            return

        dest = deleted_dir / new_name
        # Vermijd naamconflict
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            i = 2
            while dest.exists():
                dest = deleted_dir / f"{stem} ({i}){suffix}"
                i += 1

        # Bevestiging
        actors_str = (
            ", ".join(actor_names) if actor_names else "— geen acteurs gekoppeld —"
        )
        ans = QMessageBox.question(
            self,
            "Bestand archiveren",
            f"Verplaats naar:\n{dest}\n\n"
            f"Acteurs: {actors_str}\n\n"
            "Thumbnails en acteurskoppelingen blijven bewaard in het archief. "
            "Het bestand verdwijnt uit het bovenste venster.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        try:
            shutil.move(str(source), str(dest))
        except Exception as e:
            QMessageBox.warning(self, "Fout bij archiveren", str(e))
            return

        # Ververs beide panelen — bestand bestaat niet meer op oud pad,
        # dus verdwijnt het vanzelf uit OP SCHIJF en wordt gedimmed in ARCHIEF.
        self._refresh()

    # ── Thumbnail sync (aangeroepen vanuit player._sync_bigfile_thumbnail) ──

    def sync_thumbnail(self, bigfile_id: int, thumb_path: str):
        """Update beide secties direct als de speler een thumbnail heeft opgeslagen."""
        card = self._cards.get(bigfile_id)
        if card:
            card.update_thumbnail(thumb_path)
        arch_card = self._archive_cards.get(bigfile_id)
        if arch_card:
            arch_card.update_thumbnail(thumb_path)
