#!/usr/bin/env python3
"""
CineMarker Database — SQLite layer voor acteurs, films en scènes
"""

import sqlite3
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

from paths import DB_PATH, ACTEURFOTOS_DIR

_log = logging.getLogger('cinemarker.db')


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # Performance-instellingen — eenmalig per verbinding
    conn.execute("PRAGMA foreign_keys  = ON")
    conn.execute("PRAGMA journal_mode  = WAL")       # concurrent reads zonder lock
    conn.execute("PRAGMA synchronous   = NORMAL")    # veilig maar sneller dan FULL
    conn.execute("PRAGMA cache_size    = -32000")    # 32 MB page cache in geheugen
    conn.execute("PRAGMA mmap_size     = 268435456") # 256 MB memory-mapped I/O
    conn.execute("PRAGMA temp_store    = MEMORY")    # tijdelijke tabellen in RAM
    return conn


@contextmanager
def _db():
    """Context manager die een verbinding opent en altijd sluit.

    Gebruik dit voor nieuwe/gewijzigde functies zodat verbindingen nooit
    lekken — ook niet bij een onverwachte uitzondering.

        with _db() as conn:
            return [dict(r) for r in conn.execute('SELECT ...').fetchall()]

    Voor schrijf-operaties doet de aanroeper zelf conn.commit() zodat het
    commit-moment expliciet zichtbaar blijft in de code.
    """
    conn = get_connection()
    try:
        yield conn
    except Exception:
        _log.exception('DB-fout')
        raise
    finally:
        conn.close()


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS actors (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL,
            notes   TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS actor_photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id    INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
            photo_path  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS films (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            file_path   TEXT NOT NULL UNIQUE,
            notes       TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS scenes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            film_id     INTEGER NOT NULL REFERENCES films(id) ON DELETE CASCADE,
            title       TEXT NOT NULL,
            start_time  REAL NOT NULL,
            end_time    REAL NOT NULL,
            notes       TEXT DEFAULT '',
            export_path TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS scene_actors (
            scene_id    INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
            actor_id    INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
            PRIMARY KEY (scene_id, actor_id)
        );

        CREATE TABLE IF NOT EXISTS actor_films (
            actor_id    INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
            film_id     INTEGER NOT NULL REFERENCES films(id) ON DELETE CASCADE,
            PRIMARY KEY (actor_id, film_id)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL,
            icon_path TEXT DEFAULT ''
        );
    """)

    # Migration: thumbnail column
    try:
        c.execute("ALTER TABLE films ADD COLUMN thumbnail TEXT DEFAULT ''")
    except Exception:
        pass

    # Migration: duration column (seconds, float)
    try:
        c.execute("ALTER TABLE films ADD COLUMN duration REAL DEFAULT 0")
    except Exception:
        pass

    # Migration: cached marker counts (avoids reading JSON files on every scan)
    try:
        c.execute("ALTER TABLE films ADD COLUMN marker_count     INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE films ADD COLUMN neg_marker_count INTEGER DEFAULT 0")
    except Exception:
        pass

    # Migration: cached file stats (avoids fp.stat() on external SSD on every scan)
    try:
        c.execute("ALTER TABLE films ADD COLUMN file_size  INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE films ADD COLUMN file_mtime REAL    DEFAULT 0")
    except Exception:
        pass

    # Migration: film_thumbnails table (multiple thumbnails per film)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS film_thumbnails (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            film_id INTEGER NOT NULL REFERENCES films(id) ON DELETE CASCADE,
            path    TEXT NOT NULL
        );
    """)
    # Back-fill existing single thumbnails into the new table
    try:
        rows = c.execute(
            "SELECT id, thumbnail FROM films WHERE thumbnail != '' AND thumbnail IS NOT NULL"
        ).fetchall()
        for row in rows:
            exists = c.execute(
                "SELECT id FROM film_thumbnails WHERE film_id=? AND path=?",
                (row['id'], row['thumbnail'])
            ).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO film_thumbnails (film_id, path) VALUES (?, ?)",
                    (row['id'], row['thumbnail'])
                )
    except Exception:
        pass

    # Migration: film publicatiedatum + afgeleide_rating
    try:
        c.execute("ALTER TABLE films ADD COLUMN publicatiedatum   TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE films ADD COLUMN afgeleide_rating  REAL DEFAULT 0")
    except Exception:
        pass

    # Migration: categories parent_id (voor subcategorieën)
    try:
        c.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id)")
    except Exception:
        pass

    # Migration: actor_kleuren lookup (seeded met de legacy waarden 1/2/3)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS actor_kleuren (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            naam TEXT NOT NULL,
            hex  TEXT DEFAULT ''
        );
    """)
    if not c.execute("SELECT COUNT(*) FROM actor_kleuren").fetchone()[0]:
        c.executemany("INSERT INTO actor_kleuren (id, naam, hex) VALUES (?, ?, ?)", [
            (1, 'Wit',   '#ffffff'),
            (2, 'Zwart', '#000000'),
            (3, 'Bruin', '#8b4513'),
        ])

    # Migration: actor trait types + actor↔trait koppeltabel
    c.executescript("""
        CREATE TABLE IF NOT EXISTS actor_trait_types (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            naam TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'beide'
        );
        CREATE TABLE IF NOT EXISTS actor_traits (
            actor_id INTEGER NOT NULL REFERENCES actors(id)            ON DELETE CASCADE,
            trait_id INTEGER NOT NULL REFERENCES actor_trait_types(id) ON DELETE CASCADE,
            PRIMARY KEY (actor_id, trait_id)
        );
    """)
    # Migration: waarden 'sterk'→'positief', 'zwak'→'negatief' (eenmalig)
    try:
        c.execute("UPDATE actor_trait_types SET type='positief' WHERE type='sterk'")
        c.execute("UPDATE actor_trait_types SET type='negatief' WHERE type='zwak'")
    except Exception:
        pass

    # Migration: film categorie types + film↔categorie koppeltabel
    c.executescript("""
        CREATE TABLE IF NOT EXISTS film_categorie_types (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            naam      TEXT NOT NULL,
            icon_path TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS film_categorie_links (
            film_id      INTEGER NOT NULL REFERENCES films(id)               ON DELETE CASCADE,
            categorie_id INTEGER NOT NULL REFERENCES film_categorie_types(id) ON DELETE CASCADE,
            PRIMARY KEY (film_id, categorie_id)
        );
    """)
    # Migration: icon_path on film_categorie_types (voor bestaande databases)
    try:
        c.execute("ALTER TABLE film_categorie_types ADD COLUMN icon_path TEXT DEFAULT ''")
    except Exception:
        pass

    # Migration: film filter presets
    c.executescript("""
        CREATE TABLE IF NOT EXISTS film_filter_presets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            filter_json TEXT NOT NULL DEFAULT '{}'
        );
    """)

    c.executescript("""
        CREATE TABLE IF NOT EXISTS bigfiles (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            full_path      TEXT    NOT NULL UNIQUE,
            thumbnail_path TEXT,
            last_seen      TEXT,
            created_at     TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bigfiles_acteurs (
            bigfile_id  INTEGER NOT NULL REFERENCES bigfiles(id) ON DELETE CASCADE,
            acteur_id   INTEGER NOT NULL REFERENCES actors(id)   ON DELETE CASCADE,
            PRIMARY KEY (bigfile_id, acteur_id)
        );
    """)

    conn.commit()
    conn.close()


# ── Settings ──────────────────────────────────

def get_setting(key, default=None):
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


# ── Actors ────────────────────────────────────

def get_actor_by_name(name):
    conn = get_connection()
    row = conn.execute("SELECT * FROM actors WHERE name=?", (name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def import_actors_from_records(records):
    conn = get_connection()
    inserted = 0
    updated = 0
    for r in records:
        name = (r.get('name') or '').strip()
        if not name:
            continue
        notes = r.get('notes', '')
        existing = conn.execute("SELECT id FROM actors WHERE name=?", (name,)).fetchone()
        if existing:
            conn.execute("UPDATE actors SET notes=? WHERE id=?", (notes, existing['id']))
            updated += 1
        else:
            conn.execute("INSERT INTO actors (name, notes) VALUES (?, ?)", (name, notes))
            inserted += 1
    conn.commit()
    conn.close()
    return inserted, updated


def create_actor(name, notes=''):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO actors (name, notes) VALUES (?, ?)", (name, notes))
    actor_id = c.lastrowid
    conn.commit()
    conn.close()
    return actor_id


def get_all_actors():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM actors ORDER BY name COLLATE NOCASE").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_actor(actor_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM actors WHERE id=?", (actor_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_actor_meta(actor_id, meta: dict):
    conn = get_connection()
    notes = json.dumps(meta, ensure_ascii=False) if meta else ''
    conn.execute("UPDATE actors SET notes=? WHERE id=?", (notes, actor_id))
    conn.commit()
    conn.close()



def _cleanup_id_from_marker_jsons(field: str, remove_id: int):
    """Verwijder een actor- of categorie-ID uit alle marker-JSON-bestanden.

    field    = 'actors' of 'categories'
    remove_id = het ID dat niet meer in de markers mag staan

    Scant de ingestelde filmmap recursief op *_markers.json-bestanden en
    schrijft alleen terug als er daadwerkelijk iets veranderd is.
    """
    film_folder = get_setting('film_folder', '')
    if not film_folder or not os.path.isdir(film_folder):
        return
    try:
        for p in Path(film_folder).rglob('*_markers.json'):
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                changed = False
                for m in data:
                    lst = m.get(field) or []
                    if remove_id in lst:
                        m[field] = [x for x in lst if x != remove_id]
                        changed = True
                if changed:
                    p.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False),
                        encoding='utf-8'
                    )
            except Exception:
                pass  # beschadigde of niet-gerelateerde JSON: stil negeren
    except Exception:
        pass


def delete_actor(actor_id):
    conn = get_connection()
    conn.execute("DELETE FROM actors WHERE id=?", (actor_id,))
    conn.commit()
    conn.close()
    # Verwijder actor-ID uit alle marker-JSON-bestanden in de filmmap
    _cleanup_id_from_marker_jsons('actors', actor_id)


# ── Actor Photos ──────────────────────────────

def get_actor_photos(actor_id):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM actor_photos WHERE actor_id=?", (actor_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Films ─────────────────────────────────────

def get_or_create_film(file_path, title=None):
    conn = get_connection()
    row = conn.execute("SELECT * FROM films WHERE file_path=?", (file_path,)).fetchone()
    if row:
        conn.close()
        return dict(row)
    t = title or Path(file_path).stem
    c = conn.cursor()
    c.execute("INSERT INTO films (title, file_path) VALUES (?, ?)", (t, file_path))
    film_id = c.lastrowid
    conn.commit()
    conn.close()
    return {'id': film_id, 'title': t, 'file_path': file_path, 'notes': ''}


def get_all_films():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM films ORDER BY title COLLATE NOCASE").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_film_thumbnails_batch() -> dict:
    """Return {film_id: [path, ...]} voor alle films in één query."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT film_id, path FROM film_thumbnails ORDER BY film_id, id"
    ).fetchall()
    conn.close()
    result: dict = {}
    for r in rows:
        result.setdefault(r['film_id'], []).append(r['path'])
    return result


