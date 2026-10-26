"""
CineMarker — één applicatiebrede stylesheet, gegenereerd uit de tokens.

Wordt op de QApplication gezet, zodat ook dialogen en popups het thema erven.
Varianten gaan via dynamic properties, bv. `btn.setProperty("variant", "accent")`
gevolgd door `repolish(btn)`.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from . import tokens as T


def repolish(w: QWidget) -> None:
    """Pas de stylesheet opnieuw toe na het wijzigen van een dynamic property."""
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()


def set_variant(w: QWidget, variant: str | None) -> None:
    w.setProperty("variant", variant or "")
    repolish(w)


def set_tone(w: QWidget, tone: str | None) -> None:
    """Kleurfamilie voor chips/toggles: accent, blue, teal, red, green, purple."""
    w.setProperty("tone", tone or "")
    repolish(w)


def _chip_rules(selector: str) -> str:
    """Toggle-chip regels per kleurfamilie (checked-staat)."""
    tones = {
        "": (T.ACCENT, T.ACCENT_BG),
        "accent": (T.ACCENT, T.ACCENT_BG),
        "blue": (T.BLUE, T.BLUE_BG),
        "teal": (T.TEAL, T.TEAL_BG),
        "red": (T.RED, T.RED_BG),
        "green": (T.GREEN, T.GREEN_BG),
        "purple": (T.PURPLE, T.PURPLE_BG),
    }
    out = []
    for tone, (fg, bg) in tones.items():
        sel = f'{selector}[tone="{tone}"]' if tone else selector
        out.append(f"""
{sel}:checked {{ color: {fg}; background: {bg}; border-color: {fg}; }}
{sel}:checked:hover {{ background: {bg}; border-color: {fg}; color: {fg}; }}
""")
    return "".join(out)


def build_stylesheet() -> str:
    return f"""
/* ── Basis ───────────────────────────────────────────────────────────── */
QWidget {{
    background: {T.BG};
    color: {T.TEXT};
    font-family: {T.FONT_UI};
    font-size: {T.FONT_SIZE}px;
    selection-background-color: {T.ACCENT_BG};
    selection-color: {T.TEXT};
}}
QMainWindow, QDialog {{ background: {T.BG}; }}
QToolTip {{
    background: {T.SURFACE2}; color: {T.TEXT}; border: 1px solid {T.BORDER};
    padding: 4px 8px; border-radius: {T.RADIUS_SM}px;
}}
QSplitter::handle {{ background: {T.BORDER_SOFT}; }}
QSplitter::handle:horizontal {{ width: {T.SPLITTER_W}px; }}
QSplitter::handle:vertical {{ height: {T.SPLITTER_W}px; }}
QSplitter::handle:hover {{ background: {T.ACCENT}; }}
QStatusBar {{
    background: {T.SURFACE}; color: {T.TEXT_MUTED}; border-top: 1px solid {T.BORDER_SOFT};
    font-size: {T.FONT_SIZE_SM}px;
}}
QStatusBar::item {{ border: none; }}

/* ── Labels ──────────────────────────────────────────────────────────── */
QLabel {{ background: transparent; }}
QLabel[role="title"] {{
    font-size: {T.FONT_SIZE_TITLE}px; font-weight: 600; color: {T.TEXT}; letter-spacing: 1px;
}}
QLabel[role="muted"] {{ color: {T.TEXT_MUTED}; }}
QLabel[role="dim"] {{ color: {T.TEXT_DIM}; font-size: {T.FONT_SIZE_XS}px; }}
QLabel[role="section"] {{
    color: {T.TEXT_MUTED}; font-size: {T.FONT_SIZE_XS}px; font-weight: 600; letter-spacing: 2px;
}}
QLabel[role="timecode"] {{
    font-family: {T.FONT_MONO}; font-size: {T.FONT_SIZE}px; color: {T.TEXT};
}}
QLabel[role="accent"] {{ color: {T.ACCENT}; font-weight: 600; }}
QLabel[role="error"] {{ color: {T.RED}; }}

/* ── Werkbalken en frames ────────────────────────────────────────────── */
QFrame[role="toolbar"] {{
    background: {T.SURFACE}; border: none; border-bottom: 1px solid {T.BORDER_SOFT};
}}
QFrame[role="filterbar"] {{
    background: {T.BG}; border: none; border-bottom: 1px solid {T.BORDER_SOFT};
}}
QFrame[role="card"] {{
    background: {T.SURFACE}; border: 1px solid {T.BORDER_SOFT}; border-radius: {T.RADIUS}px;
}}
QFrame[role="card"]:hover {{ border-color: {T.BORDER}; }}
QFrame[role="panel"] {{
    background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: {T.RADIUS}px;
}}
QFrame[role="separator"] {{ background: {T.BORDER_SOFT}; max-height: 1px; min-height: 1px; border: none; }}

