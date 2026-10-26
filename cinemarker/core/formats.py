"""
CineMarker — tekstformattering en invoer-parsers (één plek, geen Qt).

Alle tijd-, grootte- en decennia-notaties staan hier zodat elk paneel
dezelfde weergave gebruikt.
"""

from __future__ import annotations

# ── Tijd ────────────────────────────────────────────────────────────────────

def format_time(seconds: float | None) -> str:
    """HH:MM:SS.mmm — voor CSV-export en thumbnail-bestandsnamen."""
    if seconds is None or seconds < 0:
        return "00:00:00.000"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    if ms == 1000:          # afronding op .9995 → volgende seconde
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def fmt_hms(seconds: float | None) -> str:
    """HH:MM:SS — tijdlabel in de speler en markerlijsten."""
    if seconds is None or seconds < 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_compact(seconds: float | None) -> str:
    """M:SS onder een uur, anders H:MM:SS — voor badges op kaarten."""
    if seconds is None or seconds <= 0:
        return ""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_duration(text: str) -> float:
    """Gebruikersinvoer → seconden. Leeg of ongeldig = 0.0 (geen filter).

      '10'       → 10 minuten
      '10:30'    → 10 min 30 sec
      '1:10:30'  → 1 u 10 min 30 sec
    """
    text = (text or "").strip()
    if not text:
        return 0.0
    parts = text.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0]) * 60
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    return 0.0


# ── Bestandsgrootte ─────────────────────────────────────────────────────────

GB = 1_073_741_824
MB = 1_048_576


def parse_size(text: str) -> float:
    """Gebruikersinvoer → bytes. Leeg of ongeldig = 0.0 (geen filter).

      '500'     → 500 MB (zonder suffix altijd MB)
      '1.5 GB'  → 1.5 GB      '500 MB' → 500 MB
    """
    text = (text or "").strip().lower().replace(" ", "")
    if not text:
        return 0.0
    try:
        if text.endswith("gb"):
            return float(text[:-2]) * GB
        if text.endswith("g"):
            return float(text[:-1]) * GB
        if text.endswith("mb"):
            return float(text[:-2]) * MB
        if text.endswith("m"):
            return float(text[:-1]) * MB
        return float(text) * MB
    except ValueError:
        return 0.0


def fmt_size(size_bytes: int | float | None) -> str:
    """'1.2 GB' of '850 MB' — voor badges."""
    if not size_bytes or size_bytes <= 0:
        return ""
    if size_bytes >= GB:
        return f"{size_bytes / GB:.1f} GB"
    if size_bytes >= MB:
        return f"{round(size_bytes / MB)} MB"
    return f"{max(1, round(size_bytes / 1024))} KB"


SIZE_BUCKETS: tuple[tuple[str, str], ...] = (
    ("S", "S  < 0,5 GB"),
    ("M", "M  0,5–2 GB"),
    ("L", "L  2–5 GB"),
    ("XL", "XL  > 5 GB"),
)


def size_bucket(size_bytes: int | float | None) -> str:
    """Categoriseer bestandsgrootte in S/M/L/XL."""
    gb = (size_bytes or 0) / GB
    if gb < 0.5:
        return "S"
    if gb < 2.0:
        return "M"
    if gb < 5.0:
        return "L"
    return "XL"


# ── Acteur-metadata (één gedeelde lijst voor alle panelen) ──────────────────

# Decennia worden in de notes-JSON opgeslagen als één cijfer: '3'..'9' = 30s..90s,
# '0' = 00s, '1' = 10s, '2' = 20s.  Deze volgorde is chronologisch.
DECADE_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    [(str(d), f"{d * 10}s") for d in range(3, 10)] + [("0", "00s"), ("1", "10s"), ("2", "20s")]
)
_DECADE_ORDER = {key: i for i, (key, _) in enumerate(DECADE_OPTIONS)}


def decade_label(key: str | int | None) -> str:
    """'7' → '70s', '0' → '00s'. Onbekend → ''."""
    k = str(key).strip() if key not in (None, "") else ""
    for opt_key, label in DECADE_OPTIONS:
        if opt_key == k:
            return label
    return ""


def decade_sort_key(key: str | int | None) -> int:
    """Chronologische sorteersleutel; onbekend sorteert achteraan."""
    k = str(key).strip() if key not in (None, "") else ""
    return _DECADE_ORDER.get(k, len(DECADE_OPTIONS))


GROOTTE_MIN, GROOTTE_MAX = 1, 5
RATING_MIN, RATING_MAX = 1, 9


def clamp_grootte(value: str | int | None) -> int:
    """Acteur-grootte als int 1–5; 0 = onbekend. Oudere data (5–9-schaal) wordt
    op 5 geklemd zodat de kaart nooit meer dan vijf sterren tekent."""
    try:
        v = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    return min(v, GROOTTE_MAX)


def to_int(value: object, default: int = 0) -> int:
    """Tolerante int-conversie voor metadata-velden ('7', 7, '', None)."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default
