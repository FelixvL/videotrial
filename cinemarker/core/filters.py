"""
CineMarker — pure filter- en sorteerlogica (geen Qt), testbaar.

Films: `FilmCriteria` + `film_matches()` + `film_sort_key()` + preset-(de)serialisatie
met hetzelfde JSON-schema als de oude GUI (bestaande presets blijven werken).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import database as db
from .formats import decade_sort_key, parse_duration, parse_size, size_bucket
from .models import ActorCard, ActorMeta, FilmCard, MarkerEntry

# ── Films ───────────────────────────────────────────────────────────────────

FILM_SORT_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "Naam"),
    ("size", "Grootte"),
    ("date", "Datum"),
    ("markers", "Markers"),
    ("duration", "Duur"),
    ("actors", "Acteurs"),
)


@dataclass
class FilmCriteria:
    search: str = ""
    dur_min: str = ""
    dur_max: str = ""
    sz_min: str = ""
    sz_max: str = ""
    one_thumb: bool = False
    multi_thumb: bool = False
    no_thumb: bool = False
    with_markers: bool = False
    no_markers: bool = False
    marker_cats: set[int] = field(default_factory=set)
    film_size: set[str] = field(default_factory=set)
    # cross-entity (via de database)
    actor_groo: set[str] = field(default_factory=set)
    film_cats: set[int] = field(default_factory=set)
    actor_kleur: set[str] = field(default_factory=set)
    actor_dec: set[str] = field(default_factory=set)
    actors: dict[int, str] = field(default_factory=dict)      # id → weergavenaam

    def needs_cross(self) -> bool:
        return bool(self.actor_groo or self.film_cats or self.actor_kleur or self.actor_dec or self.actors)

    def cross_key(self) -> tuple:
        return (frozenset(self.actor_groo), frozenset(self.film_cats), frozenset(self.actor_kleur),
                frozenset(self.actor_dec), frozenset(self.actors))

    def is_empty(self) -> bool:
        return not any((self.search.strip(), self.dur_min.strip(), self.dur_max.strip(), self.sz_min.strip(),
                        self.sz_max.strip(), self.one_thumb, self.multi_thumb, self.no_thumb,
                        self.with_markers, self.no_markers, self.marker_cats, self.film_size)) and not self.needs_cross()

    # Preset-schema is identiek aan de oude GUI (keys flt_*); 'ag_min/ag_max' worden genegeerd.
    def to_preset(self) -> dict[str, Any]:
        return {
            "search": self.search, "dur_min": self.dur_min, "dur_max": self.dur_max,
            "sz_min": self.sz_min, "sz_max": self.sz_max,
            "flt_actor_groo": sorted(self.actor_groo),
            "flt_1thumb": self.one_thumb, "flt_multithumb": self.multi_thumb, "flt_no_thumb": self.no_thumb,
            "flt_with_markers": self.with_markers, "flt_no_markers": self.no_markers,
            "flt_film_cats": sorted(self.film_cats), "flt_actor_kleur": sorted(self.actor_kleur),
            "flt_actor_dec": sorted(self.actor_dec), "flt_film_size": sorted(self.film_size),
            "flt_marker_cats": sorted(self.marker_cats),
            "flt_actors": [{"id": aid, "name": nm} for aid, nm in self.actors.items()],
        }

    @classmethod
    def from_preset(cls, d: dict[str, Any]) -> "FilmCriteria":
        d = d or {}
        actors: dict[int, str] = {}
        for a in d.get("flt_actors") or []:
            try:
                actors[int(a["id"])] = str(a.get("name") or "")
            except (KeyError, TypeError, ValueError):
                continue
        return cls(
            search=str(d.get("search") or ""), dur_min=str(d.get("dur_min") or ""),
            dur_max=str(d.get("dur_max") or ""), sz_min=str(d.get("sz_min") or ""), sz_max=str(d.get("sz_max") or ""),
            one_thumb=bool(d.get("flt_1thumb")), multi_thumb=bool(d.get("flt_multithumb")),
            no_thumb=bool(d.get("flt_no_thumb")), with_markers=bool(d.get("flt_with_markers")),
            no_markers=bool(d.get("flt_no_markers")),
            marker_cats={int(x) for x in d.get("flt_marker_cats") or []},
            film_size={str(x) for x in d.get("flt_film_size") or []},
            actor_groo={str(x) for x in d.get("flt_actor_groo") or []},
            film_cats={int(x) for x in d.get("flt_film_cats") or []},
            actor_kleur={str(x) for x in d.get("flt_actor_kleur") or []},
            actor_dec={str(x) for x in d.get("flt_actor_dec") or []},
            actors=actors,
        )


def compute_cross_film_ids(crit: FilmCriteria) -> set[int] | None:
    """Doorsnede van alle cross-entity filters; None = geen cross-filter actief."""
    ids: set[int] | None = None

    def _and(s: set[int]) -> None:
        nonlocal ids
        ids = s if ids is None else ids & s

    if crit.film_cats:
        _and(db.get_film_ids_by_film_categories(sorted(crit.film_cats)))
    if crit.actor_kleur:
        _and(db.get_film_ids_by_actor_kleuren(sorted(crit.actor_kleur)))
    if crit.actor_groo:
        _and(db.get_film_ids_by_actor_grootte_exact(set(crit.actor_groo)))
    if crit.actor_dec:
        _and(db.get_film_ids_by_actor_decennia(sorted(crit.actor_dec)))
    if crit.actors:
        _and(db.get_film_ids_by_actors(sorted(crit.actors)))
    return ids


def film_matches(card: FilmCard, crit: FilmCriteria, cross_ids: set[int] | None) -> bool:
    q = crit.search.strip().lower()
    if q and q not in card.name.lower():
        return False
    thumbs = len(card.thumbnails)
    markers = card.markers + card.neg_markers
    if crit.one_thumb and thumbs != 1:
        return False
    if crit.multi_thumb and thumbs <= 1:
        return False
    if crit.no_thumb and thumbs > 0:
        return False
    if crit.with_markers and markers == 0:
        return False
    if crit.no_markers and markers > 0:
        return False
    min_s, max_s = parse_duration(crit.dur_min), parse_duration(crit.dur_max)
    if min_s > 0 and 0 < card.duration < min_s:
        return False
    if max_s > 0 and (card.duration == 0 or card.duration > max_s):   # onbekend = verbergen bij max
        return False
    min_b, max_b = parse_size(crit.sz_min), parse_size(crit.sz_max)
    if min_b > 0 and card.size < min_b:
        return False
    if max_b > 0 and card.size > max_b:
        return False
    if crit.marker_cats and not (card.marker_cat_ids & crit.marker_cats):
        return False
    if crit.film_size and size_bucket(card.size) not in crit.film_size:
        return False
    if cross_ids is not None and card.film_id not in cross_ids:
        return False
    return True


def film_sort_key(card: FilmCard, key: str):
    if key == "name":
        return card.name.lower()
    if key == "size":
        return card.size
    if key == "date":
        return card.mtime
    if key == "markers":
        return card.markers
    if key == "duration":
        return card.duration
    if key == "actors":
        return card.actor_count
    return 0


def sort_film_cards(cards: list[FilmCard], key: str, ascending: bool) -> list[FilmCard]:
    if not key:
        return list(cards)
    return sorted(cards, key=lambda c: film_sort_key(c, key), reverse=not ascending)


# ── Acteurs ─────────────────────────────────────────────────────────────────

ACTOR_SORT_FIELDS: tuple[tuple[str, str], ...] = (
    ("decennia", "Decennia"),
    ("grootte", "Grootte"),
    ("kleur", "Kleur"),
    ("markers", "Markers"),
    ("films", "Films"),
)

ACTOR_META_FIELDS: tuple[str, ...] = ("kleur", "grootte", "rating", "decennia")


@dataclass
class ActorCriteria:
    search: str = ""
    db: set[str] = field(default_factory=set)         # {'in', 'out'}
    kleur: set[str] = field(default_factory=set)
    grootte: set[str] = field(default_factory=set)
    rating: set[str] = field(default_factory=set)
    decennia: set[str] = field(default_factory=set)
    buiten_db: bool = False                            # tag-modus: toon acteurs ZONDER het gekozen veld

    def is_empty(self) -> bool:
        return not (self.search.strip() or self.db or self.kleur or self.grootte or self.rating or self.decennia)

    def buiten_db_active(self) -> tuple[str, str] | None:
        """In BUITEN DB-modus: (veld, waarde) van de enige actieve keuze."""
        for name in ACTOR_META_FIELDS:
            vals = getattr(self, name)
            if vals:
                return name, sorted(vals)[0]
        return None


def _meta_value(card: ActorCard, name: str) -> str:
    m = card.meta
    if name == "kleur":
        return m.kleur
    if name == "grootte":
        return str(m.grootte) if m.grootte else ""
    if name == "rating":
        return str(m.rating) if m.rating else ""
    if name == "decennia":
        return m.decennia
    return ""


def actor_matches(card: ActorCard, crit: ActorCriteria) -> bool:
    q = crit.search.strip().lower()
    if q and not (q in card.stem.lower() or q in card.meta.voornaam.lower() or q in card.meta.achternaam.lower()):
        return False
    if crit.buiten_db:
        active = crit.buiten_db_active()
        if active:
            name, _ = active
            if _meta_value(card, name):
                return False
        return True
    if crit.db and ("in" if card.in_db else "out") not in crit.db:
        return False
    for name in ACTOR_META_FIELDS:
        vals = getattr(crit, name)
        if vals and _meta_value(card, name) not in vals:
            return False
    return True


def actor_sort_key(card: ActorCard, key: str):
    """Sorteersleutel; lege waarden altijd achteraan, ongeacht richting."""
    m = card.meta
    if key == "decennia":
        return (0, decade_sort_key(m.decennia)) if m.decennia else (1, 0)
    if key == "grootte":
        return (0, m.grootte) if m.grootte else (1, 0)
    if key == "kleur":
        return (0, m.kleur) if m.kleur else (1, "")
    if key == "markers":
        return (0, card.marker_count)
    if key == "films":
        return (0, card.film_count)
    return (0, card.stem.lower())


def actor_default_key(card: ActorCard):
    """Standaardvolgorde: rating ↓, films ↓, markers ↓, naam ↑."""
    m = card.meta
    return (-m.rating if m.rating else 1, -card.film_count, -card.marker_count, card.stem.lower())


def sort_actor_cards(cards: list[ActorCard], key: str, descending: bool) -> list[ActorCard]:
    if not key:
        return sorted(cards, key=actor_default_key)
    if key in ("decennia", "grootte", "kleur"):
        # lege waarden achteraan houden: sorteer eerst op "leeg", dan op waarde in de gevraagde richting
        empties = [c for c in cards if actor_sort_key(c, key)[0] == 1]
        filled = [c for c in cards if actor_sort_key(c, key)[0] == 0]
        filled.sort(key=lambda c: actor_sort_key(c, key)[1], reverse=descending)
        return filled + sorted(empties, key=lambda c: c.stem.lower())
    return sorted(cards, key=lambda c: actor_sort_key(c, key)[1], reverse=descending)


# ── Markers-overzicht ───────────────────────────────────────────────────────

MARKER_SORT_FIELDS: tuple[tuple[str, str], ...] = (
    ("film", "Film"),
    ("tijd", "Tijd"),
    ("sterren", "Sterren"),
    ("acteur", "Acteur"),
    ("decade", "Decennium"),
    ("maat", "Grootte"),
    ("filmgr", "Film gr."),
    ("rating", "Rating"),
)


@dataclass
class MarkerContext:
    """Batch-lookups die het markers-overzicht nodig heeft (één keer per refresh)."""
    actor_meta: dict[int, ActorMeta] = field(default_factory=dict)
    actor_traits: dict[int, set[int]] = field(default_factory=dict)
    film_cats: dict[int, set[int]] = field(default_factory=dict)       # film_id → cat ids
    film_ids: dict[str, int] = field(default_factory=dict)             # path → film_id
    film_size: dict[str, int] = field(default_factory=dict)
    film_rating: dict[str, float] = field(default_factory=dict)


@dataclass
class MarkerCriteria:
    search: str = ""
    markertype: set[int] = field(default_factory=set)
    include_actors: set[int] = field(default_factory=set)
    exclude_actors: set[int] = field(default_factory=set)
    traits: set[int] = field(default_factory=set)
    film_cats: set[int] = field(default_factory=set)
    kleur: set[str] = field(default_factory=set)
    grootte: set[str] = field(default_factory=set)
    decennia: set[str] = field(default_factory=set)
    film_size: set[str] = field(default_factory=set)
    stars: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not any((self.search.strip(), self.markertype, self.include_actors, self.exclude_actors, self.traits,
                        self.film_cats, self.kleur, self.grootte, self.decennia, self.film_size, self.stars))


def marker_matches(e: MarkerEntry, crit: MarkerCriteria, ctx: MarkerContext) -> bool:
    actors = set(e.marker.actors)
    if crit.markertype and not (crit.markertype & set(e.marker.categories)):
        return False
    if crit.include_actors and not (crit.include_actors & actors):
        return False
    if crit.exclude_actors and (crit.exclude_actors & actors):
        return False
    if crit.traits and not any(crit.traits & ctx.actor_traits.get(a, set()) for a in actors):
        return False
    if crit.film_cats and not (crit.film_cats & ctx.film_cats.get(ctx.film_ids.get(e.film_path, -1), set())):
        return False
    metas = [ctx.actor_meta.get(a) for a in actors]
    metas = [m for m in metas if m is not None]
    if crit.kleur and not any(m.kleur in crit.kleur for m in metas):
        return False
    if crit.grootte and not any(str(m.grootte) in crit.grootte for m in metas if m.grootte):
        return False
    if crit.decennia and not any(m.decennia in crit.decennia for m in metas):
        return False
    if crit.film_size and size_bucket(ctx.film_size.get(e.film_path, 0)) not in crit.film_size:
        return False
    if crit.stars and str(e.marker.stars) not in crit.stars:
        return False
    q = crit.search.strip().lower()
    if q and not (q in e.film_name.lower() or any(q in n.lower() for n in e.actor_names)):
        return False
    return True


def marker_sort_key(e: MarkerEntry, key: str, ctx: MarkerContext):
    if key == "film":
        return (e.film_name.lower(), e.time)
    if key == "tijd":
        return e.time
    if key == "sterren":
        return e.marker.stars
    if key == "acteur":
        return e.actor_names[0].lower() if e.actor_names else ""
    if key == "decade":
        for a in e.marker.actors:
            m = ctx.actor_meta.get(a)
            if m and m.decennia:
                return decade_sort_key(m.decennia)
        return 99
    if key == "maat":
        for a in e.marker.actors:
            m = ctx.actor_meta.get(a)
            if m and m.grootte:
                return m.grootte
        return 0
    if key == "filmgr":
        return ctx.film_size.get(e.film_path, 0)
    if key == "rating":
        return ctx.film_rating.get(e.film_path, 0.0)
    return 0


def filter_and_sort_markers(entries: list[MarkerEntry], crit: MarkerCriteria, ctx: MarkerContext,
                            sort_key: str = "", ascending: bool = True,
                            shuffle_seed: int | None = None) -> list[MarkerEntry]:
    out = [e for e in entries if marker_matches(e, crit, ctx)]
    if shuffle_seed is not None:
        import random
        random.Random(shuffle_seed).shuffle(out)
    elif sort_key:
        out.sort(key=lambda e: marker_sort_key(e, sort_key, ctx), reverse=not ascending)
    return out


def actor_marker_counts(entries: list[MarkerEntry], markertype: set[int]) -> dict[int, int]:
    """{actor_id: aantal markers} binnen de markertype-filter (voor de acteurlijst)."""
    counts: dict[int, int] = {}
    for e in entries:
        if markertype and not (markertype & set(e.marker.categories)):
            continue
        for a in e.marker.actors:
            counts[a] = counts.get(a, 0) + 1
    return counts
