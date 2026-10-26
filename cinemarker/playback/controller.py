"""
CineMarker — PlayerController: alles rond libmpv, zonder widgets.

- `attach(wid)` koppelt mpv één keer aan een native venster (nooit meer wisselen)
- 100 ms-poll → `position_changed(pos, dur)`; eerste bekende duur → `duration_known(path, dur)`
- afspelen/pauze, exacte seeks, frame-stappen (achteruit sequentieel), multi-tap L/N
- snelheid met stappenlijst; achteruit via frame-back-step (≤2×) of seek-timer (>2×)
- video-zoom/pan, screenshots (OSD uit), negatieve zones overslaan
- klikgeluid (in-memory WAV via winsound)
"""

from __future__ import annotations

import logging
import math
import os
import struct
import sys
import tempfile

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap

_log = logging.getLogger("cinemarker.mpv")

SPEED_STEPS: tuple[float, ...] = (
    -50.0, -30.0, -20.0, -15.0, -10.0, -8.0, -6.0, -5.0, -4.0, -3.0, -2.0, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25,
    0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 50.0,
)
SEEK_PLAY: tuple[float, ...] = (0, 5, 30, 300, 1800)      # spelend: 1–4 tikken
SEEK_PAUSE_FRAMES: tuple[int, ...] = (0, 1, 5)            # gepauzeerd: 1 tik = 1 frame, 2 = 5 frames
SEEK_PAUSE_SECONDS: tuple[float, ...] = (0, 0, 0, 1, 5)   # 3 tikken = 1 s, 4 = 5 s
MULTITAP_MS = 380
ZOOM_STEP = 0.25
POLL_MS = 100


def speed_label(speed: float) -> str:
    return f"{int(speed)}×" if abs(speed) == int(abs(speed)) else f"{speed}×"


# ── klikgeluid ──────────────────────────────────────────────────────────────

def _build_click_wav(freq: int = 1100, duration_ms: int = 28, sample_rate: int = 44100, volume: float = 0.18) -> bytes:
    n = int(sample_rate * duration_ms / 1000)
    fade = max(1, n // 5)
    pcm = bytearray(n * 2)
    for i in range(n):
        env = min(i, n - 1 - i, fade) / fade
        val = int(volume * env * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
        struct.pack_into("<h", pcm, i * 2, max(-32768, min(32767, val)))
    header = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<I", 16)
              + struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(pcm)))
    return header + bytes(pcm)


try:
    import winsound as _winsound
    _CLICK_WAV = _build_click_wav()
    _CLICK_FLAGS = _winsound.SND_MEMORY | _winsound.SND_ASYNC | _winsound.SND_NODEFAULT
except ImportError:
    _winsound = None
    _CLICK_WAV = b""
    _CLICK_FLAGS = 0


def play_click() -> None:
    if _winsound and _CLICK_WAV:
        try:
            _winsound.PlaySound(_CLICK_WAV, _CLICK_FLAGS)
        except RuntimeError:
            pass


# ── controller ──────────────────────────────────────────────────────────────