def get_actor_counts_batch() -> dict:
    """Return {film_id: actor_count} voor alle films in één query."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT film_id, COUNT(*) as cnt FROM actor_films GROUP BY film_id"
    ).fetchall()
    conn.close()
    return {r['film_id']: r['cnt'] for r in rows}


def get_actor_film_counts_batch() -> dict:
    """Return {actor_id: film_count} voor alle acteurs in één query.
    Gebruikt door de speler-zoekbalk om te sorteren zonder per-acteur DB-queries."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT actor_id, COUNT(*) as cnt FROM actor_films GROUP BY actor_id"
    ).fetchall()
    conn.close()
    return {r['actor_id']: r['cnt'] for r in rows}


def get_actor_photos_for_films_batch() -> dict:
    """Return {film_id: [photo_path, ...]} (max 6 per film) in één query.
    Gebruikt door de film-delegate zodat paint() geen DB-queries nodig heeft."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT af.film_id, MIN(ap.photo_path) AS photo_path
        FROM actor_films af
        JOIN actor_photos ap ON ap.actor_id = af.actor_id
        GROUP BY af.film_id, af.actor_id
        ORDER BY af.film_id, MIN(ap.id)
    """).fetchall()
    conn.close()
    result: dict = {}
    for r in rows:
        lst = result.setdefault(r['film_id'], [])
        if len(lst) < 6:   # max 6 per film
            lst.append(r['photo_path'])
    return result


