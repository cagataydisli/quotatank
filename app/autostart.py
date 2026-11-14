"""Start with Windows: a value under HKCU\\...\\Run (per user, no admin rights needed)."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # not Windows
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "QuotaTank"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "launch.py"
AUTOSTART_FLAG = "--autostart"


def _pythonw() -> str:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")   # no console window at login
    return str(windowless if windowless.exists() else exe)


def command() -> str:
    return f'"{_pythonw()}" "{LAUNCHER}" {AUTOSTART_FLAG}'


def is_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False
    return str(LAUNCHER).lower() in str(value).lower()


def set_enabled(enabled: bool) -> bool:
    """Add/remove the Run entry. Returns the resulting state."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
    return is_enabled()
