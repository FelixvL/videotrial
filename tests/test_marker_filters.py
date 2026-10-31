from cinemarker.core.filters import (MarkerContext, MarkerCriteria, actor_marker_counts, filter_and_sort_markers,
                                     marker_matches)
from cinemarker.core.formats import GB
from cinemarker.core.models import ActorMeta, Marker, MarkerEntry


def entry(path="C:/f/a.mp4", name="Alpha", time=1.0, actors=(1,), cats=(10,), stars=0, names=("Jan",)) -> MarkerEntry:
    return MarkerEntry(marker=Marker(time=time, actors=list(actors), categories=list(cats), stars=stars),
                       film_path=path, film_name=name, actor_names=list(names))


def ctx() -> MarkerContext:
    return MarkerContext(
        actor_meta={1: ActorMeta(kleur="2", grootte=3, rating=8, decennia="7"),
                    2: ActorMeta(grootte=5, rating=5, decennia="0")},
        actor_traits={1: {100}}, film_cats={5: {50}}, film_ids={"C:/f/a.mp4": 5},
        film_size={"C:/f/a.mp4": 3 * GB}, film_rating={"C:/f/a.mp4": 7.0},
    )


def test_marker_filters():
    e = entry(stars=4)
    c = ctx()
    assert marker_matches(e, MarkerCriteria(), c)
    assert marker_matches(e, MarkerCriteria(markertype={10}), c)
    assert not marker_matches(e, MarkerCriteria(markertype={11}), c)
    assert marker_matches(e, MarkerCriteria(include_actors={1}), c)
    assert not marker_matches(e, MarkerCriteria(exclude_actors={1}), c)
    assert marker_matches(e, MarkerCriteria(traits={100}), c)
    assert marker_matches(e, MarkerCriteria(film_cats={50}), c)
    assert marker_matches(e, MarkerCriteria(kleur={"2"}, grootte={"3"}, decennia={"7"}), c)
    assert not marker_matches(e, MarkerCriteria(grootte={"4"}), c)
    assert marker_matches(e, MarkerCriteria(rating={"8"}), c)
    assert not marker_matches(e, MarkerCriteria(rating={"5"}), c)
    assert marker_matches(e, MarkerCriteria(film_size={"L"}), c)
    assert marker_matches(e, MarkerCriteria(stars={"4"}), c)
    assert marker_matches(e, MarkerCriteria(search="alp"), c) and marker_matches(e, MarkerCriteria(search="jan"), c)
    assert not marker_matches(e, MarkerCriteria(search="zzz"), c)


def test_marker_sorting_and_shuffle_is_stable():
    c = ctx()
    es = [entry(name="b", time=5, stars=1, actors=(2,), names=("Zed",)), entry(name="a", time=2, stars=3)]
    assert [x.film_name for x in filter_and_sort_markers(es, MarkerCriteria(), c, "film", True)] == ["a", "b"]
    assert [x.time for x in filter_and_sort_markers(es, MarkerCriteria(), c, "tijd", False)] == [5, 2]
    assert [x.film_name for x in filter_and_sort_markers(es, MarkerCriteria(), c, "sterren", False)] == ["a", "b"]
    assert [x.film_name for x in filter_and_sort_markers(es, MarkerCriteria(), c, "acteur", True)] == ["a", "b"]
    assert [x.film_name for x in filter_and_sort_markers(es, MarkerCriteria(), c, "decade", True)] == ["a", "b"]
    assert [x.film_name for x in filter_and_sort_markers(es, MarkerCriteria(), c, "maat", False)] == ["b", "a"]
    assert [x.film_name for x in filter_and_sort_markers(es, MarkerCriteria(), c, "actrating", False)] == ["a", "b"]
    s1 = filter_and_sort_markers(es * 5, MarkerCriteria(), c, shuffle_seed=42)
    s2 = filter_and_sort_markers(es * 5, MarkerCriteria(), c, shuffle_seed=42)
    assert [x.time for x in s1] == [x.time for x in s2]


def test_actor_counts_within_markertype():
    es = [entry(actors=(1, 2), cats=(10,)), entry(actors=(2,), cats=(11,))]
    assert actor_marker_counts(es, set()) == {1: 1, 2: 2}
    assert actor_marker_counts(es, {10}) == {1: 1, 2: 1}
