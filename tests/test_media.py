from pathlib import Path

from cinemarker.core import media


def test_filename_words_and_suggest():
    words = media.filename_words("C:/x/JohnDoe_and-Jane.Smith (2019).mp4")
    assert {"john", "doe", "and", "jane", "smith", "2019"} <= words
    actors = [{"name": "John Doe"}, {"name": "Jane"}, {"name": "Bob Ross"}, {"name": ""}]
    got = media.suggest_actors("JohnDoe_and-Jane.mp4", actors)
    assert [a["name"] for a in got] == ["John Doe", "Jane"]


def test_archive_filename():
    assert media.archive_filename(Path("film.mp4"), ["Jan Jansen", 'K<l>:e"ur']) == "CMARCH Jan Jansen Kleur film.mp4"
    assert media.archive_filename(Path("film.mp4"), []) == "CMARCH film.mp4"


def test_unique_path(tmp_path: Path):
    p = tmp_path / "a.mp4"
    assert media.unique_path(p) == p
    p.write_bytes(b"")
    assert media.unique_path(p).name == "a_1.mp4"
    assert media.unique_path(p, style="paren").name == "a (2).mp4"


def test_frame_cache_name_and_scan(tmp_path: Path):
    name = media.marker_frame_cache_name("C:/films/Mijn Film.mkv", 12.345)
    assert name == "Mijn Film_12345_w320.jpg"
    (tmp_path / name).write_bytes(b"")
    (tmp_path / "Andere_5000_w320.jpg").write_bytes(b"")
    (tmp_path / "los.jpg").write_bytes(b"")
    grouped = media.marker_frames_by_stem(tmp_path)
    assert set(grouped) == {"Mijn Film", "Andere"}


def test_commands():
    cmd = media.frame_extract_cmd("v.mp4", 3.0, "out.jpg")
    assert cmd[:2] == ["ffmpeg", "-y"] and "-vf" in cmd and cmd[-1] == "out.jpg"
    assert "-vf" not in media.frame_extract_cmd("v.mp4", 3.0, "out.jpg", scaled=False)
    assert media.is_video("a.MKV") and not media.is_video("a.jpg")
    assert media.is_photo("a.PNG")
