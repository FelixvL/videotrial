#!/usr/bin/env python3
"""
CineMarker — Acteurs module
Acteursbeheer, filmkoppelingen, scène-editor
"""

import os
import csv
import io
import json
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QSplitter, QLineEdit,
    QFileDialog, QMessageBox, QInputDialog, QScrollArea,
    QFrame, QGridLayout, QTextEdit, QDialog,
    QProgressBar, QCheckBox, QSizePolicy, QStackedWidget,
    QStyledItemDelegate, QApplication, QComboBox, QStyle, QListView
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QTimer, QRect, QEvent
from PyQt6.QtGui import QPixmap, QFont, QIcon, QPen, QColor, QPainter

import database as db
from films_panel import (FilmGridDelegate as _FilmGridDelegate,
                         load_scaled_cache, save_scaled_cache)
from paths import THUMBNAILS_DIR, MARKER_THUMBS_DIR, SCALED_ACTOR_CARDS_DIR


def _count_actor_markers(actor_id: int, films: list) -> int:
    """Count how many markers across all films reference this actor."""
    count = 0
    for film in films:
        p = Path(film['file_path'])
        mf = p.parent / f".{p.stem}_markers.json"
        if mf.exists():
            try:
                import json as _json
                for m in _json.load(open(str(mf), 'r')):
                    if actor_id in (m.get('actors') or []):
                        count += 1
            except Exception:
                pass
    return count


# ─────────────────────────────────────────────
#  Frame Extraction Worker
# ─────────────────────────────────────────────

class FrameExtractWorker(QThread):
    """Extract single frames from videos at given timestamps using ffmpeg."""

    frame_ready = pyqtSignal(int, str)   # row_index, cache_path

    def __init__(self, tasks: list):
        super().__init__()
        # tasks: [(row_idx, film_path, time_sec, cache_path), ...]
        self._tasks = tasks
        self._stop  = False

    def stop(self):
        self._stop = True

    # Target resolution for extracted frames — wide enough for all zoom levels
    THUMB_W = 320
    THUMB_H = 180

    def run(self):
        for row_idx, film_path, time_sec, cache_path in self._tasks:
            if self._stop:
                break
            if not os.path.exists(cache_path):
                try:
                    scale = f"scale={self.THUMB_W}:{self.THUMB_H}"
                    result = subprocess.run([
                        'ffmpeg', '-y',
                        '-ss', str(time_sec),
                        '-i', film_path,
                        '-frames:v', '1',
                        '-vf', scale,
                        '-q:v', '2',
                        cache_path,
                    ], capture_output=True, timeout=30)
                    # Fallback: extract without scale filter (handles unusual formats)
                    if result.returncode != 0 or not os.path.exists(cache_path):
                        subprocess.run([
                            'ffmpeg', '-y',
                            '-ss', str(time_sec),
                            '-i', film_path,
                            '-frames:v', '1',
                            '-q:v', '2',
                            cache_path,
                        ], capture_output=True, timeout=30)
                except Exception:
                    continue
            if os.path.exists(cache_path):
                self.frame_ready.emit(row_idx, cache_path)


# ─────────────────────────────────────────────
#  Marker Grid Delegate
# ─────────────────────────────────────────────

