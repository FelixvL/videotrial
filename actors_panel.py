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
    QFrame, QGridLayout, QTextEdit, QDialog, QDialogButtonBox,
    QProgressBar, QCheckBox, QSizePolicy, QStackedWidget,
    QStyledItemDelegate, QApplication, QComboBox, QStyle
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QTimer, QRect
from PyQt6.QtGui import QPixmap, QFont, QIcon, QPen, QColor, QPainter, QBrush

import database as db


# ─────────────────────────────────────────────
#  FFmpeg Scene Export Worker
# ─────────────────────────────────────────────

class SceneExportWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, film_path, start, end, output_path):
        super().__init__()
        self.film_path = film_path
        self.start = start
        self.end = end
        self.output_path = output_path

    def run(self):
        duration = self.end - self.start
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(self.start),
            '-i', self.film_path,
            '-t', str(duration),
            '-c:v', 'libx264',
            '-crf', '18',
            '-c:a', 'aac',
            '-avoid_negative_ts', 'make_zero',
            self.output_path
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            self.finished.emit(self.output_path)
        else:
            self.error.emit(result.stderr.decode()[-500:])


# ─────────────────────────────────────────────
#  Actor Card Delegate
# ─────────────────────────────────────────────

class ActorCardDelegate(QStyledItemDelegate):

    BORDER = {
        '9': ('#FFD700', Qt.PenStyle.SolidLine, 3),
        '8': ('#C0C0C0', Qt.PenStyle.SolidLine, 3),
        '7': ('#CD7F32', Qt.PenStyle.SolidLine, 3),
        '6': ('#FFFF00', Qt.PenStyle.DashLine, 2),
        '5': ('#FFFFFF', Qt.PenStyle.DashLine, 2),
    }
    TEXT_COLOR = {
        '1': QColor('#FFFFFF'),   # wit
        '2': QColor('#000000'),   # zwart
        '3': QColor('#8B4513'),   # bruin
    }
    GLOW_COLOR = {
        '1': QColor(0, 0, 0, 220),
        '2': QColor(255, 255, 255, 220),
        '3': QColor(0, 0, 0, 220),
    }
    GLOW_OFFSETS = [
        (-1,-1),(0,-1),(1,-1),
        (-1, 0),       (1, 0),
        (-1, 1),(0, 1),(1, 1),
        (-2, 0),(2, 0),(0,-2),(0, 2),
    ]

    def __init__(self):
        super().__init__()
        self._cache: dict[str, QPixmap] = {}

    def _get_pix(self, path, w, h):
        key = f"{path}:{w}:{h}"
        if key not in self._cache:
            if os.path.exists(path):
                self._cache[key] = QPixmap(path).scaled(
                    w, h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            else:
                self._cache[key] = QPixmap()
        return self._cache[key]

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            super().paint(painter, option, index)
            return

        r = option.rect
        inner = r.adjusted(3, 3, -3, -3)
        meta = data.get('meta', {})
        in_db = data.get('in_db', False)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Background
        painter.fillRect(r, QColor('#0d0d0d'))

        # Photo — fills entire inner rect
        pix = self._get_pix(data['photo_path'], inner.width(), inner.height())
        if not pix.isNull():
            px = inner.x() + (inner.width() - pix.width()) // 2
            py = inner.y() + (inner.height() - pix.height()) // 2
            painter.drawPixmap(px, py, pix)

        # Name — glow text, no bar
        voornaam  = meta.get('voornaam', '')
        achternaam = meta.get('achternaam', '')
        display = f"{voornaam} {achternaam}".strip() or data.get('stem', '')

        kleur = str(meta.get('kleur', '1'))
        text_col = self.TEXT_COLOR.get(kleur, QColor('#FFFFFF'))
        glow_col = self.GLOW_COLOR.get(kleur, QColor(0, 0, 0, 220))

        nf = painter.font()
        nf.setPointSize(8)
        nf.setBold(True)
        painter.setFont(nf)

        name_rect = QRect(inner.x() + 2, inner.bottom() - 22, inner.width() - 4, 20)
        name_flags = Qt.AlignmentFlag.AlignCenter

        painter.setPen(glow_col)
        for dx, dy in self.GLOW_OFFSETS:
            painter.drawText(name_rect.translated(dx, dy), name_flags, display)
        painter.setPen(text_col)
        painter.drawText(name_rect, name_flags, display)

        # Stars — top right
        try:
            stars = int(meta.get('grootte', 0))
        except (ValueError, TypeError):
            stars = 0
        if stars > 0:
            sf = painter.font()
            sf.setPointSize(9)
            sf.setBold(False)
            painter.setFont(sf)
            painter.setPen(QColor('#FFD700'))
            star_rect = QRect(inner.x(), inner.y() + 3, inner.width() - 3, 16)
            painter.drawText(star_rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                '★' * stars)

        # Decade — top left
        dec_val = str(meta.get('decennia', '')).strip()
        if dec_val and dec_val.lower() not in ('null', ''):
            try:
                dec_str = str(int(dec_val) * 10)
            except ValueError:
                dec_str = dec_val
            df = painter.font()
            df.setPointSize(8)
            df.setBold(False)
            painter.setFont(df)
            painter.setPen(QColor('#aaaaaa'))
            dec_rect = QRect(inner.x() + 4, inner.y() + 3, 40, 16)
            painter.drawText(dec_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                dec_str)

        # Rating border
        rating = str(meta.get('rating', '')).strip()
        if rating in self.BORDER:
            col, style, width = self.BORDER[rating]
            pen = QPen(QColor(col), width, style)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(inner.adjusted(1, 1, -1, -1))

        # Dim overlay if not in DB
        if not in_db:
            painter.fillRect(inner, QColor(0, 0, 0, 140))
            f2 = painter.font()
            f2.setPointSize(8)
            f2.setBold(False)
            painter.setFont(f2)
            painter.setPen(QColor('#555555'))
            painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, "niet in\ndatabase")

        # Selection highlight
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(inner, QColor(232, 184, 109, 35))
            sel_pen = QPen(QColor('#e8b86d'), 2, Qt.PenStyle.SolidLine)
            painter.setPen(sel_pen)
            painter.drawRect(inner.adjusted(1, 1, -1, -1))

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(170, 220)


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


