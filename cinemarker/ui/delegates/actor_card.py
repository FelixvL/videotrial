"""
CineMarker — ActorCardDelegate: acteurkaart (foto, naam, sterren, decennium,
rating-rand, film-/markerbadges, "niet in database", ›-knop naar detail).
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from ...core.formats import decade_label
from ...core.models import ActorCard
from ...core.paths import SCALED_ACTOR_CARDS_DIR
from ..theme import icons, tokens as T
from ..widgets.thumb_grid import CELL_ROLE, DATA_ROLE
from .pixcache import PixmapCache

ARROW = 24
STAR = 12
BADGE_ICON = 12

# rating → (kleur, stijl, breedte)
RATING_BORDER = {
    9: (T.GOLD, Qt.PenStyle.SolidLine, 3),
    8: (T.SILVER, Qt.PenStyle.SolidLine, 3),
    7: (T.BRONZE, Qt.PenStyle.SolidLine, 3),
    6: (T.YELLOW, Qt.PenStyle.DashLine, 2),
    5: (T.WHITE, Qt.PenStyle.DashLine, 2),
}
GLOW_OFFSETS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1), (-2, 0), (2, 0), (0, -2), (0, 2)]


def _luminance(c: QColor) -> float:
    return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()


def _font(base: QFont, px: int, bold: bool = False) -> QFont:
    f = QFont(base)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


class ActorCardDelegate(QStyledItemDelegate):
    detail_requested = pyqtSignal(object)      # ActorCard

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.photos = PixmapCache(SCALED_ACTOR_CARDS_DIR, mode="fit")
        self.kleur_hex: dict[str, str] = {}    # kleur-id (str) → '#rrggbb'

    def set_kleur_map(self, mapping: dict[str, str]) -> None:
        self.kleur_hex = dict(mapping)

    def invalidate(self) -> None:
        self.photos.invalidate()

    @staticmethod
    def _arrow_rect(r: QRect) -> QRect:
        return QRect(r.right() - ARROW - 3, r.bottom() - ARROW - 3, ARROW, ARROW)

    def _glow_text(self, painter: QPainter, rect: QRect, flags, text: str, color: QColor) -> None:
        glow = QColor(255, 255, 255, 220) if _luminance(color) < 0.35 else QColor(0, 0, 0, 230)
        painter.setPen(glow)
        for dx, dy in GLOW_OFFSETS:
            painter.drawText(rect.translated(dx, dy), flags, text)
        painter.setPen(color)
        painter.drawText(rect, flags, text)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        card: ActorCard | None = index.data(DATA_ROLE)
        if card is None:
            super().paint(painter, option, index)
            return
        r: QRect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        base_font = painter.font()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(r)
        painter.fillRect(r, QColor(T.SURFACE))

        pm = self.photos.get(card.photo_path, r.width(), r.height())
        if pm is not None:
            painter.drawPixmap(r.x() + (r.width() - pm.width()) // 2, r.y() + (r.height() - pm.height()) // 2, pm)
        else:
            sz = max(24, r.width() // 4)
            painter.drawPixmap(r.center().x() - sz // 2, r.center().y() - sz // 2, icons.pixmap("user", sz, T.TEXT_DIM))

        # naam onderaan met glow, kleur uit actor_kleuren.hex
        color = QColor(self.kleur_hex.get(card.meta.kleur, T.WHITE))
        if not color.isValid():
            color = QColor(T.WHITE)
        painter.setFont(_font(base_font, T.FONT_SIZE_SM, bold=True))
        name_rect = QRect(r.x() + 3, r.bottom() - 26, r.width() - 6, 22)
        fm = painter.fontMetrics()
        name = fm.elidedText(card.display_name, Qt.TextElideMode.ElideRight, name_rect.width())
        self._glow_text(painter, name_rect, Qt.AlignmentFlag.AlignCenter, name, color)

        # sterren (grootte) rechtsboven
        if card.meta.grootte > 0:
            n = card.meta.grootte
            bw = n * (STAR + 1) + 8
            badge = QRect(r.right() - bw - 3, r.y() + 3, bw, STAR + 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*T.OVERLAY_DARK))
            painter.drawRoundedRect(badge, 4, 4)
            x = badge.x() + 4
            for _ in range(n):
                painter.drawPixmap(x, badge.y() + 4, icons.pixmap("star-filled", STAR, T.GOLD))
                x += STAR + 1

        # rating (cijfer, medaillekleur) en decennium linksboven
        painter.setFont(_font(base_font, T.FONT_SIZE_SM, bold=True))
        fm = painter.fontMetrics()
        bx = r.x() + 3
        if card.meta.rating > 0:
            txt = str(card.meta.rating)
            col = RATING_BORDER.get(card.meta.rating, (T.TEXT,))[0]
            bw = fm.horizontalAdvance(txt) + 12
            badge = QRect(bx, r.y() + 3, bw, fm.height() + 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*T.OVERLAY_DARK))
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor(col))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, txt)
            bx += bw + 3
        dec = decade_label(card.meta.decennia)
        if dec:
            bw = fm.horizontalAdvance(dec) + 10
            badge = QRect(bx, r.y() + 3, bw, fm.height() + 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*T.OVERLAY_DARK))
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor(T.TEXT))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, dec)

        # film-/markerbadges boven de naam
        painter.setFont(_font(base_font, T.FONT_SIZE_XS))
        fm = painter.fontMetrics()
        x = r.x() + 3
        by = name_rect.y() - 18
        for icon_name, count, col in (("film", card.film_count, T.ACCENT), ("bookmark", card.marker_count, T.BLUE)):
            if not count:
                continue
            txt = str(count)
            bw = BADGE_ICON + 4 + fm.horizontalAdvance(txt) + 8
            badge = QRect(x, by, bw, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*T.OVERLAY_DARK))
            painter.drawRoundedRect(badge, 4, 4)
            painter.drawPixmap(badge.x() + 4, badge.y() + 2, icons.pixmap(icon_name, BADGE_ICON, col))
            painter.setPen(QColor(col))
            painter.drawText(badge.adjusted(BADGE_ICON + 7, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, txt)
            x += bw + 3

        # rating-rand
        border = RATING_BORDER.get(card.meta.rating)
        if border:
            col, style, width = border
            painter.setPen(QPen(QColor(col), width, style))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(width // 2 + 1, width // 2 + 1, -(width // 2 + 1), -(width // 2 + 1)))

        # niet in database
        if not card.in_db:
            painter.fillRect(r, QColor(*T.OVERLAY_DIM))
            painter.setFont(_font(base_font, T.FONT_SIZE_XS))
            painter.setPen(QColor(T.TEXT_MUTED))
            painter.drawText(r.adjusted(0, 0, 0, -30), Qt.AlignmentFlag.AlignCenter, "niet in\ndatabase")

        # ›-knop
        ar = self._arrow_rect(r)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*T.OVERLAY_DARK))
        painter.drawRoundedRect(ar, 5, 5)
        painter.drawPixmap(ar.x() + 4, ar.y() + 4, icons.pixmap("chevron-right", ARROW - 8, T.ACCENT))

        if hovered and not selected:
            painter.setPen(QPen(QColor(T.TEXT_DIM), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(0, 0, -1, -1))
        if selected:
            painter.fillRect(r, QColor(*T.SELECTION_TINT))
            painter.setPen(QPen(QColor(T.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(1, 1, -1, -1))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._arrow_rect(option.rect).contains(event.position().toPoint()):
                card = index.data(DATA_ROLE)
                if card is not None:
                    self.detail_requested.emit(card)
                return True
        return False

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        cell = index.data(CELL_ROLE)
        return QSize(*cell) if cell else QSize(160, 206)
