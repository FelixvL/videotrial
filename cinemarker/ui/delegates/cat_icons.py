"""CineMarker — categorie-iconen (uit de DB) als gecachte pixmaps/QIcons."""

from __future__ import annotations

import os

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap

from ...core import database as db


class CategoryIcons:
    """Lookup van marker-categorieën: naam, parent en icoon per id. `reload()` na taxonomy_changed."""

    def __init__(self) -> None:
        self._cats: dict[int, dict] = {}
        self._pix: dict[tuple[int, int], QPixmap | None] = {}
        self.reload()

    def reload(self) -> None:
        self._cats = {c["id"]: c for c in db.get_all_categories()}
        self._pix.clear()

    def all(self) -> list[dict]:
        return sorted(self._cats.values(), key=lambda c: c["name"].lower())

    def get(self, cat_id: int) -> dict | None:
        return self._cats.get(cat_id)

    def name(self, cat_id: int) -> str:
        c = self._cats.get(cat_id)
        return c["name"] if c else ""

    def pixmap(self, cat_id: int, size: int) -> QPixmap | None:
        key = (cat_id, size)
        if key not in self._pix:
            pm = None
            c = self._cats.get(cat_id)
            p = (c or {}).get("icon_path") or ""
            if p and os.path.exists(p):
                raw = QPixmap(p)
                if not raw.isNull():
                    pm = raw.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            self._pix[key] = pm
        return self._pix[key]

    def icon(self, cat_id: int, size: int) -> QIcon | None:
        pm = self.pixmap(cat_id, size)
        return QIcon(pm) if pm is not None else None

    @staticmethod
    def icon_size(size: int) -> QSize:
        return QSize(size, size)