def parse_time(time_str):
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


class SceneEditorDialog(QDialog):
    """Dialog for defining a scene with start/end and actors"""

    def __init__(self, parent, film, player, existing_scene=None):
        super().__init__(parent)
        self.film = film
        self.player = player  # mpv player instance
        self.existing_scene = existing_scene
        self._export_worker = None

        self.setWindowTitle("Scène Editor")
        self.setMinimumWidth(520)
        self.setStyleSheet(parent.styleSheet())

        self._build_ui()
        if existing_scene:
            self._load_existing(existing_scene)

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)
        v.setContentsMargins(16, 16, 16, 16)

        # Title
        lbl_film = QLabel(f"Film: {self.film['title']}")
        lbl_film.setStyleSheet("color: #e8b86d; font-size: 11px; letter-spacing: 2px;")
        v.addWidget(lbl_film)

        # Scene name
        v.addWidget(self._section("NAAM"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Naam van de scène...")
        v.addWidget(self.title_input)

        # Start / End
        v.addWidget(self._section("TIJDCODES"))

        grid = QHBoxLayout()

        start_col = QVBoxLayout()
        start_col.addWidget(QLabel("Start"))
        self.start_input = QLineEdit("00:00:00.000")
        self.start_input.setFixedWidth(140)
        start_col.addWidget(self.start_input)
        btn_set_start = QPushButton("⊕ Stel in op huidig moment")
        btn_set_start.clicked.connect(self._set_start_now)
        start_col.addWidget(btn_set_start)
        btn_play_start = QPushButton("▶ Spring naar start")
        btn_play_start.clicked.connect(self._jump_to_start)
        start_col.addWidget(btn_play_start)
        grid.addLayout(start_col)

        grid.addSpacing(16)

        end_col = QVBoxLayout()
        end_col.addWidget(QLabel("Einde"))
        self.end_input = QLineEdit("00:00:00.000")
        self.end_input.setFixedWidth(140)
        end_col.addWidget(self.end_input)
        btn_set_end = QPushButton("⊕ Stel in op huidig moment")
        btn_set_end.clicked.connect(self._set_end_now)
        end_col.addWidget(btn_set_end)
        btn_play_end = QPushButton("▶ Spring naar einde")
        btn_play_end.clicked.connect(self._jump_to_end)
        end_col.addWidget(btn_play_end)
        grid.addLayout(end_col)

        grid.addStretch()
        v.addLayout(grid)

        # Duration preview
        self.lbl_duration = QLabel("Duur: —")
        self.lbl_duration.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.lbl_duration)
        self.start_input.textChanged.connect(self._update_duration)
        self.end_input.textChanged.connect(self._update_duration)

        # Actors
        v.addWidget(self._section("ACTEURS IN DEZE SCÈNE"))

        actors_row = QHBoxLayout()
        self.actors_list = QListWidget()
        self.actors_list.setMaximumHeight(120)
        self.actors_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        actors = db.get_all_actors()
        for a in actors:
            item = QListWidgetItem(a['name'])
            item.setData(Qt.ItemDataRole.UserRole, a['id'])
            self.actors_list.addItem(item)
        actors_row.addWidget(self.actors_list)
        v.addLayout(actors_row)

        # Notes
        v.addWidget(self._section("NOTITIES"))
        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(60)
        self.notes_input.setPlaceholderText("Optionele notities...")
        v.addWidget(self.notes_input)

        # Export
        v.addWidget(self._section("EXPORTEREN"))
        export_row = QHBoxLayout()
        self.export_path_input = QLineEdit()
        self.export_path_input.setPlaceholderText("Uitvoerpad voor export...")
        export_row.addWidget(self.export_path_input)
        btn_pick_export = QPushButton("...")
        btn_pick_export.setFixedWidth(32)
        btn_pick_export.clicked.connect(self._pick_export_path)
        export_row.addWidget(btn_pick_export)
        v.addLayout(export_row)

        btn_suggest = QPushButton("← Stel automatisch pad in")
        btn_suggest.clicked.connect(self._suggest_export_path)
        v.addWidget(btn_suggest)

        self.export_progress = QProgressBar()
        self.export_progress.setVisible(False)
        self.export_progress.setRange(0, 0)  # indeterminate
        v.addWidget(self.export_progress)

        self.export_status = QLabel("")
        self.export_status.setStyleSheet("color: #888; font-size: 11px;")
        self.export_status.setWordWrap(True)
        v.addWidget(self.export_status)

        self.btn_export = QPushButton("✂  EXPORTEER SCÈNE")
        self.btn_export.setObjectName("accent")
        self.btn_export.clicked.connect(self._do_export)
        v.addWidget(self.btn_export)

        # Dialog buttons
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #222;")
        v.addWidget(sep)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.Save).setText("💾  Opslaan")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuleren")
        v.addWidget(btns)

    def _section(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 3px; margin-top: 4px;")
        return lbl

    def _load_existing(self, scene):
        self.title_input.setText(scene['title'])
        self.start_input.setText(format_time(scene['start_time']))
        self.end_input.setText(format_time(scene['end_time']))
        self.notes_input.setPlainText(scene.get('notes', ''))
        if scene.get('export_path'):
            self.export_path_input.setText(scene['export_path'])

        # Select linked actors
        linked = {a['id'] for a in db.get_actors_for_scene(scene['id'])}
        for i in range(self.actors_list.count()):
            item = self.actors_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) in linked:
                item.setSelected(True)

    def _set_start_now(self):
        try:
            pos = self.player.time_pos or 0
            self.start_input.setText(format_time(pos))
        except Exception:
            pass

    def _set_end_now(self):
        try:
            pos = self.player.time_pos or 0
            self.end_input.setText(format_time(pos))
        except Exception:
            pass

    def _jump_to_start(self):
        try:
            t = parse_time(self.start_input.text())
            self.player.seek(t, 'absolute+exact')
        except Exception:
            pass

    def _jump_to_end(self):
        try:
            t = parse_time(self.end_input.text())
            self.player.seek(t, 'absolute+exact')
        except Exception:
            pass

    def _update_duration(self):
        try:
            s = parse_time(self.start_input.text())
            e = parse_time(self.end_input.text())
            dur = e - s
            if dur > 0:
                self.lbl_duration.setText(f"Duur: {format_time(dur)}")
            else:
                self.lbl_duration.setText("⚠ Eindtijd moet na starttijd liggen")
        except Exception:
            self.lbl_duration.setText("Duur: —")

    def _pick_export_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exporteer scène", "",
            "MP4 (*.mp4);;MOV (*.mov);;MKV (*.mkv)")
        if path:
            self.export_path_input.setText(path)

    def _suggest_export_path(self):
        title = self.title_input.text().strip() or "scene"
        safe = "".join(c for c in title if c.isalnum() or c in ' _-').strip().replace(' ', '_')
        film_dir = Path(self.film['file_path']).parent
        self.export_path_input.setText(str(film_dir / f"{safe}.mp4"))

    def _do_export(self):
        output = self.export_path_input.text().strip()
        if not output:
            QMessageBox.warning(self, "Exporteren", "Geef een uitvoerpad op.")
            return
        start = parse_time(self.start_input.text())
        end = parse_time(self.end_input.text())
        if end <= start:
            QMessageBox.warning(self, "Exporteren", "Eindtijd moet na starttijd liggen.")
            return

        self.btn_export.setEnabled(False)
        self.export_progress.setVisible(True)
        self.export_status.setText("Bezig met exporteren…")

        self._export_worker = SceneExportWorker(self.film['file_path'], start, end, output)
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, path):
        self.btn_export.setEnabled(True)
        self.export_progress.setVisible(False)
        self.export_status.setText(f"✓ Geëxporteerd: {path}")
        self.export_path_input.setText(path)

    def _on_export_error(self, err):
        self.btn_export.setEnabled(True)
        self.export_progress.setVisible(False)
        self.export_status.setText(f"✗ Fout: {err}")

    def get_data(self):
        selected_actors = []
        for i in range(self.actors_list.count()):
            item = self.actors_list.item(i)
            if item.isSelected():
                selected_actors.append(item.data(Qt.ItemDataRole.UserRole))

        return {
            'title': self.title_input.text().strip() or "Naamloos",
            'start_time': parse_time(self.start_input.text()),
            'end_time': parse_time(self.end_input.text()),
            'notes': self.notes_input.toPlainText(),
            'export_path': self.export_path_input.text().strip(),
            'actor_ids': selected_actors
        }


