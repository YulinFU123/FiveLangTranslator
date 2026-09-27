from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer

from app.storage.config_store import ConfigRepository

CONFIG_KEY = "subtitle_visibility"
WRITE_DEBOUNCE_MS = 200
TOGGLE_DEBOUNCE_S = 0.1

logger = logging.getLogger(__name__)


class SubtitleVisibilityManager(QObject):
    """Owns the subtitle show/hide state and broadcasts changes on the bus.

    Decoupled from the hotkey and the window: the hotkey is merely one trigger,
    and the overlay subscribes to `subtitleVisibilityChanged` rather than being
    poked directly. Persistence is debounced so rapid toggles do not hammer IO.
    """

    def __init__(self, bus, config: ConfigRepository) -> None:
        super().__init__()
        self._bus = bus
        self._config = config
        self._visible = True
        self._last_toggle = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)

    def load(self) -> bool:
        """Reads persisted state. Call once at startup before wiring UI."""
        self._visible = self._config.get_bool(CONFIG_KEY, True)
        return self._visible

    def isVisible(self) -> bool:
        """Whether the subtitle window should currently be shown."""
        return self._visible

    def toggle(self) -> None:
        """Flips visibility. Debounced to ignore accidental double presses."""
        now = time.monotonic()
        if now - self._last_toggle < TOGGLE_DEBOUNCE_S:
            return
        self._last_toggle = now
        self.set_visible(not self._visible)

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._visible:
            return
        self._visible = visible
        # Broadcast first: overlay, tray menu and the control centre react now.
        # The database write is debounced to avoid IO on every toggle.
        self._bus.subtitleVisibilityChanged.emit(visible)
        self._timer.stop()
        self._timer.start(WRITE_DEBOUNCE_MS)

    def _flush(self) -> None:
        try:
            self._config.set_bool(CONFIG_KEY, self._visible)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("字幕显隐状态持久化失败，内存状态保持：%s", exc)
