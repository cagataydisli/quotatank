"""System-wide keyboard shortcut (works while any other program is in front).

RegisterHotKey delivers WM_HOTKEY to the message queue of the thread that registered
it, so a small dedicated thread registers the key and waits for messages.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_ID = 0x5154  # "QT"

MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
NAMED_KEYS = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def parse(spec: str) -> tuple[int, int] | None:
    """'ctrl+shift+q' -> (modifiers, virtual key). None if it is not a usable shortcut."""
    parts = [p.strip().lower() for p in str(spec or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in MODIFIERS:
            return None
        mods |= MODIFIERS[p]
    key = parts[-1]
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    elif key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    else:
        return None
    if not mods:
        return None
    return mods, vk


class GlobalHotkey:
    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        self.spec = spec
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self.ok = False   # registered successfully (False: invalid or taken by another program)

    def start(self, timeout: float = 2.0) -> bool:
        parsed = parse(self.spec)
        if parsed is None:
            return False
        self._thread = threading.Thread(target=self._run, args=parsed, daemon=True, name="hotkey")
        self._thread.start()
        self._ready.wait(timeout)
        return self.ok

    def _run(self, mods: int, vk: int) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        self.ok = bool(user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk))
        self._ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    # window work happens elsewhere; keep this loop responsive
                    threading.Thread(target=self._safe_callback, daemon=True).start()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def _safe_callback(self) -> None:
        try:
            self._callback()
        except Exception:
            pass

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive() and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(1.0)
        self._thread = None
        self.ok = False
