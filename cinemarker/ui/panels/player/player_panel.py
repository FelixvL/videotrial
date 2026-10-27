"""
CineMarker — Speler-paneel: werkbalk, video (mpv), tijdlijn en de overlays.

Publieke API (gebruikt door het hoofdvenster en het register):
  load_video(path, start=None) · jump_to(path, t) · play_selection(entries, interval)
  edit_marker(marker, path) · handle_action(key) · open_file() · toggle_fullscreen() · shutdown()
"""

from __future__ import annotations

import csv
import logging
import os
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ....core import database as db, markers_store, media, settings
from ....core.formats import fmt_hms, format_time
from ....core.markers_io import load_markers
from ....core.models import Marker, MarkerEntry
from ....core.paths import THUMBNAILS_DIR
from ....playback.controller import PlayerController, play_click, speed_label
from ...actions import ActionRegistry
from ...delegates.cat_icons import CategoryIcons
from ...signals import AppSignals
from ...theme import icons, tokens as T
from ...widgets.buttons import IconButton, ToolButton
from ...widgets.dialogs import confirm, warning
from ...widgets.overlay import Anchor, FloatingPanel, VideoAnchor
from ...widgets.search_field import SearchField
from ...workers import ThumbnailExportWorker, stop_worker
from .actor_link_panel import ActorLinkPanel
from .actors_overlay import FilmActorsOverlay
from .film_edit_panel import FilmEditPanel
from .fullscreen import FullscreenMode
from .marker_dialog import MarkerQuickDialog
from .marker_list import MarkerSidePanel
from .timeline import TimelineSlider

_log = logging.getLogger("cinemarker.player")

VIDEO_FILTER = ("Video (*.mp4 *.avi *.mov *.wmv *.mkv *.flv *.webm *.m4v *.mpg *.mpeg *.ts *.mts *.m2ts);;"
                "Alle bestanden (*)")


