from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer

from app.storage.config_store import ConfigRepository

CONFIG_KEY = "subtitle_always_on_top"
WRITE_DEBOUNCE_MS = 200

logger = logging.getLogger(__name__)


class WindowTopmostManager(QObject):
    """Owns the always-on-top state for the subtitle window.

    Pure state control: it never touches rendering, layout or geometry. State
    changes are broadcast on the shared event bus (`topmost_changed`) so the
    settings panel, the tray menu and the overlay window all stay in sync
    without poking each other directly. Persistence is debounced so rapid
    toggles do not hammer the database.

    Instantiated once by the runtime; treat it as a singleton.
    """

    def __init__(self, bus, config: ConfigRepository) -> None:
        super().__init__()
        self._bus = bus
        self._config = config
        self._topmost = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)

    def load(self) -> bool:
        """Reads persisted state. Call once during startup before wiring UI."""
        self._topmost = self._config.get_bool(CONFIG_KEY, False)
        return self._topmost

    @property
    def isTopmost(self) -> bool:
        """Unified state query used by other modules (anchors, hotkeys, ...)."""
        return self._topmost

    def set_topmost(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._topmost:
            return
        self._topmost = enabled
        # Broadcast first: UI and overlay react immediately. Persist is debounced.
        self._bus.topmost_changed.emit(enabled)
        self._timer.stop()
        self._timer.start(WRITE_DEBOUNCE_MS)

    def _flush(self) -> None:
        try:
            self._config.set_bool(CONFIG_KEY, self._topmost)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("置顶状态持久化失败，内存状态保持：%s", exc)
