"""
CineMarker — Naam splitsen: acteurs zonder voornaam krijgen voornaam + achternaam
door in het invoerveld de cursor op de scheidingspositie te zetten en Enter te drukken.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from ....core import database as db
from ....core.actors import actors_without_first_name, split_name
from ....core.media import PHOTO_EXTS
from ...signals import AppSignals
from ...theme import tokens as T
from ...theme.stylesheet import repolish
from ...widgets.buttons import ToolButton
from ...widgets.flow_layout import FlowLayout
from ...widgets.toolbar import PanelToolbar

PHOTO_W, PHOTO_H = 56, 76


class SplitCard(QFrame):
    split_done = pyqtSignal(int, str, str)     # actor_id, voornaam, achternaam

    def __init__(self, actor: dict, photo_folder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.actor_id = actor["id"]
        self.stem = actor.get("name") or ""
        self.setProperty("role", "card")
        self.setFixedWidth(260)

        h = QHBoxLayout(self)
        h.setContentsMargins(T.SPACE_SM, T.SPACE_SM, T.SPACE_SM, T.SPACE_SM)
        h.setSpacing(T.SPACE_SM)

        photo = QLabel()
        photo.setFixedSize(PHOTO_W, PHOTO_H)
        photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        photo.setStyleSheet(f"background:{T.BG}; border-radius:4px; border:none;")
        if photo_folder:
            for ext in sorted(PHOTO_EXTS):
                fp = Path(photo_folder) / f"{self.stem}{ext}"
                if fp.exists():
                    pm = QPixmap(str(fp))
                    if not pm.isNull():
                        photo.setPixmap(pm.scaled(PHOTO_W, PHOTO_H, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
                    break
        h.addWidget(photo)

        v = QVBoxLayout()
        v.setSpacing(T.SPACE_XS)
        lbl = QLabel(self.stem)
        lbl.setProperty("role", "muted")
        lbl.setWordWrap(True)
        self.input = QLineEdit(self.stem)
        self.input.setProperty("role", "compact")
        self.input.setToolTip("Zet de cursor tussen voor- en achternaam en druk Enter")
        self.input.returnPressed.connect(self._on_enter)
        hint = QLabel("cursor op scheiding → Enter")
        hint.setProperty("role", "dim")
        v.addWidget(lbl)
        v.addWidget(self.input)
        v.addWidget(hint)
        v.addStretch(1)
        h.addLayout(v, 1)

    def _on_enter(self) -> None:
        result = split_name(self.input.text(), self.input.cursorPosition())
        if result is None:
            self.input.setProperty("invalid", "true")
            repolish(self.input)
            QTimer.singleShot(500, self._clear_invalid)
            return
        self.split_done.emit(self.actor_id, *result)

    def _clear_invalid(self) -> None:
        self.input.setProperty("invalid", "")
        repolish(self.input)


class SplitNamesPanel(QWidget):
    back_requested = pyqtSignal()

    def __init__(self, signals: AppSignals, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.signals = signals
        self._photo_folder = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.toolbar = PanelToolbar("Naam splitsen")
        btn_back = ToolButton("Terug", "arrow-left")
        btn_back.clicked.connect(self.back_requested)
        self.toolbar._lay.insertWidget(0, btn_back)
        self.toolbar.add_stretch()
        hint = QLabel("Klik in het veld, zet de cursor tussen voor- en achternaam, druk Enter")
        hint.setProperty("role", "muted")
        self.toolbar.add_widget(hint)
        root.addWidget(self.toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self._flow = FlowLayout(inner, margin=T.SPACE_MD, h_spacing=T.SPACE_SM, v_spacing=T.SPACE_SM)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    def refresh(self, photo_folder: str) -> None:
        self._photo_folder = photo_folder
        self._flow.clear()
        rows = actors_without_first_name()
        self.toolbar.set_count(f"{len(rows)} acteur{'s' if len(rows) != 1 else ''} zonder voornaam")
        for actor, _meta in rows:
            card = SplitCard(actor, photo_folder)
            card.split_done.connect(self._on_split)
            self._flow.addWidget(card)

    def _on_split(self, actor_id: int, voornaam: str, achternaam: str) -> None:
        db.update_actor_meta(actor_id, {"voornaam": voornaam, "achternaam": achternaam})
        self.signals.actors_changed.emit()
        self.signals.say(f"{voornaam} {achternaam} opgeslagen")
        self.refresh(self._photo_folder)
