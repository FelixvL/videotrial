#!/usr/bin/env python3
"""
CineMarker — Films browser panel
"""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QLineEdit, QFrame,
    QFileDialog, QStyledItemDelegate, QStyle
)
from PyQt6.QtCore import Qt, QSize, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap

import database as db


VIDEO_EXTS = {
    '.mp4', '.avi', '.mov', '.wmv', '.mkv', '.flv',
    '.webm', '.m4v', '.mpg', '.mpeg', '.ts', '.mts',
    '.divx', '.vob', '.rmvb', '.rm', '.3gp',
}


# ─────────────────────────────────────────────
#  Delegate
# ─────────────────────────────────────────────

class FilmDelegate(QStyledItemDelegate):

    THUMB_W = 85
    ROW_H   = 54

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict = {}

    def invalidate_cache(self):
        self._cache.clear()

    def _get_thumb(self, path: str) -> QPixmap | None:
        if path in self._cache:
            return self._cache[path]
        if not path or not os.path.exists(path):
            self._cache[path] = None
            return None
        raw = QPixmap(path)
        if raw.isNull():
            self._cache[path] = None
            return None
        scaled = raw.scaled(
            self.THUMB_W, self.ROW_H,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        ox = (scaled.width()  - self.THUMB_W) // 2
        oy = (scaled.height() - self.ROW_H)   // 2
        pix = scaled.copy(ox, oy, self.THUMB_W, self.ROW_H)
        self._cache[path] = pix
        return pix

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            super().paint(painter, option, index)
            return

        r        = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered  = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()

        # Background
        if selected:
            painter.fillRect(r, QColor('#1e1600'))
        elif hovered:
            painter.fillRect(r, QColor('#111111'))

        # Left column: thumbnail or play icon
        thumb_r = QRect(r.x(), r.y(), self.THUMB_W, r.height())
        pix = self._get_thumb(data.get('thumbnail', ''))
        if pix:
            painter.drawPixmap(r.x(), r.y() + (r.height() - pix.height()) // 2, pix)
            if selected:
                painter.fillRect(thumb_r, QColor(232, 184, 109, 40))
        else:
            painter.fillRect(thumb_r, QColor('#0c0c0c'))
            pf = QFont(painter.font())
            pf.setPointSize(14)
            painter.setFont(pf)
            painter.setPen(QColor('#e8b86d') if selected else QColor('#2a2a2a'))
            painter.drawText(thumb_r, Qt.AlignmentFlag.AlignCenter, '▶')

        # Divider
        painter.setPen(QPen(QColor('#1a1a1a'), 1))
        painter.drawLine(r.x() + self.THUMB_W, r.y(), r.x() + self.THUMB_W, r.bottom())

        # Film name
        nf = QFont(painter.font())
        nf.setPointSize(12)
        nf.setBold(False)
        painter.setFont(nf)
        painter.setPen(QColor('#e8b86d') if selected else QColor('#cccccc'))
        name_r = QRect(r.x() + self.THUMB_W + 12, r.y(), r.width() - self.THUMB_W - 180, r.height())
        painter.drawText(
            name_r,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            data.get('name', '')
        )

        # Meta (right — ext + size)
        mf = QFont(painter.font())
        mf.setPointSize(10)
        painter.setFont(mf)
        painter.setPen(QColor('#363636'))
        meta_r = QRect(r.right() - 155, r.y(), 148, r.height())
        painter.drawText(
            meta_r,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            data.get('meta', '')
        )

        # Bottom separator
        painter.setPen(QPen(QColor('#141414'), 1))
        painter.drawLine(r.x() + self.THUMB_W + 1, r.bottom(), r.right(), r.bottom())

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(max(option.rect.width(), 200), self.ROW_H)


# ─────────────────────────────────────────────
#  Panel
# ─────────────────────────────────────────────

class FilmsPanel(QWidget):

    play_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._all_items: list = []
        self._build_ui()
        folder = db.get_setting('film_folder', '')
        if folder:
            self._update_folder_label(folder)
            self._scan_folder(folder)

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Toolbar
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet(
            "QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; }"
        )
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(10)

        lbl = QLabel("FILMS")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px;")
        b.addWidget(lbl)

        self.lbl_folder = QLabel("Geen map geselecteerd")
        self.lbl_folder.setStyleSheet("color: #383838; font-size: 10px;")
        b.addWidget(self.lbl_folder)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: #333; font-size: 10px;")
        b.addWidget(self.lbl_count)

        b.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Zoeken...")
        self.search_input.setFixedWidth(220)
        self.search_input.textChanged.connect(self._filter)
        b.addWidget(self.search_input)

        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedSize(28, 28)
        btn_refresh.setToolTip("Herlaad map")
        btn_refresh.clicked.connect(self._refresh)
        b.addWidget(btn_refresh)

        btn_folder = QPushButton("📁  Kies map")
        btn_folder.setFixedHeight(28)
        btn_folder.clicked.connect(self._pick_folder)
        b.addWidget(btn_folder)

        v.addWidget(bar)

        # Film list
        self.film_list = QListWidget()
        self.film_list.setMouseTracking(True)
        self.film_list.setStyleSheet(
            "QListWidget { background: #0a0a0a; border: none; outline: none; }"
            "QListWidget::item { border: none; background: transparent; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        self.film_list.setItemDelegate(FilmDelegate())
        self.film_list.itemDoubleClicked.connect(self._on_double_click)
        v.addWidget(self.film_list)

        # Footer hint
        foot = QFrame()
        foot.setFixedHeight(26)
        foot.setStyleSheet(
            "QFrame { background: #080808; border-top: 1px solid #1a1a1a; }"
        )
        fh = QHBoxLayout(foot)
        fh.setContentsMargins(12, 0, 12, 0)
        hint = QLabel("Dubbelklik op een film om af te spelen")
        hint.setStyleSheet("color: #2a2a2a; font-size: 10px;")
        fh.addWidget(hint)
        fh.addStretch()
        v.addWidget(foot)

    # ── Folder ───────────────────────────────────

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecteer filmmap")
        if folder:
            db.set_setting('film_folder', folder)
            self._update_folder_label(folder)
            self._scan_folder(folder)

    def _update_folder_label(self, folder):
        p = Path(folder)
        self.lbl_folder.setText(p.name or folder)
        self.lbl_folder.setToolTip(folder)

    def _refresh(self):
        folder = db.get_setting('film_folder', '')
        if folder:
            self._scan_folder(folder)

    # ── Scan ─────────────────────────────────────

    def _scan_folder(self, folder):
        self.film_list.clear()
        self._all_items.clear()
        self.film_list.itemDelegate().invalidate_cache()

        folder_path = Path(folder)
        if not folder_path.exists():
            return

        film_thumbs = {f['file_path']: f.get('thumbnail', '') for f in db.get_all_films()}

        films = sorted(
            (f for f in folder_path.iterdir() if f.suffix.lower() in VIDEO_EXTS),
            key=lambda f: f.name.lower()
        )

        for fp in films:
            try:
                mb = fp.stat().st_size / (1024 * 1024)
                size_str = f"{mb / 1024:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"
            except OSError:
                size_str = ''

            ext  = fp.suffix.upper().lstrip('.')
            meta = f"{ext}  ·  {size_str}" if size_str else ext

            item = QListWidgetItem()
            item.setSizeHint(QSize(100, FilmDelegate.ROW_H))
            item.setData(Qt.ItemDataRole.UserRole, {
                'path': str(fp),
                'name': fp.stem,
                'meta': meta,
                'thumbnail': film_thumbs.get(str(fp), ''),
            })
            self.film_list.addItem(item)
            self._all_items.append(item)

        self._update_count()

    # ── Filter ───────────────────────────────────

    def _filter(self, query: str):
        q = query.lower()
        for item in self._all_items:
            d    = item.data(Qt.ItemDataRole.UserRole)
            name = d.get('name', '').lower() if d else ''
            item.setHidden(bool(q) and q not in name)
        self._update_count()

    def _update_count(self):
        visible = sum(1 for i in self._all_items if not i.isHidden())
        total   = len(self._all_items)
        self.lbl_count.setText(f"  {visible} / {total} films")

    # ── Play ─────────────────────────────────────

    def _on_double_click(self, item):
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            self.play_requested.emit(d['path'])
