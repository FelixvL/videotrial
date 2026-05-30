#!/usr/bin/env python3
"""
CineMarker — Data tabblad
Beheer van grote/zelden-beschikbare bestanden met thumbnails en acteurskoppelingen.
"""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QFrame, QSizePolicy, QScrollArea, QListWidget,
    QListWidgetItem, QDialog, QDialogButtonBox, QCheckBox,
    QLineEdit, QSplitter, QGridLayout, QSpacerItem,
    QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QColor, QPainter, QFont

import database as db
from paths import BIGFILES_DIR

VIDEO_EXTS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv',
    '.m4v', '.ts', '.mts', '.m2ts', '.webm', '.mpg', '.mpeg',
}

CARD_W       = 220
CARD_THUMB_H = 130
GRID_COLS    = 4


# ── Actor picker dialog ──────────────────────────────────────────────────────

class _ActorPickerDlg(QDialog):
    """Dialoogvenster om acteurs aan een bigfile te koppelen."""

    def __init__(self, parent, current_ids: list):
        super().__init__(parent)
        self.setWindowTitle("Acteurs koppelen")
        self.setModal(True)
        self.resize(320, 480)
        self.setStyleSheet("""
            QDialog      { background:#111; color:#ccc; }
            QScrollArea  { border:none; }
            QWidget#inner{ background:#111; }
        """)

        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.setContentsMargins(12, 12, 12, 12)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText("Zoek acteur…")
        self._search.setStyleSheet(
            "background:#1a1a1a; border:1px solid #333; border-radius:4px;"
            "padding:5px 8px; color:#ccc;"
        )
        self._search.textChanged.connect(self._filter)
        v.addWidget(self._search)

        # Scroll area with checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("inner")
        self._cb_layout = QVBoxLayout(inner)
        self._cb_layout.setContentsMargins(4, 4, 4, 4)
        self._cb_layout.setSpacing(2)
        scroll.setWidget(inner)
        v.addWidget(scroll, stretch=1)

        # OK / Cancel
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.setStyleSheet("color:#ccc;")
        v.addWidget(bb)

        # Populate checkboxes
        self._checkboxes: list[tuple[int, QCheckBox]] = []
        for a in db.get_all_actors():
            cb = QCheckBox(a['name'])
            cb.setStyleSheet("color:#ccc; padding:3px 6px;")
            cb.setChecked(a['id'] in current_ids)
            self._checkboxes.append((a['id'], cb))
            self._cb_layout.addWidget(cb)
        self._cb_layout.addStretch()

    def _filter(self, text: str):
        text = text.lower()
        for _aid, cb in self._checkboxes:
            cb.setVisible(not text or text in cb.text().lower())

    def get_selected_ids(self) -> list:
        return [aid for aid, cb in self._checkboxes if cb.isChecked()]


# ── BigFile card ─────────────────────────────────────────────────────────────

