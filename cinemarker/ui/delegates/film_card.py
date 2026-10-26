"""
CineMarker — FilmCardDelegate: tekent één filmkaart in een ThumbGrid.

Beeld (crossfade bij meerdere thumbnails of marker-modus), infobalk onderaan
(markers · negatief | duur · grootte), acteurfoto's linksonder, rating-badge
rechtsboven, naam bij hover, selectie-rand.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from ...core.formats import fmt_compact, fmt_size
from ...core.models import FilmCard
from ...core.paths import SCALED_ACTOR_GRID_DIR, SCALED_FILM_THUMBS_DIR
from ..theme import icons, tokens as T
from ..widgets.thumb_grid import CELL_ROLE, DATA_ROLE
from .pixcache import PixmapCache

ACTOR_PHOTO = 26
BAR_H = 20
BADGE_ICON = 12
THUMB_PERIOD, THUMB_FADE = 2.0, 0.2
MARKER_FADE = 0.4


def small_font(base: QFont, px: int = T.FONT_SIZE_XS, bold: bool = False) -> QFont:
    f = QFont(base)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


class FilmCardDelegate(QStyledItemDelegate):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.thumbs = PixmapCache(SCALED_FILM_THUMBS_DIR)
        self.actors = PixmapCache(SCALED_ACTOR_GRID_DIR)
        self.marker_mode = False
        self.show_actors = True
        self.show_rating = True

    def set_marker_mode(self, on: bool) -> None:
        self.marker_mode = on

    def invalidate(self, path: str | None = None) -> None:
        self.thumbs.invalidate(path)
        if path is None:
            self.actors.invalidate()

    # ── tekenen ─────────────────────────────────────────────────────────
    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        card: FilmCard | None = index.data(DATA_ROLE)
        if card is None:
            super().paint(painter, option, index)
            return
        r: QRect = option.rect
        w, h = r.width(), r.height()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(r)

        if self.marker_mode:
            self._draw_cycle(painter, r, card.marker_thumbs, card.thumb_phase, card.marker_period, MARKER_FADE)
        elif len(card.thumbnails) > 1:
            self._draw_cycle(painter, r, card.thumbnails, card.thumb_phase, THUMB_PERIOD, THUMB_FADE)
        else:
            self._draw_single(painter, r, card.thumbnail)

        self._draw_bar(painter, r, card)
        if self.show_actors and card.actor_photos:
            self._draw_actors(painter, r, card)
        if self.show_rating and card.rating > 0:
            self._draw_rating(painter, r, card)
        if card.dimmed:
            painter.fillRect(r, QColor(*T.OVERLAY_DARK))
        if (hovered and not selected) or card.always_show_name:
            self._draw_hover(painter, r, card, dim=hovered and not selected and not card.dimmed)
        if selected:
            painter.fillRect(r, QColor(*T.SELECTION_TINT))
            painter.setPen(QPen(QColor(T.ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(1, 1, -1, -1))
        painter.setPen(QPen(QColor(T.BG), 1))
        painter.drawLine(r.right(), r.y(), r.right(), r.bottom())
        painter.drawLine(r.x(), r.bottom(), r.right(), r.bottom())
        painter.restore()

    def _placeholder(self, painter: QPainter, r: QRect) -> None:
        painter.fillRect(r, QColor(T.SURFACE))
        sz = max(18, min(48, r.height() // 3))
        pm = icons.pixmap("film", sz, T.TEXT_DIM)
        painter.drawPixmap(r.center().x() - sz // 2, r.center().y() - sz // 2, pm)

    def _draw_single(self, painter: QPainter, r: QRect, path: str | None) -> None:
        pm = self.thumbs.get(path or "", r.width(), r.height())
        if pm is None:
            self._placeholder(painter, r)
        else:
            painter.drawPixmap(r.x(), r.y(), pm)

    def _draw_cycle(self, painter: QPainter, r: QRect, imgs: list[str], phase: float,
                    period: float, fade: float) -> None:
        if not imgs:
            self._placeholder(painter, r)
            return
        w, h = r.width(), r.height()
        t = time.time() + phase
        t_mod = t % period
        i_cur = int(t / period) % len(imgs)
        cur = self.thumbs.get(imgs[i_cur], w, h)
        if cur is None:
            self._placeholder(painter, r)
        else:
            painter.drawPixmap(r.x(), r.y(), cur)
        if t_mod > period - fade:
            nxt = self.thumbs.get(imgs[(i_cur + 1) % len(imgs)], w, h)
            if nxt is not None:
                painter.setOpacity((t_mod - (period - fade)) / fade)
                painter.drawPixmap(r.x(), r.y(), nxt)
                painter.setOpacity(1.0)

    def _draw_bar(self, painter: QPainter, r: QRect, card: FilmCard) -> None:
        bar = QRect(r.x(), r.bottom() - BAR_H + 1, r.width(), BAR_H)
        painter.fillRect(bar, QColor(*T.OVERLAY_DARK))
        painter.setFont(small_font(painter.font()))
        fm = painter.fontMetrics()
        right = bar.right() - 5
        size_s = fmt_size(card.size)
        if size_s:
            painter.setPen(QColor(T.TEXT_MUTED))
            tw = fm.horizontalAdvance(size_s)
            painter.drawText(QRect(right - tw, bar.y(), tw, bar.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, size_s)
            right -= tw + 8
        dur_s = fmt_compact(card.duration)
        if dur_s:
            painter.setPen(QColor(T.TEXT))
            tw = fm.horizontalAdvance(dur_s)
            painter.drawText(QRect(right - tw, bar.y(), tw, bar.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, dur_s)
        x = bar.x() + 5
        iy = bar.y() + (bar.height() - BADGE_ICON) // 2
        if card.markers > 0:
            painter.drawPixmap(x, iy, icons.pixmap("bookmark", BADGE_ICON, T.BLUE))
            x += BADGE_ICON + 2
            painter.setPen(QColor(T.BLUE))
            txt = str(card.markers)
            painter.drawText(QRect(x, bar.y(), 40, bar.height()), Qt.AlignmentFlag.AlignVCenter, txt)
            x += fm.horizontalAdvance(txt) + 8
        if card.neg_markers > 0:
            painter.drawPixmap(x, iy, icons.pixmap("ban", BADGE_ICON, T.RED))
            x += BADGE_ICON + 2
            painter.setPen(QColor(T.RED))
            painter.drawText(QRect(x, bar.y(), 40, bar.height()), Qt.AlignmentFlag.AlignVCenter, str(card.neg_markers))

    def _draw_actors(self, painter: QPainter, r: QRect, card: FilmCard) -> None:
        ax = r.x() + 3
        ay = r.bottom() - BAR_H - ACTOR_PHOTO - 2
        for p in card.actor_photos[:6]:
            pm = self.actors.get(p, ACTOR_PHOTO, ACTOR_PHOTO)
            if pm is None:
                continue
            painter.drawPixmap(ax, ay, pm)
            ax += ACTOR_PHOTO + 2
            if ax + ACTOR_PHOTO > r.right():
                break

    def _draw_rating(self, painter: QPainter, r: QRect, card: FilmCard) -> None:
        painter.setFont(small_font(painter.font(), T.FONT_SIZE_XS, bold=True))
        fm = painter.fontMetrics()
        txt = str(int(card.rating))
        bw = BADGE_ICON + 4 + fm.horizontalAdvance(txt) + 10
        bh = fm.height() + 4
        badge = QRect(r.right() - bw - 3, r.y() + 3, bw, bh)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*T.OVERLAY_DARK))
        painter.drawRoundedRect(badge, 4, 4)
        painter.drawPixmap(badge.x() + 5, badge.y() + (bh - BADGE_ICON) // 2,
                           icons.pixmap("star-filled", BADGE_ICON, T.ACCENT))
        painter.setPen(QColor(T.ACCENT))
        painter.drawText(badge.adjusted(BADGE_ICON + 9, 0, -5, 0), Qt.AlignmentFlag.AlignVCenter, txt)

    def _draw_hover(self, painter: QPainter, r: QRect, card: FilmCard, dim: bool = True) -> None:
        if dim:
            painter.fillRect(r, QColor(*T.OVERLAY_DIM))
        strip_h = 24
        strip = QRect(r.x(), r.bottom() - BAR_H - strip_h + 1, r.width(), strip_h)
        painter.fillRect(strip, QColor(*T.OVERLAY_DARK))
        painter.setFont(small_font(painter.font(), T.FONT_SIZE_SM))
        painter.setPen(QColor(T.TEXT))
        fm = painter.fontMetrics()
        painter.drawText(strip.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(card.name, Qt.TextElideMode.ElideRight, strip.width() - 12))

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        cell = index.data(CELL_ROLE)
        if cell:
            return QSize(*cell)
        return super().sizeHint(option, index)
