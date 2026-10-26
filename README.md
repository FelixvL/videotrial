# CineMarker

Videospeler met markers, acteurs en filmbeheer (PyQt6 + libmpv).

## Vereisten

- Python 3.11+ met `pip install -r requirements.txt` (PyQt6, python-mpv)
- **libmpv**: Windows → `libmpv-2.dll` in `C:\mpv\` (of naast `main.py`); Linux → `sudo apt install libmpv-dev`
- **FFmpeg** (`ffmpeg` en `ffprobe`) in PATH

## Starten

```bash
python main.py
```

## Structuur

```
main.py                 entry point
cinemarker/
  app.py                opstart: migraties, database, thema, hoofdvenster
  core/                 datalaag zonder Qt: database, markers (JSON), settings, modellen, filters, media
  playback/             PlayerController (libmpv): laden, seeken, snelheid, zoom, screenshots
  ui/
    theme/              tokens (kleuren/maten), één stylesheet, SVG-iconen
    widgets/            gedeelde bouwstenen: werkbalk, filterbalk, zoom, multiselect, grid, dialogen, overlays
    delegates/          kaart-tekenaars voor films, acteurs en markers
    panels/             Speler, Films, Markers, Acteurs, Data, Beheer, Sorteren, Instellingen
    main_window.py      tabs, corner-knoppen, actie-dispatcher
    actions.py          sneltoetsen (ActionRegistry), instelbaar zonder herstart
    signals.py          AppSignals: panelen praten alleen via signalen met elkaar
tests/                  pytest voor de core-logica (gebruikt een tijdelijke datamap)
```

Alle data die je moet meenemen staat in `TE_KOPIEREN/` (database, acteurfoto's, thumbnails, iconen).
`scaled_cache/` is een wegwerpcache.

## Bediening

| Actie | Toets |
|---|---|
| Afspelen / pauzeren | Spatie |
| ±5 seconden | ← / → |
| Vooruit / terug (multi-tap: 5s, 30s, 5m, 30m; gepauzeerd: 1f, 5f, 1s, 5s) | L / N |
| Marker plaatsen | M |
| Negatieve marker (overslaan vanaf hier) | X |
| Volgende / vorige marker | P / O |
| Volgende film | V |
| Thumbnail exporteren | T |
| Sneller / langzamer / reset | ] / [ / \ |
| Zoom in / uit / reset | + / − / 0 |
| Volledig scherm | F11 |
| Acteur koppelen | Ctrl+L |
| Bestand openen | Ctrl+O |

Alle toetsen zijn instelbaar via de instellingenknop rechtsboven.

Markers worden per video opgeslagen als `.<bestandsnaam>_markers.json` naast het videobestand (UTF-8).

## Tests

```bash
python -m pytest tests -q
```
