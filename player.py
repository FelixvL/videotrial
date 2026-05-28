#!/usr/bin/env python3
"""
CineMarker - Professional Video Player
Requires: pip install python-mpv PyQt6
Requires: mpv and ffmpeg installed on system
"""

import sys
import os
import re
import json
import logging
import subprocess
import threading
from pathlib import Path
from datetime import datetime

# Minimale logging-setup: WARNING+ naar stdout, fouten zijn zichtbaar maar ruis blijft weg.
# Vervang StreamHandler door FileHandler(logfile) als je een log-bestand wilt bijhouden.
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s  %(levelname)-8s  %(name)s — %(message)s',
    datefmt='%H:%M:%S',
)
_log = logging.getLogger('cinemarker')

# Add common mpv install locations to PATH so python-mpv can find the DLL
for _mpv_path in [r"C:\mpv", r"C:\Program Files\mpv", r"C:\Program Files (x86)\mpv"]:
    if os.path.isdir(_mpv_path):
        os.environ["PATH"] = _mpv_path + os.pathsep + os.environ["PATH"]
# Also add the script's own directory (handy if the DLL is placed alongside player.py)
os.environ["PATH"] = os.path.dirname(os.path.abspath(__file__)) + os.pathsep + os.environ["PATH"]

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFileDialog, QListWidget,
    QListWidgetItem, QLineEdit, QComboBox, QSpinBox,
    QProgressBar, QTabWidget, QStackedWidget, QFrame, QMessageBox,
    QInputDialog, QSizePolicy, QStatusBar, QScrollArea, QStyle, QMenu,
    QDialog, QGridLayout
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QThread, QSize, QEvent
)
from PyQt6.QtGui import QFont, QIcon, QKeySequence, QShortcut, QColor, QPixmap, QCursor

try:
    import mpv
except OSError:
    app = QApplication(sys.argv)
    if sys.platform == 'win32':
        detail = (
            "Kan mpv-2.dll niet vinden.\n\n"
            "Download de Windows dev-build van mpv:\n"
            "https://sourceforge.net/projects/mpv-player-windows/files/libmpv/\n\n"
            "Pak mpv-2.dll uit en plaats hem in C:\\mpv\\ of naast player.py"
        )
    else:
        detail = "Installeer libmpv via je package manager:\n  sudo apt install libmpv-dev  (Debian/Ubuntu)\n  sudo dnf install mpv-libs     (Fedora)"
    QMessageBox.critical(None, "mpv niet gevonden", detail)
    sys.exit(1)
from actors_panel import ActorsPanel
from films_panel import FilmsPanel
from database_panel import DatabasePanel, SHORTCUT_DEFS
from sorter_panel import SorterPanel
from markers_panel import MarkersPanel
import database as db
from paths import THUMBNAILS_DIR, ensure_data_dirs, migrate_legacy_data


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def format_time(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "00:00:00.000"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _fmt_hms(seconds: float) -> str:
    """HH:MM:SS without milliseconds, for the player time label."""
    if seconds is None or seconds < 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def markers_file_for(video_path: str) -> str:
    p = Path(video_path)
    return str(p.parent / f".{p.stem}_markers.json")


def load_markers(video_path: str) -> list:
    path = markers_file_for(video_path)
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return []


def save_markers(video_path: str, markers: list):
    path = markers_file_for(video_path)
    with open(path, 'w') as f:
        json.dump(markers, f, indent=2)
    # Houd DB-tellers bij zodat _scan_folder geen JSON hoeft te lezen
    total = len(markers)
    neg   = sum(1 for m in markers if m.get('negative'))
    try:
        db.update_film_marker_counts(video_path, total, neg)
    except Exception:
        pass


# ─────────────────────────────────────────────
#  Help-tekst (HTML)
# ─────────────────────────────────────────────

_HELP_HTML = r"""
<style>
  body  { background:#0e0e0e; color:#ccc;
          font-family:'Consolas',monospace; font-size:12px; margin:16px; }
  h2    { color:#e8b86d; font-size:13px; letter-spacing:3px;
          border-bottom:1px solid #2a2a2a; padding-bottom:4px; margin-top:18px; }
  h3    { color:#888; font-size:10px; letter-spacing:2px; margin:10px 0 4px; }
  table { border-collapse:collapse; width:100%; margin-bottom:6px; }
  td    { padding:3px 8px; vertical-align:top; }
  td:first-child { color:#e8b86d; white-space:nowrap; min-width:160px; }
  tr:nth-child(even) td { background:#111; }
  .dim  { color:#555; }
</style>

<h2>GLOBAAL</h2>
<table>
<tr><td>Ctrl+O</td><td>Videobestand openen</td></tr>
<tr><td>F11</td><td>Volledig scherm aan/uit</td></tr>
<tr><td>Escape</td><td>Acteur-zoekbalk sluiten · categorie-selectie wissen · focus terug</td></tr>
</table>

<h2>▶ SPELER — toetsenbord</h2>
<table>
<tr><td>Spatie</td><td>Afspelen / pauzeren</td></tr>
<tr><td>← →</td><td>5 seconden terug / vooruit</td></tr>
<tr><td>L</td><td>Multi-tap vooruit — 1×=5s · 2×=30s · 3×=5min · 4×=30min</td></tr>
<tr><td>N</td><td>Multi-tap achteruit — zelfde stappen</td></tr>
<tr><td>L / N &nbsp;<span class="dim">(gepauzeerd)</span></td><td>1×=1 frame · 2×=5 frames · 3×=1s · 4×=5s</td></tr>
<tr><td>Home / End</td><td>Naar begin / einde springen</td></tr>
<tr><td>O</td><td>Vorige marker in de lijst (wraps rond)</td></tr>
<tr><td>P</td><td>Volgende marker in de lijst (wraps rond)</td></tr>
<tr><td>M</td><td>Marker plaatsen — toont gefixeerd frame + geselecteerde acteurs + categoriekeuze</td></tr>
<tr><td>X</td><td>Negatieve marker zetten op huidige positie</td></tr>
<tr><td>[ &nbsp;/&nbsp; ]</td><td>Afspeelsnelheid omlaag / omhoog — −50× … −1× … −0.25 · 0.25 … 1× … 50×</td></tr>
<tr><td>\</td><td>Snelheid resetten naar 1× (normaal)</td></tr>
<tr><td>+ / = &nbsp;/&nbsp; −</td><td>Inzoomen / uitzoomen op video</td></tr>
<tr><td>0</td><td>Zoom en pan resetten</td></tr>
<tr><td>T</td><td>Thumbnail exporteren (bestandskeuze)</td></tr>
<tr><td>V</td><td>Volgende film in de Films-lijst laden</td></tr>
<tr><td>Ctrl+L</td><td>Acteur-koppelen overlay openen</td></tr>
</table>

<h2>▶ SPELER — muis</h2>
<table>
<tr><td>Klik videoscherm</td><td>Focus terug naar speler (sluit acteur-zoekbalk)</td></tr>
<tr><td>Slepen <span class="dim">(ingezoomd)</span></td><td>Video pannen</td></tr>
<tr><td>Dubbelklik <span class="dim">(ingezoomd)</span></td><td>Zoom resetten</td></tr>
<tr><td>Klik op tijdlijn</td><td>Spring naar positie</td></tr>
<tr><td>Slepen op tijdlijn</td><td>Scrubben</td></tr>
<tr><td>Dubbelklik op marker <span class="dim">(rechter paneel)</span></td><td>Spring naar markerpositie</td></tr>
</table>

<h2>▶ SPELER — werkbalk &amp; overlays</h2>
<table>
<tr><td>🗑</td><td>Huidige film naar map deleted/ verplaatsen → springt naar Films-tab</td></tr>
<tr><td>1× knop <span class="dim">(amber = actief)</span></td><td>Klik = afspeelsnelheid resetten naar 1×</td></tr>
<tr><td>⏭</td><td>Volgende film in de Films-lijst</td></tr>
<tr><td>⊘ knop <span class="dim">(rood = aan)</span></td><td>Negatieve perioden overslaan aan/uit</td></tr>
<tr><td>Acteur zoeken…</td><td>Acteurs zoeken; resultaten in rechter paneel</td></tr>
<tr><td>Klik acteur-foto <span class="dim">(overlay)</span></td><td>Acteur selecteren / deselecteren voor marker</td></tr>
<tr><td>Klik categorie-icoon <span class="dim">(overlay)</span></td><td>Marker aanmaken voor geselecteerde acteurs</td></tr>
<tr><td>⊡ thumbnail-knop</td><td>Huidig frame opslaan als film-thumbnail</td></tr>
<tr><td>✕ naast marker</td><td>Marker verwijderen</td></tr>
</table>

<h2>◈ MARKERS</h2>
<table>
<tr><td>Categorie-chips</td><td>Filter op categorie (meerdere tegelijk · amber = actief · leeg = alle)</td></tr>
<tr><td>Acteurlijst links</td><td>Filter op acteur (multi-selectie · leeg = alle acteurs)</td></tr>
<tr><td>▶ N afspelen</td><td>Speler openen met gefilterde markers als afspeellijst</td></tr>
<tr><td>↺</td><td>Alle markers van alle films herladen</td></tr>
<tr><td>Dubbelklik thumbnail</td><td>Spring naar die scène in de Speler</td></tr>
</table>

<h2>🎬 FILMS</h2>
<table>
<tr><td>↻</td><td>Filmmap herladen</td></tr>
<tr><td>📁 Kies map</td><td>Andere filmmap instellen</td></tr>
<tr><td>− / +</td><td>Thumbnail-formaat aanpassen</td></tr>
<tr><td>Zoekbalk</td><td>Films filteren op naam</td></tr>
<tr><td>Naam / Grootte / Datum / Markers / Duur</td><td>Sorteren (klik nogmaals = omgekeerd)</td></tr>
<tr><td>Dubbelklik op film</td><td>Film laden en naar Speler-tab gaan</td></tr>
<tr><td>Rechtermuisklik op film</td><td>Film afspelen of naar deleted/ verplaatsen</td></tr>
</table>

<h2>◉ ACTEURS</h2>
<table>
<tr><td>Zoekbalk <span class="dim">(auto-focus)</span></td><td>Acteurs zoeken / filteren</td></tr>
<tr><td>Kleur / Rating / Grootte / Dec filters</td><td>Acteurs filteren op eigenschap (meerdere tegelijk)</td></tr>
<tr><td>Decennia / Grootte / Kleur / Markers / Films</td><td>Sorteren — eerste klik hoog→laag · tweede klik omgekeerd · ↺ reset</td></tr>
<tr><td>BUITEN DB knop</td><td>Wisselen naar map-modus: klik acteur om eigenschap direct in te stellen</td></tr>
<tr><td>↻</td><td>Fotomap herladen — nieuwe foto's in acteurfotos/ oppikken zonder herstart</td></tr>
<tr><td>📁 Map</td><td>Acteur-fotomap instellen</td></tr>
<tr><td>⬆ Import</td><td>Acteurs importeren uit CSV / TSV</td></tr>
<tr><td>− / + <span class="dim">(foto-grid)</span></td><td>Thumbnail-formaat aanpassen</td></tr>
<tr><td>› pijltje <span class="dim">(rechtsonder kaart)</span></td><td>Acteur-detailpagina openen</td></tr>
<tr><td>Categorie-chips <span class="dim">(detail)</span></td><td>Markers filteren op categorie</td></tr>
<tr><td>← Terug</td><td>Terug naar acteuroverzicht</td></tr>
<tr><td>✎ Bewerken</td><td>Acteurgegevens bewerken en opslaan</td></tr>
<tr><td>+ Koppel film</td><td>Film handmatig koppelen aan acteur</td></tr>
<tr><td>▶ Open</td><td>Film laden in de Speler</td></tr>
<tr><td>✕ Ontkoppel</td><td>Film loskoppelen van acteur</td></tr>
<tr><td>↵ Spring naar scène</td><td>Film laden op scène-positie in de Speler</td></tr>
<tr><td>✂ Exporteer</td><td>Scène als apart videobestand exporteren</td></tr>
</table>

<h2>⊕ SORTEREN</h2>
<table>
<tr><td>← →</td><td>Vorige / volgende foto</td></tr>
<tr><td>Spatie</td><td>Foto naar map p verplaatsen</td></tr>
<tr><td>M</td><td>Foto naar map m verplaatsen</td></tr>
<tr><td>📁 Kies map</td><td>Fotomap instellen</td></tr>
</table>

<h2>⊞ DATABASE</h2>
<table>
<tr><td>↻ Vernieuwen</td><td>Database-tabellen herladen</td></tr>
<tr><td>✕ Verwijder rij</td><td>Geselecteerde rij verwijderen</td></tr>
<tr><td>＋ Toevoegen</td><td>Nieuwe acteur toevoegen</td></tr>
</table>

<h2>⟳ CONVERTER</h2>
<table>
<tr><td>← Gebruik huidig video</td><td>Huidige film als invoer instellen</td></tr>
<tr><td>⟳ START CONVERSIE</td><td>Conversie starten met gekozen instellingen</td></tr>
</table>
"""

# ─────────────────────────────────────────────
#  Subtle UI click-sound (instant mouse feedback)
# ─────────────────────────────────────────────

def _build_click_wav(freq: int = 1100, duration_ms: int = 28,
                     volume: float = 0.13, sample_rate: int = 22050) -> bytes:
    """Build a tiny mono 16-bit PCM WAV for instant click feedback."""
    import math, struct
    n    = int(sample_rate * duration_ms / 1000)
    fade = max(1, n // 5)          # 20 % fade-in / fade-out to avoid pops
    pcm  = bytearray(n * 2)
    for i in range(n):
        env = min(i, n - 1 - i, fade) / fade          # 0.0 → 1.0 → 0.0
        val = int(volume * env * 32767
                  * math.sin(2 * math.pi * freq * i / sample_rate))
        struct.pack_into('<h', pcm, i * 2, max(-32768, min(32767, val)))
    data_size = len(pcm)
    header = (
        b'RIFF'                           + struct.pack('<I', 36 + data_size) +
        b'WAVEfmt '                       + struct.pack('<I', 16)             +
        struct.pack('<H', 1)              +   # PCM
        struct.pack('<H', 1)              +   # mono
        struct.pack('<I', sample_rate)    +
        struct.pack('<I', sample_rate*2)  +   # byte rate
        struct.pack('<H', 2)              +   # block align
        struct.pack('<H', 16)             +   # bits/sample
        b'data'                           + struct.pack('<I', data_size)
    )
    return bytes(header) + bytes(pcm)


try:
    import winsound as _winsound
    _CLICK_WAV   = _build_click_wav()
    _CLICK_FLAGS = (_winsound.SND_MEMORY | _winsound.SND_ASYNC
                    | _winsound.SND_NODEFAULT)
except Exception:
    _winsound    = None   # type: ignore[assignment]
    _CLICK_WAV   = None
    _CLICK_FLAGS = 0


def _play_ui_click() -> None:
    """Play the subtle click WAV asynchronously (non-blocking)."""
    if _winsound and _CLICK_WAV:
        try:
            _winsound.PlaySound(_CLICK_WAV, _CLICK_FLAGS)
        except Exception:
            pass


# ─────────────────────────────────────────────
#  FFmpeg worker threads
# ─────────────────────────────────────────────

class ThumbnailWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, video_path, timestamp, output_path):
        super().__init__()
        self.video_path = video_path
        self.timestamp = timestamp
        self.output_path = output_path

    def run(self):
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(self.timestamp),
            '-i', self.video_path,
            '-vframes', '1',
            '-q:v', '2',
            self.output_path
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            self.finished.emit(self.output_path)
        else:
            self.error.emit(result.stderr.decode())


class ConvertWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, input_path, output_path, codec, resolution, crf):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.codec = codec
        self.resolution = resolution
        self.crf = crf
        self._duration = None

    def run(self):
        # Get duration first
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', self.input_path],
            capture_output=True, text=True
        )
        try:
            self._duration = float(probe.stdout.strip())
        except Exception:
            self._duration = None

        vf = []
        if self.resolution != 'Original':
            w, h = self.resolution.split('x')
            vf.append(f"scale={w}:{h}")

        cmd = ['ffmpeg', '-y', '-i', self.input_path]
        if vf:
            cmd += ['-vf', ','.join(vf)]
        cmd += ['-c:v', self.codec, '-crf', str(self.crf), '-c:a', 'aac', '-b:a', '192k']
        cmd += ['-progress', 'pipe:1', '-nostats', self.output_path]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        while True:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith('out_time_ms='):
                try:
                    ms = int(line.split('=')[1].strip())
                    if self._duration:
                        pct = min(100, int((ms / 1_000_000) / self._duration * 100))
                        self.progress.emit(pct)
                except Exception:
                    pass

        proc.wait()
        if proc.returncode == 0:
            self.finished.emit(self.output_path)
        else:
            self.error.emit(proc.stderr.read())


# ─────────────────────────────────────────────
#  Custom Widgets
# ─────────────────────────────────────────────

class TimelineSlider(QSlider):
    """Slider that supports click-to-seek anywhere, with negative-zone overlay.

    Signals:
      seeked   — emitted during press/drag: use for fast keyframe seek (approx)
      released — emitted on mouse release:  use for exact seek (precise frame)
    """
    seeked   = pyqtSignal(float)   # press + drag → keyframe seek
    released = pyqtSignal(float)   # mouse up     → exact seek

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(0, 10000)
        self._markers  = []
        self._neg_zones:    list = []   # [(start_frac, end_frac), ...]
        self._marker_fracs: list = []   # [frac, ...]  — gewone markers
        # NoFocus: slider should never steal keyboard focus (shortcuts handle seeking)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_neg_zones(self, zones: list):
        self._neg_zones = zones
        self.update()

    def set_marker_fracs(self, fracs: list):
        """Stel de fractie-posities in van gewone (niet-negatieve) markers."""
        self._marker_fracs = fracs
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = self._pos_to_value(event.position().x())
            self.setValue(val)
            self.seeked.emit(val / 10000)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            val = self._pos_to_value(event.position().x())
            self.setValue(val)
            self.seeked.emit(val / 10000)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = self._pos_to_value(event.position().x())
            self.released.emit(val / 10000)
        super().mouseReleaseEvent(event)

    def _pos_to_value(self, x):
        w = self.width()
        return int(max(0, min(10000, x / w * 10000)))

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter, QColor
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(self)

        val_f    = self.value() / self.maximum() if self.maximum() > 0 else 0
        played_w = int(val_f * w)

        # 1 — Background
        p.fillRect(0, 0, w, h, QColor('#141414'))

        # 2 — Amber progress (non-negative parts)
        if played_w > 0:
            p.fillRect(0, 0, played_w, h, QColor('#e8b86d'))

        # 3 — Red zones (full extent, unplayed and played alike)
        for start_f, end_f in self._neg_zones:
            x0 = int(start_f * w)
            x1 = int(end_f   * w)
            if x1 > x0:
                p.fillRect(x0, 0, max(3, x1 - x0), h, QColor('#cc2222'))

        # 4 — Orange re-paint for the portion of each red zone already played
        #     so the playhead position is visible even inside negative zones
        if played_w > 0:
            for start_f, end_f in self._neg_zones:
                x0 = int(start_f * w)
                x1 = min(int(end_f * w), played_w)
                if x1 > x0:
                    p.fillRect(x0, 0, x1 - x0, h, QColor('#e87800'))

        # 5 — Marker dots (blue ticks, always on top)
        for frac in self._marker_fracs:
            x = int(frac * w)
            p.fillRect(max(0, x - 1), 0, 2, h, QColor('#4488ff'))

        p.end()