def update_film_file_stats(film_id: int, size: int, mtime: float):
    """Sla bestandsgrootte en wijzigingsdatum op zodat fp.stat() niet bij elke scan nodig is."""
    conn = get_connection()
    conn.execute(
        "UPDATE films SET file_size=?, file_mtime=? WHERE id=?",
        (size, mtime, film_id)
    )
    conn.commit()
    conn.close()


def update_film_marker_counts(file_path: str, total: int, neg: int):
    """Sla marker-tellingen op in de DB zodat JSON-reads bij scan niet nodig zijn."""
    conn = get_connection()
    conn.execute(
        "UPDATE films SET marker_count=?, neg_marker_count=? WHERE file_path=?",
        (total, neg, file_path)
    )
    conn.commit()
    conn.close()


def populate_marker_counts_from_json(film_folder: str):
    """
    Eenmalige initialisatie: lees JSON-bestanden en vul marker_count /
    neg_marker_count in de DB voor alle films in film_folder.
    Wordt alleen gedraaid als er nog films zijn met count=0 én een JSON-bestand.
    """
    conn = get_connection()
    films = conn.execute(
        "SELECT id, file_path FROM films WHERE marker_count=0"
    ).fetchall()
    conn.close()

    for film in films:
        fp = Path(film['file_path'])
        mf = fp.parent / f".{fp.stem}_markers.json"
        if mf.exists():
            try:
                markers = json.loads(mf.read_text('utf-8'))
                total = len(markers)
                neg   = sum(1 for m in markers if m.get('negative'))
                if total > 0:
                    update_film_marker_counts(film['file_path'], total, neg)
            except Exception:
                pass


