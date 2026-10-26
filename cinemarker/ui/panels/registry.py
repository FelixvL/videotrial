"""
CineMarker — bouwt alle panelen en registreert ze in het hoofdvenster.

Elk paneel wordt pas geïmporteerd als het bestaat; zolang een paneel nog in
opbouw is, blijft de placeholder van het hoofdvenster staan.
"""

from __future__ import annotations

import logging

from ..actions import ActionRegistry
from ..main_window import MainWindow
from ..signals import AppSignals

_log = logging.getLogger("cinemarker.ui")


def build_all(win: MainWindow, signals: AppSignals, actions: ActionRegistry) -> None:
    from .films_panel import FilmsPanel

    def _films(w, s, a):
        p = FilmsPanel(s)
        p.play_requested.connect(lambda path: w.player.load_video(path))
        return p

    from .actors.actors_panel import ActorsPanel

    def _actors(w, s, a):
        p = ActorsPanel(s)
        p.open_film_requested.connect(lambda path: w.player.load_video(path))
        p.scene_jump_requested.connect(lambda path, t: w.player.jump_to(path, t))
        return p

    from .markers_panel import MarkersPanel

    def _markers(w, s, a):
        p = MarkersPanel(s)
        p.scene_jump_requested.connect(lambda path, t: w.player.jump_to(path, t))
        p.play_selection_requested.connect(lambda entries, iv: w.player.play_selection(entries, iv))
        p.edit_marker_requested.connect(lambda marker, path: w.player.edit_marker(marker, path))
        return p

    from .player.player_panel import PlayerPanel

    def _player(w, s, a):
        p = PlayerPanel(s, a, w)
        p.set_chrome([w.tabs.tabBar(), w.tabs.cornerWidget(), w.statusBar()])
        w.set_player(p)
        return p

    from .data_panel import DataPanel
    from .manage_panel import ManagePanel
    from .sorter_panel import SorterPanel

    def _data(w, s, a):
        p = DataPanel(s)
        p.play_file_requested.connect(lambda path: w.player.load_video(path))
        return p

    builders = [
        ("films", _films),
        ("actors", _actors),
        ("markers", _markers),
        ("player", _player),
        ("data", _data),
        ("manage", lambda w, s, a: ManagePanel(s)),
        ("sorter", lambda w, s, a: SorterPanel(s)),
    ]
    for key, build in builders:
        try:
            win.register_panel(key, build(win, signals, actions))
        except Exception:
            _log.exception("Paneel %s kon niet gebouwd worden", key)
