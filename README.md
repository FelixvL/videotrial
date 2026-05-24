# CineMarker — Professionele Videospeler

## Vereisten

### 1. mpv installeren

**macOS:**
```bash
brew install mpv
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt install mpv
```

**Windows:**
- Download mpv van https://mpv.io/installation/
- Pak uit en voeg toe aan PATH

---

### 2. FFmpeg installeren

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt install ffmpeg
```

**Windows:**
- Download van https://ffmpeg.org/download.html
- Pak uit en voeg toe aan PATH

---

### 3. Python packages installeren

```bash
pip install python-mpv PyQt6
```

---

## Starten

```bash
python player.py
```

---

## Bediening

| Actie | Toets |
|---|---|
| Play / Pause | Space |
| −5 seconden | ← |
| +5 seconden | → |
| −1 seconde | Shift+← |
| +1 seconde | Shift+→ |
| Vorig frame | Ctrl+← |
| Volgend frame | Ctrl+→ |
| Marker plaatsen | M |
| Thumbnail exporteren | T |
| Bestand openen | Ctrl+O |
| Begin | Home |
| Einde | End |

---

## Features

- **Videospeler** — alle codecs via mpv (H.264, H.265, AV1, ProRes, WMV, etc.)
- **Frame-accurate navigatie** — spring exact naar frames of tijdcodes
- **Markers** — plaatsen, benoemen, opslaan per videobestand (als verborgen JSON)
- **Thumbnail export** — sla exact frame op via FFmpeg
- **Converter** — wmv/avi/mov → mp4, codec en resolutie kiezen

---

## Markers worden opgeslagen als:
`.bestandsnaam_markers.json` naast het videobestand.
