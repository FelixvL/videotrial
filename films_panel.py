#!/usr/bin/env python3
"""
CineMarker — Films browser panel  (grid view, sortable)
"""

import os
import json
import random
import subprocess
import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QLineEdit, QFrame,
    QFileDialog, QStyledItemDelegate, QStyle, QListView,
    QMenu, QMessageBox
)
from PyQt6.QtCore import Qt, QSize, QRect, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap

import database as db
from paths import (ensure_volume_id,
                   SCALED_FILM_THUMBS_DIR, SCALED_ACTOR_GRID_DIR)


VIDEO_EXTS = {
    '.mp4', '.avi', '.mov', '.wmv', '.mkv', '.flv',
    '.webm', '.m4v', '.mpg', '.mpeg', '.ts', '.mts',
    '.divx', '.vob', '.rmvb', '.rm', '.3gp',
}

CELL_W      = 192   # default cell width
CELL_H      = 108   # default cell height  (16:9)
ACT_SZ      = 26    # actor photo overlay size
ZOOM_STEP_W = 32    # px per zoom level
ZOOM_MIN_W  = 64    # minimum cell width

SORT_FIELDS = [
    ('name',    'Naam'),
    ('size',    'Grootte'),
    ('date',    'Datum'),
    ('markers', 'Markers'),
    ('duration','Duur'),
    ('actors',  'Acteurs'),
]


def _ffprobe_duration(path: str) -> float:
    """Return duration in seconds via ffprobe; 0.0 on any failure."""
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error',
             '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1',
             path],
            capture_output=True, text=True, timeout=6,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _parse_duration_input(s: str) -> float:
    """Parse user duration string → seconds.  '' = no filter (0.0).
    Accepted formats:
      '10'       → 10 minutes
      '10:30'    → 10 min 30 sec
      '1:10:30'  → 1 h 10 min 30 sec
    """
    s = s.strip()
    if not s:
        return 0.0
    parts = s.split(':')
    try:
        if len(parts) == 1:
            return float(parts[0]) * 60
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        pass
    return 0.0


def _parse_size_input(s: str) -> float:
    """Parse user size string → bytes.  '' = no filter (0.0).
    Accepted formats:
      '500'      → 500 MB
      '1.5'      → 1.5 GB  (when 'gb' / 'g' suffix present)
      '1.5 GB'   → 1.5 GB
      '500 MB'   → 500 MB
    Plain number without suffix is always treated as MB.
    """
    s = s.strip()
    if not s:
        return 0.0
    sl = s.lower().replace(' ', '')
    try:
        if sl.endswith('gb') or sl.endswith('g'):
            return float(sl.rstrip('gb').rstrip('g')) * 1_073_741_824
        elif sl.endswith('mb') or sl.endswith('m'):
            return float(sl.rstrip('mb').rstrip('m')) * 1_048_576
        else:
            return float(sl) * 1_048_576   # default: MB
    except (ValueError, IndexError):
        return 0.0


# ─────────────────────────────────────────────
#  Disk-cache helpers for scaled thumbnails
# ─────────────────────────────────────────────

def scaled_cache_path(source_path: str, w: int, h: int, cache_dir: Path) -> Path:
    """Build the disk-cache filename for a scaled thumbnail.

    Format: <stem>_<mtime>_<w>x<h>.jpg
    Using the source mtime means the cache is automatically bypassed when the
    source file changes (e.g. a replaced actor photo or updated thumbnail).
    """
    try:
        mtime = int(os.path.getmtime(source_path))
    except OSError:
        mtime = 0
    stem = Path(source_path).stem[:80]   # cap length for Windows path limit
    return cache_dir / f"{stem}_{mtime}_{w}x{h}.jpg"


def load_scaled_cache(source_path: str, w: int, h: int,
                      cache_dir: Path) -> 'QPixmap | None':
    """Return a previously saved scaled QPixmap, or None on cache miss."""
    cp = scaled_cache_path(source_path, w, h, cache_dir)
    if cp.exists():
        pix = QPixmap(str(cp))
        if not pix.isNull():
            return pix
    return None


def save_scaled_cache(source_path: str, w: int, h: int,
                      pixmap: QPixmap, cache_dir: Path) -> None:
    """Persist a scaled QPixmap to disk so future sessions skip the scaling step."""
    if pixmap.isNull():
        return
    cp = scaled_cache_path(source_path, w, h, cache_dir)
    try:
        pixmap.save(str(cp), 'JPEG', quality=85)
    except Exception:
        pass


# ─────────────────────────────────────────────
#  Background duration worker
# ─────────────────────────────────────────────

class _DurationWorker(QThread):
    """Runs ffprobe for a list of films without a cached duration.

    Emits duration_ready(film_id, file_path, duration_seconds) for each
    result so the panel can update the item in-place without a full rescan.
    """
    duration_ready = pyqtSignal(int, str, float)   # film_id, file_path, seconds

    def __init__(self, tasks: list):
        """tasks: list of (film_id, file_path) — film_id may be None."""
        super().__init__()
        self._tasks = tasks
        self._stop  = False

    def stop(self):
        self._stop = True

    def run(self):
        for film_id, file_path in self._tasks:
            if self._stop:
                break
            dur = _ffprobe_duration(file_path)
            if dur > 0:
                self.duration_ready.emit(film_id or -1, file_path, dur)


