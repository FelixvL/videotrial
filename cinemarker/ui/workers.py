"""
CineMarker — QThread-workers (gedeeld door panelen).

De eigenlijke logica (ffprobe/ffmpeg-commando's, bestandsnamen) staat in
core.media; hier zit alleen de thread-, stop- en signaalafhandeling.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QThread, pyqtSignal

from ..core import media

_log = logging.getLogger("cinemarker.workers")


class DurationWorker(QThread):
    """ffprobe voor films zonder bekende duur. tasks: [(film_id | None, path)]."""

    duration_ready = pyqtSignal(object, str, float)     # film_id (of None), path, seconden

    def __init__(self, tasks: list[tuple[int | None, str]], parent=None) -> None:
        super().__init__(parent)
        self._tasks = list(tasks)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        for film_id, path in self._tasks:
            if self._stop:
                return
            dur = media.ffprobe_duration(path)
            if dur > 0:
                self.duration_ready.emit(film_id, path, dur)


class FrameExtractWorker(QThread):
    """ffmpeg-frames voor markers. tasks: [(row_key, film_path, time_sec, cache_path)].

    `stop()` zet de vlag én killt het lopende ffmpeg-proces, zodat de thread
    direct stopt en niet pas na de 30-secondentimeout.
    """

    frame_ready = pyqtSignal(object, str)      # row_key, cache_path

    def __init__(self, tasks: list[tuple[object, str, float, str]], parent=None) -> None:
        super().__init__(parent)
        self._tasks = list(tasks)
        self._stop = False
        self._proc: list = []

    def stop(self) -> None:
        self._stop = True
        for p in list(self._proc):
            try:
                p.kill()
            except OSError:
                pass

    def run(self) -> None:
        for key, film_path, time_sec, cache_path in self._tasks:
            if self._stop:
                return
            if not os.path.exists(cache_path):
                ok = media.run_ffmpeg(media.frame_extract_cmd(film_path, time_sec, cache_path),
                                      proc_holder=self._proc)
                if self._stop:
                    return
                if not ok or not os.path.exists(cache_path):
                    media.run_ffmpeg(media.frame_extract_cmd(film_path, time_sec, cache_path, scaled=False),
                                     proc_holder=self._proc)
                if self._stop:
                    return
            if os.path.exists(cache_path):
                self.frame_ready.emit(key, cache_path)


class ThumbnailExportWorker(QThread):
    """ffmpeg-export van één ruw frame (toets T)."""

    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, video: str, time_sec: float, dest: str, parent=None) -> None:
        super().__init__(parent)
        self._video, self._time, self._dest = video, time_sec, dest
        self._proc: list = []

    def stop(self) -> None:
        for p in list(self._proc):
            try:
                p.kill()
            except OSError:
                pass

    def run(self) -> None:
        ok = media.run_ffmpeg(media.thumbnail_export_cmd(self._video, self._time, self._dest),
                              timeout=60, proc_holder=self._proc)
        if ok and os.path.exists(self._dest):
            self.finished_ok.emit(self._dest)
        else:
            self.failed.emit("ffmpeg kon het frame niet exporteren")


def stop_worker(worker: QThread | None, wait_ms: int = 500) -> None:
    """Veilig stoppen: stop-vlag, wachten, anders terminate."""
    if worker is None:
        return
    if worker.isRunning():
        if hasattr(worker, "stop"):
            worker.stop()
        if not worker.wait(wait_ms):
            _log.warning("Worker %s reageert niet; terminate", type(worker).__name__)
            worker.terminate()
            worker.wait(200)


class ActorScanWorker(QThread):
    """Scant de fotomap en bouwt ActorCards (incl. film-/markertellingen)."""

    scan_ready = pyqtSignal(list)

    def __init__(self, photo_folder: str, film_folder: str, parent=None) -> None:
        super().__init__(parent)
        self._photo_folder = photo_folder
        self._film_folder = film_folder
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..core.actors import scan_actor_photos
        try:
            cards = scan_actor_photos(self._photo_folder, self._film_folder, stop=lambda: self._stop)
        except Exception:  # noqa: BLE001 — een scanfout mag de app niet laten crashen
            _log.exception("Acteurscan mislukt")
            cards = []
        if not self._stop:
            self.scan_ready.emit(cards)
