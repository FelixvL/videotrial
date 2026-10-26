"""
CineMarker — ontwerptokens: de enige plek met kleuren, maten en fonts.

Alle panelen, widgets en delegates lezen hieruit; nergens anders staan
hex-kleuren of pixelmaten hardcoded.
"""

from __future__ import annotations

# ── Kleuren ─────────────────────────────────────────────────────────────────
BG = "#121417"            # venster
SURFACE = "#1a1d22"       # werkbalken, lijsten, kaarten
SURFACE2 = "#22262c"      # knoppen, invoervelden
HOVER = "#2a2f36"
PRESSED = "#323841"
BORDER = "#2e333a"
BORDER_SOFT = "#262a30"
VIDEO_BG = "#000000"

TEXT = "#e8eaed"
TEXT_MUTED = "#9aa0a8"
TEXT_DIM = "#6b7280"      # nooit donkerder voor leesbare tekst

ACCENT = "#f0b454"        # amber — identiteit van CineMarker
ACCENT_HOVER = "#f6c67a"
ACCENT_BG = "#2d2515"     # subtiele amber-achtergrond (actief/geselecteerd)
ACCENT_TEXT_ON = "#1a1200"  # tekst op een amber vlak

BLUE = "#5aa9e6"          # markers
BLUE_BG = "#16283a"
TEAL = "#3fb8b0"          # filters
TEAL_BG = "#12302e"
RED = "#e5534b"           # gevaar / negatief
RED_BG = "#3a1a18"
GREEN = "#57c26b"         # ok / positief
GREEN_BG = "#163022"
PURPLE = "#b07fe0"        # markertype-filter
PURPLE_BG = "#2a1f3a"
ORANGE = "#e8853a"        # archiveren / neg-zone afgespeeld

# Kaart-overlays (delegates)
OVERLAY_DARK = (0, 0, 0, 170)
OVERLAY_DIM = (0, 0, 0, 110)
SELECTION_TINT = (240, 180, 84, 48)

# Medaille-randen op acteurkaarten (rating 9/8/7; 6 en 5 gestippeld)
GOLD = "#ffd700"
SILVER = "#c0c0c0"
BRONZE = "#cd7f32"
YELLOW = "#ffff00"
WHITE = "#ffffff"

# ── Maten ───────────────────────────────────────────────────────────────────
RADIUS = 6
RADIUS_SM = 4
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16

TOOLBAR_H = 44
FILTERBAR_MIN_H = 36
BUTTON_H = 32
BUTTON_SM_H = 26
ICON_BTN = 32
ICON_SIZE = 18
ICON_SIZE_SM = 14
INPUT_H = 32
TAB_H = 40
CHIP_H = 26
SPLITTER_W = 4

# ── Fonts ───────────────────────────────────────────────────────────────────
FONT_UI = "'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif"
FONT_MONO = "Consolas, 'Cascadia Mono', 'SF Mono', Menlo, monospace"
FONT_SIZE = 13
FONT_SIZE_SM = 12
FONT_SIZE_XS = 11       # minimum voor hints
FONT_SIZE_LG = 15
FONT_SIZE_TITLE = 14
