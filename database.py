#!/usr/bin/env python3
"""
CineMarker Database — SQLite layer voor acteurs, films en scènes
"""

import sqlite3
import json
import os
from pathlib import Path


DB_PATH = os.path.join(Path.home(), ".cinemarker.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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


def get_or_create_actor_by_name(name):
    conn = get_connection()
    row = conn.execute("SELECT * FROM actors WHERE name=?", (name,)).fetchone()
    if row:
        conn.close()
        return dict(row)
    c = conn.cursor()
    c.execute("INSERT INTO actors (name) VALUES (?)", (name,))
    actor_id = c.lastrowid
    conn.commit()
    conn.close()
    return {'id': actor_id, 'name': name, 'notes': ''}


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


def update_actor(actor_id, name, notes=''):
    conn = get_connection()
    conn.execute("UPDATE actors SET name=?, notes=? WHERE id=?", (name, notes, actor_id))
    conn.commit()
    conn.close()


def delete_actor(actor_id):
    conn = get_connection()
    conn.execute("DELETE FROM actors WHERE id=?", (actor_id,))
    conn.commit()
    conn.close()


# ── Actor Photos ──────────────────────────────

def add_actor_photo(actor_id, photo_path):
    conn = get_connection()
    conn.execute("INSERT INTO actor_photos (actor_id, photo_path) VALUES (?, ?)", (actor_id, photo_path))
    conn.commit()
    conn.close()


def get_actor_photos(actor_id):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM actor_photos WHERE actor_id=?", (actor_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_actor_photo(photo_id):
    conn = get_connection()
    conn.execute("DELETE FROM actor_photos WHERE id=?", (photo_id,))
    conn.commit()
    conn.close()


def import_photos_from_folder(actor_id, folder_path):
    """Import all image files from a folder for an actor"""
    exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.gif'}
    imported = 0
    for f in Path(folder_path).iterdir():
        if f.suffix.lower() in exts:
            add_actor_photo(actor_id, str(f))
            imported += 1
    return imported


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


def get_film(film_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM films WHERE id=?", (film_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_film_notes(film_id, notes):
    conn = get_connection()
    conn.execute("UPDATE films SET notes=? WHERE id=?", (notes, film_id))
    conn.commit()
    conn.close()


def set_film_thumbnail(film_id, path):
    conn = get_connection()
    conn.execute("UPDATE films SET thumbnail=? WHERE id=?", (path, film_id))
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


# ── Scenes ────────────────────────────────────

def create_scene(film_id, title, start_time, end_time, notes=''):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO scenes (film_id, title, start_time, end_time, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (film_id, title, start_time, end_time, notes))
    scene_id = c.lastrowid
    conn.commit()
    conn.close()
    return scene_id


def get_scenes_for_film(film_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM scenes WHERE film_id=? ORDER BY start_time
    """, (film_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_scenes_for_actor(actor_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.*, f.title as film_title, f.file_path as film_path
        FROM scenes s
        JOIN scene_actors sa ON sa.scene_id = s.id
        JOIN films f ON f.id = s.film_id
        WHERE sa.actor_id = ?
        ORDER BY f.title, s.start_time
    """, (actor_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_scene(scene_id):
    conn = get_connection()
    row = conn.execute("""
        SELECT s.*, f.title as film_title, f.file_path as film_path
        FROM scenes s JOIN films f ON f.id = s.film_id
        WHERE s.id=?
    """, (scene_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_scene(scene_id, title, start_time, end_time, notes=''):
    conn = get_connection()
    conn.execute("""
        UPDATE scenes SET title=?, start_time=?, end_time=?, notes=?
        WHERE id=?
    """, (title, start_time, end_time, notes, scene_id))
    conn.commit()
    conn.close()


def update_scene_export_path(scene_id, path):
    conn = get_connection()
    conn.execute("UPDATE scenes SET export_path=? WHERE id=?", (path, scene_id))
    conn.commit()
    conn.close()


def delete_scene(scene_id):
    conn = get_connection()
    conn.execute("DELETE FROM scenes WHERE id=?", (scene_id,))
    conn.commit()
    conn.close()


# ── Scene ↔ Actor links ───────────────────────

def link_scene_actor(scene_id, actor_id):
    conn = get_connection()
    conn.execute("INSERT OR IGNORE INTO scene_actors (scene_id, actor_id) VALUES (?, ?)", (scene_id, actor_id))
    conn.commit()
    conn.close()


def unlink_scene_actor(scene_id, actor_id):
    conn = get_connection()
    conn.execute("DELETE FROM scene_actors WHERE scene_id=? AND actor_id=?", (scene_id, actor_id))
    conn.commit()
    conn.close()


def get_all_categories():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_category(name, icon_path=''):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO categories (name, icon_path) VALUES (?, ?)", (name, icon_path))
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


def get_actors_for_scene(scene_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT a.* FROM actors a
        JOIN scene_actors sa ON sa.actor_id = a.id
        WHERE sa.scene_id = ?
        ORDER BY a.name COLLATE NOCASE
    """, (scene_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Initialize on import
init_db()


# ── Auto-link actor photos ────────────────────

def auto_link_actor_photos():
    """Scan acteurfotos/ and link any photo whose stem matches an actor name."""
    import os
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'acteurfotos')
    if not os.path.isdir(folder):
        return
    exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif'}
    photos_by_stem = {}
    for f in Path(folder).iterdir():
        if f.suffix.lower() in exts:
            photos_by_stem[f.stem.lower()] = str(f)

    conn = get_connection()
    actors = conn.execute("SELECT id, name FROM actors").fetchall()
    for actor in actors:
        stem = actor['name'].lower()
        if stem not in photos_by_stem:
            continue
        existing = conn.execute(
            "SELECT id FROM actor_photos WHERE actor_id=?", (actor['id'],)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO actor_photos (actor_id, photo_path) VALUES (?, ?)",
                (actor['id'], photos_by_stem[stem])
            )
    conn.commit()
    conn.close()


auto_link_actor_photos()