class _BigFileCard(QFrame):
    """Kaart met thumbnail, bestandsnaam, acteurs en knoppen."""

    play_requested  = pyqtSignal(str)   # full_path
    actors_changed  = pyqtSignal(int)   # bigfile_id
    thumb_requested = pyqtSignal(int)   # bigfile_id

    def __init__(self, record: dict, actor_names: list):
        super().__init__()
        self._record      = record
        self._actor_names = actor_names
        self._available   = Path(record['full_path']).exists()
        self._build()

    @property
    def bigfile_id(self) -> int:
        return self._record['id']

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
        lbl_name.setStyleSheet(
            f"color:{'#bbb' if self._available else '#444'}; font-size:10px; border:none;"
        )
        lbl_name.setToolTip(self._record['full_path'])
        v.addWidget(lbl_name)

        # Actors
        self._lbl_actors = QLabel(
            ", ".join(self._actor_names) if self._actor_names else "—"
        )
        self._lbl_actors.setWordWrap(True)
        self._lbl_actors.setFixedWidth(CARD_W - 12)
        self._lbl_actors.setStyleSheet("color:#555; font-size:9px; border:none;")
        v.addWidget(self._lbl_actors)

        # Availability badge
        if not self._available:
            badge = QLabel("niet beschikbaar")
            badge.setStyleSheet(
                "color:#6b3a1f; font-size:9px; background:#1a0d06;"
                " border:1px solid #4a2a10; border-radius:3px; padding:1px 5px;"
            )
            v.addWidget(badge)

        # Buttons
        bh = QHBoxLayout()
        bh.setContentsMargins(0, 2, 0, 0)
        bh.setSpacing(4)

        btn_play = QPushButton("▶")
        btn_play.setFixedHeight(26)
        btn_play.setEnabled(self._available)
        btn_play.setToolTip("Afspelen")
        if self._available:
            btn_play.setStyleSheet(
                "QPushButton{background:#1a2a1a;border:1px solid #2a5a2a;"
                "border-radius:4px;color:#55e055;font-size:13px;border-bottom:none;}"
                "QPushButton:hover{background:#1f3f1f;}"
                "QPushButton:pressed{background:#55e055;color:#000;}"
            )
        else:
            btn_play.setStyleSheet(
                "QPushButton{background:#141414;border:1px solid #2a2a2a;"
                "border-radius:4px;color:#333;font-size:13px;border-bottom:none;}"
            )
        btn_play.clicked.connect(lambda: self.play_requested.emit(self._record['full_path']))
        bh.addWidget(btn_play)

        btn_thumb = QPushButton("📷")
        btn_thumb.setFixedHeight(26)
        btn_thumb.setToolTip("Thumbnail van huidige spelerframe")
        btn_thumb.setStyleSheet(
            "QPushButton{background:#1a1a2a;border:1px solid #2a2a5a;"
            "border-radius:4px;color:#5588ee;font-size:12px;border-bottom:none;}"
            "QPushButton:hover{background:#1f1f3f;}"
            "QPushButton:pressed{background:#5588ee;color:#000;}"
        )
        btn_thumb.clicked.connect(lambda: self.thumb_requested.emit(self._record['id']))
        bh.addWidget(btn_thumb)

        btn_actors = QPushButton("◉")
        btn_actors.setFixedHeight(26)
        btn_actors.setToolTip("Acteurs koppelen")
        btn_actors.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #323232;"
            "border-radius:4px;color:#777;font-size:13px;border-bottom:none;}"
            "QPushButton:hover{background:#242424;}"
            "QPushButton:pressed{background:#444;color:#fff;}"
        )
        btn_actors.clicked.connect(self._open_actor_picker)
        bh.addWidget(btn_actors)

        v.addLayout(bh)

    # ── Thumbnail helpers ────────────────────────────────────────────────

    def _reload_thumb(self):
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
        self._lbl_actors.setText(", ".join(names) if names else "—")

    # ── Actor picker ─────────────────────────────────────────────────────

    def _open_actor_picker(self):
        current_ids = db.get_bigfile_actor_ids(self._record['id'])
        dlg = _ActorPickerDlg(self.window(), current_ids)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_ids = dlg.get_selected_ids()
            db.set_bigfile_actors(self._record['id'], new_ids)
            self.actors_changed.emit(self._record['id'])


# ── Archive card (compact, read-only) ────────────────────────────────────────

ARCHIVE_W       = 180
ARCHIVE_THUMB_H = 101   # 16:9 voor 180px breed
ARCHIVE_COLS    = 5


