"""
CineMarker — geschaalde pixmaps met geheugen- (LRU) en schijfcache.

Alle delegates gebruiken dezelfde cache. De schijfsleutel bevat een korte hash
van het volledige pad, zodat gelijknamige bestanden in andere mappen niet
botsen, en de mtime zodat een vervangen bron automatisch opnieuw geschaald wordt.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

_log = logging.getLogger("cinemarker.pixcache")

_MEM_LIMIT = 900
_MEM_BYTES_LIMIT = 400 * 1024 * 1024      # grote zoomniveaus leveren pixmaps van megabytes op


def center_crop(raw: QPixmap, w: int, h: int) -> QPixmap:
    sc = raw.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
    ox = (sc.width() - w) // 2
    oy = (sc.height() - h) // 2
    return sc.copy(ox, oy, w, h)


def fit_inside(raw: QPixmap, w: int, h: int) -> QPixmap:
    return raw.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


def disk_cache_path(source: str, w: int, h: int, cache_dir: Path, mode: str = "crop") -> Path:
    try:
        mtime = int(os.path.getmtime(source))
    except OSError:
        mtime = 0
    digest = hashlib.sha1(source.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    stem = Path(source).stem[:60]
    return cache_dir / f"{stem}_{digest}_{mtime}_{w}x{h}{'' if mode == 'crop' else '_fit'}.jpg"


class PixmapCache:
    """get(path, w, h) → QPixmap of None. `mode` = 'crop' (vullen) of 'fit' (letterbox)."""

    def __init__(self, cache_dir: Path | None, *, mode: str = "crop", write_disk: bool = True) -> None:
        self._dir = cache_dir
        self._mode = mode
        self._write_disk = write_disk and cache_dir is not None
        self._mem: OrderedDict[tuple, QPixmap | None] = OrderedDict()
        self._bytes = 0

    def get(self, path: str, w: int, h: int) -> QPixmap | None:
        if not path:
            return None
        key = (path, w, h)
        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]
        pm = self._load(path, w, h)
        self._mem[key] = pm
        self._bytes += self._size_of(pm)
        while self._mem and (len(self._mem) > _MEM_LIMIT or self._bytes > _MEM_BYTES_LIMIT):
            _, old = self._mem.popitem(last=False)
            self._bytes -= self._size_of(old)
        return pm

    @staticmethod
    def _size_of(pm: QPixmap | None) -> int:
        return 0 if pm is None else pm.width() * pm.height() * 4

    def _load(self, path: str, w: int, h: int) -> QPixmap | None:
        if not os.path.exists(path):
            return None
        cp = disk_cache_path(path, w, h, self._dir, self._mode) if self._dir else None
        if cp is not None and cp.exists():
            pm = QPixmap(str(cp))
            if not pm.isNull():
                return pm
        raw = QPixmap(path)
        if raw.isNull():
            return None
        pm = center_crop(raw, w, h) if self._mode == "crop" else fit_inside(raw, w, h)
        if cp is not None and self._write_disk:
            try:
                cp.parent.mkdir(parents=True, exist_ok=True)
                pm.save(str(cp), "JPEG", quality=86)
            except OSError as e:
                _log.debug("schijfcache niet geschreven %s: %s", cp, e)
        return pm

    def invalidate(self, path: str | None = None) -> None:
        if path is None:
            self._mem.clear()
            self._bytes = 0
            return
        for key in [k for k in self._mem if k[0] == path]:
            self._bytes -= self._size_of(self._mem.pop(key))
