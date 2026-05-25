#!/usr/bin/env python3
"""
CineMarker — Markers Overzicht
Toont alle markers van alle acteurs in alle films, filterbaar op categorie en acteur.
"""

import os
import json
import random
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QSplitter, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QIcon

import database as db
from actors_panel import FrameExtractWorker, MarkerGridDelegate


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _fmt_hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _load_cat_pix(cat: dict, size: int) -> QPixmap | None:
    ip = cat.get('icon_path', '')
    if ip and os.path.exists(ip):
        raw = QPixmap(ip)
        if not raw.isNull():
            return raw.scaled(size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
    return None


# ─────────────────────────────────────────────
#  MarkersPanel
# ─────────────────────────────────────────────

THUMB_W = FrameExtractWorker.THUMB_W   # 320
THUMB_H = FrameExtractWorker.THUMB_H   # 180
CELL_W  = THUMB_W + 4
CELL_H  = THUMB_H + 4


class MarkersPanel(QWidget):
    scene_jump_requested    = pyqtSignal(str, float)   # film_path, time_sec
    play_selection_requested = pyqtSignal(list)         # list of filtered entries

    def __init__(self, mpv_player=None):
        super().__init__()
        self._player      = mpv_player
        self._all_entries: list = []    # alle geladen markers (dicts)
        self._cat_filter:  set  = set() # actieve categorie-id's (leeg = alle)
        self._actor_filter: set = set() # actieve acteur-id's   (leeg = alle)
        self._all_items:   list = []    # QListWidgetItems in volgorde van grid
        self._worker: FrameExtractWorker | None = None
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Topbalk ─────────────────────────────────────────────
        top = QWidget()
        top.setFixedHeight(42)
        top.setStyleSheet("background: #111; border-bottom: 1px solid #1e1e1e;")
        th = QHBoxLayout(top)
        th.setContentsMargins(12, 0, 10, 0)
        th.setSpacing(10)

        lbl_title = QLabel("ALLE MARKERS")
        lbl_title.setStyleSheet(
            "color: #444; font-size: 9px; letter-spacing: 4px;"
        )
        th.addWidget(lbl_title)

        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet("color: #444; font-size: 11px;")
        th.addWidget(self._lbl_count)

        th.addStretch()

        self._btn_play = QPushButton("▶  Afspelen")
        self._btn_play.setFixedHeight(28)
        self._btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_play.setEnabled(False)
        self._btn_play.setToolTip(
            "Open de speler met alle gefilterde markers als afspeellijst"
        )
        self._btn_play.setStyleSheet(
            "QPushButton { background: #1a1500; border: 1px solid #3a3000;"
            "  border-radius: 4px; color: #665500; font-size: 11px;"
            "  padding: 0 14px; }"
            "QPushButton:enabled { border-color: #e8b86d; color: #e8b86d; }"
            "QPushButton:enabled:hover { background: #2a2200; }"
            "QPushButton:enabled:pressed { background: #e8b86d; color: #000; }"
        )
        self._btn_play.clicked.connect(self._emit_play_selection)
        th.addWidget(self._btn_play)

        btn_refresh = QPushButton("↺")
        btn_refresh.setFixedSize(28, 28)
        btn_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_refresh.setToolTip("Vernieuwen")
        btn_refresh.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #2a2a2a;"
            "  border-radius: 4px; color: #555; font-size: 14px; }"
            "QPushButton:hover { border-color: #e8b86d; color: #e8b86d; }"
        )
        btn_refresh.clicked.connect(self.refresh)
        th.addWidget(btn_refresh)

        root.addWidget(top)

        # ── Categorie-filter ─────────────────────────────────────
        self._cat_scroll = QScrollArea()
        self._cat_scroll.setFixedHeight(36)
        self._cat_scroll.setWidgetResizable(True)
        self._cat_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cat_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cat_scroll.setStyleSheet(
            "QScrollArea { border: none; background: #0d0d0d;"
            "  border-bottom: 1px solid #1a1a1a; }"
        )

        self._cat_bar = QWidget()
        self._cat_bar.setStyleSheet("background: #0d0d0d;")
        self._cat_layout = QHBoxLayout(self._cat_bar)
        self._cat_layout.setContentsMargins(8, 4, 8, 4)
        self._cat_layout.setSpacing(5)
        self._cat_layout.addStretch()
        self._cat_scroll.setWidget(self._cat_bar)
        root.addWidget(self._cat_scroll)

        # ── Body: acteurlijst | markersgrid ──────────────────────
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet("QSplitter::handle { background: #1e1e1e; width: 2px; }")
        body.setHandleWidth(2)

        # Acteurlijst
        actor_wrap = QWidget()
        actor_wrap.setStyleSheet("background: #0d0d0d;")
        av = QVBoxLayout(actor_wrap)
        av.setContentsMargins(8, 8, 6, 8)
        av.setSpacing(6)

        lbl_a = QLabel("ACTEURS")
        lbl_a.setStyleSheet(
            "color: #444; font-size: 9px; letter-spacing: 3px;"
        )
        av.addWidget(lbl_a)

        self._actor_list = QListWidget()
        self._actor_list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                color: #aaa;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 5px 6px;
                border-bottom: 1px solid #181818;
            }
            QListWidget::item:hover    { background: #161616; }
            QListWidget::item:selected { background: #1e1800; color: #e8b86d; }
        """)
        self._actor_list.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection
        )
        self._actor_list.itemSelectionChanged.connect(
            self._on_actor_selection_changed
        )
        av.addWidget(self._actor_list)

        actor_wrap.setMinimumWidth(140)
        actor_wrap.setMaximumWidth(230)
        body.addWidget(actor_wrap)

        # Markersgrid
        self._grid = QListWidget()
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setSpacing(8)
        self._grid.setUniformItemSizes(True)
        self._grid.setIconSize(QSize(THUMB_W, THUMB_H))
        self._grid.setStyleSheet("""
            QListWidget {
                background: #0a0a0a;
                border: none;
            }
            QListWidget::item {
                background: #111;
                border-radius: 4px;
            }
            QListWidget::item:hover    { background: #181818; }
            QListWidget::item:selected { background: #2a2200; }
        """)

        self._delegate = MarkerGridDelegate(self._grid)
        self._grid.setItemDelegate(self._delegate)
        self._grid.itemDoubleClicked.connect(self._on_item_jump)
        body.addWidget(self._grid)

        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([180, 9999])
        root.addWidget(body, stretch=1)

    # ── Data laden ───────────────────────────────────────────────

    def refresh(self):
        """Laad alle markers van alle films opnieuw."""
        # Stop lopende worker
        self._stop_worker()

        self._all_entries.clear()
        self._cat_filter.clear()
        self._actor_filter.clear()

        actors_dict = {a['id']: a for a in db.get_all_actors()}
        cats_dict   = {c['id']: c for c in db.get_all_categories()}

        for film in db.get_all_films():
            fp = film.get('file_path', '')
            if not fp or not os.path.exists(fp):
                continue
            p  = Path(fp)
            mf = p.parent / f".{p.stem}_markers.json"
            if not mf.exists():
                continue
            try:
                markers = json.loads(mf.read_text('utf-8'))
            except Exception:
                continue

            for m in markers:
                if m.get('negative'):
                    continue
                actor_ids = m.get('actors') or []
                cat_ids   = m.get('categories') or []
                if not actor_ids:
                    continue  # markers zonder acteur overslaan
                self._all_entries.append({
                    'marker':     m,
                    'film_path':  fp,
                    'film_name':  p.stem,
                    'actor_ids':  actor_ids,
                    'cat_ids':    cat_ids,
                    'actor_objs': [actors_dict[a] for a in actor_ids if a in actors_dict],
                    'cat_objs':   [cats_dict[c] for c in cat_ids if c in cats_dict],
                })

        self._rebuild_cat_buttons()
        self._rebuild_actor_list()
        self._apply_filters()

    # ── Categorie-filter ─────────────────────────────────────────

    def _rebuild_cat_buttons(self):
        lay = self._cat_layout
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.deleteLater()

        used_ids = set()
        for e in self._all_entries:
            used_ids.update(e['cat_ids'])

        cats = [c for c in db.get_all_categories() if c['id'] in used_ids]
        for cat in cats:
            btn = QPushButton(cat['name'])
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ip = cat.get('icon_path', '')
            if ip and os.path.exists(ip):
                btn.setIcon(QIcon(ip))
                btn.setIconSize(QSize(16, 16))
            btn.setStyleSheet(
                "QPushButton {"
                "  background: #161616; border: 1px solid #2a2a2a;"
                "  border-radius: 4px; padding: 2px 10px;"
                "  color: #666; font-size: 11px; }"
                "QPushButton:checked {"
                "  border-color: #e8b86d; color: #e8b86d; background: #1a1500; }"
                "QPushButton:hover { color: #aaa; border-color: #444; }"
            )
            btn.toggled.connect(
                lambda checked, cid=cat['id']: self._toggle_cat(cid, checked)
            )
            lay.addWidget(btn)

        lay.addStretch()

    def _toggle_cat(self, cat_id: int, checked: bool):
        if checked:
            self._cat_filter.add(cat_id)
        else:
            self._cat_filter.discard(cat_id)
        self._rebuild_actor_list()
        self._apply_filters()

    # ── Acteur-filter ────────────────────────────────────────────

    def _rebuild_actor_list(self):
        """Bouw de acteurlijst opnieuw — alleen acteurs die markers hebben
        na de actieve categorie-filter."""
        if self._cat_filter:
            visible = [e for e in self._all_entries
                       if self._cat_filter & set(e['cat_ids'])]
        else:
            visible = self._all_entries

        actor_counts: dict = {}
        for e in visible:
            for aid in e['actor_ids']:
                actor_counts[aid] = actor_counts.get(aid, 0) + 1

        prev_sel = self._actor_filter.copy()

        self._actor_list.blockSignals(True)
        self._actor_list.clear()

        actors_dict = {a['id']: a for a in db.get_all_actors()}
        for aid, cnt in sorted(
            actor_counts.items(),
            key=lambda x: actors_dict.get(x[0], {}).get('name', '').lower()
        ):
            actor = actors_dict.get(aid)
            if not actor:
                continue
            item = QListWidgetItem(f"{actor['name']}  ({cnt})")
            item.setData(Qt.ItemDataRole.UserRole, aid)
            self._actor_list.addItem(item)
            if aid in prev_sel:
                item.setSelected(True)

        self._actor_list.blockSignals(False)
        # Verwijder niet-meer-zichtbare acteurs uit het filter
        self._actor_filter &= set(actor_counts.keys())

    def _on_actor_selection_changed(self):
        self._actor_filter = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._actor_list.selectedItems()
        }
        self._apply_filters()

    # ── Markersgrid ──────────────────────────────────────────────

    def _apply_filters(self):
        """Herbouw het grid op basis van actieve filters."""
        self._stop_worker()
        self._delegate.invalidate_cache()
        self._grid.clear()
        self._all_items.clear()

        entries = self._all_entries
        if self._cat_filter:
            entries = [e for e in entries
                       if self._cat_filter & set(e['cat_ids'])]
        if self._actor_filter:
            entries = [e for e in entries
                       if self._actor_filter & set(e['actor_ids'])]

        cache_dir = (
            Path(os.path.dirname(os.path.abspath(__file__))) / 'marker_cache'
        )
        cache_dir.mkdir(exist_ok=True)

        pending_tasks: list = []   # (row_idx, film_path, time_sec, cache_path)

        for row_idx, entry in enumerate(entries):
            m         = entry['marker']
            time_sec  = m.get('time', 0.0)
            time_ms   = int(time_sec * 1000)
            film_path = entry['film_path']

            cache_name = (
                f"{Path(film_path).stem}_{time_ms}"
                f"_w{THUMB_W}.jpg"
            )
            cache_path = str(cache_dir / cache_name)

            # Categorie-iconen voor de delegate
            cat_pixmaps = []
            for c in entry['cat_objs']:
                px = _load_cat_pix(c, 20)
                if px:
                    cat_pixmaps.append(px)

            actor_names = ', '.join(a['name'] for a in entry['actor_objs'])

            item = QListWidgetItem()
            item.setSizeHint(QSize(CELL_W, CELL_H))
            item.setData(Qt.ItemDataRole.UserRole, {
                'cache_path':  cache_path,
                'time_str':    f"{_fmt_hms(time_sec)}  •  {actor_names}",
                'cat_pixmaps': cat_pixmaps,
                'cell_size':   QSize(CELL_W, CELL_H),
                'film_path':   film_path,
                'time':        time_sec,
            })
            item.setToolTip(
                f"{entry['film_name']}\n"
                f"{_fmt_hms(time_sec)}\n"
                f"{actor_names}"
            )

            self._grid.addItem(item)
            self._all_items.append(item)

            if not os.path.exists(cache_path):
                pending_tasks.append(
                    (row_idx, film_path, time_sec, cache_path)
                )

        # Start één worker voor alle ontbrekende thumbnails
        if pending_tasks:
            self._worker = FrameExtractWorker(pending_tasks)
            self._worker.frame_ready.connect(self._on_frame_ready)
            self._worker.start()

        count = len(entries)
        self._lbl_count.setText(
            f"{count} marker{'s' if count != 1 else ''}"
        )
        self._btn_play.setEnabled(count > 0)
        self._btn_play.setText(
            f"▶  {count} afspelen" if count > 0 else "▶  Afspelen"
        )

    def _on_frame_ready(self, row_idx: int, cache_path: str):
        """Plan de visuele update met een willekeurige vertraging zodat thumbnails
        niet allemaal tegelijk binnenkomen en het scherm flipt."""
        delay = 1000 + random.randint(0, 1000)
        QTimer.singleShot(delay, lambda: self._show_thumb(row_idx, cache_path))

    def _show_thumb(self, row_idx: int, cache_path: str):
        """Pas het thumbnail aan voor één item, zonder de volledige cache te wissen."""
        if row_idx >= len(self._all_items):
            return
        item = self._all_items[row_idx]
        try:
            d = item.data(Qt.ItemDataRole.UserRole)
            if d:
                d['cache_path'] = cache_path
                item.setData(Qt.ItemDataRole.UserRole, d)
                # Geen invalidate_cache() — het nieuwe pad zat nog nooit in de
                # delegate-cache (bestand bestond nog niet), dus het laadt vanzelf
                # vers op de volgende paint van dit ene item.
                self._grid.update(self._grid.model().index(row_idx, 0))
        except RuntimeError:
            pass  # item al verwijderd door nieuwe filter

    def _emit_play_selection(self):
        """Stuur de gefilterde entries naar de speler als afspeellijst."""
        entries = self._all_entries
        if self._cat_filter:
            entries = [e for e in entries if self._cat_filter & set(e['cat_ids'])]
        if self._actor_filter:
            entries = [e for e in entries if self._actor_filter & set(e['actor_ids'])]
        if entries:
            self.play_selection_requested.emit(entries)

    def _on_item_jump(self, item: QListWidgetItem):
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            self.scene_jump_requested.emit(d['film_path'], d['time'])

    # ── Worker cleanup ───────────────────────────────────────────

    def _stop_worker(self):
        if self._worker is not None:
            self._worker.stop()
            try:
                self._worker.frame_ready.disconnect()
            except Exception:
                pass
            self._worker = None