def get_film(film_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM films WHERE id=?", (film_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_film_by_path(file_path: str):
    """Remove a film record (and all cascades) by its file path.
    Also deletes the associated thumbnail files from disk."""
    conn = get_connection()
    film = conn.execute(
        "SELECT id FROM films WHERE file_path=?", (file_path,)
    ).fetchone()
    if film:
        # Haal thumbnail-paden op vóór de cascade-delete ze verwijdert
        thumb_paths = [
            r['path'] for r in conn.execute(
                "SELECT path FROM film_thumbnails WHERE film_id=?", (film['id'],)
            ).fetchall()
        ]
        conn.execute("DELETE FROM films WHERE id=?", (film['id'],))
        conn.commit()
        conn.close()
        # Verwijder de fysieke thumbnailbestanden na de DB-commit
        for path in thumb_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
    else:
        conn.execute("DELETE FROM films WHERE file_path=?", (file_path,))
        conn.commit()
        conn.close()


def set_film_thumbnail(film_id, path):
    conn = get_connection()
    conn.execute("UPDATE films SET thumbnail=? WHERE id=?", (path, film_id))
    conn.commit()
    conn.close()


def add_film_thumbnail(film_id, path):
    conn = get_connection()
    conn.execute("INSERT INTO film_thumbnails (film_id, path) VALUES (?, ?)", (film_id, path))
    conn.commit()
    conn.close()


def get_film_thumbnails(film_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM film_thumbnails WHERE film_id=? ORDER BY id", (film_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_film_thumbnail(thumb_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT path FROM film_thumbnails WHERE id=?", (thumb_id,)
    ).fetchone()
    conn.execute("DELETE FROM film_thumbnails WHERE id=?", (thumb_id,))
    conn.commit()
    conn.close()
    # Verwijder ook het fysieke bestand
    if row:
        try:
            os.unlink(row['path'])
        except OSError:
            pass


def set_film_duration(film_id, duration: float):
    conn = get_connection()
    conn.execute("UPDATE films SET duration=? WHERE id=?", (duration, film_id))
    conn.commit()
    conn.close()


# ── Actor ↔ Film links ────────────────────────

def link_actor_film(actor_id, film_id):
    conn = get_connection()
    conn.execute("INSERT OR IGNORE INTO actor_films (actor_id, film_id) VALUES (?, ?)", (actor_id, film_id))
    conn.commit()
    conn.close()


def unlink_actor_film(actor_id, film_id):
    conn = get_connection()
    conn.execute("DELETE FROM actor_films WHERE actor_id=? AND film_id=?", (actor_id, film_id))
    conn.commit()
    conn.close()


def get_films_for_actor(actor_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT f.* FROM films f
        JOIN actor_films af ON af.film_id = f.id
        WHERE af.actor_id = ?
        ORDER BY f.title COLLATE NOCASE
    """, (actor_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_actors_for_film(film_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT a.* FROM actors a
        JOIN actor_films af ON af.actor_id = a.id
        WHERE af.film_id = ?
        ORDER BY a.name COLLATE NOCASE
    """, (film_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_categories():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_category(name, icon_path='', parent_id=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO categories (name, icon_path, parent_id) VALUES (?, ?, ?)",
        (name, icon_path, parent_id)
    )
    cat_id = c.lastrowid
    conn.commit()
    conn.close()
    return cat_id


def get_categories_by_ids(ids: list) -> list:
    if not ids:
        return []
    conn = get_connection()
    placeholders = ','.join('?' for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM categories WHERE id IN ({placeholders})", ids
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_category(cat_id, name, icon_path=''):
    conn = get_connection()
    conn.execute("UPDATE categories SET name=?, icon_path=? WHERE id=?", (name, icon_path, cat_id))
    conn.commit()
    conn.close()


def delete_category(cat_id):
    conn = get_connection()
    conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()
    conn.close()
    # Verwijder categorie-ID uit alle marker-JSON-bestanden in de filmmap
    _cleanup_id_from_marker_jsons('categories', cat_id)


# Initialize on import
init_db()


# ── Actor Kleuren ─────────────────────────────

def get_actor_kleuren() -> list:
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM actor_kleuren ORDER BY id"
        ).fetchall()]


def create_actor_kleur(naam: str, hex_color: str = '') -> int:
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO actor_kleuren (naam, hex) VALUES (?, ?)", (naam, hex_color)
        )
        conn.commit()
        return cur.lastrowid


def delete_actor_kleur(kleur_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM actor_kleuren WHERE id=?", (kleur_id,))
        conn.commit()


# ── Actor Trait Types ─────────────────────────

def get_actor_trait_types(type_filter: str = None) -> list:
    """Geeft trait-typen terug.
    type_filter: None = alle  |  'positief' | 'negatief' | 'beide'
    Voor de sterke-kanten-weergave: filter op ('positief', 'beide').
    Voor de zwakke-kanten-weergave: filter op ('negatief', 'beide').
    """
    with _db() as conn:
        if type_filter:
            rows = conn.execute(
                "SELECT * FROM actor_trait_types WHERE type=? ORDER BY naam COLLATE NOCASE",
                (type_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM actor_trait_types ORDER BY naam COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]


def create_actor_trait_type(naam: str, type: str = 'beide') -> int:
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO actor_trait_types (naam, type) VALUES (?, ?)", (naam, type)
        )
        conn.commit()
        return cur.lastrowid


def delete_actor_trait_type(trait_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM actor_trait_types WHERE id=?", (trait_id,))
        conn.commit()


# ── Actor Traits (actor ↔ trait_type links) ───

def get_actor_trait_ids(actor_id: int) -> set:
    with _db() as conn:
        rows = conn.execute(
            "SELECT trait_id FROM actor_traits WHERE actor_id=?", (actor_id,)
        ).fetchall()
        return {r['trait_id'] for r in rows}


def set_actor_traits(actor_id: int, trait_ids: list):
    """Vervang alle traits van een acteur door de gegeven lijst."""
    with _db() as conn:
        conn.execute("DELETE FROM actor_traits WHERE actor_id=?", (actor_id,))
        for tid in trait_ids:
            conn.execute(
                "INSERT OR IGNORE INTO actor_traits (actor_id, trait_id) VALUES (?, ?)",
                (actor_id, tid)
            )
        conn.commit()


# ── Film Categorie Types ──────────────────────

def get_film_categorie_types() -> list:
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM film_categorie_types ORDER BY naam COLLATE NOCASE"
        ).fetchall()]


def create_film_categorie_type(naam: str, icon_path: str = '') -> int:
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO film_categorie_types (naam, icon_path) VALUES (?, ?)",
            (naam, icon_path)
        )
        conn.commit()
        return cur.lastrowid