# ─────────────────────────────────────────────
#  Actor Detail Panel
# ─────────────────────────────────────────────

class ActorDetailPanel(QWidget):
    open_film_requested = pyqtSignal(str)  # file path
    scene_jump_requested = pyqtSignal(str, float)  # film path, time

    def __init__(self, player):
        super().__init__()
        self.player = player
        self._actor = None
        self._current_film = None
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        # Header
        header = QHBoxLayout()
        self.lbl_name = QLabel("Selecteer een acteur")
        self.lbl_name.setStyleSheet("font-size: 18px; font-weight: bold; color: #e8b86d; letter-spacing: 2px;")
        header.addWidget(self.lbl_name)
        header.addStretch()

        self.btn_edit_actor = QPushButton("✎ Bewerken")
        self.btn_edit_actor.setVisible(False)
        self.btn_edit_actor.clicked.connect(self._edit_actor)
        header.addWidget(self.btn_edit_actor)

        self.btn_delete_actor = QPushButton("✕")
        self.btn_delete_actor.setObjectName("danger")
        self.btn_delete_actor.setFixedWidth(32)
        self.btn_delete_actor.setVisible(False)
        self.btn_delete_actor.clicked.connect(self._delete_actor)
        header.addWidget(self.btn_delete_actor)

        v.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top: photos + films
        top = QWidget()
        top_h = QHBoxLayout(top)
        top_h.setContentsMargins(0, 0, 0, 0)
        top_h.setSpacing(8)

        # Photo
        photos_frame = QFrame()
        photos_frame.setStyleSheet("QFrame { background: #111; border-radius: 6px; border: 1px solid #1e1e1e; }")
        photos_frame.setFixedWidth(160)
        photos_v = QVBoxLayout(photos_frame)
        photos_v.setContentsMargins(8, 8, 8, 8)
        photos_v.setSpacing(4)
        photos_v.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_actor_photo = QLabel()
        self.lbl_actor_photo.setFixedSize(140, 175)
        self.lbl_actor_photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_actor_photo.setStyleSheet(
            "background: #1a1a1a; border-radius: 4px; color: #333; font-size: 32px;"
        )
        self.lbl_actor_photo.setText("?")
        photos_v.addWidget(self.lbl_actor_photo)

        top_h.addWidget(photos_frame)

        # Films
        films_frame = QFrame()
        films_frame.setStyleSheet("QFrame { background: #111; border-radius: 6px; border: 1px solid #1e1e1e; }")
        films_v = QVBoxLayout(films_frame)
        films_v.setContentsMargins(8, 8, 8, 8)
        films_v.setSpacing(4)

        f_header = QHBoxLayout()
        f_lbl = QLabel("FILMS")
        f_lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 3px;")
        f_header.addWidget(f_lbl)
        f_header.addStretch()
        btn_link_film = QPushButton("+ Koppel film")
        btn_link_film.setFixedHeight(24)
        btn_link_film.clicked.connect(self._link_film)
        f_header.addWidget(btn_link_film)
        films_v.addLayout(f_header)

        self.films_list = QListWidget()
        self.films_list.currentItemChanged.connect(self._on_film_selected)
        self.films_list.itemDoubleClicked.connect(self._open_film)
        films_v.addWidget(self.films_list)

        films_btn_row = QHBoxLayout()
        btn_open_film = QPushButton("▶ Open")
        btn_open_film.clicked.connect(self._open_film)
        films_btn_row.addWidget(btn_open_film)
        btn_unlink_film = QPushButton("✕ Ontkoppel")
        btn_unlink_film.setObjectName("danger")
        btn_unlink_film.clicked.connect(self._unlink_film)
        films_btn_row.addWidget(btn_unlink_film)
        films_v.addLayout(films_btn_row)

        top_h.addWidget(films_frame, stretch=2)

        splitter.addWidget(top)

        # Bottom: scenes
        scenes_frame = QFrame()
        scenes_frame.setStyleSheet("QFrame { background: #111; border-radius: 6px; border: 1px solid #1e1e1e; }")
        scenes_v = QVBoxLayout(scenes_frame)
        scenes_v.setContentsMargins(8, 8, 8, 8)
        scenes_v.setSpacing(4)

        sc_header = QHBoxLayout()
        sc_lbl = QLabel("SCÈNES VAN DEZE ACTEUR")
        sc_lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 3px;")
        sc_header.addWidget(sc_lbl)
        sc_header.addStretch()
        self.btn_new_scene = QPushButton("+ Nieuwe scène")
        self.btn_new_scene.setObjectName("accent")
        self.btn_new_scene.setFixedHeight(26)
        self.btn_new_scene.clicked.connect(self._new_scene)
        sc_header.addWidget(self.btn_new_scene)
        scenes_v.addLayout(sc_header)

        self.scenes_list = QListWidget()
        self.scenes_list.itemDoubleClicked.connect(self._jump_to_scene)
        scenes_v.addWidget(self.scenes_list)

        sc_btn_row = QHBoxLayout()
        btn_jump_scene = QPushButton("↵ Spring naar scène")
        btn_jump_scene.clicked.connect(self._jump_to_scene)
        sc_btn_row.addWidget(btn_jump_scene)
        btn_edit_scene = QPushButton("✎ Bewerken")
        btn_edit_scene.clicked.connect(self._edit_scene)
        sc_btn_row.addWidget(btn_edit_scene)
        btn_export_scene = QPushButton("✂ Exporteer")
        btn_export_scene.clicked.connect(self._export_scene_quick)
        sc_btn_row.addWidget(btn_export_scene)
        btn_del_scene = QPushButton("✕")
        btn_del_scene.setObjectName("danger")
        btn_del_scene.setFixedWidth(32)
        btn_del_scene.clicked.connect(self._delete_scene)
        sc_btn_row.addWidget(btn_del_scene)
        scenes_v.addLayout(sc_btn_row)

        splitter.addWidget(scenes_frame)
        splitter.setSizes([280, 300])

        v.addWidget(splitter)

    def load_actor(self, actor):
        self._actor = actor
        self.lbl_name.setText(actor['name'])
        self.btn_edit_actor.setVisible(True)
        self.btn_delete_actor.setVisible(True)
        self._refresh_photo()
        self._refresh_films()
        self._refresh_scenes()

    def _refresh_photo(self):
        if not self._actor:
            return
        path = self._find_actor_photo()
        if path:
            pix = QPixmap(path).scaled(
                140, 175,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.lbl_actor_photo.setPixmap(pix)
        else:
            self.lbl_actor_photo.setPixmap(QPixmap())
            self.lbl_actor_photo.setText("?")

    def _find_actor_photo(self):
        folder = db.get_setting('photo_folder', '')
        if not folder or not self._actor:
            return None
        exts = ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.gif']
        for ext in exts:
            p = Path(folder) / f"{self._actor['name']}{ext}"
            if p.exists():
                return str(p)
        return None

    def _refresh_films(self):
        if not self._actor:
            return
        self.films_list.clear()
        films = db.get_films_for_actor(self._actor['id'])
        for f in films:
            item = QListWidgetItem(f"  {f['title']}")
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.films_list.addItem(item)

    def _refresh_scenes(self):
        if not self._actor:
            return
        self.scenes_list.clear()
        scenes = db.get_scenes_for_actor(self._actor['id'])
        for s in scenes:
            dur = s['end_time'] - s['start_time']
            exported = " ✓" if s.get('export_path') else ""
            text = (f"  {s['film_title']}  —  "
                    f"{format_time(s['start_time'])} → {format_time(s['end_time'])}"
                    f"  [{format_time(dur)}]  {s['title']}{exported}")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.scenes_list.addItem(item)

    def _on_film_selected(self, item):
        if item:
            self._current_film = item.data(Qt.ItemDataRole.UserRole)

    def _edit_actor(self):
        if not self._actor:
            return
        name, ok = QInputDialog.getText(self, "Bewerk acteur", "Naam:", text=self._actor['name'])
        if ok and name:
            db.update_actor(self._actor['id'], name)
            self._actor['name'] = name
            self.lbl_name.setText(name)

    def _delete_actor(self):
        if not self._actor:
            return
        reply = QMessageBox.question(self, "Verwijder acteur",
            f"Acteur '{self._actor['name']}' en alle koppelingen verwijderen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_actor(self._actor['id'])
            self._actor = None
            self.lbl_name.setText("Selecteer een acteur")
            self.btn_edit_actor.setVisible(False)
            self.btn_delete_actor.setVisible(False)
            self.lbl_actor_photo.setPixmap(QPixmap())
            self.lbl_actor_photo.setText("?")
            self.films_list.clear()
            self.scenes_list.clear()

    def _add_photos(self):
        if not self._actor:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Selecteer foto's", "",
            "Afbeeldingen (*.jpg *.jpeg *.png *.webp *.bmp *.tiff)")
        for p in paths:
            db.add_actor_photo(self._actor['id'], p)
        self._refresh_photos()

    def _add_photo_folder(self):
        if not self._actor:
            return
        folder = QFileDialog.getExistingDirectory(self, "Selecteer map met foto's")
        if folder:
            n = db.import_photos_from_folder(self._actor['id'], folder)
            self._refresh_photos()
            QMessageBox.information(self, "Foto's", f"{n} foto('s) toegevoegd.")

    def _link_film(self):
        if not self._actor:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Selecteer film", "",
            "Video (*.mp4 *.avi *.mov *.wmv *.mkv *.flv *.webm *.m4v);;Alle bestanden (*)")
        if path:
            film = db.get_or_create_film(path)
            db.link_actor_film(self._actor['id'], film['id'])
            self._refresh_films()
            # Also open the film
            self.open_film_requested.emit(path)

    def _open_film(self):
        item = self.films_list.currentItem()
        if item:
            film = item.data(Qt.ItemDataRole.UserRole)
            self.open_film_requested.emit(film['file_path'])
            self._current_film = film

    def _unlink_film(self):
        item = self.films_list.currentItem()
        if not item or not self._actor:
            return
        film = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(self, "Ontkoppel film",
            f"Film '{film['title']}' ontkoppelen van {self._actor['name']}?\n"
            f"(Scènes blijven bewaard)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.unlink_actor_film(self._actor['id'], film['id'])
            self._refresh_films()

    def _get_current_film_for_scene(self):
        """Return the film to use for new scenes"""
        if self._current_film:
            return self._current_film
        # Try to get from player's current video
        return None

    def _new_scene(self):
        if not self._actor:
            QMessageBox.information(self, "Scène", "Selecteer eerst een acteur.")
            return

        film = self._current_film
        if not film:
            # Ask user to pick
            films = db.get_films_for_actor(self._actor['id'])
            if not films:
                QMessageBox.information(self, "Scène",
                    "Koppel eerst een film aan deze acteur via '+ Koppel film'.")
                return
            if len(films) == 1:
                film = films[0]
            else:
                names = [f['title'] for f in films]
                name, ok = QInputDialog.getItem(self, "Kies film",
                    "Welke film?", names, 0, False)
                if ok:
                    film = next(f for f in films if f['title'] == name)
                else:
                    return

        dlg = SceneEditorDialog(self, film, self.player)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if data['end_time'] <= data['start_time']:
                QMessageBox.warning(self, "Scène", "Eindtijd moet na starttijd liggen.")
                return
            scene_id = db.create_scene(
                film['id'], data['title'],
                data['start_time'], data['end_time'], data['notes']
            )
            if data['export_path']:
                db.update_scene_export_path(scene_id, data['export_path'])
            for actor_id in data['actor_ids']:
                db.link_scene_actor(scene_id, actor_id)
            # Always link to current actor
            db.link_scene_actor(scene_id, self._actor['id'])
            self._refresh_scenes()

    def _edit_scene(self):
        item = self.scenes_list.currentItem()
        if not item:
            return
        scene = item.data(Qt.ItemDataRole.UserRole)
        film = db.get_film(scene['film_id'])
        dlg = SceneEditorDialog(self, film, self.player, existing_scene=scene)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            db.update_scene(scene['id'], data['title'],
                data['start_time'], data['end_time'], data['notes'])
            if data['export_path']:
                db.update_scene_export_path(scene['id'], data['export_path'])
            # Update actor links
            for actor_id in data['actor_ids']:
                db.link_scene_actor(scene['id'], actor_id)
            self._refresh_scenes()

    def _jump_to_scene(self):
        item = self.scenes_list.currentItem()
        if not item:
            return
        scene = item.data(Qt.ItemDataRole.UserRole)
        self.scene_jump_requested.emit(scene['film_path'], scene['start_time'])

    def _export_scene_quick(self):
        item = self.scenes_list.currentItem()
        if not item:
            return
        scene = item.data(Qt.ItemDataRole.UserRole)
        film = db.get_film(scene['film_id'])
        dlg = SceneEditorDialog(self, film, self.player, existing_scene=scene)
        dlg.exec()

    def _delete_scene(self):
        item = self.scenes_list.currentItem()
        if not item:
            return
        scene = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(self, "Verwijder scène",
            f"Scène '{scene['title']}' verwijderen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_scene(scene['id'])
            self._refresh_scenes()


# ─────────────────────────────────────────────
#  Main Actors Panel — full-screen photo grid
# ─────────────────────────────────────────────

class ActorsPanel(QWidget):
    open_film_requested = pyqtSignal(str)
    scene_jump_requested = pyqtSignal(str, float)

    PHOTO_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.gif'}
    ZOOM_STEPS = [(100,130),(130,168),(160,206),(200,258),(240,310)]
    ZOOM_DEFAULT = 2

    def __init__(self, player):
        super().__init__()
        self.player = player
        self._all_items: list = []
        self._zoom_idx = self.ZOOM_DEFAULT
        self._cb_db: dict = {}
        self._cb_kleur: dict = {}
        self._cb_grootte: dict = {}
        self._cb_rating: dict = {}
        self._cb_dec: dict = {}
        self._build_ui()
        folder = db.get_setting('photo_folder', '')
        if folder:
            self._update_folder_label(folder)
            self._scan_folder(folder)

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Row 1: title / folder / search / buttons
        bar1 = QFrame()
        bar1.setFixedHeight(44)
        bar1.setStyleSheet("QFrame { background: #0d0d0d; border-bottom: 1px solid #1e1e1e; }")
        b1 = QHBoxLayout(bar1)
        b1.setContentsMargins(12, 0, 12, 0)
        b1.setSpacing(10)

        lbl_title = QLabel("ACTEURS")
        lbl_title.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px;")
        b1.addWidget(lbl_title)

        self.lbl_folder = QLabel("Geen map geselecteerd")
        self.lbl_folder.setStyleSheet("color: #383838; font-size: 10px;")
        b1.addWidget(self.lbl_folder)
        b1.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Zoeken...")
        self.search_input.setFixedWidth(180)
        self.search_input.textChanged.connect(self._apply_filters)
        b1.addWidget(self.search_input)

        btn_folder = QPushButton("📁  Kies map")
        btn_folder.setFixedHeight(28)
        btn_folder.clicked.connect(self._pick_folder)
        b1.addWidget(btn_folder)

        btn_import = QPushButton("⬆  Importeer")
        btn_import.setFixedHeight(28)
        btn_import.clicked.connect(self._import_actors)
        b1.addWidget(btn_import)

        btn_zoom_out = QPushButton("−")
        btn_zoom_out.setFixedSize(28, 28)
        btn_zoom_out.clicked.connect(self._zoom_out)
        b1.addWidget(btn_zoom_out)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setFixedSize(28, 28)
        btn_zoom_in.clicked.connect(self._zoom_in)
        b1.addWidget(btn_zoom_in)

        v.addWidget(bar1)

        # Filter sectie — 2 rijen checkboxes
        filter_frame = QFrame()
        filter_frame.setStyleSheet(
            "QFrame { background: #0a0a0a; border-bottom: 1px solid #161616; }"
            "QCheckBox { color: #777; font-size: 10px; spacing: 3px; }"
            "QCheckBox::indicator { width: 11px; height: 11px; }"
            "QCheckBox:checked { color: #e8b86d; }"
        )
        fv = QVBoxLayout(filter_frame)
        fv.setContentsMargins(12, 4, 12, 4)
        fv.setSpacing(4)

        row_a = QHBoxLayout()
        row_a.setSpacing(16)
        row_b = QHBoxLayout()
        row_b.setSpacing(16)

        # DB
        self._cb_db = self._cb_group(row_a, "Database:",
            [("in_db", "In DB"), ("not_in_db", "Niet in DB")])

        # Kleur
        self._cb_kleur = self._cb_group(row_a, "Kleur:",
            [("1", "Wit"), ("2", "Zwart"), ("3", "Bruin")])

        # Rating
        self._cb_rating = self._cb_group(row_a, "Rating:",
            [("9", "9●"), ("8", "8●"), ("7", "7●"), ("6", "6--"), ("5", "5--")])

        btn_reset = QPushButton("✕")
        btn_reset.setFixedSize(22, 22)
        btn_reset.setToolTip("Reset filters")
        btn_reset.clicked.connect(self._reset_filters)
        row_a.addStretch()
        row_a.addWidget(btn_reset)

        # Grootte
        self._cb_grootte = self._cb_group(row_b, "Grootte:",
            [(str(i), "★" * i) for i in range(1, 7)])

        # Decennia
        self._cb_dec = self._cb_group(row_b, "Decennia:",
            [(str(d), f"{d*10}s") for d in range(3, 10)] +
            [("0", "00s"), ("1", "10s"), ("2", "20s")])

        row_b.addStretch()

        fv.addLayout(row_a)
        fv.addLayout(row_b)
        v.addWidget(filter_frame)

        # Photo grid
        cw, ch = self.ZOOM_STEPS[self._zoom_idx]
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(1, 1))
        self.grid.setGridSize(QSize(cw + 8, ch + 8))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setStyleSheet(
            "QListWidget { background: #0a0a0a; border: none; padding: 10px; }"
            "QListWidget::item { border: none; background: transparent; }"
        )
        self._delegate = ActorCardDelegate()
        self.grid.setItemDelegate(self._delegate)
        self.grid.itemClicked.connect(self._on_item_clicked)
        v.addWidget(self.grid)

    def _cb_group(self, layout: QHBoxLayout, label: str,
                  options: list) -> dict:
        from PyQt6.QtWidgets import QCheckBox
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #444; font-size: 9px;")
        layout.addWidget(lbl)
        cbs = {}
        for val, text in options:
            cb = QCheckBox(text)
            cb.stateChanged.connect(self._apply_filters)
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

    def _scan_folder(self, folder):
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

            cw, ch = self.ZOOM_STEPS[self._zoom_idx]
            item = QListWidgetItem()
            item.setSizeHint(QSize(cw, ch))
            item.setData(Qt.ItemDataRole.UserRole, {
                'photo_path': str(photo_path),
                'stem': photo_path.stem,
                'actor': actor,
                'in_db': in_db,
                'meta': meta,
            })
            self.grid.addItem(item)
            self._all_items.append(item)

        self._apply_filters()

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

    # ── Zoom ─────────────────────────────────────

    def _zoom_in(self):
        if self._zoom_idx < len(self.ZOOM_STEPS) - 1:
            self._zoom_idx += 1
            self._apply_zoom()

    def _zoom_out(self):
        if self._zoom_idx > 0:
            self._zoom_idx -= 1
            self._apply_zoom()

    def _apply_zoom(self):
        cw, ch = self.ZOOM_STEPS[self._zoom_idx]
        self.grid.setGridSize(QSize(cw + 8, ch + 8))
        for item in self._all_items:
            item.setSizeHint(QSize(cw, ch))
        self._delegate._cache.clear()
        self.grid.update()

    def _on_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or not data.get('in_db'):
            return
        meta = data.get('meta', {})
        voornaam   = meta.get('voornaam', '')
        achternaam = meta.get('achternaam', '')
        full_name  = f"{voornaam} {achternaam}".strip() or data.get('stem', '')
        if full_name:
            QApplication.clipboard().setText(full_name)

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