# ─────────────────────────────────────────────
#  Delegate
# ─────────────────────────────────────────────

class FilmGridDelegate(QStyledItemDelegate):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_cache: dict = {}
        self._actor_cache: dict = {}   # film_id -> [QPixmap, ...]
        self._tick: int = 0

    def set_tick(self, tick: int):
        self._tick = tick

    def invalidate_cache(self):
        self._thumb_cache.clear()
        self._actor_cache.clear()

    def _thumb(self, path: str, w: int, h: int) -> QPixmap | None:
        key = f"{path}:{w}:{h}"
        if key not in self._thumb_cache:
            pix = None
            if path and os.path.exists(path):
                # Try disk cache first — avoids re-scaling on every session start
                pix = load_scaled_cache(path, w, h, SCALED_FILM_THUMBS_DIR)
                if pix is None:
                    raw = QPixmap(path)
                    if not raw.isNull():
                        sc = raw.scaled(w, h,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
                        ox = (sc.width()  - w) // 2
                        oy = (sc.height() - h) // 2
                        pix = sc.copy(ox, oy, w, h)
                        save_scaled_cache(path, w, h, pix, SCALED_FILM_THUMBS_DIR)
            self._thumb_cache[key] = pix
        return self._thumb_cache[key]

    def _actor_pixmaps(self, photo_paths: list) -> list:
        """Laad en schaal acteursfoto's. Input zijn al pre-geladen paden uit item-data."""
        key = tuple(photo_paths)
        if key not in self._actor_cache:
            result = []
            sz = ACT_SZ
            for path in photo_paths[:6]:
                if path and os.path.exists(path):
                    pix = load_scaled_cache(path, sz, sz, SCALED_ACTOR_GRID_DIR)
                    if pix is None:
                        raw = QPixmap(path)
                        if not raw.isNull():
                            sc = raw.scaled(sz, sz,
                                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
                            ox = (sc.width()  - sz) // 2
                            oy = (sc.height() - sz) // 2
                            pix = sc.copy(ox, oy, sz, sz)
                            save_scaled_cache(path, sz, sz, pix, SCALED_ACTOR_GRID_DIR)
                    if pix:
                        result.append(pix)
            self._actor_cache[key] = result
        return self._actor_cache[key]

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            super().paint(painter, option, index)
            return

        r = option.rect
        w, h = r.width(), r.height()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered  = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Thumbnail met 0.2s crossfade tussen switches
        FADE_DUR = 0.2
        thumbs   = data.get('thumbnails', [])

        def _placeholder():
            painter.fillRect(r, QColor('#0d0d0d'))
            _f = QFont(painter.font())
            _f.setPointSize(18)
            painter.setFont(_f)
            painter.setPen(QColor('#252525'))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, '▶')

        if len(thumbs) > 1:
            phase   = data.get('thumb_phase', 0.0)
            t       = time.time() + phase
            period  = t % 2.0                          # positie in 2s cyclus
            idx_cur = int(t / 2.0) % len(thumbs)
            pix_cur = self._thumb(thumbs[idx_cur], w, h)

            if period > (2.0 - FADE_DUR):
                # Overgangsfase: fade in de volgende thumbnail
                fade     = (period - (2.0 - FADE_DUR)) / FADE_DUR   # 0.0 → 1.0
                idx_next = (idx_cur + 1) % len(thumbs)
                pix_next = self._thumb(thumbs[idx_next], w, h)
                if pix_cur:
                    painter.drawPixmap(r.x(), r.y(), pix_cur)
                else:
                    _placeholder()
                if pix_next:
                    painter.setOpacity(fade)
                    painter.drawPixmap(r.x(), r.y(), pix_next)
                    painter.setOpacity(1.0)
            else:
                # Stabiele fase
                if pix_cur:
                    painter.drawPixmap(r.x(), r.y(), pix_cur)
                else:
                    _placeholder()
        elif thumbs:
            pix = self._thumb(thumbs[0], w, h)
            if pix:
                painter.drawPixmap(r.x(), r.y(), pix)
            else:
                _placeholder()
        else:
            pix = self._thumb(data.get('thumbnail', ''), w, h)
            if pix:
                painter.drawPixmap(r.x(), r.y(), pix)
            else:
                _placeholder()

        # Bottom info bar — always visible
        bar_h = 20
        bar_r = QRect(r.x(), r.bottom() - bar_h, w, bar_h)
        painter.fillRect(bar_r, QColor(0, 0, 0, 170))
        bf = QFont(painter.font())
        bf.setPointSize(7)
        painter.setFont(bf)

        duration = data.get('duration', 0) or 0
        markers  = data.get('markers',  0) or 0
        size_b   = data.get('size',     0) or 0

        # File size — right-aligned
        size_str = ''
        if size_b > 0:
            gb = size_b / 1_073_741_824
            mb = size_b / 1_048_576
            size_str = (f"{gb:.1f} GB" if gb >= 1 else f"{mb:.0f} MB")
            painter.setPen(QColor('#888888'))
            painter.drawText(bar_r.adjusted(0, 0, -5, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                size_str)

        # Duration — to the left of file size
        if duration > 0:
            s = int(duration)
            dur_str = (f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"
                       if s >= 3600 else f"{s//60}:{s%60:02d}")
            fm = painter.fontMetrics()
            size_w = (fm.horizontalAdvance(size_str) + 10) if size_b > 0 else 0
            painter.setPen(QColor('#aaaaaa'))
            painter.drawText(bar_r.adjusted(0, 0, -(5 + size_w), 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                dur_str)

        # Marker count (blue) + negative count (red) — left-aligned
        neg_markers = data.get('neg_markers', 0) or 0
        x_off = 5
        if markers > 0:
            txt_m = f'◉{markers}'
            painter.setPen(QColor('#6db8e8'))
            painter.drawText(bar_r.adjusted(x_off, 0, 0, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                txt_m)
            x_off += painter.fontMetrics().horizontalAdvance(txt_m) + 4
        if neg_markers > 0:
            painter.setPen(QColor('#cc3333'))
            painter.drawText(bar_r.adjusted(x_off, 0, 0, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f'⊘{neg_markers}')

        # Actor photos (bottom-left, above the info bar) — paden uit item-data, geen DB
        actor_photos = data.get('actor_photos', [])
        if actor_photos:
            ax = r.x() + 3
            ay = r.bottom() - ACT_SZ - bar_h - 2
            for ap in self._actor_pixmaps(actor_photos):
                painter.drawPixmap(ax, ay, ap)
                ax += ACT_SZ + 2

        # Hover: dim + name (above info bar)
        if hovered and not selected:
            painter.fillRect(r, QColor(0, 0, 0, 80))
            name_r = QRect(r.x(), r.bottom() - bar_h - 22, w, 22)
            painter.fillRect(name_r, QColor(0, 0, 0, 180))
            nf = QFont(painter.font())
            nf.setPointSize(8)
            painter.setFont(nf)
            painter.setPen(QColor('#eeeeee'))
            painter.drawText(name_r.adjusted(5, 0, -5, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                data.get('name', ''))

        # Selection
        if selected:
            painter.fillRect(r, QColor(232, 184, 109, 50))
            painter.setPen(QPen(QColor('#e8b86d'), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(1, 1, -1, -1))

        # Border: right + bottom only (1 px)
        painter.setPen(QPen(QColor('#1e1e1e'), 1))
        painter.drawLine(r.right(), r.y(), r.right(), r.bottom())
        painter.drawLine(r.x(), r.bottom(), r.right(), r.bottom())

        painter.restore()

    def sizeHint(self, option, index):
        d = index.data(Qt.ItemDataRole.UserRole)
        if d and 'cell_size' in d:
            return d['cell_size']
        return QSize(CELL_W, CELL_H)


# ─────────────────────────────────────────────
#  Panel
# ─────────────────────────────────────────────

class FilmsPanel(QWidget):

    play_requested = pyqtSignal(str)

    _SORT_BTN_STYLE = (
        "QPushButton{background:#111;border:1px solid #252525;border-radius:3px;"
        "color:#444;font-size:10px;padding:2px 7px;}"
        "QPushButton:hover{color:#888;border-color:#444;}"
    )
    _SORT_BTN_ACTIVE = (
        "QPushButton{background:#1a1400;border:1px solid #554400;border-radius:3px;"
        "color:#e8b86d;font-size:10px;padding:2px 7px;}"
        "QPushButton:hover{border-color:#e8b86d;}"
    )
    _FILTER_BTN_STYLE = (
        "QPushButton{background:#111;border:1px solid #252525;border-radius:3px;"
        "color:#444;font-size:10px;padding:2px 7px;}"
        "QPushButton:hover{color:#888;border-color:#444;}"
    )
    _FILTER_BTN_ACTIVE = (
        "QPushButton{background:#001818;border:1px solid #004040;border-radius:3px;"
        "color:#4db8b8;font-size:10px;padding:2px 7px;}"
        "QPushButton:hover{border-color:#4db8b8;}"
    )
    # Kleine vierkante icoontjesknopjes — padding:0 zodat het karakter zichtbaar blijft
    _ICON_BTN_STYLE = (
        "QPushButton{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:4px;"
        "color:#888;font-size:14px;padding:0;}"
        "QPushButton:hover{border-color:#e8b86d;color:#e8b86d;}"
        "QPushButton:pressed{background:#e8b86d;color:#000;}"
    )

    def __init__(self):
        super().__init__()
        self._all_items:      list = []
        self._sort_key:       str  = 'name'
        self._sort_asc:       bool = True
        self._sort_btns:      dict = {}
        self._zoom_level:     int  = int(db.get_setting('zoom_films_panel', '0') or '0')
        self._dur_worker:     _DurationWorker | None = None   # background ffprobe worker
        # Filter toggles
        self._flt_1thumb:       bool = False
        self._flt_multithumb:   bool = False
        self._flt_no_thumb:     bool = False
        self._flt_with_markers: bool = False
        self._flt_no_markers:   bool = False
        self._build_ui()
        folder = db.get_setting('film_folder', '')
        if folder:
            ensure_volume_id(folder)   # schrijft .cinedata/volume.id als die er nog niet is
            self._update_folder_label(folder)
            self._scan_folder(folder)

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Single toolbar: alles op één lijn ─────
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet(
            "QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; }"
        )
        b = QHBoxLayout(bar)
        b.setContentsMargins(10, 0, 10, 0)
        b.setSpacing(4)

        # helper: dunne verticale scheidingslijn
        def _vsep():
            s = QFrame()
            s.setFrameShape(QFrame.Shape.VLine)
            s.setFixedSize(1, 22)
            s.setStyleSheet("QFrame { background: #2a2a2a; }")
            return s

        lbl = QLabel("FILMS")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px;")
        b.addWidget(lbl)

        self.lbl_folder = QLabel("Geen map")
        self.lbl_folder.setStyleSheet("color: #383838; font-size: 10px;")
        b.addWidget(self.lbl_folder)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: #333; font-size: 10px;")
        b.addWidget(self.lbl_count)

        b.addSpacing(4)
        b.addWidget(_vsep())
        b.addSpacing(4)

        # ── Sorteerknopjes ────────────────────────
        for key, label in SORT_FIELDS:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setStyleSheet(self._SORT_BTN_STYLE)
            btn.clicked.connect(lambda _, k=key: self._set_sort(k))
            b.addWidget(btn)
            self._sort_btns[key] = btn

        b.addSpacing(4)
        b.addWidget(_vsep())
        b.addSpacing(4)

        # ── Filterknopjes ─────────────────────────
        self._btn_flt_1thumb = QPushButton("1 thumb")
        self._btn_flt_1thumb.setFixedHeight(26)
        self._btn_flt_1thumb.setStyleSheet(self._FILTER_BTN_STYLE)
        self._btn_flt_1thumb.setToolTip("Alleen films met precies 1 thumbnail")
        self._btn_flt_1thumb.clicked.connect(lambda: self._toggle_filter('1thumb'))
        b.addWidget(self._btn_flt_1thumb)

        self._btn_flt_multithumb = QPushButton("meer thumb")
        self._btn_flt_multithumb.setFixedHeight(26)
        self._btn_flt_multithumb.setStyleSheet(self._FILTER_BTN_STYLE)
        self._btn_flt_multithumb.setToolTip("Alleen films met meerdere thumbnails")
        self._btn_flt_multithumb.clicked.connect(lambda: self._toggle_filter('multithumb'))
        b.addWidget(self._btn_flt_multithumb)

        self._btn_flt_no_thumb = QPushButton("geen thumb")
        self._btn_flt_no_thumb.setFixedHeight(26)
        self._btn_flt_no_thumb.setStyleSheet(self._FILTER_BTN_STYLE)
        self._btn_flt_no_thumb.setToolTip("Alleen films zonder thumbnail")
        self._btn_flt_no_thumb.clicked.connect(lambda: self._toggle_filter('no_thumb'))
        b.addWidget(self._btn_flt_no_thumb)

        self._btn_flt_with_markers = QPushButton("met markers")
        self._btn_flt_with_markers.setFixedHeight(26)
        self._btn_flt_with_markers.setStyleSheet(self._FILTER_BTN_STYLE)
        self._btn_flt_with_markers.setToolTip("Alleen films met markers")
        self._btn_flt_with_markers.clicked.connect(lambda: self._toggle_filter('with_markers'))
        b.addWidget(self._btn_flt_with_markers)

        self._btn_flt_no_markers = QPushButton("geen markers")
        self._btn_flt_no_markers.setFixedHeight(26)
        self._btn_flt_no_markers.setStyleSheet(self._FILTER_BTN_STYLE)
        self._btn_flt_no_markers.setToolTip("Alleen films zonder markers")
        self._btn_flt_no_markers.clicked.connect(lambda: self._toggle_filter('no_markers'))
        b.addWidget(self._btn_flt_no_markers)

        b.addSpacing(4)
        b.addWidget(_vsep())
        b.addSpacing(4)

        # ── Duurfilter (min / max) ────────────────
        _dur_lbl = QLabel("⏱")
        _dur_lbl.setStyleSheet("color:#444;font-size:12px;")
        b.addWidget(_dur_lbl)

        _dur_input_style = (
            "QLineEdit{background:#111;border:1px solid #252525;border-radius:3px;"
            "color:#4db8b8;font-size:10px;padding:1px 4px;}"
            "QLineEdit:focus{border-color:#4db8b8;}"
            "QLineEdit[hasValue='true']{border-color:#004040;background:#001818;}"
        )

        self._dur_min_input = QLineEdit()
        self._dur_min_input.setPlaceholderText("min")
        self._dur_min_input.setFixedSize(52, 26)
        self._dur_min_input.setToolTip("Minimale duur  (bijv. 10  of  1:30:00)")
        self._dur_min_input.setStyleSheet(_dur_input_style)
        self._dur_min_input.textChanged.connect(self._apply_search_visibility)
        b.addWidget(self._dur_min_input)

        _dash = QLabel("–")
        _dash.setStyleSheet("color:#333;")
        b.addWidget(_dash)

        self._dur_max_input = QLineEdit()
        self._dur_max_input.setPlaceholderText("max")
        self._dur_max_input.setFixedSize(52, 26)
        self._dur_max_input.setToolTip("Maximale duur  (bijv. 30  of  2:00:00)")
        self._dur_max_input.setStyleSheet(_dur_input_style)
        self._dur_max_input.textChanged.connect(self._apply_search_visibility)
        b.addWidget(self._dur_max_input)

        b.addSpacing(4)
        b.addWidget(_vsep())
        b.addSpacing(4)

        # ── Groottefilter (min / max MB) ──────────
        _sz_lbl = QLabel("MB")
        _sz_lbl.setStyleSheet("color:#444;font-size:9px;letter-spacing:1px;")
        b.addWidget(_sz_lbl)

        _sz_input_style = (
            "QLineEdit{background:#111;border:1px solid #252525;border-radius:3px;"
            "color:#b89060;font-size:10px;padding:1px 4px;}"
            "QLineEdit:focus{border-color:#b89060;}"
        )

        self._sz_min_input = QLineEdit()
        self._sz_min_input.setPlaceholderText("min")
        self._sz_min_input.setFixedSize(52, 26)
        self._sz_min_input.setToolTip("Minimale bestandsgrootte  (bijv. 200  of  1.5GB)")
        self._sz_min_input.setStyleSheet(_sz_input_style)
        self._sz_min_input.textChanged.connect(self._apply_search_visibility)
        b.addWidget(self._sz_min_input)

        _sz_dash = QLabel("–")
        _sz_dash.setStyleSheet("color:#333;")
        b.addWidget(_sz_dash)

        self._sz_max_input = QLineEdit()
        self._sz_max_input.setPlaceholderText("max")
        self._sz_max_input.setFixedSize(52, 26)
        self._sz_max_input.setToolTip("Maximale bestandsgrootte  (bijv. 2000  of  4GB)")
        self._sz_max_input.setStyleSheet(_sz_input_style)
        self._sz_max_input.textChanged.connect(self._apply_search_visibility)
        b.addWidget(self._sz_max_input)

        b.addStretch()

        # ── Zoekbalk + actieknoppen ───────────────
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Zoeken...")
        self.search_input.setFixedWidth(140)
        self.search_input.setFixedHeight(26)
        self.search_input.textChanged.connect(self._filter)
        b.addWidget(self.search_input)

        btn_reset = QPushButton("⊘")
        btn_reset.setFixedSize(28, 28)
        btn_reset.setToolTip("Reset alle filters")
        btn_reset.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:4px;"
            "color:#555;font-size:14px;padding:0;}"
            "QPushButton:hover{border-color:#cc4444;color:#cc4444;}"
            "QPushButton:pressed{background:#cc4444;color:#fff;}"
        )
        btn_reset.clicked.connect(self._reset_all_filters)
        b.addWidget(btn_reset)

        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedSize(28, 28)
        btn_refresh.setToolTip("Herlaad map")
        btn_refresh.setStyleSheet(self._ICON_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh)
        b.addWidget(btn_refresh)

        btn_folder = QPushButton("📁")
        btn_folder.setFixedSize(28, 28)
        btn_folder.setToolTip("Kies filmmap")
        btn_folder.setStyleSheet(self._ICON_BTN_STYLE)
        btn_folder.clicked.connect(self._pick_folder)
        b.addWidget(btn_folder)

        btn_zoom_out = QPushButton("−")
        btn_zoom_out.setFixedSize(28, 28)
        btn_zoom_out.setStyleSheet(self._ICON_BTN_STYLE)
        btn_zoom_out.setAutoRepeat(True)
        btn_zoom_out.setAutoRepeatDelay(400)
        btn_zoom_out.setAutoRepeatInterval(80)
        btn_zoom_out.clicked.connect(self._zoom_out)
        b.addWidget(btn_zoom_out)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setFixedSize(28, 28)
        btn_zoom_in.setStyleSheet(self._ICON_BTN_STYLE)
        btn_zoom_in.setAutoRepeat(True)
        btn_zoom_in.setAutoRepeatDelay(400)
        btn_zoom_in.setAutoRepeatInterval(80)
        btn_zoom_in.clicked.connect(self._zoom_in)
        b.addWidget(btn_zoom_in)

        v.addWidget(bar)
        self._update_sort_buttons()

        # ── Grid ─────────────────────────────────
        self.film_list = QListWidget()
        self.film_list.setMouseTracking(True)
        self.film_list.setViewMode(QListView.ViewMode.IconMode)
        self.film_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.film_list.setFlow(QListView.Flow.LeftToRight)
        self.film_list.setWrapping(True)
        self.film_list.setUniformItemSizes(True)
        self.film_list.setSpacing(0)
        _cw0, _ch0 = self._zoom_size()
        self.film_list.setGridSize(QSize(_cw0, _ch0))
        self.film_list.setIconSize(QSize(0, 0))
        self.film_list.setStyleSheet(
            "QListWidget { background: #0a0a0a; border: none; outline: none; }"
            "QListWidget::item { padding: 0; margin: 0; background: transparent; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        self.film_list.setItemDelegate(FilmGridDelegate())
        self.film_list.itemDoubleClicked.connect(self._on_double_click)
        self.film_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.film_list.customContextMenuRequested.connect(self._show_context_menu)
        v.addWidget(self.film_list, stretch=1)

        # Animation timer — hertekent de viewport zodat gestaggerde
        # per-item thumbnail-wissels (elke ~2s, eigen fase) zichtbaar worden
        self._tick = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)   # ~30 fps voor vloeiende crossfade
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_timer.start()

    # ── Sort ─────────────────────────────────────

    def _set_sort(self, key: str):
        if self._sort_key == key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_key = key
            self._sort_asc = True
        self._update_sort_buttons()
        self._sort_and_repopulate()

    def _update_sort_buttons(self):
        arrow = ' ↑' if self._sort_asc else ' ↓'
        for key, label in SORT_FIELDS:
            btn = self._sort_btns[key]
            if key == self._sort_key:
                btn.setText(label + arrow)
                btn.setStyleSheet(self._SORT_BTN_ACTIVE)
            else:
                btn.setText(label)
                btn.setStyleSheet(self._SORT_BTN_STYLE)

    def _sort_key_fn(self, item):
        d = item.data(Qt.ItemDataRole.UserRole)
        if not d:
            return 0
        k = self._sort_key
        if k == 'name':
            return d.get('name', '').lower()
        if k == 'size':
            return d.get('size', 0)
        if k == 'date':
            return d.get('date', 0)
        if k == 'markers':
            return d.get('markers', 0)
        if k == 'duration':
            return d.get('duration', 0)
        if k == 'actors':
            return d.get('actor_count', 0)
        return 0

    def _sort_and_repopulate(self):
        # takeItem removes items from the list without deleting the C++ objects
        items = []
        while self.film_list.count():
            items.append(self.film_list.takeItem(0))

        items.sort(key=self._sort_key_fn, reverse=not self._sort_asc)

        for item in items:
            self.film_list.addItem(item)
        self._all_items = items
        self._apply_search_visibility()

    # ── Animation ────────────────────────────────

    def _anim_tick(self):
        self._tick += 1
        self.film_list.itemDelegate().set_tick(self._tick)
        self.film_list.viewport().update()

    # ── Zoom ─────────────────────────────────────

    def _zoom_size(self):
        w = max(ZOOM_MIN_W, CELL_W + self._zoom_level * ZOOM_STEP_W)
        h = w * 9 // 16
        return w, h

    def _zoom_in(self):
        self._zoom_level += 1
        db.set_setting('zoom_films_panel', str(self._zoom_level))
        self._apply_zoom()

    def _zoom_out(self):
        if CELL_W + (self._zoom_level - 1) * ZOOM_STEP_W >= ZOOM_MIN_W:
            self._zoom_level -= 1
            db.set_setting('zoom_films_panel', str(self._zoom_level))
            self._apply_zoom()

    def _apply_zoom(self):
        cw, ch = self._zoom_size()
        self.film_list.setGridSize(QSize(cw, ch))
        for item in self._all_items:
            item.setSizeHint(QSize(cw, ch))
            d = item.data(Qt.ItemDataRole.UserRole)
            if d:
                d['cell_size'] = QSize(cw, ch)
                item.setData(Qt.ItemDataRole.UserRole, d)
        self.film_list.itemDelegate().invalidate_cache()
        self.film_list.update()

    # ── Folder ───────────────────────────────────

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecteer filmmap")
        if folder:
            db.set_setting('film_folder', folder)
            ensure_volume_id(folder)   # schrijft .cinedata/volume.id op de SSD
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
        # Stop any running duration worker before rebuilding the list
        if self._dur_worker and self._dur_worker.isRunning():
            self._dur_worker.stop()
            self._dur_worker.wait(500)
            self._dur_worker = None

        self.film_list.clear()
        self._all_items.clear()
        self.film_list.itemDelegate().invalidate_cache()

        folder_path = Path(folder)
        if not folder_path.exists():
            return

        db_films = {f['file_path']: f for f in db.get_all_films()}

        # Batch-queries — één DB-call voor alles, geen per-film queries
        all_thumbs        = db.get_all_film_thumbnails_batch()       # {film_id: [path]}
        all_actor_counts  = db.get_actor_counts_batch()              # {film_id: count}
        all_actor_photos  = db.get_actor_photos_for_films_batch()    # {film_id: [photo_path]}

        films = sorted(
            (f for f in folder_path.iterdir() if f.suffix.lower() in VIDEO_EXTS),
            key=lambda f: f.name.lower()
        )

        dur_tasks = []   # (film_id, file_path) pairs that still need ffprobe

        for fp in films:
            db_film   = db_films.get(str(fp), {})
            film_id   = db_film.get('id')
            thumbnail = db_film.get('thumbnail', '')
            duration  = db_film.get('duration', 0) or 0
            if duration == 0:
                dur_tasks.append((film_id, str(fp)))   # defer to background worker

            # Thumbnails uit batch — geen extra DB-query
            if film_id and film_id in all_thumbs:
                thumbnails = [p for p in all_thumbs[film_id] if os.path.exists(p)]
            else:
                thumbnails = []
            if not thumbnails and thumbnail and os.path.exists(thumbnail):
                thumbnails = [thumbnail]

            # Bestandsgrootte + datum uit DB-cache; alleen stat() als nog niet gecached
            size = db_film.get('file_size',  0) or 0
            date = db_film.get('file_mtime', 0) or 0
            if (size == 0 or date == 0):
                try:
                    st   = fp.stat()
                    size = st.st_size
                    date = st.st_mtime
                    if film_id:
                        db.update_film_file_stats(film_id, size, date)
                except OSError:
                    pass

            # Marker-tellingen uit DB (geen JSON-read van SSD)
            markers     = db_film.get('marker_count',     0) or 0
            neg_markers = db_film.get('neg_marker_count', 0) or 0
            # Actor-count + foto-paden uit batch (geen DB-queries in paint())
            actor_count  = all_actor_counts.get(film_id, 0) if film_id else 0
            actor_photos = all_actor_photos.get(film_id, []) if film_id else []

            cw, ch = self._zoom_size()
            item = QListWidgetItem()
            item.setSizeHint(QSize(cw, ch))
            item.setToolTip(fp.stem)
            item.setData(Qt.ItemDataRole.UserRole, {
                'path':          str(fp),
                'name':          fp.stem,
                'thumbnail':     thumbnail,
                'thumbnails':    thumbnails,
                'film_id':       film_id,
                'size':          size,
                'date':          date,
                'markers':       markers,
                'neg_markers':   neg_markers,
                'duration':      duration,
                'actor_count':   actor_count,
                'actor_photos':  actor_photos,   # pre-geladen, geen DB-query in paint()
                'cell_size':     QSize(cw, ch),
                'thumb_phase':   random.uniform(0.0, 2.0),
            })
            self.film_list.addItem(item)
            self._all_items.append(item)

        self._sort_and_repopulate()
        self._update_count()

        # Start background ffprobe for films without a cached duration
        if dur_tasks:
            self._dur_worker = _DurationWorker(dur_tasks)
            self._dur_worker.duration_ready.connect(self._on_duration_ready)
            self._dur_worker.start()

    def _on_duration_ready(self, film_id: int, file_path: str, duration: float):
        """Called from the background worker when ffprobe returns a duration."""
        # Persist to DB (film_id == -1 means film not in DB yet — skip DB write)
        if film_id > 0:
            db.set_film_duration(film_id, duration)
        # Update the matching item in the list in-place
        for item in self._all_items:
            d = item.data(Qt.ItemDataRole.UserRole)
            if d and d.get('path') == file_path:
                d['duration'] = duration
                item.setData(Qt.ItemDataRole.UserRole, d)
                break
        self.film_list.viewport().update()

    # ── Filter ───────────────────────────────────

    def _filter(self, query: str):
        self._apply_search_visibility()

    def _toggle_filter(self, key: str):
        if key == '1thumb':
            self._flt_1thumb = not self._flt_1thumb
        elif key == 'multithumb':
            self._flt_multithumb = not self._flt_multithumb
        elif key == 'no_thumb':
            self._flt_no_thumb = not self._flt_no_thumb
        elif key == 'with_markers':
            self._flt_with_markers = not self._flt_with_markers
        elif key == 'no_markers':
            self._flt_no_markers = not self._flt_no_markers
        self._update_filter_buttons()
        self._apply_search_visibility()

    def _update_filter_buttons(self):
        self._btn_flt_1thumb.setStyleSheet(
            self._FILTER_BTN_ACTIVE if self._flt_1thumb else self._FILTER_BTN_STYLE)
        self._btn_flt_multithumb.setStyleSheet(
            self._FILTER_BTN_ACTIVE if self._flt_multithumb else self._FILTER_BTN_STYLE)
        self._btn_flt_no_thumb.setStyleSheet(
            self._FILTER_BTN_ACTIVE if self._flt_no_thumb else self._FILTER_BTN_STYLE)
        self._btn_flt_with_markers.setStyleSheet(
            self._FILTER_BTN_ACTIVE if self._flt_with_markers else self._FILTER_BTN_STYLE)
        self._btn_flt_no_markers.setStyleSheet(
            self._FILTER_BTN_ACTIVE if self._flt_no_markers else self._FILTER_BTN_STYLE)

    def _reset_all_filters(self):
        """Wis alle actieve filters en zoektekst."""
        self._flt_1thumb       = False
        self._flt_multithumb   = False
        self._flt_no_thumb     = False
        self._flt_with_markers = False
        self._flt_no_markers   = False
        for w in (self._dur_min_input, self._dur_max_input,
                  self._sz_min_input,  self._sz_max_input):
            w.blockSignals(True)
            w.clear()
            w.blockSignals(False)
        self.search_input.clear()          # triggers _filter → _apply_search_visibility
        self._update_filter_buttons()
        self._apply_search_visibility()

    def _apply_search_visibility(self):
        q = self.search_input.text().lower()
        for item in self._all_items:
            d    = item.data(Qt.ItemDataRole.UserRole)
            name = d.get('name', '').lower() if d else ''

            # Text search
            if q and q not in name:
                item.setHidden(True)
                continue

            # Thumbnail & marker filters
            if d:
                thumb_count  = len(d.get('thumbnails', []))
                marker_count = (d.get('markers', 0) or 0) + (d.get('neg_markers', 0) or 0)

                if self._flt_1thumb and thumb_count != 1:
                    item.setHidden(True)
                    continue
                if self._flt_multithumb and thumb_count <= 1:
                    item.setHidden(True)
                    continue
                if self._flt_no_thumb and thumb_count > 0:
                    item.setHidden(True)
                    continue
                if self._flt_with_markers and marker_count == 0:
                    item.setHidden(True)
                    continue
                if self._flt_no_markers and marker_count > 0:
                    item.setHidden(True)
                    continue

                # Duration filter
                dur   = d.get('duration', 0) or 0
                min_s = _parse_duration_input(self._dur_min_input.text())
                max_s = _parse_duration_input(self._dur_max_input.text())
                if min_s > 0 and dur < min_s:
                    item.setHidden(True)
                    continue
                if max_s > 0 and dur > max_s:
                    item.setHidden(True)
                    continue

                # Size filter
                size_b    = d.get('size', 0) or 0
                min_bytes = _parse_size_input(self._sz_min_input.text())
                max_bytes = _parse_size_input(self._sz_max_input.text())
                if min_bytes > 0 and size_b < min_bytes:
                    item.setHidden(True)
                    continue
                if max_bytes > 0 and size_b > max_bytes:
                    item.setHidden(True)
                    continue

            item.setHidden(False)
        self._update_count()

    def _update_count(self):
        visible = sum(1 for i in self._all_items if not i.isHidden())
        total   = len(self._all_items)
        self.lbl_count.setText(f"  {visible} / {total} films")

    # ── Context menu ─────────────────────────────

    def _show_context_menu(self, pos):
        item = self.film_list.itemAt(pos)
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not d:
            return

        menu = QMenu(self)
        act_play   = menu.addAction("▶  Afspelen")
        menu.addSeparator()
        act_delete = menu.addAction("🗑  Verplaats naar map 'deleted'")

        chosen = menu.exec(self.film_list.viewport().mapToGlobal(pos))
        if chosen == act_play:
            self.play_requested.emit(d['path'])
        elif chosen == act_delete:
            self._confirm_and_delete(item, d)

    def _confirm_and_delete(self, item, d):
        name = d.get('name', Path(d.get('path', '')).stem)
        reply = QMessageBox.question(
            self,
            "Film verplaatsen",
            f"'{name}' verplaatsen naar de map 'deleted'?\n\n"
            "De film verdwijnt uit de applicatie maar blijft als\n"
            "bestand bewaard in de submap 'deleted'.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, msg = self.delete_film(d.get('path', ''))
        if not ok:
            QMessageBox.warning(self, "Fout bij verplaatsen", msg)

    def delete_film(self, file_path: str) -> tuple:
        """Move film + markers to a 'deleted/' subfolder; remove from DB and list.

        Returns (success: bool, error_message: str).
        """
        p = Path(file_path)

        if p.exists():
            deleted_dir = p.parent / 'deleted'
            try:
                deleted_dir.mkdir(exist_ok=True)
            except OSError as e:
                return False, f"Kan map 'deleted' niet aanmaken:\n{e}"

            dest = deleted_dir / p.name
            # Avoid collision — append a counter
            if dest.exists():
                i = 1
                while dest.exists():
                    dest = deleted_dir / f"{p.stem}_{i}{p.suffix}"
                    i += 1

            try:
                p.rename(dest)
            except OSError as e:
                return False, f"Kan bestand niet verplaatsen:\n{e}"

            # Move markers JSON alongside the film
            mf = p.parent / f".{p.stem}_markers.json"
            if mf.exists():
                try:
                    mf.rename(deleted_dir / mf.name)
                except OSError:
                    pass  # not critical — markers file stays behind, no harm

        # Remove from DB (cascades to film_thumbnails, scenes, actor_films…)
        db.delete_film_by_path(file_path)

        # Remove from displayed list
        for i in range(self.film_list.count()):
            it = self.film_list.item(i)
            if it:
                itd = it.data(Qt.ItemDataRole.UserRole)
                if itd and itd.get('path') == file_path:
                    self.film_list.takeItem(i)
                    self._all_items = [x for x in self._all_items if x is not it]
                    break

        self._update_count()
        return True, ''

    # ── Play ─────────────────────────────────────

    def _on_double_click(self, item):
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            self.play_requested.emit(d['path'])