def update_film_categorie_type(cat_id: int, naam: str, icon_path: str = ''):
    with _db() as conn:
        conn.execute(
            "UPDATE film_categorie_types SET naam=?, icon_path=? WHERE id=?",
            (naam, icon_path, cat_id)
        )
        conn.commit()


def delete_film_categorie_type(cat_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM film_categorie_types WHERE id=?", (cat_id,))
        conn.commit()


# ── Film Categorieën (film ↔ categorie links) ──

def get_film_category_ids(film_id: int) -> set:
    with _db() as conn:
        rows = conn.execute(
            "SELECT categorie_id FROM film_categorie_links WHERE film_id=?", (film_id,)
        ).fetchall()
        return {r['categorie_id'] for r in rows}


def set_film_categories(film_id: int, cat_ids: list):
    """Vervang alle filmcategorieën door de gegeven lijst."""
    with _db() as conn:
        conn.execute("DELETE FROM film_categorie_links WHERE film_id=?", (film_id,))
        for cid in cat_ids:
            conn.execute(
                "INSERT OR IGNORE INTO film_categorie_links (film_id, categorie_id) VALUES (?, ?)",
                (film_id, cid)
            )
        conn.commit()


def get_film_category_ids_batch() -> dict:
    """Return {film_id: set(categorie_id)} voor alle films in één query."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT film_id, categorie_id FROM film_categorie_links"
        ).fetchall()
    result: dict = {}
    for r in rows:
        result.setdefault(r['film_id'], set()).add(r['categorie_id'])
    return result


def get_actor_trait_ids_batch() -> dict:
    """Return {actor_id: set(trait_id)} voor alle acteurs in één query."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT actor_id, trait_id FROM actor_traits"
        ).fetchall()
    result: dict = {}
    for r in rows:
        result.setdefault(r['actor_id'], set()).add(r['trait_id'])
    return result