class _ClickFlash(QWidget):
    """Brief ✓ that appears in the centre of the video on each mouse click.
    Runs as a transparent top-level Tool window (same pattern as the other
    overlays) so it floats above the mpv surface without intercepting input."""

    SIZE = 72

    def __init__(self, main_win, video_container):
        super().__init__(
            main_win,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._vc    = video_container
        self._phase = 1.0             # 1.0 = invisible
        self._timer = QTimer(self)
        self._timer.setInterval(16)   # ~60 fps
        self._timer.timeout.connect(self._tick)
        self.hide()

    # ── derived opacity ──────────────────────────
    @property
    def _opacity(self) -> float:
        if self._phase < 0.25:        # hold full-bright for first ~90 ms
            return 1.0
        return max(0.0, 1.0 - (self._phase - 0.25) / 0.75)  # then fade

    # ── public ───────────────────────────────────
    def trigger(self):
        vc = self._vc
        c  = vc.mapToGlobal(vc.rect().center())
        self.move(c.x() - self.SIZE // 2, c.y() - self.SIZE // 2)
        self._phase = 0.0
        self.show()
        self.raise_()
        self.update()
        if not self._timer.isActive():
            self._timer.start()

    # ── animation ────────────────────────────────
    def _tick(self):
        self._phase += 0.044          # ~23 ticks → ~370 ms total
        self.update()
        if self._phase >= 1.0:
            self._timer.stop()
            self.hide()

    # ── paint ────────────────────────────────────
    def paintEvent(self, _event):
        op = self._opacity
        if op <= 0:
            return
        from PyQt6.QtGui import QPainter, QColor, QFont as _QFont
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sz = self.SIZE

        # Semi-transparent dark circle
        p.setBrush(QColor(0, 0, 0, int(op * 155)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, sz, sz)

        # Subtle ring
        p.setBrush(Qt.BrushStyle.NoBrush)
        from PyQt6.QtGui import QPen as _QPen
        p.setPen(_QPen(QColor(255, 255, 255, int(op * 40)), 1))
        p.drawEllipse(1, 1, sz - 2, sz - 2)

        # ✓ glyph
        f = _QFont('Segoe UI')
        f.setPointSize(28)
        f.setWeight(_QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, int(op * 230)))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '✓')


# ─────────────────────────────────────────────
#  Fade overlay — voor automatische marker-overgangen
# ─────────────────────────────────────────────

class _FadeOverlay(QLabel):
    """Crossfade-overlay voor automatische marker-overgangen.

    Aanpak:
      1. Screenshot vastleggen (vóór de seek)
      2. setWindowOpacity(1.0) — DWM-laag was al actief, geen initialisatietijd
      3. Callback uitvoeren (sprong naar volgende marker)
      4. setWindowOpacity geleidelijk naar 0.0 — hardware crossfade

    Cruciaal: het venster is ALTIJD zichtbaar maar op opacity 0.0 (idle).
    Zo heeft DWM de compositor-laag al gereserveerd; setWindowOpacity(1.0)
    is daardoor onmiddellijk — geen 'lost frame' meer door DWM-initialisatie.
    """

    _STEPS = 28   # ~450 ms fade bij 16 ms interval

    def __init__(self, main_win, video_container):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setScaledContents(True)
        self.setStyleSheet("background: #000;")
        self._main_win = main_win
        self._vc       = video_container
        self._opacity  = 0.0
        self._phase    = 'idle'
        self._timer    = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        main_win.installEventFilter(self)

        # Altijd in de DWM-laagstapel houden, volledig transparant in rust.
        # show() + opacity 0 = geen visueel effect maar wel actieve compositor-laag.
        self.setGeometry(0, 0, 1, 1)
        self.setWindowOpacity(0.0)
        self.show()

    # ── Public ───────────────────────────────────

    def trigger(self, callback, video_widget=None):
        """Crossfade: bevries huidig frame → spring direct → fade weg."""
        self._timer.stop()
        vc = video_widget or self._vc

        # Schermopname vóór de jump; gebruik devicePixelRatio voor hi-DPI
        pix  = None
        geom = None
        try:
            screen = QApplication.primaryScreen()
            dpr    = screen.devicePixelRatio()
            tl     = vc.mapToGlobal(vc.rect().topLeft())
            full   = screen.grabWindow(0)
            pix    = full.copy(
                int(tl.x() * dpr), int(tl.y() * dpr),
                int(vc.width() * dpr), int(vc.height() * dpr),
            )
            geom = (tl.x(), tl.y(), vc.width(), vc.height())
        except Exception:
            pix = None

        if not pix or pix.isNull():
            if callback:
                callback()
            return

        self.setPixmap(pix)
        self.setGeometry(*geom)
        self._opacity = 1.0
        self._phase   = 'out'
        # Venster is al zichtbaar → opacity-wissel gaat onmiddellijk via DWM
        self.setWindowOpacity(1.0)
        self.raise_()

        # Direct springen; bevroren frame dekt de video terwijl mpv al decodeert
        if callback:
            callback()

        self._timer.start()

    def abort(self):
        """Stop en zet terug naar transparant (niet verbergen)."""
        self._timer.stop()
        self._opacity = 0.0
        self._phase   = 'idle'
        self.setWindowOpacity(0.0)

    # ── Animatie ─────────────────────────────────

    def _tick(self):
        if self._phase == 'out':
            self._opacity = max(0.0, self._opacity - 1.0 / self._STEPS)
            self.setWindowOpacity(self._opacity)
            if self._opacity <= 0.0:
                self._timer.stop()
                self._phase = 'idle'
                self.setWindowOpacity(0.0)

    # ── Positie volgen ───────────────────────────

    def eventFilter(self, obj, event):
        if self._phase != 'idle' and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Move,
            QEvent.Type.Show, QEvent.Type.WindowStateChange,
        ):
            vc = self._vc
            if vc.isVisible():
                tl = vc.mapToGlobal(vc.rect().topLeft())
                self.setGeometry(tl.x(), tl.y(), vc.width(), vc.height())
        return False


# ─────────────────────────────────────────────
#  Actor Link Overlay  (floating over player)
# ─────────────────────────────────────────────

class _HtmlClickLabel(QLabel):
    """QLabel met rich-text én een clicked-signaal — vervangt QPushButton waar HTML nodig is."""
    clicked = pyqtSignal()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class _ActorLinkOverlay(QFrame):
    link_requested = pyqtSignal(dict)

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedWidth(270)
        self.setFixedHeight(320)
        self.setStyleSheet("""
            _ActorLinkOverlay, QFrame#actorOverlay {
                background: #111;
                border: 1px solid #333;
                border-radius: 8px;
            }
            QLineEdit {
                background: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                padding: 6px 8px;
                color: #e0e0e0;
                font-size: 13px;
            }
            QListWidget {
                background: #0e0e0e;
                border: none;
                color: #ccc;
                font-size: 12px;
            }
            QListWidget::item { padding: 7px 10px; border-bottom: 1px solid #181818; }
            QListWidget::item:hover { background: #1a1a1a; }
            QListWidget::item:selected { background: #2a2200; color: #e8b86d; }
        """)
        self._actors: list = []
        self._build_ui()
        self.hide()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        hdr = QHBoxLayout()
        lbl = QLabel("ACTEUR KOPPELEN")
        lbl.setStyleSheet("color: #555; font-size: 9px; letter-spacing: 3px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        btn_x = QPushButton("✕")
        btn_x.setFixedSize(20, 20)
        btn_x.setStyleSheet(
            "QPushButton { border: none; color: #444; background: transparent; }"
            "QPushButton:hover { color: #e0e0e0; }"
        )
        btn_x.clicked.connect(self.hide)
        hdr.addWidget(btn_x)
        v.addLayout(hdr)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Naam zoeken...")
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)

        self.actor_list = QListWidget()
        self.actor_list.itemClicked.connect(self._on_actor_click)
        v.addWidget(self.actor_list)

    def show_overlay(self):
        self._actors = db.get_all_actors()
        self.search.clear()
        self._filter('')
        p = self.parent()
        if p:
            self.move(p.width() - self.width() - 16, 56)
        self.show()
        self.raise_()
        self.search.setFocus()

    def _filter(self, text: str):
        self.actor_list.clear()
        q = text.lower()
        for a in self._actors:
            if not q or q in a.get('name', '').lower():
                item = QListWidgetItem(a['name'])
                item.setData(Qt.ItemDataRole.UserRole, a)
                self.actor_list.addItem(item)

    def _on_actor_click(self, item):
        a = item.data(Qt.ItemDataRole.UserRole)
        if a:
            self.link_requested.emit(a)
            self.hide()


# ─────────────────────────────────────────────
#  Film actors overlay (floating, selectable)
# ─────────────────────────────────────────────

from PyQt6.QtCore import pyqtSignal as _pyqtSignal


# ─────────────────────────────────────────────
#  Film edit panel (remove actors / thumbnails)
# ─────────────────────────────────────────────

