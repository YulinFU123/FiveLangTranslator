from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = ".five_lang_translator"
ENV_HOME = "FLT_HOME"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle instead of source."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Read-only program resources: _MEIPASS in a bundle, repo root in source."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """Writable per-user directory for models, whisper binaries, config and DB.

    Overridable with FLT_HOME so smoke tests can use an isolated location.
    """
    override = os.environ.get(ENV_HOME, "").strip()
    base = Path(override).expanduser() if override else Path.home() / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def models_dir() -> Path:
    path = data_root() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tools_dir() -> Path:
    path = data_root() / "tools" / "whisper.cpp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _has_content(directory: Path) -> bool:
    return directory.is_dir() and any(directory.iterdir())


def resource_root() -> Path:
    """Search root for ASR binaries and models.

    Prefers the writable user directory (where the downloader places assets) and
    falls back to the bundled program directory. Source mode uses the repo root,
    preserving the original development behaviour.
    """
    root = data_root()
    if _has_content(root / "models") or _has_content(root / "tools"):
        return root
    return app_root()
