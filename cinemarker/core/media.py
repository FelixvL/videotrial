"""
CineMarker — media-hulpfuncties rond ffmpeg/ffprobe en bestandsnamen (geen Qt).

De QThread-workers in de UI roepen deze functies aan; de logica zelf is
daardoor los testbaar.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger("cinemarker.media")

VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".avi", ".mov", ".wmv", ".mkv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".divx", ".vob", ".rmvb", ".rm", ".3gp",
})
PHOTO_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif",
})

FRAME_W, FRAME_H = 320, 180          # marker-frames (breed genoeg voor alle zoomniveaus)

# Op Windows geen zwart consolevenster per ffmpeg-aanroep
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def is_photo(path: str | Path) -> bool:
    return Path(path).suffix.lower() in PHOTO_EXTS


# ── ffprobe / ffmpeg ────────────────────────────────────────────────────────

def ffprobe_duration(path: str, timeout: float = 6.0) -> float:
    """Duur in seconden via ffprobe; 0.0 bij elke fout."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=timeout, **_NO_WINDOW,
        )
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        _log.debug("ffprobe mislukt voor %s: %s", path, e)
        return 0.0


def frame_extract_cmd(video: str, time_sec: float, dest: str, scaled: bool = True) -> list[str]:
    """ffmpeg-commando voor één frame (markerthumbnail). `scaled=False` is de
    fallback voor formaten waar de scale-filter op stukloopt."""
    cmd = ["ffmpeg", "-y", "-ss", str(time_sec), "-i", video, "-frames:v", "1"]
    if scaled:
        cmd += ["-vf", f"scale={FRAME_W}:{FRAME_H}"]
    cmd += ["-q:v", "2", dest]
    return cmd


def thumbnail_export_cmd(video: str, time_sec: float, dest: str) -> list[str]:
    """ffmpeg-commando voor de T-export (ruw frame, volle resolutie)."""
    return ["ffmpeg", "-y", "-ss", str(time_sec), "-i", video, "-vframes", "1", "-q:v", "2", dest]


def run_ffmpeg(cmd: list[str], timeout: float = 30.0,
               proc_holder: list | None = None) -> bool:
    """Voer een ffmpeg-commando uit. `proc_holder` (lijst) krijgt het Popen-
    object zodat een worker het proces direct kan killen bij stop()."""
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_NO_WINDOW)
        if proc_holder is not None:
            proc_holder[:] = [proc]
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return proc.returncode == 0
    except OSError as e:
        _log.warning("ffmpeg niet gestart (%s): %s", cmd[:1], e)
        return False
    finally:
        if proc_holder is not None:
            proc_holder[:] = []


def marker_frame_cache_name(video: str, time_sec: float) -> str:
    """Cachebestandsnaam voor een markerframe; gedeeld door Markers-paneel,
    acteur-detail en de marker-modus van het Films-paneel."""
    return f"{Path(video).stem}_{int(time_sec * 1000)}_w{FRAME_W}.jpg"


_MARKER_FRAME_RE = re.compile(r"^(.+)_(\d+)_w\d+$")


def marker_frames_by_stem(cache_dir: Path) -> dict[str, list[str]]:
    """Alle gecachte markerframes gegroepeerd per filmstam (voor marker-modus)."""
    result: dict[str, list[str]] = {}
    try:
        for p in cache_dir.iterdir():
            if p.suffix.lower() != ".jpg":
                continue
            m = _MARKER_FRAME_RE.match(p.stem)
            if m:
                result.setdefault(m.group(1), []).append(str(p))
    except OSError:
        pass
    for lst in result.values():
        lst.sort()
    return result


# ── Bestandsnamen ───────────────────────────────────────────────────────────

_CAMEL_RE = re.compile(r"([a-z])([A-Z])")
_SEP_RE = re.compile(r"[_\-\.\s,()\[\]{}]+")


def filename_words(path: str | Path) -> set[str]:
    """Woorden uit een bestandsnaam (camelCase en scheidingstekens gesplitst)."""
    stem = Path(path).stem
    normalized = _CAMEL_RE.sub(r"\1 \2", stem)
    normalized = _SEP_RE.sub(" ", normalized).lower()
    return set(normalized.split())


def suggest_actors(path: str | Path, actors: list[dict]) -> list[dict]:
    """Acteurs waarvan de voornaam (eerste woord van `name`) in de bestandsnaam staat."""
    words = filename_words(path)
    out = []
    for a in actors:
        name = (a.get("name") or "").strip()
        if name and name.split()[0].lower() in words:
            out.append(a)
    return out


_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


def archive_filename(source: Path, actor_names: list[str]) -> str:
    """'CMARCH Voornaam Achternaam … origineel.ext' (zonder acteurs: 'CMARCH origineel.ext')."""
    safe = [_UNSAFE_RE.sub("", n).strip() for n in actor_names]
    safe = [n for n in safe if n]
    prefix = f"CMARCH {' '.join(safe)} " if safe else "CMARCH "
    return prefix + source.name


def move_film_to_deleted(file_path: str) -> tuple[bool, str]:
    """Verplaats een film (+ markers-JSON) naar <map>/deleted/. Geeft (ok, foutmelding).

    Het DB-record wordt NIET verwijderd; dat doet de aanroeper (db.delete_film_by_path),
    zodat deze functie ook bruikbaar is als het bestand al weg is.
    """
    from .markers_io import markers_file_for   # lokaal: markers_io importeert database
    p = Path(file_path)
    if not p.exists():
        return True, ""
    deleted_dir = p.parent / "deleted"
    try:
        deleted_dir.mkdir(exist_ok=True)
    except OSError as e:
        return False, f"Kan map 'deleted' niet aanmaken:\n{e}"
    dest = unique_path(deleted_dir / p.name)
    try:
        p.rename(dest)
    except OSError as e:
        return False, f"Kan bestand niet verplaatsen:\n{e}"
    mf = markers_file_for(p)
    if mf.exists():
        try:
            mf.rename(unique_path(deleted_dir / mf.name))
        except OSError as e:
            _log.warning("Markerbestand niet meeverplaatst: %s (%s)", mf, e)
    return True, ""


def unique_path(dest: Path, style: str = "underscore") -> Path:
    """Vrij pad in dezelfde map: 'naam_1.ext' (underscore) of 'naam (2).ext' (paren)."""
    if not dest.exists():
        return dest
    n = 1 if style == "underscore" else 2
    while True:
        cand = (dest.with_name(f"{dest.stem}_{n}{dest.suffix}") if style == "underscore"
                else dest.with_name(f"{dest.stem} ({n}){dest.suffix}"))
        if not cand.exists():
            return cand
        n += 1
