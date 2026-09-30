"""Generate resources/icon.ico.

Preferred source is ``resources/logo.png`` (the square brand logo), which is
scaled smoothly into every icon size. If that file is missing the original
dependency-free procedural design (rounded blue tile with two white subtitle
bars) is used as a fallback, so the build never breaks.
"""
from __future__ import annotations

import struct
from pathlib import Path

SIZES = (16, 32, 48, 64, 256)

# -- fallback (procedural) design: rounded blue tile with two white bars --------
BG = (37, 99, 235)      # RGB
FG = (255, 255, 255)    # RGB
RADIUS_RATIO = 0.22
BARS = (
    (0.22, 0.30, 0.78, 0.42),   # x0, y0, x1, y1 (fractions) - upper line
    (0.22, 0.56, 0.60, 0.68),   # lower, shorter line
)


def _inside_round_rect(x: int, y: int, size: int, radius: float) -> bool:
    if radius <= 0:
        return True
    cx = min(max(x + 0.5, radius), size - radius)
    cy = min(max(y + 0.5, radius), size - radius)
    dx, dy = x + 0.5 - cx, y + 0.5 - cy
    return dx * dx + dy * dy <= radius * radius


def render(size: int) -> list[list[tuple[int, int, int, int]]]:
    radius = size * RADIUS_RATIO
    rows: list[list[tuple[int, int, int, int]]] = []
    for y in range(size):
        row: list[tuple[int, int, int, int]] = []
        for x in range(size):
            if not _inside_round_rect(x, y, size, radius):
                row.append((0, 0, 0, 0))
                continue
            red, green, blue = BG
            fx, fy = (x + 0.5) / size, (y + 0.5) / size
            for bx0, by0, bx1, by1 in BARS:
                if bx0 <= fx <= bx1 and by0 <= fy <= by1:
                    red, green, blue = FG
                    break
            row.append((red, green, blue, 255))
        rows.append(row)
    return rows


def render_from_logo(logo_path: Path, size: int):
    """Smoothly scales the brand logo down to one icon size.

    Returns ``None`` when Qt (or the logo) is unavailable so the caller can fall
    back to the procedural design instead of failing the whole build.
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
    except Exception:  # pragma: no cover - PySide6 always present in this project
        return None
    image = QImage(str(logo_path))
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_ARGB32).scaled(
        size, size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    rows: list[list[tuple[int, int, int, int]]] = []
    for y in range(image.height()):
        row: list[tuple[int, int, int, int]] = []
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            row.append((color.red(), color.green(), color.blue(), color.alpha()))
        rows.append(row)
    return rows


def _image_chunk(size: int, rows) -> bytes:
    # BITMAPINFOHEADER: height doubles because the AND mask follows the pixels
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bytearray()
    for row in reversed(rows):            # ICO stores rows bottom-up
        for red, green, blue, alpha in row:
            pixels += bytes((blue, green, red, alpha))
    mask_row = ((size + 31) // 32) * 4    # 1bpp AND mask, 4-byte aligned rows
    return header + bytes(pixels) + bytes(mask_row * size)


def build_ico(target: Path, logo_path: Path | None = None) -> Path:
    chunks = []
    for size in SIZES:
        rows = render_from_logo(logo_path, size) if logo_path is not None else None
        if rows is None:
            rows = render(size)
        chunks.append((size, _image_chunk(size, rows)))
    header = struct.pack("<HHH", 0, 1, len(chunks))
    offset = 6 + 16 * len(chunks)
    entries = b""
    for size, data in chunks:
        entries += struct.pack(
            "<BBBBHHII",
            size % 256, size % 256, 0, 0, 1, 32, len(data), offset,
        )
        offset += len(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(header + entries + b"".join(data for _, data in chunks))
    return target


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    logo = root / "resources" / "logo.png"
    source = logo if logo.exists() else None
    path = build_ico(root / "resources" / "icon.ico", source)
    origin = "logo.png" if source else "procedural fallback"
    print(f"icon written: {path} ({path.stat().st_size} bytes, from {origin})")
