"""
CineMarker — datamodellen (dataclasses, geen Qt).

Markers blijven op schijf gewone dicts (JSON); `Marker` is een getypte
leeslaag met `from_dict`/`to_dict` zodat de panelen niet op sleutelnamen
hoeven te vertrouwen.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from .formats import clamp_grootte, to_int


# ── Markers ─────────────────────────────────────────────────────────────────

@dataclass
class Marker:
    time: float
    name: str = ""
    actors: list[int] = field(default_factory=list)
    categories: list[int] = field(default_factory=list)
    negative: bool = False
    stars: int = 0
    created: str = ""
    created_at: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Marker":
        return cls(
            time=float(d.get("time") or 0.0),
            name=str(d.get("name") or ""),
            actors=[int(a) for a in (d.get("actors") or [])],
            categories=[int(c) for c in (d.get("categories") or [])],
            negative=bool(d.get("negative")),
            stars=to_int(d.get("stars"), 0),
            created=str(d.get("created") or ""),
            created_at=float(d.get("created_at") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["stars"] = self.stars if self.stars > 0 else None
        return d

    @classmethod
    def new(cls, time: float, name: str, actors: list[int], categories: list[int],
            stars: int = 0, negative: bool = False) -> "Marker":
        now = datetime.now()
        return cls(time=time, name=name, actors=list(actors), categories=list(categories),
                   negative=negative, stars=stars, created=now.isoformat(timespec="seconds"),
                   created_at=now.timestamp())


# ── Kaarten in grids ────────────────────────────────────────────────────────

@dataclass
class FilmCard:
    path: str
    name: str
    film_id: int | None = None
    thumbnails: list[str] = field(default_factory=list)
    marker_thumbs: list[str] = field(default_factory=list)
    duration: float = 0.0
    size: int = 0
    mtime: float = 0.0
    markers: int = 0
    neg_markers: int = 0
    marker_cat_ids: set[int] = field(default_factory=set)
    actor_count: int = 0
    actor_photos: list[str] = field(default_factory=list)
    rating: float = 0.0
    thumb_phase: float = 0.0        # willekeurige fase voor crossfade
    marker_period: float = 3.0
    dimmed: bool = False            # bv. bigfile dat nu niet bereikbaar is
    always_show_name: bool = False

    @property
    def thumbnail(self) -> str | None:
        return self.thumbnails[0] if self.thumbnails else None


@dataclass
class ActorMeta:
    voornaam: str = ""
    achternaam: str = ""
    kleur: str = ""
    grootte: int = 0          # 1–5, 0 = onbekend
    rating: int = 0           # 1–9, 0 = onbekend
    decennia: str = ""        # één cijfer, zie formats.DECADE_OPTIONS
    piek_start: str = ""
    piek_eind: str = ""

    @classmethod
    def from_notes(cls, meta: dict[str, Any] | None) -> "ActorMeta":
        m = meta or {}
        return cls(
            voornaam=str(m.get("voornaam") or ""),
            achternaam=str(m.get("achternaam") or ""),
            kleur=str(m.get("kleur") or ""),
            grootte=clamp_grootte(m.get("grootte")),
            rating=to_int(m.get("rating"), 0),
            decennia=str(m.get("decennia") or ""),
            piek_start=str(m.get("piek_start") or ""),
            piek_eind=str(m.get("piek_eind") or ""),
        )

    def to_notes(self) -> dict[str, str]:
        """Alleen gevulde velden; lege velden worden door update_actor_meta verwijderd."""
        return {
            "voornaam": self.voornaam,
            "achternaam": self.achternaam,
            "kleur": self.kleur,
            "grootte": str(self.grootte) if self.grootte else "",
            "rating": str(self.rating) if self.rating else "",
            "decennia": self.decennia,
            "piek_start": self.piek_start,
            "piek_eind": self.piek_eind,
        }

    @property
    def display_name(self) -> str:
        return " ".join(p for p in (self.voornaam, self.achternaam) if p).strip()

    @property
    def in_db(self) -> bool:
        return bool(self.voornaam or self.achternaam)


@dataclass
class ActorCard:
    stem: str
    photo_path: str
    actor_id: int | None
    meta: ActorMeta
    film_count: int = 0
    marker_count: int = 0

    @property
    def in_db(self) -> bool:
        return self.actor_id is not None and self.meta.in_db

    @property
    def display_name(self) -> str:
        return self.meta.display_name or self.stem


@dataclass
class MarkerEntry:
    """Eén marker in het Markers-overzicht of het acteur-detail."""
    marker: Marker
    film_path: str
    film_name: str
    actor_names: list[str] = field(default_factory=list)
    cache_path: str = ""

    @property
    def time(self) -> float:
        return self.marker.time


@dataclass
class BigFile:
    id: int
    full_path: str
    thumbnail_path: str = ""
    actor_names: list[str] = field(default_factory=list)
    available: bool = False


# ── Zoom ────────────────────────────────────────────────────────────────────

ZOOM_FACTOR = 1.25      # elke zoomstap = 25 % groter; niveau +10 ≈ 9,3× de basisgrootte


@dataclass(frozen=True)
class ZoomSpec:
    """Per grid: basisbreedte (niveau 0), geometrische stappen, begrensd niveau, hoogte via aspect.

    `level` is het opgeslagen (gesigneerde) niveau onder de bestaande setting-sleutel;
    niveau 0 geeft dezelfde maat als de oude GUI.
    """
    setting_key: str
    base_w: int
    min_level: int
    max_level: int
    aspect: float = 9 / 16          # hoogte = breedte × aspect

    def clamp(self, level: int) -> int:
        return max(self.min_level, min(self.max_level, level))

    def size(self, level: int) -> tuple[int, int]:
        lvl = self.clamp(level)
        w = int(round(self.base_w * (ZOOM_FACTOR ** lvl)))
        return w, int(round(w * self.aspect))

    @property
    def level_count(self) -> int:
        return self.max_level - self.min_level + 1

    def display_index(self, level: int) -> int:
        """1-gebaseerd nummer voor de gebruiker."""
        return self.clamp(level) - self.min_level + 1


ZOOM_FILMS = ZoomSpec("zoom_films_panel", 192, -4, 10)                # 79 … 1788 px
ZOOM_ACTORS = ZoomSpec("zoom_actors_panel", 160, -4, 10, aspect=1.29)  # 66 … 1490 px
ZOOM_DETAIL_FILMS = ZoomSpec("zoom_detail_films", 160, -3, 10)
ZOOM_DETAIL_MARKERS = ZoomSpec("zoom_detail_markers", 128, -3, 10)
ZOOM_MARKERS = ZoomSpec("zoom_markers_panel", 320, -4, 8)                # 131 … 1907 px
