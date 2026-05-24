#!/usr/bin/env python3
"""
CineMarker - Professional Video Player
Requires: pip install python-mpv PyQt6
Requires: mpv and ffmpeg installed on system
"""

import sys
import os
import re
import json
import subprocess
import threading
from pathlib import Path
from datetime import datetime

# Add common mpv install locations to PATH so python-mpv can find the DLL
for _mpv_path in [r"C:\mpv", r"C:\Program Files\mpv", r"C:\Program Files (x86)\mpv"]:
    if os.path.isdir(_mpv_path):
        os.environ["PATH"] = _mpv_path + os.pathsep + os.environ["PATH"]
# Also add the script's own directory (handy if the DLL is placed alongside player.py)
os.environ["PATH"] = os.path.dirname(os.path.abspath(__file__)) + os.pathsep + os.environ["PATH"]

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFileDialog, QListWidget,
    QListWidgetItem, QLineEdit, QComboBox, QSpinBox,
    QProgressBar, QTabWidget, QStackedWidget, QFrame, QMessageBox,
    QInputDialog, QSizePolicy, QStatusBar, QScrollArea, QStyle, QMenu,
    QDialog, QDialogButtonBox
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, QThread, QSize, QEvent
)
from PyQt6.QtGui import QFont, QIcon, QKeySequence, QShortcut, QColor, QPalette, QPixmap, QCursor

try:
    import mpv
except OSError:
    app = QApplication(sys.argv)
    if sys.platform == 'win32':
        detail = (
            "Kan mpv-2.dll niet vinden.\n\n"
            "Download de Windows dev-build van mpv:\n"
            "https://sourceforge.net/projects/mpv-player-windows/files/libmpv/\n\n"
            "Pak mpv-2.dll uit en plaats hem in C:\\mpv\\ of naast player.py"
        )
    else:
        detail = "Installeer libmpv via je package manager:\n  sudo apt install libmpv-dev  (Debian/Ubuntu)\n  sudo dnf install mpv-libs     (Fedora)"
    QMessageBox.critical(None, "mpv niet gevonden", detail)
    sys.exit(1)
from actors_panel import ActorsPanel
from films_panel import FilmsPanel
from database_panel import DatabasePanel
from sorter_panel import SorterPanel
import database as db


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def format_time(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "00:00:00.000"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _fmt_hms(seconds: float) -> str:
    """HH:MM:SS without milliseconds, for the player time label."""
    if seconds is None or seconds < 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_time(time_str: str) -> float:
    """Parse HH:MM:SS.mmm or MM:SS or seconds"""
    try:
        parts = time_str.strip().replace(',', '.').split(':')
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except Exception:
        return 0.0


def markers_file_for(video_path: str) -> str:
    p = Path(video_path)
    return str(p.parent / f".{p.stem}_markers.json")


def load_markers(video_path: str) -> list:
    path = markers_file_for(video_path)
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return []


def save_markers(video_path: str, markers: list):
    path = markers_file_for(video_path)
    with open(path, 'w') as f:
        json.dump(markers, f, indent=2)


# ─────────────────────────────────────────────
#  FFmpeg worker threads
# ─────────────────────────────────────────────

class ThumbnailWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, video_path, timestamp, output_path):
        super().__init__()
        self.video_path = video_path
        self.timestamp = timestamp
        self.output_path = output_path

    def run(self):
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(self.timestamp),
            '-i', self.video_path,
            '-vframes', '1',
            '-q:v', '2',
            self.output_path
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            self.finished.emit(self.output_path)
        else:
            self.error.emit(result.stderr.decode())


class ConvertWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, input_path, output_path, codec, resolution, crf):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.codec = codec
        self.resolution = resolution
        self.crf = crf
        self._duration = None

    def run(self):
        # Get duration first
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', self.input_path],
            capture_output=True, text=True
        )
        try:
            self._duration = float(probe.stdout.strip())
        except Exception:
            self._duration = None

        vf = []
        if self.resolution != 'Original':
            w, h = self.resolution.split('x')
            vf.append(f"scale={w}:{h}")

        cmd = ['ffmpeg', '-y', '-i', self.input_path]
        if vf:
            cmd += ['-vf', ','.join(vf)]
        cmd += ['-c:v', self.codec, '-crf', str(self.crf), '-c:a', 'aac', '-b:a', '192k']
        cmd += ['-progress', 'pipe:1', '-nostats', self.output_path]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        while True:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith('out_time_ms='):
                try:
                    ms = int(line.split('=')[1].strip())
                    if self._duration:
                        pct = min(100, int((ms / 1_000_000) / self._duration * 100))
                        self.progress.emit(pct)
                except Exception:
                    pass

        proc.wait()
        if proc.returncode == 0:
            self.finished.emit(self.output_path)
        else:
            self.error.emit(proc.stderr.read())


# ─────────────────────────────────────────────
#  Custom Widgets
# ─────────────────────────────────────────────

class TimelineSlider(QSlider):
    """Slider that supports click-to-seek anywhere"""
    seeked = pyqtSignal(float)

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(0, 10000)
        self._markers = []

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = self._pos_to_value(event.position().x())
            self.setValue(val)
            self.seeked.emit(val / 10000)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            val = self._pos_to_value(event.position().x())
            self.setValue(val)
            self.seeked.emit(val / 10000)
        super().mouseMoveEvent(event)

    def _pos_to_value(self, x):
        w = self.width()
        return int(max(0, min(10000, x / w * 10000)))


class ClickableLabel(QLabel):
    clicked = pyqtSignal()
    def mousePressEvent(self, e):
        self.clicked.emit()


# ─────────────────────────────────────────────
#  Actor Link Overlay  (floating over player)
# ─────────────────────────────────────────────

class _ActorLinkOverlay(QFrame):
    link_requested = pyqtSignal(dict)

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedWidth(270)
        self.setFixedHeight(320)
        self.setStyleSheet("""
            _ActorLinkOverlay, QFrame#actorOverlay {
                background: #111;
                border: 1px solid #333;
                border-radius: 8px;
            }
            QLineEdit {
                background: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                padding: 6px 8px;
                color: #e0e0e0;
                font-size: 13px;
            }
            QListWidget {
                background: #0e0e0e;
                border: none;
                color: #ccc;
                font-size: 12px;
            }
            QListWidget::item { padding: 7px 10px; border-bottom: 1px solid #181818; }
            QListWidget::item:hover { background: #1a1a1a; }
            QListWidget::item:selected { background: #2a2200; color: #e8b86d; }
        """)
        self._actors: list = []
        self._build_ui()
        self.hide()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        hdr = QHBoxLayout()
        lbl = QLabel("ACTEUR KOPPELEN")
        lbl.setStyleSheet("color: #555; font-size: 9px; letter-spacing: 3px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        btn_x = QPushButton("✕")
        btn_x.setFixedSize(20, 20)
        btn_x.setStyleSheet(
            "QPushButton { border: none; color: #444; background: transparent; }"
            "QPushButton:hover { color: #e0e0e0; }"
        )
        btn_x.clicked.connect(self.hide)
        hdr.addWidget(btn_x)
        v.addLayout(hdr)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Naam zoeken...")
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)

        self.actor_list = QListWidget()
        self.actor_list.itemClicked.connect(self._on_actor_click)
        v.addWidget(self.actor_list)

    def show_overlay(self):
        self._actors = db.get_all_actors()
        self.search.clear()
        self._filter('')
        p = self.parent()
        if p:
            self.move(p.width() - self.width() - 16, 56)
        self.show()
        self.raise_()
        self.search.setFocus()

    def _filter(self, text: str):
        self.actor_list.clear()
        q = text.lower()
        for a in self._actors:
            if not q or q in a.get('name', '').lower():
                item = QListWidgetItem(a['name'])
                item.setData(Qt.ItemDataRole.UserRole, a)
                self.actor_list.addItem(item)

    def _on_actor_click(self, item):
        a = item.data(Qt.ItemDataRole.UserRole)
        if a:
            self.link_requested.emit(a)
            self.hide()


