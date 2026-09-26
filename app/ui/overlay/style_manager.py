from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QTimer

from app.storage.appearance import (
    LAYOUT_DUAL_LINE, LAYOUT_SINGLE_ALTERNATE, OverlayAppearance,
)
from app.storage.fonts import MONO_FALLBACK, SANS_FALLBACK
from app.ui.theme import system_prefers_light

logger = logging.getLogger(__name__)

# Keys that, once the user edits them, lock the colour set so a later Windows
# theme switch no longer overrides the user's explicit choice.
_COLOR_KEYS = ("subtitle_original_color", "subtitle_translation_color", "subtitle_bg_color")

# Default palettes applied when following the Windows light/dark theme.
_THEME_DEFAULTS = {
    True: {  # light
        "subtitle_original_color": "#1f2937",
        "subtitle_translation_color": "#0f172a",
        "subtitle_bg_color": "rgba(255,255,255,0.80)",
    },
    False: {  # dark
        "subtitle_original_color": "#cbd5e1",
        "subtitle_translation_color": "#ffffff",
        "subtitle_bg_color": "rgba(8,12,20,0.76)",
    },
}

STYLE_PERSIST_DEBOUNCE_MS = 300


class SubtitleStyleManager(QObject):
    """Owns the subtitle box style: state, debounced SQLite persistence, events.

    The overlay and the settings panel both read/write through this single owner,
    so a change from either side fans out through ``subtitleStyleChanged`` and the
    whole UI + the floating box stay in sync. Persistence is debounced 300ms so a
    slider drag does not hammer SQLite, and the value survives a restart.
    """

    def __init__(self, bus, repository) -> None:
        super().__init__()
        self.bus = bus
        self.repository = repository
        self.appearance = repository.load()
        # Normalise legacy layout values to the two spec modes. "stacked" maps to
        # dual-line; "inline" is no longer exposed in the UI, so it collapses to
        # dual-line too, keeping the placement logic simple and predictable.
        if self.appearance.subtitle_layout_mode not in (LAYOUT_DUAL_LINE, LAYOUT_SINGLE_ALTERNATE):
            self.appearance.subtitle_layout_mode = LAYOUT_DUAL_LINE
        self._theme_locked = self.appearance.subtitle_colors_customized
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush)

    def get_style(self) -> OverlayAppearance:
        return self.appearance

    def update(self, **changes: Any) -> None:
        """Merges changes, persists (debounced) and broadcasts the new style."""
        dirty = False
        for key, value in changes.items():
            if not hasattr(self.appearance, key):
                logger.warning("未知样式字段，已忽略：%s", key)
                continue
            if getattr(self.appearance, key) != value:
                setattr(self.appearance, key, value)
                dirty = True
                if key in _COLOR_KEYS:
                    self._theme_locked = True
                    self.appearance.subtitle_colors_customized = True
        if dirty:
            self._schedule_save()
            self.bus.subtitleStyleChanged.emit(self.appearance)

    def load(self) -> None:
        """Reads persisted state back (used on startup)."""
        self.appearance = self.repository.load()
        self.bus.subtitleStyleChanged.emit(self.appearance)

    def apply_theme_defaults(self, light: bool | None = None) -> None:
        """Follows the Windows theme for colours unless the user customised them."""
        if self._theme_locked:
            return
        light = system_prefers_light() if light is None else bool(light)
        for key, value in _THEME_DEFAULTS[bool(light)].items():
            setattr(self.appearance, key, value)
        self._schedule_save()
        self.bus.subtitleStyleChanged.emit(self.appearance)

    def reset_colors_to_theme(self, light: bool | None = None) -> None:
        """Explicitly re-enables theme following (used by a "follow system" toggle)."""
        self._theme_locked = False
        self.appearance.subtitle_colors_customized = False
        self.apply_theme_defaults(light)

    def _schedule_save(self) -> None:
        self._save_timer.start(STYLE_PERSIST_DEBOUNCE_MS)

    def _flush(self) -> None:
        try:
            self.repository.save(self.appearance)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("字幕样式持久化失败，内存状态保持：%s", exc)


def safe_family(family: str | None) -> str:
    """Resolves a possibly-missing font family to a Windows fallback."""
    return family.strip() if (family and family.strip()) else SANS_FALLBACK


def safe_mono_family(family: str | None) -> str:
    return family.strip() if (family and family.strip()) else MONO_FALLBACK