class _ArchiveCard(QFrame):
    """Compacte thumbnailkaart voor de archiefsectie — geen actieknoppen.

    Toont thumbnail + bestandsnaam. Grijs/dimmed als bestand niet beschikbaar.
    Dubbelklik speelt af als bestand bereikbaar is.
    """

    play_requested = pyqtSignal(str)   # full_path

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

    play_file_requested     = pyqtSignal(str)   # pad naar videobestand
    capture_thumb_requested = pyqtSignal(int)   # bigfile_id waarvoor screenshot gewenst is

    def __init__(self):
        super().__init__()
        self._pending_thumb_id: int | None         = None
        self._cards:         dict[int, _BigFileCard]  = {}  # bigfile_id → actieve kaart
        self._archive_cards: dict[int, _ArchiveCard]  = {}  # bigfile_id → archiefkaart
        self._actor_map:     dict[int, str]           = {}  # actor_id → naam (cache)
        self._build_ui()
        self._refresh()

    def showEvent(self, event):
        """Herscant + ververs bij elke tabwissel naar DATA."""
        super().showEvent(event)
        self._rescan_and_refresh()

    # ── Rescan ───────────────────────────────────────────────────────────

    def _rescan_and_refresh(self):
        self._rescan_known_folders()
        self._refresh()

    def _rescan_known_folders(self):
        """Doorzoek alle bekende parent-mappen op nieuwe videobestanden.

        Slaat mappen over die niet bereikbaar zijn (externe schijf weg, etc.).
        """
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
                    if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
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
        hint_top = QLabel("dubbelklik = afspelen  •  📷 = thumbnail  •  ◉ = acteurs")
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
        arch_hdr.setFixedHeight(28)
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
        hint_arch = QLabel(
            "alle geïndexeerde bestanden — ook niet bereikbare  •  "
            "dubbelklik = afspelen als beschikbaar"
        )
        hint_arch.setStyleSheet("color:#1a1a28; font-size:9px;")
        ah.addWidget(hint_arch)
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
            card.actors_changed.connect(self._on_actors_changed)
            card.thumb_requested.connect(self._on_thumb_requested)
            self._cards[rec['id']] = card

            row, col = divmod(i, GRID_COLS)
            self._grid_layout.addWidget(card, row, col)

    def _refresh_archive(self, records: list | None = None):
        """Vul de onderste archiefsectie met ALLE geïndexeerde bestanden."""
        if records is None:
            records = db.get_all_bigfiles()

        self._archive_cards.clear()

        while self._arch_layout.count():
            item = self._arch_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, rec in enumerate(records):
            # Thumbnail fallback
            display_rec = dict(rec)
            bf_thumb = rec.get('thumbnail_path') or ''
            if not bf_thumb or not Path(bf_thumb).exists():
                film_thumb = db.get_best_film_thumbnail(rec['full_path'])
                if film_thumb:
                    display_rec['thumbnail_path'] = film_thumb

            card = _ArchiveCard(display_rec)
            card.play_requested.connect(self._on_play)
            self._archive_cards[rec['id']] = card

            row, col = divmod(i, ARCHIVE_COLS)
            self._arch_layout.addWidget(card, row, col)

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
        added = 0
        for f in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
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

    def _on_thumb_requested(self, bigfile_id: int):
        self._pending_thumb_id = bigfile_id
        self.capture_thumb_requested.emit(bigfile_id)

    def _on_actors_changed(self, bigfile_id: int):
        card = self._cards.get(bigfile_id)
        if card is None:
            return
        actor_ids   = db.get_bigfile_actor_ids(bigfile_id)
        actor_names = [self._actor_map[aid] for aid in actor_ids if aid in self._actor_map]
        card.update_actors(actor_names)

    # ── List double-click (actieve sectie) ────────────────────────────────

    def _on_list_dblclick(self, item: QListWidgetItem):
        bigfile_id = item.data(Qt.ItemDataRole.UserRole)
        for rec in db.get_all_bigfiles():
            if rec['id'] == bigfile_id:
                self._on_play(rec['full_path'])
                return

    # ── Thumbnail callback (called from player.py after screenshot) ───────

    def on_thumbnail_saved(self, thumb_path: str):
        """Wordt aangeroepen door player.py nadat een bigfile-thumbnail is opgeslagen."""
        if self._pending_thumb_id is None:
            return
        bigfile_id             = self._pending_thumb_id
        self._pending_thumb_id = None
        db.set_bigfile_thumbnail(bigfile_id, thumb_path)
        # Beide secties direct bijwerken
        card = self._cards.get(bigfile_id)
        if card:
            card.update_thumbnail(thumb_path)
        arch_card = self._archive_cards.get(bigfile_id)
        if arch_card:
            arch_card.update_thumbnail(thumb_path)