class _FilmEditPanel(QWidget):
    """Kleine bewerkoverlap: acteurs ontkoppelen, thumbnails verwijderen."""

    data_changed = _pyqtSignal(int)   # film_id — emitted after actor/thumb removal

    _ROW_STYLE = (
        "QPushButton{background:#2a1414;border:1px solid #3a2222;"
        "border-radius:3px;color:#884444;font-size:10px;padding:0;}"
        "QPushButton:hover{border-color:#e05555;color:#e05555;background:#3a1a1a;}"
        "QPushButton:pressed{background:#e05555;color:#fff;}"
    )

    def __init__(self, main_win, video_container):
        super().__init__(
            main_win,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._vc      = video_container
        self._film_id = None
        self.setFixedWidth(264)
        self._build_ui()
        self.hide()
        main_win.installEventFilter(self)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._frame = QFrame()
        self._frame.setStyleSheet(
            "QFrame { background: rgba(10,10,10,230); border: 1px solid #2e2e2e;"
            "  border-radius: 8px; }"
        )
        fv = QVBoxLayout(self._frame)
        fv.setContentsMargins(10, 8, 10, 10)
        fv.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        lbl = QLabel("BEWERKEN")
        lbl.setStyleSheet(
            "color:#555;font-size:9px;letter-spacing:3px;background:transparent;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        btn_x = QPushButton("✕")
        btn_x.setFixedSize(18, 18)
        btn_x.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#444;font-size:10px;padding:0;}"
            "QPushButton:hover{color:#e0e0e0;}"
        )
        btn_x.clicked.connect(self.hide)
        hdr.addWidget(btn_x)
        fv.addLayout(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("QFrame{background:#222;max-height:1px;}")
        fv.addWidget(sep)

        # Scroll content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea,QScrollArea>QWidget>QWidget{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:4px;}"
            "QScrollBar::handle:vertical{background:rgba(60,60,60,180);border-radius:2px;}"
        )
        self._inner = QWidget()
        self._inner.setStyleSheet("background:transparent;")
        self._cv = QVBoxLayout(self._inner)
        self._cv.setContentsMargins(0, 0, 0, 0)
        self._cv.setSpacing(4)
        scroll.setWidget(self._inner)
        fv.addWidget(scroll, stretch=1)

        outer.addWidget(self._frame)

    # ── Public ───────────────────────────────────

    def open_for_film(self, film_id: int):
        self._film_id = film_id
        self._rebuild()
        self._reposition()
        self.show()
        self.raise_()

    # ── Rebuild content ──────────────────────────

    def _rebuild(self):
        # Remove old widgets
        while self._cv.count():
            it = self._cv.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not self._film_id:
            return

        # ── Acteurs ──────────────────────────────
        self._cv.addWidget(self._section_lbl("ACTEURS"))
        actors = db.get_actors_for_film(self._film_id)
        if actors:
            for a in actors:
                self._cv.addWidget(self._actor_row(a))
        else:
            self._cv.addWidget(self._dim_lbl("Geen acteurs gekoppeld"))

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("QFrame{background:#1e1e1e;max-height:1px;}")
        self._cv.addWidget(sep)

        # ── Thumbnails ────────────────────────────
        self._cv.addWidget(self._section_lbl("THUMBNAILS"))
        thumbs = db.get_film_thumbnails(self._film_id)
        if thumbs:
            TW, TH = 68, 38
            for i in range(0, len(thumbs), 3):
                self._cv.addWidget(self._thumb_row(thumbs[i:i + 3], TW, TH))
        else:
            self._cv.addWidget(self._dim_lbl("Geen thumbnails opgeslagen"))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("QFrame{background:#1e1e1e;max-height:1px;}")
        self._cv.addWidget(sep2)

        # ── Film categorieën ──────────────────────
        self._cv.addWidget(self._section_lbl("CATEGORIEËN"))
        all_cats    = db.get_film_categorie_types()
        active_ids  = db.get_film_category_ids(self._film_id)

        if all_cats:
            _CAT_OFF = (
                "QPushButton{background:#111;border:1px solid #1e1e1e;"
                "border-radius:3px;color:#444;font-size:10px;padding:2px 7px;}"
                "QPushButton:hover{border-color:#555;color:#aaa;}"
            )
            _CAT_ON = (
                "QPushButton{background:#001818;border:1px solid #004040;"
                "border-radius:3px;color:#4db8b8;font-size:10px;padding:2px 7px;"
                "font-weight:bold;}"
                "QPushButton:hover{border-color:#4db8b8;}"
            )

            cat_wrap = QWidget(); cat_wrap.setStyleSheet("background:transparent;")
            cat_flow = QHBoxLayout(cat_wrap)
            cat_flow.setContentsMargins(0, 0, 0, 0)
            cat_flow.setSpacing(4)

            def _make_cat_toggle(cat):
                is_on = cat['id'] in active_ids
                btn = QPushButton(cat['naam'])
                btn.setCheckable(True)
                btn.setChecked(is_on)
                btn.setStyleSheet(_CAT_ON if is_on else _CAT_OFF)

                def _toggled(checked, cid=cat['id'], b=btn):
                    b.setStyleSheet(_CAT_ON if checked else _CAT_OFF)
                    cur = db.get_film_category_ids(self._film_id)
                    if checked:
                        cur.add(cid)
                    else:
                        cur.discard(cid)
                    db.set_film_categories(self._film_id, list(cur))

                btn.toggled.connect(_toggled)
                return btn

            for cat in all_cats:
                cat_flow.addWidget(_make_cat_toggle(cat))
            cat_flow.addStretch()
            self._cv.addWidget(cat_wrap)
        else:
            self._cv.addWidget(self._dim_lbl("Geen filmcategorieën aangemaakt"))

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("QFrame{background:#1e1e1e;max-height:1px;}")
        self._cv.addWidget(sep3)

        # ── Markers ───────────────────────────────
        self._cv.addWidget(self._section_lbl("MARKERS"))
        film = db.get_film(self._film_id)
        film_path = film['file_path'] if film else None
        if film_path:
            markers = load_markers(film_path)
            actor_markers = [
                (i, m) for i, m in enumerate(markers)
                if not m.get('negative') and m.get('actors')
            ]
            if actor_markers:
                for idx, m in actor_markers:
                    self._cv.addWidget(self._marker_block(m, idx, film_path))
            else:
                self._cv.addWidget(self._dim_lbl("Geen markers met acteurs"))
        else:
            self._cv.addWidget(self._dim_lbl("Filmpad niet gevonden"))

        self._cv.addStretch()

        # Auto-size panel height
        self._inner.adjustSize()
        content_h = self._inner.sizeHint().height()
        self.setFixedHeight(min(520, content_h + 72))

    # ── Row builders ─────────────────────────────

    def _section_lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:#555;font-size:9px;letter-spacing:2px;background:transparent;")
        return lbl

    def _dim_lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#2e2e2e;font-size:10px;background:transparent;")
        return lbl

    def _actor_row(self, actor: dict) -> QWidget:
        row = QWidget(); row.setStyleSheet("background:transparent;")
        h = QHBoxLayout(row); h.setContentsMargins(0, 1, 0, 1); h.setSpacing(6)

        # Tiny photo
        lbl_p = QLabel(); lbl_p.setFixedSize(22, 28)
        lbl_p.setStyleSheet("background:#161616;border-radius:2px;")
        photos = db.get_actor_photos(actor['id'])
        if photos:
            raw = QPixmap(photos[0]['photo_path'])
            if not raw.isNull():
                sc = raw.scaled(22, 28,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                ox = (sc.width()  - 22) // 2
                oy = (sc.height() - 28) // 2
                lbl_p.setPixmap(sc.copy(ox, oy, 22, 28))
        h.addWidget(lbl_p)

        name = QLabel(actor.get('name', ''))
        name.setStyleSheet("color:#ccc;font-size:11px;background:transparent;")
        h.addWidget(name, stretch=1)

        btn = QPushButton("✕"); btn.setFixedSize(20, 20)
        btn.setStyleSheet(self._ROW_STYLE)
        btn.clicked.connect(lambda _, a=actor: self._remove_actor(a))
        h.addWidget(btn)
        return row

    def _thumb_row(self, thumbs: list, tw: int, th: int) -> QWidget:
        row = QWidget(); row.setStyleSheet("background:transparent;")
        h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        for t in thumbs:
            h.addWidget(self._thumb_cell(t, tw, th))
        h.addStretch()
        return row

    def _thumb_cell(self, thumb: dict, tw: int, th: int) -> QWidget:
        cell = QWidget(); cell.setFixedSize(tw, th + 20)
        cell.setStyleSheet("background:transparent;")

        lbl = QLabel(cell); lbl.setFixedSize(tw, th); lbl.move(0, 0)
        lbl.setStyleSheet("background:#161616;border-radius:2px;")
        path = thumb.get('path', '')
        if path and os.path.exists(path):
            raw = QPixmap(path)
            if not raw.isNull():
                sc = raw.scaled(tw, th,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                ox = (sc.width()  - tw) // 2
                oy = (sc.height() - th) // 2
                lbl.setPixmap(sc.copy(ox, oy, tw, th))

        btn = QPushButton("✕", cell); btn.setFixedSize(16, 16); btn.move(tw - 18, 2)
        btn.setStyleSheet(
            "QPushButton{background:rgba(160,30,30,200);border:none;"
            "border-radius:3px;color:#fff;font-size:9px;padding:0;}"
            "QPushButton:hover{background:rgba(220,50,50,240);}"
        )
        btn.clicked.connect(lambda _, t=thumb: self._remove_thumb(t))
        return cell

    def _marker_block(self, marker: dict, marker_idx: int, film_path: str) -> QWidget:
        """One marker: timestamp + cat icons + indented actor rows."""
        block = QWidget(); block.setStyleSheet("background:transparent;")
        v = QVBoxLayout(block)
        v.setContentsMargins(0, 3, 0, 1)
        v.setSpacing(1)

        # ── Time + category icons ─────────────────
        h_top = QHBoxLayout()
        h_top.setContentsMargins(0, 0, 0, 0)
        h_top.setSpacing(4)

        t = marker.get('time', 0)
        s = int(t)
        time_str = f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
        lbl_t = QLabel(time_str)
        lbl_t.setStyleSheet(
            "color:#4a4a4a;font-size:10px;"
            "font-family:'Consolas',monospace;background:transparent;")
        h_top.addWidget(lbl_t)

        for cat_id in (marker.get('categories') or []):
            cats = db.get_categories_by_ids([cat_id])
            if cats:
                ip = cats[0].get('icon_path', '')
                if ip and os.path.exists(ip):
                    pix = QPixmap(ip).scaled(
                        13, 13,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    ic = QLabel(); ic.setFixedSize(15, 15)
                    ic.setPixmap(pix)
                    ic.setStyleSheet("background:transparent;")
                    h_top.addWidget(ic)

        h_top.addStretch()
        v.addLayout(h_top)

        # ── Actor rows (indented) ─────────────────
        for actor_id in (marker.get('actors') or []):
            actor = db.get_actor(actor_id)
            if actor:
                v.addWidget(self._marker_actor_row(actor, marker_idx, film_path))

        return block

    def _marker_actor_row(self, actor: dict, marker_idx: int, film_path: str) -> QWidget:
        """Single actor within a marker — indented, with ✕ to unlink from that marker."""
        row = QWidget(); row.setStyleSheet("background:transparent;")
        h = QHBoxLayout(row)
        h.setContentsMargins(14, 0, 0, 0)   # indent under time label
        h.setSpacing(5)

        # Tiny photo
        lbl_p = QLabel(); lbl_p.setFixedSize(16, 20)
        lbl_p.setStyleSheet("background:#161616;border-radius:2px;")
        photos = db.get_actor_photos(actor['id'])
        if photos:
            raw = QPixmap(photos[0]['photo_path'])
            if not raw.isNull():
                sc = raw.scaled(16, 20,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                ox = (sc.width()  - 16) // 2
                oy = (sc.height() - 20) // 2
                lbl_p.setPixmap(sc.copy(ox, oy, 16, 20))
        h.addWidget(lbl_p)

        name = QLabel(actor.get('name', ''))
        name.setStyleSheet("color:#777;font-size:10px;background:transparent;")
        h.addWidget(name, stretch=1)

        btn = QPushButton("✕"); btn.setFixedSize(16, 16)
        btn.setStyleSheet(self._ROW_STYLE)
        btn.clicked.connect(
            lambda _, aid=actor['id'], mi=marker_idx, fp=film_path:
                self._remove_actor_from_marker(aid, mi, fp)
        )
        h.addWidget(btn)
        return row

    # ── Actions ──────────────────────────────────

    def _remove_actor(self, actor: dict):
        if self._film_id:
            db.unlink_actor_film(actor['id'], self._film_id)
            self._rebuild()
            self.data_changed.emit(self._film_id)

    def _remove_thumb(self, thumb: dict):
        db.delete_film_thumbnail(thumb['id'])
        if self._film_id:
            self.data_changed.emit(self._film_id)
        self.hide()

    def _remove_actor_from_marker(self, actor_id: int, marker_idx: int, film_path: str):
        markers = load_markers(film_path)
        if 0 <= marker_idx < len(markers):
            actors = list(markers[marker_idx].get('actors') or [])
            if actor_id in actors:
                actors.remove(actor_id)
                markers[marker_idx]['actors'] = actors
                save_markers(film_path, markers)
        self.hide()

    # ── Position ─────────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move,
                            QEvent.Type.Show, QEvent.Type.WindowStateChange):
            self._reposition()
        return False

    def _reposition(self):
        vc = self._vc
        if not vc.isVisible():
            return
        tl = vc.mapToGlobal(vc.rect().topLeft())
        # Just above the actors overlay (TOTAL_H=154, margin=8)
        y = tl.y() + vc.height() - 154 - 8 - self.height() - 6
        self.move(tl.x() + 8, max(tl.y() + 4, y))


# ─────────────────────────────────────────────
#  Film actors overlay (floating, selectable)
# ─────────────────────────────────────────────

class _FilmActorsOverlay(QWidget):
    """Floating overlay at bottom-left of video.
    Row 1: actor thumbnails. Row 2: category icons.
    Everything drawn in paintEvent — no child widgets for images."""

    marker_requested    = _pyqtSignal(list, list)   # actors, categories
    thumbnail_requested = _pyqtSignal()
    edit_requested      = _pyqtSignal(int)           # film_id

    TW, TH   = 52, 62   # actor thumb dims
    CW, CH   = 42, 42   # category icon dims
    SPACING  = 6
    PAD      = 6
    ROW_GAP  = 8
    BTN_W      = 36       # square action buttons (+ category)
    BTN_EDIT_W = 30       # edit label — grote E
    BTN_TW   = 80       # thumbnail button width  (~2× area vs old 56×36)
    BTN_TH   = 52       # thumbnail button height
    CELL_A   = TH + 16  # actor row height  (78)
    CELL_C   = CH + 14  # category row height (56)
    TOTAL_H  = PAD + CELL_A + ROW_GAP + CELL_C + PAD   # 154

    def __init__(self, main_win, video_container):
        super().__init__(main_win,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._vc = video_container

        self._actors:    list = []
        self._pixmaps:   list = []
        self._selected:  set  = set()   # actor ids

        self._cats:      list = []
        self._cat_pixes: list = []
        self._cat_sel:   set  = set()   # category ids

        self._film_id: int | None = None

        self.setFixedHeight(self.TOTAL_H)
        main_win.installEventFilter(self)

        self._thumb_paths: list = []
        self._thumb_idx:   int  = 0
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(2000)
        self._thumb_timer.timeout.connect(self._cycle_thumb)

        self._btn_thumb = QPushButton("⊡", self)
        self._btn_thumb.setFixedSize(self.BTN_TW, self.BTN_TH)
        self._btn_thumb.setToolTip("Klik om huidig frame als thumbnail op te slaan")
        self._btn_thumb.setStyleSheet(
            "QPushButton { background: #001a1a; border: 1px solid #006b6b;"
            "  border-radius: 4px; color: #55dede; font-size: 16px; }"
            "QPushButton:hover { background: #002a2a; border-color: #55dede; }"
            "QPushButton:pressed { background: #55dede; color: #000; }"
        )
        self._btn_thumb.clicked.connect(self.thumbnail_requested)

        self._btn_edit = _HtmlClickLabel(self)
        self._btn_edit.setFixedSize(self.BTN_EDIT_W, self.BTN_W)
        self._btn_edit.setToolTip("Acteurs en thumbnails beheren")
        self._btn_edit.setTextFormat(Qt.TextFormat.RichText)
        self._btn_edit.setText(
            '<span style="color:#cc4444;font-size:36px;font-weight:bold;line-height:1;">E</span>'
        )
        self._btn_edit.setStyleSheet("background: transparent;")
        self._btn_edit.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_edit.clicked.connect(
            lambda: self.edit_requested.emit(self._film_id) if self._film_id else None
        )

        self._place_buttons()
        main_win.installEventFilter(self)

    # ── Layout helpers ───────────────────────────

    def _actor_row_width(self):
        n = len(self._actors)
        return n * (self.TW + self.SPACING) - (self.SPACING if n else 0)

    def _cat_row_width(self):
        n = len(self._cats)
        return n * (self.CW + self.SPACING) - (self.SPACING if n else 0)

    def _total_width(self):
        # Cat row: icons + edit button + spacing + thumb button
        cat_total = (self._cat_row_width()
                     + self.SPACING + self.BTN_EDIT_W + self.SPACING + self.BTN_TW)
        return self.PAD + max(self._actor_row_width(), cat_total) + self.PAD

    def _place_buttons(self):
        cat_row_y = self.PAD + self.CELL_A + self.ROW_GAP
        # Thumbnail button — far right of the category row
        thumb_x = self._total_width() - self.PAD - self.BTN_TW
        self._btn_thumb.move(thumb_x, cat_row_y + (self.CELL_C - self.BTN_TH) // 2)
        # edit button — just left of the thumbnail button
        edit_x = thumb_x - self.SPACING - self.BTN_EDIT_W
        self._btn_edit.move(edit_x, cat_row_y + (self.CELL_C - self.BTN_W) // 2)

    # ── Paint ────────────────────────────────────

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter, QPen, QFontMetrics
        from PyQt6.QtCore import QRect as _R
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = QFontMetrics(p.font())

        # ── Actor row ────────────────────────────
        x = self.PAD
        for actor, pix in zip(self._actors, self._pixmaps):
            aid = actor['id']
            sel = aid in self._selected
            p.fillRect(x, self.PAD, self.TW, self.TH,
                       QColor('#3c3200') if sel else QColor('#1e1e1e'))
            if pix and not pix.isNull():
                p.drawPixmap(x, self.PAD, pix)
            else:
                p.fillRect(x, self.PAD, self.TW, self.TH, QColor('#2a2a2a'))
            if sel:
                from PyQt6.QtGui import QPen as _Pen
                p.setPen(_Pen(QColor('#ff2222'), 4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(x, self.PAD, self.TW - 1, self.TH - 1)
                p.setPen(Qt.PenStyle.NoPen)
            p.setPen(QColor('#aaa') if sel else QColor('#555'))
            p.drawText(_R(x, self.PAD + self.TH + 2, self.TW, 13),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       fm.elidedText(actor.get('name', ''), Qt.TextElideMode.ElideRight, self.TW))
            p.setPen(Qt.PenStyle.NoPen)
            x += self.TW + self.SPACING

        # ── Category row ─────────────────────────
        cat_y = self.PAD + self.CELL_A + self.ROW_GAP
        x = self.PAD
        for cat, cpix in zip(self._cats, self._cat_pixes):
            p.fillRect(x, cat_y, self.CW, self.CH, QColor('#141414'))
            if cpix and not cpix.isNull():
                p.drawPixmap(x, cat_y, cpix)
            else:
                p.fillRect(x, cat_y, self.CW, self.CH, QColor('#1a1a1a'))
                p.setPen(QColor('#333'))
                p.drawText(_R(x, cat_y, self.CW, self.CH), Qt.AlignmentFlag.AlignCenter, '?')
                p.setPen(Qt.PenStyle.NoPen)
            p.setPen(QColor('#555'))
            p.drawText(_R(x, cat_y + self.CH + 2, self.CW, 12),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       fm.elidedText(cat.get('name', ''), Qt.TextElideMode.ElideRight, self.CW))
            p.setPen(Qt.PenStyle.NoPen)
            x += self.CW + self.SPACING

    # ── Mouse ────────────────────────────────────

    def mousePressEvent(self, event):
        # Actor row
        x = self.PAD
        for actor in self._actors:
            if (x <= event.pos().x() <= x + self.TW and
                    self.PAD <= event.pos().y() <= self.PAD + self.TH):
                if event.button() == Qt.MouseButton.LeftButton:
                    aid = actor['id']
                    if aid in self._selected:
                        self._selected.discard(aid)
                    else:
                        self._selected.add(aid)
                    self.update()
                return
            x += self.TW + self.SPACING

        # Category row
        cat_y = self.PAD + self.CELL_A + self.ROW_GAP
        x = self.PAD
        for cat in self._cats:
            if (x <= event.pos().x() <= x + self.CW and
                    cat_y <= event.pos().y() <= cat_y + self.CH):
                if event.button() == Qt.MouseButton.LeftButton:
                    self.marker_requested.emit(self.selected_actors(), [cat])
                elif event.button() == Qt.MouseButton.RightButton:
                    self._delete_category_menu(cat)
                return
            x += self.CW + self.SPACING

        super().mousePressEvent(event)

    # ── Event filter & reposition ────────────────

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            self._reposition()
        return False

    def _reposition(self):
        vc = self._vc
        if not vc.isVisible():
            return
        tl = vc.mapToGlobal(vc.rect().topLeft())
        w = min(self._total_width(), vc.width() - 20)
        w = max(self.BTN_W * 2 + self.PAD * 2 + 4, w)
        self.setFixedWidth(w)
        self._place_buttons()
        self.move(tl.x() + 8, tl.y() + vc.height() - self.TOTAL_H - 8)

    # ── Thumbnail cycling ────────────────────────

    def load_thumbnails(self, film_id: int):
        """Load all thumbnails for a film and start cycling if > 1."""
        rows = db.get_film_thumbnails(film_id)
        self._thumb_paths = [r['path'] for r in rows if os.path.exists(r['path'])]
        self._thumb_idx   = 0
        self._thumb_timer.stop()
        if self._thumb_paths:
            self.set_thumb_preview(self._thumb_paths[0])
            if len(self._thumb_paths) > 1:
                self._thumb_timer.start()
        else:
            self.set_thumb_preview('')

    def _cycle_thumb(self):
        if len(self._thumb_paths) > 1:
            self._thumb_idx = (self._thumb_idx + 1) % len(self._thumb_paths)
            self.set_thumb_preview(self._thumb_paths[self._thumb_idx])

    # ── Thumbnail button ─────────────────────────

    def set_thumb_preview(self, path: str):
        from PyQt6.QtGui import QIcon
        if path and os.path.exists(path):
            raw = QPixmap(path)
            if not raw.isNull():
                scaled = raw.scaled(
                    self.BTN_TW, self.BTN_TH,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                ox = (scaled.width()  - self.BTN_TW) // 2
                oy = (scaled.height() - self.BTN_TH) // 2
                pix = scaled.copy(ox, oy, self.BTN_TW, self.BTN_TH)
                self._btn_thumb.setIcon(QIcon(pix))
                self._btn_thumb.setIconSize(QSize(self.BTN_TW, self.BTN_TH))
                self._btn_thumb.setText('')
                self._btn_thumb.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #444;"
                    "  border-radius: 4px; padding: 0; }"
                    "QPushButton:hover { border: 2px solid #55dede; }"
                    "QPushButton:pressed { border: 2px solid #fff; }"
                )
                return
        # No thumbnail — reset to default
        self._btn_thumb.setIcon(QIcon())
        self._btn_thumb.setIconSize(QSize(0, 0))
        self._btn_thumb.setText('⊡')
        self._btn_thumb.setStyleSheet(
            "QPushButton { background: #001a1a; border: 1px solid #006b6b;"
            "  border-radius: 4px; color: #55dede; font-size: 16px; }"
            "QPushButton:hover { background: #002a2a; border-color: #55dede; }"
            "QPushButton:pressed { background: #55dede; color: #000; }"
        )

    # ── Data ─────────────────────────────────────

    def refresh(self, film_id: int | None):
        self._film_id = film_id
        self._actors.clear(); self._pixmaps.clear(); self._selected.clear()
        self._cat_sel.clear()

        if film_id is None:
            self.hide()
            return

        for actor in db.get_actors_for_film(film_id):
            self._actors.append(actor)
            self._selected.add(actor['id'])   # pre-select all film actors
            photos = db.get_actor_photos(actor['id'])
            path = photos[0]['photo_path'] if photos else ''
            pix = None
            if path:
                raw = QPixmap(path)
                if not raw.isNull():
                    scaled = raw.scaled(self.TW, self.TH,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
                    ox = (scaled.width()  - self.TW) // 2
                    oy = (scaled.height() - self.TH) // 2
                    pix = scaled.copy(ox, oy, self.TW, self.TH)
            self._pixmaps.append(pix)

        self.load_thumbnails(film_id)
        self._reload_categories()
        self._reposition()
        self.update()
        self.show()
        self.raise_()

    def _reload_categories(self):
        self._cats.clear(); self._cat_pixes.clear()
        # Overlay toont alleen hoofdcategorieën (geen subcategorieën)
        for cat in [c for c in db.get_all_categories() if not c.get('parent_id')]:
            self._cats.append(cat)
            path = cat.get('icon_path', '')
            pix = None
            if path:
                raw = QPixmap(path)
                if not raw.isNull():
                    scaled = raw.scaled(self.CW, self.CH,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
                    ox = (scaled.width()  - self.CW) // 2
                    oy = (scaled.height() - self.CH) // 2
                    pix = scaled.copy(ox, oy, self.CW, self.CH)
            self._cat_pixes.append(pix)

    def selected_actors(self) -> list:
        return [a for a in self._actors if a['id'] in self._selected]

    # ── Category management ──────────────────────

    def _delete_category_menu(self, cat):
        menu = QMenu(self)
        act = menu.addAction(f"Verwijder  '{cat['name']}'")
        if menu.exec(QCursor.pos()) == act:
            db.delete_category(cat['id'])
            self._cat_sel.discard(cat['id'])
            self._reload_categories()
            self._reposition()
            self.update()


# ─────────────────────────────────────────────
#  Actor photo search (inside panel overlay)
# ─────────────────────────────────────────────

class _PhotoWidget(QWidget):
    """Draws photo directly in paintEvent — bypasses all stylesheet cascade issues
    that occur inside WA_TranslucentBackground top-level windows."""

    PW, PH = 130, 158

    def __init__(self, photo_path: str):
        super().__init__()
        self.setFixedSize(self.PW, self.PH)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._pix = None
        if photo_path:
            raw = QPixmap(photo_path)
            if not raw.isNull():
                scaled = raw.scaled(
                    self.PW, self.PH,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                ox = (scaled.width()  - self.PW) // 2
                oy = (scaled.height() - self.PH) // 2
                self._pix = scaled.copy(ox, oy, self.PW, self.PH)

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#1a1a1a'))
        if self._pix:
            p.drawPixmap(0, 0, self._pix)


class _ActorCard(QWidget):
    clicked = pyqtSignal(dict)

    def __init__(self, actor: dict):
        super().__init__()
        self._actor = actor
        self._hovered = False
        self.setFixedWidth(148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(3)

        photos = db.get_actor_photos(actor['id'])
        path = photos[0]['photo_path'] if photos else ''
        v.addWidget(_PhotoWidget(path))

        lbl = QLabel(actor.get('name', ''))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("QLabel { color: #bbb; font-size: 10px; background: transparent; }")
        v.addWidget(lbl)

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(p.RenderHint.Antialiasing)
        color = QColor(36, 30, 10, 230) if self._hovered else QColor(22, 22, 22, 210)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 4, 4)

    def enterEvent(self, _e):
        self._hovered = True;  self.update()

    def leaveEvent(self, _e):
        self._hovered = False; self.update()

    def mousePressEvent(self, _event):
        self.clicked.emit(self._actor)


class _SearchPage(QWidget):
    actor_clicked = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; }"
            "QScrollBar::handle:vertical { background: rgba(60,60,60,180); border-radius: 3px; }"
        )
        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._scroll.setWidget(self._inner)
        v.addWidget(self._scroll)

    def update_results(self, actors: list):
        # setWidget() deletes the previous widget automatically — don't call deleteLater
        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._scroll.setWidget(self._inner)

        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout(self._inner)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(6)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)

        for i, actor in enumerate(actors[:24]):
            card = _ActorCard(actor)
            card.clicked.connect(self.actor_clicked)
            grid.addWidget(card, i, 0)


# ─────────────────────────────────────────────
#  Right-panel overlay (floats over player)
# ─────────────────────────────────────────────

class _PanelOverlay(QWidget):
    """Frameless top-level window — WA_TranslucentBackground works only for
    top-level windows on Windows; child-widget transparency can never show
    through an mpv-rendered surface."""

    def __init__(self, main_win, video_container):
        super().__init__(
            main_win,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(170)
        self._vc = video_container

        self.setStyleSheet("""
            QWidget          { background: transparent; color: #e0e0e0;
                               font-family: 'Consolas', monospace; font-size: 12px; }
            QTabWidget::pane { background: transparent; border: none; }
            QTabBar::tab     { background: rgba(18,18,18,210); color: #666;
                               padding: 6px 16px; border: 1px solid #2a2a2a;
                               border-bottom: none; border-radius: 4px 4px 0 0; }
            QTabBar::tab:selected { background: rgba(10,10,10,220); color: #e8b86d; }
            QListWidget      { background: rgba(12,12,12,210); border: 1px solid #222;
                               border-radius: 4px; }
            QListWidget::item          { padding: 6px 8px; border-bottom: 1px solid #1a1a1a; }
            QListWidget::item:hover    { background: rgba(26,26,26,240); }
            QListWidget::item:selected { background: rgba(42,34,0,240); color: #e8b86d; }
            QPushButton      { background: rgba(30,30,30,210); border: 1px solid #333;
                               border-radius: 4px; padding: 5px 12px; color: #e0e0e0; }
            QPushButton:hover    { background: rgba(42,42,42,240); border-color: #e8b86d; }
            QPushButton:pressed  { background: #e8b86d; color: #000; }
            QPushButton#accent   { background: rgba(232,184,109,230); color: #000;
                                   font-weight: bold; border: none; }
            QPushButton#accent:hover { background: rgba(240,202,138,240); }
            QPushButton#danger   { border-color: #c0392b; color: #c0392b; }
            QPushButton#danger:hover { background: #c0392b; color: #fff; }
            QLineEdit, QComboBox, QSpinBox {
                background: rgba(26,26,26,210); border: 1px solid #333;
                border-radius: 4px; padding: 4px 8px; color: #e0e0e0; }
            QComboBox::drop-down { border: none; }
            QProgressBar         { background: rgba(26,26,26,210); border: 1px solid #333;
                                   border-radius: 4px; text-align: center; }
            QProgressBar::chunk  { background: #e8b86d; border-radius: 3px; }
            QLabel#section       { color: #888; font-size: 10px; letter-spacing: 3px; }
            QFrame#separator     { background: #333; max-height: 1px; }
            QScrollBar:vertical  { background: transparent; width: 8px; }
            QScrollBar::handle:vertical { background: rgba(42,42,42,200); border-radius: 4px; }
        """)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        v.addWidget(self._stack)

        self.tab_widget   = QWidget()   # no tab bar — markers shown directly
        self._search_page = _SearchPage()
        self._stack.addWidget(self.tab_widget)    # index 0
        self._stack.addWidget(self._search_page)  # index 1

        main_win.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move,
                            QEvent.Type.Show, QEvent.Type.WindowStateChange):
            self._reposition()
        return False

    def _reposition(self):
        vc = self._vc
        if not vc.isVisible():
            return
        tl = vc.mapToGlobal(vc.rect().topLeft())
        self.setGeometry(
            tl.x() + vc.width() - self.width(),
            tl.y(),
            self.width(),
            vc.height(),
        )

    def show_search(self, active: bool):
        self._stack.setCurrentIndex(1 if active else 0)


# ─────────────────────────────────────────────
#  Snelle marker-popup  (M-toets)
# ─────────────────────────────────────────────

class _MarkerQuickDlg(QDialog):
    """Popup voor de M-toets workflow:
    - Toont het gefixeerde frame op het moment van drukken
    - Toont de al-geselecteerde acteurs (elk afzonderlijk te deselecteren)
    - Toont alle categorieën — één klik maakt de marker en sluit de popup
    """

    def __init__(self, parent, actors: list, pos: float, frame_pix, categories: list,
                 initial_stars: int = 0, show_delete: bool = False,
                 initial_cat_id: int | None = None,
                 film_id: int | None = None,
                 film_cat_types: list | None = None,
                 initial_film_cat_ids: set | None = None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._actors           = list(actors)
        self._pos              = pos
        self._chosen_cat       = None
        self._stars            = 0
        self._deleted          = False
        self._actor_btns       = {}
        self._initial_cat_id   = initial_cat_id
        self._wants_thumbnail  = False
        self._film_id          = film_id
        self._film_cat_types   = film_cat_types or []
        self._film_cat_sel: set = set(initial_film_cat_ids or set())
        # Pre-populate _chosen_cat from the existing category if editing
        if initial_cat_id is not None:
            for c in categories:
                if c['id'] == initial_cat_id:
                    self._chosen_cat = c
                    break
        self._build(frame_pix, categories, show_delete)
        if initial_stars:
            self._set_stars(initial_stars)
        self._center_on_parent()

    def was_deleted(self) -> bool:
        return self._deleted

    def wants_thumbnail(self) -> bool:
        return self._wants_thumbnail

    # ── Opbouw ───────────────────────────────────

    def _build(self, frame_pix, categories, show_delete: bool = False):
        self.setStyleSheet("""
            QDialog   { background: transparent; border: none; }
            QLabel    { color:#ccc; background: transparent; }
            QPushButton {
                background:#252525; color:#ccc;
                border:1px solid #444; border-radius:4px;
                padding:5px 10px; font-size:12px;
            }
            QPushButton:hover   { background:#333; border-color:#888; }
            QPushButton:checked { background:#2a1414; color:#664444; border-color:#442222;
                                  text-decoration: line-through; }
            QPushButton#cancel  { color:#888; border-color:#333; }
            QPushButton#cancel:hover { color:#ccc; background:#2a2a2a; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        _frame = QFrame()
        _frame.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
        )
        outer.addWidget(_frame)

        v = QVBoxLayout(_frame)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        # ── Tijdstip-header ──
        lbl_time = QLabel(format_time(self._pos))
        lbl_time.setStyleSheet("color:#e8b86d; font-size:14px; font-weight:bold;")
        v.addWidget(lbl_time, alignment=Qt.AlignmentFlag.AlignCenter)

        # ── Gefixeerd frame ──
        if frame_pix and not frame_pix.isNull():
            lbl_frame = QLabel()
            scaled = frame_pix.scaledToWidth(
                400, Qt.TransformationMode.SmoothTransformation
            )
            lbl_frame.setPixmap(scaled)
            lbl_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(lbl_frame)

            # Thumbnail-toggle knop onder het frame
            btn_thumb = QPushButton("📷  Als thumbnail opslaan")
            btn_thumb.setCheckable(True)
            btn_thumb.setChecked(False)
            btn_thumb.setStyleSheet(
                "QPushButton { background:#0d1a2a; border:1px solid #1e4a6e;"
                "  border-radius:4px; padding:4px 12px; color:#4488cc; font-size:11px; }"
                "QPushButton:hover  { background:#142233; border-color:#4488cc; color:#66aaee; }"
                "QPushButton:checked { background:#1a3a5a; border:2px solid #4488ff;"
                "  color:#88ccff; font-weight:bold; }"
            )
            def _toggle_thumb(checked, _btn=btn_thumb):
                self._wants_thumbnail = checked
            btn_thumb.toggled.connect(_toggle_thumb)
            v.addWidget(btn_thumb, alignment=Qt.AlignmentFlag.AlignLeft)

        # ── Acteurs (deselecteerbaar) ──
        actors_h = QHBoxLayout()
        actors_h.setSpacing(6)
        for a in self._actors:
            btn = QPushButton(f"✕  {a['name']}")
            btn.setCheckable(True)
            btn.setChecked(False)     # niet-aangevinkt = actief (logisch omgekeerd voor UX)
            btn.setToolTip("Klik om deze acteur niet mee te nemen")
            btn.clicked.connect(lambda _, aid=a['id']: self._toggle_actor(aid))
            self._actor_btns[a['id']] = btn
            actors_h.addWidget(btn)
        actors_h.addStretch()
        v.addLayout(actors_h)

        # ── Sterren (0-5, optioneel) ──
        stars_h = QHBoxLayout()
        stars_h.setSpacing(4)
        lbl_stars = QLabel("Score:")
        lbl_stars.setStyleSheet("color:#666; font-size:10px;")
        stars_h.addWidget(lbl_stars)
        self._star_btns = []
        for n in range(1, 6):
            sb = QPushButton("★")
            sb.setFixedSize(28, 28)
            sb.setCheckable(True)
            sb.setStyleSheet(
                "QPushButton{background:transparent;border:none;color:#444;font-size:14px;padding:0;}"
                "QPushButton:hover{color:#e8b86d;}"
                "QPushButton:checked{color:#e8b86d;background:transparent;border:none;}"
            )
            sb.clicked.connect(lambda _, num=n: self._set_stars(num))
            stars_h.addWidget(sb)
            self._star_btns.append(sb)
        stars_h.addStretch()
        v.addLayout(stars_h)

        # ── Scheidingslijn ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#333;")
        v.addWidget(sep)

        # ── Categorie-knoppen (gegroepeerd per hoofdcategorie) ──
        lbl = QLabel("Kies een categorie:")
        lbl.setStyleSheet("color:#888; font-size:11px;")
        v.addWidget(lbl)

        roots = [c for c in categories if not c.get('parent_id')]
        subs  = {c['id']: [] for c in roots}
        for c in categories:
            pid = c.get('parent_id')
            if pid and pid in subs:
                subs[pid].append(c)

        cat_container = QWidget()
        cat_v = QVBoxLayout(cat_container)
        cat_v.setContentsMargins(0, 0, 0, 0)
        cat_v.setSpacing(6)

        SUB_SS = (
            "QPushButton { background:#1e1200; color:#e8b86d; "
            "  border:1px solid #4a3800; border-radius:4px; "
            "  padding:5px 10px; font-size:12px; }"
            "QPushButton:hover  { background:#2a1a00; border-color:#e8b86d; }"
            "QPushButton:pressed { background:#e8b86d; color:#000; }"
        )
        ACTIVE_SS = (
            "QPushButton { background:#2a2000; color:#e8b86d; "
            "  border:2px solid #e8b86d; border-radius:4px; "
            "  padding:5px 10px; font-size:12px; font-weight:bold; }"
            "QPushButton:hover  { background:#3a2e00; }"
            "QPushButton:pressed { background:#e8b86d; color:#000; }"
        )

        self._cat_btns = {}   # cat_id -> QPushButton, for highlighting

        def _cat_btn(cat):
            btn = QPushButton(cat['name'])
            ip = cat.get('icon_path', '')
            if ip and os.path.exists(ip):
                btn.setIcon(QIcon(ip))
                btn.setIconSize(QSize(20, 20))
            # Highlight if this is the currently selected category
            if cat['id'] == self._initial_cat_id:
                btn.setStyleSheet(ACTIVE_SS)
            btn.clicked.connect(lambda _, c=cat: self._choose(c))
            self._cat_btns[cat['id']] = btn
            return btn

        for root in roots:
            row_h = QHBoxLayout()
            row_h.setSpacing(6)
            row_h.addWidget(_cat_btn(root))
            for sub in subs.get(root['id'], []):
                sb = _cat_btn(sub)
                if sub['id'] != self._initial_cat_id:
                    sb.setStyleSheet(SUB_SS)
                row_h.addWidget(sb)
            row_h.addStretch()
            cat_v.addLayout(row_h)

        v.addWidget(cat_container)

        # ── Filmcategorie (multi-select, geldt voor de hele film) ──
        if self._film_cat_types:
            sep2 = QFrame()
            sep2.setFrameShape(QFrame.Shape.HLine)
            sep2.setStyleSheet("background: #333; max-height: 1px;")
            v.addWidget(sep2)

            lbl_fc = QLabel("Filmcategorie:")
            lbl_fc.setStyleSheet("color:#888; font-size:11px;")
            v.addWidget(lbl_fc)

            FC_OFF = (
                "QPushButton { background:#141414; border:1px solid #2a2a2a; border-radius:4px;"
                "  padding:4px 10px; color:#555; font-size:11px; }"
                "QPushButton:hover { border-color:#555; color:#888; }"
            )
            FC_ON = (
                "QPushButton { background:#1a1500; border:1px solid #e8b86d; border-radius:4px;"
                "  padding:4px 10px; color:#e8b86d; font-size:11px; font-weight:bold; }"
                "QPushButton:hover { background:#2a2200; }"
            )

            self._fc_btns: dict = {}
            fc_h = QHBoxLayout()
            fc_h.setSpacing(6)

            def _make_fc_toggle(fid, b, on_ss, off_ss):
                def _h(checked):
                    if checked:
                        self._film_cat_sel.add(fid)
                        b.setStyleSheet(on_ss)
                    else:
                        self._film_cat_sel.discard(fid)
                        b.setStyleSheet(off_ss)
                return _h

            for fc in self._film_cat_types:
                btn_fc = QPushButton(fc['naam'])
                btn_fc.setCheckable(True)
                is_on = fc['id'] in self._film_cat_sel
                btn_fc.setChecked(is_on)
                btn_fc.setStyleSheet(FC_ON if is_on else FC_OFF)
                btn_fc.toggled.connect(_make_fc_toggle(fc['id'], btn_fc, FC_ON, FC_OFF))
                self._fc_btns[fc['id']] = btn_fc
                fc_h.addWidget(btn_fc)

            fc_h.addStretch()
            v.addLayout(fc_h)

        # ── Onderste balk: optioneel verwijder + opslaan + annuleren ──
        bottom_h = QHBoxLayout()
        bottom_h.setSpacing(8)

        if show_delete:
            btn_del_marker = QPushButton("✕  Verwijder marker")
            btn_del_marker.setStyleSheet(
                "QPushButton { background:#2a0808; border:1px solid #6b1f1f;"
                "  border-radius:4px; padding:5px 14px; color:#cc4444; font-size:11px; }"
                "QPushButton:hover  { background:#3a0a0a; border-color:#e05555; color:#ff6666; }"
                "QPushButton:pressed { background:#e05555; color:#fff; }"
            )
            def _do_delete():
                self._deleted = True
                self.accept()
            btn_del_marker.clicked.connect(_do_delete)
            bottom_h.addWidget(btn_del_marker)

        bottom_h.addStretch()
        btn_cancel = QPushButton("Annuleren")
        btn_cancel.setObjectName("cancel")
        btn_cancel.clicked.connect(self.reject)
        bottom_h.addWidget(btn_cancel)

        if show_delete:
            btn_save = QPushButton("✓  Opslaan")
            btn_save.setStyleSheet(
                "QPushButton { background:#1a2a00; border:1px solid #4a7a1f;"
                "  border-radius:4px; padding:5px 14px; color:#88cc44; font-size:11px; }"
                "QPushButton:hover  { background:#253800; border-color:#88cc44; color:#aaee66; }"
                "QPushButton:pressed { background:#88cc44; color:#000; }"
            )
            def _do_save():
                excluded = {aid for aid, b in self._actor_btns.items() if b.isChecked()}
                self._actors = [a for a in self._actors if a['id'] not in excluded]
                if not self._actors:
                    return
                if self._chosen_cat is None:
                    return   # geen categorie — klik eerst op een categorie
                self.accept()
            btn_save.clicked.connect(_do_save)
            bottom_h.addWidget(btn_save)

        v.addLayout(bottom_h)

    # ── Acties ───────────────────────────────────

    def _toggle_actor(self, actor_id: int):
        """Acteur aan/uit — minimaal 1 acteur verplicht."""
        btn = self._actor_btns[actor_id]
        excluded = {aid for aid, b in self._actor_btns.items() if b.isChecked()}
        active = [a for a in self._actors if a['id'] not in excluded]
        if not active:
            # Laatste acteur — niet toestaan
            btn.setChecked(False)

    def _set_stars(self, n: int):
        """Selecteer n sterren — klik nogmaals op hetzelfde om te wissen."""
        if self._stars == n:
            n = 0   # toggle: klik dezelfde ster → wis
        self._stars = n
        for i, btn in enumerate(self._star_btns):
            btn.setChecked(i < n)

    def _choose(self, cat: dict):
        excluded = {aid for aid, b in self._actor_btns.items() if b.isChecked()}
        self._actors = [a for a in self._actors if a['id'] not in excluded]
        if not self._actors:
            return  # mag niet voorkomen door _toggle_actor guard
        self._chosen_cat = cat
        self.accept()

    def get_result(self):
        return self._actors, self._chosen_cat, self._stars

    def accept(self):
        """Sla filmcategorieën op bij elke acceptatie (nieuw + bewerk)."""
        self._save_film_cats()
        super().accept()

    def _save_film_cats(self):
        if self._film_id is not None:
            try:
                db.set_film_categories(self._film_id, list(self._film_cat_sel))
            except Exception:
                pass

    def _center_on_parent(self):
        """Positioneer rechtsonder in het oudervenster (of scherm als fallback)."""
        self.adjustSize()
        margin = 24
        parent = self.parent()
        if parent:
            # geometry() geeft de client-area in schermcoördinaten voor top-level vensters
            pg = parent.geometry()
            self.move(
                pg.x() + pg.width()  - self.width()  - margin,
                pg.y() + pg.height() - self.height() - margin,
            )
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(
                screen.x() + screen.width()  - self.width()  - margin,
                screen.y() + screen.height() - self.height() - margin,
            )




# ─────────────────────────────────────────────
#  Main Window
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  Video-only fullscreen window
# ─────────────────────────────────────────────

class _VideoFullscreenWindow(QWidget):
    """Volledig-scherm venster dat uitsluitend de video toont.
    Heeft een klein halftransparant ✕-knopje rechtsboven.
    Routeert M / V / O / P naar de CineMarker-instantie."""

    def __init__(self, cinemarker: 'CineMarker'):
        super().__init__(
            None,
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint,
        )
        self._cm = cinemarker
        self.setStyleSheet("background: #000;")

        # Video-oppervlak — mpv rendert hiernaartoe via wid
        self._video = QWidget(self)
        self._video.setStyleSheet("background: #000;")
        self._video.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        # Klein sluitknopje — halftransparant, rechtsboven
        self._btn_close = QPushButton("✕", self)
        self._btn_close.setFixedSize(38, 38)
        self._btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.setStyleSheet(
            "QPushButton { background: rgba(10,10,10,130); border: 1px solid rgba(80,80,80,70);"
            "  border-radius: 5px; color: rgba(170,170,170,150); font-size: 15px; }"
            "QPushButton:hover { background: rgba(160,20,20,220); border-color: #e05555;"
            "  color: #fff; }"
        )
        self._btn_close.clicked.connect(cinemarker._exit_video_fullscreen)

        # Toetsconfiguratie ophalen (keer bij aanmaken)
        self._key_m  = db.get_setting('shortcut_marker',        'M').split(',')[0].strip()
        self._key_v  = db.get_setting('shortcut_volgende_film', 'V').split(',')[0].strip()
        self._key_o  = db.get_setting('shortcut_marker_achter', 'O').split(',')[0].strip()
        self._key_p  = db.get_setting('shortcut_marker_voor',   'P').split(',')[0].strip()
        self._key_fs = db.get_setting('shortcut_fullscreen',    'F11').split(',')[0].strip()

    # ── Layout ───────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._video.setGeometry(0, 0, self.width(), self.height())
        self._btn_close.move(self.width() - 50, 12)
        self._btn_close.raise_()

    # ── Keyboard ─────────────────────────────────

    def keyPressEvent(self, event):
        seq = QKeySequence(event.keyCombination()).toString()
        if seq == self._key_m:
            self._cm._shortcut_m()
        elif seq == self._key_v:
            self._cm._next_film()
        elif seq == self._key_o:
            self._cm._shortcut_o()
        elif seq == self._key_p:
            self._cm._shortcut_p()
        elif seq == self._key_fs or event.key() == Qt.Key.Key_Escape:
            self._cm._exit_video_fullscreen()
        else:
            super().keyPressEvent(event)


class CineMarker(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CineMarker")
        self.resize(1400, 900)

        self._video_path = None
        self._duration = 0
        self._markers = []
        self._updating_slider = False
        self._skip_negative    = db.get_setting('skip_negative', '1') == '1'
        self._neg_zones_cache: list = []
        self._convert_worker = None
        self._thumb_worker = None

        self._zoom_level = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_active = False
        self._drag_last = None
        self._current_speed = 1.0
        self._reverse_speed = 0.0          # |speed| tijdens achteruit-modus
        self._reverse_use_frameback = False # True = frame_back_step(), False = seek
        self._selection_entries: list = []   # cross-film afspeellijst vanuit markers-tab
        self._current_marker_row: int = -1  # blijft bewaard over list-rebuilds heen
        self._active_shortcuts: list = []   # QShortcut-objecten (voor herladen)
        self._fs_win: _VideoFullscreenWindow | None = None  # video-only fullscreen

        # Auto-advance (markers afspeellijst)
        self._auto_advance_sec: int = 0    # 0 = uitgeschakeld
        self._auto_advance_timer = QTimer(self)
        self._auto_advance_timer.setSingleShot(True)
        self._auto_advance_timer.timeout.connect(self._do_auto_advance)

        # Persistente caches voor marker-lijst — worden niet bij elke rebuild gewist
        self._actor_pix_cache: dict = {}   # actor_id  -> QPixmap | None
        self._cat_pix_cache:   dict = {}   # cat_id    -> QPixmap | None

        # Debounce timer voor acteur-zoekbalk — voorkomt DB-queries per toetsaanslag
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(180)
        self._search_debounce.timeout.connect(self._do_player_search)
        self._search_pending: str = ''

        # Twee-fase marker-seek: keyframe-seek toont direct iets, exact-seek volgt 80ms later.
        # Bij snel O/O/P/P herstarten we de timer — exact-seek valt altijd op de eindstop.
        self._exact_seek_timer = QTimer()
        self._exact_seek_timer.setSingleShot(True)
        self._exact_seek_timer.setInterval(80)
        self._exact_seek_timer.timeout.connect(self._do_exact_seek)
        self._exact_seek_target: float | None = None

        # Multi-tap seek state
        self._seek_count = 0
        self._seek_dir   = 0
        self._seek_timer = QTimer()
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(380)
        self._seek_timer.timeout.connect(self._commit_seek)

        self._setup_style()
        self._setup_mpv()
        self._build_ui()
        self._setup_shortcuts()
        self._setup_timer()

    # ── Style ──────────────────────────────────

    def _setup_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0e0e0e;
                color: #e0e0e0;
                font-family: 'SF Mono', 'Menlo', 'Consolas', monospace;
                font-size: 12px;
            }
            QSplitter::handle { background: #222; width: 2px; height: 2px; }
            QPushButton {
                background: #1e1e1e;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 5px 12px;
                color: #e0e0e0;
            }
            QPushButton:hover { background: #2a2a2a; border-color: #e8b86d; }
            QPushButton:pressed { background: #e8b86d; color: #000; }
            QPushButton#accent {
                background: #e8b86d;
                color: #000;
                border: none;
                font-weight: bold;
            }
            QPushButton#accent:hover { background: #f0ca8a; }
            QPushButton#danger { border-color: #c0392b; color: #c0392b; }
            QPushButton#danger:hover { background: #c0392b; color: #fff; }
            QSlider::groove:horizontal {
                height: 4px;
                background: #2a2a2a;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #e8b86d;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #e8b86d;
                width: 14px; height: 14px;
                border-radius: 7px;
                margin: -5px 0;
            }
            QSlider::groove:vertical {
                width: 4px;
                background: #2a2a2a;
                border-radius: 2px;
            }
            QSlider::sub-page:vertical {
                background: #e8b86d;
                border-radius: 2px;
            }
            QSlider::handle:vertical {
                background: #e8b86d;
                width: 14px; height: 14px;
                border-radius: 7px;
                margin: 0 -5px;
            }
            QListWidget {
                background: #111;
                border: 1px solid #222;
                border-radius: 4px;
            }
            QListWidget::item { padding: 6px 8px; border-bottom: 1px solid #1a1a1a; }
            QListWidget::item:hover { background: #1a1a1a; }
            QListWidget::item:selected { background: #2a2200; color: #e8b86d; }
            QLineEdit, QComboBox, QSpinBox {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 4px 8px;
                color: #e0e0e0;
            }
            QLineEdit:focus, QComboBox:focus { border-color: #e8b86d; }
            QComboBox::drop-down { border: none; }
            QComboBox::down-arrow { image: none; border: none; }
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: #1a1a1a;
                padding: 6px 18px;
                border: 1px solid #222;
                border-bottom: none;
                border-radius: 4px 4px 0 0;
            }
            QTabBar::tab:selected { background: #0e0e0e; border-bottom: 1px solid #0e0e0e; color: #e8b86d; }
            QProgressBar {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk { background: #e8b86d; border-radius: 3px; }
            QLabel#timecode {
                font-size: 18px;
                font-weight: bold;
                color: #e8b86d;
                letter-spacing: 2px;
            }
            QLabel#section { color: #888; font-size: 10px; letter-spacing: 3px; text-transform: uppercase; }
            QFrame#separator { background: #222; max-height: 1px; }
            QStatusBar { background: #0a0a0a; color: #555; border-top: 1px solid #1a1a1a; }
        """)

    # ── mpv setup ──────────────────────────────

    def _setup_mpv(self):
        self.player = mpv.MPV(
            log_handler=self._mpv_log,
            loglevel='error',
            input_default_bindings=False,   # mpv's eigen sneltoetsen uitzetten
            hwdec='auto-safe',              # GPU-decodering (D3D11VA/NVDEC/…) — snellere seeks
        )
        self.player['keep-open'] = True
        self.player['hr-seek'] = True  # frame-accurate seeking
        self.player['cache'] = 'yes'   # demuxer-cache voor lokale bestanden

    def _mpv_log(self, level, component, message):
        # Alleen echte fouten doorgeven — info/debug is te uitgebreid
        if level in ('error', 'fatal'):
            _log.error('mpv [%s] %s', component, message.rstrip())

    # ── UI Building ───────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main tabs — no separate title bar
        self.main_tabs = QTabWidget()
        self.main_tabs.setStyleSheet("""
            QTabBar::tab { padding: 8px 20px; font-size: 12px; letter-spacing: 2px; }
            QTabBar::tab:selected { color: #e8b86d; }
        """)

        # Corner widget: Open Video + Fullscreen
        _corner = QWidget()
        _corner.setStyleSheet("background: transparent;")
        _ch = QHBoxLayout(_corner)
        _ch.setContentsMargins(0, 3, 8, 3)
        _ch.setSpacing(6)
        btn_open_corner = QPushButton("⊕  Open Video")
        btn_open_corner.setObjectName("accent")
        btn_open_corner.setFixedHeight(28)
        btn_open_corner.clicked.connect(self.open_file)
        _ch.addWidget(btn_open_corner)
        self.btn_fs = QPushButton("⛶")
        self.btn_fs.setFixedSize(28, 28)
        self.btn_fs.setToolTip("Volledig scherm  F11")
        self.btn_fs.setStyleSheet(
            "QPushButton { background: #1e1e1e; border: 1px solid #333; border-radius: 4px;"
            "  color: #e0e0e0; font-size: 14px; padding: 0; }"
            "QPushButton:hover { border-color: #e8b86d; color: #e8b86d; }"
            "QPushButton:pressed { background: #e8b86d; color: #000; }"
        )
        self.btn_fs.clicked.connect(self._toggle_fullscreen)
        _ch.addWidget(self.btn_fs)
        btn_help = QPushButton("?")
        btn_help.setFixedSize(28, 28)
        btn_help.setToolTip("Toetsen & knoppen overzicht")
        btn_help.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #2a2a2a;"
            "  border-radius: 4px; color: #444; font-size: 13px; font-weight: bold; padding: 0; }"
            "QPushButton:hover { border-color: #e8b86d; color: #e8b86d; }"
        )
        btn_help.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_help.clicked.connect(self._show_help)
        _ch.addWidget(btn_help)
        self.main_tabs.setCornerWidget(_corner, Qt.Corner.TopRightCorner)
        self._corner_layout = _ch

        # Player tab — toolbar + video + timeline
        player_widget = QWidget()
        self._player_widget = player_widget
        pv = QVBoxLayout(player_widget)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        # ── Player toolbar (eigen balk, niet in corner-widget) ────
        player_bar = QFrame()
        player_bar.setFixedHeight(36)
        player_bar.setStyleSheet(
            "QFrame { background: #0a0a0a; border-bottom: 1px solid #1a1a1a; }"
        )
        _ph = QHBoxLayout(player_bar)
        _ph.setContentsMargins(10, 0, 10, 0)
        _ph.setSpacing(8)

        btn_del_film = QPushButton("🗑")
        btn_del_film.setFixedSize(28, 28)
        btn_del_film.setToolTip("Verplaats huidige film naar map 'deleted'")
        btn_del_film.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_del_film.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #443333; font-size: 14px; padding: 0; }"
            "QPushButton:hover { color: #cc4444; }"
        )
        btn_del_film.clicked.connect(self._delete_current_film)
        _ph.addWidget(btn_del_film)

        self._lbl_time = QLabel("--:-- / --:--")
        self._lbl_time.setStyleSheet(
            "color: #555; font-size: 11px; font-family: 'Consolas', monospace;"
        )
        self._lbl_time.setFixedWidth(130)
        _ph.addWidget(self._lbl_time)

        self._btn_speed = QPushButton("1×")
        self._btn_speed.setFixedSize(56, 28)
        self._btn_speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_speed.setToolTip("Afspeelsnelheid  [ = langzamer  ] = sneller  \\ of klik = reset 1×")
        self._btn_speed.clicked.connect(self._reset_speed)
        self._btn_speed.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 0;"
            "  color: #444; font-size: 11px; font-family: 'Consolas', monospace; }"
            "QPushButton:hover { color: #888; }"
        )
        _ph.addWidget(self._btn_speed)

        btn_next = QPushButton("⏭")
        btn_next.setFixedSize(28, 28)
        btn_next.setToolTip("Volgende film in de lijst")
        btn_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_next.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #444; font-size: 14px; padding: 0; }"
            "QPushButton:hover { color: #e8b86d; }"
        )
        btn_next.clicked.connect(self._next_film)
        _ph.addWidget(btn_next)

        self._btn_skip_neg = QPushButton("⊘")
        self._btn_skip_neg.setFixedSize(28, 28)
        self._btn_skip_neg.setCheckable(True)
        self._btn_skip_neg.setChecked(self._skip_negative)
        self._btn_skip_neg.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_skip_neg.setToolTip("Negatieve perioden overslaan (aan/uit)")
        self._btn_skip_neg.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #333; font-size: 14px; padding: 0; }"
            "QPushButton:checked { color: #cc4444; }"
            "QPushButton:hover { color: #888; }"
            "QPushButton:checked:hover { color: #ff6666; }"
        )
        self._btn_skip_neg.clicked.connect(self._toggle_skip_negative)
        _ph.addWidget(self._btn_skip_neg)

        _ph.addStretch()

        self._player_search = QLineEdit()
        self._player_search.setPlaceholderText("Acteur zoeken…")
        self._player_search.setFixedWidth(200)
        self._player_search.setFixedHeight(28)
        # Only receive focus when the user explicitly clicks — never from keyboard
        # tab-order or any other indirect focus transfer (zoom, drag, etc.)
        self._player_search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._player_search.textChanged.connect(self._on_player_search)
        _ph.addWidget(self._player_search)

        pv.addWidget(player_bar)

        # ── Video area ────────────────────────────
        self._build_video_area(pv)

        self.timeline = TimelineSlider()
        self.timeline.seeked.connect(self._on_timeline_scrub)    # snel, tijdens slepen
        self.timeline.released.connect(self._on_timeline_seek)   # exact, bij loslaten
        self.timeline.setFixedHeight(8)   # tall enough for neg-zone color to be visible
        self.timeline.setStyleSheet("")    # paintEvent handles all drawing
        pv.addWidget(self.timeline)

        # Floating right panel — top-level transparent window
        self._panel = _PanelOverlay(self, self.video_container)
        self.tabs = self._panel.tab_widget
        self._build_markers_tab()
        self._panel._search_page.actor_clicked.connect(self._link_actor_to_film)
        self._panel.hide()

        # Floating actors overlay — bottom-left of video
        self._actors_overlay = _FilmActorsOverlay(self, self.video_container)
        self._actors_overlay.marker_requested.connect(self._quick_marker)
        self._actors_overlay.thumbnail_requested.connect(self._capture_thumbnail)
        self._actors_overlay.hide()

        # Film edit panel — floats above actors overlay, shows actors + thumbnails to remove
        self._film_edit_panel = _FilmEditPanel(self, self.video_container)
        self._actors_overlay.edit_requested.connect(self._film_edit_panel.open_for_film)
        self._film_edit_panel.data_changed.connect(self._actors_overlay.refresh)

        # Click-flash ✓ overlay — centre of video, fades after ~370 ms
        self._click_flash = _ClickFlash(self, self.video_container)

        # Fade overlay — zwart scherm bij automatische marker-overgangen
        self._fade_overlay = _FadeOverlay(self, self.video_container)

        # Floating actor-link overlay (child of player_widget)
        self._actor_overlay = _ActorLinkOverlay(player_widget)
        self._actor_overlay.link_requested.connect(self._link_actor_to_film)

        self.main_tabs.addTab(player_widget, "▶  SPELER")

        # Films tab
        self.films_panel = FilmsPanel()
        self.films_panel.play_requested.connect(self._load_video_and_switch)
        self.main_tabs.addTab(self.films_panel, "🎬  FILMS")

        # Markers overzicht tab
        self.markers_panel = MarkersPanel(self.player)
        self.markers_panel.scene_jump_requested.connect(self._on_scene_jump)
        self.markers_panel.play_selection_requested.connect(self._load_selection)
        self.markers_panel.edit_marker_requested.connect(self._edit_marker_from_panel)
        self.main_tabs.addTab(self.markers_panel, "◈  MARKERS")

        # Acteurs tab
        self.actors_panel = ActorsPanel(self.player)
        self.actors_panel.open_film_requested.connect(self._load_video_and_switch)
        self.actors_panel.scene_jump_requested.connect(self._on_scene_jump)
        self.main_tabs.addTab(self.actors_panel, "◉  ACTEURS")

        # Insert actors toolbar into corner (hidden until acteurs tab active)
        self._actors_tb = self.actors_panel.tab_toolbar
        self._actors_tb.setVisible(False)
        self._corner_layout.insertWidget(0, self._actors_tb)
        self.main_tabs.currentChanged.connect(self._on_tab_changed)

        # Database tab
        self.db_panel = DatabasePanel()
        self.main_tabs.addTab(self.db_panel, "⊞  DATABASE")

        # Sorter tab
        self.sorter_panel = SorterPanel()
        self.main_tabs.addTab(self.sorter_panel, "⊕  SORTEREN")

        self._build_converter_tab()
        self.main_tabs.addTab(self._converter_widget, "⟳  CONVERTER")

        actors_tab = self.main_tabs.indexOf(self.actors_panel)
        self.main_tabs.setCurrentIndex(actors_tab)  # default: ACTEURS

        root.addWidget(self.main_tabs)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Open een videobestand om te beginnen  •  CineMarker")

    def _on_tab_changed(self, idx):
        actors_idx  = self.main_tabs.indexOf(self.actors_panel)
        markers_idx = self.main_tabs.indexOf(self.markers_panel)
        films_idx   = self.main_tabs.indexOf(self.films_panel)
        self._actors_tb.setVisible(idx == actors_idx)
        player_idx = self.main_tabs.indexOf(self._player_widget)
        on_player = (idx == player_idx)
        on_actors = (idx == actors_idx)
        if idx == markers_idx:
            QTimer.singleShot(0, self.markers_panel.refresh)
        if idx == films_idx:
            # Herlaad filterbalk zodat nieuw aangemaakte filmcategorieën/kleuren
            # direct zichtbaar zijn na een bezoek aan de DATABASE-tab
            QTimer.singleShot(0, self.films_panel.reload_filter_bar2)
        self._panel.setVisible(on_player)
        if on_player and self._video_path:
            self._actors_overlay.show()
            self._actors_overlay.raise_()
        else:
            self._actors_overlay.hide()
            self._film_edit_panel.hide()
        if on_player:
            # Give focus to video_container so no text field is active on entry
            QTimer.singleShot(0, lambda: self.video_container.setFocus(
                Qt.FocusReason.OtherFocusReason))
        elif on_actors:
            # Give focus to the actor search bar immediately
            QTimer.singleShot(0, lambda: self.actors_panel.search_input.setFocus(
                Qt.FocusReason.OtherFocusReason))
        else:
            self._player_search.clear()

    def _on_player_search(self, text: str):
        self._search_pending = text
        if not text.strip():
            self._search_debounce.stop()
            self._panel.show_search(False)
        else:
            self._search_debounce.start()   # herstart bij elke toetsaanslag

    def _do_player_search(self):
        q = self._search_pending.strip().lower()
        if not q:
            return
        actors = [a for a in db.get_all_actors()
                  if q in a.get('name', '').lower()]

        # Sorteer op filmcount uit DB — één query, geen JSON-reads van SSD
        actor_film_counts = db.get_actor_film_counts_batch()   # {actor_id: film_count}
        actors.sort(key=lambda a: -actor_film_counts.get(a['id'], 0))

        self._panel._search_page.update_results(actors)
        self._panel.show_search(True)
        if not self._panel.isVisible():
            self._panel.show()

    def _toggle_fullscreen(self):
        if self._fs_win and self._fs_win.isVisible():
            self._exit_video_fullscreen()
        else:
            self._enter_video_fullscreen()

    def _enter_video_fullscreen(self):
        """Schakel over naar video-only fullscreen: apart schermvullend venster."""
        if self._fs_win is None:
            self._fs_win = _VideoFullscreenWindow(self)
        # Verberg overlays — ze drijven anders over het fullscreen-venster
        self._panel.hide()
        self._actors_overlay.hide()
        self._film_edit_panel.hide()
        self._fs_win.showFullScreen()
        self._fs_win.raise_()
        self._fs_win.activateWindow()
        # Kleine vertraging zodat Qt de HWND aanmaakt vóór mpv koppeling
        QTimer.singleShot(80, self._attach_mpv_to_fs)

    def _attach_mpv_to_fs(self):
        """Koppel mpv aan het fullscreen-videoscherm (na HWND-creatie)."""
        if self._fs_win and self._fs_win.isVisible():
            try:
                self.player['wid'] = int(self._fs_win._video.winId())
            except Exception:
                pass

    def _exit_video_fullscreen(self):
        """Sluit video-only fullscreen en herstel normale weergave."""
        if self._fs_win:
            self._fs_win.hide()
        # Mpv terug aan het originele videoscherm koppelen
        try:
            self.player['wid'] = int(self.video_container.winId())
        except Exception:
            pass
        # Herstel overlays als we op het speler-tabblad zijn
        player_idx = self.main_tabs.indexOf(self._player_widget)
        if self.main_tabs.currentIndex() == player_idx:
            self._panel.show()
            if self._video_path:
                self._actors_overlay.show()
                self._actors_overlay.raise_()

    def _show_actor_overlay(self):
        if not self._video_path:
            return
        self._actor_overlay.show_overlay()

    def _link_actor_to_film(self, actor: dict):
        if not self._video_path:
            return
        film = db.get_or_create_film(self._video_path)
        db.link_actor_film(actor['id'], film['id'])
        self._actors_overlay.refresh(film['id'])
        self._player_search.clear()   # reset search → full video visible again
        self.status.showMessage(
            f"  {actor['name']} gekoppeld aan {Path(self._video_path).name}"
        )

    def _build_video_area(self, layout):
        self.video_container = QWidget()
        self.video_container.setStyleSheet("background: #000;")
        self.video_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_container.setMouseTracking(True)
        # ClickFocus: clicking anywhere on the video steals focus from _player_search
        self.video_container.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.video_container.installEventFilter(self)
        layout.addWidget(self.video_container, stretch=1)

        # Attach mpv to widget after show
        self._mpv_attached = False

    def _attach_mpv(self):
        if not self._mpv_attached:
            wid = int(self.video_container.winId())
            self.player['wid'] = wid
            self._mpv_attached = True

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self._attach_mpv)
        QTimer.singleShot(150, self._reposition_overlays)

    def _reposition_overlays(self):
        self._panel._reposition()
        self._actors_overlay._reposition()

    def _build_markers_tab(self):
        v = QVBoxLayout(self.tabs)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        self.marker_list = QListWidget()
        self.marker_list.itemDoubleClicked.connect(self._on_marker_jump)
        self.marker_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.marker_list.customContextMenuRequested.connect(self._marker_context_menu)
        v.addWidget(self.marker_list)

        # Footer: CSV-export
        footer = QHBoxLayout()
        footer.addStretch()
        btn_csv = QPushButton("↓ CSV")
        btn_csv.setFixedWidth(56)
        btn_csv.setToolTip("Markers exporteren als CSV-bestand")
        btn_csv.clicked.connect(self._export_markers_csv)
        footer.addWidget(btn_csv)
        v.addLayout(footer)

    def _build_converter_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        lbl = QLabel("CONVERTER")
        lbl.setObjectName("section")
        v.addWidget(lbl)

        # Input file
        row_in = QHBoxLayout()
        self.conv_input = QLineEdit()
        self.conv_input.setPlaceholderText("Invoerbestand...")
        row_in.addWidget(self.conv_input)
        btn_in = QPushButton("...")
        btn_in.setFixedWidth(32)
        btn_in.clicked.connect(self._conv_pick_input)
        row_in.addWidget(btn_in)
        v.addLayout(row_in)

        btn_use_current = QPushButton("← Gebruik huidig videobestand")
        btn_use_current.clicked.connect(self._conv_use_current)
        v.addWidget(btn_use_current)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(sep)

        # Output format
        lbl2 = QLabel("UITVOERFORMAAT")
        lbl2.setObjectName("section")
        v.addWidget(lbl2)

        self.conv_format = QComboBox()
        self.conv_format.addItems(["mp4", "mov", "avi", "mkv"])
        v.addWidget(self.conv_format)

        lbl3 = QLabel("VIDEO CODEC")
        lbl3.setObjectName("section")
        v.addWidget(lbl3)

        self.conv_codec = QComboBox()
        self.conv_codec.addItems(["libx264 (H.264)", "libx265 (H.265/HEVC)", "libvpx-vp9 (VP9)", "copy (geen hercodering)"])
        v.addWidget(self.conv_codec)

        lbl4 = QLabel("RESOLUTIE")
        lbl4.setObjectName("section")
        v.addWidget(lbl4)

        self.conv_res = QComboBox()
        self.conv_res.addItems(["Original", "3840x2160 (4K)", "1920x1080 (1080p)", "1280x720 (720p)", "854x480 (480p)"])
        v.addWidget(self.conv_res)

        lbl5 = QLabel("KWALITEIT (CRF: lager = beter)")
        lbl5.setObjectName("section")
        v.addWidget(lbl5)

        self.conv_crf = QSpinBox()
        self.conv_crf.setRange(0, 51)
        self.conv_crf.setValue(18)
        v.addWidget(self.conv_crf)

        sep2 = QFrame()
        sep2.setObjectName("separator")
        sep2.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(sep2)

        # Output file
        row_out = QHBoxLayout()
        self.conv_output = QLineEdit()
        self.conv_output.setPlaceholderText("Uitvoerbestand...")
        row_out.addWidget(self.conv_output)
        btn_out = QPushButton("...")
        btn_out.setFixedWidth(32)
        btn_out.clicked.connect(self._conv_pick_output)
        row_out.addWidget(btn_out)
        v.addLayout(row_out)

        self.conv_progress = QProgressBar()
        self.conv_progress.setVisible(False)
        v.addWidget(self.conv_progress)

        self.conv_status = QLabel("")
        self.conv_status.setWordWrap(True)
        self.conv_status.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.conv_status)

        v.addStretch()

        self.btn_convert = QPushButton("⟳  START CONVERSIE")
        self.btn_convert.setObjectName("accent")
        self.btn_convert.setFixedHeight(36)
        self.btn_convert.clicked.connect(self.start_conversion)
        v.addWidget(self.btn_convert)

        self._converter_widget = w

    # ── Shortcuts ─────────────────────────────

    def _shortcut_dispatch(self) -> dict:
        """Geeft een mapping van action_key → slot-methode terug."""
        return {
            'play_pause':    self._shortcut_space,
            'marker':        self._shortcut_m,
            'neg_marker':    self._add_negative_marker,
            'thumbnail':     self.export_thumbnail,
            'volgende_film': self._next_film,
            'marker_voor':   self._shortcut_p,
            'marker_achter': self._shortcut_o,
            'skip_voor':     self._shortcut_l,
            'skip_achter':   self._shortcut_n,
            'sneller':       self._speed_up,
            'langzamer':     self._speed_down,
            'reset_speed':   self._reset_speed,
            'zoom_in':       self._shortcut_plus,
            'zoom_uit':      self._shortcut_minus,
            'zoom_reset':    self._reset_zoom,
            'fullscreen':    self._toggle_fullscreen,
            'open_bestand':  self.open_file,
            'acteurs_tonen': self._show_actor_overlay,
            'begin':         self.go_to_start,
            'einde':         self.go_to_end,
            'links':         self._shortcut_left,
            'rechts':        self._shortcut_right,
            'ontsnappen':    self._shortcut_esc,
        }

    def _setup_shortcuts(self):
        """Laad sneltoetsen uit de DB (of gebruik standaardwaarden) en registreer ze."""
        dispatch = self._shortcut_dispatch()
        for action, _label, default in SHORTCUT_DEFS:
            slot = dispatch.get(action)
            if not slot:
                continue
            raw = db.get_setting(f'shortcut_{action}', default)
            for key_str in raw.split(','):
                key_str = key_str.strip()
                if not key_str:
                    continue
                try:
                    sc = QShortcut(QKeySequence(key_str), self)
                    sc.activated.connect(slot)
                    self._active_shortcuts.append(sc)
                except Exception:
                    pass

    def _reload_shortcuts(self):
        """Verwijder alle huidige sneltoetsen en laad ze opnieuw uit de DB."""
        for sc in self._active_shortcuts:
            sc.setEnabled(False)
            sc.setParent(None)
        self._active_shortcuts.clear()
        self._setup_shortcuts()

    # ── Marker-list helpers ───────────────────
    # Grootte-constanten voor acteur-/categorie-icoontjes in de markerlijst.
    # Op één plek gedefinieerd zodat _refresh_marker_list én
    # _refresh_selection_markers altijd in sync zijn.
    _MARKER_SZ_A  = 26   # acteur-foto (vierkant)
    _MARKER_SZ_C  = 22   # categorie-icoon (vierkant)
    _MARKER_ROW_H = 34   # rijhoogte

    def _marker_actor_pix(self, aid: int):
        """Geeft een gecachede vierkante acteur-thumbnail voor de markerlijst.
        Retourneert None als de acteur geen foto heeft."""
        if aid not in self._actor_pix_cache:
            sz     = self._MARKER_SZ_A
            photos = db.get_actor_photos(aid)
            pix    = None
            if photos:
                raw = QPixmap(photos[0]['photo_path'])
                if not raw.isNull():
                    sc  = raw.scaled(sz, sz,
                              Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
                    ox  = (sc.width()  - sz) // 2
                    oy  = (sc.height() - sz) // 2
                    pix = sc.copy(ox, oy, sz, sz)
            self._actor_pix_cache[aid] = pix
        return self._actor_pix_cache[aid]

    def _marker_cat_pix(self, cid: int):
        """Geeft een gecachede categorie-icoon voor de markerlijst.
        Retourneert None als de categorie geen icoon heeft of niet meer bestaat."""
        if cid not in self._cat_pix_cache:
            sz   = self._MARKER_SZ_C
            cats = db.get_categories_by_ids([cid])
            pix  = None
            if cats:
                ip = cats[0].get('icon_path', '')
                if ip and os.path.exists(ip):
                    raw = QPixmap(ip)
                    if not raw.isNull():
                        pix = raw.scaled(sz, sz,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            self._cat_pix_cache[cid] = pix
        return self._cat_pix_cache[cid]

    @staticmethod
    def _marker_img_label(pix, size: int, fallback_color: str) -> 'QLabel':
        """Klein QLabel met pixmap of gekleurde placeholder als de pixmap ontbreekt."""
        lbl = QLabel()
        lbl.setFixedSize(size, size)
        if pix:
            lbl.setPixmap(pix)
        else:
            lbl.setStyleSheet(f"background:{fallback_color}; border-radius:3px;")
        return lbl

    def _current_pos(self) -> float:
        """Geeft de huidige mpv-afspeelpositie in seconden, of 0 bij fout/geen video."""
        try:
            return self.player.time_pos or 0
        except Exception:
            return 0

    # ── Timer ─────────────────────────────────

    def _setup_timer(self):
        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._update_ui)
        self.timer.start()

        # Timer voor achteruit-spelen (timer-based seeking i.p.v. mpv native reverse)
        self._reverse_timer = QTimer(self)
        self._reverse_timer.setInterval(50)
        self._reverse_timer.timeout.connect(self._reverse_tick)

    def _update_ui(self):
        if not self._video_path:
            return
        try:
            pos = self.player.time_pos
            dur = self.player.duration
            if pos is not None and dur and dur > 0:
                self._updating_slider = True
                self.timeline.setValue(int(pos / dur * 10000))
                self._updating_slider = False
                self._lbl_time.setText(f"{_fmt_hms(pos)} / {_fmt_hms(dur)}")

                # Skip negative zones during playback
                if self._skip_negative and not self.player.pause:
                    for start, end in self._neg_zones_cache:
                        if start <= pos < end:
                            self.player.seek(min(end, dur - 0.05), 'absolute+exact')
                            break

            if dur is not None and self._duration != dur:
                self._duration = dur
                if dur > 0 and self._video_path:
                    film = db.get_or_create_film(self._video_path)
                    db.set_film_duration(film['id'], dur)
                self._refresh_timeline_zones()
        except Exception:
            pass

    # ── Playback ──────────────────────────────

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", "",
            "Video bestanden (*.mp4 *.avi *.mov *.wmv *.mkv *.flv *.webm *.m4v *.mpg *.mpeg *.ts *.mts);;Alle bestanden (*)"
        )
        if path:
            self._selection_entries.clear()
            self._current_marker_row = -1
            self._load_video(path)

    def _load_video(self, path, start_time: float | None = None):
        self._reset_zoom()
        self._video_path = path
        self._duration = 0
        self._lbl_time.setText("--:-- / --:--")
        self._neg_zones_cache: list = []
        self._markers = load_markers(path)
        self._refresh_marker_list()   # also calls _refresh_timeline_zones
        if start_time is not None and start_time > 0:
            try:
                # Moderne mpv: options als dict (node map), geen string
                self.player.command('loadfile', path, 'replace', 0,
                                    {'start': str(round(start_time, 3))})
            except Exception:
                # Fallback: laad normaal en zoek zodra mpv klaar is
                self.player.play(path)
                QTimer.singleShot(0, lambda st=start_time: self._seek_when_ready(st))
        else:
            self.player.play(path)
        self.player.pause = False   # always start playing, even if previously paused
        film = db.get_or_create_film(path)
        self._actors_overlay.refresh(film['id'])
        self.status.showMessage(f"  {Path(path).name}  •  {path}")
        self.setWindowTitle(f"CineMarker  —  {Path(path).name}")
        self._suggest_actors_from_filename(path)

    def _suggest_actors_from_filename(self, path: str):
        stem = Path(path).stem
        normalized = re.sub(r'([a-z])([A-Z])', r'\1 \2', stem)
        normalized = re.sub(r'[_\-\.\s,()[\]{}]+', ' ', normalized).lower()
        filename_words = set(normalized.split())

        def _first_name_matches(actor):
            name = actor.get('name', '').strip()
            if not name:
                return False
            first = name.split()[0].lower()
            return first in filename_words

        matches = [a for a in db.get_all_actors() if _first_name_matches(a)]
        # Nooit het paneel tonen als fullscreen actief is (activeert anders het
        # hoofd-QMainWindow op Windows via het Tool-venster).
        if self._fs_win and self._fs_win.isVisible():
            return
        if matches:
            self._panel._search_page.update_results(matches)
            self._panel.show_search(True)
            if not self._panel.isVisible():
                self._panel.show()
        else:
            self._panel.show_search(False)

    def _load_video_and_switch(self, path):
        """Load video and switch to player tab"""
        self._selection_entries.clear()
        self._current_marker_row = -1
        self._auto_advance_sec = 0
        self._auto_advance_timer.stop()
        if hasattr(self, '_fade_overlay'):
            self._fade_overlay.abort()
        self._load_video(path)
        self.main_tabs.setCurrentIndex(0)

    def _next_film(self):
        """Load the next film in the films panel list."""
        self._selection_entries.clear()
        self._current_marker_row = -1
        self._auto_advance_sec = 0
        self._auto_advance_timer.stop()
        if hasattr(self, '_fade_overlay'):
            self._fade_overlay.abort()
        film_list = self.films_panel.film_list
        n = film_list.count()
        if n == 0:
            return
        current_path = self._video_path or ''
        # Find current index
        current_idx = -1
        for i in range(n):
            item = film_list.item(i)
            if item and not item.isHidden():
                d = item.data(Qt.ItemDataRole.UserRole)
                if d and d.get('path') == current_path:
                    current_idx = i
                    break
        # Find next visible item
        start = current_idx + 1
        for i in range(start, n):
            item = film_list.item(i)
            if item and not item.isHidden():
                d = item.data(Qt.ItemDataRole.UserRole)
                if d:
                    self._load_video(d['path'])
                    return
        # Wrap around
        for i in range(0, start):
            item = film_list.item(i)
            if item and not item.isHidden():
                d = item.data(Qt.ItemDataRole.UserRole)
                if d:
                    self._load_video(d['path'])
                    return

    def _delete_current_film(self):
        """Move the currently playing film to the 'deleted/' subfolder."""
        if not self._video_path:
            return
        path = self._video_path
        name = Path(path).stem

        reply = QMessageBox.question(
            self,
            "Film verplaatsen",
            f"'{name}' verplaatsen naar de map 'deleted'?\n\n"
            "De film verdwijnt uit de applicatie maar blijft als\n"
            "bestand bewaard in de submap 'deleted'.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Stop playback first
        try:
            self.player.stop()
        except Exception:
            pass
        self._video_path = None

        ok, msg = self.films_panel.delete_film(path)
        if ok:
            self.status.showMessage(f"Verplaatst naar 'deleted/': {name}")
            self.main_tabs.setCurrentIndex(self.main_tabs.indexOf(self.films_panel))
        else:
            QMessageBox.warning(self, "Fout bij verplaatsen", msg)

    def _on_scene_jump(self, film_path, start_time):
        """Jump to a scene: load film if needed, seek to start."""
        if self._video_path != film_path:
            # start_time wordt direct meegegeven aan mpv via loadfile start=
            # zodat er geen poll-loop nodig is
            self._load_video(film_path, start_time=start_time)
        else:
            self.player.seek(start_time, 'absolute+exact')
        self.main_tabs.setCurrentIndex(0)

    # ── Selectie-afspeellijst (vanuit markers-tab) ────────────────

    def _load_selection(self, entries: list, interval_sec: int = 0):
        """Laad een cross-film afspeellijst vanuit het markers-tabblad.
        interval_sec > 0 schakelt auto-advance in: na N seconden fade naar volgende marker."""
        if not entries:
            return
        # Sorteer op film + tijd zodat je per film afspeelt
        self._selection_entries = sorted(
            entries,
            key=lambda e: (e['film_path'], e['marker'].get('time', 0))
        )
        self._auto_advance_sec = max(0, interval_sec)
        self._auto_advance_timer.stop()
        if hasattr(self, '_fade_overlay'):
            self._fade_overlay.abort()

        first      = self._selection_entries[0]
        first_path = first['film_path']
        first_time = first['marker'].get('time', 0)

        self._current_marker_row = 0   # start bij eerste marker

        self.main_tabs.setCurrentIndex(0)   # naar speler-tab
        if not self._panel.isVisible():
            self._panel.show()
        self._panel.show_search(False)

        if self._video_path != first_path:
            self._load_video(first_path, start_time=first_time)
        else:
            self._refresh_marker_list()     # selectie-modus activeren
            self.player.seek(first_time, 'absolute+exact')
        if self._auto_advance_sec > 0:
            self._restart_auto_advance()

    def _refresh_selection_markers(self):
        """Bouw de marker-list op uit self._selection_entries (meerdere films)."""
        self.marker_list.clear()
        # _current_marker_row wordt na het vullen hersteld (zie einde methode)
        prev_film = None
        for entry in self._selection_entries:
            m         = entry['marker']
            film_path = entry['film_path']
            film_name = entry['film_name']

            item = QListWidgetItem()
            self.marker_list.addItem(item)

            row_w = QWidget()
            # Subtiel andere achtergrond als de film wisselt
            is_alt = (film_path != prev_film) and (prev_film is not None)
            row_w.setStyleSheet(
                "background: #141414;" if is_alt else "background: transparent;"
            )
            prev_film = film_path

            rh = QHBoxLayout(row_w)
            rh.setContentsMargins(4, 0, 4, 0)
            rh.setSpacing(3)

            # Acteur-foto('s)
            for aid in (m.get('actors') or []):
                rh.addWidget(self._marker_img_label(
                    self._marker_actor_pix(aid), self._MARKER_SZ_A, '#222'))
            # Categorie-icoon(tjes)
            for cid in (m.get('categories') or []):
                rh.addWidget(self._marker_img_label(
                    self._marker_cat_pix(cid), self._MARKER_SZ_C, '#1a1a2a'))

            # Tijdcode
            s = int(m.get('time', 0))
            time_str = f"{s // 60:02d}:{s % 60:02d}"
            lbl_t = QLabel(time_str)
            lbl_t.setStyleSheet(
                "color:#888; font-size:11px;"
                " font-family:'Consolas',monospace; background:transparent;"
            )
            lbl_t.setFixedWidth(34)
            rh.addWidget(lbl_t)

            # Filmnaam (ingekort)
            lbl_f = QLabel(film_name[:18])
            lbl_f.setStyleSheet(
                "color:#444; font-size:9px;"
                " font-family:'Consolas',monospace; background:transparent;"
            )
            rh.addStretch()
            rh.addWidget(lbl_f)

            item.setSizeHint(QSize(0, self._MARKER_ROW_H))
            self.marker_list.setItemWidget(item, row_w)

        # Herstel de selectie na de rebuild
        if 0 <= self._current_marker_row < self.marker_list.count():
            self.marker_list.setCurrentRow(self._current_marker_row)

    # ── Auto-advance ─────────────────────────────────────────────

    def _restart_auto_advance(self):
        """(Her)start de auto-advance timer voor N seconden."""
        if self._auto_advance_sec > 0 and self._selection_entries:
            self._auto_advance_timer.start(self._auto_advance_sec * 1000)

    def _do_auto_advance(self):
        """Wordt aangeroepen door de timer: crossfade naar de volgende marker."""
        n = self.marker_list.count()
        if n == 0 or not self._selection_entries:
            return
        next_row = (self._current_marker_row + 1) % n

        def _jump():
            self._current_marker_row = next_row
            self.marker_list.setCurrentRow(next_row)
            self._on_marker_jump()
            # Herstart timer nadat de jump klaar is; de crossfade-fadeout loopt
            # ondertussen door maar de nieuwe marker is al gestart.
            self._restart_auto_advance()

        if hasattr(self, '_fade_overlay'):
            # In fullscreen rendert mpv naar _fs_win._video, niet naar video_container
            active_vc = (
                self._fs_win._video
                if (self._fs_win and self._fs_win.isVisible())
                else self.video_container
            )
            self._fade_overlay.trigger(_jump, video_widget=active_vc)
        else:
            _jump()

    def _seek_when_ready(self, target: float, attempts: int = 60):
        """Seek to target once mpv has finished loading (duration > 0).
        Retries every 50 ms for up to ~3 seconds before giving up."""
        try:
            dur = self.player.duration
            if dur and dur > 0:
                self.player.seek(target, 'absolute+exact')
                return
        except Exception:
            pass
        if attempts > 0:
            QTimer.singleShot(50, lambda: self._seek_when_ready(target, attempts - 1))

    def toggle_play(self):
        if not self._video_path:
            return
        if self._current_speed < 0:
            # Achteruit-modus: reverse timer aan/uit (gebruik huidig interval, niet 50ms hardcoded)
            if self._reverse_timer.isActive():
                self._reverse_timer.stop()
            else:
                self._reverse_timer.start()
            return
        now_paused = not self.player.pause   # dit is de nieuwe staat na de toggle
        self.player.pause = now_paused
        # Auto-advance: stoppen bij pauzeren, hervatten bij afspelen
        if self._auto_advance_sec > 0 and self._selection_entries:
            if now_paused:
                self._auto_advance_timer.stop()
                if hasattr(self, '_fade_overlay'):
                    self._fade_overlay.abort()
            else:
                self._restart_auto_advance()

    def seek_relative(self, seconds):
        if not self._video_path:
            return
        self.player.seek(seconds, 'relative+exact')

    def _step_back_sequentially(self, remaining: int):
        """Stap frame voor frame achteruit met een kleine pauze ertussen.
        Geeft hetzelfde tick-tick-tick gevoel als frame_step() vooruit."""
        if remaining <= 0 or not self._video_path:
            return
        try:
            self.player.frame_back_step()
        except Exception:
            return
        if remaining > 1:
            QTimer.singleShot(75, lambda: self._step_back_sequentially(remaining - 1))

    def seek_frames(self, n):
        """Step n frames forward (n>0) or backward (n<0).
        Backward uses frame_back_step() — exact, frame-accurate."""
        if not self._video_path or n == 0:
            return
        if n > 0:
            for _ in range(n):
                self.player.frame_step()
        elif n == -1:
            self.player.frame_back_step()
        else:
            # Meerdere frames achteruit: één voor één met kleine vertraging
            # zodat de gebruiker elk frame ziet (zelfde feel als vooruit)
            self._step_back_sequentially(abs(n))

    def go_to_start(self):
        if self._video_path:
            self.player.seek(0, 'absolute+exact')

    def go_to_end(self):
        if self._video_path and self._duration:
            self.player.seek(self._duration - 0.1, 'absolute+exact')

    # ── Playback speed ────────────────────────

    _SPEED_STEPS = [
        -50.0, -30.0, -20.0, -15.0, -10.0, -8.0, -6.0, -5.0, -4.0,
        -3.0, -2.0, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25,
        0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0,
        8.0, 10.0, 15.0, 20.0, 30.0, 50.0,
    ]

    def _speed_up(self):
        steps = self._SPEED_STEPS
        cur = self._current_speed
        faster = [s for s in steps if s > cur]
        self._apply_speed(faster[0] if faster else steps[-1])

    def _speed_down(self):
        steps = self._SPEED_STEPS
        cur = self._current_speed
        slower = [s for s in steps if s < cur]
        self._apply_speed(slower[-1] if slower else steps[0])

    def _reset_speed(self):
        self._apply_speed(1.0)

    def _apply_speed(self, speed: float):
        self._current_speed = speed
        if speed < 0:
            abs_s = abs(speed)
            self._reverse_speed = abs_s
            try:
                self.player.speed = 1.0
                self.player.pause = True
            except Exception:
                pass

            # Voor trage reverse (≤ 2×): frame_back_step() op fps-gebaseerd interval
            # → frame-accuraat en vloeiender dan seek-based
            # Voor snelle reverse (> 2×): seek-based (frame_back_step zou te langzaam zijn)
            if abs_s <= 2.0:
                self._reverse_use_frameback = True
                try:
                    fps = max(10.0, float(self.player.container_fps or 25.0))
                except Exception:
                    fps = 25.0
                # Interval in ms: 1 frame per (1000 / fps / abs_s) ms
                interval = max(16, int(1000 / (fps * abs_s)))
                self._reverse_timer.setInterval(interval)
            else:
                self._reverse_use_frameback = False
                self._reverse_timer.setInterval(50)

            self._reverse_timer.start()
        else:
            # Vooruit of stop: reverse timer uit, mpv speed instellen
            self._reverse_timer.stop()
            self._reverse_speed = 0.0
            self._reverse_use_frameback = False
            try:
                self.player.speed = speed
                if speed > 0:
                    self.player.pause = False
            except Exception:
                pass
        self._update_speed_label(speed)

    def _reverse_tick(self):
        if not self._video_path:
            return
        try:
            pos = self.player.time_pos
            if pos is None:
                return

            if self._reverse_use_frameback:
                # Frame-accurate reverse: één frame terug per tick
                # Controleer eerst of we al aan het begin zijn
                if pos <= 0:
                    self._stop_reverse()
                    return
                self.player.frame_back_step()
            else:
                # Seek-based reverse voor hogere snelheden (> 2×)
                seek_amount = self._reverse_speed * 0.05  # 50ms × snelheid = stap in seconden
                new_pos = pos - seek_amount
                if new_pos <= 0:
                    self.player.seek(0, 'absolute+exact')
                    self._stop_reverse()
                    return
                self.player.seek(-seek_amount, 'relative+exact')
        except Exception:
            pass

    def _stop_reverse(self):
        """Reset na bereiken begin of bij stoppen van reverse-modus."""
        self._reverse_timer.stop()
        self._current_speed = 1.0
        self._reverse_speed = 0.0
        self._reverse_use_frameback = False
        try:
            self.player.speed = 1.0
            self.player.pause = True
        except Exception:
            pass
        self._update_speed_label(1.0)

    def _update_speed_label(self, speed: float):
        abs_s = abs(speed)
        label = f"{int(speed)}×" if abs_s == int(abs_s) else f"{speed}×"
        self._btn_speed.setText(label)
        if speed < 0:
            # achteruit — blauw
            self._btn_speed.setStyleSheet(
                "QPushButton { background: transparent; border: none;"
                "  color: #6db8e8; font-size: 11px; font-family: 'Consolas', monospace;"
                "  font-weight: bold; }"
                "QPushButton:hover { color: #90cef0; }"
            )
        elif speed != 1.0:
            # vooruit afwijkend — amber
            self._btn_speed.setStyleSheet(
                "QPushButton { background: transparent; border: none;"
                "  color: #e8b86d; font-size: 11px; font-family: 'Consolas', monospace;"
                "  font-weight: bold; }"
                "QPushButton:hover { color: #f0ca8a; }"
            )
        else:
            # 1× normaal — grijs
            self._btn_speed.setStyleSheet(
                "QPushButton { background: transparent; border: none;"
                "  color: #444; font-size: 11px; font-family: 'Consolas', monospace; }"
                "QPushButton:hover { color: #888; }"
            )

    def _on_timeline_scrub(self, fraction):
        """Tijdens slepen op de tijdlijn: keyframe-seek voor directe visuele feedback."""
        if self._video_path and self._duration and not self._updating_slider:
            try:
                self.player.seek(fraction * self._duration, 'absolute')
            except Exception:
                pass

    def _on_timeline_seek(self, fraction):
        """Bij loslaten van de tijdlijn: exact-seek op het precieze frame."""
        if self._video_path and self._duration and not self._updating_slider:
            try:
                self.player.seek(fraction * self._duration, 'absolute+exact')
            except Exception:
                pass

    # ── Multi-tap seek ────────────────────────

    # playing:  1×=5s  2×=30s  3×=5min  4×=30min
    # paused:   1×=1frame  2×=1s  3×=10s  4×=1min
    _SEEK_PLAY   = [0,    5,   30,  300, 1800]
    _SEEK_PAUSE  = [0,    0,    1,   10,   60]  # 0 = frame step

    def _on_seek_key(self, direction: int):
        if not self._video_path:
            return
        if self._seek_dir != direction and self._seek_count > 0:
            self._seek_timer.stop()
            self._commit_seek()
        self._seek_dir    = direction
        self._seek_count  = min(self._seek_count + 1, 4)
        self._seek_timer.start()

    def _commit_seek(self):
        n, d = self._seek_count, self._seek_dir
        self._seek_count = 0
        self._seek_dir   = 0
        if n == 0 or not self._video_path:
            return
        try:
            paused = self.player.pause
        except Exception:
            paused = False
        if paused:
            # 1 press → 1 frame,  2 → 5 frames,  3 → 1 s,  4 → 5 s
            if n == 1:
                self.seek_frames(d)
            elif n == 2:
                self.seek_frames(d * 5)
            elif n == 3:
                self.seek_relative(d * 1)
            else:
                self.seek_relative(d * 5)
        else:
            self.seek_relative(d * self._SEEK_PLAY[n])

    # ── Markers ───────────────────────────────

    def _save_thumbnail_from_file(self, src_path: str):
        """Kopieer een al-bestaand afbeeldingsbestand als filmthumbnail naar de thumb-map."""
        if not self._video_path:
            return
        film = db.get_or_create_film(self._video_path)
        thumb_dir = THUMBNAILS_DIR
        thumb_dir.mkdir(exist_ok=True)
        import time as _time, shutil
        ts   = int(_time.time() * 1000)
        dest = str(thumb_dir / f"{film['id']}_thumb_{ts}.jpg")
        try:
            shutil.copy2(src_path, dest)
            db.add_film_thumbnail(film['id'], dest)
            db.set_film_thumbnail(film['id'], dest)
            self._actors_overlay.load_thumbnails(film['id'])
            folder = db.get_setting('film_folder', '')
            if folder:
                self.films_panel._scan_folder(folder)
            self.status.showMessage(f"  Thumbnail opgeslagen voor {Path(self._video_path).name}")
        except Exception as e:
            self.status.showMessage(f"  Thumbnail mislukt: {e}")

    def _capture_thumbnail(self):
        if not self._video_path:
            return
        film = db.get_or_create_film(self._video_path)
        thumb_dir = THUMBNAILS_DIR
        thumb_dir.mkdir(exist_ok=True)
        import time as _time
        ts   = int(_time.time() * 1000)
        path = str(thumb_dir / f"{film['id']}_thumb_{ts}.jpg")
        try:
            # 'window' mode captures what is actually rendered on screen —
            # i.e. the zoomed/panned view the user is looking at, not the raw frame.
            # Temporarily mute OSD so the time display is not baked into the thumbnail.
            try:
                old_osd = self.player.osd_level
                self.player.osd_level = 0
            except Exception:
                old_osd = None
            try:
                self.player.command('screenshot-to-file', path, 'window')
            finally:
                if old_osd is not None:
                    try:
                        self.player.osd_level = old_osd
                    except Exception:
                        pass
            db.add_film_thumbnail(film['id'], path)
            db.set_film_thumbnail(film['id'], path)   # keep backward-compat primary
            # Reload overlay with full thumbnail list so it starts cycling
            self._actors_overlay.load_thumbnails(film['id'])
            folder = db.get_setting('film_folder', '')
            if folder:
                self.films_panel._scan_folder(folder)
            self.status.showMessage(f"  Thumbnail opgeslagen voor {Path(self._video_path).name}")
        except Exception as e:
            self.status.showMessage(f"  Thumbnail mislukt: {e}")

    def _quick_marker(self, actors: list, categories: list,
                      pos: float | None = None, stars: int = 0):
        """Create marker — actor + category both required."""
        if not self._video_path:
            return

        # ── Validate actor ───────────────────────
        if not actors:
            self.status.showMessage("  Selecteer eerst een acteur")
            return

        # ── Validate / pick category ─────────────
        if not categories:
            cats = db.get_all_categories()
            if not cats:
                self.status.showMessage("  Maak eerst een categorie aan in het database-tabblad")
                return
            chosen = self._pick_category_dialog(cats)
            if chosen is None:
                return          # user cancelled
            categories = [chosen]

        if pos is None:
            pos = self._current_pos()

        cat_names   = [c['name'] for c in categories]
        actor_names = [a['name'] for a in actors]
        name = ', '.join(cat_names) + ' — ' + ', '.join(actor_names)

        import time as _time
        marker = {
            'time':       pos,
            'name':       name,
            'actors':     [a['id'] for a in actors],
            'categories': [c['id'] for c in categories],
            'created':    datetime.now().isoformat(),
            'created_at': _time.time(),
            'stars':      stars if stars > 0 else None,
        }
        self._markers.append(marker)
        self._markers.sort(key=lambda m: m['time'])
        save_markers(self._video_path, self._markers)
        self._recalc_film_rating(self._video_path)
        self._refresh_marker_list()
        self._actors_overlay._cat_sel.clear()
        self._actors_overlay.update()
        self.status.showMessage(f"  Marker '{name}' op {format_time(pos)}")

    def _pick_category_dialog(self, cats: list) -> dict | None:
        """Small popup to pick one category. Returns the chosen dict or None."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Kies een categorie")
        dlg.setModal(True)
        dlg.setStyleSheet("""
            QDialog { background: #111; }
            QPushButton {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 6px;
                color: #ccc;
                font-size: 13px;
                padding: 8px 18px;
                min-width: 120px;
            }
            QPushButton:hover  { background: #252525; border-color: #e8b86d; color: #e8b86d; }
            QPushButton:pressed { background: #e8b86d; color: #000; }
            QLabel { color: #666; font-size: 10px; letter-spacing: 3px; }
        """)

        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        lbl = QLabel("CATEGORIE")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(lbl)

        chosen = [None]

        for cat in cats:
            icon_path = cat.get('icon_path', '')
            label = cat['name']
            btn = QPushButton(label)
            if icon_path:
                from PyQt6.QtGui import QIcon
                ico = QIcon(icon_path)
                if not ico.isNull():
                    btn.setIcon(ico)
            btn.clicked.connect(lambda _, c=cat: (chosen.__setitem__(0, c), dlg.accept()))
            v.addWidget(btn)

        cancel = QPushButton("Annuleren")
        cancel.setStyleSheet(
            "QPushButton { color: #444; border-color: #222; }"
            "QPushButton:hover { color: #888; border-color: #444; }"
        )
        cancel.clicked.connect(dlg.reject)
        v.addWidget(cancel)

        dlg.exec()
        return chosen[0]

    def _shortcut_esc(self):
        # Deselect categories, leave actors untouched
        self._actors_overlay._cat_sel.clear()
        self._actors_overlay.update()
        # Switch back from search to markers view (keep panel visible)
        self._panel.show_search(False)
        self._player_search.clear()
        # Return keyboard focus to the main window so shortcuts work
        self.setFocus()

    def _shortcut_space(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._move_p()
        else:
            self.toggle_play()

    def _shortcut_left(self):
        # Als een lijstwidget focus heeft: navigeer daarin in plaats van te seekken
        focused = QApplication.focusWidget()
        if isinstance(focused, QListWidget):
            row = focused.currentRow()
            if row > 0:
                focused.setCurrentRow(row - 1)
            return
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._prev()
        else:
            self.seek_relative(-5)

    def _shortcut_right(self):
        focused = QApplication.focusWidget()
        if isinstance(focused, QListWidget):
            row = focused.currentRow()
            if row < focused.count() - 1:
                focused.setCurrentRow(row + 1)
            return
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._next()
        else:
            self.seek_relative(5)

    def _shortcut_l(self):
        if self.main_tabs.currentWidget() is not self.sorter_panel:
            self._on_seek_key(1)

    def _shortcut_m(self):
        if self.main_tabs.currentWidget() is self.sorter_panel:
            self.sorter_panel._move_m()
        elif self._video_path:
            self._open_marker_quick_popup()

    def _open_marker_quick_popup(self):
        """M-toets: gefixeerd frame + acteurs + categoriekeuze → marker."""
        actors = self._actors_overlay.selected_actors()
        if not actors:
            self.status.showMessage("  Selecteer eerst een acteur (linksonder)")
            return

        cats = db.get_all_categories()
        if not cats:
            self.status.showMessage("  Maak eerst een categorie aan in het database-tabblad")
            return

        pos = self._current_pos()

        # Frame opvangen via mpv screenshot naar tijdelijk bestand
        # 'window' mode respecteert zoom/pan — zelfde als _capture_thumbnail.
        frame_pix = None
        tmp_path: str | None = None
        try:
            import tempfile
            tmp_path = tempfile.mktemp(suffix='.jpg')
            # OSD tijdelijk uitzetten zodat het tijdstip niet in het frame gebakken zit
            try:
                _old_osd = self.player.osd_level
                self.player.osd_level = 0
            except Exception:
                _old_osd = None
            try:
                self.player.command('screenshot-to-file', tmp_path, 'window')
            finally:
                if _old_osd is not None:
                    try:
                        self.player.osd_level = _old_osd
                    except Exception:
                        pass
            if os.path.exists(tmp_path):
                frame_pix = QPixmap(tmp_path)
            else:
                tmp_path = None
        except Exception:
            tmp_path = None
            pass  # popup werkt ook zonder frame

        try:
            _dlg_parent = (
                self._fs_win
                if (self._fs_win and self._fs_win.isVisible())
                else self
            )
            _film_obj  = db.get_or_create_film(self._video_path) if self._video_path else None
            _film_id   = _film_obj['id'] if _film_obj else None
            _fc_types  = db.get_film_categorie_types()
            _fc_cur    = db.get_film_category_ids(_film_id) if _film_id else set()
            dlg = _MarkerQuickDlg(_dlg_parent, actors, pos, frame_pix, cats,
                                  film_id=_film_id, film_cat_types=_fc_types,
                                  initial_film_cat_ids=_fc_cur)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                chosen_actors, chosen_cat, stars = dlg.get_result()
                self._quick_marker(chosen_actors, [chosen_cat], pos=pos, stars=stars)

                # Thumbnail opslaan als de gebruiker dat gevraagd heeft
                if dlg.wants_thumbnail() and tmp_path and os.path.exists(tmp_path):
                    self._save_thumbnail_from_file(tmp_path)
        finally:
            # Tijdelijk bestand altijd opruimen
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            # Focus teruggeven — aan fullscreen als die actief is, anders aan hoofdvenster
            if self._fs_win and self._fs_win.isVisible():
                self._fs_win.activateWindow()
                self._fs_win.setFocus()
            else:
                self.activateWindow()
                self.setFocus()

    def _shortcut_n(self):
        if self.main_tabs.currentWidget() is not self.sorter_panel:
            self._on_seek_key(-1)

    def _shortcut_p(self):
        """Ga naar de volgende marker in de lijst (wraps rond)."""
        if self.main_tabs.currentWidget() is self.sorter_panel:
            return
        if not self._video_path:
            return
        n = self.marker_list.count()
        if n == 0:
            return
        # Paneel alleen tonen als we NIET in fullscreen zijn — anders activeert
        # het tonen van het Tool-venster het hoofd-QMainWindow op Windows.
        if not (self._fs_win and self._fs_win.isVisible()):
            if not self._panel.isVisible():
                self._panel.show()
            self._panel.show_search(False)
        # Breek een lopende auto-advance fade af (handmatige skip heeft voorrang)
        self._auto_advance_timer.stop()
        if hasattr(self, '_fade_overlay'):
            self._fade_overlay.abort()
        next_row = (self._current_marker_row + 1) % n
        self._current_marker_row = next_row
        self.marker_list.setCurrentRow(next_row)
        self._on_marker_jump()
        # Herstart timer zodat de teller opnieuw begint na een handmatige skip
        self._restart_auto_advance()

    def _shortcut_o(self):
        """Ga naar de vorige marker in de lijst (wraps rond)."""
        if self.main_tabs.currentWidget() is self.sorter_panel:
            return
        if not self._video_path:
            return
        n = self.marker_list.count()
        if n == 0:
            return
        # Paneel alleen tonen als we NIET in fullscreen zijn — anders activeert
        # het tonen van het Tool-venster het hoofd-QMainWindow op Windows.
        if not (self._fs_win and self._fs_win.isVisible()):
            if not self._panel.isVisible():
                self._panel.show()
            self._panel.show_search(False)
        # Breek een lopende auto-advance fade af (handmatige skip heeft voorrang)
        self._auto_advance_timer.stop()
        if hasattr(self, '_fade_overlay'):
            self._fade_overlay.abort()
        prev_row = (self._current_marker_row - 1) % n
        self._current_marker_row = prev_row
        self.marker_list.setCurrentRow(prev_row)
        self._on_marker_jump()
        # Herstart timer zodat de teller opnieuw begint na een handmatige skip
        self._restart_auto_advance()

    def _shortcut_plus(self):
        self._zoom_in_video()

    def _shortcut_minus(self):
        self._zoom_out_video()

    # ── Video zoom / pan ──────────────────────────

    def _zoom_in_video(self):
        self._zoom_level += 0.25
        self._apply_zoom_pan()
        self.video_container.setCursor(Qt.CursorShape.OpenHandCursor)

    def _zoom_out_video(self):
        self._zoom_level = max(0.0, self._zoom_level - 0.25)
        if self._zoom_level == 0.0:
            self._pan_x = 0.0
            self._pan_y = 0.0
            self.video_container.setCursor(Qt.CursorShape.ArrowCursor)
        self._apply_zoom_pan()

    def _reset_zoom(self):
        self._zoom_level = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_active = False
        self._apply_zoom_pan()
        self.video_container.setCursor(Qt.CursorShape.ArrowCursor)

    def _apply_zoom_pan(self):
        try:
            self.player['video-zoom']  = self._zoom_level
            self.player['video-pan-x'] = self._pan_x
            self.player['video-pan-y'] = self._pan_y
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj is self.video_container:
            t = event.type()
            if t == QEvent.Type.MouseButtonPress:
                # Any click on the video area reclaims focus from _player_search
                # (or any other text field that may have retained it)
                self.video_container.setFocus(Qt.FocusReason.MouseFocusReason)
                if event.button() == Qt.MouseButton.LeftButton:
                    _play_ui_click()          # instant audible confirmation
                    self._click_flash.trigger()   # brief ✓ flash at centre
                    if self._zoom_level > 0:
                        self._drag_active = True
                        self._drag_last = event.pos()
                        self.video_container.setCursor(Qt.CursorShape.ClosedHandCursor)
                        return True
            elif t == QEvent.Type.MouseMove:
                if self._drag_active:
                    delta = event.pos() - self._drag_last
                    self._drag_last = event.pos()
                    w = self.video_container.width()
                    h = self.video_container.height()
                    self._pan_x += delta.x() / w
                    self._pan_y += delta.y() / h
                    self._apply_zoom_pan()
                    return True
            elif t == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton and self._drag_active:
                    self._drag_active = False
                    self.video_container.setCursor(Qt.CursorShape.OpenHandCursor)
                    return True
            elif t == QEvent.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton and self._zoom_level > 0:
                    self._reset_zoom()
                    return True
        return super().eventFilter(obj, event)

    # ── Negative-zone helpers ─────────────────────

    def _get_neg_zones(self) -> list:
        """Returns [(start_sec, end_sec), ...] for each negative period.
        A zone starts at a negative marker and ends at the next non-negative
        marker (or end of film).
        """
        dur = self._duration
        if not self._markers or not dur:
            return []
        sorted_m = sorted(self._markers, key=lambda m: m.get('time', 0))
        zones = []
        for i, m in enumerate(sorted_m):
            if not m.get('negative'):
                continue
            start = m['time']
            end = dur
            for j in range(i + 1, len(sorted_m)):
                if not sorted_m[j].get('negative'):
                    end = sorted_m[j]['time']
                    break
            if end > start:
                zones.append((start, end))
        return zones

    def _refresh_timeline_zones(self):
        zones = self._get_neg_zones()
        self._neg_zones_cache = zones
        dur = self._duration or 1
        self.timeline.set_neg_zones([(s / dur, e / dur) for s, e in zones])
        # Blauwe stipjes voor gewone (niet-negatieve) markers
        fracs = [m['time'] / dur for m in self._markers
                 if not m.get('negative') and dur > 0]
        self.timeline.set_marker_fracs(fracs)

    def _add_negative_marker(self):
        if not self._video_path:
            return
        pos = self._current_pos()
        marker = {
            'time':       pos,
            'name':       'SKIP',
            'actors':     [],
            'categories': [],
            'negative':   True,
            'created':    datetime.now().isoformat(),
        }
        self._markers.append(marker)
        self._markers.sort(key=lambda m: m['time'])
        save_markers(self._video_path, self._markers)
        self._refresh_marker_list()
        # Switch panel back to markers view so the new negative marker is visible
        self._panel.show_search(False)
        if not self._panel.isVisible():
            self._panel.show()
        # Zet skip-negative altijd uit zodat je niet direct naar het einde springt
        if self._skip_negative:
            self._skip_negative = False
            self._btn_skip_neg.setChecked(False)
            db.set_setting('skip_negative', '0')
        self.status.showMessage(f"  Negatieve marker gezet op {_fmt_hms(pos)}  •  skip UIT")

    def _toggle_skip_negative(self):
        self._skip_negative = self._btn_skip_neg.isChecked()
        db.set_setting('skip_negative', '1' if self._skip_negative else '0')
        state = "aan" if self._skip_negative else "uit"
        self.status.showMessage(f"  Negatieve perioden overslaan: {state}")

    def _refresh_marker_list(self):
        if self._selection_entries:
            self._refresh_selection_markers()
            return
        # Normale modus: nieuwe film → teller resetten
        self._current_marker_row = -1
        self.marker_list.clear()

        for idx, m in enumerate(self._markers):
            is_neg = m.get('negative', False)

            item = QListWidgetItem()
            self.marker_list.addItem(item)

            row_w = QWidget()
            row_w.setStyleSheet(
                "background: #1f0808;" if is_neg else "background: transparent;"
            )
            rh = QHBoxLayout(row_w)
            rh.setContentsMargins(4, 0, 4, 0)
            rh.setSpacing(3)

            if is_neg:
                # Negative marker — red ⊘ symbol instead of actor/category icons
                lbl_neg = QLabel("⊘")
                lbl_neg.setStyleSheet(
                    "color:#cc3333; font-size:16px; font-weight:bold; background:transparent;")
                lbl_neg.setFixedWidth(self._MARKER_SZ_A)
                rh.addWidget(lbl_neg)
            else:
                # Actor photo(s)
                for aid in (m.get('actors') or []):
                    rh.addWidget(self._marker_img_label(
                        self._marker_actor_pix(aid), self._MARKER_SZ_A, '#222'))
                # Category icon(s)
                for cid in (m.get('categories') or []):
                    rh.addWidget(self._marker_img_label(
                        self._marker_cat_pix(cid), self._MARKER_SZ_C, '#1a1a2a'))

            # MM:SS time
            s = int(m.get('time', 0))
            time_str = f"{s // 60:02d}:{s % 60:02d}"
            lbl_t = QLabel(time_str)
            lbl_t.setStyleSheet(
                ("color:#cc3333;" if is_neg else "color:#888;") +
                " font-size:11px; font-family:'Consolas',monospace; background:transparent;")
            lbl_t.setFixedWidth(34)
            rh.addWidget(lbl_t)

            if is_neg:
                lbl_skip = QLabel("SKIP")
                lbl_skip.setStyleSheet(
                    "color:#883333; font-size:9px; letter-spacing:2px; background:transparent;")
                rh.addWidget(lbl_skip)

            rh.addStretch()

            # Edit button (gewone markers) / delete button (negatieve markers)
            if not is_neg:
                btn_edit = QPushButton("✎")
                btn_edit.setFixedSize(20, 20)
                btn_edit.setToolTip("Categorie / acteurs / score aanpassen of marker verwijderen")
                btn_edit.setStyleSheet(
                    "QPushButton{background:#1a1a2a;border:1px solid #333;"
                    "border-radius:3px;color:#888;font-size:12px;}"
                    "QPushButton:hover{background:#1e1e40;border-color:#8888cc;color:#aaaaff;}"
                    "QPushButton:pressed{background:#3333aa;color:#fff;}")
                btn_edit.clicked.connect(lambda _, i=idx: self._edit_marker_by_index(i))
                rh.addWidget(btn_edit)
            else:
                btn_del = QPushButton("✕")
                btn_del.setFixedSize(20, 20)
                btn_del.setStyleSheet(
                    "QPushButton{background:#2a2a2a;border:1px solid #444;"
                    "border-radius:3px;color:#ccc;font-size:12px;font-weight:bold;}"
                    "QPushButton:hover{background:#6b1f1f;border-color:#e05555;color:#fff;}"
                    "QPushButton:pressed{background:#e05555;color:#fff;}")
                btn_del.clicked.connect(lambda _, i=idx: self._delete_marker_by_index(i))
                rh.addWidget(btn_del)

            item.setSizeHint(QSize(0, self._MARKER_ROW_H))
            self.marker_list.setItemWidget(item, row_w)

        # Keep zones in sync whenever markers change
        self._refresh_timeline_zones()

    def _delete_marker_by_index(self, idx: int):
        if 0 <= idx < len(self._markers):
            self._markers.pop(idx)
            save_markers(self._video_path, self._markers)
            self._recalc_film_rating(self._video_path)
            self._refresh_marker_list()

    def _edit_marker_by_index(self, idx: int):
        if not (0 <= idx < len(self._markers)):
            return
        m = self._markers[idx]
        if m.get('negative'):
            return
        actors = [db.get_actor(aid) for aid in (m.get('actors') or [])]
        actors = [a for a in actors if a]
        cats   = db.get_all_categories()
        if not cats:
            self.status.showMessage("  Maak eerst een categorie aan in het database-tabblad")
            return
        stars      = int(m.get('stars') or 0)
        cur_cat_id = (m.get('categories') or [None])[0]
        _film_obj_e  = db.get_or_create_film(self._video_path) if self._video_path else None
        _film_id_e   = _film_obj_e['id'] if _film_obj_e else None
        _fc_types_e  = db.get_film_categorie_types()
        _fc_cur_e    = db.get_film_category_ids(_film_id_e) if _film_id_e else set()
        dlg = _MarkerQuickDlg(self, actors, m.get('time', 0), None, cats,
                              initial_stars=stars, show_delete=True,
                              initial_cat_id=cur_cat_id,
                              film_id=_film_id_e, film_cat_types=_fc_types_e,
                              initial_film_cat_ids=_fc_cur_e)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.was_deleted():
                self._delete_marker_by_index(idx)
                return
            new_actors, chosen_cat, new_stars = dlg.get_result()
            m['actors']     = [a['id'] for a in new_actors]
            m['categories'] = [chosen_cat['id']] if chosen_cat else m.get('categories', [])
            m['stars']      = new_stars if new_stars > 0 else None
            save_markers(self._video_path, self._markers)
            self._recalc_film_rating(self._video_path)
            self._refresh_marker_list()

    def _edit_marker_from_panel(self, marker: dict, film_path: str):
        """Bewerk een marker vanuit het markers-tabblad (context menu ✎)."""
        import json as _json
        from pathlib import Path as _Path
        actors = [db.get_actor(aid) for aid in (marker.get('actors') or [])]
        actors = [a for a in actors if a]
        cats   = db.get_all_categories()
        if not cats:
            self.status.showMessage("  Maak eerst een categorie aan in het database-tabblad")
            return
        stars      = int(marker.get('stars') or 0)
        cur_cat_id = (marker.get('categories') or [None])[0]
        _film_obj_p  = db.get_or_create_film(film_path) if film_path else None
        _film_id_p   = _film_obj_p['id'] if _film_obj_p else None
        _fc_types_p  = db.get_film_categorie_types()
        _fc_cur_p    = db.get_film_category_ids(_film_id_p) if _film_id_p else set()
        dlg = _MarkerQuickDlg(self, actors, marker.get('time', 0), None, cats,
                              initial_stars=stars, show_delete=True,
                              initial_cat_id=cur_cat_id,
                              film_id=_film_id_p, film_cat_types=_fc_types_p,
                              initial_film_cat_ids=_fc_cur_p)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        p  = _Path(film_path)
        mf = p.parent / f".{p.stem}_markers.json"
        if not mf.exists():
            return
        try:
            markers = _json.loads(mf.read_text('utf-8'))
        except Exception:
            return
        target_time = marker.get('time')
        if dlg.was_deleted():
            markers = [m for m in markers if m.get('time') != target_time]
        else:
            new_actors, chosen_cat, new_stars = dlg.get_result()
            for m in markers:
                if m.get('time') == target_time:
                    m['actors']     = [a['id'] for a in new_actors]
                    m['categories'] = [chosen_cat['id']] if chosen_cat else m.get('categories', [])
                    m['stars']      = new_stars if new_stars > 0 else None
                    break
        mf.write_text(_json.dumps(markers, ensure_ascii=False, indent=2), 'utf-8')
        self._recalc_film_rating(film_path)
        self.markers_panel.refresh()
        # Als de film ook open staat in de speler, herlaad de markerlijst
        if self._video_path and str(_Path(self._video_path).resolve()) == str(p.resolve()):
            self._markers = markers
            self._refresh_marker_list()

    def _recalc_film_rating(self, film_path: str):
        """Herbereken de afgeleide_rating van een film op basis van marker-sterren.
        Som van alle sterren (niet-negatieve markers), afgetopt op 10.
        Slaat op in DB en werkt het films-panel live bij."""
        try:
            markers = load_markers(film_path)
            total = sum(int(m.get('stars') or 0)
                        for m in markers
                        if not m.get('negative'))
            total = min(total, 10)
            db.update_afgeleide_rating(film_path, total)
            self.films_panel.update_film_rating(film_path, total)
        except Exception:
            pass

    def _on_marker_jump(self, item=None):
        row = self.marker_list.currentRow()
        if self._selection_entries:
            if 0 <= row < len(self._selection_entries):
                entry = self._selection_entries[row]
                self._on_scene_jump(entry['film_path'],
                                    entry['marker'].get('time', 0))
        else:
            if 0 <= row < len(self._markers):
                t = self._markers[row]['time']
                # Directe exact-seek — geen twee fases, voorkomt flash van verkeerde keyframe
                try:
                    self.player.seek(t, 'absolute+exact')
                except Exception:
                    pass
        # Focus teruggeven — aan fullscreen als die actief is, anders aan hoofdvenster.
        # Gebruik singleShot(0) zodat de re-raise na alle synchrone Qt-events plaatsvindt
        # en niet verliest van een latere activatie van het hoofd-QMainWindow.
        if self._fs_win and self._fs_win.isVisible():
            def _refocus():
                if self._fs_win and self._fs_win.isVisible():
                    self._fs_win.raise_()
                    self._fs_win.activateWindow()
            QTimer.singleShot(0, _refocus)
        else:
            self.activateWindow()
            self.video_container.setFocus(Qt.FocusReason.OtherFocusReason)

    def _do_exact_seek(self):
        """Fase 2 van de marker-jump: land op het precieze frame."""
        if self._exact_seek_target is not None and self._video_path:
            try:
                self.player.seek(self._exact_seek_target, 'absolute+exact')
            except Exception:
                pass
        self._exact_seek_target = None

    def _marker_context_menu(self, pos):
        """Rechtermuisklik-menu op de marker-lijst."""
        if self._selection_entries:
            return  # selectie-modus toont meerdere films — niet bewerken
        row = self.marker_list.currentRow()
        if row < 0 or row >= len(self._markers):
            return
        menu = QMenu(self)
        menu.addAction("▶  Spring naar",   self._on_marker_jump)
        menu.addSeparator()
        menu.addAction("✎  Hernoem...",    self._on_marker_rename)
        menu.addAction("✕  Verwijder",     self._on_marker_delete)
        menu.exec(self.marker_list.mapToGlobal(pos))

    def _on_marker_rename(self):
        row = self.marker_list.currentRow()
        if 0 <= row < len(self._markers):
            old = self._markers[row]['name']
            name, ok = QInputDialog.getText(self, "Hernoem marker", "Nieuwe naam:", text=old)
            if ok and name:
                self._markers[row]['name'] = name
                save_markers(self._video_path, self._markers)
                self._refresh_marker_list()

    def _on_marker_delete(self):
        row = self.marker_list.currentRow()
        if 0 <= row < len(self._markers):
            m = self._markers[row]
            reply = QMessageBox.question(self, "Verwijder marker",
                f"Marker '{m['name']}' verwijderen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self._markers.pop(row)
                save_markers(self._video_path, self._markers)
                self._refresh_marker_list()

    def _export_markers_csv(self):
        if not self._video_path or not self._markers:
            QMessageBox.information(self, "Export", "Geen markers om te exporteren.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporteer CSV", "", "CSV (*.csv)")
        if path:
            with open(path, 'w', encoding='utf-8-sig') as f:
                f.write("Tijdcode,Seconden,Naam,Aangemaakt\n")
                for m in self._markers:
                    f.write(f"{format_time(m['time'])},{m['time']:.3f},{m['name']},{m.get('created','')}\n")
            self.status.showMessage(f"  Markers geëxporteerd naar {path}")

    # ── Thumbnail ─────────────────────────────

    def export_thumbnail(self):
        if not self._video_path:
            return
        pos = self._current_pos()

        default = str(Path(self._video_path).parent / f"thumb_{format_time(pos).replace(':', '-')}.jpg")
        path, _ = QFileDialog.getSaveFileName(self, "Sla thumbnail op", default, "JPEG (*.jpg);;PNG (*.png)")
        if not path:
            return

        self.status.showMessage(f"  Thumbnail exporteren op {format_time(pos)}…")
        self._thumb_worker = ThumbnailWorker(self._video_path, pos, path)
        self._thumb_worker.finished.connect(lambda p: self.status.showMessage(f"  Thumbnail opgeslagen: {p}"))
        self._thumb_worker.error.connect(lambda e: self.status.showMessage(f"  Fout: {e}"))
        self._thumb_worker.start()

    # ── Converter ─────────────────────────────

    def _conv_pick_input(self):
        path, _ = QFileDialog.getOpenFileName(self, "Invoerbestand", "",
            "Video bestanden (*.mp4 *.avi *.mov *.wmv *.mkv *.flv *.webm *.m4v);;Alle bestanden (*)")
        if path:
            self.conv_input.setText(path)
            self._conv_suggest_output()

    def _conv_use_current(self):
        if self._video_path:
            self.conv_input.setText(self._video_path)
            self._conv_suggest_output()

    def _conv_suggest_output(self):
        inp = self.conv_input.text()
        if inp:
            fmt = self.conv_format.currentText()
            p = Path(inp)
            self.conv_output.setText(str(p.parent / f"{p.stem}_converted.{fmt}"))

    def _conv_pick_output(self):
        fmt = self.conv_format.currentText()
        path, _ = QFileDialog.getSaveFileName(self, "Uitvoerbestand", "",
            f"{fmt.upper()} (*.{fmt});;Alle bestanden (*)")
        if path:
            self.conv_output.setText(path)

    def start_conversion(self):
        inp = self.conv_input.text().strip()
        out = self.conv_output.text().strip()

        if not inp or not os.path.exists(inp):
            QMessageBox.warning(self, "Fout", "Selecteer een geldig invoerbestand.")
            return
        if not out:
            QMessageBox.warning(self, "Fout", "Geef een uitvoerbestand op.")
            return

        codec_map = {
            "libx264 (H.264)": "libx264",
            "libx265 (H.265/HEVC)": "libx265",
            "libvpx-vp9 (VP9)": "libvpx-vp9",
            "copy (geen hercodering)": "copy"
        }
        codec = codec_map.get(self.conv_codec.currentText(), "libx264")
        res_map = {
            "Original": "Original",
            "3840x2160 (4K)": "3840x2160",
            "1920x1080 (1080p)": "1920x1080",
            "1280x720 (720p)": "1280x720",
            "854x480 (480p)": "854x480"
        }
        resolution = res_map.get(self.conv_res.currentText(), "Original")
        crf = self.conv_crf.value()

        self.btn_convert.setEnabled(False)
        self.conv_progress.setVisible(True)
        self.conv_progress.setValue(0)
        self.conv_status.setText("Bezig met converteren…")

        self._convert_worker = ConvertWorker(inp, out, codec, resolution, crf)
        self._convert_worker.progress.connect(self.conv_progress.setValue)
        self._convert_worker.finished.connect(self._on_convert_done)
        self._convert_worker.error.connect(self._on_convert_error)
        self._convert_worker.start()

    def _on_convert_done(self, path):
        self.btn_convert.setEnabled(True)
        self.conv_progress.setValue(100)
        self.conv_status.setText(f"✓ Klaar: {path}")
        self.status.showMessage(f"  Conversie voltooid: {path}")

    def _on_convert_error(self, err):
        self.btn_convert.setEnabled(True)
        self.conv_progress.setVisible(False)
        self.conv_status.setText(f"✗ Fout: {err[:200]}")

    # ── Help / Settings ───────────────────────

    def _show_help(self):
        from PyQt6.QtWidgets import (
            QTextEdit, QGroupBox, QTabWidget, QTreeWidget, QTreeWidgetItem,
            QListWidget, QListWidgetItem, QLineEdit, QLabel, QSplitter,
            QScrollArea,
        )

        # ── stylesheet helpers ──────────────────
        BASE_SS = """
            QDialog    { background: #0e0e0e; }
            QTabWidget::pane  { border: 1px solid #1e1e1e; background: #0e0e0e; }
            QTabBar::tab      { background: #141414; color: #666;
                                padding: 6px 18px; border: 1px solid #1e1e1e;
                                border-bottom: none; border-radius: 3px 3px 0 0;
                                margin-right: 2px; font-size: 11px; letter-spacing: 1px; }
            QTabBar::tab:selected  { background: #0e0e0e; color: #e8b86d; }
            QTabBar::tab:hover     { color: #ccc; }
            QTextEdit  { background: #0e0e0e; border: none;
                         color: #ccc; font-size: 12px;
                         font-family: 'Consolas', monospace; }
            QGroupBox  { color: #555; font-size: 10px; letter-spacing: 3px;
                         border: 1px solid #1e1e1e; border-radius: 4px;
                         margin-top: 6px; padding: 8px 10px 6px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QTreeWidget, QListWidget {
                background: #0a0a0a; border: 1px solid #1e1e1e;
                color: #ccc; font-size: 12px; outline: none; }
            QTreeWidget::item, QListWidget::item {
                padding: 4px 6px; border-bottom: 1px solid #141414; }
            QTreeWidget::item:selected, QListWidget::item:selected {
                background: #1a1200; color: #e8b86d; }
            QTreeWidget::item:hover, QListWidget::item:hover { background: #141414; }
            QLineEdit  { background: #141414; border: 1px solid #2a2a2a;
                         border-radius: 4px; padding: 5px 8px; color: #e0e0e0;
                         font-size: 12px; }
            QLineEdit:focus { border-color: #e8b86d; }
            QPushButton { background: #1e1e1e; border: 1px solid #333;
                          border-radius: 4px; padding: 5px 14px; color: #ccc;
                          font-size: 11px; }
            QPushButton:hover  { border-color: #e8b86d; color: #e8b86d; }
            QPushButton:pressed { background: #2a2000; }
            QPushButton#del_btn { color: #884444; border-color: #441414; }
            QPushButton#del_btn:hover { color: #ff6666; border-color: #884444; }
        """

        dlg = QDialog(self)
        dlg.setWindowTitle("Instellingen & sneltoetsen")
        dlg.resize(760, 820)
        dlg.setStyleSheet(BASE_SS)

        v = QVBoxLayout(dlg)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        # ── Acties (always visible, above tabs) ──
        grp_acties = QGroupBox("ACTIES")
        gh = QHBoxLayout(grp_acties)
        gh.setSpacing(8)

        btn_refresh_actors = QPushButton("↻  Acteurs & foto's herladen")
        btn_refresh_actors.setToolTip(
            "Nieuwe foto's in acteurfotos/ oppikken en acteurs-tab vernieuwen"
        )
        def _do_refresh():
            self.actors_panel.refresh()
            btn_refresh_actors.setText("✓  Herladen")
            QTimer.singleShot(1500, lambda: btn_refresh_actors.setText("↻  Acteurs & foto's herladen"))
        btn_refresh_actors.clicked.connect(_do_refresh)
        gh.addWidget(btn_refresh_actors)
        gh.addStretch()
        v.addWidget(grp_acties)

        # ── Tab widget ───────────────────────────
        tabs = QTabWidget()
        v.addWidget(tabs, 1)

        # ═══════════════════════════════════════
        #  TAB 1 — Sneltoetsen (bewerkbaar)
        # ═══════════════════════════════════════
        sc_page = QWidget()
        sc_page.setStyleSheet("QWidget { background: #0e0e0e; }")
        sc_v = QVBoxLayout(sc_page)
        sc_v.setContentsMargins(0, 0, 0, 0)
        sc_v.setSpacing(0)

        # Hint + reset-knop boven de lijst
        sc_top = QWidget()
        sc_top.setStyleSheet("background:#0d0d0d; border-bottom:1px solid #1e1e1e;")
        sc_top_h = QHBoxLayout(sc_top)
        sc_top_h.setContentsMargins(12, 6, 12, 6)
        sc_top_h.setSpacing(10)
        sc_hint = QLabel("Meerdere toetsen per actie: komma-gescheiden  (bijv.  <b>M,K</b>)  ·  "
                         "leeg = deactiveren  ·  Enter of klik buiten veld om op te slaan")
        sc_hint.setStyleSheet("color:#333; font-size:10px;")
        sc_top_h.addWidget(sc_hint)
        sc_top_h.addStretch()
        sc_btn_reset = QPushButton("↺  Alles resetten")
        sc_btn_reset.setFixedHeight(26)
        sc_btn_reset.setStyleSheet(
            "QPushButton{background:#111;border:1px solid #252525;border-radius:3px;"
            "color:#444;font-size:10px;padding:0 8px;}"
            "QPushButton:hover{border-color:#888;color:#aaa;}"
        )
        sc_top_h.addWidget(sc_btn_reset)
        sc_v.addWidget(sc_top)

        # Scrollbaar rij-raster
        sc_scroll = QScrollArea()
        sc_scroll.setWidgetResizable(True)
        sc_scroll.setStyleSheet(
            "QScrollArea{border:none;background:#0e0e0e;}"
            "QScrollBar:vertical{background:#0a0a0a;width:8px;}"
            "QScrollBar::handle:vertical{background:#2a2a2a;border-radius:4px;}"
        )
        sc_inner = QWidget()
        sc_inner.setStyleSheet("background:#0e0e0e;")
        sc_grid = QGridLayout(sc_inner)
        sc_grid.setContentsMargins(14, 8, 14, 8)
        sc_grid.setHorizontalSpacing(12)
        sc_grid.setVerticalSpacing(2)
        sc_grid.setColumnStretch(1, 1)

        # Bouw sneltoets-lookup  action_key -> (label, default)
        _sc_map = {a: (lbl, dflt) for a, lbl, dflt in SHORTCUT_DEFS}

        # Groepen met volgorde
        _SC_GROUPS = [
            ("GLOBAAL", [
                'ontsnappen', 'open_bestand', 'fullscreen', 'acteurs_tonen',
            ]),
            ("AFSPELEN  &  NAVIGATIE", [
                'play_pause', 'links', 'rechts',
                'skip_voor', 'skip_achter', 'begin', 'einde',
            ]),
            ("MARKERS", [
                'marker', 'neg_marker',
                'marker_voor', 'marker_achter',
                'volgende_film', 'thumbnail',
            ]),
            ("ZOOM  &  SNELHEID", [
                'zoom_in', 'zoom_uit', 'zoom_reset',
                'sneller', 'langzamer', 'reset_speed',
            ]),
        ]

        _HDR_SS  = ("color:#444;font-size:9px;letter-spacing:3px;"
                    "padding:10px 0 3px 0;")
        _LBL_SS  = "color:#555;font-size:11px;padding:1px 0;"
        _EDIT_SS = (
            "QLineEdit{background:#111;border:1px solid #1e1e1e;"
            "border-radius:3px;color:#e8b86d;font-size:11px;"
            "padding:2px 5px;}"
            "QLineEdit:focus{border-color:#e8b86d;}"
            "QLineEdit:hover{border-color:#333;}"
        )

        _sc_edits_dlg = []   # (action, QLineEdit, default)
        grid_row = 0

        for grp_name, actions in _SC_GROUPS:
            hdr = QLabel(grp_name)
            hdr.setStyleSheet(_HDR_SS)
            sc_grid.addWidget(hdr, grid_row, 0, 1, 2)
            grid_row += 1

            for action in actions:
                if action not in _sc_map:
                    continue
                lbl_txt, default = _sc_map[action]
                current = db.get_setting(f'shortcut_{action}', default)

                edit = QLineEdit(current)
                edit.setStyleSheet(_EDIT_SS)
                edit.setFixedWidth(120)
                edit.setFixedHeight(22)
                edit.setPlaceholderText(default)
                edit.setToolTip(f"Standaard: {default}  ·  meerdere toetsen: komma-gescheiden")

                lbl = QLabel(lbl_txt)
                lbl.setStyleSheet(_LBL_SS)

                sc_grid.addWidget(edit, grid_row, 0)
                sc_grid.addWidget(lbl,  grid_row, 1)
                grid_row += 1

                edit.editingFinished.connect(
                    lambda a=action, e=edit: (
                        db.set_setting(f'shortcut_{a}', e.text().strip()),
                        self._reload_shortcuts(),
                    )
                )
                _sc_edits_dlg.append((action, edit, default))

        sc_scroll.setWidget(sc_inner)
        sc_v.addWidget(sc_scroll, stretch=1)

        def _reset_all_sc():
            for action, edit, default in _sc_edits_dlg:
                edit.blockSignals(True)
                edit.setText(default)
                edit.blockSignals(False)
                db.set_setting(f'shortcut_{action}', default)
            self._reload_shortcuts()

        sc_btn_reset.clicked.connect(_reset_all_sc)

        tabs.addTab(sc_page, "Sneltoetsen")

        # ═══════════════════════════════════════
        #  TAB 2 — Categorieën
        # ═══════════════════════════════════════
        cat_page = QWidget()
        cat_page.setStyleSheet("QWidget { background: #0e0e0e; }")
        cat_v = QVBoxLayout(cat_page)
        cat_v.setContentsMargins(8, 8, 8, 8)
        cat_v.setSpacing(10)

        # ── Helper: styled small button ──────────
        def _mk_btn(label, obj_name=None):
            b = QPushButton(label)
            if obj_name:
                b.setObjectName(obj_name)
            b.setFixedHeight(28)
            return b

        def _mk_input(placeholder):
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setFixedHeight(28)
            return e

        # ─────────────────────────────────────────
        #  Marker-categorieën met subcategorieën
        # ─────────────────────────────────────────
        grp_marker = QGroupBox("MARKER CATEGORIEËN  (+ subcategorieën)")
        gm_v = QVBoxLayout(grp_marker)
        gm_v.setSpacing(6)

        marker_tree = QTreeWidget()
        marker_tree.setHeaderHidden(True)
        marker_tree.setMinimumHeight(180)
        gm_v.addWidget(marker_tree)

        inp_marker = _mk_input("Naam…")
        btn_add_root   = _mk_btn("+ Hoofdcat.")
        btn_add_sub    = _mk_btn("+ Subcat. van geselecteerde")
        btn_del_marker = _mk_btn("✕ Verwijder", "del_btn")
        row_m = QHBoxLayout()
        row_m.setSpacing(6)
        row_m.addWidget(inp_marker, 1)
        row_m.addWidget(btn_add_root)
        row_m.addWidget(btn_add_sub)
        row_m.addWidget(btn_del_marker)
        gm_v.addLayout(row_m)
        cat_v.addWidget(grp_marker)

        def _load_marker_cats():
            marker_tree.clear()
            cats = db.get_all_categories()
            roots = [c for c in cats if not c.get('parent_id')]
            subs  = [c for c in cats if c.get('parent_id')]
            p_items = {}
            for cat in sorted(roots, key=lambda x: x['name'].lower()):
                it = QTreeWidgetItem(marker_tree, [cat['name']])
                it.setData(0, Qt.ItemDataRole.UserRole, cat['id'])
                p_items[cat['id']] = it
            for cat in sorted(subs, key=lambda x: x['name'].lower()):
                parent_it = p_items.get(cat['parent_id'])
                if parent_it:
                    it = QTreeWidgetItem(parent_it, [f"  {cat['name']}"])
                    it.setData(0, Qt.ItemDataRole.UserRole, cat['id'])
            marker_tree.expandAll()

        def _add_root_cat():
            name = inp_marker.text().strip()
            if not name:
                return
            db.create_category(name)
            inp_marker.clear()
            _load_marker_cats()
            # Reload overlay categories in the player
            if hasattr(self, '_actors_overlay'):
                self._actors_overlay._reload_categories()
                self._actors_overlay._reposition()

        def _add_sub_cat():
            name = inp_marker.text().strip()
            sel = marker_tree.currentItem()
            if not name or not sel:
                return
            # find root item (parent_id=None parent)
            top = sel
            while top.parent():
                top = top.parent()
            parent_id = top.data(0, Qt.ItemDataRole.UserRole)
            db.create_category(name, parent_id=parent_id)
            inp_marker.clear()
            _load_marker_cats()

        def _del_marker_cat():
            sel = marker_tree.currentItem()
            if not sel:
                return
            cat_id = sel.data(0, Qt.ItemDataRole.UserRole)
            db.delete_category(cat_id)
            _load_marker_cats()
            if hasattr(self, '_actors_overlay'):
                self._actors_overlay._reload_categories()
                self._actors_overlay._reposition()

        btn_add_root.clicked.connect(_add_root_cat)
        btn_add_sub.clicked.connect(_add_sub_cat)
        btn_del_marker.clicked.connect(_del_marker_cat)

        # ─────────────────────────────────────────
        #  Film-categorieën
        # ─────────────────────────────────────────
        grp_film = QGroupBox("FILM CATEGORIEËN")
        gf_v = QVBoxLayout(grp_film)
        gf_v.setSpacing(6)

        film_cat_list = QListWidget()
        film_cat_list.setMaximumHeight(130)
        gf_v.addWidget(film_cat_list)

        inp_film_cat = _mk_input("Naam nieuwe filmcategorie…")
        btn_add_film_cat = _mk_btn("+ Toevoegen")
        btn_del_film_cat = _mk_btn("✕ Verwijder", "del_btn")
        row_f = QHBoxLayout()
        row_f.setSpacing(6)
        row_f.addWidget(inp_film_cat, 1)
        row_f.addWidget(btn_add_film_cat)
        row_f.addWidget(btn_del_film_cat)
        gf_v.addLayout(row_f)
        cat_v.addWidget(grp_film)

        def _load_film_cats():
            film_cat_list.clear()
            for fc in db.get_film_categorie_types():
                it = QListWidgetItem(fc['naam'])
                it.setData(Qt.ItemDataRole.UserRole, fc['id'])
                film_cat_list.addItem(it)

        def _add_film_cat():
            name = inp_film_cat.text().strip()
            if not name:
                return
            db.create_film_categorie_type(name)
            inp_film_cat.clear()
            _load_film_cats()
            if hasattr(self, 'films_panel'):
                self.films_panel.reload_filter_bar2()

        def _del_film_cat():
            sel = film_cat_list.currentItem()
            if not sel:
                return
            db.delete_film_categorie_type(sel.data(Qt.ItemDataRole.UserRole))
            _load_film_cats()
            if hasattr(self, 'films_panel'):
                self.films_panel.reload_filter_bar2()

        btn_add_film_cat.clicked.connect(_add_film_cat)
        btn_del_film_cat.clicked.connect(_del_film_cat)

        tabs.addTab(cat_page, "Categorieën")

        # ═══════════════════════════════════════
        #  TAB 3 — Acteur-eigenschappen
        # ═══════════════════════════════════════
        from PyQt6.QtWidgets import QComboBox as _QComboBox
        trait_page = QWidget()
        trait_page.setStyleSheet("QWidget { background: #0e0e0e; }")
        trait_v = QVBoxLayout(trait_page)
        trait_v.setContentsMargins(8, 8, 8, 8)
        trait_v.setSpacing(8)

        _WEERGAVE_LABELS = {
            'beide':    'beide (sterk + zwak)',
            'positief': 'alleen positief (sterk)',
            'negatief': 'alleen negatief (zwak)',
        }

        grp_traits = QGroupBox("ACTEUR EIGENSCHAPPEN")
        gt_v = QVBoxLayout(grp_traits)
        gt_v.setSpacing(6)

        traits_list = QListWidget()
        gt_v.addWidget(traits_list)

        # Invoer-rij: naam + weergave-dropdown + knoppen
        inp_trait   = _mk_input("Naam nieuwe eigenschap…")
        cmb_weergave = _QComboBox()
        cmb_weergave.setFixedHeight(28)
        for val, lbl in _WEERGAVE_LABELS.items():
            cmb_weergave.addItem(lbl, val)
        cmb_weergave.setCurrentIndex(0)   # 'beide' is standaard
        btn_add_trait = _mk_btn("+ Toevoegen")
        btn_del_trait = _mk_btn("✕ Verwijder", "del_btn")

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        add_row.addWidget(inp_trait, 1)
        add_row.addWidget(cmb_weergave)
        add_row.addWidget(btn_add_trait)
        add_row.addWidget(btn_del_trait)
        gt_v.addLayout(add_row)

        # Hint
        lbl_hint = QLabel(
            "Weergave bepaalt in welke sectie de eigenschap verschijnt bij acteurs  "
            "(sterke kanten / zwakke kanten / beide)"
        )
        lbl_hint.setStyleSheet("color:#333; font-size:10px;")
        lbl_hint.setWordWrap(True)
        gt_v.addWidget(lbl_hint)

        trait_v.addWidget(grp_traits)

        def _load_traits():
            traits_list.clear()
            for tt in db.get_actor_trait_types():
                weergave = tt.get('type', 'beide')
                lbl = _WEERGAVE_LABELS.get(weergave, weergave)
                it = QListWidgetItem(f"{tt['naam']}  —  {lbl}")
                it.setData(Qt.ItemDataRole.UserRole, tt['id'])
                traits_list.addItem(it)

        def _add_trait():
            name = inp_trait.text().strip()
            if not name:
                return
            weergave = cmb_weergave.currentData()
            db.create_actor_trait_type(name, weergave)
            inp_trait.clear()
            _load_traits()

        def _del_trait():
            sel = traits_list.currentItem()
            if not sel:
                return
            db.delete_actor_trait_type(sel.data(Qt.ItemDataRole.UserRole))
            _load_traits()

        btn_add_trait.clicked.connect(_add_trait)
        btn_del_trait.clicked.connect(_del_trait)

        tabs.addTab(trait_page, "Acteur-eigenschappen")

        # ── Initial data load ────────────────────
        _load_marker_cats()
        _load_film_cats()
        _load_traits()

        # ── Close button ─────────────────────────
        btn_close = QPushButton("Sluiten")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(dlg.accept)
        bh = QHBoxLayout()
        bh.addStretch()
        bh.addWidget(btn_close)
        bh.addStretch()
        v.addLayout(bh)

        dlg.exec()

    # ── Cleanup ───────────────────────────────

    def closeEvent(self, event):
        self.player.terminate()
        event.accept()


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

def main():
    # Migratie van oude locaties naar TE_KOPIEREN/ (eenmalig, stil op de achtergrond)
    migrate_legacy_data()
    ensure_data_dirs()

    app = QApplication(sys.argv)
    app.setApplicationName("CineMarker")

    # Check dependencies
    missing = []
    try:
        import mpv
    except ImportError:
        missing.append("python-mpv  (pip install python-mpv)")

    import shutil
    for tool in ['mpv', 'ffmpeg']:
        if shutil.which(tool) is None:
            missing.append(f"{tool}  (installeer via package manager)")

    if missing:
        msg = QMessageBox()
        msg.setWindowTitle("Ontbrekende afhankelijkheden")
        msg.setText("De volgende software is vereist:\n\n" + "\n".join(f"• {m}" for m in missing))
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.exec()
        sys.exit(1)

    window = CineMarker()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
