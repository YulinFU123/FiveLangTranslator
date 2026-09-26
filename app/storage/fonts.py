from __future__ import annotations

from PySide6.QtGui import QFontDatabase

# Standard QFont weight levels (100..900, step 100) exposed to the user.
WEIGHT_LEVELS = (100, 200, 300, 400, 500, 600, 700, 800, 900)
WEIGHT_LABELS = {
    100: "100 · Thin",
    200: "200 · ExtraLight",
    300: "300 · Light",
    400: "400 · Regular",
    500: "500 · Medium",
    600: "600 · DemiBold",
    700: "700 · Bold",
    800: "800 · ExtraBold",
    900: "900 · Black",
}

# Windows fallback fonts used when a configured family is missing/unreadable.
SANS_FALLBACK = "Microsoft YaHei UI"
MONO_FALLBACK = "Consolas"


def list_font_families() -> tuple[list[str], list[str]]:
    """Returns ``(common, monospace)`` family lists grouped by pitch.

    Enumeration goes through ``QFontDatabase``, which on Windows is backed by the
    system DirectWrite font collection, so it reflects exactly what is installed.
    """
    families = QFontDatabase.families()
    common: list[str] = []
    monospace: list[str] = []
    for family in families:
        if QFontDatabase.isFixedPitch(family):
            monospace.append(family)
        else:
            common.append(family)
    return common, monospace


def supported_weights(family: str) -> list[int]:
    """Weights offered for ``family``.

    Qt synthesises the nearest available weight for any family, so the full
    standard range is always selectable; the chosen weight is downgraded to the
    closest supported one automatically when the family lacks it (e.g. a thin-only
    display font requested at 700). The UI surfaces this via a tooltip rather than
    hiding options, which would be misleading on Windows where most fonts report a
    single ``Regular`` style.
    """
    return list(WEIGHT_LEVELS)


def resolve_family(family: str | None) -> str:
    """Picks a usable family, falling back to the Windows system fonts."""
    if family and family.strip():
        return family
    return SANS_FALLBACK


def is_monospace(family: str) -> bool:
    return QFontDatabase.isFixedPitch(family)
