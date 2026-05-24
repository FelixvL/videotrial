#!/usr/bin/env python3
"""
CineMarker — Foto-sorteerpaneel
Blader door foto's in een map, stuur ze naar /p of /m
"""

import shutil
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QKeyEvent

import database as db


IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif'}


class _PhotoView(QWidget):

    def __init__(self):
        super().__init__()
        self._pixmap = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width()  - scaled.width())  // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)


class SorterPanel(QWidget):

    def __init__(self):
        super().__init__()
        self._folder: Path | None = None
        self._photos: list = []
        self._index: int = 0
        self._build_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        folder = db.get_setting('sorter_folder', '')
        if folder:
            self._load_folder(folder)

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Toolbar ───────────────────────────
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; }")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(12)

        lbl = QLabel("SORTEREN")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px;")
        b.addWidget(lbl)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: #666; font-size: 11px;")
        b.addWidget(self.lbl_count)

        self.lbl_name = QLabel("")
        self.lbl_name.setStyleSheet("color: #383838; font-size: 10px;")
        b.addWidget(self.lbl_name)

        b.addStretch()

        btn_folder = QPushButton("📁  Kies map")
        btn_folder.setFixedHeight(28)
        btn_folder.clicked.connect(self._pick_folder)
        b.addWidget(btn_folder)

        v.addWidget(bar)

        # ── Photo ─────────────────────────────
        self.photo_view = _PhotoView()
        v.addWidget(self.photo_view, stretch=1)

        # ── Buttons ───────────────────────────
        btn_row = QFrame()
        btn_row.setFixedHeight(80)
        btn_row.setStyleSheet("QFrame { background: #0a0a0a; border-top: 1px solid #1a1a1a; }")
        bh = QHBoxLayout(btn_row)
        bh.setContentsMargins(24, 12, 24, 12)
        bh.setSpacing(24)

        self.btn_minus = QPushButton("−")
        self.btn_minus.setFixedHeight(56)
        self.btn_minus.setStyleSheet("""
            QPushButton {
                background: #1a0808;
                border: 2px solid #6b1f1f;
                border-radius: 8px;
                color: #e05555;
                font-size: 32px;
                font-weight: bold;
            }
            QPushButton:hover { background: #2a1010; border-color: #e05555; }
            QPushButton:pressed { background: #e05555; color: #fff; }
        """)
        self.btn_minus.clicked.connect(self._move_m)
        bh.addWidget(self.btn_minus)

        self.btn_plus = QPushButton("+")
        self.btn_plus.setFixedHeight(56)
        self.btn_plus.setStyleSheet("""
            QPushButton {
                background: #081808;
                border: 2px solid #1f6b1f;
                border-radius: 8px;
                color: #55e055;
                font-size: 32px;
                font-weight: bold;
            }
            QPushButton:hover { background: #102a10; border-color: #55e055; }
            QPushButton:pressed { background: #55e055; color: #000; }
        """)
        self.btn_plus.clicked.connect(self._move_p)
        bh.addWidget(self.btn_plus)

        v.addWidget(btn_row)

        # ── Footer hint ───────────────────────
        foot = QFrame()
        foot.setFixedHeight(26)
        foot.setStyleSheet("QFrame { background: #080808; border-top: 1px solid #141414; }")
        fh = QHBoxLayout(foot)
        fh.setContentsMargins(12, 0, 12, 0)
        hint = QLabel("+ of → of Spatie → map p     −  of ← → map m")
        hint.setStyleSheet("color: #2a2a2a; font-size: 10px;")
        fh.addWidget(hint)
        fh.addStretch()
        v.addWidget(foot)

    # ── Folder ──────────────────────────────────

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecteer fotomap")
        if folder:
            db.set_setting('sorter_folder', folder)
            self._load_folder(folder)

    def _load_folder(self, folder: str):
        self._folder = Path(folder)
        self._photos = sorted(
            [f for f in self._folder.iterdir() if f.suffix.lower() in IMAGE_EXTS],
            key=lambda f: f.name.lower(),
        )
        self._index = 0
        self._show_current()

    # ── Display ─────────────────────────────────

    def _show_current(self):
        if not self._photos:
            self.photo_view.set_pixmap(None)
            self.lbl_count.setText("Geen foto's meer")
            self.lbl_name.setText("")
            return
        self.lbl_count.setText(f"  {self._index + 1} / {len(self._photos)}")
        fp = self._photos[self._index]
        self.lbl_name.setText(fp.name)
        self.photo_view.set_pixmap(QPixmap(str(fp)))

    # ── Sort ────────────────────────────────────

    def _move_to(self, subfolder: str):
        if not self._photos or self._folder is None:
            return
        dest_dir = self._folder / subfolder
        dest_dir.mkdir(exist_ok=True)
        fp = self._photos[self._index]
        dest = dest_dir / fp.name
        if dest.exists():
            i = 1
            while dest.exists():
                dest = dest_dir / f"{fp.stem}_{i}{fp.suffix}"
                i += 1
        shutil.move(str(fp), str(dest))
        self._photos.pop(self._index)
        if self._index >= len(self._photos) and self._index > 0:
            self._index -= 1
        self._show_current()

    def _move_p(self):
        self._move_to('p')

    def _move_m(self):
        self._move_to('m')

    # ── Keyboard ────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Right, Qt.Key.Key_Space, Qt.Key.Key_Return):
            self._move_p()
        elif k in (Qt.Key.Key_Minus, Qt.Key.Key_Left):
            self._move_m()
        else:
            super().keyPressEvent(event)
