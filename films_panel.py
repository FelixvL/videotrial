#!/usr/bin/env python3
"""
CineMarker — Films browser panel  (grid view, sortable)
"""

import os
import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QLineEdit, QFrame,
    QFileDialog, QStyledItemDelegate, QStyle, QListView,
    QMenu, QMessageBox
)
from PyQt6.QtCore import Qt, QSize, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap

import database as db


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
]


def _count_film_markers(file_path: str) -> int:
    p = Path(file_path)
    mf = p.parent / f".{p.stem}_markers.json"
    if not mf.exists():
        return 0
    try:
        return len(json.loads(mf.read_text('utf-8')))
    except Exception:
        return 0


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
                raw = QPixmap(path)
                if not raw.isNull():
                    sc = raw.scaled(w, h,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
                    ox = (sc.width()  - w) // 2
                    oy = (sc.height() - h) // 2
                    pix = sc.copy(ox, oy, w, h)
            self._thumb_cache[key] = pix
        return self._thumb_cache[key]

    def _actor_pixmaps(self, film_id: int) -> list:
        if film_id not in self._actor_cache:
            result = []
            for actor in db.get_actors_for_film(film_id)[:6]:
                photos = db.get_actor_photos(actor['id'])
                if photos:
                    raw = QPixmap(photos[0]['photo_path'])
                    if not raw.isNull():
                        sz = ACT_SZ
                        sc = raw.scaled(sz, sz,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
                        ox = (sc.width()  - sz) // 2
                        oy = (sc.height() - sz) // 2
                        result.append(sc.copy(ox, oy, sz, sz))
            self._actor_cache[film_id] = result
        return self._actor_cache[film_id]

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

        # Thumbnail — cycle through all thumbnails when more than one exists
        thumbs = data.get('thumbnails', [])
        if thumbs:
            thumb_path = thumbs[self._tick % len(thumbs)]
        else:
            thumb_path = data.get('thumbnail', '')
        pix = self._thumb(thumb_path, w, h)
        if pix:
            painter.drawPixmap(r.x(), r.y(), pix)
        else:
            painter.fillRect(r, QColor('#0d0d0d'))
            f = QFont(painter.font())
            f.setPointSize(18)
            painter.setFont(f)
            painter.setPen(QColor('#252525'))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, '▶')

        # Bottom info bar — always visible when not hovered
        bar_h = 20
        bar_r = QRect(r.x(), r.bottom() - bar_h, w, bar_h)
        if not hovered:
            painter.fillRect(bar_r, QColor(0, 0, 0, 170))
            bf = QFont(painter.font())
            bf.setPointSize(7)
            painter.setFont(bf)

            duration = data.get('duration', 0) or 0
            markers  = data.get('markers',  0) or 0
            size_b   = data.get('size',     0) or 0

            # File size — right-aligned
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

            # Marker count — left-aligned
            if markers > 0:
                painter.setPen(QColor('#6db8e8'))
                painter.drawText(bar_r.adjusted(5, 0, 0, 0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f'◉{markers}')

        # Actor photos (bottom-left, above the info bar)
        film_id = data.get('film_id')
        if film_id:
            ax = r.x() + 3
            ay = r.bottom() - ACT_SZ - bar_h - 2
            for ap in self._actor_pixmaps(film_id):
                painter.drawPixmap(ax, ay, ap)
                ax += ACT_SZ + 2

        # Hover: dim + name
        if hovered and not selected:
            painter.fillRect(r, QColor(0, 0, 0, 80))
            name_r = QRect(r.x(), r.bottom() - 22, w, 22)
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

    def __init__(self):
        super().__init__()
        self._all_items:  list = []
        self._sort_key:   str  = 'name'
        self._sort_asc:   bool = True
        self._sort_btns:  dict = {}
        self._zoom_level: int  = int(db.get_setting('zoom_films_panel', '0') or '0')
        self._build_ui()
        folder = db.get_setting('film_folder', '')
        if folder:
            self._update_folder_label(folder)
            self._scan_folder(folder)

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Top toolbar ──────────────────────────
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
        self.search_input.setFixedWidth(200)
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

        btn_zoom_out = QPushButton("−")
        btn_zoom_out.setFixedSize(28, 28)
        btn_zoom_out.setAutoRepeat(True)
        btn_zoom_out.setAutoRepeatDelay(400)
        btn_zoom_out.setAutoRepeatInterval(80)
        btn_zoom_out.clicked.connect(self._zoom_out)
        b.addWidget(btn_zoom_out)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setFixedSize(28, 28)
        btn_zoom_in.setAutoRepeat(True)
        btn_zoom_in.setAutoRepeatDelay(400)
        btn_zoom_in.setAutoRepeatInterval(80)
        btn_zoom_in.clicked.connect(self._zoom_in)
        b.addWidget(btn_zoom_in)

        v.addWidget(bar)

        # ── Sort bar ─────────────────────────────
        sort_bar = QFrame()
        sort_bar.setFixedHeight(30)
        sort_bar.setStyleSheet(
            "QFrame { background: #080808; border-bottom: 1px solid #161616; }"
        )
        sb = QHBoxLayout(sort_bar)
        sb.setContentsMargins(8, 3, 8, 3)
        sb.setSpacing(4)

        sort_lbl = QLabel("Sorteren:")
        sort_lbl.setStyleSheet("color: #333; font-size: 10px;")
        sb.addWidget(sort_lbl)

        for key, label in SORT_FIELDS:
            btn = QPushButton(label)
            btn.setFixedHeight(22)
            btn.setStyleSheet(self._SORT_BTN_STYLE)
            btn.clicked.connect(lambda _, k=key: self._set_sort(k))
            sb.addWidget(btn)
            self._sort_btns[key] = btn

        sb.addStretch()
        v.addWidget(sort_bar)
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

        # Animation timer — advances thumbnail frame every 2 s
        self._tick = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(2000)
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

        db_films = {f['file_path']: f for f in db.get_all_films()}

        films = sorted(
            (f for f in folder_path.iterdir() if f.suffix.lower() in VIDEO_EXTS),
            key=lambda f: f.name.lower()
        )

        for fp in films:
            db_film   = db_films.get(str(fp), {})
            film_id   = db_film.get('id')
            thumbnail = db_film.get('thumbnail', '')
            duration  = db_film.get('duration', 0) or 0

            # All thumbnails for cycling animation
            if film_id:
                _rows = db.get_film_thumbnails(film_id)
                thumbnails = [r['path'] for r in _rows if os.path.exists(r['path'])]
            else:
                thumbnails = []
            if not thumbnails and thumbnail and os.path.exists(thumbnail):
                thumbnails = [thumbnail]

            try:
                st = fp.stat()
                size = st.st_size
                date = st.st_mtime
            except OSError:
                size = 0
                date = 0

            markers = _count_film_markers(str(fp))

            cw, ch = self._zoom_size()
            item = QListWidgetItem()
            item.setSizeHint(QSize(cw, ch))
            item.setToolTip(fp.stem)
            item.setData(Qt.ItemDataRole.UserRole, {
                'path':       str(fp),
                'name':       fp.stem,
                'thumbnail':  thumbnail,
                'thumbnails': thumbnails,
                'film_id':    film_id,
                'size':       size,
                'date':       date,
                'markers':    markers,
                'duration':   duration,
                'cell_size':  QSize(cw, ch),
            })
            self.film_list.addItem(item)
            self._all_items.append(item)

        self._sort_and_repopulate()
        self._update_count()

    # ── Filter ───────────────────────────────────

    def _filter(self, query: str):
        self._apply_search_visibility()

    def _apply_search_visibility(self):
        q = self.search_input.text().lower()
        for item in self._all_items:
            d    = item.data(Qt.ItemDataRole.UserRole)
            name = d.get('name', '').lower() if d else ''
            item.setHidden(bool(q) and q not in name)
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
