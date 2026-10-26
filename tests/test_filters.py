from cinemarker.core.filters import FilmCriteria, film_matches, sort_film_cards
from cinemarker.core.formats import GB, MB
from cinemarker.core.models import FilmCard


def card(**kw) -> FilmCard:
    base = dict(path="C:/f/a.mp4", name="Alpha", film_id=1)
    base.update(kw)
    return FilmCard(**base)


def test_text_thumb_marker_filters():
    c = card(thumbnails=["t1"], markers=2, neg_markers=1)
    assert film_matches(c, FilmCriteria(search="alp"), None)
    assert not film_matches(c, FilmCriteria(search="zzz"), None)
    assert film_matches(c, FilmCriteria(one_thumb=True), None)
    assert not film_matches(c, FilmCriteria(multi_thumb=True), None)
    assert not film_matches(c, FilmCriteria(no_thumb=True), None)
    assert film_matches(c, FilmCriteria(with_markers=True), None)
    assert not film_matches(c, FilmCriteria(no_markers=True), None)


def test_duration_and_size_rules():
    unknown = card(duration=0, size=3 * GB)
    assert film_matches(unknown, FilmCriteria(dur_min="10"), None)       # onbekend laat min door
    assert not film_matches(unknown, FilmCriteria(dur_max="10"), None)   # onbekend verborgen bij max
    long = card(duration=3600)
    assert not film_matches(long, FilmCriteria(dur_max="30"), None)
    assert film_matches(long, FilmCriteria(dur_min="30"), None)
    assert not film_matches(unknown, FilmCriteria(sz_max="1 GB"), None)
    assert film_matches(unknown, FilmCriteria(sz_min="500"), None)
    assert film_matches(unknown, FilmCriteria(film_size={"L"}), None)
    assert not film_matches(unknown, FilmCriteria(film_size={"S"}), None)


def test_marker_cats_and_cross_ids():
    c = card(marker_cat_ids={3, 4})
    assert film_matches(c, FilmCriteria(marker_cats={4}), None)
    assert not film_matches(c, FilmCriteria(marker_cats={9}), None)
    assert film_matches(c, FilmCriteria(), {1, 2})
    assert not film_matches(c, FilmCriteria(), {2})
    assert not film_matches(card(film_id=None), FilmCriteria(), {2})


def test_preset_roundtrip_and_legacy_keys():
    crit = FilmCriteria(search="x", one_thumb=True, marker_cats={1, 2}, film_cats={5}, actor_groo={"3"},
                        actor_dec={"7"}, actors={4: "Jan"})
    d = crit.to_preset()
    d["ag_min"] = "2"                       # legacy, moet genegeerd worden
    back = FilmCriteria.from_preset(d)
    assert back == crit
    assert FilmCriteria.from_preset({}).is_empty()
    assert not crit.is_empty() and crit.needs_cross()


def test_sorting():
    cards = [card(name="b", size=2 * MB, markers=1), card(name="a", size=5 * MB, markers=3)]
    assert [c.name for c in sort_film_cards(cards, "name", True)] == ["a", "b"]
    assert [c.name for c in sort_film_cards(cards, "size", False)] == ["a", "b"]
    assert [c.name for c in sort_film_cards(cards, "markers", True)] == ["b", "a"]
    assert [c.name for c in sort_film_cards(cards, "", True)] == ["b", "a"]
