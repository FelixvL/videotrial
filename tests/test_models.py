from cinemarker.core import database as db
from cinemarker.core.models import (ZOOM_ACTORS, ZOOM_FILMS, ZOOM_MARKERS, ActorMeta, Marker, ZoomSpec)


def test_marker_roundtrip():
    m = Marker.from_dict({"time": "3.5", "name": "x", "actors": ["1"], "categories": [2],
                          "negative": 0, "stars": None})
    assert m.time == 3.5 and m.actors == [1] and m.categories == [2] and m.stars == 0
    d = m.to_dict()
    assert d["stars"] is None
    n = Marker.new(1.0, "n", [1], [2], stars=3)
    assert n.created and n.created_at > 0 and n.to_dict()["stars"] == 3


def test_zoomspec_base_and_large_range():
    assert ZOOM_FILMS.size(0) == (192, 108)                       # niveau 0 = oude basismaat
    assert ZOOM_FILMS.size(1) == (240, 135)                       # +25 % per stap
    assert ZOOM_FILMS.size(ZOOM_FILMS.max_level)[0] >= 192 * 9    # ruim 9× zo groot
    assert ZOOM_FILMS.size(-99) == ZOOM_FILMS.size(ZOOM_FILMS.min_level)
    assert ZOOM_FILMS.size(ZOOM_FILMS.min_level)[0] >= 64
    assert ZOOM_ACTORS.size(0) == (160, 206)
    assert ZOOM_MARKERS.size(ZOOM_MARKERS.max_level)[0] >= 320 * 5
    assert ZOOM_FILMS.display_index(ZOOM_FILMS.min_level) == 1
    assert ZOOM_FILMS.display_index(ZOOM_FILMS.max_level) == ZOOM_FILMS.level_count
    s = ZoomSpec("k", 100, -1, 1)
    assert s.level_count == 3 and s.size(1)[0] == 125


def test_actor_meta_from_notes_and_merge():
    meta = ActorMeta.from_notes({"voornaam": "Jan", "grootte": "8", "rating": "7", "decennia": "9"})
    assert meta.grootte == 5 and meta.rating == 7 and meta.in_db and meta.display_name == "Jan"
    aid = db.create_actor("Testacteur")
    db.update_actor_meta(aid, {"voornaam": "A", "piek_start": "2001-01"})
    db.update_actor_meta(aid, {"voornaam": "B", "achternaam": "C"})      # merge: piek blijft
    got = db.get_actor(aid)
    import json
    notes = json.loads(got["notes"])
    assert notes == {"voornaam": "B", "achternaam": "C", "piek_start": "2001-01"}
    db.update_actor_meta(aid, {"achternaam": ""})                        # leeg = verwijderen
    assert "achternaam" not in json.loads(db.get_actor(aid)["notes"])
    db.update_actor_meta(aid, {"x": "1"}, replace=True)
    assert json.loads(db.get_actor(aid)["notes"]) == {"x": "1"}
    db.delete_actor(aid)
