from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass(slots=True)
class PlayerWindowState:
    hwnd: int = 0
    title: str = ""
    process_name: str = ""
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    minimized: bool = False
    fullscreen: bool = False

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


class PlayerWindowTracker(QObject):
    changed = Signal(object)

    def __init__(self, overlay_hwnd_getter, interval_ms: int = 500) -> None:
        super().__init__()
        self.overlay_hwnd_getter = overlay_hwnd_getter
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.poll)
        self.last = PlayerWindowState()

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def poll(self) -> None:
        state = foreground_window_state(self.overlay_hwnd_getter())
        if state and state != self.last:
            self.last = state
            self.changed.emit(state)


def foreground_window_state(overlay_hwnd: int = 0) -> PlayerWindowState | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    hwnd = int(user32.GetForegroundWindow())
    if not hwnd or hwnd == overlay_hwnd:
        return None
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if int(process_id.value) == os.getpid():
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    minimized = bool(user32.IsIconic(hwnd))
    monitor = user32.MonitorFromWindow(hwnd, 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    tolerance = 3
    fullscreen = (
        abs(rect.left - info.rcMonitor.left) <= tolerance
        and abs(rect.top - info.rcMonitor.top) <= tolerance
        and abs(rect.right - info.rcMonitor.right) <= tolerance
        and abs(rect.bottom - info.rcMonitor.bottom) <= tolerance
    )
    return PlayerWindowState(
        hwnd, buffer.value, "", rect.left, rect.top, rect.right, rect.bottom,
        minimized, fullscreen,
    )


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", ctypes.c_ulong),
    ]
