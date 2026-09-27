"""Generate resources/version_info.txt from pyproject.toml.

Keeps the Windows file version a single source of truth (pyproject `version`).
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = """# UTF-8
#
# 由 scripts/make_version_info.py 自动生成，请勿手改。
#
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={prodvers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904b0',
        [StringStruct(u'CompanyName', u'FiveLangTranslator'),
        StringStruct(u'FileDescription', u'FiveLangTranslator - 实时双语悬浮字幕'),
        StringStruct(u'FileVersion', u'{version}'),
        StringStruct(u'InternalName', u'FiveLangTranslator'),
        StringStruct(u'LegalCopyright', u'Copyright (c) 2026 FiveLangTranslator'),
        StringStruct(u'OriginalFilename', u'FiveLangTranslator.exe'),
        StringStruct(u'ProductName', u'FiveLangTranslator'),
        StringStruct(u'ProductVersion', u'{version}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""


def _numeric(version: str) -> str:
    """'1.0.3' -> '(1, 0, 3, 0)' (Windows needs exactly 4 components)."""
    parts = [re.sub(r"\D", "", part) or "0" for part in version.split(".")]
    parts = [part for part in parts if part][:4]
    while len(parts) < 4:
        parts.append("0")
    return "(" + ", ".join(parts) + ")"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    match = re.search(
        r"^version\s*=\s*\"([^\"]+)\"", (root / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    version = match.group(1) if match else "0.0.0"
    filevers = _numeric(version)
    target = root / "resources" / "version_info.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        TEMPLATE.format(version=version, filevers=filevers, prodvers=filevers),
        encoding="utf-8",
    )
    print(f"version info written: {target} (version={version}, filevers={filevers})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
