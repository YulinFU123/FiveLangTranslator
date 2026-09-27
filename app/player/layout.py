from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Rect:
    left: int
    top: int
    width: int
    height: int


def profile_to_rect(profile, anchor: Rect, minimum_width: int = 380, minimum_height: int = 110) -> Rect:
    return Rect(
        anchor.left + int(anchor.width * profile.x),
        anchor.top + int(anchor.height * profile.y),
        max(minimum_width, int(anchor.width * profile.w)),
        max(minimum_height, int(anchor.height * profile.h)),
    )


ANCHOR_KEYS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")

ANCHOR_LABELS = {
    "tl": "左上", "tc": "中上", "tr": "右上",
    "ml": "左中", "mc": "居中", "mr": "右中",
    "bl": "左下", "bc": "中下", "br": "右下",
}

EDGE_MARGIN = 12


def anchor_rect(anchor: str, screen: Rect, width: int, height: int, margin: int = EDGE_MARGIN) -> Rect:
    """Places a box of `width` x `height` on one of the nine screen anchors."""
    if anchor not in ANCHOR_KEYS:
        return Rect(screen.left + (screen.width - width) // 2,
                    screen.top + screen.height - height - margin, width, height)
    left = {
        "tl": screen.left + margin,
        "tc": screen.left + (screen.width - width) // 2,
        "tr": screen.left + screen.width - width - margin,
        "ml": screen.left + margin,
        "mc": screen.left + (screen.width - width) // 2,
        "mr": screen.left + screen.width - width - margin,
        "bl": screen.left + margin,
        "bc": screen.left + (screen.width - width) // 2,
        "br": screen.left + screen.width - width - margin,
    }[anchor]
    top = {
        "tl": screen.top + margin,
        "tc": screen.top + margin,
        "tr": screen.top + margin,
        "ml": screen.top + (screen.height - height) // 2,
        "mc": screen.top + (screen.height - height) // 2,
        "mr": screen.top + (screen.height - height) // 2,
        "bl": screen.top + screen.height - height - margin,
        "bc": screen.top + screen.height - height - margin,
        "br": screen.top + screen.height - height - margin,
    }[anchor]
    return Rect(int(left), int(top), width, height)


def snap_to_edge(rect: Rect, screen: Rect, margin: int = EDGE_MARGIN) -> Rect:
    """Moves a box flush to the screen edge it is closest to."""
    distances = {
        "left": rect.left - screen.left,
        "right": (screen.left + screen.width) - (rect.left + rect.width),
        "top": rect.top - screen.top,
        "bottom": (screen.top + screen.height) - (rect.top + rect.height),
    }
    edge = min(distances, key=lambda key: distances[key])
    if edge == "left":
        return Rect(screen.left + margin, max(screen.top, rect.top), rect.width, rect.height)
    if edge == "right":
        return Rect(screen.left + screen.width - rect.width - margin, max(screen.top, rect.top), rect.width, rect.height)
    if edge == "top":
        return Rect(max(screen.left, rect.left), screen.top + margin, rect.width, rect.height)
    return Rect(max(screen.left, rect.left), screen.top + screen.height - rect.height - margin, rect.width, rect.height)


def clamp_rect(rect: Rect, screen: Rect, visible_width: int = 90, visible_height: int = 45) -> Rect:
    x = max(screen.left - rect.width + visible_width, min(rect.left, screen.left + screen.width - visible_width))
    y = max(screen.top, min(rect.top, screen.top + screen.height - visible_height))
    return Rect(x, y, min(rect.width, screen.width), min(rect.height, screen.height))
