from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from PySide6.QtCore import QObject, QPropertyAnimation, Signal

from app.core.events import EventBus
from app.storage.appearance import parse_color
from app.storage.config_store import ConfigRepository
from app.storage.presets import (
    ACTIVE_PRESET_KEY, CUSTOM_PRESET_ID, SUBTITLE_PRESETS, PresetSpec,
)

logger = logging.getLogger(__name__)


class SubtitlePresetManager(QObject):
    """Applies the cinema / meeting / reading scenario presets in one shot.

    Zero intrusion: it never reaches into the style / anchor / topmost managers'
    internals — it only calls their public ``update`` / ``setAnchor`` /
    ``set_topmost`` setters and listens on the shared event bus. When the user
    manually edits any configuration the active preset is dropped to ``custom``
    (detected by comparing the live state against the active preset's spec).
    """

    # Emitted with the now-active preset id ("cinema" / "meeting" / "reading" / "custom").
    presetApplied = Signal(str)

    def __init__(self, bus: EventBus, style_manager, anchor_manager,
                 topmost_manager, config: ConfigRepository, overlay=None) -> None:
        super().__init__()
        self.bus = bus
        self.style_manager = style_manager
        self.anchor_manager = anchor_manager
        self.topmost_manager = topmost_manager
        self.config = config
        self.overlay = overlay
        self._applying = False
        stored = config.get_string(ACTIVE_PRESET_KEY, CUSTOM_PRESET_ID)
        self._active = (
            stored if stored in SUBTITLE_PRESETS or stored == CUSTOM_PRESET_ID
            else CUSTOM_PRESET_ID
        )
        bus.subtitleStyleChanged.connect(self._on_style_changed)
        bus.subtitleAnchorChanged.connect(self._on_anchor_changed)
        bus.topmost_changed.connect(self._on_topmost_changed)

    def available(self) -> list[str]:
        return list(SUBTITLE_PRESETS)

    def current(self) -> str:
        return self._active

    def apply(self, preset_id: str) -> None:
        spec = SUBTITLE_PRESETS.get(preset_id)
        if spec is None:
            logger.warning("未知预设，已忽略：%s", preset_id)
            return
        snapshot = self._snapshot()
        self._applying = True
        try:
            self.style_manager.update(**spec.style)
            self.anchor_manager.setAnchor(spec.anchor)
            self.topmost_manager.set_topmost(spec.topmost)
        except Exception as exc:  # pragma: no cover - defensive rollback
            logger.error("应用预设失败，回滚至上一状态：%s", exc)
            self._restore(snapshot)
            return
        finally:
            self._applying = False
        self._set_active(preset_id)
        self._pulse()

    def mark_custom(self) -> None:
        if self._active != CUSTOM_PRESET_ID:
            self._set_active(CUSTOM_PRESET_ID)

    # -- internals ----------------------------------------------------------
    def _set_active(self, preset_id: str) -> None:
        if self._active == preset_id:
            self.presetApplied.emit(preset_id)
            return
        self._active = preset_id
        try:
            self.config.set_string(ACTIVE_PRESET_KEY, preset_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("预设状态持久化失败，内存状态保持：%s", exc)
        self.presetApplied.emit(preset_id)

    def _snapshot(self):
        return (
            asdict(self.style_manager.get_style()),
            self.anchor_manager.getAnchor(),
            self.topmost_manager.isTopmost,
        )

    def _restore(self, snapshot) -> None:
        appearance, anchor, topmost = snapshot
        self._applying = True
        try:
            self.style_manager.update(**appearance)
            self.anchor_manager.setAnchor(anchor)
            self.topmost_manager.set_topmost(topmost)
        finally:
            self._applying = False

    def _matches(self, preset_id: str) -> bool:
        spec: Optional[PresetSpec] = SUBTITLE_PRESETS.get(preset_id)
        if spec is None:
            return False
        a = self.style_manager.get_style()
        s = spec.style
        checks = (
            a.subtitle_font_family == s["subtitle_font_family"],
            a.subtitle_font_weight == s["subtitle_font_weight"],
            a.subtitle_font_size == s["subtitle_font_size"],
            a.subtitle_layout_mode == s["subtitle_layout_mode"],
            self._color_eq(a.subtitle_original_color, s["subtitle_original_color"]),
            self._color_eq(a.subtitle_translation_color, s["subtitle_translation_color"]),
            self._color_eq(a.subtitle_bg_color, s["subtitle_bg_color"]),
            self.anchor_manager.getAnchor() == spec.anchor,
            self.topmost_manager.isTopmost == spec.topmost,
        )
        return all(checks)

    @staticmethod
    def _color_eq(left: str, right: str) -> bool:
        return parse_color(left) == parse_color(right)

    def _on_style_changed(self, _appearance=None) -> None:
        if self._applying or self._active == CUSTOM_PRESET_ID:
            return
        if not self._matches(self._active):
            self.mark_custom()

    def _on_anchor_changed(self, _anchor=None) -> None:
        if self._applying or self._active == CUSTOM_PRESET_ID:
            return
        if not self._matches(self._active):
            self.mark_custom()

    def _on_topmost_changed(self, _enabled=None) -> None:
        if self._applying or self._active == CUSTOM_PRESET_ID:
            return
        if not self._matches(self._active):
            self.mark_custom()

    def _pulse(self) -> None:
        """A brief 200ms opacity dip on the floating box = a smooth "applied" cue."""
        if self.overlay is None:
            return
        try:
            animation = QPropertyAnimation(self.overlay, b"windowOpacity")
            animation.setDuration(200)
            animation.setStartValue(0.7)
            animation.setEndValue(1.0)
            animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        except Exception:  # pragma: no cover - depends on the display stack
            pass
