"""
CineMarker — getypte toegang tot de settings-tabel.

De sleutels zijn ongewijzigd t.o.v. de oude GUI zodat bestaande voorkeuren
(mappen, zoomniveaus, sneltoetsen, skip-negatief) blijven werken.
"""

from __future__ import annotations

import json
import logging

from . import database as db

_log = logging.getLogger("cinemarker.settings")

# ── Sleutels ────────────────────────────────────────────────────────────────
FILM_FOLDER = "film_folder"
PHOTO_FOLDER = "photo_folder"
SORTER_FOLDER = "sorter_folder"
DATA_PANEL_FOLDER = "data_panel_folder"
SKIP_NEGATIVE = "skip_negative"
BIGFILES_BLOCKED = "bigfiles_blocked"

ZOOM_FILMS = "zoom_films_panel"
ZOOM_ACTORS = "zoom_actors_panel"
ZOOM_DETAIL_FILMS = "zoom_detail_films"
ZOOM_DETAIL_MARKERS = "zoom_detail_markers"
ZOOM_MARKERS = "zoom_markers_panel"          # nieuw

WINDOW_GEOMETRY = "window_geometry"          # nieuw: "x,y,w,h" of "max"
LAST_TAB = "last_tab"                        # nieuw: tab-sleutel


def shortcut_key(action: str) -> str:
    return f"shortcut_{action}"


# ── Generiek ────────────────────────────────────────────────────────────────

def get_str(key: str, default: str = "") -> str:
    value = db.get_setting(key, default)
    return default if value is None else str(value)


def set_str(key: str, value: str) -> None:
    db.set_setting(key, value)


def get_int(key: str, default: int = 0) -> int:
    raw = db.get_setting(key, None)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        _log.warning("Setting %s heeft geen int-waarde: %r", key, raw)
        return default


def set_int(key: str, value: int) -> None:
    db.set_setting(key, str(int(value)))


def get_bool(key: str, default: bool = False) -> bool:
    raw = db.get_setting(key, None)
    if raw in (None, ""):
        return default
    return str(raw) in ("1", "true", "True")


def set_bool(key: str, value: bool) -> None:
    db.set_setting(key, "1" if value else "0")


def get_json(key: str, default):
    raw = db.get_setting(key, None)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        _log.warning("Setting %s bevat geen geldige JSON", key)
        return default


def set_json(key: str, value) -> None:
    db.set_setting(key, json.dumps(value, ensure_ascii=False))


# ── Sneltoetsen ─────────────────────────────────────────────────────────────

def get_shortcut(action: str, default: str) -> str:
    """Komma-gescheiden toetsenlijst voor een actie; lege string = uitgeschakeld."""
    raw = db.get_setting(shortcut_key(action), None)
    return default if raw is None else str(raw)


def set_shortcut(action: str, keys: str) -> None:
    db.set_setting(shortcut_key(action), keys.strip())


# ── Venster ─────────────────────────────────────────────────────────────────

def get_window_geometry() -> tuple[int, int, int, int] | str | None:
    raw = get_str(WINDOW_GEOMETRY, "")
    if raw == "max":
        return "max"
    try:
        x, y, w, h = (int(p) for p in raw.split(","))
        return x, y, w, h
    except ValueError:
        return None


def set_window_geometry(maximized: bool, x: int, y: int, w: int, h: int) -> None:
    set_str(WINDOW_GEOMETRY, "max" if maximized else f"{x},{y},{w},{h}")
