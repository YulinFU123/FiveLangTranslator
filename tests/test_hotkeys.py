"""Hotkey registration failure branch (Qt dependent, win32 path)."""

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for Qt objects")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.windows.hotkeys import (  # noqa: E402
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    GlobalHotkeyManager,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def test_register_failure_emits_failed_signal(qt_app, monkeypatch):
    """When RegisterHotKey returns 0 the manager must surface `failed` with the
    exact hotkey label so the UI can toast it."""
    manager = GlobalHotkeyManager(qt_app)
    received = []
    manager.failed.connect(received.append)
    monkeypatch.setattr("app.windows.hotkeys._win_register_hotkey", lambda *a, **k: 0)
    ok = manager.register(
        201, "H", lambda: None, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
    )
    assert ok is False
    assert received == ["全局快捷键注册失败：Ctrl+Alt+H"]
    manager.unregister_all()


def test_register_success_does_not_emit_failed(qt_app, monkeypatch):
    manager = GlobalHotkeyManager(qt_app)
    received = []
    manager.failed.connect(received.append)
    monkeypatch.setattr("app.windows.hotkeys._win_register_hotkey", lambda *a, **k: 1)
    ok = manager.register(
        202, "H", lambda: None, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
    )
    assert ok is True
    assert received == []
    manager.unregister_all()
