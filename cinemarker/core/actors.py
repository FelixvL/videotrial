"""CineMarker — acteurs scannen vanuit de fotomap (geen Qt; draait in een worker-thread)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import database as db
from .markers_io import iter_marker_files, read_marker_file
from .media import is_photo
from .models import ActorCard, ActorMeta

_log = logging.getLogger("cinemarker.actors")


def notes_of(actor: dict | None) -> dict:
    if not actor or not actor.get("notes"):
        return {}
    try:
        d = json.loads(actor["notes"])
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def batch_marker_counts(film_folder: str) -> dict[int, int]:
    """{actor_id: aantal markers} door alle marker-JSON's in de filmmap één keer te lezen."""
    counts: dict[int, int] = {}
    for mf in iter_marker_files(film_folder):
        for m in read_marker_file(mf):
            for aid in m.get("actors") or []:
                try:
                    counts[int(aid)] = counts.get(int(aid), 0) + 1
                except (TypeError, ValueError):
                    continue
    return counts


def scan_actor_photos(photo_folder: str, film_folder: str, stop=lambda: False) -> list[ActorCard]:
    """Alle foto's in de map (niet-recursief) gekoppeld aan DB-acteurs op naam == stam."""
    folder = Path(photo_folder) if photo_folder else None
    if folder is None or not folder.is_dir():
        return []
    try:
        photos = sorted((f for f in folder.iterdir() if is_photo(f)), key=lambda f: f.stem.lower())
    except OSError as e:
        _log.warning("Fotomap niet leesbaar: %s", e)
        return []
    by_name = {a["name"]: a for a in db.get_all_actors()}
    film_counts = db.get_actor_film_counts_batch()
    marker_counts = batch_marker_counts(film_folder)
    cards: list[ActorCard] = []
    for p in photos:
        if stop():
            return []
        actor = by_name.get(p.stem)
        aid = actor["id"] if actor else None
        cards.append(ActorCard(
            stem=p.stem, photo_path=str(p), actor_id=aid,
            meta=ActorMeta.from_notes(notes_of(actor)),
            film_count=film_counts.get(aid, 0) if aid else 0,
            marker_count=marker_counts.get(aid, 0) if aid else 0,
        ))
    return cards


def actors_without_first_name() -> list[tuple[dict, dict]]:
    out = []
    for a in db.get_all_actors():
        meta = notes_of(a)
        if not (meta.get("voornaam") or "").strip():
            out.append((a, meta))
    out.sort(key=lambda t: (t[0].get("name") or "").lower())
    return out


def split_name(text: str, cursor: int, photo_exts: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
               ) -> tuple[str, str] | None:
    """Alles vóór de cursor = voornaam, erna = achternaam (zonder foto-extensie). None als voornaam leeg."""
    voornaam = text[:cursor].strip()
    achternaam = text[cursor:].strip()
    for ext in photo_exts:
        if achternaam.lower().endswith(ext):
            achternaam = achternaam[: -len(ext)].strip()
            break
    if not voornaam:
        return None
    return voornaam, achternaam