/* zwevende panelen boven de video: zo min mogelijk film verbergen */
QWidget[role="transparent"] {{ background: transparent; border: none; }}
QListWidget[role="glass"] {{ background: transparent; border: none; }}
QListWidget[role="glass"]::item {{ background: rgba(18, 20, 24, 175); border-radius: {T.RADIUS_SM}px; margin: 1px 0; }}
QListWidget[role="glass"]::item:hover {{ background: rgba(42, 47, 54, 220); }}
QListWidget[role="glass"]::item:selected {{ background: rgba(45, 37, 21, 235); color: {T.ACCENT}; }}
QFrame[role="glass"] {{ background: transparent; border: none; }}
QLabel[role="pill"] {{
    background: rgba(18, 20, 24, 175); color: {T.TEXT}; border-radius: {T.RADIUS_SM}px; padding: 2px 6px;
}}
QLabel[role="pill-muted"] {{
    background: rgba(18, 20, 24, 175); color: {T.TEXT_MUTED}; border-radius: {T.RADIUS_SM}px; padding: 2px 6px;
    font-size: {T.FONT_SIZE_XS}px;
}}
QLabel[role="pill-section"] {{
    background: rgba(18, 20, 24, 175); color: {T.TEXT_MUTED}; border-radius: {T.RADIUS_SM}px; padding: 2px 6px;
    font-size: {T.FONT_SIZE_XS}px; font-weight: 600; letter-spacing: 2px;
}}
QLabel[role="pill-accent"] {{
    background: rgba(18, 20, 24, 175); color: {T.ACCENT}; border-radius: {T.RADIUS_SM}px; padding: 2px 6px; font-weight: 600;
}}
QFrame[role="separator-glass"] {{ background: rgba(255, 255, 255, 30); max-height: 1px; min-height: 1px; border: none; }}
QLineEdit[role="glass"] {{ background: rgba(34, 38, 44, 200); }}
QFrame[role="vseparator"] {{ background: {T.BORDER}; max-width: 1px; min-width: 1px; border: none; }}

/* ── Knoppen ─────────────────────────────────────────────────────────── */
QPushButton {{
    background: {T.SURFACE2}; color: {T.TEXT}; border: 1px solid {T.BORDER};
    border-radius: {T.RADIUS}px; padding: 0 12px; min-height: {T.BUTTON_H}px; max-height: {T.BUTTON_H}px;
}}
QPushButton:hover {{ background: {T.HOVER}; border-color: {T.TEXT_DIM}; }}
QPushButton:pressed {{ background: {T.PRESSED}; }}
QPushButton:disabled {{ color: {T.TEXT_DIM}; border-color: {T.BORDER_SOFT}; background: {T.SURFACE}; }}
QPushButton:focus {{ outline: none; }}
QPushButton:checked {{ background: {T.ACCENT_BG}; border-color: {T.ACCENT}; color: {T.ACCENT}; }}

QPushButton[variant="accent"] {{
    background: {T.ACCENT}; color: {T.ACCENT_TEXT_ON}; border: 1px solid {T.ACCENT}; font-weight: 600;
}}
QPushButton[variant="accent"]:hover {{ background: {T.ACCENT_HOVER}; border-color: {T.ACCENT_HOVER}; }}
QPushButton[variant="accent"]:disabled {{ background: {T.SURFACE2}; color: {T.TEXT_DIM}; border-color: {T.BORDER_SOFT}; }}
QPushButton[variant="danger"] {{ color: {T.RED}; border-color: {T.RED_BG}; background: {T.SURFACE2}; }}
QPushButton[variant="danger"]:hover {{ background: {T.RED}; color: {T.WHITE}; border-color: {T.RED}; }}
QPushButton[variant="success"] {{ color: {T.GREEN}; border-color: {T.GREEN_BG}; }}
QPushButton[variant="success"]:hover {{ background: {T.GREEN}; color: {T.ACCENT_TEXT_ON}; border-color: {T.GREEN}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {T.TEXT_MUTED}; }}
QPushButton[variant="ghost"]:hover {{ background: {T.HOVER}; color: {T.TEXT}; }}
QPushButton[variant="ghost"]:checked {{ background: {T.ACCENT_BG}; color: {T.ACCENT}; border-color: transparent; }}
QPushButton[focused="true"] {{ border: 2px solid {T.TEAL}; color: {T.TEAL}; background: {T.TEAL_BG}; }}

