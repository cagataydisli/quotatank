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


# ---------------------------------------------------------------- taskbar docking
#
# The compact widget can sit *on* the taskbar. It is not re-parented into Explorer
# (a cross-process child window shares its input queue with Explorer and freezes
# with it); it is a topmost tool window placed over the taskbar instead, and kept
# above it when the taskbar raises itself.

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
GW_HWNDPREV = 3
DWMWCP_DONOTROUND = 1
ABM_GETSTATE = 0x4
ABS_AUTOHIDE = 0x1

user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, 64)
    return buf.value


def _monitor_of(hwnd: int):
    return user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)


def monitor_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    mon = _monitor_of(hwnd)
    if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return None
    r = info.rcMonitor
    return r.left, r.top, r.right, r.bottom


def taskbars() -> list[int]:
    """Primary taskbar first, then the taskbars of the other monitors."""
    out: list[int] = []
    primary = user32.FindWindowW("Shell_TrayWnd", None)
    if primary:
        out.append(int(primary))
    h = None
    while True:
        h = user32.FindWindowExW(None, h, "Shell_SecondaryTrayWnd", None)
        if not h:
            break
        out.append(int(h))
    return out


def taskbar_for(hwnd: int | None) -> int | None:
    """The taskbar on the monitor holding ``hwnd`` (or the primary one)."""
    bars = taskbars()
    if not bars:
        return None
    if hwnd:
        mon = _monitor_of(hwnd)
        for tb in bars:
            if _monitor_of(tb) == mon:
                return tb
    return bars[0]


def taskbar_autohide() -> bool:
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    try:
        return bool(ctypes.windll.shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(abd)) & ABS_AUTOHIDE)
    except Exception:
        return False


def tray_area_left(taskbar: int) -> int | None:
    """Left edge of the notification area (clock, tray icons) on the primary taskbar."""
    child = user32.FindWindowExW(wintypes.HWND(taskbar), None, "TrayNotifyWnd", None)
    if not child:
        return None
    r = window_rect(int(child))
    return r[0] if r and r[2] > r[0] else None


def set_rect(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    """Move + resize in physical px."""
    user32.SetWindowPos(wintypes.HWND(hwnd), None, int(x), int(y), int(w), int(h), SWP_NOZORDER | SWP_NOACTIVATE)


def is_covered_by(hwnd: int, other: int, limit: int = 400) -> bool:
    """True if ``other`` is above ``hwnd`` in the z-order."""
    h = user32.GetWindow(wintypes.HWND(hwnd), GW_HWNDPREV)
    while h and limit > 0:
        if int(h) == other:
            return True
        h = user32.GetWindow(h, GW_HWNDPREV)
        limit -= 1
    return False


def raise_topmost(hwnd: int) -> None:
    user32.SetWindowPos(
        wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )


def fullscreen_foreground(mon: tuple[int, int, int, int]) -> bool:
    """A game / video / presentation covers the whole monitor (taskbar included)."""
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    if _class_name(int(fg)) in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False
    r = window_rect(int(fg))
    if r is None:
        return False
    return r[0] <= mon[0] and r[1] <= mon[1] and r[2] >= mon[2] and r[3] >= mon[3]


DWMWA_BORDER_COLOR = 34
DWMWA_COLOR_NONE = 0xFFFFFFFE
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF


def set_square_corners(hwnd: int, square: bool) -> None:
    """Docked look: square corners and no Windows 11 window outline (or back to normal)."""
    pref = ctypes.c_int(DWMWCP_DONOTROUND if square else DWMWCP_ROUND)
    border = wintypes.DWORD(DWMWA_COLOR_NONE if square else DWMWA_COLOR_DEFAULT)
    try:
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref)
        )
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_BORDER_COLOR, ctypes.byref(border), ctypes.sizeof(border)
        )
    except Exception:
        pass


def is_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))


def show_no_activate(hwnd: int, show: bool) -> None:
    user32.ShowWindow(wintypes.HWND(hwnd), 4 if show else 0)  # SW_SHOWNOACTIVATE / SW_HIDE


def scale_of(hwnd: int) -> float:
    return _scale(hwnd)


gdi32 = ctypes.WinDLL("gdi32")
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
SRCCOPY = 0x00CC0020


def _screen_row(x: int, y: int, width: int) -> bytes | None:
    """One row of screen pixels as BGRA bytes (GDI, well under a millisecond)."""
    screen = user32.GetDC(None)
    if not screen:
        return None
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, width, 1)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, width, 1, screen, x, y, SRCCOPY):
            return None
        # BITMAPINFOHEADER: 32-bit, top-down, uncompressed
        header = (ctypes.c_uint32 * 10)()
        header[0] = 40
        header[1] = width
        header[2] = ctypes.c_uint32(-1).value
        header[3] = 1 | (32 << 16)
        buf = ctypes.create_string_buffer(width * 4)
        gdi32.SelectObject(mem, old)
        if not gdi32.GetDIBits(mem, bmp, 0, 1, buf, ctypes.byref(header), 0):
            return None
        return buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


def taskbar_color(tb_rect: tuple[int, int, int, int], skip: tuple[int, int] | None = None) -> str | None:
    """Dominant colour of the taskbar (one pixel row near its bottom), as "#rrggbb".

    With transparency effects it follows the wallpaper, so the docked bar samples it
    instead of guessing. ``skip`` is an x range to ignore (the bar itself).
    """
    left, top, right, bottom = tb_rect
    width = right - left
    if width <= 0:
        return None
    raw = _screen_row(left, bottom - 3, width)
    if not raw:
        return None
    counts: dict[bytes, int] = {}
    for i in range(0, width, 2):
        if skip and skip[0] <= left + i < skip[1]:
            continue
        px = raw[i * 4:i * 4 + 3]
        counts[px] = counts.get(px, 0) + 1
    if not counts:
        return None
    b, g, r = max(counts, key=counts.get)
    return f"#{r:02x}{g:02x}{b:02x}"
