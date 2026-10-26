#!/usr/bin/env python3
"""
CineMarker — centrale lees/schrijf-laag voor marker-JSON-bestanden

Markers worden per video opgeslagen als verborgen JSON naast het bestand:
    <map>/.<bestandsnaam-zonder-extensie>_markers.json

ALLE code die markers leest of schrijft hoort via deze module te gaan, zodat
encoding (altijd UTF-8), bestandsnaam, atomair schrijven en DB-tellers op
één plek geregeld zijn.
"""

import json
import logging
import os
from pathlib import Path
from typing import Iterator

_log = logging.getLogger('cinemarker.markers')

MARKER_SUFFIX = '_markers.json'


def markers_file_for(video_path) -> Path:
    """Pad van het marker-JSON-bestand dat bij een video hoort."""
    p = Path(video_path)
    return p.parent / f".{p.stem}{MARKER_SUFFIX}"


def read_marker_file(marker_file) -> list:
    """Lees een marker-JSON-bestand direct (bv. uit iter_marker_files()).

    Geeft [] terug als het bestand niet bestaat of onleesbaar is;
    een fout wordt gelogd maar laat de app niet crashen.
    """
    mf = Path(marker_file)
    if not mf.exists():
        return []
    try:
        data = json.loads(mf.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        _log.warning('Marker-bestand onleesbaar: %s (%s)', mf, e)
        return []
    if not isinstance(data, list):
        _log.warning('Marker-bestand heeft onverwacht formaat: %s', mf)
        return []
    return data


def write_marker_file(marker_file, markers: list) -> bool:
    """Schrijf een marker-lijst naar een JSON-bestand (UTF-8, atomair).

    Schrijft eerst naar een tijdelijk bestand en vervangt daarna het origineel,
    zodat een crash halverwege nooit een half bestand achterlaat.
    Geeft True terug bij succes.
    """
    mf  = Path(marker_file)
    tmp = mf.with_name(mf.name + '.tmp')
    try:
        tmp.write_text(json.dumps(markers, ensure_ascii=False, indent=2),
                       encoding='utf-8')
        os.replace(tmp, mf)
        return True
    except OSError as e:
        _log.warning('Marker-bestand niet opgeslagen: %s (%s)', mf, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_markers(video_path) -> list:
    """Alle markers van een video. [] als er (nog) geen bestand is."""
    return read_marker_file(markers_file_for(video_path))


def save_markers(video_path, markers: list) -> bool:
    """Sla de markers van een video op en werk de DB-tellers bij.

    Geeft True terug als het bestand geschreven is.
    """
    ok = write_marker_file(markers_file_for(video_path), markers)
    if ok:
        _update_db_counts(str(video_path), markers)
    return ok


def _update_db_counts(video_path: str, markers: list) -> None:
    """Houd marker_count / neg_marker_count in de DB bij zodat de filmscan
    geen JSON hoeft te lezen. Lazy import: database.py importeert deze module."""
    try:
        import database as db
        total = len(markers)
        neg   = sum(1 for m in markers if m.get('negative'))
        db.update_film_marker_counts(video_path, total, neg)
    except Exception as e:
        _log.warning('DB-tellers niet bijgewerkt voor %s (%s)', video_path, e)


def iter_marker_files(folder) -> Iterator[Path]:
    """Alle marker-JSON-bestanden onder een map (recursief).

    Levert niets op als de map leeg of niet bereikbaar is.
    """
    if not folder:
        return
    try:
        yield from Path(folder).rglob(f'*{MARKER_SUFFIX}')
    except OSError as e:
        _log.warning('Filmmap niet doorzoekbaar: %s (%s)', folder, e)
