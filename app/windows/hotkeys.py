from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK = {
    "E": 0x45, "L": 0x4C, "P": 0x50,
    "O": 0x4F, "R": 0x52, "D": 0x44,
    "H": 0x48,
}


class GlobalHotkeyFilter(QObject, QAbstractNativeEventFilter):
    activated = Signal(int)

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY:
            self.activated.emit(int(msg.wParam))
            return True, 0
        return False, 0


class GlobalHotkeyManager(QObject):
    failed = Signal(str)

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.filter = GlobalHotkeyFilter()
        self.callbacks = {}
        self.registered = set()
        self.app.installNativeEventFilter(self.filter)
        self.filter.activated.connect(self._dispatch)

    def register(self, hotkey_id: int, key: str, callback, modifiers=MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT) -> bool:
        self.callbacks[hotkey_id] = callback
        if sys.platform != "win32":
            return False
        ok = bool(ctypes.windll.user32.RegisterHotKey(None, hotkey_id, modifiers, VK[key]))
        if ok:
            self.registered.add(hotkey_id)
        else:
            parts = []
            if modifiers & MOD_CONTROL:
                parts.append("Ctrl")
            if modifiers & MOD_ALT:
                parts.append("Alt")
            if modifiers & MOD_SHIFT:
                parts.append("Shift")
            self.failed.emit("全局快捷键注册失败：" + "+".join(parts + [key]))
        return ok

    def unregister_all(self) -> None:
        if sys.platform == "win32":
            for hotkey_id in tuple(self.registered):
                ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self.registered.clear()
        self.callbacks.clear()
        self.app.removeNativeEventFilter(self.filter)

    def _dispatch(self, hotkey_id: int) -> None:
        callback = self.callbacks.get(hotkey_id)
        if callback:
            callback()
