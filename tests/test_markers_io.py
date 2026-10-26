from pathlib import Path

from cinemarker.core import markers_io as mio

NAME = "scène één — ü"


def test_roundtrip_utf8(tmp_video: Path):
    mf = mio.markers_file_for(tmp_video)
    assert mf.name == ".Film élan_markers.json"
    assert mio.load_markers(tmp_video) == []
    markers = [{"time": 1.5, "name": NAME, "actors": [1, 2], "negative": False},
               {"time": 9.0, "negative": True}]
    assert mio.save_markers(tmp_video, markers)
    assert NAME.encode("utf-8") in mf.read_bytes()
    assert mio.load_markers(tmp_video) == markers
    assert not (Path(str(mf) + ".tmp")).exists()


def test_corrupt_file_returns_empty(tmp_video: Path):
    mf = mio.markers_file_for(tmp_video)
    mf.write_text("{not json", encoding="utf-8")
    assert mio.load_markers(tmp_video) == []
    mf.write_text('{"a": 1}', encoding="utf-8")       # geen lijst
    assert mio.load_markers(tmp_video) == []


def test_iter_marker_files(tmp_video: Path, tmp_path: Path):
    mio.save_markers(tmp_video, [{"time": 1}])
    sub = tmp_path / "sub"
    sub.mkdir()
    other = sub / "b.mkv"
    other.write_bytes(b"")
    mio.save_markers(other, [{"time": 2}])
    found = sorted(p.name for p in mio.iter_marker_files(tmp_path))
    assert found == [".Film élan_markers.json", ".b_markers.json"]
    assert list(mio.iter_marker_files("")) == []
    assert list(mio.iter_marker_files(tmp_path / "nope")) == []
