"""Small Win32 helpers for the compact widget window (placement, taskbar, corners).

Everything works in the coordinate space the process sees (physical pixels when
DPI-aware), and sizes are converted with the window's own DPI, so the result is
consistent on scaled and multi-monitor setups.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

# Private instances: setting argtypes on the shared ctypes.windll.user32 would break
# pywebview, which calls the same functions with different argument types.
user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MONITOR_DEFAULTTONEAREST = 2
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


def hwnd_of(window) -> int | None:
    """Native HWND of a pywebview window (WinForms backend)."""
    try:
        return int(window.native.Handle.ToInt64())
    except Exception:
        return None


def _scale(hwnd: int) -> float:
    try:
        dpi = user32.GetDpiForWindow(wintypes.HWND(hwnd))
        return dpi / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def work_area(ref_hwnd: int | None) -> tuple[int, int, int, int]:
    """Work area (excluding the taskbar) of the monitor holding ``ref_hwnd`` (or primary)."""
    mon = user32.MonitorFromWindow(wintypes.HWND(ref_hwnd or 0), MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def make_tool_window(hwnd: int) -> None:
    """No taskbar button and no Alt+Tab entry — it is a desktop widget."""
    h = wintypes.HWND(hwnd)
    style = user32.GetWindowLongW(h, GWL_EXSTYLE)
    user32.SetWindowLongW(h, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)


def round_corners(hwnd: int) -> None:
    """Windows 11 rounded corners for a frameless window (no-op on Windows 10)."""
    pref = ctypes.c_int(DWMWCP_ROUND)
    try:
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:
        pass


def place_bottom_right(hwnd: int, width: int, height: int, ref_hwnd: int | None, margin: int = 12) -> None:
    """Size the window (logical px) and dock it to the bottom-right of the work area."""
    left, top, right, bottom = work_area(ref_hwnd)
    s = _scale(ref_hwnd or hwnd)
    w, h, m = int(width * s), int(height * s), int(margin * s)
    x = max(left, right - w - m)
    y = max(top, bottom - h - m)
    user32.SetWindowPos(wintypes.HWND(hwnd), None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)


def bottom_right_logical(width: int, height: int, ref_hwnd: int | None, margin: int = 12) -> tuple[int, int]:
    """Logical x/y that docks a width×height window bottom-right (for create_window)."""
    left, top, right, bottom = work_area(ref_hwnd)
    s = _scale(ref_hwnd) if ref_hwnd else 1.0
    x = max(left / s, right / s - width - margin)
    y = max(top / s, bottom / s - height - margin)
    return int(x), int(y)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _on_some_monitor(x: int, y: int, w: int, h: int) -> bool:
    """True if a good part of the rect is on a connected monitor's work area."""
    probe = wintypes.RECT(x + w // 4, y + h // 4, x + w - w // 4, y + h - h // 4)
    user32.MonitorFromRect.restype = wintypes.HMONITOR
    mon = user32.MonitorFromRect(ctypes.byref(probe), 0)  # MONITOR_DEFAULTTONULL
    if not mon:
        return False
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return False
    r = info.rcWork
    return r.left <= probe.left and probe.right <= r.right and r.top <= probe.top and probe.bottom <= r.bottom


def dock_widget(
    hwnd: int, width: int, height: int, ref_hwnd: int | None, pos: tuple[int, int] | None = None
) -> None:
    """Turn an already shown frameless window into a desktop widget.

    WinForms re-applies its own styles and start position when it first shows a form,
    so this runs after that: hide → tool-window style (no taskbar button) → rounded
    corners → exact size/position → show again without stealing focus.
    """
    h = wintypes.HWND(hwnd)
    user32.ShowWindow(h, 0)  # SW_HIDE: the taskbar only re-reads the style on show
    make_tool_window(hwnd)
    round_corners(hwnd)
    placed = False
    if pos is not None:
        # Remembered spot — only if that monitor is still there (layout may have changed).
        s = _scale(ref_hwnd or hwnd)
        w, hh = int(width * s), int(height * s)
        if _on_some_monitor(pos[0], pos[1], w, hh):
            user32.SetWindowPos(h, None, pos[0], pos[1], w, hh, SWP_NOZORDER | SWP_NOACTIVATE)
            placed = True
    if not placed:
        place_bottom_right(hwnd, width, height, ref_hwnd)
    user32.ShowWindow(h, 4)  # SW_SHOWNOACTIVATE


def set_bounds_logical(hwnd: int, x: float, y: float, width: float, height: float) -> None:
    """Move + resize in one call (logical px, i.e. CSS screen coordinates)."""
    s = _scale(hwnd)
    user32.SetWindowPos(
        wintypes.HWND(hwnd), None,
        int(round(x * s)), int(round(y * s)), int(round(width * s)), int(round(height * s)),
        SWP_NOZORDER | SWP_NOACTIVATE,
    )


def resize_keep_bottom_right(hwnd: int, width: int, height: int) -> None:
    """Resize (logical px) while keeping the bottom-right corner where it is, inside the work area."""
    h_ = wintypes.HWND(hwnd)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(h_, ctypes.byref(rect)):
        return
    s = _scale(hwnd)
    w, h = int(width * s), int(height * s)
    left, top, right, bottom = work_area(hwnd)
    x = min(max(left, rect.right - w), right - w)
    y = min(max(top, rect.bottom - h), bottom - h)
    user32.SetWindowPos(h_, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)


WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x2
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]


def set_see_through(hwnd: int, opacity: int, click_through: bool) -> None:
    """Widget transparency (30..100 %) and click-through (mouse goes to the window below)."""
    h = wintypes.HWND(hwnd)
    alpha = int(round(max(20, min(100, opacity)) * 2.55))
    style = user32.GetWindowLongW(h, GWL_EXSTYLE)
    if alpha >= 255 and not click_through:
        new = style & ~(WS_EX_LAYERED | WS_EX_TRANSPARENT)
        if new != style:
            user32.SetWindowLongW(h, GWL_EXSTYLE, new)
        return
    new = style | WS_EX_LAYERED
    new = (new | WS_EX_TRANSPARENT) if click_through else (new & ~WS_EX_TRANSPARENT)
    if new != style:
        user32.SetWindowLongW(h, GWL_EXSTYLE, new)
    user32.SetLayeredWindowAttributes(h, 0, alpha, LWA_ALPHA)


user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]


def cursor_in_client_rect(hwnd: int, rect: tuple[float, float, float, float] | None, pad: float = 0) -> bool:
    """Is the mouse over ``rect`` (CSS px, relative to the window's top-left corner)?"""
    if rect is None:
        return False
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return False
    win = window_rect(hwnd)
    if win is None:
        return False
    s = _scale(hwnd)
    x, y, w, h = rect
    left = win[0] + (x - pad) * s
    top = win[1] + (y - pad) * s
    return left <= pt.x <= left + (w + 2 * pad) * s and top <= pt.y <= top + (h + 2 * pad) * s
