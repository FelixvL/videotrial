"""
CineMarker — alle mutaties op marker-JSON op één plek (geen Qt).

Elke functie leest, wijzigt, schrijft (via markers_io, dus UTF-8 + DB-tellers)
en geeft terug of er iets veranderd is. De aanroeper zendt daarna
`AppSignals.markers_changed(path)`. Ratings worden hier herberekend.
"""

from __future__ import annotations

import logging

from . import database as db
from .markers_io import load_markers, save_markers
from .models import Marker

_log = logging.getLogger("cinemarker.markers")

TIME_TOLERANCE = 0.01
RATING_CAP = 10


def markers(path: str) -> list[dict]:
    return load_markers(path)


def typed(path: str) -> list[Marker]:
    return [Marker.from_dict(m) for m in load_markers(path)]


def find_index(ms: list[dict], time_sec: float, index_hint: int | None = None) -> int:
    """Index van de marker op `time_sec` (met tolerantie). `index_hint` wint als
    die klopt, zodat twee markers op hetzelfde tijdstip uit elkaar blijven."""
    if index_hint is not None and 0 <= index_hint < len(ms):
        if abs(float(ms[index_hint].get("time") or 0) - time_sec) <= TIME_TOLERANCE:
            return index_hint
    for i, m in enumerate(ms):
        if abs(float(m.get("time") or 0) - time_sec) <= TIME_TOLERANCE:
            return i
    return -1


def recalc_rating(path: str, ms: list[dict] | None = None) -> float:
    """Som van de sterren van niet-negatieve markers, gemaximeerd op 10 → DB."""
    ms = load_markers(path) if ms is None else ms
    total = 0
    for m in ms:
        if m.get("negative"):
            continue
        try:
            total += int(m.get("stars") or 0)
        except (TypeError, ValueError):
            pass
    value = float(min(total, RATING_CAP))
    db.update_afgeleide_rating(path, value)
    return value


def _write(path: str, ms: list[dict]) -> bool:
    ms.sort(key=lambda m: float(m.get("time") or 0))
    ok = save_markers(path, ms)
    if ok:
        recalc_rating(path, ms)
    return ok


def add(path: str, marker: Marker) -> bool:
    ms = load_markers(path)
    ms.append(marker.to_dict())
    return _write(path, ms)


def update(path: str, time_sec: float, *, index_hint: int | None = None, actors: list[int] | None = None,
           categories: list[int] | None = None, stars: int | None = None, name: str | None = None) -> bool:
    ms = load_markers(path)
    i = find_index(ms, time_sec, index_hint)
    if i < 0:
        return False
    m = ms[i]
    if actors is not None:
        m["actors"] = [int(a) for a in actors]
    if categories is not None:
        m["categories"] = [int(c) for c in categories]
    if stars is not None:
        m["stars"] = int(stars) if int(stars) > 0 else None
    if name is not None:
        m["name"] = name
    return _write(path, ms)


def delete(path: str, time_sec: float, *, index_hint: int | None = None) -> bool:
    ms = load_markers(path)
    i = find_index(ms, time_sec, index_hint)
    if i < 0:
        return False
    del ms[i]
    return _write(path, ms)


def remove_actor(path: str, time_sec: float, actor_id: int, *, index_hint: int | None = None) -> bool:
    ms = load_markers(path)
    i = find_index(ms, time_sec, index_hint)
    if i < 0:
        return False
    actors = [int(a) for a in (ms[i].get("actors") or [])]
    if actor_id not in actors:
        return False
    ms[i]["actors"] = [a for a in actors if a != actor_id]
    return _write(path, ms)


def negative_zones(ms: list[dict], duration: float) -> list[tuple[float, float]]:
    """Zones (start, einde) van negatieve markers tot de volgende gewone marker of het einde."""
    ordered = sorted(ms, key=lambda m: float(m.get("time") or 0))
    zones: list[tuple[float, float]] = []
    for i, m in enumerate(ordered):
        if not m.get("negative"):
            continue
        start = float(m.get("time") or 0)
        end = duration
        for nxt in ordered[i + 1:]:
            if not nxt.get("negative"):
                end = float(nxt.get("time") or 0)
                break
        if end > start:
            zones.append((start, end))
    return zones
