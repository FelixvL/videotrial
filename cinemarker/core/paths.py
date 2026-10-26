#!/usr/bin/env python3
"""
CineMarker — centrale paaddefinities

Alle data die je moet kopiëren/backuppen staat in TE_KOPIEREN/.
De code (git repo) staat ernaast en is volledig apart.

Tests kunnen de datamap verleggen met de omgevingsvariabele
CINEMARKER_DATA_ROOT (moet gezet zijn vóór deze module geïmporteerd wordt).
"""

from __future__ import annotations

import os
from pathlib import Path

# Root van de applicatie (de map met main.py); dit bestand staat in cinemarker/core/
APP_ROOT = Path(__file__).resolve().parents[2]

# Alles wat je moet kopiëren bij verhuizen naar een andere computer
_env_root = os.environ.get('CINEMARKER_DATA_ROOT')
DATA_ROOT = Path(_env_root).resolve() if _env_root else APP_ROOT / 'TE_KOPIEREN'

# Sub-mappen binnen TE_KOPIEREN
DB_PATH        = DATA_ROOT / 'cinemarker.db'
ACTEURFOTOS_DIR = DATA_ROOT / 'acteurfotos'
THUMBNAILS_DIR      = DATA_ROOT / 'thumbnails'
MARKER_THUMBS_DIR   = THUMBNAILS_DIR / 'markers'   # gedeeld door markers-paneel én acteurs-detail
ICONEN_DIR          = DATA_ROOT / 'iconen'          # categorie-iconen (gekozen via bestandsdialoog)

# Lokale schaalcache — weggooimap, NIET kopiëren (snel te herberekenen)
_SCALED_CACHE_ROOT     = APP_ROOT / 'scaled_cache'
SCALED_FILM_THUMBS_DIR = _SCALED_CACHE_ROOT / 'film'
SCALED_ACTOR_GRID_DIR  = _SCALED_CACHE_ROOT / 'actor_grid'
SCALED_ACTOR_CARDS_DIR = _SCALED_CACHE_ROOT / 'actor_cards'

# Grote bestanden — thumbnails voor bestanden die MEESTAL niet toegankelijk zijn
# Staat in de app-root (naast player.py), NIET in TE_KOPIEREN
BIGFILES_DIR = APP_ROOT / '_BIGFILES'


def ensure_data_dirs():
    """Maak alle datamappen aan als ze nog niet bestaan."""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    ACTEURFOTOS_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    MARKER_THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    ICONEN_DIR.mkdir(parents=True, exist_ok=True)
    SCALED_FILM_THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    SCALED_ACTOR_GRID_DIR.mkdir(parents=True, exist_ok=True)
    SCALED_ACTOR_CARDS_DIR.mkdir(parents=True, exist_ok=True)
    BIGFILES_DIR.mkdir(parents=True, exist_ok=True)


def ensure_volume_id(film_folder: str) -> str:
    """
    Zorg dat de filmmap een .cinedata/volume.id heeft.
    Maakt de map + bestand aan als dat nog niet bestaat.
    Geeft de UUID terug.
    """
    import uuid
    cinedata = Path(film_folder) / '.cinedata'
    try:
        cinedata.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ''   # schijf niet beschikbaar — geen volume-ID, geen crash
    vol_file = cinedata / 'volume.id'
    try:
        if not vol_file.exists():
            vol_file.write_text(str(uuid.uuid4()), encoding='utf-8')
        return vol_file.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def migrate_legacy_data():
    """
    Eenmalige migratie van de oude locaties naar TE_KOPIEREN/.

    Oude locaties:
      ~/.cinemarker.db
      <app_root>/acteurfotos/
      <app_root>/thumbnails/

    Draait alleen als TE_KOPIEREN/ nog niet bestaat.
    """
    if DATA_ROOT.exists():
        return   # al gemigreerd of nieuw — niets te doen

    import shutil

    DATA_ROOT.mkdir(exist_ok=True)

    # 1. Database
    old_db = Path.home() / '.cinemarker.db'
    if old_db.exists() and not DB_PATH.exists():
        shutil.move(str(old_db), str(DB_PATH))

    # 2. Acteurfotos
    old_fotos = APP_ROOT / 'acteurfotos'
    if old_fotos.exists() and not ACTEURFOTOS_DIR.exists():
        shutil.move(str(old_fotos), str(ACTEURFOTOS_DIR))
    else:
        ACTEURFOTOS_DIR.mkdir(exist_ok=True)

    # 3. Thumbnails
    old_thumbs = APP_ROOT / 'thumbnails'
    if old_thumbs.exists() and not THUMBNAILS_DIR.exists():
        shutil.move(str(old_thumbs), str(THUMBNAILS_DIR))
    else:
        THUMBNAILS_DIR.mkdir(exist_ok=True)
        (THUMBNAILS_DIR / 'markers').mkdir(exist_ok=True)

    # 4. Vul marker-tellingen in DB vanuit bestaande JSON-bestanden (eenmalig)
    try:
        from . import database as _db
        _db.init_db()
        film_folder = _db.get_setting('film_folder', '')
        if film_folder:
            _db.populate_marker_counts_from_json(film_folder)
    except Exception:
        pass
