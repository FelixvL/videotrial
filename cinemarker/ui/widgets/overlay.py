"""
CineMarker — zwevende panelen boven de video.

mpv tekent in een native child-venster; gewone Qt-children verdwijnen daar
achter en kunnen op Windows niet doorzichtig zijn. Panelen die de film zo
min mogelijk mogen verbergen zijn daarom frameless Tool-vensters met een
doorzichtige achtergrond (`mode="tool_window"`): alleen de knoppen zelf
liggen op de film. Ondoorzichtige panelen kunnen native children blijven
(`mode="native_child"`). Eén VideoAnchor volgt het videogebied en
herplaatst alle panelen.
"""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from ..theme import tokens as T

GLASS = None            # zwevende panelen hebben GEEN achtergrond: de film blijft volledig zichtbaar
GLASS_DENSE = None      # (alleen de knoppen, foto's en regels zelf hebben een eigen achtergrondje)


class Anchor(Enum):
    TOP_LEFT = "tl"
    TOP_RIGHT = "tr"
    BOTTOM_LEFT = "bl"
    BOTTOM_RIGHT = "br"
    RIGHT_FULL = "right"      # volle hoogte, rechts
    CENTER = "center"
    FILL = "fill"


class FloatingPanel(QWidget):
    """Basis voor panelen die over de video liggen."""

    def __init__(self, video: QWidget, *, anchor: Anchor, mode: str = "tool_window",
                 margin: int = T.SPACE_SM, offset: QPoint | None = None,
                 click_through: bool = False, bg: QColor | None = None, radius: int = T.RADIUS) -> None:
        self.mode = mode
        self.anchor = anchor
        self.margin = margin
        self.offset = offset or QPoint(0, 0)
        self.bg = bg
        self.radius = radius
        self._video = video
        if mode == "native_child":
            super().__init__(video.parentWidget())
            self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            self.setAutoFillBackground(True)
        else:
            flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            if click_through:
                flags |= Qt.WindowType.WindowStaysOnTopHint
            super().__init__(video.window(), flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            if click_through:
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setProperty("role", "transparent")
        self.hide()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.mode == "tool_window" and self.bg is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.bg)
            p.drawRoundedRect(self.rect(), self.radius, self.radius)
            p.end()

    def desired_size(self, video_size: QSize) -> QSize:
        """Standaard: vaste maat als die gezet is, anders sizeHint. Subklassen
        kunnen overschrijven (bv. volle videohoogte)."""
        mn, mx = self.minimumSize(), self.maximumSize()
        if not mn.isEmpty() and mn == mx:
            return mn
        return self.sizeHint()

    def place(self, video_rect: QRect) -> None:
        """video_rect in coördinaten van het parent-widget (native_child) of globaal (tool)."""
        size = self.desired_size(video_rect.size())
        w, h = size.width(), size.height()
        m = self.margin
        a = self.anchor
        if a is Anchor.FILL:
            geo = QRect(video_rect)
        elif a is Anchor.RIGHT_FULL:
            geo = QRect(video_rect.right() - w + 1, video_rect.top(), w, video_rect.height())
        elif a is Anchor.TOP_LEFT:
            geo = QRect(video_rect.left() + m, video_rect.top() + m, w, h)
        elif a is Anchor.TOP_RIGHT:
            geo = QRect(video_rect.right() - w - m + 1, video_rect.top() + m, w, h)
        elif a is Anchor.BOTTOM_LEFT:
            geo = QRect(video_rect.left() + m, video_rect.bottom() - h - m + 1, w, h)
        elif a is Anchor.BOTTOM_RIGHT:
            geo = QRect(video_rect.right() - w - m + 1, video_rect.bottom() - h - m + 1, w, h)
        else:
            geo = QRect(video_rect.center().x() - w // 2, video_rect.center().y() - h // 2, w, h)
        geo.translate(self.offset)
        self.setGeometry(geo)
        if self.isVisible() and self.mode == "native_child":
            self.raise_()

    def show_over_video(self) -> None:
        self.show()
        if self.mode == "native_child":
            self.raise_()


class VideoAnchor(QObject):
    """Volgt het videogebied en herplaatst alle geregistreerde panelen."""

    def __init__(self, video: QWidget, window: QWidget) -> None:
        super().__init__(video)
        self._video = video
        self._window = window
        self._panels: list[FloatingPanel] = []
        video.installEventFilter(self)
        window.installEventFilter(self)

    def register(self, panel: FloatingPanel) -> FloatingPanel:
        self._panels.append(panel)
        self.place(panel)
        return panel

    def video_rect_for(self, panel: FloatingPanel) -> QRect:
        if panel.mode == "native_child":
            parent = panel.parentWidget()
            top_left = self._video.mapTo(parent, QPoint(0, 0)) if parent else QPoint(0, 0)
            return QRect(top_left, self._video.size())
        return QRect(self._video.mapToGlobal(QPoint(0, 0)), self._video.size())

    def place(self, panel: FloatingPanel) -> None:
        panel.place(self.video_rect_for(panel))

    def place_all(self) -> None:
        for p in self._panels:
            self.place(p)

    def hide_all(self) -> None:
        for p in self._panels:
            p.hide()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        t = event.type()
        if t in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.WindowStateChange,
                 QEvent.Type.LayoutRequest):
            self.place_all()
        return False
