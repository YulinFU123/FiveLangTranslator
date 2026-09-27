from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.storage.appearance import LAYOUT_DUAL_LINE

# Active preset identifier persisted under this key in the shared key-value store.
ACTIVE_PRESET_KEY = "subtitle_active_preset"
CUSTOM_PRESET_ID = "custom"


@dataclass(frozen=True)
class PresetSpec:
    """A complete, one-click subtitle configuration scenario.

    ``style`` holds every field accepted by ``SubtitleStyleManager.update``; the
    anchor and topmost are applied through their own managers. The spec is pure
    data so adding a preset never touches manager or UI code.
    """

    id: str
    title: str
    subtitle: str
    style: dict[str, Any]
    anchor: str
    topmost: bool


def _style(family: str, weight: int, size: int, original: str, translation: str,
           bg: str) -> dict[str, Any]:
    return {
        "subtitle_font_family": family,
        "subtitle_font_weight": weight,
        "subtitle_font_size": size,
        "subtitle_original_color": original,
        "subtitle_translation_color": translation,
        "subtitle_bg_color": bg,
        "subtitle_layout_mode": LAYOUT_DUAL_LINE,
    }


# Font family names use the actual Windows family Qt resolves ("Microsoft YaHei UI"
# is the 微软雅黑 UI face; "Consolas" is the monospace face). The style manager's
# safe_family() fallback covers the rare case a face is missing.
SUBTITLE_PRESETS: dict[str, PresetSpec] = {
    "cinema": PresetSpec(
        id="cinema",
        title="影院",
        subtitle="大字号 · 金色译文 · 底部居中",
        style=_style("Microsoft YaHei UI", 700, 20, "#FFFFFF", "#FFD700", "rgba(0,0,0,0.8)"),
        anchor="bottom-center",
        topmost=True,
    ),
    "meeting": PresetSpec(
        id="meeting",
        title="会议",
        subtitle="常规字重 · 浅绿译文 · 顶部居中",
        style=_style("Microsoft YaHei UI", 400, 16, "#FFFFFF", "#90EE90", "rgba(0,0,0,0.6)"),
        anchor="top-center",
        topmost=True,
    ),
    "reading": PresetSpec(
        id="reading",
        title="阅读",
        subtitle="等宽字体 · 浅蓝译文 · 左下角",
        style=_style("Consolas", 400, 18, "#E0E0E0", "#B0C4DE", "rgba(26,26,26,0.7)"),
        anchor="bottom-left",
        topmost=False,
    ),
}

# Stable display order for the preset picker.
SUBTITLE_PRESET_ORDER = ("cinema", "meeting", "reading")
