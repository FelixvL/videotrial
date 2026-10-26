"""CineMarker — Instellingen: sneltoetsen per groep bewerken, resetten, acteurs herladen."""

from __future__ import annotations

from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QWidget

from ...core import settings
from ..actions import DEFAULTS, LABELS, SHORTCUT_GROUPS, ActionRegistry
from ..signals import AppSignals
from ..theme import tokens as T
from ..widgets.buttons import ToolButton
from ..widgets.dialogs import BaseDialog


class SettingsDialog(BaseDialog):
    def __init__(self, parent: QWidget | None, registry: ActionRegistry, signals: AppSignals) -> None:
        super().__init__("Instellingen", parent, min_width=560)
        self.registry = registry
        self.signals = signals
        self._edits: dict[str, QLineEdit] = {}

        top = QHBoxLayout()
        btn_reload = ToolButton("Acteurs en foto's herladen", "refresh")
        btn_reload.clicked.connect(self.signals.actors_changed)
        top.addWidget(btn_reload)
        top.addStretch(1)
        self.body.addLayout(top)

        hint = QLabel("Sneltoetsen: meerdere toetsen per actie scheiden met een komma (bijv. M,K). "
                      "Leeg = uitgeschakeld. Wijzigingen gelden direct, ook in volledig scherm.")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        self.body.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(420)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(T.SPACE_SM, T.SPACE_SM, T.SPACE_SM, T.SPACE_SM)
        grid.setHorizontalSpacing(T.SPACE_MD)
        grid.setVerticalSpacing(T.SPACE_XS)
        grid.setColumnStretch(1, 1)
        row = 0
        for group, keys in SHORTCUT_GROUPS:
            hdr = QLabel(group.upper())
            hdr.setProperty("role", "section")
            hdr.setContentsMargins(0, T.SPACE_SM, 0, T.SPACE_XS)
            grid.addWidget(hdr, row, 0, 1, 2)
            row += 1
            for key in keys:
                edit = QLineEdit(settings.get_shortcut(key, DEFAULTS[key]))
                edit.setProperty("role", "mono")
                edit.setFixedWidth(150)
                edit.setPlaceholderText(DEFAULTS[key])
                edit.setToolTip(f"Standaard: {DEFAULTS[key]}")
                edit.editingFinished.connect(lambda k=key, e=edit: self._save(k, e))
                lbl = QLabel(LABELS[key])
                lbl.setProperty("role", "muted")
                grid.addWidget(edit, row, 0)
                grid.addWidget(lbl, row, 1)
                self._edits[key] = edit
                row += 1
        scroll.setWidget(inner)
        self.body.addWidget(scroll, 1)

        btn_reset = ToolButton("Alles resetten", "rotate-ccw")
        btn_reset.clicked.connect(self._reset_all)
        self.add_buttons("Sluiten", None, ok_variant="", extra_left=[btn_reset])

    def _save(self, key: str, edit: QLineEdit) -> None:
        settings.set_shortcut(key, edit.text())
        self.registry.reload()

    def _reset_all(self) -> None:
        for key, edit in self._edits.items():
            edit.blockSignals(True)
            edit.setText(DEFAULTS[key])
            edit.blockSignals(False)
            settings.set_shortcut(key, DEFAULTS[key])
        self.registry.reload()
        self.signals.say("Sneltoetsen teruggezet naar standaard")
