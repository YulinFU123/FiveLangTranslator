# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir (zip 免安装绿色版), windowed GUI app."""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden_imports = ["qasync", "numpy", "scipy"]
try:
    hidden_imports += collect_submodules("app")
except Exception:  # pragma: no cover - defensive
    pass

a = Analysis(
    ["app/main.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytestqt", "tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FiveLangTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # 首次链路验证保留控制台便于排错；发布前改为 False（纯 GUI，无黑窗）
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FiveLangTranslator",
)