def get_actor_metadata_batch() -> dict:
    """Return {actor_id: {'kleur': str, 'grootte': str, 'decennia': str}}
    voor alle acteurs in één query."""
    with _db() as conn:
        rows = conn.execute("SELECT id, notes FROM actors").fetchall()
    result: dict = {}
    for r in rows:
        try:
            meta = json.loads(r['notes'] or '{}')
        except Exception:
            meta = {}
        result[r['id']] = {
            'kleur':    str(meta.get('kleur',    '') or ''),
            'grootte':  str(meta.get('grootte',  '') or ''),
            'decennia': str(meta.get('decennia', '') or ''),
        }
    return result


# ── Cross-entity filter queries ───────────────

def get_film_ids_by_actor_kleuren(kleur_ids: list) -> set:
    """Film-IDs waar minstens één acteur één van de gegeven kleuren heeft."""
    if not kleur_ids:
        return set()
    kleur_strs = {str(k) for k in kleur_ids}
    with _db() as conn:
        actors = conn.execute("SELECT id, notes FROM actors").fetchall()
        matching = []
        for a in actors:
            try:
                meta = json.loads(a['notes'] or '{}')
                if str(meta.get('kleur', '')) in kleur_strs:
                    matching.append(a['id'])
            except Exception:
                pass
        if not matching:
            return set()
        ph = ','.join('?' for _ in matching)
        rows = conn.execute(
            f"SELECT DISTINCT film_id FROM actor_films WHERE actor_id IN ({ph})", matching
        ).fetchall()
        return {r['film_id'] for r in rows}


def get_film_ids_by_actor_grootte_exact(values: set) -> set:
    """Film-IDs waar minstens één acteur een grootte heeft die in `values` zit."""
    if not values:
        return set()
    str_vals = {str(v) for v in values}
    with _db() as conn:
        actors = conn.execute("SELECT id, notes FROM actors").fetchall()
        matching = []
        for a in actors:
            try:
                meta = json.loads(a['notes'] or '{}')
                if str(meta.get('grootte', '') or '') in str_vals:
                    matching.append(a['id'])
            except Exception:
                pass
        if not matching:
            return set()
        ph = ','.join('?' for _ in matching)
        rows = conn.execute(
            f"SELECT DISTINCT film_id FROM actor_films WHERE actor_id IN ({ph})", matching
        ).fetchall()
        return {r['film_id'] for r in rows}


def get_film_ids_by_actor_grootte(min_g: int | None, max_g: int | None) -> set:
    """Film-IDs waar minstens één acteur grootte binnen het gegeven bereik heeft."""
    if min_g is None and max_g is None:
        return set()
    with _db() as conn:
        actors = conn.execute("SELECT id, notes FROM actors").fetchall()
        matching = []
        for a in actors:
            try:
                meta = json.loads(a['notes'] or '{}')
                g = int(meta.get('grootte', 0) or 0)
                if g == 0:
                    continue
                if min_g is not None and g < min_g:
                    continue
                if max_g is not None and g > max_g:
                    continue
                matching.append(a['id'])
            except Exception:
                pass
        if not matching:
            return set()
        ph = ','.join('?' for _ in matching)
        rows = conn.execute(
            f"SELECT DISTINCT film_id FROM actor_films WHERE actor_id IN ({ph})", matching
        ).fetchall()
        return {r['film_id'] for r in rows}