class MarkerGridDelegate(QStyledItemDelegate):
    """Paints a marker grid cell: frame thumbnail + category icon overlay + time bar."""

    remove_requested = pyqtSignal(dict)   # emits the item's UserRole data dict

    _BTN_SZ = 16   # ✕ button size in pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_cache: dict = {}   # only caches successful loads

    def invalidate_cache(self):
        self._thumb_cache.clear()

    def _remove_btn_rect(self, cell_rect: QRect) -> QRect:
        """Top-right corner ✕ hit area."""
        return QRect(cell_rect.right() - self._BTN_SZ,
                     cell_rect.top(),
                     self._BTN_SZ, self._BTN_SZ)

    def _get_pix(self, path: str, w: int, h: int):
        """Return scaled-and-cropped QPixmap or None. Only caches successes."""
        if not path or not os.path.exists(path):
            return None
        key = f"{path}:{w}:{h}"
        if key in self._thumb_cache:
            return self._thumb_cache[key]
        raw = QPixmap(path)
        if raw.isNull():
            return None
        sc = raw.scaled(w, h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        ox = (sc.width() - w) // 2
        oy = (sc.height() - h) // 2
        pix = sc.copy(ox, oy, w, h)
        self._thumb_cache[key] = pix
        return pix

    def sizeHint(self, option, index):
        d = index.data(Qt.ItemDataRole.UserRole)
        if d and 'cell_size' in d:
            return d['cell_size']
        return QSize(128, 72)

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            super().paint(painter, option, index)
            return

        r = option.rect
        w, h = r.width(), r.height()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Frame thumbnail
        pix = self._get_pix(data.get('cache_path', ''), w, h)
        if pix:
            painter.drawPixmap(r.x(), r.y(), pix)
        else:
            painter.fillRect(r, QColor('#0d0d0d'))
            f = QFont(painter.font())
            f.setPointSize(14)
            painter.setFont(f)
            painter.setPen(QColor('#252525'))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, '◉')

        # Category icons — top-left overlay
        cat_pixmaps = data.get('cat_pixmaps', [])
        if cat_pixmaps:
            cat_sz = max(14, min(20, h // 4))
            cx = r.x() + 3
            cy = r.y() + 3
            for cp in cat_pixmaps:
                sc_c = cp.scaled(cat_sz, cat_sz,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                painter.fillRect(QRect(cx - 1, cy - 1, cat_sz + 2, cat_sz + 2),
                                 QColor(0, 0, 0, 160))
                painter.drawPixmap(cx, cy, sc_c)
                cx += cat_sz + 3

        # Time bar — bottom
        time_str = data.get('time_str', '')
        stars    = data.get('stars', 0) or 0
        if time_str:
            bar_h = 16
            bar_r = QRect(r.x(), r.bottom() - bar_h + 1, w, bar_h)
            painter.fillRect(bar_r, QColor(0, 0, 0, 170))
            bf = QFont(painter.font())
            bf.setPointSize(7)
            painter.setFont(bf)
            painter.setPen(QColor('#aaaaaa'))
            # Stars badge on the right side of the bar
            if stars > 0:
                star_str = '★' * stars
                sf = QFont(painter.font())
                sf.setPointSize(7)
                painter.setFont(sf)
                fm_s = painter.fontMetrics()
                sw = fm_s.horizontalAdvance(star_str) + 4
                star_r = QRect(bar_r.right() - sw, bar_r.top(), sw, bar_h)
                painter.setPen(QColor('#e8b86d'))
                painter.drawText(star_r, Qt.AlignmentFlag.AlignCenter, star_str)
                text_r = bar_r.adjusted(4, 0, -sw - 2, 0)
            else:
                text_r = bar_r.adjusted(4, 0, -4, 0)
            painter.setPen(QColor('#aaaaaa'))
            painter.drawText(text_r,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                time_str)

        # Selection highlight
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(r, QColor(232, 184, 109, 40))
            painter.setPen(QPen(QColor('#e8b86d'), 2))
            painter.drawRect(r.adjusted(1, 1, -1, -1))

        # ✕ remove button — top-right corner, shown on hover
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        btn_r = self._remove_btn_rect(r)
        if hovered:
            painter.fillRect(btn_r, QColor(160, 20, 20, 220))
            xf = QFont(painter.font())
            xf.setPointSize(7)
            xf.setBold(True)
            painter.setFont(xf)
            painter.setPen(QColor('#ffffff'))
            painter.drawText(btn_r, Qt.AlignmentFlag.AlignCenter, '✕')
        else:
            painter.fillRect(btn_r, QColor(0, 0, 0, 120))
            xf = QFont(painter.font())
            xf.setPointSize(7)
            painter.setFont(xf)
            painter.setPen(QColor('#444444'))
            painter.drawText(btn_r, Qt.AlignmentFlag.AlignCenter, '✕')

        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseButtonRelease:
            if self._remove_btn_rect(option.rect).contains(event.pos()):
                data = index.data(Qt.ItemDataRole.UserRole)
                if data:
                    self.remove_requested.emit(data)
                return True
        return super().editorEvent(event, model, option, index)


# ─────────────────────────────────────────────
#  Actor Card Delegate
# ─────────────────────────────────────────────

class ActorCardDelegate(QStyledItemDelegate):

    detail_requested = pyqtSignal(dict)

    BORDER = {
        '9': ('#FFD700', Qt.PenStyle.SolidLine, 3),
        '8': ('#C0C0C0', Qt.PenStyle.SolidLine, 3),
        '7': ('#CD7F32', Qt.PenStyle.SolidLine, 3),
        '6': ('#FFFF00', Qt.PenStyle.DashLine, 2),
        '5': ('#FFFFFF', Qt.PenStyle.DashLine, 2),
    }
    TEXT_COLOR = {
        '1': QColor('#FFFFFF'),
        '2': QColor('#000000'),
        '3': QColor('#8B4513'),
    }
    GLOW_COLOR = {
        '1': QColor(0, 0, 0, 230),
        '2': QColor(255, 255, 255, 230),
        '3': QColor(0, 0, 0, 230),
    }
    BLACK_GLOW = [
        (-1,-1),(0,-1),(1,-1),
        (-1, 0),       (1, 0),
        (-1, 1),(0, 1),(1, 1),
        (-2, 0),(2, 0),(0,-2),(0, 2),
    ]
    ARROW_SIZE = 22

    def __init__(self):
        super().__init__()
        self._cache: dict = {}

    def _get_pix(self, path, w, h):
        key = f"{path}:{w}:{h}"
        if key not in self._cache:
            if os.path.exists(path):
                pix = load_scaled_cache(path, w, h, SCALED_ACTOR_CARDS_DIR)
                if pix is None:
                    raw = QPixmap(path)
                    if not raw.isNull():
                        pix = raw.scaled(w, h,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
                        save_scaled_cache(path, w, h, pix, SCALED_ACTOR_CARDS_DIR)
                self._cache[key] = pix if (pix and not pix.isNull()) else QPixmap()
            else:
                self._cache[key] = QPixmap()
        return self._cache[key]

    def _arrow_rect(self, rect):
        a = self.ARROW_SIZE
        return QRect(rect.right() - a - 2, rect.bottom() - a - 2, a, a)

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            super().paint(painter, option, index)
            return

        r     = option.rect          # full cell, no padding
        meta  = data.get('meta', {})
        in_db = data.get('in_db', False)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Photo — flush, fills entire cell
        pix = self._get_pix(data['photo_path'], r.width(), r.height())
        if not pix.isNull():
            px = r.x() + (r.width()  - pix.width())  // 2
            py = r.y() + (r.height() - pix.height()) // 2
            painter.drawPixmap(px, py, pix)

        # ── Naam (glow, geen balk) ────────────────
        voornaam   = meta.get('voornaam', '')
        achternaam = meta.get('achternaam', '')
        display    = f"{voornaam} {achternaam}".strip() or data.get('stem', '')
        kleur      = str(meta.get('kleur', '1'))
        text_col   = self.TEXT_COLOR.get(kleur, QColor('#FFFFFF'))
        glow_col   = self.GLOW_COLOR.get(kleur, QColor(0, 0, 0, 230))

        nf = QFont(painter.font())
        nf.setPointSize(9)
        nf.setBold(True)
        painter.setFont(nf)

        name_rect  = QRect(r.x() + 2, r.bottom() - 24, r.width() - 4, 22)
        name_flags = Qt.AlignmentFlag.AlignCenter
        painter.setPen(glow_col)
        for dx, dy in self.BLACK_GLOW:
            painter.drawText(name_rect.translated(dx, dy), name_flags, display)
        painter.setPen(text_col)
        painter.drawText(name_rect, name_flags, display)

        # ── Sterren rechtsbovenin (groter + glow) ─
        try:
            stars = int(meta.get('grootte', 0))
        except (ValueError, TypeError):
            stars = 0
        if stars > 0:
            sf = QFont(painter.font())
            sf.setPointSize(12)
            sf.setBold(False)
            painter.setFont(sf)
            star_rect  = QRect(r.x(), r.y() + 2, r.width() - 3, 20)
            star_flags = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            glow_blk   = QColor(0, 0, 0, 230)
            painter.setPen(glow_blk)
            for dx, dy in self.BLACK_GLOW:
                painter.drawText(star_rect.translated(dx, dy), star_flags, '★' * stars)
            painter.setPen(QColor('#FFD700'))
            painter.drawText(star_rect, star_flags, '★' * stars)

        # ── Decennium linksbovenin (groter + glow) ─
        dec_val = str(meta.get('decennia', '')).strip()
        if dec_val and dec_val.lower() not in ('null', ''):
            try:
                dec_str = str(int(dec_val) * 10)
            except ValueError:
                dec_str = dec_val
            df = QFont(painter.font())
            df.setPointSize(11)
            df.setBold(True)
            painter.setFont(df)
            dec_rect  = QRect(r.x() + 4, r.y() + 2, 50, 20)
            dec_flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            painter.setPen(QColor(0, 0, 0, 230))
            for dx, dy in self.BLACK_GLOW:
                painter.drawText(dec_rect.translated(dx, dy), dec_flags, dec_str)
            painter.setPen(QColor('#dddddd'))
            painter.drawText(dec_rect, dec_flags, dec_str)

        # ── Rating rand ───────────────────────────
        rating = str(meta.get('rating', '')).strip()
        if rating in self.BORDER:
            col, style, width = self.BORDER[rating]
            painter.setPen(QPen(QColor(col), width, style))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(1, 1, -1, -1))

        # ── Pijltje (detail) ──────────────────────
        ar = self._arrow_rect(r)
        painter.fillRect(ar, QColor(0, 0, 0, 170))
        af = QFont(painter.font())
        af.setPointSize(13)
        af.setBold(True)
        painter.setFont(af)
        painter.setPen(QColor('#e8b86d'))
        painter.drawText(ar, Qt.AlignmentFlag.AlignCenter, '›')

        # ── Film- en markertellingen (linksonder) ─
        film_count   = data.get('film_count', 0)
        marker_count = data.get('marker_count', 0)
        if film_count or marker_count:
            bf = QFont(painter.font())
            bf.setPointSize(7)
            bf.setBold(False)
            painter.setFont(bf)
            badge_y = r.bottom() - 38
            x_cur   = r.x() + 3
            for symbol, count, col in (
                ('▶', film_count,   '#e8b86d'),
                ('◉', marker_count, '#6db8e8'),
            ):
                if count == 0:
                    continue
                txt = f"{symbol}{count}"
                fm  = painter.fontMetrics()
                tw  = fm.horizontalAdvance(txt) + 6
                br  = QRect(x_cur, badge_y, tw, 14)
                painter.fillRect(br, QColor(0, 0, 0, 160))
                painter.setPen(QColor(col))
                painter.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)
                x_cur += tw + 3

        # ── Niet in DB overlay ────────────────────
        if not in_db:
            painter.fillRect(r, QColor(0, 0, 0, 140))
            fi = QFont(painter.font())
            fi.setPointSize(8)
            fi.setBold(False)
            painter.setFont(fi)
            painter.setPen(QColor('#555'))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "niet in\ndatabase")

        # ── Selectie highlight ────────────────────
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(r, QColor(232, 184, 109, 30))
            painter.setPen(QPen(QColor('#e8b86d'), 2))
            painter.drawRect(r.adjusted(1, 1, -1, -1))

        painter.restore()

    def editorEvent(self, event, model, option, index):
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.MouseButtonRelease:
            pos = event.position().toPoint()
            if self._arrow_rect(option.rect).contains(pos):
                data = index.data(Qt.ItemDataRole.UserRole)
                if data:
                    self.detail_requested.emit(data)
                return True
        return False

    def sizeHint(self, option, index):
        d = index.data(Qt.ItemDataRole.UserRole)
        if d and 'cell_size' in d:
            return d['cell_size']
        return QSize(160, 206)


# ─────────────────────────────────────────────
#  Scene Editor Dialog
# ─────────────────────────────────────────────

def format_time(seconds):
    if seconds is None or seconds < 0:
        return "00:00:00.000"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


# ─────────────────────────────────────────────
#  Wrap-layout helper voor trait-knoppen
# ─────────────────────────────────────────────

class _WrapLayout(QHBoxLayout):
    """Eenvoudige horizontale lay-out voor checkbare trait-knoppen.
    Overschrijdt geen complexe wrap-logica — bij veel traits scrolt de sectie."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(4)

    def clear_buttons(self):
        while self.count():
            item = self.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def add_trait_btn(self, trait_id: int, naam: str, checked: bool,
                       on_toggle) -> QPushButton:
        btn = QPushButton(naam)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setFixedHeight(22)
        btn.setStyleSheet(
            "QPushButton{background:#111;border:1px solid #252525;border-radius:3px;"
            "  color:#555;font-size:9px;padding:0 6px;}"
            "QPushButton:hover{border-color:#666;color:#999;}"
            "QPushButton:checked{background:#0a1a0a;border-color:#3a6644;color:#5a9a6a;}"
        )
        btn.toggled.connect(lambda checked, tid=trait_id: on_toggle(tid, checked))
        self.addWidget(btn)
        return btn


# ─────────────────────────────────────────────
#  Actor Detail View  (full-screen embedded page)
# ─────────────────────────────────────────────

class ActorDetailView(QWidget):

    back_requested        = pyqtSignal()
    saved                 = pyqtSignal()
    open_film_requested   = pyqtSignal(str)
    marker_jump_requested = pyqtSignal(str, float)
    navigate_requested    = pyqtSignal(int)   # -1 vorige · +1 volgende

    GROOTTE_OPTS = [('', '—')] + [(str(i), '★' * i) for i in range(1, 6)]
    RATING_OPTS  = [('', '—')] + [(str(i), str(i)) for i in range(1, 10)]
    DEC_OPTS     = ([('', '—')] +
                    [(str(d), f"{d*10}s") for d in range(3, 10)] +
                    [('0', '00s'), ('1', '10s'), ('2', '20s')])

    _FILMS_CELL_W    = 160
    _FILMS_CELL_H    = 90
    _FILMS_ZOOM_STEP = 32
    _FILMS_ZOOM_MIN  = 64

    _MARKERS_CELL_W    = 128
    _MARKERS_CELL_H    = 72
    _MARKERS_ZOOM_STEP = 24
    _MARKERS_ZOOM_MIN  = 56

    def __init__(self):
        super().__init__()
        self._data:  dict = {}
        self._actor        = None
        self._frame_worker = None
        # Load zoom levels from DB before _build_ui so initial grid sizes are correct
        self._films_zoom_level:   int  = int(db.get_setting('zoom_detail_films',   '0') or '0')
        self._films_tick:         int  = 0
        self._films_all_items:    list = []
        self._markers_zoom_level: int  = int(db.get_setting('zoom_detail_markers', '0') or '0')
        self._markers_all_items:  list = []
        self._markers_cat_filter: set  = set()   # cat IDs to filter by; empty = show all
        self._markers_film_filter: str = ''       # file_path to filter by; '' = show all
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Header bar
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; }")
        b = QHBoxLayout(bar)
        b.setContentsMargins(12, 0, 12, 0)
        b.setSpacing(10)

        btn_back = QPushButton("← Terug")
        btn_back.setFixedHeight(28)
        btn_back.clicked.connect(self.back_requested)
        b.addWidget(btn_back)

        _nav_style_on = (
            "QPushButton { background: transparent; border: none; padding: 0;"
            "  color: #666; font-size: 13px; }"
            "QPushButton:hover { color: #e8b86d; }"
            "QPushButton:pressed { color: #fff; }"
        )
        _nav_style_off = (
            "QPushButton { background: transparent; border: none; padding: 0;"
            "  color: #252525; font-size: 13px; }"
        )

        self._btn_prev = QPushButton("◀")
        self._btn_prev.setFixedSize(26, 28)
        self._btn_prev.setToolTip("Vorige acteur  (houdt rekening met filter/sortering)")
        self._btn_prev.setStyleSheet(_nav_style_off)
        self._btn_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_prev.clicked.connect(lambda: self.navigate_requested.emit(-1))
        b.addWidget(self._btn_prev)

        self._lbl_nav = QLabel("")
        self._lbl_nav.setStyleSheet(
            "color: #2a2a2a; font-size: 10px; font-family: 'Consolas', monospace;")
        self._lbl_nav.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_nav.setFixedWidth(52)
        b.addWidget(self._lbl_nav)

        self._btn_next = QPushButton("▶")
        self._btn_next.setFixedSize(26, 28)
        self._btn_next.setToolTip("Volgende acteur  (houdt rekening met filter/sortering)")
        self._btn_next.setStyleSheet(_nav_style_off)
        self._btn_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_next.clicked.connect(lambda: self.navigate_requested.emit(1))
        b.addWidget(self._btn_next)

        # Store styles for enable/disable toggling
        self._nav_style_on  = _nav_style_on
        self._nav_style_off = _nav_style_off

        self.lbl_stem = QLabel("")
        self.lbl_stem.setStyleSheet("color: #555; font-size: 11px;")
        b.addWidget(self.lbl_stem)

        b.addStretch()

        btn_save = QPushButton("💾  Opslaan")
        btn_save.setObjectName("accent")
        btn_save.setFixedHeight(28)
        btn_save.clicked.connect(self._save)
        b.addWidget(btn_save)

        v.addWidget(bar)

        # Content
        content = QWidget()
        ch = QHBoxLayout(content)
        ch.setContentsMargins(24, 24, 24, 24)
        ch.setSpacing(24)

        # Left: photo
        self.lbl_photo = QLabel()
        self.lbl_photo.setFixedSize(220, 284)
        self.lbl_photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_photo.setStyleSheet(
            "background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 4px;"
            " color: #333; font-size: 40px;"
        )
        self.lbl_photo.setText("?")
        ch.addWidget(self.lbl_photo)

        # Right column
        right = QVBoxLayout()
        right.setSpacing(16)

        # Edit fields
        fields_frame = QFrame()
        fields_frame.setStyleSheet(
            "QFrame { background: #111; border: 1px solid #1e1e1e; border-radius: 6px; }"
        )
        fg = QGridLayout(fields_frame)
        fg.setContentsMargins(16, 16, 16, 16)
        fg.setSpacing(10)
        fg.setColumnStretch(1, 1)
        fg.setColumnStretch(3, 1)

        def lbl(t):
            l = QLabel(t)
            l.setStyleSheet("color: #444; font-size: 10px; letter-spacing: 2px;")
            return l

        fg.addWidget(lbl("VOORNAAM"),    0, 0)
        self.inp_voornaam   = QLineEdit()
        fg.addWidget(self.inp_voornaam,  0, 1)

        fg.addWidget(lbl("ACHTERNAAM"),  0, 2)
        self.inp_achternaam = QLineEdit()
        fg.addWidget(self.inp_achternaam, 0, 3)

        fg.addWidget(lbl("KLEUR"),    1, 0)
        self.cmb_kleur = QComboBox()
        fg.addWidget(self.cmb_kleur, 1, 1)

        fg.addWidget(lbl("GROOTTE"),  1, 2)
        self.cmb_grootte = QComboBox()
        for val, text in self.GROOTTE_OPTS:
            self.cmb_grootte.addItem(text, val)
        fg.addWidget(self.cmb_grootte, 1, 3)

        fg.addWidget(lbl("RATING"),   2, 0)
        self.cmb_rating = QComboBox()
        for val, text in self.RATING_OPTS:
            self.cmb_rating.addItem(text, val)
        fg.addWidget(self.cmb_rating, 2, 1)

        fg.addWidget(lbl("DECENNIA"), 2, 2)
        self.cmb_dec = QComboBox()
        for val, text in self.DEC_OPTS:
            self.cmb_dec.addItem(text, val)
        fg.addWidget(self.cmb_dec, 2, 3)

        fg.addWidget(lbl("PIEK START"), 3, 0)
        self.inp_piek_start = QLineEdit()
        self.inp_piek_start.setPlaceholderText("JJJJ-MM")
        fg.addWidget(self.inp_piek_start, 3, 1)

        fg.addWidget(lbl("PIEK EIND"),  3, 2)
        self.inp_piek_eind = QLineEdit()
        self.inp_piek_eind.setPlaceholderText("JJJJ-MM")
        fg.addWidget(self.inp_piek_eind, 3, 3)

        right.addWidget(fields_frame)

        # ── Traits sectie (sterke / zwakke kanten) ────
        traits_frame = QFrame()
        traits_frame.setStyleSheet(
            "QFrame { background: #111; border: 1px solid #1e1e1e; border-radius: 6px; }"
        )
        tv = QVBoxLayout(traits_frame)
        tv.setContentsMargins(12, 8, 12, 8)
        tv.setSpacing(6)

        traits_header = QHBoxLayout()
        lbl_traits = QLabel("KANTEN")
        lbl_traits.setStyleSheet("color: #444; font-size: 10px; letter-spacing: 3px;")
        traits_header.addWidget(lbl_traits)
        traits_header.addStretch()
        tv.addLayout(traits_header)

        # Sterke kanten
        lbl_sterk = QLabel("Sterk:")
        lbl_sterk.setStyleSheet("color: #3a6644; font-size: 9px; letter-spacing: 1px;")
        tv.addWidget(lbl_sterk)

        self._traits_sterk_widget = QWidget()
        self._traits_sterk_widget.setStyleSheet("background: transparent;")
        self._traits_sterk_layout = _WrapLayout(self._traits_sterk_widget)
        tv.addWidget(self._traits_sterk_widget)

        # Zwakke kanten
        lbl_zwak = QLabel("Zwak:")
        lbl_zwak.setStyleSheet("color: #663a3a; font-size: 9px; letter-spacing: 1px;")
        tv.addWidget(lbl_zwak)

        self._traits_zwak_widget = QWidget()
        self._traits_zwak_widget.setStyleSheet("background: transparent;")
        self._traits_zwak_layout = _WrapLayout(self._traits_zwak_widget)
        tv.addWidget(self._traits_zwak_widget)

        right.addWidget(traits_frame)

        # Films linked to this actor — thumbnail grid
        films_frame = QFrame()
        films_frame.setStyleSheet(
            "QFrame { background: #111; border: 1px solid #1e1e1e; border-radius: 6px; }"
        )
        fv = QVBoxLayout(films_frame)
        fv.setContentsMargins(8, 8, 8, 8)
        fv.setSpacing(4)

        fl_h = QHBoxLayout()
        fl_lbl = QLabel("FILMS")
        fl_lbl.setStyleSheet("color: #444; font-size: 10px; letter-spacing: 3px;")
        fl_h.addWidget(fl_lbl)
        fl_h.addStretch()
        btn_fzo = QPushButton("−")
        btn_fzo.setFixedSize(22, 22)
        btn_fzo.setAutoRepeat(True)
        btn_fzo.setAutoRepeatDelay(400)
        btn_fzo.setAutoRepeatInterval(80)
        btn_fzo.clicked.connect(self._films_zoom_out)
        fl_h.addWidget(btn_fzo)
        btn_fzi = QPushButton("+")
        btn_fzi.setFixedSize(22, 22)
        btn_fzi.setAutoRepeat(True)
        btn_fzi.setAutoRepeatDelay(400)
        btn_fzi.setAutoRepeatInterval(80)
        btn_fzi.clicked.connect(self._films_zoom_in)
        fl_h.addWidget(btn_fzi)
        fv.addLayout(fl_h)

        cw0, ch0 = self._films_zoom_size()
        self.films_list = QListWidget()
        self.films_list.setMouseTracking(True)
        self.films_list.setViewMode(QListView.ViewMode.IconMode)
        self.films_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.films_list.setFlow(QListView.Flow.LeftToRight)
        self.films_list.setWrapping(True)
        self.films_list.setUniformItemSizes(True)
        self.films_list.setSpacing(0)
        self.films_list.setGridSize(QSize(cw0, ch0))
        self.films_list.setIconSize(QSize(0, 0))
        self.films_list.setStyleSheet(
            "QListWidget { background: #0a0a0a; border: none; outline: none; }"
            "QListWidget::item { padding: 0; margin: 0; background: transparent; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        self.films_list.setItemDelegate(_FilmGridDelegate())
        self.films_list.itemDoubleClicked.connect(self._open_film)
        self.films_list.itemClicked.connect(self._on_film_filter_clicked)
        fv.addWidget(self.films_list)

        # Animation timer — cycles multi-thumbnail films every 2 s
        self._films_anim_timer = QTimer(self)
        self._films_anim_timer.setInterval(2000)
        self._films_anim_timer.timeout.connect(self._films_anim_tick)
        self._films_anim_timer.start()

        # Markers linked to this actor — thumbnail grid
        markers_frame = QFrame()
        markers_frame.setStyleSheet(
            "QFrame { background: #111; border: 1px solid #1e1e1e; border-radius: 6px; }"
        )
        mv = QVBoxLayout(markers_frame)
        mv.setContentsMargins(8, 8, 8, 8)
        mv.setSpacing(4)

        mk_h = QHBoxLayout()
        mk_lbl = QLabel("MARKERS")
        mk_lbl.setStyleSheet("color: #444; font-size: 10px; letter-spacing: 3px;")
        mk_h.addWidget(mk_lbl)
        mk_h.addStretch()
        btn_mzo = QPushButton("−")
        btn_mzo.setFixedSize(22, 22)
        btn_mzo.setAutoRepeat(True)
        btn_mzo.setAutoRepeatDelay(400)
        btn_mzo.setAutoRepeatInterval(80)
        btn_mzo.clicked.connect(self._markers_zoom_out)
        mk_h.addWidget(btn_mzo)
        btn_mzi = QPushButton("+")
        btn_mzi.setFixedSize(22, 22)
        btn_mzi.setAutoRepeat(True)
        btn_mzi.setAutoRepeatDelay(400)
        btn_mzi.setAutoRepeatInterval(80)
        btn_mzi.clicked.connect(self._markers_zoom_in)
        mk_h.addWidget(btn_mzi)
        mv.addLayout(mk_h)

        # Category filter buttons — populated dynamically in _refresh_markers()
        self._cat_filter_row = QWidget()
        self._cat_filter_row.setStyleSheet("background: transparent;")
        _cfl = QHBoxLayout(self._cat_filter_row)
        _cfl.setContentsMargins(0, 1, 0, 1)
        _cfl.setSpacing(3)
        _cfl.addStretch()
        self._cat_filter_row.setVisible(False)
        mv.addWidget(self._cat_filter_row)

        mcw, mch = self._markers_zoom_size()
        self.markers_list = QListWidget()
        self.markers_list.setMouseTracking(True)
        self.markers_list.setViewMode(QListView.ViewMode.IconMode)
        self.markers_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.markers_list.setFlow(QListView.Flow.LeftToRight)
        self.markers_list.setWrapping(True)
        self.markers_list.setUniformItemSizes(True)
        self.markers_list.setSpacing(0)
        self.markers_list.setGridSize(QSize(mcw, mch))
        self.markers_list.setIconSize(QSize(0, 0))
        self.markers_list.setStyleSheet(
            "QListWidget { background: #0a0a0a; border: none; outline: none; }"
            "QListWidget::item { padding: 0; margin: 0; background: transparent; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        _marker_delegate = MarkerGridDelegate()
        self.markers_list.setItemDelegate(_marker_delegate)
        _marker_delegate.remove_requested.connect(self._remove_actor_from_marker)
        self.markers_list.itemDoubleClicked.connect(self._jump_to_marker)
        mv.addWidget(self.markers_list)

        fm_row = QHBoxLayout()
        fm_row.setSpacing(12)
        fm_row.addWidget(films_frame, stretch=1)
        fm_row.addWidget(markers_frame, stretch=1)

        right.addLayout(fm_row, stretch=1)

        # ── DATA-bestanden — volledig gescheiden sectie ───────────────────
        # Eigen frame, eigen stijl, eigen logica — géén overlap met films.
        # Alleen zichtbaar als er bigfiles aan deze acteur zijn gekoppeld.
        self._data_frame = QFrame()
        self._data_frame.setVisible(False)
        self._data_frame.setStyleSheet(
            "QFrame#data_frame {"
            "  background:#0c0c10;"
            "  border:1px solid #1e1e2a;"
            "  border-radius:6px;"
            "}"
        )
        self._data_frame.setObjectName("data_frame")
        dv = QVBoxLayout(self._data_frame)
        dv.setContentsMargins(8, 6, 8, 6)
        dv.setSpacing(4)

        # Header
        dh = QHBoxLayout()
        dh.setSpacing(8)
        _d_icon = QLabel("◈")
        _d_icon.setStyleSheet("color:#3a3a5a; font-size:11px;")
        dh.addWidget(_d_icon)
        _d_lbl = QLabel("GROTE BESTANDEN")
        _d_lbl.setStyleSheet(
            "color:#3a3a5a; font-size:9px; letter-spacing:3px;"
        )
        dh.addWidget(_d_lbl)
        _d_note = QLabel("— beelden gemaakt via DATA-tabblad, bestanden doorgaans niet beschikbaar")
        _d_note.setStyleSheet("color:#252530; font-size:9px;")
        dh.addWidget(_d_note)
        dh.addStretch()
        dv.addLayout(dh)

        # Horizontal scroll strip
        self._bf_scroll = QScrollArea()
        self._bf_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._bf_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._bf_scroll.setWidgetResizable(True)
        self._bf_scroll.setStyleSheet(
            "QScrollArea { border:none; background:transparent; }"
            "QScrollBar:horizontal { height:6px; background:#111; }"
            "QScrollBar::handle:horizontal { background:#2a2a3a; border-radius:3px; }"
        )
        self._bf_inner = QWidget()
        self._bf_inner.setStyleSheet("background:transparent;")
        self._bf_row = QHBoxLayout(self._bf_inner)
        self._bf_row.setContentsMargins(0, 0, 0, 0)
        self._bf_row.setSpacing(6)
        self._bf_row.addStretch()
        self._bf_scroll.setWidget(self._bf_inner)
        dv.addWidget(self._bf_scroll)

        self._bf_records: list = []   # bigfile records voor huidige acteur (cache)

        _, ch0_bf = self._films_zoom_size()
        self._bf_scroll.setFixedHeight(ch0_bf + 20)

        right.addWidget(self._data_frame)

        ch.addLayout(right, stretch=1)
        v.addWidget(content, stretch=1)

    def set_nav_info(self, idx: int, total: int):
        """Update prev/next buttons and position counter."""
        has_prev = (idx > 0)
        has_next = (idx < total - 1)
        self._btn_prev.setEnabled(has_prev)
        self._btn_prev.setStyleSheet(
            self._nav_style_on if has_prev else self._nav_style_off)
        self._btn_next.setEnabled(has_next)
        self._btn_next.setStyleSheet(
            self._nav_style_on if has_next else self._nav_style_off)
        if total > 0:
            self._lbl_nav.setText(f"{idx + 1} / {total}")
        else:
            self._lbl_nav.setText("")

    def load(self, data: dict):
        if self._frame_worker:
            self._frame_worker.stop()
            self._frame_worker = None
        self._data  = data
        self._actor = data.get('actor')
        meta        = data.get('meta', {})

        self.lbl_stem.setText(data.get('stem', ''))

        photo_path = data.get('photo_path', '')
        if photo_path and os.path.exists(photo_path):
            pix = QPixmap(photo_path).scaled(
                220, 284,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.lbl_photo.setPixmap(pix)
            self.lbl_photo.setText('')
        else:
            self.lbl_photo.setPixmap(QPixmap())
            self.lbl_photo.setText('?')

        self.inp_voornaam.setText(meta.get('voornaam', ''))
        self.inp_achternaam.setText(meta.get('achternaam', ''))
        self.inp_piek_start.setText(meta.get('piek_start', ''))
        self.inp_piek_eind.setText(meta.get('piek_eind', ''))
        self._reload_kleur_combo(meta.get('kleur', ''))
        self._set_combo(self.cmb_grootte, meta.get('grootte', ''))
        self._set_combo(self.cmb_rating,  meta.get('rating', ''))
        self._set_combo(self.cmb_dec,     meta.get('decennia', ''))

        # Laad traits
        actor_id = self._actor['id'] if self._actor else None
        active_traits = db.get_actor_trait_ids(actor_id) if actor_id else set()
        self._reload_traits(active_traits)

        self._refresh_films()
        self._markers_cat_filter  = set()   # reset filter for new actor
        self._markers_film_filter = ''
        self._refresh_markers()

    def _reload_kleur_combo(self, current_val: str):
        """Herbouw de kleur-combobox dynamisch vanuit de DB."""
        self.cmb_kleur.blockSignals(True)
        self.cmb_kleur.clear()
        self.cmb_kleur.addItem('—', '')
        for k in db.get_actor_kleuren():
            self.cmb_kleur.addItem(k['naam'], str(k['id']))
        self.cmb_kleur.blockSignals(False)
        self._set_combo(self.cmb_kleur, str(current_val) if current_val else '')

    def _reload_traits(self, active_ids: set):
        """Herbouw de trait-knoppen vanuit de DB.
        weergave 'positief' → alleen sterke kanten
        weergave 'negatief' → alleen zwakke kanten
        weergave 'beide'    → in beide secties (standaard)
        """
        self._traits_sterk_layout.clear_buttons()
        self._traits_zwak_layout.clear_buttons()
        self._trait_checks: dict = {}

        for tt in db.get_actor_trait_types():
            tid      = tt['id']
            checked  = tid in active_ids
            weergave = tt.get('type', 'beide')
            self._trait_checks[tid] = checked
            if weergave in ('positief', 'beide'):
                self._traits_sterk_layout.add_trait_btn(
                    tid, tt['naam'], checked, self._on_trait_toggled)
            if weergave in ('negatief', 'beide'):
                self._traits_zwak_layout.add_trait_btn(
                    tid, tt['naam'], checked, self._on_trait_toggled)

    def _on_trait_toggled(self, trait_id: int, checked: bool):
        self._trait_checks[trait_id] = checked

    def _open_film(self, item):
        f = item.data(Qt.ItemDataRole.UserRole)
        if f and f.get('file_path'):
            self.open_film_requested.emit(f['file_path'])

    # ── Films grid zoom / animation ──────────────

    def _films_zoom_size(self):
        w = max(self._FILMS_ZOOM_MIN,
                self._FILMS_CELL_W + self._films_zoom_level * self._FILMS_ZOOM_STEP)
        return w, w * 9 // 16

    def _films_zoom_in(self):
        self._films_zoom_level += 1
        db.set_setting('zoom_detail_films', str(self._films_zoom_level))
        self._films_apply_zoom()

    def _films_zoom_out(self):
        if (self._FILMS_CELL_W +
                (self._films_zoom_level - 1) * self._FILMS_ZOOM_STEP) >= self._FILMS_ZOOM_MIN:
            self._films_zoom_level -= 1
            db.set_setting('zoom_detail_films', str(self._films_zoom_level))
            self._films_apply_zoom()

    def _films_apply_zoom(self):
        cw, ch = self._films_zoom_size()
        self.films_list.setGridSize(QSize(cw, ch))
        for item in self._films_all_items:
            item.setSizeHint(QSize(cw, ch))
            d = item.data(Qt.ItemDataRole.UserRole)
            if d:
                d['cell_size'] = QSize(cw, ch)
                item.setData(Qt.ItemDataRole.UserRole, d)
        self.films_list.itemDelegate().invalidate_cache()
        self.films_list.update()
        # Pas ook de DATA-strip aan op het nieuwe zoomniveau
        self._rebuild_bf_strip()

    def _make_bf_cell(self, bf: dict, cw: int, ch: int) -> QFrame:
        """Maak een klikbare thumbnailcel voor een bigfile in de DATA-strip."""
        cell = QFrame()
        cell.setFixedSize(cw, ch)
        cell.setStyleSheet(
            "QFrame { background:#111; border-radius:4px; border:1px solid #1e1e1e; }"
        )
        v = QVBoxLayout(cell)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(1)

        # Thumbnail
        lbl_pix = QLabel()
        lbl_pix.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_pix.setStyleSheet("border:none; background:#0a0a0a; border-radius:3px;")
        lbl_pix.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tp = bf.get('thumbnail_path')
        if tp and Path(tp).exists():
            pix = QPixmap(tp).scaled(
                cw - 4, ch - 18,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            lbl_pix.setPixmap(pix)
        else:
            placeholder = QPixmap(cw - 4, ch - 18)
            placeholder.fill(QColor(18, 18, 18))
            _p = QPainter(placeholder)
            _p.setPen(QColor(50, 50, 50))
            _p.setFont(QFont("Segoe UI", max(10, (cw - 4) // 8)))
            _p.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, "▶")
            _p.end()
            lbl_pix.setPixmap(placeholder)
        v.addWidget(lbl_pix, stretch=1)

        # Filename label
        name = Path(bf['full_path']).name
        lbl_name = QLabel(name)
        lbl_name.setStyleSheet("color:#555; font-size:8px; border:none; background:transparent;")
        lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_name.setFixedHeight(14)
        lbl_name.setToolTip(bf['full_path'])
        v.addWidget(lbl_name)

        # Unavailable indicator
        available = Path(bf['full_path']).exists()
        if not available:
            cell.setStyleSheet(
                "QFrame { background:#0e0e0e; border-radius:4px;"
                " border:1px solid #1a1a1a; }"
            )

        # Double-click → play (only when available)
        if available:
            _path = bf['full_path']
            cell.mouseDoubleClickEvent = (
                lambda _e, p=_path: self.open_film_requested.emit(p)
            )
            cell.setCursor(Qt.CursorShape.PointingHandCursor)

        return cell

    def _rebuild_bf_strip(self):
        """Herbouw de bigfile-thumbnailstrip op basis van self._bf_records."""
        # Wis bestaande cellen (alles behalve de eindstretch)
        while self._bf_row.count() > 1:
            item = self._bf_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._bf_records:
            self._data_frame.setVisible(False)
            return

        cw, ch = self._films_zoom_size()
        self._bf_scroll.setFixedHeight(ch + 20)

        for bf in self._bf_records:
            cell = self._make_bf_cell(bf, cw, ch)
            self._bf_row.insertWidget(self._bf_row.count() - 1, cell)

        self._data_frame.setVisible(True)

    def _films_anim_tick(self):
        self._films_tick += 1
        self.films_list.itemDelegate().set_tick(self._films_tick)
        self.films_list.viewport().update()

    # ── Markers grid zoom ──────────────────────

    def _markers_zoom_size(self):
        w = max(self._MARKERS_ZOOM_MIN,
                self._MARKERS_CELL_W + self._markers_zoom_level * self._MARKERS_ZOOM_STEP)
        return w, w * 9 // 16

    def _markers_zoom_in(self):
        self._markers_zoom_level += 1
        db.set_setting('zoom_detail_markers', str(self._markers_zoom_level))
        self._markers_apply_zoom()

    def _markers_zoom_out(self):
        if (self._MARKERS_CELL_W +
                (self._markers_zoom_level - 1) * self._MARKERS_ZOOM_STEP) >= self._MARKERS_ZOOM_MIN:
            self._markers_zoom_level -= 1
            db.set_setting('zoom_detail_markers', str(self._markers_zoom_level))
            self._markers_apply_zoom()

    def _markers_apply_zoom(self):
        cw, ch = self._markers_zoom_size()
        self.markers_list.setGridSize(QSize(cw, ch))
        for item in self._markers_all_items:
            item.setSizeHint(QSize(cw, ch))
            d = item.data(Qt.ItemDataRole.UserRole)
            if d:
                d['cell_size'] = QSize(cw, ch)
                item.setData(Qt.ItemDataRole.UserRole, d)
        self.markers_list.itemDelegate().invalidate_cache()
        self.markers_list.update()

    def _jump_to_marker(self, item):
        d = item.data(Qt.ItemDataRole.UserRole)
        if d and d.get('film_path'):
            self.marker_jump_requested.emit(d['film_path'], float(d.get('time', 0)))

    def _refresh_films(self):
        self.films_list.clear()
        self._films_all_items.clear()
        self.films_list.itemDelegate().invalidate_cache()

        if not self._actor:
            return

        cw, ch = self._films_zoom_size()

        for f in db.get_films_for_actor(self._actor['id']):
            film_id = f.get('id')

            # All thumbnails for cycling animation
            if film_id:
                rows = db.get_film_thumbnails(film_id)
                thumbnails = [r['path'] for r in rows if os.path.exists(r['path'])]
            else:
                thumbnails = []
            if not thumbnails and f.get('thumbnail') and os.path.exists(f.get('thumbnail', '')):
                thumbnails = [f['thumbnail']]

            # File size from disk
            fp = f.get('file_path', '')
            size = 0
            if fp and os.path.exists(fp):
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    pass

            # Marker count (total and negative)
            markers     = 0
            neg_markers = 0
            if fp:
                _mp = Path(fp)
                _mf = _mp.parent / f".{_mp.stem}_markers.json"
                if _mf.exists():
                    try:
                        _ms = json.loads(_mf.read_text('utf-8'))
                        markers     = len(_ms)
                        neg_markers = sum(1 for m in _ms if m.get('negative'))
                    except Exception:
                        pass

            item = QListWidgetItem()
            item.setSizeHint(QSize(cw, ch))
            item.setToolTip(f.get('title', ''))
            item.setData(Qt.ItemDataRole.UserRole, {
                'path':        fp,
                'file_path':   fp,
                'name':        f.get('title', ''),
                'thumbnail':   f.get('thumbnail', ''),
                'thumbnails':  thumbnails,
                'film_id':     film_id,
                'size':        size,
                'date':        0,
                'markers':     markers,
                'neg_markers': neg_markers,
                'duration':    f.get('duration', 0) or 0,
                'cell_size':   QSize(cw, ch),
            })
            self.films_list.addItem(item)
            self._films_all_items.append(item)

        # ── Bigfile-thumbnailstrip ────────────────────────────────────────
        self._bf_records = db.get_bigfiles_for_actor(self._actor['id'])
        self._rebuild_bf_strip()

    def _refresh_markers(self):
        self.markers_list.clear()
        self._markers_all_items.clear()
        self.markers_list.itemDelegate().invalidate_cache()
        if self._frame_worker:
            self._frame_worker.stop()
            self._frame_worker = None

        if not self._actor:
            return

        actor_id   = self._actor['id']
        cat_cache:  dict = {}   # cid -> QPixmap | None
        cats_info:  dict = {}   # cid -> full cat dict (for filter buttons)
        tasks: list = []

        thumb_dir = MARKER_THUMBS_DIR
        thumb_dir.mkdir(parents=True, exist_ok=True)

        cw, ch = self._markers_zoom_size()

        for film in db.get_films_for_actor(actor_id):
            for m in self._load_markers(film['file_path']):
                if actor_id not in (m.get('actors') or []):
                    continue

                time_val = m.get('time', 0)
                s = int(time_val)
                time_str = f"{s // 60:02d}:{s % 60:02d}"

                # Category pixmaps + info
                cat_ids  = m.get('categories') or []
                cat_pixs = []
                for cid in cat_ids:
                    if cid not in cat_cache:
                        db_cats = db.get_categories_by_ids([cid])
                        if db_cats:
                            cats_info[cid] = db_cats[0]
                            ip = db_cats[0].get('icon_path', '')
                        else:
                            ip = ''
                        if ip and os.path.exists(ip):
                            p = QPixmap(ip)
                            cat_cache[cid] = p if not p.isNull() else None
                        else:
                            cat_cache[cid] = None
                    if cat_cache[cid]:
                        cat_pixs.append(cat_cache[cid])

                # Frame cache path — filename encodes resolution so old low-res
                # files are automatically bypassed when the target size changes
                time_ms    = int(time_val * 1000)
                cache_name = (f"{Path(film['file_path']).stem}_{time_ms}"
                              f"_w{FrameExtractWorker.THUMB_W}.jpg")
                cache_path = str(thumb_dir / cache_name)

                row_idx = len(self._markers_all_items)
                item = QListWidgetItem()
                item.setSizeHint(QSize(cw, ch))
                item.setData(Qt.ItemDataRole.UserRole, {
                    'film_path':   film['file_path'],
                    'time':        time_val,
                    'time_str':    time_str,
                    'cache_path':  cache_path,
                    'cat_pixmaps': cat_pixs,
                    'cat_ids':     cat_ids,    # needed for filter logic
                    'cell_size':   QSize(cw, ch),
                })
                self.markers_list.addItem(item)
                self._markers_all_items.append(item)

                if not os.path.exists(cache_path):
                    tasks.append((row_idx, film['file_path'], time_val, cache_path))

        # Rebuild category filter buttons and apply current filter
        self._rebuild_cat_filter_buttons(list(cats_info.values()))
        self._markers_apply_cat_filter()

        # Start background extraction for missing frames
        if tasks:
            self._frame_worker = FrameExtractWorker(tasks)
            self._frame_worker.frame_ready.connect(self._on_frame_ready)
            self._frame_worker.start()

    def _on_frame_ready(self, row_idx: int, cache_path: str):
        # Delegate's _get_pix only caches successes, so just refresh the
        # viewport — next repaint will pick up the newly extracted file.
        self.markers_list.viewport().update()

    # ── Category filter ───────────────────────────

    def _rebuild_cat_filter_buttons(self, cats: list):
        """Rebuild the category filter row with toggle buttons for each category."""
        layout = self._cat_filter_row.layout()
        # Remove all existing widgets (leave the trailing stretch)
        while layout.count():
            child = layout.takeAt(0)
            w = child.widget()
            if w:
                w.deleteLater()

        if not cats:
            self._cat_filter_row.setVisible(False)
            return

        self._cat_filter_row.setVisible(True)

        for cat in sorted(cats, key=lambda c: c.get('name', '').lower()):
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.setCheckable(True)
            btn.setChecked(cat['id'] in self._markers_cat_filter)
            btn.setToolTip(cat.get('name', ''))
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

            ip = cat.get('icon_path', '')
            if ip and os.path.exists(ip):
                btn.setIcon(QIcon(QPixmap(ip)))
                btn.setIconSize(QSize(20, 20))
            else:
                btn.setText(cat.get('name', '?')[:2])

            cid = cat['id']
            btn.toggled.connect(lambda checked, c=cid: self._markers_toggle_cat(c, checked))
            btn.setStyleSheet(
                "QPushButton { background: #111; border: 1px solid #252525;"
                "  border-radius: 3px; padding: 1px; }"
                "QPushButton:checked { border: 2px solid #e8b86d; background: #1a1400; }"
                "QPushButton:hover { border-color: #555; }"
                "QPushButton:checked:hover { border-color: #f0ca8a; }"
            )
            layout.addWidget(btn)

        layout.addStretch()

    def _markers_toggle_cat(self, cat_id: int, checked: bool):
        if checked:
            self._markers_cat_filter.add(cat_id)
        else:
            self._markers_cat_filter.discard(cat_id)
        self._markers_apply_cat_filter()

    def _on_film_filter_clicked(self, item):
        """Toggle film filter on single click; click same film again to show all."""
        d  = item.data(Qt.ItemDataRole.UserRole)
        fp = d.get('file_path', '') if d else ''
        if self._markers_film_filter == fp:
            # Already active → deselect and show all
            self._markers_film_filter = ''
            self.films_list.clearSelection()
        else:
            self._markers_film_filter = fp
        self._apply_markers_filter()

    def _markers_apply_cat_filter(self):
        self._apply_markers_filter()

    def _apply_markers_filter(self):
        """Show/hide marker items based on the active film + category filter."""
        for item in self._markers_all_items:
            d         = item.data(Qt.ItemDataRole.UserRole)
            item_cats = set(d.get('cat_ids', []) if d else [])
            item_fp   = d.get('film_path', '') if d else ''

            # Film filter
            if self._markers_film_filter and item_fp != self._markers_film_filter:
                item.setHidden(True)
                continue

            # Category filter
            if self._markers_cat_filter and not (item_cats & self._markers_cat_filter):
                item.setHidden(True)
                continue

            item.setHidden(False)

    @staticmethod
    def _load_markers(video_path: str) -> list:
        p = Path(video_path)
        mf = p.parent / f".{p.stem}_markers.json"
        if mf.exists():
            try:
                with open(str(mf), 'r') as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    @staticmethod
    def _save_markers(video_path: str, markers: list):
        p = Path(video_path)
        mf = p.parent / f".{p.stem}_markers.json"
        try:
            with open(str(mf), 'w') as f:
                json.dump(markers, f, indent=2)
        except Exception:
            pass

    def _remove_actor_from_marker(self, data: dict):
        """Remove the current actor from the marker described by `data`."""
        if not self._actor:
            return
        film_path = data.get('film_path', '')
        time_val  = data.get('time')
        if not film_path or time_val is None:
            return
        actor_id = self._actor['id']
        markers  = self._load_markers(film_path)
        changed  = False
        for m in markers:
            if abs(m.get('time', -1) - time_val) < 0.01:
                actors = list(m.get('actors') or [])
                if actor_id in actors:
                    actors.remove(actor_id)
                    m['actors'] = actors
                    changed = True
        if changed:
            self._save_markers(film_path, markers)
            self._refresh_markers()

    def _set_combo(self, combo: QComboBox, value: str):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _get_meta(self) -> dict:
        meta = {}
        for field, w in [('voornaam', self.inp_voornaam), ('achternaam', self.inp_achternaam),
                         ('piek_start', self.inp_piek_start), ('piek_eind', self.inp_piek_eind)]:
            val = w.text().strip()
            if val:
                meta[field] = val
        for field, c in [('kleur', self.cmb_kleur), ('grootte', self.cmb_grootte),
                         ('rating', self.cmb_rating), ('decennia', self.cmb_dec)]:
            val = c.currentData()
            if val:
                meta[field] = val
        return meta

    def _save(self):
        meta = self._get_meta()
        stem = self._data.get('stem', '')
        if self._actor:
            actor_id = self._actor['id']
        else:
            actor_id = db.create_actor(stem)
        db.update_actor_meta(actor_id, meta)
        # Traits opslaan
        active_trait_ids = [tid for tid, on in getattr(self, '_trait_checks', {}).items() if on]
        db.set_actor_traits(actor_id, active_trait_ids)
        self.saved.emit()
        self.back_requested.emit()


# ─────────────────────────────────────────────
#  Naam-splitter: kaart per acteur zonder voornaam
# ─────────────────────────────────────────────

class _SplitCard(QFrame):
    """Compacte kaart: foto links, tekstveld rechts.

    Gebruiker klikt in het tekstveld (fotobestandsnaamstem), plaatst de cursor
    op de scheidingspositie en drukt Enter.  Alles vóór de cursor wordt
    voornaam, alles erna tot een eventuele extensie wordt achternaam.
    """

    split_done = pyqtSignal(int, str, str)   # actor_id, voornaam, achternaam

    _PHOTO_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
    _PH_W, _PH_H = 55, 75

    def __init__(self, actor: dict, meta: dict, foto_folder: str):
        super().__init__()
        self._actor_id   = actor['id']
        self._stem       = actor.get('name', '')
        self._foto_folder = foto_folder
        self._build()

    def _build(self):
        self.setFixedWidth(230)
        self.setStyleSheet(
            "QFrame { background:#111; border:1px solid #1e1e1e; border-radius:5px; }"
        )

        h = QHBoxLayout(self)
        h.setContentsMargins(5, 5, 5, 5)
        h.setSpacing(6)

        # ── Foto ─────────────────────────────────────────────────────────
        photo_lbl = QLabel()
        photo_lbl.setFixedSize(self._PH_W, self._PH_H)
        photo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        photo_lbl.setStyleSheet(
            "border:none; background:#0a0a0a; border-radius:3px;"
        )
        if self._foto_folder:
            for ext in self._PHOTO_EXTS:
                fp = Path(self._foto_folder) / f"{self._stem}{ext}"
                if fp.exists():
                    pix = QPixmap(str(fp)).scaled(
                        self._PH_W, self._PH_H,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    photo_lbl.setPixmap(pix)
                    break
        h.addWidget(photo_lbl)

        # ── Rechts: naam-label + invoer ───────────────────────────────────
        v = QVBoxLayout()
        v.setSpacing(4)
        v.setContentsMargins(0, 0, 0, 0)

        lbl_stem = QLabel(self._stem)
        lbl_stem.setStyleSheet("color:#383838; font-size:8px; border:none;")
        lbl_stem.setWordWrap(True)
        v.addWidget(lbl_stem)

        self._inp = QLineEdit(self._stem)
        self._inp.setStyleSheet(
            "QLineEdit { background:#181818; border:1px solid #2a2a2a; "
            "border-radius:3px; color:#ccc; font-size:11px; padding:2px 4px; }"
            "QLineEdit:focus { border-color:#e8b86d; }"
        )
        self._inp.setToolTip("Cursor op de scheidingspositie zetten, dan Enter")
        self._inp.returnPressed.connect(self._on_enter)
        v.addWidget(self._inp)

        lbl_hint = QLabel("cursor ▏ Enter")
        lbl_hint.setStyleSheet("color:#252525; font-size:8px; border:none;")
        v.addWidget(lbl_hint)

        v.addStretch()
        h.addLayout(v, stretch=1)

    def _on_enter(self):
        text = self._inp.text()
        pos  = self._inp.cursorPosition()
        voornaam   = text[:pos].strip()
        achternaam = text[pos:].strip()

        # Verwijder extensie als die per ongeluk in achternaam zit
        for ext in self._PHOTO_EXTS:
            if achternaam.lower().endswith(ext):
                achternaam = achternaam[:-len(ext)].strip()
                break

        if not voornaam:
            # Cursor staat helemaal links — flash rood en doe niets
            ok_style = self._inp.styleSheet()
            self._inp.setStyleSheet(
                "QLineEdit { background:#2a1010; border:1px solid #cc3333; "
                "border-radius:3px; color:#ccc; font-size:11px; padding:2px 4px; }"
            )
            QTimer.singleShot(500, lambda: self._inp.setStyleSheet(ok_style))
            return

        self.split_done.emit(self._actor_id, voornaam, achternaam)


# ─────────────────────────────────────────────
#  Naam-splitter paneel  (stack page 2)
# ─────────────────────────────────────────────

class _SplitNaamPanel(QWidget):
    """Overzicht van alle acteurs zonder voornaam — naam splitsen via bestandsnaam."""

    back_requested = pyqtSignal()
    split_done     = pyqtSignal()   # één acteur gesplitst → ververs hoofdgrid

    COLS = 4

    def __init__(self):
        super().__init__()
        self._foto_folder: str = ''
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(42)
        bar.setStyleSheet(
            "QFrame { background:#0d0d0d; border-bottom:1px solid #1e1e1e; }"
        )
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(10, 0, 10, 0)
        bh.setSpacing(10)

        btn_back = QPushButton("◀  Terug")
        btn_back.setFixedHeight(28)
        btn_back.clicked.connect(self.back_requested)
        bh.addWidget(btn_back)

        lbl_title = QLabel("NAAM SPLITSEN")
        lbl_title.setStyleSheet(
            "color:#666; font-size:10px; letter-spacing:3px;"
        )
        bh.addWidget(lbl_title)

        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet("color:#333; font-size:10px;")
        bh.addWidget(self._lbl_count)

        bh.addStretch()

        lbl_hint = QLabel(
            "klik in tekstveld  •  cursor op scheidingspositie  •  Enter = splitsen"
        )
        lbl_hint.setStyleSheet("color:#252525; font-size:9px;")
        bh.addWidget(lbl_hint)

        v.addWidget(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none; background:#0a0a0a;")

        self._inner = QWidget()
        self._inner.setStyleSheet("background:#0a0a0a;")
        self._grid_layout = QGridLayout(self._inner)
        self._grid_layout.setContentsMargins(12, 12, 12, 12)
        self._grid_layout.setSpacing(10)
        self._grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        scroll.setWidget(self._inner)
        v.addWidget(scroll, stretch=1)

    # ── Data ─────────────────────────────────────────────────────────────

    def refresh(self, foto_folder: str):
        """Herlaad alle acteurs zonder voornaam direct uit de DB."""
        self._foto_folder = foto_folder

        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Haal acteurs zonder voornaam op
        no_naam = []
        for actor in db.get_all_actors():
            meta = {}
            if actor.get('notes'):
                try:
                    meta = json.loads(actor['notes'])
                except (ValueError, TypeError):
                    meta = {}
            if not meta.get('voornaam', '').strip():
                no_naam.append((actor, meta))
        no_naam.sort(key=lambda t: t[0].get('name', '').lower())

        n = len(no_naam)
        self._lbl_count.setText(
            f"{n} acteur{'s' if n != 1 else ''} zonder voornaam"
        )

        for i, (actor, meta) in enumerate(no_naam):
            card = _SplitCard(actor, meta, self._foto_folder)
            card.split_done.connect(self._on_split)
            row, col = divmod(i, self.COLS)
            self._grid_layout.addWidget(card, row, col)

    def _on_split(self, actor_id: int, voornaam: str, achternaam: str):
        actor = db.get_actor(actor_id)
        meta  = {}
        if actor and actor.get('notes'):
            try:
                meta = json.loads(actor['notes'])
            except (ValueError, TypeError):
                meta = {}
        meta['voornaam']   = voornaam
        meta['achternaam'] = achternaam
        db.update_actor_meta(actor_id, meta)

        self.split_done.emit()          # → ActorsPanel vernieuwt _all_items
        self.refresh(self._foto_folder) # kaart verdwijnt direct


# ─────────────────────────────────────────────
#  Main Actors Panel — full-screen photo grid
# ─────────────────────────────────────────────

class ActorsPanel(QWidget):
    open_film_requested = pyqtSignal(str)
    scene_jump_requested = pyqtSignal(str, float)

    PHOTO_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.gif'}
    ZOOM_STEP_W  = 20   # px per zoom level
    ZOOM_MIN_W   = 40   # never narrower than this
    ZOOM_DEFAULT_LEVEL = 0  # level 0 = 160px wide

    def __init__(self, player):
        super().__init__()
        self.player = player
        self._all_items: list = []
        self._current_detail_stem: str = ''
        self._zoom_level = int(
            db.get_setting('zoom_actors_panel', str(self.ZOOM_DEFAULT_LEVEL))
            or str(self.ZOOM_DEFAULT_LEVEL)
        )
        self._mode = 'in_db'
        self._cb_db: dict = {}
        self._cb_kleur: dict = {}
        self._cb_grootte: dict = {}
        self._cb_rating: dict = {}
        self._cb_dec: dict = {}
        self._sort_key: str = ''
        self._sort_reverse: bool = False
        self._sort_btns: dict = {}
        self._last_foto_mtime: float = 0.0   # voor auto_link throttle
        self._build_ui()
        folder = db.get_setting('photo_folder', '')
        if folder:
            self._update_folder_label(folder)
            self._scan_folder(folder)

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._stack = QStackedWidget()
        v.addWidget(self._stack)

        # ── Page 0: photo grid ────────────────────
        page0 = QWidget()
        v0 = QVBoxLayout(page0)
        v0.setContentsMargins(0, 0, 0, 0)
        v0.setSpacing(0)

        # ── Tab toolbar (lives in the main window corner) ──
        self.tab_toolbar = QWidget()
        self.tab_toolbar.setStyleSheet("background: transparent;")
        tb = QHBoxLayout(self.tab_toolbar)
        tb.setContentsMargins(0, 2, 0, 2)
        tb.setSpacing(6)

        self.lbl_folder = QLabel("—")
        self.lbl_folder.setStyleSheet("color: #333; font-size: 10px;")
        self.lbl_folder.setMaximumWidth(110)
        tb.addWidget(self.lbl_folder)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Zoeken...")
        self.search_input.setFixedWidth(160)
        self.search_input.textChanged.connect(self._apply_filters)
        tb.addWidget(self.search_input)

        self._btn_mode = QPushButton("BUITEN DB")
        self._btn_mode.setCheckable(True)
        self._btn_mode.setFixedHeight(28)
        self._btn_mode.setStyleSheet(
            "QPushButton { background: #0a0a0a; border: 1px solid #2a2a2a;"
            "  border-radius: 4px; color: #444; font-size: 10px; padding: 0 8px; }"
            "QPushButton:checked { background: #1a1a3a; border-color: #5555cc;"
            "  color: #8888ff; }"
            "QPushButton:hover { border-color: #555; color: #777; }"
            "QPushButton:checked:hover { border-color: #8888ff; }"
        )
        self._btn_mode.toggled.connect(self._toggle_mode)
        tb.addWidget(self._btn_mode)

        self._btn_split_naam = QPushButton("✂  NAAM")
        self._btn_split_naam.setFixedHeight(28)
        self._btn_split_naam.setToolTip(
            "Toon alle acteurs zonder voornaam\n"
            "Bestandsnaamstem splitsen via cursor + Enter"
        )
        self._btn_split_naam.setStyleSheet(
            "QPushButton { background: #0a0a0a; border: 1px solid #2a2a2a;"
            "  border-radius: 4px; color: #444; font-size: 10px; padding: 0 8px; }"
            "QPushButton:hover { border-color: #555; color: #777; }"
        )
        self._btn_split_naam.clicked.connect(self._open_split_panel)
        tb.addWidget(self._btn_split_naam)

        btn_folder = QPushButton("📁  Map")
        btn_folder.setFixedHeight(28)
        btn_folder.clicked.connect(self._pick_folder)
        tb.addWidget(btn_folder)

        btn_import = QPushButton("⬆  Import")
        btn_import.setFixedHeight(28)
        btn_import.clicked.connect(self._import_actors)
        tb.addWidget(btn_import)

        btn_zoom_out = QPushButton("−")
        btn_zoom_out.setFixedSize(28, 28)
        btn_zoom_out.setAutoRepeat(True)
        btn_zoom_out.setAutoRepeatDelay(400)
        btn_zoom_out.setAutoRepeatInterval(100)
        btn_zoom_out.clicked.connect(self._zoom_out)
        tb.addWidget(btn_zoom_out)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setFixedSize(28, 28)
        btn_zoom_in.setAutoRepeat(True)
        btn_zoom_in.setAutoRepeatDelay(400)
        btn_zoom_in.setAutoRepeatInterval(100)
        btn_zoom_in.clicked.connect(self._zoom_in)
        tb.addWidget(btn_zoom_in)
        # tab_toolbar is NOT added to v0; player.py inserts it into the corner

        # Filter — 1 rij checkboxes
        filter_frame = QFrame()
        filter_frame.setStyleSheet(
            "QFrame { background: #0a0a0a; border-bottom: 1px solid #161616; }"
            "QCheckBox { color: #777; font-size: 10px; spacing: 3px; }"
            "QCheckBox::indicator { width: 11px; height: 11px; }"
            "QCheckBox:checked { color: #e8b86d; }"
        )
        fv = QVBoxLayout(filter_frame)
        fv.setContentsMargins(12, 4, 12, 4)
        fv.setSpacing(0)

        row_f = QHBoxLayout()
        row_f.setSpacing(10)

        # DB group wrapped so it can be hidden in "buiten db" mode
        self._db_group_widget = QWidget()
        self._db_group_widget.setStyleSheet("background: transparent;")
        _dbh = QHBoxLayout(self._db_group_widget)
        _dbh.setContentsMargins(0, 0, 0, 0)
        _dbh.setSpacing(6)
        self._cb_db = self._cb_group(_dbh, "DB:",
            [("in_db", "✓"), ("not_in_db", "✗")])
        row_f.addWidget(self._db_group_widget)

        self._cb_kleur = self._cb_group(row_f, "Kleur:",
            [("1", "Wit"), ("2", "Zwart"), ("3", "Bruin")])

        self._cb_rating = self._cb_group(row_f, "Rating:",
            [("9", "9"), ("8", "8"), ("7", "7"), ("6", "6"), ("5", "5")])

        self._cb_grootte = self._cb_group(row_f, "Grootte:",
            [(str(i), str(i)) for i in range(1, 6)])

        self._cb_dec = self._cb_group(row_f, "Dec:",
            [("7", "70"), ("8", "80"), ("9", "90"), ("0", "00")])

        btn_reset = QPushButton("✕")
        btn_reset.setFixedSize(22, 22)
        btn_reset.setToolTip("Reset filters")
        btn_reset.clicked.connect(self._reset_filters)
        row_f.addStretch()
        row_f.addWidget(btn_reset)

        fv.addLayout(row_f)

        # Sorteer-rij
        row_s = QHBoxLayout()
        row_s.setSpacing(5)

        lbl_sort = QLabel("Sorteer:")
        lbl_sort.setStyleSheet("color: #444; font-size: 9px;")
        row_s.addWidget(lbl_sort)

        _sort_defs = [
            ('decennia', 'Decennia'),
            ('grootte',  'Grootte'),
            ('kleur',    'Kleur'),
            ('markers',  'Markers'),
            ('films',    'Films'),
        ]
        for key, label in _sort_defs:
            btn = QPushButton(label)
            btn.setFixedHeight(20)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet(self._sort_btn_style(False, False))
            btn.clicked.connect(lambda _, k=key: self._sort_by(k))
            row_s.addWidget(btn)
            self._sort_btns[key] = btn

        row_s.addStretch()

        btn_sort_reset = QPushButton("↺")
        btn_sort_reset.setFixedSize(22, 20)
        btn_sort_reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_sort_reset.setToolTip("Volgorde resetten")
        btn_sort_reset.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #252525;"
            "  border-radius: 3px; color: #444; font-size: 11px; }"
            "QPushButton:hover { border-color: #e8b86d; color: #e8b86d; }"
        )
        btn_sort_reset.clicked.connect(self._reset_sort)
        row_s.addWidget(btn_sort_reset)

        fv.addLayout(row_s)
        v0.addWidget(filter_frame)

        # Photo grid
        cw, ch = self._zoom_size()
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(1, 1))
        self.grid.setGridSize(QSize(cw, ch))
        self.grid.setSpacing(0)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setStyleSheet(
            "QListWidget { background: #0a0a0a; border: none; padding: 0px; }"
            "QListWidget::item { border: none; background: transparent; padding: 0px; margin: 0px; }"
        )
        self._delegate = ActorCardDelegate()
        self.grid.setItemDelegate(self._delegate)
        self.grid.itemClicked.connect(self._on_item_clicked)
        self.grid.itemActivated.connect(self._on_item_enter)
        self._delegate.detail_requested.connect(self._open_detail)
        v0.addWidget(self.grid)

        self._stack.addWidget(page0)

        # ── Page 1: detail view ───────────────────
        self._detail_view = ActorDetailView()
        self._detail_view.back_requested.connect(self._on_detail_back)
        self._detail_view.saved.connect(self._on_detail_saved)
        self._detail_view.open_film_requested.connect(self._on_detail_open_film)
        self._detail_view.marker_jump_requested.connect(self.scene_jump_requested)
        self._detail_view.navigate_requested.connect(self._navigate_actor)
        self._stack.addWidget(self._detail_view)

        # ── Page 2: naam-splitter ─────────────────
        self._split_naam_panel = _SplitNaamPanel()
        self._split_naam_panel.back_requested.connect(self._on_split_naam_back)
        self._split_naam_panel.split_done.connect(self._on_split_naam_done)
        self._stack.addWidget(self._split_naam_panel)

    def _cb_group(self, layout: QHBoxLayout, label: str,
                  options: list) -> dict:
        from PyQt6.QtWidgets import QCheckBox
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #444; font-size: 9px;")
        layout.addWidget(lbl)
        cbs = {}
        for val, text in options:
            cb = QCheckBox(text)
            cb.stateChanged.connect(self._on_filter_changed)
            layout.addWidget(cb)
            cbs[val] = cb
        return cbs

    # ── Folder ───────────────────────────────────

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecteer foto-map")
        if folder:
            db.set_setting('photo_folder', folder)
            self._update_folder_label(folder)
            self._scan_folder(folder)

    def _update_folder_label(self, folder):
        self.lbl_folder.setText(Path(folder).name or folder)
        self.lbl_folder.setToolTip(folder)

    # ── Scan ─────────────────────────────────────

    def _auto_link_if_changed(self, folder: str):
        """Roep auto_link_actor_photos alleen aan als de fotomap veranderd is
        sinds de laatste scan. Voorkomt onnodige DB-writes bij elke refresh."""
        from paths import ACTEURFOTOS_DIR
        try:
            mtime = ACTEURFOTOS_DIR.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._last_foto_mtime:
            db.auto_link_actor_photos()
            self._last_foto_mtime = mtime

    def _scan_folder(self, folder):
        # Nieuwe foto's oppikken — alleen als de map daadwerkelijk veranderd is
        self._auto_link_if_changed(folder)

        self.grid.clear()
        self._all_items.clear()
        self._delegate._cache.clear()

        folder_path = Path(folder)
        if not folder_path.exists():
            return

        photos = sorted(
            (f for f in folder_path.iterdir() if f.suffix.lower() in self.PHOTO_EXTS),
            key=lambda f: f.stem.lower()
        )

        for photo_path in photos:
            actor = db.get_actor_by_name(photo_path.stem)
            meta = {}
            if actor and actor.get('notes'):
                try:
                    meta = json.loads(actor['notes'])
                except (ValueError, TypeError):
                    meta = {}
            # in_db = heeft zinvolle metadata (voornaam of achternaam ingevuld)
            in_db = bool(meta.get('voornaam') or meta.get('achternaam'))

            films       = db.get_films_for_actor(actor['id']) if actor else []
            film_count  = len(films)
            marker_count = _count_actor_markers(actor['id'], films) if actor else 0

            cw, ch = self._zoom_size()
            item = QListWidgetItem()
            item.setSizeHint(QSize(cw, ch))
            item.setData(Qt.ItemDataRole.UserRole, {
                'photo_path': str(photo_path),
                'stem': photo_path.stem,
                'actor': actor,
                'in_db': in_db,
                'meta': meta,
                'cell_size': QSize(cw, ch),
                'film_count': film_count,
                'marker_count': marker_count,
            })
            self.grid.addItem(item)
            self._all_items.append(item)

        self._apply_sort()   # past ook _apply_filters() toe achteraf

    def _toggle_mode(self, checked: bool):
        self._mode = 'buiten_db' if checked else 'in_db'
        self._db_group_widget.setVisible(not checked)
        self._reset_filters()

    # ── Naam-splitter ─────────────────────────────────────────────────────

    def _open_split_panel(self):
        """Open het naam-splitter paneel (page 2)."""
        foto_folder = db.get_setting('photo_folder', '')
        self._split_naam_panel.refresh(foto_folder)
        self._stack.setCurrentIndex(2)

    def _on_split_naam_back(self):
        """Terug naar het hoofdgrid (page 0)."""
        self._stack.setCurrentIndex(0)

    def _on_split_naam_done(self):
        """Één acteur is gesplitst — ververs het hoofdgrid op de achtergrond."""
        folder = db.get_setting('photo_folder', '')
        if folder:
            self._scan_folder(folder)

    # ─────────────────────────────────────────────────────────────────────

    def _on_filter_changed(self):
        if self._mode == 'buiten_db':
            sender = self.sender()
            if sender and sender.isChecked():
                # Radio behaviour: uncheck all other filter checkboxes
                for group in (self._cb_kleur, self._cb_grootte,
                               self._cb_rating, self._cb_dec):
                    for cb in group.values():
                        if cb is not sender:
                            cb.blockSignals(True)
                            cb.setChecked(False)
                            cb.blockSignals(False)
        self._apply_filters()

    def _buiten_db_active(self):
        """Return (category_key, value) of the single selected filter, or None."""
        for cat_key, group in [
            ('kleur',    self._cb_kleur),
            ('grootte',  self._cb_grootte),
            ('rating',   self._cb_rating),
            ('decennia', self._cb_dec),
        ]:
            for val, cb in group.items():
                if cb.isChecked():
                    return cat_key, val
        return None

    def _reset_filters(self):
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        for group in (self._cb_db, self._cb_kleur, self._cb_grootte,
                      self._cb_rating, self._cb_dec):
            for cb in group.values():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
        self._apply_filters()

    @staticmethod
    def _active(cb_group: dict) -> set:
        return {val for val, cb in cb_group.items() if cb.isChecked()}

    def _apply_filters(self):
        if self._mode == 'buiten_db':
            self._apply_filters_buiten_db()
            return

        query     = self.search_input.text().lower()
        act_db    = self._active(self._cb_db)
        act_kleur = self._active(self._cb_kleur)
        act_groo  = self._active(self._cb_grootte)
        act_rat   = self._active(self._cb_rating)
        act_dec   = self._active(self._cb_dec)

        for item in self._all_items:
            data  = item.data(Qt.ItemDataRole.UserRole)
            meta  = data.get('meta', {})
            in_db = data.get('in_db', False)
            stem  = data.get('stem', '').lower()

            hide = False

            if query:
                name_match = (
                    query in stem or
                    query in meta.get('voornaam', '').lower() or
                    query in meta.get('achternaam', '').lower()
                )
                if not name_match:
                    hide = True

            if not hide and act_db:
                db_val = 'in_db' if in_db else 'not_in_db'
                if db_val not in act_db:
                    hide = True

            if not hide and act_kleur and str(meta.get('kleur', '')) not in act_kleur:
                hide = True
            if not hide and act_groo and str(meta.get('grootte', '')) not in act_groo:
                hide = True
            if not hide and act_rat and str(meta.get('rating', '')) not in act_rat:
                hide = True
            if not hide and act_dec and str(meta.get('decennia', '')) not in act_dec:
                hide = True

            item.setHidden(hide)

    def _apply_filters_buiten_db(self):
        active = self._buiten_db_active()
        query  = self.search_input.text().lower()
        for item in self._all_items:
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                item.setHidden(True)
                continue
            meta = data.get('meta', {})
            stem = data.get('stem', '').lower()
            hide = False
            if query:
                name_match = (
                    query in stem or
                    query in meta.get('voornaam', '').lower() or
                    query in meta.get('achternaam', '').lower()
                )
                if not name_match:
                    hide = True
            if not hide and active:
                cat_key, _ = active
                # Show only actors that DON'T have this field set yet
                if meta.get(cat_key, ''):
                    hide = True
            item.setHidden(hide)

    # ── Sorteren ─────────────────────────────────

    @staticmethod
    def _sort_btn_style(active: bool, reverse: bool) -> str:
        if active:
            arrow = ' ↓' if reverse else ' ↑'
            return (
                f"QPushButton {{ background: #1a1500; border: 1px solid #e8b86d;"
                f"  border-radius: 3px; color: #e8b86d; font-size: 9px;"
                f"  padding: 0 6px; }}"
                f"QPushButton:hover {{ background: #2a2200; }}"
            )
        return (
            "QPushButton { background: transparent; border: 1px solid #252525;"
            "  border-radius: 3px; color: #555; font-size: 9px; padding: 0 6px; }"
            "QPushButton:hover { border-color: #666; color: #999; }"
        )

    def _sort_by(self, key: str):
        if self._sort_key == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = key
            self._sort_reverse = True   # eerste klik altijd hoog → laag
        self._apply_sort()
        self._update_sort_buttons()

    def _reset_sort(self):
        self._sort_key = ''
        self._sort_reverse = False
        self._apply_sort()
        self._update_sort_buttons()

    def _update_sort_buttons(self):
        for key, btn in self._sort_btns.items():
            active = (key == self._sort_key)
            label = {'decennia': 'Decennia', 'grootte': 'Grootte',
                     'kleur': 'Kleur', 'markers': 'Markers',
                     'films': 'Films'}[key]
            arrow = (' ↓' if self._sort_reverse else ' ↑') if active else ''
            btn.setText(label + arrow)
            btn.setStyleSheet(self._sort_btn_style(active, self._sort_reverse))

    def _item_sort_key(self, item):
        d = item.data(Qt.ItemDataRole.UserRole) or {}
        meta = d.get('meta', {})
        key = self._sort_key

        if key == 'decennia':
            v = str(meta.get('decennia', ''))
            return (0 if v else 1, v)
        if key == 'grootte':
            v = meta.get('grootte', '')
            try:
                return (0 if v else 1, int(v))
            except (ValueError, TypeError):
                return (1, 0)
        if key == 'kleur':
            v = str(meta.get('kleur', ''))
            return (0 if v else 1, v)
        if key == 'markers':
            return (0, -(d.get('marker_count', 0)))
        if key == 'films':
            return (0, -(d.get('film_count', 0)))
        # Fallback: alfabetisch op naam
        return (0, d.get('stem', '').lower())

    def _apply_sort(self):
        n = self.grid.count()
        if n == 0:
            return
        # takeItem verwijdert het item uit de widget maar houdt het in Python geldig
        items = [self.grid.takeItem(0) for _ in range(n)]

        if self._sort_key:
            items.sort(key=self._item_sort_key, reverse=self._sort_reverse)
        else:
            # Standaard: rating ↓, dan aantal films ↓, dan aantal markers ↓, dan naam ↑
            def _default_key(it):
                d = it.data(Qt.ItemDataRole.UserRole) or {}
                meta = d.get('meta', {})
                rat_s = str(meta.get('rating', '') or '')
                try:
                    rat = -int(rat_s)       # negatief → hoog eerst
                except (ValueError, TypeError):
                    rat = 1                 # geen rating → achteraan
                return (rat,
                        -d.get('film_count',   0),
                        -d.get('marker_count', 0),
                        d.get('stem', '').lower())
            items.sort(key=_default_key)

        self._all_items = items
        for item in items:
            self.grid.addItem(item)

        # Filter opnieuw toepassen zodat verborgen items verborgen blijven
        self._apply_filters()

    # ── Zoom ─────────────────────────────────────

    def _zoom_size(self):
        w = max(self.ZOOM_MIN_W, 160 + self._zoom_level * self.ZOOM_STEP_W)
        h = int(w * 1.29)
        return w, h

    def _zoom_in(self):
        self._zoom_level += 1
        db.set_setting('zoom_actors_panel', str(self._zoom_level))
        self._apply_zoom()

    def _zoom_out(self):
        if 160 + (self._zoom_level - 1) * self.ZOOM_STEP_W >= self.ZOOM_MIN_W:
            self._zoom_level -= 1
            db.set_setting('zoom_actors_panel', str(self._zoom_level))
            self._apply_zoom()

    def _apply_zoom(self):
        cw, ch = self._zoom_size()
        self.grid.setGridSize(QSize(cw, ch))
        for item in self._all_items:
            item.setSizeHint(QSize(cw, ch))
            d = item.data(Qt.ItemDataRole.UserRole)
            if d:
                d['cell_size'] = QSize(cw, ch)
                item.setData(Qt.ItemDataRole.UserRole, d)
        self._delegate._cache.clear()
        self.grid.update()

    def _on_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        if self._mode == 'buiten_db':
            active = self._buiten_db_active()
            if active is None:
                return
            cat_key, val = active
            actor = data.get('actor')
            if not actor:
                actor_id = db.create_actor(data.get('stem', ''))
                actor = db.get_actor(actor_id)
                if not actor:
                    return
            meta = dict(data.get('meta', {}))
            meta[cat_key] = val
            db.update_actor_meta(actor['id'], meta)
            # Update item so card re-renders and filter hides it
            new_data = dict(data)
            new_data['meta']   = meta
            new_data['actor']  = actor
            new_data['in_db']  = bool(meta.get('voornaam') or meta.get('achternaam'))
            item.setData(Qt.ItemDataRole.UserRole, new_data)
            self._apply_filters()
            return

        # in_db mode: copy full name to clipboard
        if not data.get('in_db'):
            return
        meta = data.get('meta', {})
        voornaam   = meta.get('voornaam', '')
        achternaam = meta.get('achternaam', '')
        full_name  = f"{voornaam} {achternaam}".strip() or data.get('stem', '')
        if full_name:
            QApplication.clipboard().setText(full_name)

    def _on_item_enter(self, item):
        """Enter op een acteur-kaart: open detailpagina (niet in buiten_db-modus)."""
        if self._mode == 'buiten_db':
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and data.get('in_db'):
            self._open_detail(data)

    # ── Detail navigatie ─────────────────────────

    def _visible_items(self) -> list:
        """Return all items in current sorted order that are not hidden."""
        return [it for it in self._all_items if not it.isHidden()]

    def _open_detail(self, data: dict):
        self._current_detail_stem = data.get('stem', '')
        self._detail_view.load(data)
        self._update_nav_info()
        self._stack.setCurrentIndex(1)

    def _update_nav_info(self):
        visible = self._visible_items()
        idx = next(
            (i for i, it in enumerate(visible)
             if (it.data(Qt.ItemDataRole.UserRole) or {}).get('stem') == self._current_detail_stem),
            0
        )
        self._detail_view.set_nav_info(idx, len(visible))

    def _navigate_actor(self, direction: int):
        visible = self._visible_items()
        if not visible:
            return
        idx = next(
            (i for i, it in enumerate(visible)
             if (it.data(Qt.ItemDataRole.UserRole) or {}).get('stem') == self._current_detail_stem),
            -1
        )
        new_idx = idx + direction
        if 0 <= new_idx < len(visible):
            data = visible[new_idx].data(Qt.ItemDataRole.UserRole)
            if data:
                self._open_detail(data)

    def _on_detail_back(self):
        self._stack.setCurrentIndex(0)

    def _on_detail_saved(self):
        self.refresh()

    def _on_detail_open_film(self, path):
        self.search_input.clear()
        self.open_film_requested.emit(path)

    # ── Import ───────────────────────────────────

    def _import_actors(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importeer acteurs",
            "", "Bestanden (*.csv *.txt *.tsv);;Alle bestanden (*.*)"
        )
        if not path:
            return

        try:
            records = self._parse_import_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Importfout", f"Kan bestand niet lezen:\n{e}")
            return

        if not records:
            QMessageBox.information(self, "Importeren", "Geen records gevonden in het bestand.")
            return

        preview_lines = [f"• {r['name']}" for r in records[:15]]
        if len(records) > 15:
            preview_lines.append(f"... en {len(records) - 15} meer")

        msg = QMessageBox(self)
        msg.setWindowTitle("Importeren")
        msg.setText(f"{len(records)} acteurs gevonden. Importeren?")
        msg.setDetailedText("\n".join(preview_lines))
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        msg.button(QMessageBox.StandardButton.Yes).setText("Importeer")
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return

        inserted, updated = db.import_actors_from_records(records)
        self.refresh()
        QMessageBox.information(
            self, "Klaar",
            f"{inserted} nieuwe acteurs toegevoegd.\n"
            f"{updated} bestaande acteurs bijgewerkt."
        )

    def _parse_import_file(self, path):
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            content = f.read()

        first_line = content.lstrip()[:200]
        if first_line.startswith('+') or '|' in first_line.split('\n')[0]:
            return self._parse_mysql_table(content)
        elif '\t' in content.split('\n')[0]:
            return self._parse_dsv(content, delimiter='\t')
        else:
            return self._parse_dsv(content, delimiter=',')

    def _parse_mysql_table(self, content):
        data_lines = [l for l in content.splitlines() if l.strip().startswith('|')]
        if not data_lines:
            return []

        def split_row(line):
            return [c.strip() for c in line.strip().strip('|').split('|')]

        headers = [h.lower().strip() for h in split_row(data_lines[0])]
        records = []
        for line in data_lines[1:]:
            values = split_row(line)
            row = dict(zip(headers, values))
            r = self._normalize_import_row(row)
            if r:
                records.append(r)
        return records

    def _parse_dsv(self, content, delimiter=','):
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        records = []
        for row in reader:
            normalized = {k.lower().strip(): (v or '').strip() for k, v in row.items()}
            r = self._normalize_import_row(normalized)
            if r:
                records.append(r)
        return records

    def _normalize_import_row(self, row):
        # Prefer AfbeeldingURL stem as name (matches photo filenames)
        img = row.get('afbeeldingurl') or row.get('afbeelding') or ''
        img = img.strip()
        if img and img.lower() not in ('null', ''):
            name = Path(img).stem
        else:
            voornaam = row.get('voornaam', '').strip()
            achternaam = row.get('achternaam', '').strip()
            name = (voornaam + achternaam).strip()

        if not name or name.lower() == 'null':
            return None

        metadata = {}
        for field in ('rating', 'decennia', 'kleur', 'grootte', 'voornaam', 'achternaam'):
            val = row.get(field, '').strip()
            if val and val.lower() != 'null':
                metadata[field] = val

        return {
            'name': name,
            'notes': json.dumps(metadata, ensure_ascii=False) if metadata else ''
        }

    def refresh(self):
        folder = db.get_setting('photo_folder', '')
        if folder:
            self._scan_folder(folder)