class PlayerController(QObject):
    position_changed = pyqtSignal(float, float)      # pos, dur (dur 0 = onbekend)
    duration_known = pyqtSignal(str, float)          # path, dur
    speed_changed = pyqtSignal(float)
    zoom_changed = pyqtSignal(float)
    paused_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        import mpv
        self.mpv = mpv.MPV(log_handler=self._on_log, loglevel="error", input_default_bindings=False,
                           hwdec="auto-safe")
        self.mpv["keep-open"] = True
        self.mpv["hr-seek"] = True
        self.mpv["cache"] = "yes"
        self.path: str = ""
        self.duration: float = 0.0
        self.speed: float = 1.0
        self.zoom: float = 0.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.skip_negative = False
        self.neg_zones: list[tuple[float, float]] = []
        self._attached = False
        self._reverse_speed = 0.0
        self._reverse_frameback = False
        self._last_paused: bool | None = None

        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._tick)
        self._poll.start()
        self._reverse = QTimer(self)
        self._reverse.setInterval(50)
        self._reverse.timeout.connect(self._reverse_tick)
        self._tap_timer = QTimer(self)
        self._tap_timer.setSingleShot(True)
        self._tap_timer.setInterval(MULTITAP_MS)
        self._tap_timer.timeout.connect(self._commit_taps)
        self._tap_count = 0
        self._tap_dir = 0

    @staticmethod
    def _on_log(level: str, component: str, message: str) -> None:
        if level in ("error", "fatal"):
            _log.error("mpv [%s] %s", component, message.rstrip())

    # ── koppeling / laden ───────────────────────────────────────────────
    def attach(self, wid: int) -> None:
        if not self._attached:
            self.mpv["wid"] = int(wid)
            self._attached = True

    def load(self, path: str, start: float | None = None) -> None:
        self.reset_zoom()
        self.path = path
        self.duration = 0.0
        self.neg_zones = []
        self._stop_reverse(silent=True)
        self.speed = 1.0
        try:
            if start is not None and start > 0:
                self.mpv.command("loadfile", path, "replace", 0, {"start": str(round(start, 3))})
            else:
                self.mpv.play(path)
            self.mpv.speed = 1.0
            self.mpv.pause = False
        except Exception as e:  # noqa: BLE001 — mpv-fouten mogen de UI niet laten crashen
            _log.error("Laden mislukt (%s): %s", path, e)
        self.speed_changed.emit(1.0)

    def stop(self) -> None:
        self._stop_reverse(silent=True)
        try:
            self.mpv.command("stop")
        except Exception:  # noqa: BLE001
            pass
        self.path = ""
        self.duration = 0.0

    def terminate(self) -> None:
        self._poll.stop()
        self._reverse.stop()
        try:
            self.mpv.terminate()
        except Exception:  # noqa: BLE001
            pass

    # ── status ──────────────────────────────────────────────────────────
    @property
    def loaded(self) -> bool:
        return bool(self.path)

    @property
    def paused(self) -> bool:
        try:
            return bool(self.mpv.pause)
        except Exception:  # noqa: BLE001
            return True

    def position(self) -> float:
        try:
            return float(self.mpv.time_pos or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    def _tick(self) -> None:
        if not self.path:
            return
        try:
            pos = self.mpv.time_pos
            dur = self.mpv.duration
            paused = bool(self.mpv.pause)
        except Exception:  # noqa: BLE001
            return
        if paused != self._last_paused:
            self._last_paused = paused
            self.paused_changed.emit(paused)
        if dur and dur > 0 and abs(dur - self.duration) > 0.001:
            self.duration = float(dur)
            self.duration_known.emit(self.path, self.duration)
        if pos is None:
            return
        pos = float(pos)
        if self.skip_negative and not paused and self.duration > 0:
            for start, end in self.neg_zones:
                if start <= pos < end:
                    self.seek(min(end, self.duration - 0.05), exact=True)
                    break
        self.position_changed.emit(pos, self.duration)

    # ── afspelen / seeken ───────────────────────────────────────────────
    def toggle_play(self) -> None:
        if not self.path:
            return
        if self.speed < 0:
            if self._reverse.isActive():
                self._reverse.stop()
            else:
                self._reverse.start()
            return
        self.mpv.pause = not self.mpv.pause

    def set_paused(self, paused: bool) -> None:
        if self.path:
            self.mpv.pause = paused

    def seek(self, seconds: float, *, exact: bool = True) -> None:
        if not self.path:
            return
        try:
            self.mpv.seek(seconds, "absolute+exact" if exact else "absolute")
        except Exception as e:  # noqa: BLE001
            _log.debug("seek mislukt: %s", e)

    def seek_relative(self, seconds: float) -> None:
        if not self.path:
            return
        try:
            self.mpv.seek(seconds, "relative+exact")
        except Exception as e:  # noqa: BLE001
            _log.debug("seek mislukt: %s", e)

    def seek_when_ready(self, target: float, attempts: int = 60) -> None:
        """Seek zodra de duur bekend is (na laden), max ~3 s proberen."""
        if self.duration > 0 or attempts <= 0:
            if self.duration > 0:
                self.seek(target)
            return
        try:
            dur = self.mpv.duration
        except Exception:  # noqa: BLE001
            dur = None
        if dur and dur > 0:
            self.seek(target)
            return
        QTimer.singleShot(50, lambda: self.seek_when_ready(target, attempts - 1))

    def step_frames(self, n: int) -> None:
        if not self.path or n == 0:
            return
        try:
            if n > 0:
                for _ in range(n):
                    self.mpv.frame_step()
            else:
                self._step_back(abs(n))
        except Exception as e:  # noqa: BLE001
            _log.debug("frame-stap mislukt: %s", e)

    def _step_back(self, remaining: int) -> None:
        if remaining <= 0 or not self.path:
            return
        try:
            self.mpv.frame_back_step()
        except Exception:  # noqa: BLE001
            return
        if remaining > 1:
            QTimer.singleShot(75, lambda: self._step_back(remaining - 1))

    def go_to_start(self) -> None:
        self.seek(0)

    def go_to_end(self) -> None:
        if self.duration:
            self.seek(max(0.0, self.duration - 0.1))

    # multi-tap L/N
    def tap_seek(self, direction: int) -> None:
        if not self.path:
            return
        if self._tap_dir != direction and self._tap_count > 0:
            self._tap_timer.stop()
            self._commit_taps()
        self._tap_dir = direction
        self._tap_count = min(self._tap_count + 1, 4)
        self._tap_timer.start()

    def _commit_taps(self) -> None:
        n, d = self._tap_count, self._tap_dir
        self._tap_count, self._tap_dir = 0, 0
        if n == 0 or not self.path:
            return
        if self.paused:
            if n <= 2:
                self.step_frames(d * SEEK_PAUSE_FRAMES[n])
            else:
                self.seek_relative(d * SEEK_PAUSE_SECONDS[n])
        else:
            self.seek_relative(d * SEEK_PLAY[n])

    # ── snelheid ────────────────────────────────────────────────────────
    def speed_up(self) -> None:
        faster = [s for s in SPEED_STEPS if s > self.speed]
        self.apply_speed(faster[0] if faster else SPEED_STEPS[-1])

    def speed_down(self) -> None:
        slower = [s for s in SPEED_STEPS if s < self.speed]
        self.apply_speed(slower[-1] if slower else SPEED_STEPS[0])

    def reset_speed(self) -> None:
        self.apply_speed(1.0)

    def apply_speed(self, speed: float) -> None:
        self.speed = speed
        if speed < 0:
            self._reverse_speed = abs(speed)
            try:
                self.mpv.speed = 1.0
                self.mpv.pause = True
            except Exception:  # noqa: BLE001
                pass
            if self._reverse_speed <= 2.0:
                self._reverse_frameback = True
                try:
                    fps = max(10.0, float(self.mpv.container_fps or 25.0))
                except Exception:  # noqa: BLE001
                    fps = 25.0
                self._reverse.setInterval(max(16, int(1000 / (fps * self._reverse_speed))))
            else:
                self._reverse_frameback = False
                self._reverse.setInterval(50)
            self._reverse.start()
        else:
            self._reverse.stop()
            self._reverse_speed = 0.0
            try:
                self.mpv.speed = speed
                if speed > 0:
                    self.mpv.pause = False
            except Exception:  # noqa: BLE001
                pass
        self.speed_changed.emit(speed)

    def _reverse_tick(self) -> None:
        if not self.path:
            return
        try:
            pos = self.mpv.time_pos
            if pos is None:
                return
            if self._reverse_frameback:
                if pos <= 0:
                    self._stop_reverse()
                    return
                self.mpv.frame_back_step()
            else:
                step = self._reverse_speed * 0.05
                if pos - step <= 0:
                    self.mpv.seek(0, "absolute+exact")
                    self._stop_reverse()
                    return
                self.mpv.seek(-step, "relative+exact")
        except Exception:  # noqa: BLE001
            pass

    def _stop_reverse(self, silent: bool = False) -> None:
        self._reverse.stop()
        self._reverse_speed = 0.0
        self._reverse_frameback = False
        self.speed = 1.0
        try:
            self.mpv.speed = 1.0
            if not silent:
                self.mpv.pause = True
        except Exception:  # noqa: BLE001
            pass
        if not silent:
            self.speed_changed.emit(1.0)

    # ── zoom / pan ──────────────────────────────────────────────────────
    def zoom_in(self) -> None:
        self.zoom += ZOOM_STEP
        self._apply_zoom_pan()

    def zoom_out(self) -> None:
        self.zoom = max(0.0, self.zoom - ZOOM_STEP)
        if self.zoom == 0.0:
            self.pan_x = self.pan_y = 0.0
        self._apply_zoom_pan()

    def reset_zoom(self) -> None:
        self.zoom = 0.0
        self.pan_x = self.pan_y = 0.0
        self._apply_zoom_pan()

    def pan_by(self, dx_frac: float, dy_frac: float) -> None:
        self.pan_x += dx_frac
        self.pan_y += dy_frac
        self._apply_zoom_pan()

    def _apply_zoom_pan(self) -> None:
        try:
            self.mpv["video-zoom"] = self.zoom
            self.mpv["video-pan-x"] = self.pan_x
            self.mpv["video-pan-y"] = self.pan_y
        except Exception:  # noqa: BLE001
            pass
        self.zoom_changed.emit(self.zoom)

    # ── screenshots ─────────────────────────────────────────────────────
    def screenshot_window(self, dest: str) -> bool:
        """Sla het beeld zoals weergegeven (incl. zoom/pan) op, zonder OSD."""
        if not self.path:
            return False
        old_osd = None
        try:
            old_osd = self.mpv.osd_level
            self.mpv.osd_level = 0
        except Exception:  # noqa: BLE001
            pass
        try:
            self.mpv.command("screenshot-to-file", dest, "window")
        except Exception as e:  # noqa: BLE001
            _log.warning("screenshot mislukt: %s", e)
            return False
        finally:
            if old_osd is not None:
                try:
                    self.mpv.osd_level = old_osd
                except Exception:  # noqa: BLE001
                    pass
        return os.path.exists(dest)

    def capture_pixmap(self) -> QPixmap | None:
        tmp = tempfile.mktemp(suffix=".jpg")
        try:
            if self.screenshot_window(tmp):
                pm = QPixmap(tmp)
                return pm if not pm.isNull() else None
            return None
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
