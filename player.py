#!/usr/bin/env python3
"""
CineMarker - Professional Video Player
Requires: pip install python-mpv PyQt6
Requires: mpv and ffmpeg installed on system
"""

import sys
import os
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
    QInputDialog, QSizePolicy, QStatusBar, QScrollArea, QStyle
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, QThread, QSize, QEvent
)
from PyQt6.QtGui import QFont, QIcon, QKeySequence, QShortcut, QColor, QPalette, QPixmap

try:
    import mpv
except OSError:
    app = QApplication(sys.argv)
    QMessageBox.critical(
        None,
        "mpv DLL niet gevonden",
        "Kan mpv-2.dll niet vinden.\n\n"
        "Download de Windows dev-build van mpv:\n"
        "https://sourceforge.net/projects/mpv-player-windows/files/libmpv/\n\n"
        "Pak mpv-2.dll uit en plaats hem in:\n"
        r"  C:\mpv\  of naast player.py",
    )
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


class _SelectableThumb(QWidget):
    toggled = _pyqtSignal(int, bool)   # actor_id, selected

    TW, TH = 52, 62

    def __init__(self, actor: dict):
        super().__init__()
        self._actor = actor
        self._selected = False
        self.setFixedSize(self.TW + 8, self.TH + 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._pix = None
        photos = db.get_actor_photos(actor['id'])
        path = photos[0]['photo_path'] if photos else ''
        if path:
            raw = QPixmap(path)
            if not raw.isNull():
                scaled = raw.scaled(self.TW, self.TH,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                ox = (scaled.width()  - self.TW) // 2
                oy = (scaled.height() - self.TH) // 2
                self._pix = scaled.copy(ox, oy, self.TW, self.TH)

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter, QFont
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # card background
        bg = QColor(60, 50, 10, 255) if self._selected else QColor(18, 18, 18, 255)
        p.setBrush(bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 4, 4)
        # photo
        ox = (self.width() - self.TW) // 2
        if self._pix:
            p.drawPixmap(ox, 2, self._pix)
        else:
            p.fillRect(ox, 2, self.TW, self.TH, QColor('#2a2a2a'))
        # selection border
        if self._selected:
            pen = p.pen()
            from PyQt6.QtGui import QPen
            p.setPen(QPen(QColor('#e8b86d'), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)
            p.setPen(pen)
        # name
        p.setPen(QColor('#aaa') if self._selected else QColor('#666'))
        f = p.font()
        f.setPointSize(7)
        p.setFont(f)
        name_rect = self.rect().adjusted(0, self.TH + 4, 0, 0)
        p.drawText(name_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   self._actor.get('name', ''))

    def mousePressEvent(self, _e):
        self._selected = not self._selected
        self.update()
        self.toggled.emit(self._actor['id'], self._selected)


class _FilmActorsOverlay(QWidget):
    """Floating overlay at bottom-left of video showing linked actors."""
    marker_requested    = _pyqtSignal(list)   # list of selected actor dicts
    thumbnail_requested = _pyqtSignal()

    def __init__(self, main_win, video_container):
        super().__init__(main_win,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._vc = video_container
        self._film_id = None
        self._selected_ids: set = set()
        self._thumb_widgets: dict = {}   # actor_id -> _SelectableThumb

        h = QHBoxLayout(self)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(4)

        self._scroll = QScrollArea()
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget     { background: transparent; }"
        )
        self._inner = QWidget()
        self._row = QHBoxLayout(self._inner)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(4)
        self._row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._scroll.setWidget(self._inner)
        h.addWidget(self._scroll)

        self._btn_marker = QPushButton("◉")
        self._btn_marker.setFixedSize(32, 32)
        self._btn_marker.setToolTip("Marker met geselecteerde acteurs")
        self._btn_marker.setStyleSheet(
            "QPushButton { background: #1a1000; border: 1px solid #6b4a00;"
            "  border-radius: 4px; color: #e8b86d; font-size: 16px; }"
            "QPushButton:hover { background: #2a1a00; border-color: #e8b86d; }"
            "QPushButton:pressed { background: #e8b86d; color: #000; }"
        )
        self._btn_marker.clicked.connect(
            lambda: self.marker_requested.emit(self.selected_actors()))
        h.addWidget(self._btn_marker, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._btn_thumb = QPushButton("⊡")
        self._btn_thumb.setFixedSize(32, 32)
        self._btn_thumb.setToolTip("Sla huidig frame op als filmthumbnail")
        self._btn_thumb.setStyleSheet(
            "QPushButton { background: #001a1a; border: 1px solid #006b6b;"
            "  border-radius: 4px; color: #55dede; font-size: 16px; }"
            "QPushButton:hover { background: #002a2a; border-color: #55dede; }"
            "QPushButton:pressed { background: #55dede; color: #000; }"
        )
        self._btn_thumb.clicked.connect(self.thumbnail_requested)
        h.addWidget(self._btn_thumb, alignment=Qt.AlignmentFlag.AlignVCenter)

        main_win.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            self._reposition()
        return False

    def _reposition(self):
        vc = self._vc
        if not vc.isVisible():
            return
        tl = vc.mapToGlobal(vc.rect().topLeft())
        n = len(self._thumb_widgets)
        thumb_w = _SelectableThumb.TW + 8
        content_w = n * (thumb_w + 4) + 32 + 32 + 16 + 20   # thumbs + 2 btns + margins
        w = min(content_w, vc.width() - 40)
        w = max(60, w)
        h = _SelectableThumb.TH + 18 + 12
        self.setFixedHeight(h)
        self._scroll.setFixedHeight(h - 12)
        self.setGeometry(tl.x() + 8, tl.y() + vc.height() - h - 8, w, h)

    def refresh(self, film_id: int | None):
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._thumb_widgets.clear()
        self._selected_ids.clear()

        if film_id is None:
            self.hide()
            return

        self._film_id = film_id
        actors = db.get_actors_for_film(film_id)
        for actor in actors:
            thumb = _SelectableThumb(actor)
            thumb.toggled.connect(self._on_toggle)
            self._thumb_widgets[actor['id']] = thumb
            self._row.addWidget(thumb)

        self._reposition()
        self.show()
        self.raise_()

    def _on_toggle(self, actor_id: int, selected: bool):
        if selected:
            self._selected_ids.add(actor_id)
        else:
            self._selected_ids.discard(actor_id)

    def selected_actors(self) -> list:
        return [w._actor for aid, w in self._thumb_widgets.items()
                if aid in self._selected_ids]


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

        self.tab_widget   = QTabWidget()
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
            QTabWidget::pane { border: 1px solid #222; border-radius: 4px; }
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
        _ph.setSpacing(4)
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
        self._build_converter_tab()
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
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        lbl = QLabel("MARKERS")
        lbl.setObjectName("section")
        v.addWidget(lbl)

        self.marker_list = QListWidget()
        self.marker_list.itemDoubleClicked.connect(self._on_marker_jump)
        v.addWidget(self.marker_list)

        row = QHBoxLayout()
        btn_jump = QPushButton("↵ Spring")
        btn_jump.clicked.connect(self._on_marker_jump_btn)
        row.addWidget(btn_jump)

        btn_rename = QPushButton("✎ Naam")
        btn_rename.clicked.connect(self._on_marker_rename)
        row.addWidget(btn_rename)

        btn_del = QPushButton("✕ Verwijder")
        btn_del.setObjectName("danger")
        btn_del.clicked.connect(self._on_marker_delete)
        row.addWidget(btn_del)

        v.addLayout(row)

        btn_export = QPushButton("⊡ Exporteer markers als CSV")
        btn_export.clicked.connect(self._export_markers_csv)
        v.addWidget(btn_export)

        self.tabs.addTab(w, "Markers")

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

        self.tabs.addTab(w, "Converter")

    # ── Shortcuts ─────────────────────────────

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Space"), self).activated.connect(self.toggle_play)
        QShortcut(QKeySequence("Left"),  self).activated.connect(lambda: self._on_seek_key(-1))
        QShortcut(QKeySequence("Right"), self).activated.connect(lambda: self._on_seek_key(1))
        QShortcut(QKeySequence("M"), self).activated.connect(self.add_marker)
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
        self._video_path = path
        self._markers = load_markers(path)
        self._refresh_marker_list()
        self.player.play(path)
        film = db.get_or_create_film(path)
        self._actors_overlay.refresh(film['id'])
        self.status.showMessage(f"  {Path(path).name}  •  {path}")
        self.setWindowTitle(f"CineMarker  —  {Path(path).name}")

    def _load_video_and_switch(self, path):
        """Load video and switch to player tab"""
        self._load_video(path)
        self.main_tabs.setCurrentIndex(0)

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

    def _quick_marker(self, actors: list):
        """Create marker immediately with selected actors as name — no dialog."""
        if not self._video_path:
            return
        try:
            pos = self.player.time_pos or 0
        except Exception:
            pos = 0
        if actors:
            name = ', '.join(a['name'] for a in actors)
        else:
            name = f"Marker {len(self._markers) + 1}"
        marker = {
            'time': pos,
            'name': name,
            'actors': [a['id'] for a in actors],
            'created': datetime.now().isoformat()
        }
        self._markers.append(marker)
        self._markers.sort(key=lambda m: m['time'])
        save_markers(self._video_path, self._markers)
        self._refresh_marker_list()
        self.status.showMessage(f"  Marker '{name}' op {format_time(pos)}")

    def add_marker(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            return
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
        for m in self._markers:
            item = QListWidgetItem(f"  {format_time(m['time'])}   {m['name']}")
            self.marker_list.addItem(item)

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

    for tool in ['mpv', 'ffmpeg']:
        if subprocess.run(['where', tool], capture_output=True, shell=True).returncode != 0:
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
