from __future__ import annotations

import logging
import math

from PySide6.QtCore import QObject, QTimer

from app.player.layout import Rect, anchor_rect
from app.storage.config_store import ConfigRepository

CONFIG_KEY = "subtitle_anchor_point"
WRITE_DEBOUNCE_MS = 200
DEFAULT_ANCHOR = "bottom-center"
ANCHOR_MARGIN = 16  # Safe distance from the work-area edge; reserved for config.

# Canonical anchor enum (persisted verbatim) -> layout-module anchor key.
ANCHOR_POINTS = {
    "top-left": "tl", "top-center": "tc", "top-right": "tr",
    "middle-left": "ml", "center": "mc", "middle-right": "mr",
    "bottom-left": "bl", "bottom-center": "bc", "bottom-right": "br",
}
VALID_ANCHORS = set(ANCHOR_POINTS)

# (anchor, grid_row, grid_column) for the 3x3 picker, row-major from the top.
ANCHOR_ORDER = [
    ("top-left", 0, 0), ("top-center", 0, 1), ("top-right", 0, 2),
    ("middle-left", 1, 0), ("center", 1, 1), ("middle-right", 1, 2),
    ("bottom-left", 2, 0), ("bottom-center", 2, 1), ("bottom-right", 2, 2),
]

ANCHOR_TIPS = {
    "top-left": "左上角", "top-center": "顶部居中", "top-right": "右上角",
    "middle-left": "左侧居中", "center": "屏幕正中", "middle-right": "右侧居中",
    "bottom-left": "左下角", "bottom-center": "底部居中", "bottom-right": "右下角",
}

logger = logging.getLogger(__name__)


class SubtitleAnchorManager(QObject):
    """Owns the nine-point anchor selection for the subtitle window.

    Pure position intent: it never touches geometry itself. State changes are
    broadcast on the shared event bus (`subtitleAnchorChanged`) so the settings
    panel, the (future) tray menu and the overlay window all stay in sync. The
    overlay subscribes and does the actual math/`QPropertyAnimation`. Persistence
    is debounced so rapid clicks do not hammer the database.
    """

    def __init__(self, bus, config: ConfigRepository) -> None:
        super().__init__()
        self._bus = bus
        self._config = config
        self._anchor = DEFAULT_ANCHOR
        self._loaded_default = True
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)

    def load(self) -> str:
        """Reads persisted anchor. Call once at startup before wiring UI."""
        if self._config.exists(CONFIG_KEY):
            raw = self._config.get_string(CONFIG_KEY, DEFAULT_ANCHOR)
            if raw in VALID_ANCHORS:
                self._anchor = raw
                self._loaded_default = False
                return self._anchor
        self._anchor = DEFAULT_ANCHOR
        self._loaded_default = True
        return self._anchor

    def getAnchor(self) -> str:
        return self._anchor

    def used_default(self) -> bool:
        """True when no valid anchor was stored (first run)."""
        return self._loaded_default

    def setAnchor(self, anchor: str) -> None:
        if anchor not in VALID_ANCHORS:
            return
        if anchor == self._anchor:
            return
        self._anchor = anchor
        # Broadcast first: overlay repositions and the UI highlights immediately.
        self._bus.subtitleAnchorChanged.emit(anchor)
        self._timer.stop()
        self._timer.start(WRITE_DEBOUNCE_MS)

    def _flush(self) -> None:
        try:
            self._config.set_string(CONFIG_KEY, self._anchor)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("锚点持久化失败，内存状态保持：%s", exc)


# Tie-break when two anchors are effectively equidistant: 中下 > 左下 > 右下 > 其他.
_ANCHOR_PRIORITY = {"bottom-center": 3, "bottom-left": 2, "bottom-right": 1}


def nearest_anchor(center_x, center_y, screen: Rect, width, height) -> str:
    """Returns the anchor whose standard target position is closest to (cx, cy).

    Used by the double-click edge-snap: it picks the standard nine-grid position
    the box is already nearest to, so snapping lands exactly where a manual click
    would — identical geometry, persistence and highlight. Distances within half
    a pixel are treated as a tie and resolved by `_ANCHOR_PRIORITY`.
    """
    best = DEFAULT_ANCHOR
    best_distance = float("inf")
    best_priority = -1
    for anchor, layout_key in ANCHOR_POINTS.items():
        rect = anchor_rect(layout_key, screen, width, height, ANCHOR_MARGIN)
        cx = rect.left + rect.width / 2.0
        cy = rect.top + rect.height / 2.0
        distance = math.hypot(cx - center_x, cy - center_y)
        priority = _ANCHOR_PRIORITY.get(anchor, 0)
        if distance < best_distance - 0.5 or (
            abs(distance - best_distance) <= 0.5 and priority > best_priority
        ):
            best = anchor
            best_distance = distance
            best_priority = priority
    return best