def get_film_ids_by_actor_traits(trait_ids: list) -> set:
    """Film-IDs waar minstens één acteur één van de gegeven traits heeft."""
    if not trait_ids:
        return set()
    with _db() as conn:
        ph = ','.join('?' for _ in trait_ids)
        rows = conn.execute(f"""
            SELECT DISTINCT af.film_id
            FROM actor_films af
            JOIN actor_traits at2 ON at2.actor_id = af.actor_id
            WHERE at2.trait_id IN ({ph})
        """, trait_ids).fetchall()
        return {r['film_id'] for r in rows}


def get_film_ids_by_actor_decennia(decennia_keys: list) -> set:
    """Film-IDs waar minstens één acteur één van de gegeven decennia-waarden heeft.
    decennia_keys: list of strings like ['7','8','9','0'] (eerste cijfer van het decennium)."""
    if not decennia_keys:
        return set()
    dec_strs = {str(d) for d in decennia_keys}
    with _db() as conn:
        actors = conn.execute("SELECT id, notes FROM actors").fetchall()
        matching = []
        for a in actors:
            try:
                meta = json.loads(a['notes'] or '{}')
                if str(meta.get('decennia', '')) in dec_strs:
                    matching.append(a['id'])
            except Exception:
                pass
        if not matching:
            return set()
        ph = ','.join('?' for _ in matching)
        rows = conn.execute(
            f"SELECT DISTINCT film_id FROM actor_films WHERE actor_id IN ({ph})", matching
        ).fetchall()
        return {r['film_id'] for r in rows}


# ── Film filter presets ───────────────────────

def get_all_film_filter_presets() -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, filter_json FROM film_filter_presets ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def save_film_filter_preset(name: str, filter_state: dict) -> int:
    """Sla een preset op (INSERT OR REPLACE op naam). Geeft het nieuwe ID terug."""
    js = json.dumps(filter_state, ensure_ascii=False)
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO film_filter_presets (name, filter_json) VALUES (?, ?)",
            (name, js)
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM film_filter_presets WHERE name=?", (name,)
        ).fetchone()
        return row['id'] if row else -1


def delete_film_filter_preset(preset_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM film_filter_presets WHERE id=?", (preset_id,))
        conn.commit()


def load_film_filter_preset(preset_id: int) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT filter_json FROM film_filter_presets WHERE id=?", (preset_id,)
        ).fetchone()
        if row:
            try:
                return json.loads(row['filter_json'])
            except Exception:
                pass
    return {}


def get_film_ids_by_actors(actor_ids: list) -> set:
    """Film-IDs die minstens één van de gegeven acteurs bevatten."""
    if not actor_ids:
        return set()
    with _db() as conn:
        ph   = ','.join('?' for _ in actor_ids)
        rows = conn.execute(
            f"SELECT DISTINCT film_id FROM actor_films WHERE actor_id IN ({ph})",
            actor_ids
        ).fetchall()
        return {r['film_id'] for r in rows}


def get_film_ids_by_film_categories(cat_ids: list) -> set:
    """Film-IDs die minstens één van de gegeven filmcategorieën hebben."""
    if not cat_ids:
        return set()
    with _db() as conn:
        ph = ','.join('?' for _ in cat_ids)
        rows = conn.execute(
            f"SELECT DISTINCT film_id FROM film_categorie_links WHERE categorie_id IN ({ph})",
            cat_ids
        ).fetchall()
        return {r['film_id'] for r in rows}


# ── Film publicatiedatum ──────────────────────

def update_film_publicatiedatum(film_id: int, datum: str):
    conn = get_connection()
    conn.execute("UPDATE films SET publicatiedatum=? WHERE id=?", (datum, film_id))
    conn.commit()
    conn.close()


def update_afgeleide_rating(file_path: str, value: float):
    """Sla de berekende afgeleide rating op (som marker-sterren, max 10)."""
    conn = get_connection()
    conn.execute(
        "UPDATE films SET afgeleide_rating=? WHERE file_path=?",
        (value, file_path)
    )
    conn.commit()
    conn.close()


# ── Auto-link actor photos ────────────────────

