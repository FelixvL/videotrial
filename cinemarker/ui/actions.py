"""
CineMarker — sneltoets-acties.

Eén QAction per actie uit SHORTCUT_DEFS, met WindowShortcut-context. Het
register wordt met `attach()` aan elk top-level venster gehangen, zodat
dezelfde acties (en dezelfde ingestelde toetsen) overal werken. `reload()`
leest de toetsen opnieuw uit de settings zonder herstart.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QWidget

from ..core import settings

# (action_key, Nederlands label, standaardtoetsen; komma = meerdere toetsen)
SHORTCUT_DEFS: tuple[tuple[str, str, str], ...] = (
    ("play_pause", "Afspelen / Pauzeren", "Space"),
    ("marker", "Marker plaatsen", "M"),
    ("neg_marker", "Negatieve marker", "X"),
    ("thumbnail", "Thumbnail opslaan", "T"),
    ("volgende_film", "Volgende film", "V"),
    ("marker_voor", "Volgende marker", "P"),
    ("marker_achter", "Vorige marker", "O"),
    ("skip_voor", "Vooruitspoelen", "L"),
    ("skip_achter", "Terugspoelen", "N"),
    ("sneller", "Sneller afspelen", "]"),
    ("langzamer", "Langzamer afspelen", "["),
    ("reset_speed", "Snelheid resetten", "\\"),
    ("zoom_in", "Inzoomen", "+,="),
    ("zoom_uit", "Uitzoomen", "-"),
    ("zoom_reset", "Zoom resetten", "0"),
    ("fullscreen", "Volledig scherm", "F11"),
    ("open_bestand", "Bestand openen", "Ctrl+O"),
    ("acteurs_tonen", "Acteurs overlay tonen", "Ctrl+L"),
    ("begin", "Naar begin", "Home"),
    ("einde", "Naar einde", "End"),
    ("links", "Links / terug", "Left"),
    ("rechts", "Rechts / voor", "Right"),
    ("ontsnappen", "Sluiten / annuleren", "Escape"),
)

SHORTCUT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Globaal", ("ontsnappen", "open_bestand", "fullscreen", "acteurs_tonen")),
    ("Afspelen & navigatie", ("play_pause", "links", "rechts", "skip_voor", "skip_achter", "begin", "einde")),
    ("Markers", ("marker", "neg_marker", "marker_voor", "marker_achter", "volgende_film", "thumbnail")),
    ("Zoom & snelheid", ("zoom_in", "zoom_uit", "zoom_reset", "sneller", "langzamer", "reset_speed")),
)

DEFAULTS: dict[str, str] = {key: default for key, _, default in SHORTCUT_DEFS}
LABELS: dict[str, str] = {key: label for key, label, _ in SHORTCUT_DEFS}


def parse_keys(text: str) -> list[QKeySequence]:
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if part:
            seq = QKeySequence(part)
            if not seq.isEmpty():
                out.append(seq)
    return out


class ActionRegistry(QObject):
    triggered = pyqtSignal(str)          # action key

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._actions: dict[str, QAction] = {}
        for key, label, _ in SHORTCUT_DEFS:
            act = QAction(label, self)
            act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            act.triggered.connect(lambda _checked=False, k=key: self.triggered.emit(k))
            self._actions[key] = act
        self.reload()

    def action(self, key: str) -> QAction:
        return self._actions[key]

    def reload(self) -> None:
        """Toetsen opnieuw uit de settings lezen (na wijziging in Instellingen)."""
        for key, _, default in SHORTCUT_DEFS:
            self._actions[key].setShortcuts(parse_keys(settings.get_shortcut(key, default)))

    def attach(self, window: QWidget) -> None:
        """Acties registreren op een top-level venster."""
        existing = set(window.actions())
        window.addActions([a for a in self._actions.values() if a not in existing])

    def set_enabled(self, enabled: bool) -> None:
        for a in self._actions.values():
            a.setEnabled(enabled)

    def shortcut_text(self, key: str) -> str:
        seqs = self._actions[key].shortcuts()
        return ", ".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in seqs)
