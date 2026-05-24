#!/usr/bin/env python3
"""
CineMarker — Acteurs module
Acteursbeheer, filmkoppelingen, scène-editor
"""

import os
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QSplitter, QLineEdit,
    QFileDialog, QMessageBox, QInputDialog, QScrollArea,
    QFrame, QGridLayout, QTextEdit, QDialog, QDialogButtonBox,
    QProgressBar, QCheckBox, QSizePolicy, QStackedWidget
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QPixmap, QFont, QIcon

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
#  Photo Grid Widget
# ─────────────────────────────────────────────

class PhotoGrid(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(6)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self.setWidget(self._container)
        self._photos = []

    def set_photos(self, photo_records):
        # Clear
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._photos = photo_records

        cols = 3
        for i, photo in enumerate(photo_records):
            frame = QFrame()
            frame.setStyleSheet("""
                QFrame { background: #1a1a1a; border-radius: 4px; border: 1px solid #2a2a2a; }
                QFrame:hover { border-color: #e8b86d; }
            """)
            v = QVBoxLayout(frame)
            v.setContentsMargins(4, 4, 4, 4)
            v.setSpacing(2)

            lbl = QLabel()
            lbl.setFixedSize(100, 120)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("border: none;")

            path = photo['photo_path']
            if os.path.exists(path):
                pix = QPixmap(path).scaled(100, 120,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                lbl.setPixmap(pix)
            else:
                lbl.setText("?")
                lbl.setStyleSheet("color: #555; font-size: 24px; border: none;")

            v.addWidget(lbl)
            name_lbl = QLabel(Path(path).stem[:14])
            name_lbl.setStyleSheet("color: #888; font-size: 9px; border: none;")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(name_lbl)

            self._grid.addWidget(frame, i // cols, i % cols)

        # Fill remaining cells
        remainder = len(photo_records) % cols
        if remainder:
            for j in range(cols - remainder):
                spacer = QWidget()
                self._grid.addWidget(spacer, len(photo_records) // cols, remainder + j)


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

        # Photos
        photos_frame = QFrame()
        photos_frame.setStyleSheet("QFrame { background: #111; border-radius: 6px; border: 1px solid #1e1e1e; }")
        photos_v = QVBoxLayout(photos_frame)
        photos_v.setContentsMargins(8, 8, 8, 8)
        photos_v.setSpacing(4)

        ph_header = QHBoxLayout()
        ph_lbl = QLabel("FOTO'S")
        ph_lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 3px;")
        ph_header.addWidget(ph_lbl)
        ph_header.addStretch()
        btn_add_photos = QPushButton("+ Foto's")
        btn_add_photos.setFixedHeight(24)
        btn_add_photos.clicked.connect(self._add_photos)
        ph_header.addWidget(btn_add_photos)
        btn_add_folder = QPushButton("+ Map")
        btn_add_folder.setFixedHeight(24)
        btn_add_folder.clicked.connect(self._add_photo_folder)
        ph_header.addWidget(btn_add_folder)
        photos_v.addLayout(ph_header)

        self.photo_grid = PhotoGrid()
        self.photo_grid.setMinimumHeight(160)
        photos_v.addWidget(self.photo_grid)

        top_h.addWidget(photos_frame, stretch=3)

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
        self._refresh_photos()
        self._refresh_films()
        self._refresh_scenes()

    def _refresh_photos(self):
        if not self._actor:
            return
        photos = db.get_actor_photos(self._actor['id'])
        self.photo_grid.set_photos(photos)

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
            self.photo_grid.set_photos([])
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
#  Main Actors Panel (list + detail)
# ─────────────────────────────────────────────

class ActorsPanel(QWidget):
    open_film_requested = pyqtSignal(str)
    scene_jump_requested = pyqtSignal(str, float)

    def __init__(self, player):
        super().__init__()
        self.player = player
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # Left: actor list
        left = QFrame()
        left.setFixedWidth(220)
        left.setStyleSheet("QFrame { background: #0a0a0a; border-right: 1px solid #1e1e1e; }")
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(8, 8, 8, 8)
        left_v.setSpacing(6)

        lbl = QLabel("ACTEURS")
        lbl.setStyleSheet("color: #555; font-size: 10px; letter-spacing: 4px; padding: 4px 0;")
        left_v.addWidget(lbl)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Zoeken...")
        self.search_input.textChanged.connect(self._filter_list)
        left_v.addWidget(self.search_input)

        self.actor_list = QListWidget()
        self.actor_list.setStyleSheet("""
            QListWidget { background: transparent; border: none; }
            QListWidget::item { padding: 8px 6px; border-bottom: 1px solid #111; }
            QListWidget::item:hover { background: #141414; }
            QListWidget::item:selected { background: #1e1600; color: #e8b86d; border-left: 3px solid #e8b86d; }
        """)
        self.actor_list.currentItemChanged.connect(self._on_actor_selected)
        left_v.addWidget(self.actor_list)

        btn_new = QPushButton("＋  Nieuwe acteur")
        btn_new.setObjectName("accent")
        btn_new.clicked.connect(self._new_actor)
        left_v.addWidget(btn_new)

        h.addWidget(left)

        # Right: detail
        self.detail = ActorDetailPanel(self.player)
        self.detail.open_film_requested.connect(self.open_film_requested)
        self.detail.scene_jump_requested.connect(self.scene_jump_requested)
        h.addWidget(self.detail, stretch=1)

    def _refresh_list(self):
        self._filter_list(self.search_input.text())

    def _filter_list(self, query=""):
        self.actor_list.clear()
        actors = db.get_all_actors()
        for a in actors:
            if query.lower() in a['name'].lower():
                item = QListWidgetItem(f"  {a['name']}")
                item.setData(Qt.ItemDataRole.UserRole, a)
                self.actor_list.addItem(item)

    def _on_actor_selected(self, item):
        if item:
            actor = item.data(Qt.ItemDataRole.UserRole)
            self.detail.load_actor(actor)

    def _new_actor(self):
        name, ok = QInputDialog.getText(self, "Nieuwe acteur", "Naam van de acteur:")
        if ok and name.strip():
            db.create_actor(name.strip())
            self._refresh_list()
            # Select new actor
            for i in range(self.actor_list.count()):
                if self.actor_list.item(i).data(Qt.ItemDataRole.UserRole)['name'] == name.strip():
                    self.actor_list.setCurrentRow(i)
                    break

    def refresh(self):
        self._refresh_list()