def auto_link_actor_photos():
    """Scan acteurfotos/ en verwerk elke foto:
    - Bestaat er al een acteur met die naam?  → foto koppelen als dat nog niet gedaan is.
    - Bestaat de acteur nog niet?             → acteur aanmaken én foto koppelen.
    De bestandsnaam (zonder extensie) is de naam van de acteur."""
    folder = str(ACTEURFOTOS_DIR)
    if not os.path.isdir(folder):
        return
    exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif'}

    photos = [
        (f.stem, str(f))
        for f in Path(folder).iterdir()
        if f.suffix.lower() in exts
    ]
    if not photos:
        return

    conn = get_connection()
    for name, photo_path in photos:
        # Zoek acteur (hoofdletter-onafhankelijk)
        row = conn.execute(
            "SELECT id FROM actors WHERE lower(name)=lower(?)", (name,)
        ).fetchone()
        if row:
            actor_id = row['id']
        else:
            # Nieuwe acteur aanmaken op basis van bestandsnaam
            cur = conn.execute("INSERT INTO actors (name) VALUES (?)", (name,))
            actor_id = cur.lastrowid

        # Foto koppelen als dat nog niet gedaan is
        already = conn.execute(
            "SELECT id FROM actor_photos WHERE actor_id=? AND photo_path=?",
            (actor_id, photo_path)
        ).fetchone()
        if not already:
            conn.execute(
                "INSERT INTO actor_photos (actor_id, photo_path) VALUES (?, ?)",
                (actor_id, photo_path)
            )
    conn.commit()
    conn.close()


# ── Bigfiles ──────────────────────────────────────────────────────────────────

def get_or_create_bigfile(full_path: str) -> dict:
    """Geeft een bestaand bigfile-record terug of maakt een nieuw aan."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM bigfiles WHERE full_path=?", (full_path,)
        ).fetchone()
        if row:
            return dict(row)
        conn.execute(
            "INSERT INTO bigfiles (full_path) VALUES (?)", (full_path,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM bigfiles WHERE full_path=?", (full_path,)
        ).fetchone()
        return dict(row)


def update_bigfile_last_seen(bigfile_id: int):
    with _db() as conn:
        conn.execute(
            "UPDATE bigfiles SET last_seen=datetime('now') WHERE id=?",
            (bigfile_id,)
        )
        conn.commit()


def set_bigfile_thumbnail(bigfile_id: int, thumb_path: str):
    with _db() as conn:
        conn.execute(
            "UPDATE bigfiles SET thumbnail_path=? WHERE id=?",
            (thumb_path, bigfile_id)
        )
        conn.commit()


def get_all_bigfiles() -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM bigfiles ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_bigfile_actor_ids(bigfile_id: int) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT acteur_id FROM bigfiles_acteurs WHERE bigfile_id=?",
            (bigfile_id,)
        ).fetchall()
        return [r['acteur_id'] for r in rows]


def set_bigfile_actors(bigfile_id: int, actor_ids: list):
    with _db() as conn:
        conn.execute(
            "DELETE FROM bigfiles_acteurs WHERE bigfile_id=?", (bigfile_id,)
        )
        for aid in actor_ids:
            conn.execute(
                "INSERT OR IGNORE INTO bigfiles_acteurs (bigfile_id, acteur_id) VALUES (?,?)",
                (bigfile_id, aid)
            )
        conn.commit()


def get_best_film_thumbnail(file_path: str) -> str | None:
    """Return the best available thumbnail path for a film by file path, or None.

    Checks film_thumbnails first (most recent), then films.thumbnail as fallback.
    Returns None when neither exists on disk.
    """
    with _db() as conn:
        film = conn.execute(
            "SELECT id, thumbnail FROM films WHERE file_path=?", (file_path,)
        ).fetchone()
        if not film:
            return None
        row = conn.execute(
            "SELECT path FROM film_thumbnails WHERE film_id=? ORDER BY id DESC LIMIT 1",
            (film['id'],)
        ).fetchone()
        if row and row['path'] and Path(row['path']).exists():
            return row['path']
        if film['thumbnail'] and Path(str(film['thumbnail'])).exists():
            return str(film['thumbnail'])
        return None


def get_actor_names_for_film_path(file_path: str) -> list:
    """Return list of actor names linked to a film by file path (regular films system)."""
    with _db() as conn:
        film = conn.execute(
            "SELECT id FROM films WHERE file_path=?", (file_path,)
        ).fetchone()
        if not film:
            return []
        rows = conn.execute(
            "SELECT a.name FROM actors a "
            "JOIN actor_films af ON af.actor_id = a.id "
            "WHERE af.film_id = ? ORDER BY a.name COLLATE NOCASE",
            (film['id'],)
        ).fetchall()
        return [r['name'] for r in rows]


def get_bigfile_by_path(full_path: str) -> dict | None:
    """Return a bigfile record by its full path, or None if not found."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM bigfiles WHERE full_path=?", (full_path,)
        ).fetchone()
        return dict(row) if row else None


auto_link_actor_photos()