class _ClickFlash(FloatingPanel):
    """Korte ✓-flits in het midden van de video bij een klik (transparant tool-venster)."""

    SIZE = 72

    def __init__(self, video: QWidget) -> None:
        super().__init__(video, anchor=Anchor.CENTER, mode="tool_window", margin=0, click_through=True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def trigger(self) -> None:
        self._t0 = time.monotonic()
        self.show()
        self._timer.start()

    def _opacity(self) -> float:
        dt = time.monotonic() - self._t0
        if dt < 0.09:
            return 1.0
        return max(0.0, 1.0 - (dt - 0.09) / 0.28)

    def _tick(self) -> None:
        if self._opacity() <= 0:
            self._timer.stop()
            self.hide()
        else:
            self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._opacity())
        p.setPen(QPen(QColor(255, 255, 255, 90), 2))
        p.setBrush(QColor(0, 0, 0, 155))
        p.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
        p.drawPixmap(self.SIZE // 2 - 16, self.SIZE // 2 - 16, icons.pixmap("check", 32, T.WHITE))
        p.end()


class VideoWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, False)
        self.setStyleSheet(f"background:{T.VIDEO_BG};")
        self.setAutoFillBackground(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMinimumSize(320, 180)


class PlayerPanel(QWidget):
    def __init__(self, signals: AppSignals, actions: ActionRegistry, window, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        self.actions = actions
        self.win = window
        self.cat_icons = CategoryIcons()
        self.ctrl = PlayerController(self)
        self._path = ""
        self._film_id: int | None = None
        self._markers: list[dict] = []
        self._selection: list[MarkerEntry] = []
        self._row = -1
        self._auto_sec = 0
        self._updating_slider = False
        self._active = False
        self._drag: QPoint | None = None
        self._export_worker: ThumbnailExportWorker | None = None
        self._actor_cache: list[dict] = []
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._auto_advance)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._do_search)
        self.ctrl.skip_negative = settings.get_bool(settings.SKIP_NEGATIVE, True)

        self._build_ui()
        self._build_overlays()
        self._connect()

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setProperty("role", "toolbar")
        self.toolbar.setFixedHeight(T.TOOLBAR_H)
        self.toolbar.setStyleSheet(f"QWidget[role='toolbar'] {{ background:{T.SURFACE}; border-bottom:1px solid {T.BORDER_SOFT}; }}")
        th = QHBoxLayout(self.toolbar)
        th.setContentsMargins(T.SPACE_MD, 0, T.SPACE_MD, 0)
        th.setSpacing(T.SPACE_SM)
        self.btn_delete = IconButton("trash", "Huidige film verplaatsen naar map 'deleted'", variant="ghost")
        self.btn_delete.clicked.connect(self.delete_current_film)
        self.lbl_time = QLabel("--:--:-- / --:--:--")
        self.lbl_time.setProperty("role", "timecode")
        self.lbl_time.setMinimumWidth(150)
        self.btn_speed = ToolButton("1×", variant="ghost", tooltip="Snelheid:  [ langzamer   ] sneller   \\ of klik = 1×")
        self.btn_speed.setFixedWidth(64)
        self.btn_speed.clicked.connect(self.ctrl.reset_speed)
        self.btn_next = IconButton("skip-forward", "Volgende film in het Films-overzicht (V)")
        self.btn_next.clicked.connect(self.next_film)
        self.btn_skip = IconButton("ban", "Negatieve perioden overslaan (aan/uit)", checkable=True)
        self.btn_skip.setChecked(self.ctrl.skip_negative)
        self.btn_skip.toggled.connect(self._toggle_skip)
        self.lbl_film = QLabel("")
        self.lbl_film.setProperty("role", "muted")
        self.search = SearchField("Acteur zoeken…", debounce_ms=0, width=220)
        self.search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        for w in (self.btn_delete, self.lbl_time, self.btn_speed, self.btn_next, self.btn_skip):
            th.addWidget(w)
        th.addSpacing(T.SPACE_SM)
        th.addWidget(self.lbl_film, 1)
        th.addWidget(self.search)
        root.addWidget(self.toolbar)

        self.video = VideoWidget(self)
        self.video.installEventFilter(self)
        root.addWidget(self.video, 1)

        self.timeline = TimelineSlider()
        self.timeline.scrubbed.connect(lambda f: self._seek_frac(f, exact=False))
        self.timeline.released.connect(lambda f: self._seek_frac(f, exact=True))
        root.addWidget(self.timeline)

    def _build_overlays(self) -> None:
        self.anchor = VideoAnchor(self.video, self.window() or self)
        self.side = MarkerSidePanel(self.video, self.cat_icons)
        self.actors_overlay = FilmActorsOverlay(self.video)
        self.edit_panel = FilmEditPanel(self.video, self.cat_icons)
        self.link_panel = ActorLinkPanel(self.video)
        self.flash = _ClickFlash(self.video)
        for p in (self.side, self.actors_overlay, self.edit_panel, self.link_panel, self.flash):
            self.anchor.register(p)
            if p is not self.flash:
                self.actions.attach(p)          # sneltoetsen werken ook als een zwevend paneel focus heeft
        self.fullscreen = FullscreenMode(self.win, [], [self.side, self.actors_overlay, self.edit_panel, self.link_panel],
                                         self.video)
        self.anchor.register(self.fullscreen.close_btn)
        self.actions.attach(self.fullscreen.close_btn)
        self.fullscreen.close_btn.clicked.connect(self.fullscreen.exit)

    def set_chrome(self, widgets: list[QWidget]) -> None:
        """Widgets die in fullscreen verborgen worden (tabbalk, statusbalk, …)."""
        self.fullscreen._chrome = [self.toolbar, self.timeline] + list(widgets)

    def _connect(self) -> None:
        c = self.ctrl
        c.position_changed.connect(self._on_position)
        c.duration_known.connect(self._on_duration)
        c.speed_changed.connect(self._on_speed)
        c.zoom_changed.connect(lambda z: self.video.setCursor(
            Qt.CursorShape.OpenHandCursor if z > 0 else Qt.CursorShape.ArrowCursor))
        c.paused_changed.connect(self._on_paused)
        self.side.jump_requested.connect(self._jump_row)
        self.side.edit_requested.connect(self._edit_index)
        self.side.delete_requested.connect(self._delete_index)
        self.side.export_requested.connect(self.export_csv)
        self.side.actor_clicked.connect(self.link_actor)
        self.actors_overlay.marker_requested.connect(self._quick_marker_from_overlay)
        self.actors_overlay.thumbnail_requested.connect(self.capture_thumbnail)
        self.actors_overlay.edit_requested.connect(self.edit_panel.open_for_film)
        self.actors_overlay.category_deleted.connect(self.signals.taxonomy_changed)
        self.edit_panel.actors_changed.connect(self._on_film_actors_changed_local)
        self.edit_panel.thumbnails_changed.connect(lambda fid: self.actors_overlay.load_thumbnails(fid))
        self.edit_panel.markers_changed.connect(self.signals.markers_changed)
        self.edit_panel.film_cats_changed.connect(lambda _f: self.signals.taxonomy_changed.emit())
        self.link_panel.link_requested.connect(self.link_actor)
        s = self.signals
        s.markers_changed.connect(self._on_markers_changed)
        s.taxonomy_changed.connect(self._on_taxonomy_changed)
        s.actors_changed.connect(self._on_actors_changed)
        s.film_actors_changed.connect(self._on_film_actors_changed)
        s.film_thumbnail_changed.connect(lambda p, _t: self.actors_overlay.load_thumbnails(self._film_id)
                                         if self._film_id and p == self._path else None)

    # ── zichtbaarheid / koppeling ───────────────────────────────────────
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._attach)
        QTimer.singleShot(50, self.anchor.place_all)

    def _attach(self) -> None:
        self.ctrl.attach(int(self.video.winId()))
        self.anchor.place_all()

    def on_tab_active(self, active: bool) -> None:
        self._active = active
        if active:
            self.side.show_over_video()
            if self._path:
                self.actors_overlay.show_over_video()
            self.anchor.place_all()
            self.video.setFocus()
        else:
            for p in (self.side, self.actors_overlay, self.edit_panel, self.link_panel):
                p.hide()
            self.search.clear_silently()

    # ── laden ───────────────────────────────────────────────────────────
    def open_file(self) -> None:
        start = settings.get_str(settings.FILM_FOLDER, "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self.win, "Open video", start, VIDEO_FILTER)
        if path:
            self._clear_selection()
            self.load_video(path)

    def load_video(self, path: str, start_time: float | None = None, *, keep_selection: bool = False) -> None:
        if not keep_selection:
            self._clear_selection()
        path = os.path.normpath(path)        # QFileDialog geeft '/'-paden; DB en Films-paneel gebruiken '\\'
        self._path = path
        self._markers = load_markers(path)
        self.lbl_time.setText("--:--:-- / --:--:--")
        self.timeline.setValue(0)
        self.ctrl.load(path, start_time)
        if start_time:
            self.ctrl.seek_when_ready(start_time)
        film = db.get_or_create_film(path)
        self._film_id = film["id"] if film else None
        self.lbl_film.setText(Path(path).name)
        self.lbl_film.setToolTip(path)
        self.side.reload_actor_photos()
        self._refresh_markers_ui()
        self.actors_overlay.refresh(self._film_id)
        self.edit_panel.hide()
        self.link_panel.hide()
        self.win.setWindowTitle(f"CineMarker — {Path(path).name}")
        self.signals.say(f"{Path(path).name}  •  {path}", 6000)
        self.win.show_tab("player")
        self._suggest_actors(path)
        if self.fullscreen.active:
            for p in (self.side, self.actors_overlay):
                p.hide()

    def jump_to(self, path: str, time_sec: float) -> None:
        path = os.path.normpath(path)
        if path != self._path:
            self.load_video(path, start_time=time_sec, keep_selection=bool(self._selection))
        else:
            self.ctrl.seek(time_sec)
            self.win.show_tab("player")

    def play_selection(self, entries: list[MarkerEntry], interval_sec: int) -> None:
        if not entries:
            return
        self._selection = list(entries)          # volgorde = de gefilterde en gesorteerde weergave in Markers
        self._row = 0
        self._auto_sec = int(interval_sec)
        self.win.show_tab("player")
        first = self._selection[0]
        if self._path != first.film_path:
            self.load_video(first.film_path, start_time=first.time, keep_selection=True)
        else:
            self._refresh_markers_ui()
            self.ctrl.seek(first.time)
        self.side.show_search(False)
        self.side.show_over_video()
        self._restart_auto()

    def _clear_selection(self) -> None:
        self._selection = []
        self._row = -1
        self._auto_sec = 0
        self._auto_timer.stop()

    def next_film(self) -> None:
        films = self.win.panel("films")
        paths = films.visible_paths() if films is not None and hasattr(films, "visible_paths") else []
        if not paths:
            self.signals.say("Geen films in het Films-overzicht")
            return
        try:
            idx = paths.index(self._path)
        except ValueError:
            idx = -1
        self._clear_selection()
        self.load_video(paths[(idx + 1) % len(paths)])

    def delete_current_film(self) -> None:
        if not self._path:
            return
        name = Path(self._path).name
        if not confirm(self.win, "Film verplaatsen", f"'{name}' en bijbehorende markers verplaatsen naar de map 'deleted'?",
                       ok_text="Verplaatsen"):
            return
        path = self._path
        self.ctrl.stop()
        self._path = ""
        self._film_id = None
        self._markers = []
        self._clear_selection()
        ok, msg = media.move_film_to_deleted(path)
        if not ok:
            warning(self.win, "Verplaatsen mislukt", msg)
            return
        db.delete_film_by_path(path)
        self.signals.film_deleted.emit(path)
        self._refresh_markers_ui()
        self.actors_overlay.refresh(None)
        self.lbl_film.setText("")
        self.win.setWindowTitle("CineMarker")
        self.signals.say(f"'{name}' verplaatst naar 'deleted'")
        self.win.show_tab("films")

    # ── tijd / tijdlijn ─────────────────────────────────────────────────
    def _on_position(self, pos: float, dur: float) -> None:
        if dur > 0:
            self._updating_slider = True
            self.timeline.setValue(int(pos / dur * 10000))
            self._updating_slider = False
            self.lbl_time.setText(f"{fmt_hms(pos)} / {fmt_hms(dur)}")
        else:
            self.lbl_time.setText(f"{fmt_hms(pos)} / --:--:--")

    def _on_duration(self, path: str, dur: float) -> None:
        if path == self._path and self._film_id:
            db.set_film_duration(self._film_id, dur)
        self._refresh_zones()

    def _on_speed(self, speed: float) -> None:
        self.btn_speed.setText(speed_label(speed))
        color = T.BLUE if speed < 0 else (T.ACCENT if speed != 1.0 else T.TEXT_MUTED)
        self.btn_speed.setStyleSheet(f"color:{color}; font-family:{T.FONT_MONO}; font-weight:600;")

    def _on_paused(self, paused: bool) -> None:
        if self._auto_sec > 0 and self._selection:
            if paused:
                self._auto_timer.stop()
            else:
                self._restart_auto()

    def _seek_frac(self, frac: float, *, exact: bool) -> None:
        if self._path and self.ctrl.duration > 0 and not self._updating_slider:
            self.ctrl.seek(frac * self.ctrl.duration, exact=exact)

    def _toggle_skip(self, on: bool) -> None:
        self.ctrl.skip_negative = on
        settings.set_bool(settings.SKIP_NEGATIVE, on)
        self.signals.say(f"Negatieve perioden overslaan: {'aan' if on else 'uit'}")

    # ── markers ─────────────────────────────────────────────────────────
    def _refresh_markers_ui(self) -> None:
        if self._selection:
            self.side.set_selection(self._selection, self._row)
        else:
            self._row = -1
            self.side.set_markers([Marker.from_dict(m) for m in self._markers])
        self._refresh_zones()

    def _refresh_zones(self) -> None:
        dur = self.ctrl.duration
        zones = markers_store.negative_zones(self._markers, dur) if dur > 0 else []
        self.ctrl.neg_zones = zones
        if dur > 0:
            self.timeline.set_neg_zones([(s / dur, e / dur) for s, e in zones])
            self.timeline.set_marker_fracs([float(m.get("time") or 0) / dur for m in self._markers if not m.get("negative")])
        else:
            self.timeline.set_neg_zones([])
            self.timeline.set_marker_fracs([])

    def _on_markers_changed(self, path: str) -> None:
        if path == self._path:
            self._markers = load_markers(path)
            self._refresh_markers_ui()
        if self._selection:
            self._selection = [e for e in self._selection if e.film_path != path] + [
                MarkerEntry(marker=Marker.from_dict(m), film_path=path, film_name=Path(path).stem)
                for m in load_markers(path) if not m.get("negative") and m.get("actors")
            ] if path in {e.film_path for e in self._selection} else self._selection

    def _emit_marker_update(self, path: str) -> None:
        self.signals.markers_changed.emit(path)
        film = db.get_film(self._film_id) if self._film_id else None
        if film:
            self.signals.film_rating_changed.emit(path, float(film.get("afgeleide_rating") or 0))

    def _quick_marker_from_overlay(self, actors: list[dict], cats: list[dict]) -> None:
        self.add_marker(actors, cats, self.ctrl.position(), 0)

    def add_marker(self, actors: list[dict], cats: list[dict], pos: float, stars: int) -> None:
        if not self._path:
            return
        if not actors:
            self.signals.say("Selecteer eerst een acteur (linksonder)")
            return
        if not cats:
            self.signals.say("Kies een categorie")
            return
        name = ", ".join(c["name"] for c in cats) + " — " + ", ".join(a.get("name", "") for a in actors)
        markers_store.add(self._path, Marker.new(pos, name, [a["id"] for a in actors], [c["id"] for c in cats], stars))
        self._emit_marker_update(self._path)
        self.signals.say(f"Marker '{name}' op {format_time(pos)}")

    def open_marker_dialog(self) -> None:
        if not self._path:
            return
        actors = self.actors_overlay.selected_actors()
        if not actors:
            self.signals.say("Selecteer eerst een acteur (linksonder)")
            return
        cats = db.get_all_categories()
        if not cats:
            self.signals.say("Maak eerst een categorie aan in Beheer")
            return
        pos = self.ctrl.position()
        frame = self.ctrl.capture_pixmap()
        self.actions.set_enabled(False)
        try:
            dlg = MarkerQuickDialog(self.win, actors, pos, frame, cats, film_id=self._film_id,
                                    pos_fn=self.ctrl.position, frame_fn=self.ctrl.capture_pixmap)
            if dlg.exec():
                cat = dlg.result_category()
                if cat:
                    self.add_marker(dlg.result_actors(), [cat], dlg.result_pos(), dlg.result_stars())
                if dlg.wants_thumbnail():
                    self.capture_thumbnail()
        finally:
            self.actions.set_enabled(True)
            self.win.activateWindow()
            self.video.setFocus()

    def _edit_index(self, idx: int) -> None:
        if 0 <= idx < len(self._markers) and not self._markers[idx].get("negative"):
            self.edit_marker(self._markers[idx], self._path, index_hint=idx)

    def edit_marker(self, marker: dict | Marker, path: str, *, index_hint: int | None = None) -> None:
        m = marker.to_dict() if isinstance(marker, Marker) else dict(marker)
        actors = [a for a in (db.get_actor(int(aid)) for aid in (m.get("actors") or [])) if a]
        cats = db.get_all_categories()
        if not cats:
            self.signals.say("Maak eerst een categorie aan in Beheer")
            return
        film = db.get_or_create_film(path)
        cur_cat = (m.get("categories") or [None])[0]
        self.actions.set_enabled(False)
        try:
            dlg = MarkerQuickDialog(self.win, actors, float(m.get("time") or 0), None, cats,
                                    initial_stars=int(m.get("stars") or 0), editing=True, initial_cat_id=cur_cat,
                                    film_id=film["id"] if film else None)
            if not dlg.exec():
                return
            t = float(m.get("time") or 0)
            if dlg.was_deleted():
                if confirm(self.win, "Marker verwijderen", f"Marker op {format_time(t)} definitief verwijderen?"):
                    markers_store.delete(path, t, index_hint=index_hint)
            else:
                cat = dlg.result_category()
                markers_store.update(path, t, index_hint=index_hint, actors=[a["id"] for a in dlg.result_actors()],
                                     categories=[cat["id"]] if cat else None, stars=dlg.result_stars())
            self._emit_marker_update(path)
        finally:
            self.actions.set_enabled(True)

    def _delete_index(self, idx: int) -> None:
        if not (0 <= idx < len(self._markers)):
            return
        m = self._markers[idx]
        if not confirm(self.win, "Marker verwijderen", f"Negatieve marker op {fmt_hms(float(m.get('time') or 0))} verwijderen?"):
            return
        markers_store.delete(self._path, float(m.get("time") or 0), index_hint=idx)
        self._emit_marker_update(self._path)

    def add_negative_marker(self) -> None:
        if not self._path:
            return
        pos = self.ctrl.position()
        markers_store.add(self._path, Marker.new(pos, "SKIP", [], [], negative=True))
        if self.btn_skip.isChecked():
            self.btn_skip.setChecked(False)
        self._emit_marker_update(self._path)
        self.side.show_search(False)
        if not self.fullscreen.active:
            self.side.show_over_video()
        self.signals.say(f"Negatieve marker op {fmt_hms(pos)}  •  overslaan UIT")

    def rename_marker(self, idx: int) -> None:
        from ...widgets.dialogs import ask_text
        if not (0 <= idx < len(self._markers)):
            return
        m = self._markers[idx]
        name = ask_text(self.win, "Marker hernoemen", "Nieuwe naam:", m.get("name") or "")
        if name:
            markers_store.update(self._path, float(m.get("time") or 0), index_hint=idx, name=name)
            self._emit_marker_update(self._path)

    # ── navigatie door markers / afspeellijst ───────────────────────────
    def _jump_row(self, row: int) -> None:
        self._row = row
        if self._selection:
            if 0 <= row < len(self._selection):
                e = self._selection[row]
                self.side.set_current(row)
                self.jump_to(e.film_path, e.time)
        elif 0 <= row < len(self._markers):
            self.side.set_current(row)
            self.ctrl.seek(float(self._markers[row].get("time") or 0))
        self.video.setFocus()

    def _step_marker(self, direction: int) -> None:
        n = len(self._selection) if self._selection else len(self._markers)
        if not self._path or n == 0:
            return
        if not self.fullscreen.active:
            self.side.show_search(False)
            self.side.show_over_video()
        self._auto_timer.stop()
        self._jump_row((self._row + direction) % n)
        self._restart_auto()

    def _restart_auto(self) -> None:
        if self._auto_sec > 0 and self._selection:
            self._auto_timer.start(self._auto_sec * 1000)

    def _auto_advance(self) -> None:
        if self._selection:
            self._jump_row((self._row + 1) % len(self._selection))
            self._restart_auto()

    # ── acteurs ─────────────────────────────────────────────────────────
    def _suggest_actors(self, path: str) -> None:
        if not self._actor_cache:
            self._actor_cache = db.get_all_actors()
        matches = media.suggest_actors(path, self._actor_cache)
        if self.fullscreen.active:
            return
        if matches:
            self.side.reload_actor_photos()
            self.side.set_results(matches)
            self.side.show_search(True)
            self.side.show_over_video()
        else:
            self.side.show_search(False)

    def _do_search(self) -> None:
        text = self.search.text().strip().lower()
        if not text:
            self.side.show_search(False)
            return
        if not self._actor_cache:
            self._actor_cache = db.get_all_actors()
        counts = db.get_actor_film_counts_batch()
        hits = [a for a in self._actor_cache if text in (a.get("name") or "").lower()]
        hits.sort(key=lambda a: (-counts.get(a["id"], 0), (a.get("name") or "").lower()))
        self.side.reload_actor_photos()
        self.side.set_results(hits)
        self.side.show_search(True)
        self.side.show_over_video()

    def link_actor(self, actor: dict) -> None:
        if not self._path or not self._film_id:
            return
        db.link_actor_film(actor["id"], self._film_id)
        self._actor_cache = []
        self.actors_overlay.refresh(self._film_id, select_only=actor["id"])   # alleen de nieuwe acteur geselecteerd
        self.search.clear_silently()
        self.side.show_search(False)
        self.signals.film_actors_changed.emit(self._film_id)
        self.signals.say(f"{actor.get('name', '')} gekoppeld aan {Path(self._path).name}")

    def _on_film_actors_changed_local(self, film_id: int) -> None:
        self.actors_overlay.refresh(film_id, keep_selection=True)
        self.signals.film_actors_changed.emit(film_id)

    def _on_film_actors_changed(self, film_id: int) -> None:
        if film_id == self._film_id and self._active:
            self.actors_overlay.refresh(film_id, keep_selection=True)

    def _on_actors_changed(self) -> None:
        self._actor_cache = []
        self.side.reload_actor_photos()

    def _on_taxonomy_changed(self) -> None:
        self.cat_icons.reload()
        self.actors_overlay.reload_categories()
        self._refresh_markers_ui()

    # ── thumbnails ──────────────────────────────────────────────────────
    def capture_thumbnail(self) -> None:
        if not self._path or not self._film_id:
            return
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
        dest = str(THUMBNAILS_DIR / f"{self._film_id}_thumb_{int(time.time() * 1000)}.jpg")
        if not self.ctrl.screenshot_window(dest):
            self.signals.say("Thumbnail mislukt")
            return
        self._register_thumbnail(dest)

    def _register_thumbnail(self, dest: str) -> None:
        db.add_film_thumbnail(self._film_id, dest)
        db.set_film_thumbnail(self._film_id, dest)
        rec = db.get_bigfile_by_path(self._path)
        if rec:
            db.set_bigfile_thumbnail(rec["id"], dest)
        self.actors_overlay.load_thumbnails(self._film_id)
        self.signals.film_thumbnail_changed.emit(self._path, dest)
        self.signals.say(f"Thumbnail opgeslagen voor {Path(self._path).name}")

    def export_thumbnail(self) -> None:
        if not self._path:
            return
        pos = self.ctrl.position()
        default = str(Path(self._path).parent / f"thumb_{format_time(pos).replace(':', '-')}.jpg")
        dest, _ = QFileDialog.getSaveFileName(self.win, "Thumbnail opslaan", default, "JPEG (*.jpg)")
        if not dest:
            return
        stop_worker(self._export_worker)
        self._export_worker = ThumbnailExportWorker(self._path, pos, dest, self)
        self._export_worker.finished_ok.connect(lambda p: self.signals.say(f"Thumbnail opgeslagen: {p}"))
        self._export_worker.failed.connect(lambda e: self.signals.say(f"Thumbnail mislukt: {e}"))
        self._export_worker.start()

    def export_csv(self) -> None:
        if not self._path or not self._markers:
            return
        default = str(Path(self._path).with_suffix("")) + "_markers.csv"
        dest, _ = QFileDialog.getSaveFileName(self.win, "Markers exporteren", default, "CSV (*.csv)")
        if not dest:
            return
        try:
            with open(dest, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Tijdcode", "Seconden", "Naam", "Aangemaakt"])
                for m in self._markers:
                    t = float(m.get("time") or 0)
                    w.writerow([format_time(t), f"{t:.3f}", m.get("name") or "", m.get("created") or ""])
            self.signals.say(f"Markers geëxporteerd naar {dest}")
        except OSError as e:
            warning(self.win, "Export mislukt", str(e))

    # ── fullscreen / acties ─────────────────────────────────────────────
    def toggle_fullscreen(self) -> None:
        self.win.show_tab("player")
        self.fullscreen.toggle()
        QTimer.singleShot(100, self.anchor.place_all)
        self.video.setFocus()

    def handle_action(self, key: str) -> None:
        c = self.ctrl
        if key == "ontsnappen":
            if self.fullscreen.active:
                self.fullscreen.exit()
            elif self.link_panel.isVisible():
                self.link_panel.hide()
            elif self.edit_panel.isVisible():
                self.edit_panel.hide()
            else:
                self.search.clear_silently()
                self.side.show_search(False)
            self.video.setFocus()
            return
        if self.win.current_tab_key() != "player" and key not in ("volgende_film",):
            return
        dispatch = {
            "play_pause": c.toggle_play,
            "marker": self.open_marker_dialog,
            "neg_marker": self.add_negative_marker,
            "thumbnail": self.export_thumbnail,
            "volgende_film": self.next_film,
            "marker_voor": lambda: self._step_marker(1),
            "marker_achter": lambda: self._step_marker(-1),
            "skip_voor": lambda: c.tap_seek(1),
            "skip_achter": lambda: c.tap_seek(-1),
            "sneller": c.speed_up,
            "langzamer": c.speed_down,
            "reset_speed": c.reset_speed,
            "zoom_in": c.zoom_in,
            "zoom_uit": c.zoom_out,
            "zoom_reset": c.reset_zoom,
            "acteurs_tonen": lambda: self.link_panel.open() if self._path else None,
            "begin": c.go_to_start,
            "einde": c.go_to_end,
            "links": lambda: c.seek_relative(-5),
            "rechts": lambda: c.seek_relative(5),
        }
        fn = dispatch.get(key)
        if fn:
            fn()

    # ── muis op de video ────────────────────────────────────────────────
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.video:
            t = event.type()
            if t == QEvent.Type.MouseButtonPress:
                self.video.setFocus(Qt.FocusReason.MouseFocusReason)
                if event.button() == Qt.MouseButton.LeftButton:
                    play_click()
                    self.flash.trigger()
                    if self.ctrl.zoom > 0:
                        self._drag = event.position().toPoint()
                        self.video.setCursor(Qt.CursorShape.ClosedHandCursor)
                        return True
            elif t == QEvent.Type.MouseMove and self._drag is not None:
                p = event.position().toPoint()
                d = p - self._drag
                self._drag = p
                self.ctrl.pan_by(d.x() / max(1, self.video.width()), d.y() / max(1, self.video.height()))
                return True
            elif t == QEvent.Type.MouseButtonRelease and self._drag is not None:
                self._drag = None
                self.video.setCursor(Qt.CursorShape.OpenHandCursor)
                return True
            elif t == QEvent.Type.MouseButtonDblClick and self.ctrl.zoom > 0:
                self.ctrl.reset_zoom()
                return True
        return super().eventFilter(obj, event)

    def shutdown(self) -> None:
        stop_worker(self._export_worker)
        self.ctrl.terminate()
