"""
CineMarker — Sorteren: foto's uit één map snel in submap 'p' of 'm' kopiëren.

Toetsen lopen via de actie-dispatcher van het hoofdvenster: Spatie/P = p, M = m,
←/→ = bladeren. Al gesorteerde foto's (naam al in p/ of m/) worden overgeslagen.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core import media, settings
from ..signals import AppSignals
from ..theme import icons, tokens as T
from ..widgets.buttons import ToolButton
from ..widgets.toolbar import PanelToolbar


def count_images(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for f in folder.iterdir() if media.is_photo(f))


def already_sorted(folder: Path) -> set[str]:
    names: set[str] = set()
    for sub in ("p", "m"):
        d = folder / sub
        if d.is_dir():
            names.update(f.name for f in d.iterdir() if media.is_photo(f))
    return names


class _PhotoView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._pm: QPixmap | None = None
        self.setMinimumHeight(200)

    def set_pixmap(self, pm: QPixmap | None) -> None:
        self._pm = pm if pm is not None and not pm.isNull() else None
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.VIDEO_BG))
        if self._pm is None:
            p.drawPixmap(self.width() // 2 - 24, self.height() // 2 - 24, icons.pixmap("images", 48, T.TEXT_DIM))
        else:
            scaled = self._pm.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
        p.end()


class SorterPanel(QWidget):
    def __init__(self, signals: AppSignals, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        self._folder: Path | None = None
        self._photos: list[Path] = []
        self._index = 0
        self._build_ui()
        folder = settings.get_str(settings.SORTER_FOLDER, "")
        if folder:
            self._load_folder(folder)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.toolbar = PanelToolbar("Sorteren")
        self.lbl_name = QLabel("")
        self.lbl_name.setProperty("role", "muted")
        self.toolbar.add_widget(self.lbl_name)
        self.toolbar.add_stretch()
        self.lbl_p = QLabel("p: 0")
        self.lbl_p.setStyleSheet(f"color:{T.GREEN}; font-weight:600;")
        self.lbl_m = QLabel("m: 0")
        self.lbl_m.setStyleSheet(f"color:{T.RED}; font-weight:600;")
        btn_folder = ToolButton("Kies map", "folder")
        btn_folder.clicked.connect(self._pick_folder)
        self.toolbar.add_widgets(self.lbl_p, self.lbl_m)
        self.toolbar.add_separator()
        self.toolbar.add_widget(btn_folder)
        root.addWidget(self.toolbar)

        self.view = _PhotoView()
        root.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setContentsMargins(T.SPACE_MD, T.SPACE_SM, T.SPACE_MD, T.SPACE_SM)
        row.setSpacing(T.SPACE_MD)
        self.btn_m = ToolButton("m   (M)", "thumbs-down", variant="danger", color=T.RED)
        self.btn_m.setProperty("kind", "big")
        self.btn_m.clicked.connect(lambda: self.handle("m"))
        self.btn_p = ToolButton("p   (P / Spatie)", "thumbs-up", variant="success", color=T.GREEN)
        self.btn_p.setProperty("kind", "big")
        self.btn_p.clicked.connect(lambda: self.handle("p"))
        row.addWidget(self.btn_m, 1)
        row.addWidget(self.btn_p, 1)
        root.addLayout(row)
        hint = QLabel("←  →  bladeren     P / Spatie → map p     M → map m     (kopieert; origineel blijft staan)")
        hint.setProperty("role", "dim")
        hint.setContentsMargins(T.SPACE_MD, 0, T.SPACE_MD, T.SPACE_SM)
        root.addWidget(hint)

    # ── data ────────────────────────────────────────────────────────────
    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecteer fotomap", settings.get_str(settings.SORTER_FOLDER, ""))
        if folder:
            settings.set_str(settings.SORTER_FOLDER, folder)
            self._load_folder(folder)

    def _load_folder(self, folder: str) -> None:
        self._folder = Path(folder)
        if not self._folder.is_dir():
            self._photos = []
            self._index = 0
            self._show()
            return
        done = already_sorted(self._folder)
        self._photos = sorted((f for f in self._folder.iterdir() if media.is_photo(f) and f.name not in done),
                              key=lambda f: f.name.lower())
        self._index = 0
        self._update_counts()
        self._show()

    def _show(self) -> None:
        if not self._photos:
            self.view.set_pixmap(None)
            self.toolbar.set_count("Klaar" if self._folder else "")
            self.lbl_name.setText("")
            return
        self.toolbar.set_count(f"{self._index + 1} / {len(self._photos)}")
        fp = self._photos[self._index]
        self.lbl_name.setText(fp.name)
        self.view.set_pixmap(QPixmap(str(fp)))

    def _update_counts(self) -> None:
        if self._folder is None:
            return
        self.lbl_p.setText(f"p: {count_images(self._folder / 'p')}")
        self.lbl_m.setText(f"m: {count_images(self._folder / 'm')}")

    # ── acties ──────────────────────────────────────────────────────────
    def handle(self, cmd: str) -> None:
        if cmd in ("p", "m"):
            self._copy_to(cmd)
        elif cmd == "prev" and self._photos:
            self._index = (self._index - 1) % len(self._photos)
            self._show()
        elif cmd == "next" and self._photos:
            self._index = (self._index + 1) % len(self._photos)
            self._show()

    def _copy_to(self, sub: str) -> None:
        if not self._photos or self._folder is None:
            return
        dest_dir = self._folder / sub
        try:
            dest_dir.mkdir(exist_ok=True)
            fp = self._photos[self._index]
            shutil.copy2(str(fp), str(media.unique_path(dest_dir / fp.name)))
        except OSError as e:
            self.signals.say(f"Kopiëren mislukt: {e}")
            return
        self._photos.pop(self._index)
        if self._index >= len(self._photos) and self._index > 0:
            self._index -= 1
        self._update_counts()
        self._show()
