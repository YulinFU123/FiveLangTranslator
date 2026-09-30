from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040


def is_windows() -> bool:
    return sys.platform == "win32"


def set_click_through(hwnd: int, enabled: bool) -> bool:
    if not is_windows():
        return False
    user32 = ctypes.windll.user32
    get_style = user32.GetWindowLongPtrW
    set_style = user32.SetWindowLongPtrW
    style = get_style(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_NOACTIVATE
    if enabled:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    set_style(hwnd, GWL_EXSTYLE, style)
    user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )
    return True


def set_always_on_top(hwnd: int, enabled: bool) -> bool:
    """Pins or unpins a window at the top of the Z order (HWND_TOPMOST).

    Qt only applies ``WindowStaysOnTopHint`` at the moment the flag is toggled, so
    any window that raises itself later (a video player entering fullscreen, a game
    launcher) can cover the subtitle. Re-asserting ``HWND_TOPMOST`` on a timer
    keeps it in front. Note: true exclusive-fullscreen (EFS) DirectX/OpenGL output
    bypasses the desktop compositor, so no top-level window can sit above it
    without in-process injection -- borderless/"fullscreen windowed" modes are
    covered fine.
    """
    if not is_windows():
        return False
    user32 = ctypes.windll.user32
    insert_after = HWND_TOPMOST if enabled else HWND_NOTOPMOST
    user32.SetWindowPos(
        hwnd, insert_after, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )
    return True


def move_window_native(hwnd: int, x: int, y: int) -> bool:
    """Moves a window without touching its size or Z order.

    Going straight to Win32 skips Qt's geometry machinery, which would otherwise
    invalidate the layout and re-composite the translucent surface on every single
    mouse move -- the main source of visible drag lag. Position updates therefore
    track the cursor at mouse rate.
    """
    if not is_windows():
        return False
    user32 = ctypes.windll.user32
    return bool(user32.SetWindowPos(
        hwnd, 0, int(x), int(y), 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
    ))


DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWCP_ROUND = 2

DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2         # Mica
DWMSBT_TRANSIENTWINDOW = 3    # Acrylic


def _set_dword(hwnd: int, attribute: int, value: int) -> bool:
    """Writes a DWORD window attribute through DwmSetWindowAttribute."""
    if not is_windows():
        return False
    try:
        payload = ctypes.c_int(int(value))
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attribute),
            ctypes.byref(payload), ctypes.sizeof(payload),
        )
        return result == 0
    except Exception:
        return False


def set_immersive_dark(hwnd: int, enabled: bool) -> bool:
    """Tints the native title bar to match a dark client area."""
    return _set_dword(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if enabled else 0)


def set_backdrop(hwnd: int, kind: int = DWMSBT_MAINWINDOW) -> bool:
    """Puts the Windows 11 Mica/Acrylic backdrop behind the window.

    For the effect to be visible the surface itself must stay translucent (Qt
    WA_TranslucentBackground + translucent stylesheet). On older Windows the call
    just fails and the translucent styling still reads as frosted glass.
    """
    return _set_dword(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, int(kind))


def set_rounded_corners(hwnd: int, enabled: bool = True) -> bool:
    return _set_dword(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND if enabled else 1)


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Reads the real on-screen rect (left, top, right, bottom) of a window.

    Used to resync Qt's cached geometry after a run of native moves.
    """
    if not is_windows():
        return None
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom
