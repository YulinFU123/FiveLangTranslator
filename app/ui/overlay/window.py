from __future__ import annotations

import ctypes
import html
import sys
import time
from enum import IntEnum

from PySide6.QtCore import (
    QEvent, QPoint, QPropertyAnimation, QRect, QEasingCurve, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.player.layout import Rect, anchor_rect, clamp_rect, profile_to_rect, snap_to_edge
from app.storage.appearance import (
    LAYOUT_DUAL_LINE, LAYOUT_INLINE, LAYOUT_SINGLE_ALTERNATE, LAYOUT_STACKED,
    OverlayAppearance, dim_color, parse_color, to_rgba_string,
)
from app.translation.prompt import clamp_line_budget
from app.ui.overlay.anchor import (
    ANCHOR_MARGIN, ANCHOR_POINTS, DEFAULT_ANCHOR, nearest_anchor,
)
from app.ui.overlay.style_manager import safe_family
from app.windows.window_styles import (
    get_window_rect, move_window_native, set_always_on_top, set_click_through,
)

# Rebuilding the line budget has to settle before it reaches the translator:
# dragging an edge passes through every intermediate height.
LINE_BUDGET_DEBOUNCE_MS = 100

# Rapid subtitle updates (e.g. a "翻译中…" draft immediately followed by the
# final translation for the same segment) are collapsed into a single repaint
# inside this window, so the overlay never wastes a full repaint on a frame
# that is about to be overwritten. Isolated updates still paint immediately.
REPAINT_COALESCE_MS = 10

# A translucent (layered) window only receives mouse events where it has actually
# painted pixels. With the intended fully transparent background the whole box
# became click-through: it could not be dragged, and the hover border never even
# appeared (hover requires a mouse event first). Painting with this floor alpha
# (2/255 -- visually imperceptible) keeps the "floating text, no box" look while
# making the box grabbable again. Input transparency ("through") is implemented
# separately via WA_TransparentForMouseEvents / WS_EX_TRANSPARENT and still wins.
HIT_TEST_MIN_ALPHA = 2

# Win32 plumbing for system-level topmost. Qt's WindowStaysOnTopHint is applied
# only when the flag is toggled, so Windows can (and does) push the box behind
# fullscreen surfaces afterwards. These let us re-pin on every reorder.
WM_WINDOWPOSCHANGING = 0x0046
SWP_NOZORDER = 0x0004
HWND_TOPMOST = -1


class _WinMsg(ctypes.Structure):
    """Minimal MSG layout -- only the fields needed to reach the lParam."""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
    ]


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


class OverlayWindow(QWidget):
    committed = Signal()
    anchor_requested = Signal(str)
    state_changed = Signal()
    line_budget_changed = Signal(int)
    appearance_committed = Signal(object)
    MARGIN = 10

    def __init__(self, settings, style_manager=None, bus=None) -> None:
        super().__init__()
        self.settings = settings
        if style_manager is not None:
            self.style_manager = style_manager
            self.appearance = style_manager.get_style()
        else:
            self.style_manager = None
            mode = str(getattr(settings, "overlay_layout_mode", LAYOUT_DUAL_LINE))
            if mode not in (LAYOUT_DUAL_LINE, LAYOUT_SINGLE_ALTERNATE):
                mode = LAYOUT_DUAL_LINE
            self.appearance = OverlayAppearance(
                subtitle_font_size=int(getattr(settings, "source_font", 16)),
                show_source=bool(getattr(settings, "show_source", True)),
                subtitle_layout_mode=mode,
            )
        self.bus = bus
        self.locked = settings.locked
        self.through = settings.through
        self._hovering = False
        self._user_hidden = False
        self.fullscreen_mode = False
        self.player_rect: Rect | None = None
        self.drag = False
        self.resize_active = False
        # False until the user actually drags an edge; before that, apply_profile
        # hugs the two subtitle lines so the box starts small instead of stretching
        # to the (large) saved profile size.
        self._size_customized = False
        # True once the user has placed the box by hand. Player-follow logic then
        # stops re-applying profiles, which is what used to drag the box away.
        self._user_positioned = False
        # Suppresses per-move work (line budget, reflow) while dragging.
        self._dragging = False
        # Mirrors the Qt flag so the Win32 re-assert timer knows what to restore.
        self._win_topmost = False
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(800)
        self._topmost_timer.timeout.connect(self._reassert_topmost)
        self.edge = Edge.NONE
        self.start_point = QPoint()
        self.start_geometry = QRect()
        # None until the window has actually been shown: QFontMetrics cannot be
        # trusted while the widget is still hidden (font not applied yet).
        self.published_line_budget: int | None = None
        self.line_budget_timer = QTimer(self)
        self.line_budget_timer.setSingleShot(True)
        self.line_budget_timer.timeout.connect(self.publish_line_budget)
        # Repaint coalescing state: a single short-lived timer collapses bursts
        # of subtitle updates into one paint so the final frame wins without an
        # extra intermediate repaint.
        self._pending_source = ""
        self._pending_translation = ""
        self._pending_dirty = False
        self._paint_scheduled = False
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.timeout.connect(self._flush_pending)
        self._anchor_animation: QPropertyAnimation | None = None
        self._last_snap_time = 0.0
        # Single-alternate mode: a timer flips which line is shown; hovering the
        # box pauses the rotation so the reader is never interrupted.
        self._alt_timer = QTimer(self)
        self._alt_timer.setSingleShot(False)
        self._alt_timer.timeout.connect(self._toggle_alternation)
        self._alt_show_source = True
        self._alt_paused = False
        self._build()
        self._watch_font_database()
        self.apply_profile(False)
        if bus is not None:
            bus.subtitleStyleChanged.connect(self._on_style_changed)

    def _build(self) -> None:
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        # Small floor so the box can actually be shrunk (the previous 380x110
        # minimum made it feel like resizing was unavailable).
        self.setMinimumSize(160, 48)
        self._apply_size_limits()
        self.panel = QFrame()
        self.panel.setObjectName("panel")
        self.source = QLabel("I don't think he knows the truth.")
        self.translation = QLabel("我觉得他并不知道真相。")
        for label in (self.source, self.translation):
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.source.setTextFormat(Qt.RichText)
        self.stacked_layout = QVBoxLayout()
        self.stacked_layout.setContentsMargins(26, 16, 26, 18)
        self.stacked_layout.setSpacing(6)
        self.inline_layout = QHBoxLayout()
        self.inline_layout.setContentsMargins(26, 16, 26, 18)
        self.inline_layout.setSpacing(14)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.stacked_layout)
        self._apply_layout_mode()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.panel)
        self.refresh()

    def _apply_size_limits(self) -> None:
        """Caps the box to the work area of the screen it currently sits on.

        Qt clamps every ``setGeometry`` to the minimum/maximum, so dragging an
        edge can never shrink the box into an unusable sliver nor grow it past
        the monitor.
        """
        screen = self._screen_for_center() if self.isVisible() else (self.screen() or QApplication.primaryScreen())
        available = screen.availableGeometry() if screen is not None else None
        max_width = available.width() if available is not None else 3840
        max_height = available.height() if available is not None else 2160
        self.setMaximumSize(
            max(self.minimumWidth(), max_width),
            max(self.minimumHeight(), max_height),
        )

    # -- layout mode ------------------------------------------------------
    def _apply_layout_mode(self) -> None:
        """Places source/translation into the stacked or inline arrangement.

        Safe to call repeatedly: the labels are detached from any layout first,
        so switching modes (or re-running after a font change) never orphans them.
        """
        inline = self.appearance.subtitle_layout_mode == LAYOUT_INLINE
        target = self.inline_layout if inline else self.stacked_layout
        other = self.stacked_layout if inline else self.inline_layout
        for label in (self.source, self.translation):
            other.removeWidget(label)
            target.removeWidget(label)
        target.addWidget(self.source)
        target.addWidget(self.translation)
        panel_layout = self.panel.layout()
        if panel_layout.indexOf(other) >= 0:
            panel_layout.removeItem(other)
        if panel_layout.indexOf(target) < 0:
            panel_layout.addLayout(target)
        self._apply_alternation_state()

    def set_layout_mode(self, mode: str) -> None:
        """Switches between dual-line and single-alternate, then re-flows labels."""
        mode = mode or LAYOUT_DUAL_LINE
        if self.appearance.subtitle_layout_mode == mode:
            return
        self.appearance.subtitle_layout_mode = mode
        self._apply_layout_mode()

    # -- single-alternate rotation -----------------------------------------
    def _apply_alternation_state(self) -> None:
        """Shows one subtitle line at a time in single-alternate mode."""
        single = self.appearance.subtitle_layout_mode == LAYOUT_SINGLE_ALTERNATE
        if single and self.appearance.show_source:
            self.source.setVisible(self._alt_show_source)
            self.translation.setVisible(not self._alt_show_source)
            if self._alt_paused:
                self._alt_timer.stop()
            else:
                self._alt_timer.start(3000)
        else:
            self.source.setVisible(self.appearance.show_source)
            self.translation.setVisible(True)
            self._alt_timer.stop()

    def _toggle_alternation(self) -> None:
        self._alt_show_source = not self._alt_show_source
        self._apply_alternation_state()

    def _on_style_changed(self, appearance) -> None:
        self.appearance = appearance
        self.refresh()

    def paintEvent(self, event) -> None:
        """Paints the box background with a hit-testable floor alpha.

        See ``HIT_TEST_MIN_ALPHA``: without any painted pixels Windows hands every
        click to the window underneath, so the box could not be dragged.
        """
        super().paintEvent(event)
        r, g, b, alpha = parse_color(self.appearance.subtitle_bg_color)
        painted = min(max(int(round(alpha * 255)), HIT_TEST_MIN_ALPHA), 255)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(r, g, b, painted))
        painter.end()

    def enterEvent(self, event) -> None:
        if self.appearance.subtitle_layout_mode == LAYOUT_SINGLE_ALTERNATE and self.appearance.show_source:
            self._alt_paused = True
            self._alt_timer.stop()
        if not self.through:
            self._hovering = True
            self._refresh_border()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.appearance.subtitle_layout_mode == LAYOUT_SINGLE_ALTERNATE:
            self._alt_paused = False
            if self.appearance.show_source:
                self._alt_timer.start(3000)
        if self._hovering:
            self._hovering = False
            self._refresh_border()
        super().leaveEvent(event)

    def set_topmost(self, enabled: bool) -> None:
        """Adds or removes the always-on-top window flag and pins the Z order.

        Qt applies its flag only at the moment it is toggled, so any window that
        raises itself later can cover the subtitle. On Windows the real
        ``HWND_TOPMOST`` state is asserted here and then re-asserted on a timer,
        which is what actually keeps the box in front of fullscreen video.
        """
        enabled = bool(enabled)
        self._win_topmost = enabled
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) != enabled:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
            if self.isVisible():
                self.show()
        if enabled:
            set_always_on_top(int(self.winId()), True)
            if not self._topmost_timer.isActive():
                self._topmost_timer.start()
        else:
            self._topmost_timer.stop()
            set_always_on_top(int(self.winId()), False)

    def _reassert_topmost(self) -> None:
        """Re-pins the window so later-raised windows cannot cover the subtitle."""
        if self._win_topmost and self.isVisible():
            set_always_on_top(int(self.winId()), True)

    def set_visible(self, visible: bool) -> None:
        """Shows or hides the subtitle window while preserving all other state.

        Pure visibility control: geometry, style, topmost and background logic
        are untouched, so restoring shows exactly where it was. While hidden,
        `update_subtitle` keeps updating the labels but stops forcing a show.
        """
        self._user_hidden = not visible
        if visible:
            self.show()
            if not self.through:
                self.raise_()
        else:
            self.hide()

    def apply_anchor(self, anchor: str) -> None:
        """Repositions the box to one of the nine screen anchors (keeps size).

        The target is computed from the work area of the monitor holding the
        window centre, then animated over 200ms when visible (the exact position
        is still set immediately when hidden so a later show lands correctly).
        The resulting geometry is persisted back into the profile so it survives
        a restart and feeds the future edge-snap module.
        """
        # An explicit nine-grid/snap placement counts as hand-placed.
        self._user_positioned = True
        layout_key = ANCHOR_POINTS.get(anchor, ANCHOR_POINTS[DEFAULT_ANCHOR])
        screen = self._screen_for_center()
        available = screen.availableGeometry()
        target = anchor_rect(
            layout_key,
            Rect(available.left(), available.top(), available.width(), available.height()),
            self.width(), self.height(), ANCHOR_MARGIN,
        )
        if not self.isVisible():
            self.setGeometry(target.left, target.top, target.width, target.height)
            self.save_profile()
            self.committed.emit()
            return
        if self._anchor_animation is not None:
            self._anchor_animation.stop()
        animation = QPropertyAnimation(self, b"geometry")
        animation.setDuration(200)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setEndValue(QRect(target.left, target.top, target.width, target.height))
        animation.finished.connect(lambda: (self.save_profile(), self.committed.emit()))
        self._anchor_animation = animation
        animation.start(QPropertyAnimation.DeleteWhenStopped)

    def _screen_for_center(self):
        """Monitor holding the window centre; falls back to the current/primary."""
        center = self.geometry().center()
        return QApplication.screenAt(center) or self.screen() or QApplication.primaryScreen()

    # -- line budget ------------------------------------------------------
    def _watch_font_database(self) -> None:
        """Fonts can finish loading asynchronously; recompute once that happens."""
        try:
            from PySide6.QtGui import QFontDatabase

            candidates = (getattr(QFontDatabase, "reloadFinished", None),
                          getattr(QGuiApplication, "fontDatabaseChanged", None))
        except Exception:  # pragma: no cover - depends on the Qt build
            candidates = ()
        for candidate in candidates:
            connect = getattr(candidate, "connect", None)
            if connect is None:
                continue
            try:
                connect(self.schedule_line_budget_update)
                return
            except Exception:  # pragma: no cover - defensive against signature changes
                continue

    def visible_lines(self) -> int:
        """How many translation lines fit into the box at the current font size."""
        if not self.isVisible() and self.published_line_budget is None:
            # Metrics are unreliable before the first show; the caller gets the default.
            return 2
        metrics = QFontMetrics(self.translation.font())
        line_height = metrics.lineSpacing()
        if line_height <= 0:
            return 2
        layout = self.panel.layout()
        margins = layout.contentsMargins() if layout is not None else None
        vertical_chrome = 4  # panel border on both sides
        if margins is not None:
            vertical_chrome += margins.top() + margins.bottom()
            vertical_chrome += layout.spacing()
        available = self.height() - vertical_chrome
        show_source_stacked = (
            self.appearance.show_source
            and self.appearance.subtitle_layout_mode not in (LAYOUT_INLINE, LAYOUT_SINGLE_ALTERNATE)
        )
        if show_source_stacked:
            rendered = self.source.sizeHint().height()
            available -= rendered if rendered > 0 else QFontMetrics(self.source.font()).lineSpacing()
        if available < line_height:
            return clamp_line_budget(1)
        return clamp_line_budget(int(available // line_height))

    def schedule_line_budget_update(self) -> None:
        """Debounced recompute. Restarting invalidates the pending callback."""
        self.line_budget_timer.stop()
        self.line_budget_timer.start(LINE_BUDGET_DEBOUNCE_MS)

    def publish_line_budget(self) -> None:
        """Pushes the final value only, and only when it actually differs."""
        self.line_budget_timer.stop()
        budget = self.visible_lines()
        if budget == self.published_line_budget:
            return
        self.published_line_budget = budget
        self.line_budget_changed.emit(budget)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Re-evaluate on every show: the box may have moved to a monitor with a
        # different work area.
        self._apply_size_limits()
        set_click_through(int(self.winId()), self.through)
        # Showing recreates the native handle, so re-pin the Z order here too.
        if self._win_topmost:
            set_always_on_top(int(self.winId()), True)
        if self.published_line_budget is None:
            # First real measurement: fonts are applied now, so push immediately
            # instead of waiting for the debounce timer.
            self.line_budget_timer.stop()
            self.publish_line_budget()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.schedule_line_budget_update()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self._dragging or self.resize_active:
            # Dragging/resizing: skip the per-move recomputation, it runs once on
            # release so the move itself stays on the input thread's critical path.
            return
        # Moving to another monitor can change DPI and therefore line height.
        self.schedule_line_budget_update()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.ScreenChangeInternal, QEvent.Type.PaletteChange):
            self.schedule_line_budget_update()
        if event.type() in (
            QEvent.Type.WindowStateChange,
            QEvent.Type.ActivationChange,
            QEvent.Type.ScreenChangeInternal,
        ):
            # Minimise/restore, focus changes and monitor switches all reshuffle
            # the Z order, so re-assert immediately instead of waiting for the
            # periodic timer.
            self._reassert_topmost()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # Losing focus is the classic moment another window jumps in front.
        self._reassert_topmost()

    def nativeEvent(self, eventType, message):
        """Forces HWND_TOPMOST while Windows is reordering the window.

        Patches the WINDOWPOS structure in place instead of calling
        SetWindowPos from inside the handler, which would recurse and flood the
        message queue with further WM_WINDOWPOSCHANGING messages.
        """
        if sys.platform == "win32" and self._win_topmost:
            try:
                if eventType in ("windows_generic_MSG", "windows_dispatcher_MSG"):
                    msg = _WinMsg.from_address(int(message))
                    if msg.message == WM_WINDOWPOSCHANGING and msg.lParam:
                        # WINDOWPOS layout: hwnd, hwndInsertAfter, x, y, cx, cy, flags
                        position = ctypes.cast(msg.lParam, ctypes.POINTER(ctypes.c_int64))
                        position[1] = HWND_TOPMOST
                        # SWP_NOZORDER makes the insert-after value ignored.
                        position[4] = position[4] & ~SWP_NOZORDER
            except Exception:  # pragma: no cover - native plumbing must never crash
                pass
        return super().nativeEvent(eventType, message)

    def refresh(self) -> None:
        a = self.appearance
        family = safe_family(a.subtitle_font_family)
        font = QFont(family, a.subtitle_font_size)
        font.setWeight(QFont.Weight(a.subtitle_font_weight))
        self.source.setFont(font)
        if self.style_manager is None:
            # Legacy path (no shared appearance): Settings owns separate sizes for
            # the original and translated lines, so honour them instead of
            # collapsing both lines onto one size.
            translation_font = QFont(
                family, int(getattr(self.settings, "translation_font", a.subtitle_font_size))
            )
            translation_font.setWeight(QFont.Weight(a.subtitle_font_weight))
            self.translation.setFont(translation_font)
        else:
            self.translation.setFont(font)
        oc = parse_color(a.subtitle_original_color)
        tc = parse_color(a.subtitle_translation_color)
        bg = parse_color(a.subtitle_bg_color)
        bg_rgba = to_rgba_string(*bg)
        src_rgba = to_rgba_string(*oc)
        tr_rgba = to_rgba_string(*tc)
        # Drop the visible border entirely when the panel background is fully
        # transparent, so the overlay reads as floating text with no box. A faint
        # black border is added while the pointer hovers / the box is dragged so
        # it still reads as grabbable (see _compute_border).
        border = self._compute_border(bg[3])
        self.setStyleSheet(
            f"QFrame#panel{{background:{bg_rgba};border:{border};border-radius:16px}}"
            "QLabel{background:transparent}"
        )
        self.source.setStyleSheet(f"color:{src_rgba};background:transparent")
        self.translation.setStyleSheet(f"color:{tr_rgba};background:transparent")
        self._apply_alternation_state()
        # Re-render the currently displayed text with the new font. The span now
        # carries the family/size inline, but an already-set label keeps its old
        # markup until setText runs again, so a font tweak would otherwise only
        # show up on the next subtitle instead of immediately.
        if getattr(self, "_last_subtitle_value", None) is not None:
            src, tr = self._render_subtitle(self._last_subtitle_value)
            self.source.setText(src)
            self.translation.setText(tr)
        elif getattr(self, "_last_transcript_data", None) is not None:
            self.update_transcript_state(self._last_transcript_data)
        self.schedule_line_budget_update()

    def _compute_border(self, bg_alpha: float) -> str:
        """Border policy for the floating subtitle box.

        The background is transparent by default, so the box reads as bare
        floating text with no outline. A faint black border appears only while
        the pointer is over the box (or it is being dragged / clicked) as a drag
        affordance, making it feel grabbable without a permanent visible frame.
        """
        if bg_alpha == 0:
            if self._hovering or self.drag or self.resize_active:
                return "1px solid rgba(0,0,0,120)"
            return "none"
        if self.locked:
            return "1px solid rgba(255,255,255,25)"
        return "2px solid rgba(96,165,250,220)"

    def _refresh_border(self) -> None:
        """Re-applies only the panel border (cheap, no text change)."""
        bg = parse_color(self.appearance.subtitle_bg_color)
        border = self._compute_border(bg[3])
        self.setStyleSheet(
            f"QFrame#panel{{background:{to_rgba_string(*bg)};border:{border};border-radius:16px}}"
            "QLabel{background:transparent}"
        )

    def _subtitle_font_css(self) -> tuple[str, str]:
        """Returns ``(base, draft)`` inline font CSS using the chosen family/size/weight.

        Embedding the family and size (not just ``font-weight``) is required because
        ``QLabel`` rich text does not reliably inherit them from ``setFont()``; without
        this, changing the typeface or point size has no visible effect on the overlay.
        """
        family = safe_family(self.appearance.subtitle_font_family)
        size = self.appearance.subtitle_font_size
        weight = self.appearance.subtitle_font_weight
        base = f"font-family:'{family}';font-size:{size}px;font-weight:{weight}"
        draft = f"font-family:'{family}';font-size:{size}px"
        return base, draft

    def _render_subtitle(self, value) -> tuple[str, str]:
        base_font, draft_font = self._subtitle_font_css()
        stable = html.escape(value.stable_source_text or "")
        draft = html.escape(value.draft_source_text or "")
        if stable or draft:
            oc = to_rgba_string(*parse_color(self.appearance.subtitle_original_color))
            draft_color = dim_color(self.appearance.subtitle_original_color, 0.6)
            source_html = (
                f'<span style="color:{oc};{base_font}">{stable}</span>'
                f'<span style="color:{draft_color};{draft_font}">{draft}</span>'
            )
        else:
            source_html = html.escape(value.source_text)
        tc = to_rgba_string(*parse_color(self.appearance.subtitle_translation_color))
        translated = value.translated_text
        translation_html = (
            f'<span style="color:{tc};{base_font}">{html.escape(translated)}</span>'
            if translated else "翻译中…"
        )
        return source_html, translation_html

    def update_subtitle(self, value) -> None:
        self._last_subtitle_value = value
        self._pending_source, self._pending_translation = self._render_subtitle(value)
        self._pending_dirty = True
        if self._paint_scheduled:
            # A repaint is already queued for this burst: just restart the
            # single-shot so the latest pending text wins, avoiding a second
            # repaint for the soon-to-arrive final.
            self._paint_timer.start(REPAINT_COALESCE_MS)
            return
        # Idle: paint immediately for the lowest possible latency, then open a
        # short coalescing window so a follow-up update merges into one repaint.
        self._apply_pending()
        self._paint_scheduled = True
        self._paint_timer.start(REPAINT_COALESCE_MS)

    def _apply_pending(self) -> None:
        """Commits the pending subtitle text to the widgets and shows the window."""
        self.source.setText(self._pending_source)
        self.translation.setText(self._pending_translation)
        self._pending_dirty = False
        if not self._user_hidden:
            self.show()

    def _flush_pending(self) -> None:
        """Fires after the coalescing window; applies only if new text arrived."""
        self._paint_scheduled = False
        if self._pending_dirty:
            self._apply_pending()

    def update_transcript_state(self, data) -> None:
        self._last_transcript_data = data
        base_font, draft_font = self._subtitle_font_css()
        stable = html.escape(data.get("committed", ""))
        draft = html.escape(data.get("draft", ""))
        oc = to_rgba_string(*parse_color(self.appearance.subtitle_original_color))
        draft_color = dim_color(self.appearance.subtitle_original_color, 0.6)
        self.source.setText(
            f'<span style="color:{oc};{base_font}">{stable}</span>'
            f'<span style="color:{draft_color};{draft_font}">{draft}</span>'
        )

    def set_locked(self, value: bool) -> None:
        self.locked = bool(value)
        self.settings.locked = self.locked
        self.drag = self.resize_active = False
        self.unsetCursor()
        self.refresh()
        self.state_changed.emit()

    def set_through(self, value: bool) -> None:
        self.through = bool(value)
        self.settings.through = self.through
        if value:
            self.set_locked(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self.through)
        set_click_through(int(self.winId()), self.through)
        self._hovering = False
        self._refresh_border()
        self.state_changed.emit()

    def set_player_state(self, state) -> None:
        self.player_rect = Rect(state.left, state.top, state.width, state.height)
        # The user's explicit hide intent always wins over player-follow logic.
        if self._user_hidden:
            return
        # Only hide for a tracked player. Without a configured player process the
        # tracker follows whatever window has focus, so any minimised window would
        # make the subtitle vanish for no reason.
        if (
            self.settings.hide_when_player_minimized
            and state.minimized
            and self.settings.player_process
        ):
            self.hide()
            return
        if not self.isVisible():
            self.show()
        mode_changed = self.fullscreen_mode != state.fullscreen
        self.fullscreen_mode = state.fullscreen
        if self.can_edit():
            return
        if self._user_positioned:
            # The user placed the box by hand (drag/resize/anchor): player-follow
            # must not move it again, otherwise it drifts back on every poll.
            return
        if mode_changed and self.settings.auto_fullscreen_profile:
            self.apply_profile(self.fullscreen_mode, self.player_rect)
        elif self.settings.follow_player:
            self.apply_profile(self.fullscreen_mode, self.player_rect)

    def can_edit(self) -> bool:
        return not self.locked and not self.through

    def detect(self, point) -> Edge:
        if not self.can_edit():
            return Edge.NONE
        left = point.x() <= self.MARGIN
        right = point.x() >= self.width() - self.MARGIN
        top = point.y() <= self.MARGIN
        bottom = point.y() >= self.height() - self.MARGIN
        return Edge.TL if top and left else Edge.TR if top and right else Edge.BL if bottom and left else Edge.BR if bottom and right else Edge.LEFT if left else Edge.RIGHT if right else Edge.TOP if top else Edge.BOTTOM if bottom else Edge.NONE

    def _cursor(self, edge: Edge) -> None:
        mapping = {Edge.LEFT: Qt.SizeHorCursor, Edge.RIGHT: Qt.SizeHorCursor, Edge.TOP: Qt.SizeVerCursor, Edge.BOTTOM: Qt.SizeVerCursor, Edge.TL: Qt.SizeFDiagCursor, Edge.BR: Qt.SizeFDiagCursor, Edge.TR: Qt.SizeBDiagCursor, Edge.BL: Qt.SizeBDiagCursor}
        self.setCursor(mapping.get(edge, Qt.OpenHandCursor))

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-clicking the chrome snaps the box to the nearest nine-grid anchor.

        Double-clicking the source/translation text is ignored so it can still be
        selected/copied. A 300ms guard blocks rapid repeat double-clicks from
        bouncing the window between anchors.
        """
        if not self.can_edit() or event.button() != Qt.LeftButton:
            return
        local = self.panel.mapFrom(self, event.position().toPoint())
        if self.source.geometry().contains(local) or self.translation.geometry().contains(local):
            return
        now = time.monotonic()
        if now - self._last_snap_time < 0.3:
            return
        self._last_snap_time = now
        self.snap_to_nearest_anchor()

    def snap_to_nearest_anchor(self) -> None:
        """Computes the nearest standard anchor and requests it via the manager.

        Routing through `anchor_requested` reuses the exact same path as the
        manual nine-grid picker: the manager owns state + persistence and the
        overlay's `apply_anchor` does the move + animation, so the two stay in
        perfect sync (highlight, persistence, 200ms easing all identical).
        """
        screen = self._screen_for_center()
        available = screen.availableGeometry()
        screen_rect = Rect(available.left(), available.top(), available.width(), available.height())
        center_x = self.x() + self.width() / 2.0
        center_y = self.y() + self.height() / 2.0
        anchor = nearest_anchor(center_x, center_y, screen_rect, self.width(), self.height())
        self.anchor_requested.emit(anchor)

    def mousePressEvent(self, event) -> None:
        if not self.can_edit() or event.button() != Qt.LeftButton:
            return
        self.edge = self.detect(event.position().toPoint())
        self.start_point = event.globalPosition().toPoint()
        self.start_geometry = self.geometry()
        self.resize_active = self.edge != Edge.NONE
        self.drag = not self.resize_active
        self._dragging = self.drag
        if self.drag:
            self.setCursor(Qt.ClosedHandCursor)
        self._refresh_border()

    def mouseMoveEvent(self, event) -> None:
        if not self.can_edit():
            return
        delta = event.globalPosition().toPoint() - self.start_point
        if self.drag:
            target = self.start_geometry.topLeft() + delta
            # Native move: bypasses Qt's geometry invalidation (which would
            # re-layout and re-composite the translucent surface every mouse
            # move, the source of the visible drag lag).
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
        if edge in (Edge.LEFT, Edge.TL, Edge.BL): geometry.setLeft(min(geometry.right() - min_width, geometry.left() + delta.x()))
        if edge in (Edge.RIGHT, Edge.TR, Edge.BR): geometry.setRight(max(geometry.left() + min_width, geometry.right() + delta.x()))
        if edge in (Edge.TOP, Edge.TL, Edge.TR): geometry.setTop(min(geometry.bottom() - min_height, geometry.top() + delta.y()))
        if edge in (Edge.BOTTOM, Edge.BL, Edge.BR): geometry.setBottom(max(geometry.top() + min_height, geometry.bottom() + delta.y()))
        self.setGeometry(geometry)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and (self.drag or self.resize_active):
            if self.resize_active:
                self._size_customized = True
            # Hand-placed: stop player-follow from dragging the box away later.
            self._user_positioned = True
            was_dragging = self.drag
            self.drag = self.resize_active = False
            self._dragging = False
            self.edge = Edge.NONE
            self.unsetCursor()
            if was_dragging:
                # Resync Qt's cached geometry with the real native position so the
                # persisted profile matches what is on screen.
                rect = get_window_rect(int(self.winId()))
                if rect is not None:
                    self.move(rect[0], rect[1])
            self._refresh_border()
            self.committed.emit()

    def active_profile(self):
        return self.settings.fullscreen_profile if self.fullscreen_mode else self.settings.windowed_profile

    def apply_profile(self, fullscreen: bool | None = None, anchor: Rect | None = None) -> None:
        if fullscreen is not None:
            self.fullscreen_mode = fullscreen
        profile = self.active_profile()
        if anchor is None:
            screen = self._screen_for_profile(profile)
            geometry = screen.availableGeometry()
            anchor = Rect(geometry.left(), geometry.top(), geometry.width(), geometry.height())
        target = profile_to_rect(profile, anchor)
        screen = QApplication.screenAt(QPoint(target.left, target.top)) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        target = clamp_rect(target, Rect(available.left(), available.top(), available.width(), available.height()))
        if not self._size_customized:
            # First layout: hug the two subtitle lines instead of stretching to the
            # (large) saved profile size, so the box starts small and tight. Width is
            # capped so an old wide profile doesn't spawn a huge box; height snaps to
            # the real two-line content measured at that width.
            width = min(target.width, 760)
            self.setGeometry(target.left, target.top, width, self.minimumHeight())
            content_height = max(self.minimumHeight(), self.sizeHint().height())
            target = Rect(target.left, target.top, width, content_height)
        self.setGeometry(target.left, target.top, target.width, target.height)

    def reset_size(self) -> None:
        """Restores automatic placement: re-fits content and re-enables follow.

        Used by "紧急恢复" so the box returns to its default position and size.
        """
        self._size_customized = False
        self._user_positioned = False

    def set_user_size(self, width: int, height: int) -> None:
        """Applies an exact box size coming from the settings panel.

        ``resize`` is clamped by Qt to the minimum/maximum set in
        ``_apply_size_limits``, so out-of-range input can never produce a broken box.
        """
        self._size_customized = True
        self._user_positioned = True
        self.resize(int(width), int(height))
        self.committed.emit()

    def save_profile(self) -> None:
        profile = self.active_profile()
        if self.settings.follow_player and self.player_rect and self.player_rect.width > 0 and self.player_rect.height > 0:
            anchor = self.player_rect
        else:
            screen = self.screen() or QApplication.primaryScreen()
            geometry = screen.availableGeometry()
            anchor = Rect(geometry.left(), geometry.top(), geometry.width(), geometry.height())
            profile.screen_name = screen.name()
        profile.x = (self.x() - anchor.left) / anchor.width
        profile.y = (self.y() - anchor.top) / anchor.height
        profile.w = self.width() / anchor.width
        profile.h = self.height() / anchor.height
        self.settings.profile = self.settings.windowed_profile

    def _screen_for_profile(self, profile):
        if profile.screen_name:
            found = next((screen for screen in QApplication.screens() if screen.name() == profile.screen_name), None)
            if found:
                return found
        return QApplication.primaryScreen()
