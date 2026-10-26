from cinemarker.core import formats as F


def test_format_time_and_hms():
    assert F.format_time(0) == "00:00:00.000"
    assert F.format_time(3661.5) == "01:01:01.500"
    assert F.format_time(None) == "00:00:00.000"
    assert F.fmt_hms(3661.9) == "01:01:01"
    assert F.fmt_hms(-1) == "00:00:00"


def test_fmt_compact_no_overflow_after_one_hour():
    assert F.fmt_compact(59) == "0:59"
    assert F.fmt_compact(75 * 60 + 3) == "1:15:03"
    assert F.fmt_compact(0) == ""


def test_parse_duration():
    assert F.parse_duration("") == 0.0
    assert F.parse_duration("10") == 600
    assert F.parse_duration("10:30") == 630
    assert F.parse_duration("1:10:30") == 4230
    assert F.parse_duration("abc") == 0.0


def test_parse_size_and_bucket():
    assert F.parse_size("500") == 500 * F.MB
    assert F.parse_size("1.5 GB") == 1.5 * F.GB
    assert F.parse_size("2g") == 2 * F.GB
    assert F.parse_size("300mb") == 300 * F.MB
    assert F.parse_size("x") == 0.0
    assert F.size_bucket(0.2 * F.GB) == "S"
    assert F.size_bucket(1 * F.GB) == "M"
    assert F.size_bucket(3 * F.GB) == "L"
    assert F.size_bucket(9 * F.GB) == "XL"
    assert F.fmt_size(1.5 * F.GB) == "1.5 GB"
    assert F.fmt_size(300 * F.MB) == "300 MB"


def test_decades_shared_list_and_order():
    labels = [lbl for _, lbl in F.DECADE_OPTIONS]
    assert labels == ["30s", "40s", "50s", "60s", "70s", "80s", "90s", "00s", "10s", "20s"]
    assert F.decade_label("0") == "00s"
    assert F.decade_label(7) == "70s"
    assert F.decade_label("") == ""
    assert F.decade_sort_key("7") < F.decade_sort_key("0") < F.decade_sort_key("2")
    assert F.decade_sort_key("") > F.decade_sort_key("2")


def test_grootte_clamped_to_five():
    assert F.clamp_grootte("3") == 3
    assert F.clamp_grootte(9) == 5          # oude 5–9-schaal
    assert F.clamp_grootte("") == 0
    assert F.clamp_grootte(None) == 0
    assert F.to_int("7") == 7 and F.to_int("x", -1) == -1
