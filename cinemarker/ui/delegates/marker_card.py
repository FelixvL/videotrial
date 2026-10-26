"""
CineMarker — MarkerCardDelegate: markerframe met categorie-iconen linksboven,
tijd + acteurs in de balk onderaan, sterren rechts en een duidelijke ✕ rechtsboven.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from ...core.formats import fmt_hms
from ...core.models import MarkerEntry
from ..theme import icons, tokens as T
from ..widgets.thumb_grid import CELL_ROLE, DATA_ROLE
from .cat_icons import CategoryIcons
from .pixcache import PixmapCache

BTN = 20
BAR_H = 18
STAR = 10


class MarkerCardDelegate(QStyledItemDelegate):
    remove_requested = pyqtSignal(object)      # MarkerEntry

    def __init__(self, cat_icons: CategoryIcons, parent=None, *, show_names: bool = True,
                 show_remove: bool = True, remove_tooltip: str = "Verwijderen") -> None:
        super().__init__(parent)
        self.cat_icons = cat_icons
        self.frames = PixmapCache(None, write_disk=False)
        self.show_names = show_names
        self.show_remove = show_remove
        self.remove_tooltip = remove_tooltip

    def invalidate(self, path: str | None = None) -> None:
        self.frames.invalidate(path)

    @staticmethod
    def _btn_rect(r: QRect) -> QRect:
        return QRect(r.right() - BTN - 3, r.y() + 3, BTN, BTN)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        entry: MarkerEntry | None = index.data(DATA_ROLE)
        if entry is None:
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

        pm = self.frames.get(entry.cache_path, r.width(), r.height())
        if pm is not None:
            painter.drawPixmap(r.x(), r.y(), pm)
        else:
            painter.fillRect(r, QColor(T.SURFACE))
            sz = max(18, min(40, r.height() // 3))
            painter.drawPixmap(r.center().x() - sz // 2, r.center().y() - sz // 2 - BAR_H // 2,
                               icons.pixmap("bookmark", sz, T.TEXT_DIM))

        # categorie-iconen
        cat_sz = max(14, min(22, r.height() // 4))
        cx, cy = r.x() + 4, r.y() + 4
        for cid in entry.marker.categories:
            cp = self.cat_icons.pixmap(cid, cat_sz)
            if cp is None:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*T.OVERLAY_DARK))
            painter.drawRoundedRect(QRect(cx - 2, cy - 2, cat_sz + 4, cat_sz + 4), 3, 3)
            painter.drawPixmap(cx, cy, cp)
            cx += cat_sz + 5

        # balk onderaan
        bar = QRect(r.x(), r.bottom() - BAR_H + 1, r.width(), BAR_H)
        painter.fillRect(bar, QColor(*T.OVERLAY_DARK))
        f = QFont(base_font)
        f.setPixelSize(T.FONT_SIZE_XS)
        painter.setFont(f)
        fm = painter.fontMetrics()
        right = bar.right() - 4
        if entry.marker.stars > 0:
            for _ in range(entry.marker.stars):
                right -= STAR + 1
                painter.drawPixmap(right, bar.y() + (BAR_H - STAR) // 2, icons.pixmap("star-filled", STAR, T.ACCENT))
            right -= 4
        text = fmt_hms(entry.time)
        if self.show_names and entry.actor_names:
            text += "  •  " + ", ".join(entry.actor_names)
        painter.setPen(QColor(T.TEXT))
        painter.drawText(QRect(bar.x() + 5, bar.y(), right - bar.x() - 5, BAR_H), Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(text, Qt.TextElideMode.ElideRight, right - bar.x() - 5))

        if selected:
            painter.fillRect(r, QColor(*T.SELECTION_TINT))
            painter.setPen(QPen(QColor(T.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(1, 1, -1, -1))

        if self.show_remove:
            br = self._btn_rect(r)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T.RED) if hovered else QColor(*T.OVERLAY_DARK))
            painter.drawRoundedRect(br, 4, 4)
            painter.drawPixmap(br.x() + 4, br.y() + 4, icons.pixmap("x", BTN - 8, T.WHITE if hovered else T.TEXT_MUTED))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if self.show_remove and event.type() == QEvent.Type.MouseButtonRelease \
                and event.button() == Qt.MouseButton.LeftButton:
            if self._btn_rect(option.rect).contains(event.position().toPoint()):
                entry = index.data(DATA_ROLE)
                if entry is not None:
                    self.remove_requested.emit(entry)
                return True
        return False

    def helpEvent(self, event, view, option, index) -> bool:  # noqa: N802
        if self.show_remove and self._btn_rect(option.rect).contains(event.pos()):
            from PyQt6.QtWidgets import QToolTip
            QToolTip.showText(event.globalPos(), self.remove_tooltip, view)
            return True
        return super().helpEvent(event, view, option, index)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        cell = index.data(CELL_ROLE)
        return QSize(*cell) if cell else QSize(128, 72)
