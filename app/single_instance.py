"""Single-instance guard for Windows (named mutex + named "show" event).

The first instance owns the mutex and waits on the show event. A second launch
signals that event (so the running window comes to the front) and exits.
On non-Windows platforms the guard is a no-op.
"""

from __future__ import annotations

import os
import threading
from typing import Callable

MUTEX_NAME = "Local\\AIQuotaMonitor.Instance"
SHOW_EVENT_NAME = "Local\\AIQuotaMonitor.Show"

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002


class SingleInstance:
    def __init__(self) -> None:
        self._mutex = None
        self._event = None
        self.is_primary = True
        if os.name != "nt":
            return

        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateEventW.restype = wintypes.HANDLE
        k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k32.OpenEventW.restype = wintypes.HANDLE
        k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.SetEvent.argtypes = [wintypes.HANDLE]
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k32

        self._mutex = k32.CreateMutexW(None, False, MUTEX_NAME)
        if self._mutex and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self.is_primary = False
            return
        # auto-reset event, initially non-signalled
        self._event = k32.CreateEventW(None, False, False, SHOW_EVENT_NAME)

    def signal_primary(self) -> None:
        """Ask the already running instance to show its window."""
        if os.name != "nt":
            return
        handle = self._k32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
        if handle:
            self._k32.SetEvent(handle)
            self._k32.CloseHandle(handle)

    def listen(self, on_show: Callable[[], None], stop: threading.Event) -> None:
        """Start a daemon thread that calls ``on_show`` whenever a second launch happens."""
        if os.name != "nt" or not self._event:
            return

        def _loop() -> None:
            while not stop.is_set():
                # 1 s timeout so the thread notices shutdown; the wait itself is idle.
                if self._k32.WaitForSingleObject(self._event, 1000) == WAIT_OBJECT_0:
                    try:
                        on_show()
                    except Exception:
                        pass

        threading.Thread(target=_loop, name="single-instance", daemon=True).start()
