"""
CineMarker — applicatiebrede signalen.

Panelen praten nooit rechtstreeks met elkaar: wie iets wijzigt, zendt hier een
signaal; wie het moet weten, abonneert zich. Het hoofdvenster maakt één
instantie en geeft die aan elk paneel.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class AppSignals(QObject):
    # Film-niveau
    film_thumbnail_changed = pyqtSignal(str, str)   # file_path, thumb_path
    film_rating_changed = pyqtSignal(str, float)    # file_path, afgeleide_rating
    film_actors_changed = pyqtSignal(int)           # film_id
    film_deleted = pyqtSignal(str)                  # file_path (verplaatst naar deleted/)
    films_rescanned = pyqtSignal()                  # Films-paneel heeft de map opnieuw gelezen

    # Markers
    markers_changed = pyqtSignal(str)               # file_path waarvan de markers wijzigden

    # Taxonomie en acteurs
    taxonomy_changed = pyqtSignal()                 # categorieën, filmcategorieën, traits, kleuren
    actors_changed = pyqtSignal()                   # acteurs toegevoegd/gewijzigd/verwijderd

    # Status
    status_message = pyqtSignal(str, int)           # tekst, milliseconden (0 = blijvend)

    def say(self, text: str, msec: int = 4000) -> None:
        self.status_message.emit(text, msec)