# ─────────────────────────────────────────────
#  Film actors overlay (floating, selectable)
# ─────────────────────────────────────────────

from PyQt6.QtCore import pyqtSignal as _pyqtSignal


class _FilmActorsOverlay(QWidget):
    """Floating overlay at bottom-left of video.
    Row 1: actor thumbnails. Row 2: category icons.
    Everything drawn in paintEvent — no child widgets for images."""

    marker_requested    = _pyqtSignal(list, list)   # actors, categories
    thumbnail_requested = _pyqtSignal()

    TW, TH   = 52, 62   # actor thumb dims
    CW, CH   = 42, 42   # category icon dims
    SPACING  = 6
    PAD      = 6
    ROW_GAP  = 8
    BTN_W    = 36
    CELL_A   = TH + 16  # actor row height  (78)
    CELL_C   = CH + 14  # category row height (56)
    TOTAL_H  = PAD + CELL_A + ROW_GAP + CELL_C + PAD   # 154

    def __init__(self, main_win, video_container):
        super().__init__(main_win,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._vc = video_container

        self._actors:    list = []
        self._pixmaps:   list = []
        self._selected:  set  = set()   # actor ids

        self._cats:      list = []
        self._cat_pixes: list = []
        self._cat_sel:   set  = set()   # category ids

        self.setFixedHeight(self.TOTAL_H)

        self._btn_marker = QPushButton("◉", self)
        self._btn_marker.setFixedSize(self.BTN_W, self.BTN_W)
        self._btn_marker.setToolTip("Marker met geselecteerde acteurs en categorieën")
        self._btn_marker.setStyleSheet(
            "QPushButton { background: #1a1000; border: 1px solid #6b4a00;"
            "  border-radius: 4px; color: #e8b86d; font-size: 16px; }"
            "QPushButton:hover { background: #2a1a00; border-color: #e8b86d; }"
            "QPushButton:pressed { background: #e8b86d; color: #000; }"
        )
        self._btn_marker.clicked.connect(
            lambda: self.marker_requested.emit(
                self.selected_actors(), self.selected_categories()))

        self._btn_thumb = QPushButton("⊡", self)
        self._btn_thumb.setFixedSize(self.BTN_W, self.BTN_W)
        self._btn_thumb.setToolTip("Sla huidig frame op als filmthumbnail")
        self._btn_thumb.setStyleSheet(
            "QPushButton { background: #001a1a; border: 1px solid #006b6b;"
            "  border-radius: 4px; color: #55dede; font-size: 16px; }"
            "QPushButton:hover { background: #002a2a; border-color: #55dede; }"
            "QPushButton:pressed { background: #55dede; color: #000; }"
        )
        self._btn_thumb.clicked.connect(self.thumbnail_requested)

        self._btn_add_cat = QPushButton("+", self)
        self._btn_add_cat.setFixedSize(self.BTN_W, self.BTN_W)
        self._btn_add_cat.setToolTip("Categorie toevoegen")
        self._btn_add_cat.setStyleSheet(
            "QPushButton { background: #0a0a1a; border: 1px solid #2a2a6b;"
            "  border-radius: 4px; color: #5555cc; font-size: 18px; font-weight: bold; }"
            "QPushButton:hover { background: #10103a; border-color: #5555cc; color: #8888ff; }"
            "QPushButton:pressed { background: #5555cc; color: #fff; }"
        )
        self._btn_add_cat.clicked.connect(self._add_category)

        self._place_buttons()
        main_win.installEventFilter(self)

    # ── Layout helpers ───────────────────────────

    def _actor_row_width(self):
        n = len(self._actors)
        return n * (self.TW + self.SPACING) - (self.SPACING if n else 0)

    def _cat_row_width(self):
        n = len(self._cats)
        return n * (self.CW + self.SPACING) - (self.SPACING if n else 0)

    def _content_width(self):
        return max(self._actor_row_width(), self._cat_row_width())

    def _total_width(self):
        return self.PAD + self._content_width() + self.SPACING + self.BTN_W * 2 + 4 + self.PAD

    def _place_buttons(self):
        x = self.PAD + self._content_width() + self.SPACING
        # ◉ and ⊡ side by side, centered in actor row
        btn_y = self.PAD + (self.CELL_A - self.BTN_W) // 2
        self._btn_marker.move(x, btn_y)
        self._btn_thumb.move(x + self.BTN_W + 4, btn_y)
        # + button centered in category row
        cat_row_y = self.PAD + self.CELL_A + self.ROW_GAP
        self._btn_add_cat.move(x, cat_row_y + (self.CELL_C - self.BTN_W) // 2)

    # ── Paint ────────────────────────────────────

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter, QPen, QFontMetrics
        from PyQt6.QtCore import QRect as _R
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = QFontMetrics(p.font())

        # ── Actor row ────────────────────────────
        x = self.PAD
        for actor, pix in zip(self._actors, self._pixmaps):
            aid = actor['id']
            sel = aid in self._selected
            p.fillRect(x, self.PAD, self.TW, self.TH,
                       QColor('#3c3200') if sel else QColor('#1e1e1e'))
            if pix and not pix.isNull():
                p.drawPixmap(x, self.PAD, pix)
            else:
                p.fillRect(x, self.PAD, self.TW, self.TH, QColor('#2a2a2a'))
            if sel:
                from PyQt6.QtGui import QPen as _Pen
                p.setPen(_Pen(QColor('#e8b86d'), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(x, self.PAD, self.TW - 1, self.TH - 1)
                p.setPen(Qt.PenStyle.NoPen)
            p.setPen(QColor('#aaa') if sel else QColor('#555'))
            p.drawText(_R(x, self.PAD + self.TH + 2, self.TW, 13),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       fm.elidedText(actor.get('name', ''), Qt.TextElideMode.ElideRight, self.TW))
            p.setPen(Qt.PenStyle.NoPen)
            x += self.TW + self.SPACING

        # ── Category row ─────────────────────────
        cat_y = self.PAD + self.CELL_A + self.ROW_GAP
        x = self.PAD
        for cat, cpix in zip(self._cats, self._cat_pixes):
            cid = cat['id']
            sel = cid in self._cat_sel
            p.fillRect(x, cat_y, self.CW, self.CH,
                       QColor('#001a3c') if sel else QColor('#141414'))
            if cpix and not cpix.isNull():
                p.drawPixmap(x, cat_y, cpix)
            else:
                p.fillRect(x, cat_y, self.CW, self.CH, QColor('#1a1a1a'))
                p.setPen(QColor('#333'))
                p.drawText(_R(x, cat_y, self.CW, self.CH), Qt.AlignmentFlag.AlignCenter, '?')
                p.setPen(Qt.PenStyle.NoPen)
            if sel:
                from PyQt6.QtGui import QPen as _Pen
                p.setPen(_Pen(QColor('#6db8e8'), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(x, cat_y, self.CW - 1, self.CH - 1)
                p.setPen(Qt.PenStyle.NoPen)
            p.setPen(QColor('#aaa') if sel else QColor('#555'))
            p.drawText(_R(x, cat_y + self.CH + 2, self.CW, 12),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       fm.elidedText(cat.get('name', ''), Qt.TextElideMode.ElideRight, self.CW))
            p.setPen(Qt.PenStyle.NoPen)
            x += self.CW + self.SPACING

    # ── Mouse ────────────────────────────────────

    def mousePressEvent(self, event):
        # Actor row
        x = self.PAD
        for actor in self._actors:
            if (x <= event.pos().x() <= x + self.TW and
                    self.PAD <= event.pos().y() <= self.PAD + self.TH):
                if event.button() == Qt.MouseButton.LeftButton:
                    aid = actor['id']
                    if aid in self._selected:
                        self._selected.discard(aid)
                    else:
                        self._selected.add(aid)
                    self.update()
                return
            x += self.TW + self.SPACING

        # Category row
        cat_y = self.PAD + self.CELL_A + self.ROW_GAP
        x = self.PAD
        for cat in self._cats:
            if (x <= event.pos().x() <= x + self.CW and
                    cat_y <= event.pos().y() <= cat_y + self.CH):
                if event.button() == Qt.MouseButton.LeftButton:
                    cid = cat['id']
                    if cid in self._cat_sel:
                        self._cat_sel.discard(cid)
                    else:
                        self._cat_sel.add(cid)
                    self.update()
                elif event.button() == Qt.MouseButton.RightButton:
                    self._delete_category_menu(cat)
                return
            x += self.CW + self.SPACING

        super().mousePressEvent(event)

    # ── Event filter & reposition ────────────────

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            self._reposition()
        return False

    def _reposition(self):
        vc = self._vc
        if not vc.isVisible():
            return
        tl = vc.mapToGlobal(vc.rect().topLeft())
        w = min(self._total_width(), vc.width() - 20)
        w = max(self.BTN_W * 2 + self.PAD * 2 + 4, w)
        self.setFixedWidth(w)
        self._place_buttons()
        self.move(tl.x() + 8, tl.y() + vc.height() - self.TOTAL_H - 8)

    # ── Data ─────────────────────────────────────

    def refresh(self, film_id: int | None):
        self._actors.clear(); self._pixmaps.clear(); self._selected.clear()
        self._cat_sel.clear()

        if film_id is None:
            self.hide()
            return

        for actor in db.get_actors_for_film(film_id):
            self._actors.append(actor)
            self._selected.add(actor['id'])   # pre-select all film actors
            photos = db.get_actor_photos(actor['id'])
            path = photos[0]['photo_path'] if photos else ''
            pix = None
            if path:
                raw = QPixmap(path)
                if not raw.isNull():
                    scaled = raw.scaled(self.TW, self.TH,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
                    ox = (scaled.width()  - self.TW) // 2
                    oy = (scaled.height() - self.TH) // 2
                    pix = scaled.copy(ox, oy, self.TW, self.TH)
            self._pixmaps.append(pix)

        self._reload_categories()
        self._reposition()
        self.update()
        self.show()
        self.raise_()

    def _reload_categories(self):
        self._cats.clear(); self._cat_pixes.clear()
        for cat in db.get_all_categories():
            self._cats.append(cat)
            path = cat.get('icon_path', '')
            pix = None
            if path:
                raw = QPixmap(path)
                if not raw.isNull():
                    scaled = raw.scaled(self.CW, self.CH,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
                    ox = (scaled.width()  - self.CW) // 2
                    oy = (scaled.height() - self.CH) // 2
                    pix = scaled.copy(ox, oy, self.CW, self.CH)
            self._cat_pixes.append(pix)

    def selected_actors(self) -> list:
        return [a for a in self._actors if a['id'] in self._selected]

    def selected_categories(self) -> list:
        return [c for c in self._cats if c['id'] in self._cat_sel]

    # ── Category management ──────────────────────

    def _add_category(self):
        name, ok = QInputDialog.getText(self, "Categorie toevoegen", "Naam:")
        if not ok or not name.strip():
            return
        icon_path, _ = QFileDialog.getOpenFileName(
            self, "Kies icoon (optioneel)", "",
            "Afbeeldingen (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tiff)"
        )
        db.create_category(name.strip(), icon_path)
        self._reload_categories()
        self._reposition()
        self.update()

    def _delete_category_menu(self, cat):
        menu = QMenu(self)
        act = menu.addAction(f"Verwijder  '{cat['name']}'")
        if menu.exec(QCursor.pos()) == act:
            db.delete_category(cat['id'])
            self._cat_sel.discard(cat['id'])
            self._reload_categories()
            self._reposition()
            self.update()


# ─────────────────────────────────────────────
#  Actor photo search (inside panel overlay)
# ─────────────────────────────────────────────

class _PhotoWidget(QWidget):
    """Draws photo directly in paintEvent — bypasses all stylesheet cascade issues
    that occur inside WA_TranslucentBackground top-level windows."""

    PW, PH = 130, 158

    def __init__(self, photo_path: str):
        super().__init__()
        self.setFixedSize(self.PW, self.PH)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._pix = None
        if photo_path:
            raw = QPixmap(photo_path)
            if not raw.isNull():
                scaled = raw.scaled(
                    self.PW, self.PH,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                ox = (scaled.width()  - self.PW) // 2
                oy = (scaled.height() - self.PH) // 2
                self._pix = scaled.copy(ox, oy, self.PW, self.PH)

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#1a1a1a'))
        if self._pix:
            p.drawPixmap(0, 0, self._pix)


class _ActorCard(QWidget):
    clicked = pyqtSignal(dict)

    def __init__(self, actor: dict):
        super().__init__()
        self._actor = actor
        self._hovered = False
        self.setFixedWidth(148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(3)

        photos = db.get_actor_photos(actor['id'])
        path = photos[0]['photo_path'] if photos else ''
        v.addWidget(_PhotoWidget(path))

        lbl = QLabel(actor.get('name', ''))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("QLabel { color: #bbb; font-size: 10px; background: transparent; }")
        v.addWidget(lbl)

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(p.RenderHint.Antialiasing)
        color = QColor(36, 30, 10, 230) if self._hovered else QColor(22, 22, 22, 210)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 4, 4)

    def enterEvent(self, _e):
        self._hovered = True;  self.update()

    def leaveEvent(self, _e):
        self._hovered = False; self.update()

    def mousePressEvent(self, _event):
        self.clicked.emit(self._actor)


class _SearchPage(QWidget):
    actor_clicked = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; }"
            "QScrollBar::handle:vertical { background: rgba(60,60,60,180); border-radius: 3px; }"
        )
        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._scroll.setWidget(self._inner)
        v.addWidget(self._scroll)

    def update_results(self, actors: list):
        # setWidget() deletes the previous widget automatically — don't call deleteLater
        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._scroll.setWidget(self._inner)

        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout(self._inner)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(6)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)

        for i, actor in enumerate(actors[:24]):
            card = _ActorCard(actor)
            card.clicked.connect(self.actor_clicked)
            grid.addWidget(card, i // 2, i % 2)


# ─────────────────────────────────────────────
#  Right-panel overlay (floats over player)
# ─────────────────────────────────────────────

class _PanelOverlay(QWidget):
    """Frameless top-level window — WA_TranslucentBackground works only for
    top-level windows on Windows; child-widget transparency can never show
    through an mpv-rendered surface."""

    def __init__(self, main_win, video_container):
        super().__init__(
            main_win,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(320)
        self._vc = video_container

        self.setStyleSheet("""
            QWidget          { background: transparent; color: #e0e0e0;
                               font-family: 'Consolas', monospace; font-size: 12px; }
            QTabWidget::pane { background: transparent; border: none; }
            QTabBar::tab     { background: rgba(18,18,18,210); color: #666;
                               padding: 6px 16px; border: 1px solid #2a2a2a;
                               border-bottom: none; border-radius: 4px 4px 0 0; }
            QTabBar::tab:selected { background: rgba(10,10,10,220); color: #e8b86d; }
            QListWidget      { background: rgba(12,12,12,210); border: 1px solid #222;
                               border-radius: 4px; }
            QListWidget::item          { padding: 6px 8px; border-bottom: 1px solid #1a1a1a; }
            QListWidget::item:hover    { background: rgba(26,26,26,240); }
            QListWidget::item:selected { background: rgba(42,34,0,240); color: #e8b86d; }
            QPushButton      { background: rgba(30,30,30,210); border: 1px solid #333;
                               border-radius: 4px; padding: 5px 12px; color: #e0e0e0; }
            QPushButton:hover    { background: rgba(42,42,42,240); border-color: #e8b86d; }
            QPushButton:pressed  { background: #e8b86d; color: #000; }
            QPushButton#accent   { background: rgba(232,184,109,230); color: #000;
                                   font-weight: bold; border: none; }
            QPushButton#accent:hover { background: rgba(240,202,138,240); }
            QPushButton#danger   { border-color: #c0392b; color: #c0392b; }
            QPushButton#danger:hover { background: #c0392b; color: #fff; }
            QLineEdit, QComboBox, QSpinBox {
                background: rgba(26,26,26,210); border: 1px solid #333;
                border-radius: 4px; padding: 4px 8px; color: #e0e0e0; }
            QComboBox::drop-down { border: none; }
            QProgressBar         { background: rgba(26,26,26,210); border: 1px solid #333;
                                   border-radius: 4px; text-align: center; }
            QProgressBar::chunk  { background: #e8b86d; border-radius: 3px; }
            QLabel#section       { color: #888; font-size: 10px; letter-spacing: 3px; }
            QFrame#separator     { background: #333; max-height: 1px; }
            QScrollBar:vertical  { background: transparent; width: 8px; }
            QScrollBar::handle:vertical { background: rgba(42,42,42,200); border-radius: 4px; }
        """)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        v.addWidget(self._stack)

        self.tab_widget   = QWidget()   # no tab bar — markers shown directly
        self._search_page = _SearchPage()
        self._stack.addWidget(self.tab_widget)    # index 0
        self._stack.addWidget(self._search_page)  # index 1

        main_win.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move,
                            QEvent.Type.Show, QEvent.Type.WindowStateChange):
            self._reposition()
        return False

    def _reposition(self):
        vc = self._vc
        if not vc.isVisible():
            return
        tl = vc.mapToGlobal(vc.rect().topLeft())
        self.setGeometry(
            tl.x() + vc.width() - self.width(),
            tl.y(),
            self.width(),
            vc.height(),
        )

    def show_search(self, active: bool):
        self._stack.setCurrentIndex(1 if active else 0)


# ─────────────────────────────────────────────
#  Main Window
# ─────────────────────────────────────────────

class CineMarker(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CineMarker")
        self.resize(1400, 900)

        self._video_path = None
        self._duration = 0
        self._markers = []
        self._updating_slider = False
        self._convert_worker = None
        self._thumb_worker = None

        self._zoom_level = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_active = False
        self._drag_last = None

        # Multi-tap seek state
        self._seek_count = 0
        self._seek_dir   = 0
        self._seek_timer = QTimer()
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(380)
        self._seek_timer.timeout.connect(self._commit_seek)

        self._setup_style()
        self._setup_mpv()
        self._build_ui()
        self._setup_shortcuts()
        self._setup_timer()

    # ── Style ──────────────────────────────────

    def _setup_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0e0e0e;
                color: #e0e0e0;
                font-family: 'SF Mono', 'Menlo', 'Consolas', monospace;
                font-size: 12px;
            }
            QSplitter::handle { background: #222; width: 2px; height: 2px; }
            QPushButton {
                background: #1e1e1e;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 5px 12px;
                color: #e0e0e0;
            }
            QPushButton:hover { background: #2a2a2a; border-color: #e8b86d; }
            QPushButton:pressed { background: #e8b86d; color: #000; }
            QPushButton#accent {
                background: #e8b86d;
                color: #000;
                border: none;
                font-weight: bold;
            }
            QPushButton#accent:hover { background: #f0ca8a; }
            QPushButton#danger { border-color: #c0392b; color: #c0392b; }
            QPushButton#danger:hover { background: #c0392b; color: #fff; }
            QSlider::groove:horizontal {
                height: 4px;
                background: #2a2a2a;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #e8b86d;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #e8b86d;
                width: 14px; height: 14px;
                border-radius: 7px;
                margin: -5px 0;
            }
            QSlider::groove:vertical {
                width: 4px;
                background: #2a2a2a;
                border-radius: 2px;
            }
            QSlider::sub-page:vertical {
                background: #e8b86d;
                border-radius: 2px;
            }
            QSlider::handle:vertical {
                background: #e8b86d;
                width: 14px; height: 14px;
                border-radius: 7px;
                margin: 0 -5px;
            }
            QListWidget {
                background: #111;
                border: 1px solid #222;
                border-radius: 4px;
            }
            QListWidget::item { padding: 6px 8px; border-bottom: 1px solid #1a1a1a; }
            QListWidget::item:hover { background: #1a1a1a; }
            QListWidget::item:selected { background: #2a2200; color: #e8b86d; }
            QLineEdit, QComboBox, QSpinBox {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 4px 8px;
                color: #e0e0e0;
            }
            QLineEdit:focus, QComboBox:focus { border-color: #e8b86d; }
            QComboBox::drop-down { border: none; }
            QComboBox::down-arrow { image: none; border: none; }
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: #1a1a1a;
                padding: 6px 18px;
                border: 1px solid #222;
                border-bottom: none;
                border-radius: 4px 4px 0 0;
            }
            QTabBar::tab:selected { background: #0e0e0e; border-bottom: 1px solid #0e0e0e; color: #e8b86d; }
            QProgressBar {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk { background: #e8b86d; border-radius: 3px; }
            QLabel#timecode {
                font-size: 18px;
                font-weight: bold;
                color: #e8b86d;
                letter-spacing: 2px;
            }
            QLabel#section { color: #888; font-size: 10px; letter-spacing: 3px; text-transform: uppercase; }
            QFrame#separator { background: #222; max-height: 1px; }
            QStatusBar { background: #0a0a0a; color: #555; border-top: 1px solid #1a1a1a; }
        """)

    # ── mpv setup ──────────────────────────────

    def _setup_mpv(self):
        self.player = mpv.MPV(
            log_handler=self._mpv_log,
            loglevel='error',
        )
        self.player['keep-open'] = True
        self.player['hr-seek'] = True  # frame-accurate seeking

    def _mpv_log(self, level, component, message):
        pass  # silence mpv logs

    # ── UI Building ───────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main tabs — no separate title bar
        self.main_tabs = QTabWidget()
        self.main_tabs.setStyleSheet("""
            QTabBar::tab { padding: 8px 20px; font-size: 12px; letter-spacing: 2px; }
            QTabBar::tab:selected { color: #e8b86d; }
        """)

        # Corner widget: Open Video + Fullscreen
        _corner = QWidget()
        _corner.setStyleSheet("background: transparent;")
        _ch = QHBoxLayout(_corner)
        _ch.setContentsMargins(0, 3, 8, 3)
        _ch.setSpacing(6)
        btn_open_corner = QPushButton("⊕  Open Video")
        btn_open_corner.setObjectName("accent")
        btn_open_corner.setFixedHeight(28)
        btn_open_corner.clicked.connect(self.open_file)
        _ch.addWidget(btn_open_corner)
        self.btn_fs = QPushButton("⛶")
        self.btn_fs.setFixedSize(28, 28)
        self.btn_fs.setToolTip("Volledig scherm  F11")
        self.btn_fs.clicked.connect(self._toggle_fullscreen)
        _ch.addWidget(self.btn_fs)
        self.main_tabs.setCornerWidget(_corner, Qt.Corner.TopRightCorner)
        self._corner_layout = _ch

        # Player search toolbar (hidden until SPELER tab active)
        self._player_tb = QWidget()
        self._player_tb.setStyleSheet("background: transparent;")
        _ph = QHBoxLayout(self._player_tb)
        _ph.setContentsMargins(0, 2, 0, 2)
        _ph.setSpacing(8)

        self._lbl_time = QLabel("--:-- / --:--")
        self._lbl_time.setStyleSheet(
            "color: #555; font-size: 11px; font-family: 'Consolas', monospace;"
        )
        self._lbl_time.setFixedWidth(130)
        _ph.addWidget(self._lbl_time)

        btn_next = QPushButton("⏭")
        btn_next.setFixedSize(26, 26)
        btn_next.setToolTip("Volgende film in de lijst")
        btn_next.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #444; font-size: 14px; }"
            "QPushButton:hover { color: #e8b86d; }"
        )
        btn_next.clicked.connect(self._next_film)
        _ph.addWidget(btn_next)

        self._player_search = QLineEdit()
        self._player_search.setPlaceholderText("Acteur zoeken…")
        self._player_search.setFixedWidth(160)
        self._player_search.setFixedHeight(26)
        self._player_search.textChanged.connect(self._on_player_search)
        _ph.addWidget(self._player_search)

        self._player_tb.setVisible(False)
        self._corner_layout.insertWidget(0, self._player_tb)

        # Player tab — video fills all, ultra-thin seekbar at bottom
        player_widget = QWidget()
        self._player_widget = player_widget
        pv = QVBoxLayout(player_widget)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        self._build_video_area(pv)

        self.timeline = TimelineSlider()
        self.timeline.seeked.connect(self._on_timeline_seek)
        self.timeline.setFixedHeight(4)
        self.timeline.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #141414; border-radius: 0; }"
            "QSlider::sub-page:horizontal { background: #e8b86d; border-radius: 0; }"
            "QSlider::handle:horizontal { background: transparent; width: 0; margin: 0; }"
        )
        pv.addWidget(self.timeline)

        # Floating right panel — top-level transparent window
        self._panel = _PanelOverlay(self, self.video_container)
        self.tabs = self._panel.tab_widget
        self._build_markers_tab()
        self._panel._search_page.actor_clicked.connect(self._link_actor_to_film)
        self._panel.hide()

        # Floating actors overlay — bottom-left of video
        self._actors_overlay = _FilmActorsOverlay(self, self.video_container)
        self._actors_overlay.marker_requested.connect(self._quick_marker)
        self._actors_overlay.thumbnail_requested.connect(self._capture_thumbnail)
        self._actors_overlay.hide()

        # Floating actor-link overlay (child of player_widget)
        self._actor_overlay = _ActorLinkOverlay(player_widget)
        self._actor_overlay.link_requested.connect(self._link_actor_to_film)

        self.main_tabs.addTab(player_widget, "▶  SPELER")

        # Films tab
        self.films_panel = FilmsPanel()
        self.films_panel.play_requested.connect(self._load_video_and_switch)
        self.main_tabs.addTab(self.films_panel, "🎬  FILMS")

        # Acteurs tab
        self.actors_panel = ActorsPanel(self.player)
        self.actors_panel.open_film_requested.connect(self._load_video_and_switch)
        self.actors_panel.scene_jump_requested.connect(self._on_scene_jump)
        self.main_tabs.addTab(self.actors_panel, "◉  ACTEURS")

        # Insert actors toolbar into corner (hidden until acteurs tab active)
        self._actors_tb = self.actors_panel.tab_toolbar
        self._actors_tb.setVisible(False)
        self._corner_layout.insertWidget(0, self._actors_tb)
        self.main_tabs.currentChanged.connect(self._on_tab_changed)

        # Database tab
        self.db_panel = DatabasePanel()
        self.main_tabs.addTab(self.db_panel, "⊞  DATABASE")

        # Sorter tab
        self.sorter_panel = SorterPanel()
        self.main_tabs.addTab(self.sorter_panel, "⊕  SORTEREN")

        self._build_converter_tab()
        self.main_tabs.addTab(self._converter_widget, "⟳  CONVERTER")

        self.main_tabs.setCurrentIndex(1)  # default: FILMS

        root.addWidget(self.main_tabs)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Open een videobestand om te beginnen  •  CineMarker")

    def _on_tab_changed(self, idx):
        actors_idx = self.main_tabs.indexOf(self.actors_panel)
        self._actors_tb.setVisible(idx == actors_idx)
        player_idx = self.main_tabs.indexOf(self._player_widget)
        on_player = (idx == player_idx)
        self._player_tb.setVisible(on_player)
        self._panel.setVisible(on_player)
        if on_player and self._video_path:
            self._actors_overlay.show()
            self._actors_overlay.raise_()
        else:
            self._actors_overlay.hide()
        if not on_player:
            self._player_search.clear()

    def _on_player_search(self, text: str):
        q = text.strip().lower()
        if not q:
            self._panel.show_search(False)
            return
        actors = [a for a in db.get_all_actors()
                  if q in a.get('name', '').lower()]
        self._panel._search_page.update_results(actors)
        self._panel.show_search(True)
        if not self._panel.isVisible():
            self._panel.show()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _show_actor_overlay(self):
        if not self._video_path:
            return
        self._actor_overlay.show_overlay()

    def _link_actor_to_film(self, actor: dict):
        if not self._video_path:
            return
        film = db.get_or_create_film(self._video_path)
        db.link_actor_film(actor['id'], film['id'])
        self._actors_overlay.refresh(film['id'])
        self._player_search.clear()   # reset search → full video visible again
        self.status.showMessage(
            f"  {actor['name']} gekoppeld aan {Path(self._video_path).name}"
        )

    def _build_video_area(self, layout):
        self.video_container = QWidget()
        self.video_container.setStyleSheet("background: #000;")
        self.video_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_container.setMouseTracking(True)
        self.video_container.installEventFilter(self)
        layout.addWidget(self.video_container, stretch=1)

        # Attach mpv to widget after show
        self._mpv_attached = False

    def _attach_mpv(self):
        if not self._mpv_attached:
            wid = int(self.video_container.winId())
            self.player['wid'] = wid
            self._mpv_attached = True

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self._attach_mpv)

    def _build_markers_tab(self):
        v = QVBoxLayout(self.tabs)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        self.marker_list = QListWidget()
        self.marker_list.itemDoubleClicked.connect(self._on_marker_jump)
        v.addWidget(self.marker_list)

    def _build_converter_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        lbl = QLabel("CONVERTER")
        lbl.setObjectName("section")
        v.addWidget(lbl)

        # Input file
        row_in = QHBoxLayout()
        self.conv_input = QLineEdit()
        self.conv_input.setPlaceholderText("Invoerbestand...")
        row_in.addWidget(self.conv_input)
        btn_in = QPushButton("...")
        btn_in.setFixedWidth(32)
        btn_in.clicked.connect(self._conv_pick_input)
        row_in.addWidget(btn_in)
        v.addLayout(row_in)

        btn_use_current = QPushButton("← Gebruik huidig videobestand")
        btn_use_current.clicked.connect(self._conv_use_current)
        v.addWidget(btn_use_current)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(sep)

        # Output format
        lbl2 = QLabel("UITVOERFORMAAT")
        lbl2.setObjectName("section")
        v.addWidget(lbl2)

        self.conv_format = QComboBox()
        self.conv_format.addItems(["mp4", "mov", "avi", "mkv"])
        v.addWidget(self.conv_format)

        lbl3 = QLabel("VIDEO CODEC")
        lbl3.setObjectName("section")
        v.addWidget(lbl3)

        self.conv_codec = QComboBox()
        self.conv_codec.addItems(["libx264 (H.264)", "libx265 (H.265/HEVC)", "libvpx-vp9 (VP9)", "copy (geen hercodering)"])
        v.addWidget(self.conv_codec)

        lbl4 = QLabel("RESOLUTIE")
        lbl4.setObjectName("section")
        v.addWidget(lbl4)

        self.conv_res = QComboBox()
        self.conv_res.addItems(["Original", "3840x2160 (4K)", "1920x1080 (1080p)", "1280x720 (720p)", "854x480 (480p)"])
        v.addWidget(self.conv_res)

        lbl5 = QLabel("KWALITEIT (CRF: lager = beter)")
        lbl5.setObjectName("section")
        v.addWidget(lbl5)

        self.conv_crf = QSpinBox()
        self.conv_crf.setRange(0, 51)
        self.conv_crf.setValue(18)
        v.addWidget(self.conv_crf)

        sep2 = QFrame()
        sep2.setObjectName("separator")
        sep2.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(sep2)

        # Output file
        row_out = QHBoxLayout()
        self.conv_output = QLineEdit()
        self.conv_output.setPlaceholderText("Uitvoerbestand...")
        row_out.addWidget(self.conv_output)
        btn_out = QPushButton("...")
        btn_out.setFixedWidth(32)
        btn_out.clicked.connect(self._conv_pick_output)
        row_out.addWidget(btn_out)
        v.addLayout(row_out)

        self.conv_progress = QProgressBar()
        self.conv_progress.setVisible(False)
        v.addWidget(self.conv_progress)

        self.conv_status = QLabel("")
        self.conv_status.setWordWrap(True)
        self.conv_status.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.conv_status)

        v.addStretch()

        self.btn_convert = QPushButton("⟳  START CONVERSIE")
        self.btn_convert.setObjectName("accent")
        self.btn_convert.setFixedHeight(36)
        self.btn_convert.clicked.connect(self.start_conversion)
        v.addWidget(self.btn_convert)

        self._converter_widget = w

    # ── Shortcuts ─────────────────────────────

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Space"), self).activated.connect(self._shortcut_space)
        QShortcut(QKeySequence("Left"),  self).activated.connect(self._shortcut_left)
        QShortcut(QKeySequence("Right"), self).activated.connect(self._shortcut_right)
        QShortcut(QKeySequence("L"),     self).activated.connect(self._shortcut_l)
        QShortcut(QKeySequence("M"),     self).activated.connect(self._shortcut_m)
        QShortcut(QKeySequence("N"),     self).activated.connect(self._shortcut_n)
        QShortcut(QKeySequence(Qt.Key.Key_Plus),  self).activated.connect(self._shortcut_plus)
        QShortcut(QKeySequence(Qt.Key.Key_Minus), self).activated.connect(self._shortcut_minus)
        QShortcut(QKeySequence(Qt.Key.Key_0), self).activated.connect(self._reset_zoom)
        QShortcut(QKeySequence("T"), self).activated.connect(self.export_thumbnail)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self.open_file)
        QShortcut(QKeySequence("F11"),    self).activated.connect(self._toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self._show_actor_overlay)
        QShortcut(QKeySequence("Home"), self).activated.connect(self.go_to_start)
        QShortcut(QKeySequence("End"), self).activated.connect(self.go_to_end)

    # ── Timer ─────────────────────────────────

    def _setup_timer(self):
        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._update_ui)
        self.timer.start()

    def _update_ui(self):
        if not self._video_path:
            return
        try:
            pos = self.player.time_pos
            dur = self.player.duration
            if pos is not None and dur and dur > 0:
                self._updating_slider = True
                self.timeline.setValue(int(pos / dur * 10000))
                self._updating_slider = False
                self._lbl_time.setText(f"{_fmt_hms(pos)} / {_fmt_hms(dur)}")
            if dur is not None and self._duration != dur:
                self._duration = dur
        except Exception:
            pass

    # ── Playback ──────────────────────────────

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", "",
            "Video bestanden (*.mp4 *.avi *.mov *.wmv *.mkv *.flv *.webm *.m4v *.mpg *.mpeg *.ts *.mts);;Alle bestanden (*)"
        )
        if path:
            self._load_video(path)

    def _load_video(self, path):
        self._reset_zoom()
        self._video_path = path
        self._duration = 0
        self._lbl_time.setText("--:-- / --:--")
        self._markers = load_markers(path)
        self._refresh_marker_list()
        self.player.play(path)
        film = db.get_or_create_film(path)
        self._actors_overlay.refresh(film['id'])
        self.status.showMessage(f"  {Path(path).name}  •  {path}")
        self.setWindowTitle(f"CineMarker  —  {Path(path).name}")
        self._suggest_actors_from_filename(path)

    def _suggest_actors_from_filename(self, path: str):
        stem = Path(path).stem
        normalized = re.sub(r'([a-z])([A-Z])', r'\1 \2', stem)
        normalized = re.sub(r'[_\-\.\s,()[\]{}]+', ' ', normalized).lower()
        matches = [a for a in db.get_all_actors()
                   if a.get('name', '').lower() in normalized]
        if matches:
            self._panel._search_page.update_results(matches)
            self._panel.show_search(True)
            if not self._panel.isVisible():
                self._panel.show()
        else:
            self._panel.show_search(False)

    def _load_video_and_switch(self, path):
        """Load video and switch to player tab"""
        self._load_video(path)
        self.main_tabs.setCurrentIndex(0)

    def _next_film(self):
        """Load the next film in the films panel list."""
        film_list = self.films_panel.film_list
        n = film_list.count()
        if n == 0:
            return
        current_path = self._video_path or ''
        # Find current index
        current_idx = -1
        for i in range(n):
            item = film_list.item(i)
            if item and not item.isHidden():
                d = item.data(Qt.ItemDataRole.UserRole)
                if d and d.get('path') == current_path:
                    current_idx = i
                    break
        # Find next visible item
        start = current_idx + 1
        for i in range(start, n):
            item = film_list.item(i)
            if item and not item.isHidden():
                d = item.data(Qt.ItemDataRole.UserRole)
                if d:
                    self._load_video(d['path'])
                    return
        # Wrap around
        for i in range(0, start):
            item = film_list.item(i)
            if item and not item.isHidden():
                d = item.data(Qt.ItemDataRole.UserRole)
                if d:
                    self._load_video(d['path'])
                    return

    def _on_scene_jump(self, film_path, start_time):
        """Jump to a scene: load film if needed, seek to start"""
        if self._video_path != film_path:
            self._load_video(film_path)
            # Wait for mpv to load then seek
            QTimer.singleShot(800, lambda: self.player.seek(start_time, 'absolute+exact'))
        else:
            self.player.seek(start_time, 'absolute+exact')
        self.main_tabs.setCurrentIndex(0)

    def toggle_play(self):
        if not self._video_path:
            return
        self.player.pause = not self.player.pause

    def seek_relative(self, seconds):
        if not self._video_path:
            return
        self.player.seek(seconds, 'relative+exact')

    def seek_frames(self, n):
        if not self._video_path:
            return
        if n > 0:
            self.player.frame_step()
        else:
            self.player.frame_back_step()

    def go_to_start(self):
        if self._video_path:
            self.player.seek(0, 'absolute+exact')

    def go_to_end(self):
        if self._video_path and self._duration:
            self.player.seek(self._duration - 0.1, 'absolute+exact')

    def _on_timeline_seek(self, fraction):
        if self._video_path and self._duration and not self._updating_slider:
            self.player.seek(fraction * self._duration, 'absolute+exact')

    # ── Multi-tap seek ────────────────────────

    # playing:  1×=5s  2×=30s  3×=5min  4×=30min
    # paused:   1×=1frame  2×=1s  3×=10s  4×=1min
    _SEEK_PLAY   = [0,    5,   30,  300, 1800]
    _SEEK_PAUSE  = [0,    0,    1,   10,   60]  # 0 = frame step

    def _on_seek_key(self, direction: int):
        if not self._video_path:
            return
        if self._seek_dir != direction and self._seek_count > 0:
            self._seek_timer.stop()
            self._commit_seek()
        self._seek_dir    = direction
        self._seek_count  = min(self._seek_count + 1, 4)
        self._seek_timer.start()

    def _commit_seek(self):
        n, d = self._seek_count, self._seek_dir
        self._seek_count = 0
        self._seek_dir   = 0
        if n == 0 or not self._video_path:
            return
        try:
            paused = self.player.pause
        except Exception:
            paused = False
        if paused:
            if n == 1:
                self.seek_frames(d)
            else:
                self.seek_relative(d * self._SEEK_PAUSE[n])
        else:
            self.seek_relative(d * self._SEEK_PLAY[n])

    # ── Markers ───────────────────────────────

    def _capture_thumbnail(self):
        if not self._video_path:
            return
        film = db.get_or_create_film(self._video_path)
        thumb_dir = Path(os.path.dirname(os.path.abspath(__file__))) / 'thumbnails'
        thumb_dir.mkdir(exist_ok=True)
        path = str(thumb_dir / f"{film['id']}_thumb.jpg")
        try:
            self.player.command('screenshot-to-file', path, 'video')
            db.set_film_thumbnail(film['id'], path)
            self.status.showMessage(f"  Thumbnail opgeslagen voor {Path(self._video_path).name}")
        except Exception as e:
            self.status.showMessage(f"  Thumbnail mislukt: {e}")

    def _quick_marker(self, actors: list, categories: list):
        """Create marker — actor + category both required."""
        if not self._video_path:
            return

        # ── Validate actor ───────────────────────
        if not actors:
            self.status.showMessage("  Selecteer eerst een acteur")
            return

        # ── Validate / pick category ─────────────
        if not categories:
            cats = db.get_all_categories()
            if not cats:
                self.status.showMessage("  Maak eerst een categorie aan in het database-tabblad")
                return
            chosen = self._pick_category_dialog(cats)
            if chosen is None:
                return          # user cancelled
            categories = [chosen]
            self._actors_overlay._cat_sel.add(chosen['id'])

        try:
            pos = self.player.time_pos or 0
        except Exception:
            pos = 0

        cat_names   = [c['name'] for c in categories]
        actor_names = [a['name'] for a in actors]
        name = ', '.join(cat_names) + ' — ' + ', '.join(actor_names)

        marker = {
            'time':       pos,
            'name':       name,
            'actors':     [a['id'] for a in actors],
            'categories': [c['id'] for c in categories],
            'created':    datetime.now().isoformat(),
        }
        self._markers.append(marker)
        self._markers.sort(key=lambda m: m['time'])
        save_markers(self._video_path, self._markers)
        self._refresh_marker_list()
        self._actors_overlay._cat_sel.clear()
        self._actors_overlay.update()
        self.status.showMessage(f"  Marker '{name}' op {format_time(pos)}")

    def _pick_category_dialog(self, cats: list) -> dict | None:
        """Small popup to pick one category. Returns the chosen dict or None."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Kies een categorie")
        dlg.setModal(True)
        dlg.setStyleSheet("""
            QDialog { background: #111; }
            QPushButton {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 6px;
                color: #ccc;
                font-size: 13px;
                padding: 8px 18px;
                min-width: 120px;
            }
            QPushButton:hover  { background: #252525; border-color: #e8b86d; color: #e8b86d; }
            QPushButton:pressed { background: #e8b86d; color: #000; }
            QLabel { color: #666; font-size: 10px; letter-spacing: 3px; }
        """)

        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        lbl = QLabel("CATEGORIE")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(lbl)

        chosen = [None]

        for cat in cats:
            icon_path = cat.get('icon_path', '')
            label = cat['name']
            btn = QPushButton(label)
            if icon_path:
                from PyQt6.QtGui import QIcon
                ico = QIcon(icon_path)
                if not ico.isNull():
                    btn.setIcon(ico)
            btn.clicked.connect(lambda _, c=cat: (chosen.__setitem__(0, c), dlg.accept()))
            v.addWidget(btn)

        cancel = QPushButton("Annuleren")
        cancel.setStyleSheet(
            "QPushButton { color: #444; border-color: #222; }"
            "QPushButton:hover { color: #888; border-color: #444; }"
        )
        cancel.clicked.connect(dlg.reject)
        v.addWidget(cancel)

        dlg.exec()
        return chosen[0]

    def _shortcut_space(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._move_p()
        else:
            self.toggle_play()

    def _shortcut_left(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._prev()
        else:
            self.seek_relative(-5)

    def _shortcut_right(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._next()
        else:
            self.seek_relative(5)

    def _shortcut_l(self):
        if self.main_tabs.currentWidget() is not self.sorter_panel:
            self._on_seek_key(1)

    def _shortcut_m(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._move_m()
        else:
            self._on_seek_key(1)

    def _shortcut_n(self):
        if self.main_tabs.currentWidget() is not self.sorter_panel:
            self._on_seek_key(-1)

    def _shortcut_plus(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._move_p()
        else:
            self._zoom_in_video()

    def _shortcut_minus(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._move_m()
        else:
            self._zoom_out_video()

    # ── Video zoom / pan ──────────────────────────

    def _zoom_in_video(self):
        self._zoom_level += 0.25
        self._apply_zoom_pan()
        self.video_container.setCursor(Qt.CursorShape.OpenHandCursor)

    def _zoom_out_video(self):
        self._zoom_level = max(0.0, self._zoom_level - 0.25)
        if self._zoom_level == 0.0:
            self._pan_x = 0.0
            self._pan_y = 0.0
            self.video_container.setCursor(Qt.CursorShape.ArrowCursor)
        self._apply_zoom_pan()

    def _reset_zoom(self):
        self._zoom_level = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_active = False
        self._apply_zoom_pan()
        self.video_container.setCursor(Qt.CursorShape.ArrowCursor)

    def _apply_zoom_pan(self):
        try:
            self.player['video-zoom']  = self._zoom_level
            self.player['video-pan-x'] = self._pan_x
            self.player['video-pan-y'] = self._pan_y
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj is self.video_container:
            t = event.type()
            if t == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton and self._zoom_level > 0:
                    self._drag_active = True
                    self._drag_last = event.pos()
                    self.video_container.setCursor(Qt.CursorShape.ClosedHandCursor)
                    return True
            elif t == QEvent.Type.MouseMove:
                if self._drag_active:
                    delta = event.pos() - self._drag_last
                    self._drag_last = event.pos()
                    w = self.video_container.width()
                    h = self.video_container.height()
                    self._pan_x -= delta.x() / w
                    self._pan_y -= delta.y() / h
                    self._apply_zoom_pan()
                    return True
            elif t == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton and self._drag_active:
                    self._drag_active = False
                    self.video_container.setCursor(Qt.CursorShape.OpenHandCursor)
                    return True
            elif t == QEvent.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton and self._zoom_level > 0:
                    self._reset_zoom()
                    return True
        return super().eventFilter(obj, event)

    def add_marker(self):
        if not self._video_path:
            return
        try:
            pos = self.player.time_pos or 0
        except Exception:
            pos = 0

        name, ok = QInputDialog.getText(
            self, "Marker", "Naam voor marker:",
            text=f"Marker {len(self._markers) + 1}"
        )
        if ok:
            marker = {
                'time': pos,
                'name': name,
                'created': datetime.now().isoformat()
            }
            self._markers.append(marker)
            self._markers.sort(key=lambda m: m['time'])
            save_markers(self._video_path, self._markers)
            self._refresh_marker_list()
            self.status.showMessage(f"  Marker '{name}' geplaatst op {format_time(pos)}")

    def _refresh_marker_list(self):
        self.marker_list.clear()
        for idx, m in enumerate(self._markers):
            item = QListWidgetItem()
            self.marker_list.addItem(item)

            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rh = QHBoxLayout(row_w)
            rh.setContentsMargins(6, 0, 4, 0)
            rh.setSpacing(6)

            lbl = QLabel(f"{format_time(m['time'])}   {m['name']}")
            lbl.setStyleSheet("color: #ccc; font-size: 12px; background: transparent;")
            rh.addWidget(lbl, stretch=1)

            btn_del = QPushButton("✕")
            btn_del.setFixedSize(20, 20)
            btn_del.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    color: #444;
                    font-size: 11px;
                }
                QPushButton:hover { color: #e05555; }
            """)
            btn_del.clicked.connect(lambda _, i=idx: self._delete_marker_by_index(i))
            rh.addWidget(btn_del)

            item.setSizeHint(row_w.sizeHint())
            self.marker_list.setItemWidget(item, row_w)

    def _delete_marker_by_index(self, idx: int):
        if 0 <= idx < len(self._markers):
            self._markers.pop(idx)
            save_markers(self._video_path, self._markers)
            self._refresh_marker_list()

    def _on_marker_jump(self, item=None):
        row = self.marker_list.currentRow()
        if 0 <= row < len(self._markers):
            t = self._markers[row]['time']
            self.player.seek(t, 'absolute+exact')

    def _on_marker_jump_btn(self):
        self._on_marker_jump()

    def _on_marker_rename(self):
        row = self.marker_list.currentRow()
        if 0 <= row < len(self._markers):
            old = self._markers[row]['name']
            name, ok = QInputDialog.getText(self, "Hernoem marker", "Nieuwe naam:", text=old)
            if ok and name:
                self._markers[row]['name'] = name
                save_markers(self._video_path, self._markers)
                self._refresh_marker_list()

    def _on_marker_delete(self):
        row = self.marker_list.currentRow()
        if 0 <= row < len(self._markers):
            m = self._markers[row]
            reply = QMessageBox.question(self, "Verwijder marker",
                f"Marker '{m['name']}' verwijderen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self._markers.pop(row)
                save_markers(self._video_path, self._markers)
                self._refresh_marker_list()

    def _export_markers_csv(self):
        if not self._video_path or not self._markers:
            QMessageBox.information(self, "Export", "Geen markers om te exporteren.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporteer CSV", "", "CSV (*.csv)")
        if path:
            with open(path, 'w') as f:
                f.write("Tijdcode,Seconden,Naam,Aangemaakt\n")
                for m in self._markers:
                    f.write(f"{format_time(m['time'])},{m['time']:.3f},{m['name']},{m.get('created','')}\n")
            self.status.showMessage(f"  Markers geëxporteerd naar {path}")

    # ── Thumbnail ─────────────────────────────

    def export_thumbnail(self):
        if not self._video_path:
            return
        try:
            pos = self.player.time_pos or 0
        except Exception:
            pos = 0

        default = str(Path(self._video_path).parent / f"thumb_{format_time(pos).replace(':', '-')}.jpg")
        path, _ = QFileDialog.getSaveFileName(self, "Sla thumbnail op", default, "JPEG (*.jpg);;PNG (*.png)")
        if not path:
            return

        self.status.showMessage(f"  Thumbnail exporteren op {format_time(pos)}…")
        self._thumb_worker = ThumbnailWorker(self._video_path, pos, path)
        self._thumb_worker.finished.connect(lambda p: self.status.showMessage(f"  Thumbnail opgeslagen: {p}"))
        self._thumb_worker.error.connect(lambda e: self.status.showMessage(f"  Fout: {e}"))
        self._thumb_worker.start()

    # ── Converter ─────────────────────────────

    def _conv_pick_input(self):
        path, _ = QFileDialog.getOpenFileName(self, "Invoerbestand", "",
            "Video bestanden (*.mp4 *.avi *.mov *.wmv *.mkv *.flv *.webm *.m4v);;Alle bestanden (*)")
        if path:
            self.conv_input.setText(path)
            self._conv_suggest_output()

    def _conv_use_current(self):
        if self._video_path:
            self.conv_input.setText(self._video_path)
            self._conv_suggest_output()

    def _conv_suggest_output(self):
        inp = self.conv_input.text()
        if inp:
            fmt = self.conv_format.currentText()
            p = Path(inp)
            self.conv_output.setText(str(p.parent / f"{p.stem}_converted.{fmt}"))

    def _conv_pick_output(self):
        fmt = self.conv_format.currentText()
        path, _ = QFileDialog.getSaveFileName(self, "Uitvoerbestand", "",
            f"{fmt.upper()} (*.{fmt});;Alle bestanden (*)")
        if path:
            self.conv_output.setText(path)

    def start_conversion(self):
        inp = self.conv_input.text().strip()
        out = self.conv_output.text().strip()

        if not inp or not os.path.exists(inp):
            QMessageBox.warning(self, "Fout", "Selecteer een geldig invoerbestand.")
            return
        if not out:
            QMessageBox.warning(self, "Fout", "Geef een uitvoerbestand op.")
            return

        codec_map = {
            "libx264 (H.264)": "libx264",
            "libx265 (H.265/HEVC)": "libx265",
            "libvpx-vp9 (VP9)": "libvpx-vp9",
            "copy (geen hercodering)": "copy"
        }
        codec = codec_map.get(self.conv_codec.currentText(), "libx264")
        res_map = {
            "Original": "Original",
            "3840x2160 (4K)": "3840x2160",
            "1920x1080 (1080p)": "1920x1080",
            "1280x720 (720p)": "1280x720",
            "854x480 (480p)": "854x480"
        }
        resolution = res_map.get(self.conv_res.currentText(), "Original")
        crf = self.conv_crf.value()

        self.btn_convert.setEnabled(False)
        self.conv_progress.setVisible(True)
        self.conv_progress.setValue(0)
        self.conv_status.setText("Bezig met converteren…")

        self._convert_worker = ConvertWorker(inp, out, codec, resolution, crf)
        self._convert_worker.progress.connect(self.conv_progress.setValue)
        self._convert_worker.finished.connect(self._on_convert_done)
        self._convert_worker.error.connect(self._on_convert_error)
        self._convert_worker.start()

    def _on_convert_done(self, path):
        self.btn_convert.setEnabled(True)
        self.conv_progress.setValue(100)
        self.conv_status.setText(f"✓ Klaar: {path}")
        self.status.showMessage(f"  Conversie voltooid: {path}")

    def _on_convert_error(self, err):
        self.btn_convert.setEnabled(True)
        self.conv_progress.setVisible(False)
        self.conv_status.setText(f"✗ Fout: {err[:200]}")

    # ── Cleanup ───────────────────────────────

    def closeEvent(self, event):
        self.player.terminate()
        event.accept()


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CineMarker")

    # Check dependencies
    missing = []
    try:
        import mpv
    except ImportError:
        missing.append("python-mpv  (pip install python-mpv)")

    import shutil
    for tool in ['mpv', 'ffmpeg']:
        if shutil.which(tool) is None:
            missing.append(f"{tool}  (installeer via package manager)")

    if missing:
        msg = QMessageBox()
        msg.setWindowTitle("Ontbrekende afhankelijkheden")
        msg.setText("De volgende software is vereist:\n\n" + "\n".join(f"• {m}" for m in missing))
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.exec()
        sys.exit(1)

    window = CineMarker()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