/* icoonknop: vierkant, geen padding zodat het icoon nooit wordt afgekapt */
QPushButton[kind="icon"] {{
    padding: 0; min-width: {T.ICON_BTN}px; max-width: {T.ICON_BTN}px;
    min-height: {T.ICON_BTN}px; max-height: {T.ICON_BTN}px;
}}
QPushButton[kind="icon"][variant="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[kind="icon"][variant="ghost"]:hover {{ background: {T.HOVER}; }}
QPushButton[kind="icon"][variant="danger"]:hover {{ background: {T.RED}; }}

/* chip: compacte toggle in filterbalken */
QPushButton[kind="chip"] {{
    min-height: {T.CHIP_H}px; max-height: {T.CHIP_H}px; padding: 0 10px;
    font-size: {T.FONT_SIZE_SM}px; color: {T.TEXT_MUTED}; background: {T.SURFACE}; border-color: {T.BORDER_SOFT};
    border-radius: {T.CHIP_H // 2}px;
}}
QPushButton[kind="chip"]:hover {{ color: {T.TEXT}; border-color: {T.BORDER}; background: {T.HOVER}; }}
{_chip_rules('QPushButton[kind="chip"]')}

/* sorteerknop: tekst + pijl, 3-staps */
QPushButton[kind="sort"] {{
    min-height: {T.BUTTON_SM_H}px; max-height: {T.BUTTON_SM_H}px; padding: 0 8px;
    font-size: {T.FONT_SIZE_SM}px; color: {T.TEXT_MUTED}; background: transparent; border-color: transparent;
}}
QPushButton[kind="sort"]:hover {{ color: {T.TEXT}; background: {T.HOVER}; }}
QPushButton[kind="sort"]:checked {{ color: {T.ACCENT}; background: {T.ACCENT_BG}; border-color: transparent; }}

/* grote actieknop (sorteren) */
QPushButton[kind="big"] {{ min-height: 56px; max-height: 56px; font-size: {T.FONT_SIZE_LG + 3}px; font-weight: 600; }}

/* tabknop (segmented) */
QPushButton[kind="segment"] {{
    min-height: {T.BUTTON_SM_H}px; max-height: {T.BUTTON_SM_H}px; padding: 0 10px; border-radius: {T.RADIUS_SM}px;
    font-size: {T.FONT_SIZE_SM}px;
}}

QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: {T.RADIUS_SM}px; padding: 2px;
}}
QToolButton:hover {{ background: {T.HOVER}; }}
QToolButton:checked {{ background: {T.ACCENT_BG}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ── Invoer ──────────────────────────────────────────────────────────── */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
    background: {T.SURFACE2}; color: {T.TEXT}; border: 1px solid {T.BORDER};
    border-radius: {T.RADIUS}px; padding: 0 8px; min-height: {T.INPUT_H - 2}px;
    selection-background-color: {T.ACCENT}; selection-color: {T.ACCENT_TEXT_ON};
}}
QTextEdit, QPlainTextEdit {{ padding: 6px 8px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {T.ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {T.TEXT_DIM}; background: {T.SURFACE}; }}
QLineEdit[role="search"] {{ padding-left: 30px; }}
QLineEdit[role="compact"], QSpinBox[role="compact"] {{
    min-height: {T.BUTTON_SM_H - 2}px; max-height: {T.BUTTON_SM_H - 2}px; font-size: {T.FONT_SIZE_SM}px; padding: 0 6px;
}}
QLineEdit[role="mono"] {{ font-family: {T.FONT_MONO}; }}
QLineEdit[invalid="true"] {{ border-color: {T.RED}; background: {T.RED_BG}; }}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid {T.TEXT_MUTED};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {T.SURFACE2}; color: {T.TEXT}; border: 1px solid {T.BORDER};
    selection-background-color: {T.ACCENT_BG}; selection-color: {T.ACCENT}; outline: none; padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QSpinBox::up-arrow {{
    width: 0; height: 0; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {T.TEXT_MUTED};
}}
QSpinBox::down-arrow {{
    width: 0; height: 0; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {T.TEXT_MUTED};
}}
QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; color: {T.TEXT_MUTED}; }}
QCheckBox:hover, QRadioButton:hover {{ color: {T.TEXT}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px; border: 1px solid {T.BORDER}; border-radius: 3px; background: {T.SURFACE2};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {T.TEXT_DIM}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {T.ACCENT}; border-color: {T.ACCENT}; }}
QCheckBox:checked {{ color: {T.TEXT}; }}

