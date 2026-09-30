from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from app.storage.database import Database, now_ms

# Placement of the two subtitle lines within the box.
LAYOUT_STACKED = "stacked"
LAYOUT_INLINE = "inline"
# User-facing layout modes from the style spec.
LAYOUT_DUAL_LINE = "dual-line"
LAYOUT_SINGLE_ALTERNATE = "single-alternate"

CINEMA_PRESET = "cinema"
MEETING_PRESET = "meeting"
READING_PRESET = "reading"


@dataclass(slots=True)
class OverlayAppearance:
    """Everything the subtitle box looks like. Stored in SQLite, never in QSettings.

    Field names double as the persisted `overlay_settings` keys (they are also the
    public config keys declared by the style spec, e.g. ``subtitle_font_family``).
    """

    # Spec config keys ------------------------------------------------------
    subtitle_font_family: str = "Microsoft YaHei UI"
    subtitle_font_weight: int = 400
    subtitle_font_size: int = 14
    subtitle_original_color: str = "#cbd5e1"
    subtitle_translation_color: str = "#ffffff"
    subtitle_bg_color: str = "rgba(8,12,20,0)"
    subtitle_layout_mode: str = LAYOUT_DUAL_LINE
    # True once the user edits a colour; then the Windows theme no longer overrides.
    subtitle_colors_customized: bool = False

    # Supporting fields (not part of the spec's key list but needed at runtime).
    show_source: bool = True
    topmost: bool = True
    anchor: str = ""
    preset: str = CINEMA_PRESET

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "OverlayAppearance":
        base = cls()
        for item in fields(cls):
            if item.name in (data or {}):
                setattr(base, item.name, (data or {})[item.name])
        return base

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# -- colour helpers ---------------------------------------------------------
_COLOR_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+)\s*)?\)",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3,8})$")


def parse_color(value: str | None, default: tuple[int, int, int, float] = (255, 255, 255, 1.0)) -> tuple[int, int, int, float]:
    """Parses ``#rgb``/``#rrggbb``/``#rrggbbaa`` or ``rgb()/rgba()`` into (r,g,b,a).

    ``a`` is normalised to 0..1 regardless of whether the source used 0..1 or
    0..255 notation. Returns ``default`` for anything unparseable.
    """
    if not value:
        return default
    text = str(value).strip()
    match = _HEX_RE.match(text)
    if match:
        hexdigits = match.group(1)
        if len(hexdigits) == 3:
            r = int(hexdigits[0] * 2, 16)
            g = int(hexdigits[1] * 2, 16)
            b = int(hexdigits[2] * 2, 16)
            a = 1.0
        elif len(hexdigits) == 4:
            r = int(hexdigits[0] * 2, 16)
            g = int(hexdigits[1] * 2, 16)
            b = int(hexdigits[2] * 2, 16)
            a = int(hexdigits[3] * 2, 16) / 255.0
        elif len(hexdigits) == 6:
            r = int(hexdigits[0:2], 16)
            g = int(hexdigits[2:4], 16)
            b = int(hexdigits[4:6], 16)
            a = 1.0
        else:  # 8 hex digits -> rgba
            r = int(hexdigits[0:2], 16)
            g = int(hexdigits[2:4], 16)
            b = int(hexdigits[4:6], 16)
            a = int(hexdigits[6:8], 16) / 255.0
        return (r, g, b, a)
    match = _COLOR_RE.match(text)
    if match:
        r = int(match.group(1))
        g = int(match.group(2))
        b = int(match.group(3))
        a_raw = match.group(4)
        if a_raw is None:
            a = 1.0
        else:
            a = float(a_raw)
            if a > 1.0:
                a = a / 255.0
        return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), max(0.0, min(1.0, a)))
    return default


def to_rgba_string(r: int, g: int, b: int, a: float) -> str:
    """Rounds back to the ``rgba(r,g,b,a)`` form stored in the database."""
    return f"rgba({int(r)},{int(g)},{int(b)},{round(float(a), 3)})"


def dim_color(value: str | None, factor: float = 0.6) -> str:
    """Returns a copy of ``value`` with its alpha scaled down (for draft text)."""
    r, g, b, a = parse_color(value)
    return to_rgba_string(r, g, b, a * factor)


PRESETS: dict[str, dict[str, Any]] = {
    CINEMA_PRESET: {
        "subtitle_font_family": "Microsoft YaHei UI",
        "subtitle_font_weight": 400,
        "subtitle_font_size": 14,
        "subtitle_original_color": "#cbd5e1",
        "subtitle_translation_color": "#ffffff",
        "subtitle_bg_color": "rgba(8,12,20,0)",
        "subtitle_layout_mode": LAYOUT_DUAL_LINE,
    },
    MEETING_PRESET: {
        "subtitle_font_family": "Microsoft YaHei UI",
        "subtitle_font_weight": 400,
        "subtitle_font_size": 16,
        "subtitle_original_color": "#dbeafe",
        "subtitle_translation_color": "#f8fafc",
        "subtitle_bg_color": "rgba(15,23,42,0)",
        "subtitle_layout_mode": LAYOUT_DUAL_LINE,
    },
    READING_PRESET: {
        "subtitle_font_family": "Microsoft YaHei UI",
        "subtitle_font_weight": 400,
        "subtitle_font_size": 18,
        "subtitle_original_color": "#a5b4fc",
        "subtitle_translation_color": "#e2e8f0",
        "subtitle_bg_color": "rgba(8,12,20,0)",
        "subtitle_layout_mode": LAYOUT_SINGLE_ALTERNATE,
    },
}

PRESET_ORDER = (CINEMA_PRESET, MEETING_PRESET, READING_PRESET)


def apply_preset(appearance: OverlayAppearance, preset: str) -> OverlayAppearance:
    values = PRESETS.get(preset)
    if not values:
        return appearance
    for key, value in values.items():
        setattr(appearance, key, value)
    appearance.preset = preset
    return appearance


class AppearanceRepository:
    """Key/value persistence for the subtitle box appearance."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def load(self) -> OverlayAppearance:
        rows = self.database.query("SELECT key, value FROM overlay_settings")
        data: dict[str, Any] = {}
        for row in rows:
            try:
                data[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                data[row["key"]] = row["value"]
        return OverlayAppearance.from_mapping(data)

    def save(self, appearance: OverlayAppearance) -> None:
        stamp = now_ms()
        payload = [(key, json.dumps(value, ensure_ascii=False), stamp)
                   for key, value in appearance.as_dict().items()]
        self.database.executemany(
            """
            INSERT INTO overlay_settings(key, value, updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            payload,
        )

    def clear(self) -> None:
        self.database.execute("DELETE FROM overlay_settings")
