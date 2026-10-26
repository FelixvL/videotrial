"""CineMarker — dialogen in het thema: bevestigen, tekst vragen, basisdialoog."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
                             QVBoxLayout, QWidget)

from ..theme import tokens as T
from .buttons import ToolButton


def confirm(parent: QWidget | None, title: str, text: str, *, ok_text: str = "Verwijderen",
            cancel_text: str = "Annuleren", danger: bool = True) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    ok.setProperty("variant", "danger" if danger else "accent")
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def info(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def warning(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def ask_text(parent: QWidget | None, title: str, label: str, default: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, default)
    if not ok:
        return None
    return text.strip()


class BaseDialog(QDialog):
    """QDialog met titel, inhoudsgebied en een knoppenrij (OK rechts, annuleren links ervan)."""

    def __init__(self, title: str, parent: QWidget | None = None, *, min_width: int = 420) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(min_width)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(T.SPACE_LG, T.SPACE_LG, T.SPACE_LG, T.SPACE_MD)
        self._root.setSpacing(T.SPACE_MD)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "title")
        self._root.addWidget(self.title_label)
        self.body = QVBoxLayout()
        self.body.setSpacing(T.SPACE_SM)
        self._root.addLayout(self.body, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(T.SPACE_SM)
        self._root.addLayout(self.buttons)
        self.ok_button: ToolButton | None = None
        self.cancel_button: ToolButton | None = None

    def add_buttons(self, ok_text: str = "Opslaan", cancel_text: str | None = "Annuleren",
                    *, ok_variant: str = "accent", ok_icon: str | None = None,
                    extra_left: list[QWidget] | None = None) -> None:
        for w in extra_left or []:
            self.buttons.addWidget(w)
        self.buttons.addStretch(1)
        if cancel_text:
            self.cancel_button = ToolButton(cancel_text)
            self.cancel_button.clicked.connect(self.reject)
            self.buttons.addWidget(self.cancel_button)
        self.ok_button = ToolButton(ok_text, ok_icon, variant=ok_variant,
                                    color=T.ACCENT_TEXT_ON if ok_variant == "accent" else None)
        self.ok_button.clicked.connect(self.accept)
        self.ok_button.setDefault(True)
        self.buttons.addWidget(self.ok_button)
