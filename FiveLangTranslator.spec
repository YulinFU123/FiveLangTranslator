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
    # scipy 未被 app/ 引用，排除可省下约 20MB 的 OpenBLAS
    excludes=["pytest", "pytestqt", "tkinter", "scipy"],
    noarchive=False,
    optimize=0,
)

# 精简体积（目标 <=150MB）：
#  - opengl32sw.dll：Mesa 软件 OpenGL，纯 Widgets 应用不需要（约 20MB）
#  - PySide6/translations：Qt 自带 .qm 翻译，本项目未使用（约 6.7MB）
a.binaries = [b for b in a.binaries if "opengl32sw" not in b[0].lower()]
a.datas = [d for d in a.datas if "translations" not in d[0].lower()]

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