/* ── Lijsten en tabellen ─────────────────────────────────────────────── */
QListView, QListWidget, QTreeView, QTreeWidget, QTableView, QTableWidget {{
    background: {T.SURFACE}; border: 1px solid {T.BORDER_SOFT}; border-radius: {T.RADIUS}px;
    outline: none; alternate-background-color: {T.BG};
}}
QListView[role="grid"], QListWidget[role="grid"] {{ background: {T.BG}; border: none; border-radius: 0; }}
QListWidget::item {{ padding: 6px 8px; border-radius: {T.RADIUS_SM}px; }}
QListWidget::item:hover {{ background: {T.HOVER}; }}
QListWidget::item:selected {{ background: {T.ACCENT_BG}; color: {T.ACCENT}; }}
QListWidget[role="grid"]::item {{ padding: 0; border-radius: 0; }}
QListWidget[role="grid"]::item:hover, QListWidget[role="grid"]::item:selected {{ background: transparent; }}
QTreeWidget::item, QTableWidget::item {{ padding: 4px 6px; }}
QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {T.ACCENT_BG}; color: {T.ACCENT}; }}
QTreeWidget::item:hover, QTableWidget::item:hover {{ background: {T.HOVER}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {T.SURFACE}; color: {T.TEXT_MUTED}; border: none; border-bottom: 1px solid {T.BORDER};
    border-right: 1px solid {T.BORDER_SOFT}; padding: 6px 8px; font-size: {T.FONT_SIZE_SM}px; font-weight: 600;
}}
QTableWidget {{ gridline-color: {T.BORDER_SOFT}; }}
QTableCornerButton::section {{ background: {T.SURFACE}; border: none; }}

/* ── Scrollbars ──────────────────────────────────────────────────────── */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {T.BORDER}; border-radius: 5px; min-height: 24px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {T.TEXT_DIM}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {T.BORDER}; border-radius: 5px; min-width: 24px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: {T.TEXT_DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ── Tabs ────────────────────────────────────────────────────────────── */
QTabWidget::pane {{ border: none; top: 0; }}
QTabWidget::tab-bar {{ left: 0; }}
QTabBar {{ background: {T.SURFACE}; }}
QTabBar::tab {{
    background: {T.SURFACE}; color: {T.TEXT_MUTED}; padding: 0 18px; min-height: {T.TAB_H}px;
    border: none; border-bottom: 2px solid transparent; font-weight: 600; letter-spacing: 1px;
}}
QTabBar::tab:hover {{ color: {T.TEXT}; background: {T.HOVER}; }}
QTabBar::tab:selected {{ color: {T.ACCENT}; border-bottom: 2px solid {T.ACCENT}; }}
QTabBar::tab:disabled {{ color: {T.TEXT_DIM}; }}
QTabBar::scroller {{ width: 0; }}
QWidget[role="tabbar-row"] {{ background: {T.SURFACE}; border-bottom: 1px solid {T.BORDER_SOFT}; }}

/* ── Menu's ──────────────────────────────────────────────────────────── */
QMenu {{
    background: {T.SURFACE2}; color: {T.TEXT}; border: 1px solid {T.BORDER}; border-radius: {T.RADIUS}px; padding: 6px;
}}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: {T.RADIUS_SM}px; }}
QMenu::item:selected {{ background: {T.ACCENT_BG}; color: {T.ACCENT}; }}
QMenu::item:disabled {{ color: {T.TEXT_DIM}; }}
QMenu::separator {{ height: 1px; background: {T.BORDER}; margin: 6px 4px; }}
QMenu::icon {{ padding-left: 8px; }}

/* ── Overig ──────────────────────────────────────────────────────────── */
QProgressBar {{
    background: {T.SURFACE2}; border: 1px solid {T.BORDER}; border-radius: {T.RADIUS_SM}px; text-align: center;
    color: {T.TEXT}; min-height: 16px;
}}
QProgressBar::chunk {{ background: {T.ACCENT}; border-radius: 3px; }}
QGroupBox {{
    border: 1px solid {T.BORDER_SOFT}; border-radius: {T.RADIUS}px; margin-top: 14px; padding: 10px 8px 6px 8px;
    color: {T.TEXT_MUTED}; font-weight: 600; font-size: {T.FONT_SIZE_XS}px; letter-spacing: 2px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QSlider::groove:horizontal {{ height: 4px; background: {T.SURFACE2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {T.ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {T.ACCENT}; width: 14px; height: 14px; border-radius: 7px; margin: -5px 0;
}}
QMessageBox {{ background: {T.BG}; }}
QMessageBox QLabel {{ color: {T.TEXT}; }}
QDialogButtonBox QPushButton {{ min-width: 90px; }}
"""
