"""
CineMarker — applicatie-opstart.

Volgorde: logging → migraties en mappen → database-init → QApplication + thema
→ afhankelijkheden controleren → hoofdvenster met panelen.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from .core import database as db, paths, settings
from .ui.actions import ActionRegistry
from .ui.main_window import MainWindow
from .ui.signals import AppSignals
from .ui.theme import tokens as T
from .ui.theme.stylesheet import build_stylesheet

_log = logging.getLogger("cinemarker")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _migrate_photo_folder() -> None:
    """Oude 'photo_folder'-setting naar TE_KOPIEREN/acteurfotos als die wees is."""
    current = settings.get_str(settings.PHOTO_FOLDER, "")
    target = str(paths.ACTEURFOTOS_DIR)
    legacy = paths.APP_ROOT / "acteurfotos"
    if not current or not Path(current).is_dir():
        settings.set_str(settings.PHOTO_FOLDER, target)
    elif Path(current).resolve() == legacy.resolve() and paths.ACTEURFOTOS_DIR.is_dir():
        settings.set_str(settings.PHOTO_FOLDER, target)


def _check_dependencies(app: QApplication) -> bool:
    missing: list[str] = []
    try:
        import mpv  # noqa: F401
    except (ImportError, OSError):
        if sys.platform == "win32":
            missing.append("libmpv-2.dll  (plaats in C:\\mpv\\ of naast main.py; python-mpv: pip install python-mpv)")
        else:
            missing.append("libmpv  (sudo apt install libmpv-dev) en python-mpv (pip install python-mpv)")
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            missing.append(f"{tool}  (installeer FFmpeg en zet het in PATH)")
    try:
        from PyQt6 import QtSvg  # noqa: F401
    except ImportError:
        missing.append("PyQt6.QtSvg  (pip install --upgrade PyQt6)")
    if missing:
        box = QMessageBox()
        box.setWindowTitle("Ontbrekende afhankelijkheden")
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText("De volgende software is vereist:\n\n" + "\n".join(f"•  {m}" for m in missing))
        box.exec()
        return False
    return True


def _build_panels(win: MainWindow, signals: AppSignals, actions: ActionRegistry) -> None:
    """Panelen worden hier geregistreerd zodra ze bestaan (zie bouwvolgorde)."""
    from .ui.panels import registry
    registry.build_all(win, signals, actions)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    paths.migrate_legacy_data()
    paths.ensure_data_dirs()
    db.init_db()
    db.remap_thumbnail_paths_to_current_dir()
    _migrate_photo_folder()

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("CineMarker")
    app.setOrganizationName("CineMarker")
    font = QFont("Segoe UI")
    font.setPointSizeF(T.FONT_SIZE * 72 / 96)      # 13 px bij 96 dpi; puntgrootte houdt Qt-widgets tevreden
    app.setFont(font)
    app.setStyleSheet(build_stylesheet())

    if not _check_dependencies(app):
        return 1

    signals = AppSignals()
    actions = ActionRegistry()
    win = MainWindow(signals, actions)
    _build_panels(win, signals, actions)
    if not win.isVisible():
        win.show()
    return app.exec()
