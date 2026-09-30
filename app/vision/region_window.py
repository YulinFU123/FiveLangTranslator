from __future__ import annotations

from enum import IntEnum

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from app.windows.window_styles import (
    get_window_rect, move_window_native, set_always_on_top,
)


class Edge(IntEnum):
    NONE = 0
    LEFT = 1
    RIGHT = 2
    TOP = 3
    BOTTOM = 4
    TL = 5
    TR = 6
    BL = 7
    BR = 8


class OcrRegionWindow(QWidget):
    """The framed rectangle the user selects for continuous OCR.

    Semi-transparent so it never hides what it is reading, always on top so it
    stays over the target window, and fully interactive: drag the inside to move,
    drag any edge/corner to resize, or lock it to freeze the area in place.
    """

    region_changed = Signal(QRect)
    lock_changed = Signal(bool)
    # Emitted around a drag/resize so the OCR service can stand down: every
    # capture hides this frame, and a hide mid-drag cancels the press, which
    # made the frame feel impossible to move.
    interaction_started = Signal()
    interaction_ended = Signal()
    MARGIN = 10
    MIN_WIDTH = 80
    MIN_HEIGHT = 40

    def __init__(self, geometry: QRect | None = None) -> None:
        super().__init__()
        self.locked = False
        self.drag = False
        self.resize_active = False
        self.edge = Edge.NONE
        self.start_point = QPoint()
        self.start_geometry = QRect()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setWindowTitle("OCR 识别区域")
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        # Hint chip makes the frame obvious at a glance. Transparent for mouse
        # events so it never steals a drag/resize from the window beneath it.
        self.hint = QLabel(self)
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hint.move(10, 6)
        # Windows reshuffles the Z order constantly, and every capture hides this
        # frame and shows it again (see RegionOcrService._grab_region), which costs
        # the Qt topmost flag each time. Re-pinning on a timer keeps it in front.
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(500)
        self._topmost_timer.timeout.connect(self._pin_topmost)
        self._user_placed = False
        if geometry is not None and geometry.isValid():
            self.setGeometry(geometry)
        else:
            screen = QApplication.primaryScreen()
            available = screen.availableGeometry() if screen else QRect(100, 100, 640, 200)
            width = min(720, int(available.width() * 0.5))
            height = min(220, int(available.height() * 0.25))
            x = available.left() + (available.width() - width) // 2
            y = available.top() + (available.height() - height) // 2
            self.setGeometry(x, y, width, height)
        self._refresh_style()

    # -- visibility / z-order --------------------------------------------
    def _pin_topmost(self) -> None:
        """Re-asserts HWND_TOPMOST; Windows drops it whenever anything else raises."""
        if self.isVisible():
            set_always_on_top(int(self.winId()), True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Every capture hides then re-shows this frame, so the Z order has to be
        # re-applied each time or the box silently lands behind the target window.
        self._pin_topmost()
        if not self._topmost_timer.isActive():
            self._topmost_timer.start()

    def ensure_on_screen(self) -> None:
        """Keeps the frame on a usable monitor, preferring the pointer's screen.

        A hand-placed frame is never yanked away; it is only rescued when it ends
        up off every screen (monitor unplugged) -- otherwise multi-monitor users
        would find the box sitting on the wrong (primary) display.
        """
        screens = QApplication.screens()
        if not screens:
            return
        center = self.geometry().center()
        on_some_screen = any(
            screen.availableGeometry().contains(center) for screen in screens
        )
        target = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if target is None:
            return
        available = target.availableGeometry()
        if available.contains(center):
            return  # already in view where the pointer is
        if self._user_placed and on_some_screen:
            return  # respect a manual placement that is still reachable
        width = min(self.width(), max(self.minimumWidth(), int(available.width() * 0.5)))
        height = min(self.height(), max(self.minimumHeight(), int(available.height() * 0.25)))
        self.setGeometry(
            available.left() + (available.width() - width) // 2,
            available.top() + (available.height() - height) // 2,
            width, height,
        )

    def reveal(self) -> None:
        """Shows the frame above every other window, on the screen being used."""
        self.ensure_on_screen()
        self.show()
        self.raise_()
        self._pin_topmost()

    # -- appearance ------------------------------------------------------
    def _refresh_style(self) -> None:
        """Repaints the frame body and restyles the hint chip.

        The body is painted in ``paintEvent``, NOT via a stylesheet: a stylesheet
        ``background`` on a plain ``QWidget`` top-level is not drawn reliably, and
        on this translucent (layered) window that left *nothing* for Windows to
        hit-test. Every click then fell through to the window underneath, which is
        exactly why the box looked fine but could not be dragged.
        """
        if self.locked:
            hint_text = "OCR 识别区域 · 已锁定"
            hint_color = "rgba(205,218,235,225)"
        else:
            hint_text = "OCR 识别区域 · 拖动移动，拖边角缩放"
            hint_color = "rgba(191,219,254,245)"
        self.hint.setText(hint_text)
        self.hint.setStyleSheet(
            f"background:transparent;color:{hint_color};font-size:11px;padding:2px 4px"
        )
        self.hint.adjustSize()
        self.update()

    def paintEvent(self, event) -> None:
        """Draws the translucent fill + border directly.

        Painting real pixels (with a non-zero alpha) is what makes the layered
        window hit-testable, so the frame can actually be grabbed and moved.
        """
        if self.locked:
            border = QColor(150, 170, 200, 215)
            fill = QColor(130, 150, 180, 45)
            width = 2.0
        else:
            border = QColor(96, 165, 250, 245)
            fill = QColor(96, 165, 250, 70)
            width = 3.0
        inset = width / 2.0
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(border, width))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 6.0, 6.0)
        painter.end()

    # -- state -----------------------------------------------------------
    def set_locked(self, value: bool) -> None:
        value = bool(value)
        if value == self.locked:
            return
        self.locked = value
        self.drag = self.resize_active = False
        self.unsetCursor()
        self._refresh_style()
        self.lock_changed.emit(self.locked)

    def region(self) -> QRect:
        return self.geometry()

    def apply_region(self, rect: QRect) -> None:
        self.setGeometry(rect)
        self.region_changed.emit(self.geometry())

    # -- interaction -----------------------------------------------------
    def detect(self, point) -> Edge:
        if self.locked:
            return Edge.NONE
        left = point.x() <= self.MARGIN
        right = point.x() >= self.width() - self.MARGIN
        top = point.y() <= self.MARGIN
        bottom = point.y() >= self.height() - self.MARGIN
        return (
            Edge.TL if top and left else Edge.TR if top and right
            else Edge.BL if bottom and left else Edge.BR if bottom and right
            else Edge.LEFT if left else Edge.RIGHT if right
            else Edge.TOP if top else Edge.BOTTOM if bottom else Edge.NONE
        )

    def _cursor(self, edge: Edge) -> None:
        mapping = {
            Edge.LEFT: Qt.SizeHorCursor, Edge.RIGHT: Qt.SizeHorCursor,
            Edge.TOP: Qt.SizeVerCursor, Edge.BOTTOM: Qt.SizeVerCursor,
            Edge.TL: Qt.SizeFDiagCursor, Edge.BR: Qt.SizeFDiagCursor,
            Edge.TR: Qt.SizeBDiagCursor, Edge.BL: Qt.SizeBDiagCursor,
        }
        self.setCursor(mapping.get(edge, Qt.OpenHandCursor))

    def mousePressEvent(self, event) -> None:
        if self.locked or event.button() != Qt.LeftButton:
            return
        self.edge = self.detect(event.position().toPoint())
        self.start_point = event.globalPosition().toPoint()
        self.start_geometry = self.geometry()
        self.resize_active = self.edge != Edge.NONE
        self.drag = not self.resize_active
        if self.drag or self.resize_active:
            self.interaction_started.emit()
        if self.drag:
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self.locked:
            return
        delta = event.globalPosition().toPoint() - self.start_point
        if self.drag:
            target = self.start_geometry.topLeft() + delta
            # Native move skips Qt re-laying-out and re-compositing the
            # translucent surface on every mouse move (same trick as the overlay),
            # which is what made dragging feel laggy/imprecise.
            if not move_window_native(int(self.winId()), target.x(), target.y()):
                self.move(target)
        elif self.resize_active:
            self._resize(delta)
        else:
            self.edge = self.detect(event.position().toPoint())
            self._cursor(self.edge)

    def _resize(self, delta) -> None:
        geometry = QRect(self.start_geometry)
        edge = self.edge
        min_width, min_height = self.minimumWidth(), self.minimumHeight()
        if edge in (Edge.LEFT, Edge.TL, Edge.BL):
            geometry.setLeft(min(geometry.right() - min_width, geometry.left() + delta.x()))
        if edge in (Edge.RIGHT, Edge.TR, Edge.BR):
            geometry.setRight(max(geometry.left() + min_width, geometry.right() + delta.x()))
        if edge in (Edge.TOP, Edge.TL, Edge.TR):
            geometry.setTop(min(geometry.bottom() - min_height, geometry.top() + delta.y()))
        if edge in (Edge.BOTTOM, Edge.BL, Edge.BR):
            geometry.setBottom(max(geometry.top() + min_height, geometry.bottom() + delta.y()))
        self.setGeometry(geometry)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and (self.drag or self.resize_active):
            was_dragging = self.drag
            self.drag = self.resize_active = False
            self.edge = Edge.NONE
            if was_dragging:
                # Resync Qt's cached geometry with the real native position.
                rect = get_window_rect(int(self.winId()))
                if rect is not None:
                    self.move(rect[0], rect[1])
            # Once placed by hand the frame must stay put, even across shows.
            self._user_placed = True
            self.unsetCursor()
            self.interaction_ended.emit()
            self.region_changed.emit(self.geometry())
