@echo off
REM 使用项目自带的 .venv（已含 PySide6 等全部依赖）。系统 python 没有 PySide6，裸 python 会 ModuleNotFoundError。
SETLOCAL
SET "ROOT=%~dp0"
IF EXIST "%ROOT%.venv\Scripts\python.exe" (
    "%ROOT%.venv\Scripts\python.exe" -m app.main
) ELSE (
    python -m app.main
)
