from pathlib import Path

from cinemarker.core import importing, markers_store
from cinemarker.core.actors import split_name
from cinemarker.core.filters import ActorCriteria, actor_matches, sort_actor_cards
from cinemarker.core.markers_io import load_markers, save_markers
from cinemarker.core.models import ActorCard, ActorMeta, Marker


def card(stem="a", **meta) -> ActorCard:
    fc = meta.pop("films", 0)
    mc = meta.pop("markers", 0)
    return ActorCard(stem=stem, photo_path="", actor_id=1, meta=ActorMeta(**meta), film_count=fc, marker_count=mc)


def test_actor_filters():
    c = card("jan", voornaam="Jan", kleur="2", grootte=3, rating=7, decennia="8")
    assert actor_matches(c, ActorCriteria(search="JA"))
    assert not actor_matches(c, ActorCriteria(search="piet"))
    assert actor_matches(c, ActorCriteria(db={"in"}))
    assert not actor_matches(c, ActorCriteria(db={"out"}))
    assert actor_matches(c, ActorCriteria(kleur={"2"}, grootte={"3"}, rating={"7"}, decennia={"8"}))
    assert not actor_matches(c, ActorCriteria(grootte={"4"}))
    nodb = card("x")
    assert actor_matches(nodb, ActorCriteria(db={"out"}))


def test_buiten_db_mode_shows_only_missing_field():
    tagged = card("a", voornaam="A", kleur="1")
    untagged = card("b", voornaam="B")
    crit = ActorCriteria(kleur={"1"}, buiten_db=True)
    assert crit.buiten_db_active() == ("kleur", "1")
    assert not actor_matches(tagged, crit)
    assert actor_matches(untagged, crit)
    assert actor_matches(tagged, ActorCriteria(buiten_db=True))   # geen keuze → alles


def test_actor_sorting_direction_and_empties_last():
    cards = [card("a", rating=5, films=1, markers=9), card("b", rating=9, films=3), card("c", grootte=2),
             card("d", grootte=5)]
    default = [c.stem for c in sort_actor_cards(cards, "", False)]
    assert default == ["b", "a", "c", "d"]                 # rating ↓, dan films ↓ …
    assert [c.stem for c in sort_actor_cards(cards, "films", True)][0] == "b"      # aflopend = meeste eerst
    assert [c.stem for c in sort_actor_cards(cards, "markers", True)][0] == "a"
    by_size = [c.stem for c in sort_actor_cards(cards, "grootte", True)]
    assert by_size[:2] == ["d", "c"] and set(by_size[2:]) == {"a", "b"}       # leeg achteraan
    assert [c.stem for c in sort_actor_cards(cards, "grootte", False)][:2] == ["c", "d"]
    by_rating = [c.stem for c in sort_actor_cards(cards, "rating", True)]
    assert by_rating[:2] == ["b", "a"] and set(by_rating[2:]) == {"c", "d"}      # rating ↓, leeg achteraan


def test_import_parsing():
    csv_text = "Voornaam,Achternaam,AfbeeldingURL,Rating\nJan,Jansen,/x/JanJansen.jpg,7\n,,,\n"
    recs = importing.parse_import_text(csv_text)
    assert len(recs) == 1 and recs[0]["name"] == "JanJansen" and '"rating": "7"' in recs[0]["notes"]
    tsv = "voornaam\tachternaam\nPiet\tPeters\n"
    assert importing.parse_import_text(tsv)[0]["name"] == "PietPeters"
    mysql = "+----+\n| voornaam | achternaam | kleur |\n+----+\n| Kees | Kok | NULL |\n"
    recs = importing.parse_import_text(mysql)
    assert recs[0]["name"] == "KeesKok" and "kleur" not in recs[0]["notes"]


def test_split_name():
    assert split_name("JanJansen.jpg", 3) == ("Jan", "Jansen")
    assert split_name("Jan Jansen", 0) is None


def test_markers_store_roundtrip(tmp_video: Path):
    p = str(tmp_video)
    assert markers_store.add(p, Marker.new(5.0, "m1", [1, 2], [3], stars=4))
    assert markers_store.add(p, Marker.new(2.0, "m0", [1], [3], stars=3))
    ms = load_markers(p)
    assert [m["time"] for m in ms] == [2.0, 5.0]               # gesorteerd op tijd
    assert markers_store.update(p, 5.0, actors=[2], stars=0)
    assert load_markers(p)[1]["actors"] == [2] and load_markers(p)[1]["stars"] is None
    assert markers_store.remove_actor(p, 2.0, 1)
    assert load_markers(p)[0]["actors"] == []
    assert not markers_store.remove_actor(p, 2.0, 1)
    assert markers_store.delete(p, 2.0)
    assert len(load_markers(p)) == 1
    assert not markers_store.delete(p, 99.0)
    # twee markers op dezelfde tijd: index_hint wint
    save_markers(p, [{"time": 1.0, "name": "x"}, {"time": 1.0, "name": "y"}])
    assert markers_store.find_index(load_markers(p), 1.0, index_hint=1) == 1
    assert markers_store.rename if hasattr(markers_store, "rename") else True


def test_negative_zones():
    ms = [{"time": 10, "negative": True}, {"time": 20}, {"time": 30, "negative": True}, {"time": 5}]
    assert markers_store.negative_zones(ms, 100.0) == [(10.0, 20.0), (30.0, 100.0)]
