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
from PyQt6.QtGui import QPixmap, QPainter

import database as db


IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif'}


def _count_images(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for f in folder.iterdir() if f.suffix.lower() in IMAGE_EXTS)


def _already_sorted_names(folder: Path) -> set:
    """Return set of filenames (e.g. 'foto.jpg') present in p/ or m/."""
    names = set()
    for sub in ('p', 'm'):
        d = folder / sub
        if d.exists():
            for f in d.iterdir():
                if f.suffix.lower() in IMAGE_EXTS:
                    names.add(f.name)
    return names


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

        # p / m counters
        self.lbl_p = QLabel("p: 0")
        self.lbl_p.setStyleSheet(
            "color: #2a6b2a; font-size: 11px; padding: 2px 8px;"
            "background: #081808; border: 1px solid #1f4b1f; border-radius: 4px;"
        )
        b.addWidget(self.lbl_p)

        self.lbl_m = QLabel("m: 0")
        self.lbl_m.setStyleSheet(
            "color: #6b2a2a; font-size: 11px; padding: 2px 8px;"
            "background: #1a0808; border: 1px solid #4b1f1f; border-radius: 4px;"
        )
        b.addWidget(self.lbl_m)

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

        self.btn_minus = QPushButton("−  m")
        self.btn_minus.setFixedHeight(56)
        self.btn_minus.setStyleSheet("""
            QPushButton {
                background: #1a0808;
                border: 2px solid #6b1f1f;
                border-radius: 8px;
                color: #e05555;
                font-size: 28px;
                font-weight: bold;
            }
            QPushButton:hover { background: #2a1010; border-color: #e05555; }
            QPushButton:pressed { background: #e05555; color: #fff; }
        """)
        self.btn_minus.clicked.connect(self._move_m)
        bh.addWidget(self.btn_minus)

        self.btn_plus = QPushButton("+  p")
        self.btn_plus.setFixedHeight(56)
        self.btn_plus.setStyleSheet("""
            QPushButton {
                background: #081808;
                border: 2px solid #1f6b1f;
                border-radius: 8px;
                color: #55e055;
                font-size: 28px;
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
        hint = QLabel("← →  bladeren     Spatie → map p     M → map m")
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
        already = _already_sorted_names(self._folder)
        self._photos = sorted(
            [f for f in self._folder.iterdir()
             if f.suffix.lower() in IMAGE_EXTS and f.name not in already],
            key=lambda f: f.name.lower(),
        )
        self._index = 0
        self._update_subcounts()
        self._show_current()

    # ── Display ─────────────────────────────────

    def _show_current(self):
        if not self._photos:
            self.photo_view.set_pixmap(None)
            self.lbl_count.setText("Klaar")
            self.lbl_name.setText("")
            return
        self.lbl_count.setText(f"  {self._index + 1} / {len(self._photos)}")
        fp = self._photos[self._index]
        self.lbl_name.setText(fp.name)
        self.photo_view.set_pixmap(QPixmap(str(fp)))

    def _update_subcounts(self):
        if self._folder is None:
            return
        self.lbl_p.setText(f"p: {_count_images(self._folder / 'p')}")
        self.lbl_m.setText(f"m: {_count_images(self._folder / 'm')}")

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
        shutil.copy2(str(fp), str(dest))
        self._photos.pop(self._index)
        if self._index >= len(self._photos) and self._index > 0:
            self._index -= 1
        self._update_subcounts()
        self._show_current()

    def _move_p(self):
        self._move_to('p')

    def _move_m(self):
        self._move_to('m')

    # ── Navigate ────────────────────────────────

    def _prev(self):
        if not self._photos:
            return
        self._index = (self._index - 1) % len(self._photos)
        self._show_current()

    def _next(self):
        if not self._photos:
            return
        self._index = (self._index + 1) % len(self._photos)
        self._show_current()

    # Keyboard: afgehandeld door globale shortcuts in player.py
