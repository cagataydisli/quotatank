"""Windows notifications: quota running low, and a low quota being renewed.

Each warning is sent once per quota window cycle (keyed by its reset time), and that
memory is kept on disk so restarting the app does not repeat old warnings.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable

from app.insights import effective_remaining, reset_passed
from app.paths import get_app_data_dir, read_json_safe, write_json_atomic

TOOL_NAMES = {"claude": "Claude Code", "codex": "Codex", "antigravity": "Antigravity"}

# A renewal is only worth telling about if the quota had really been used.
RESET_NOTIFY_BELOW = 50.0
# ...and only shortly after it happened (not for a reset hours ago, e.g. after a reboot).
RESET_NOTIFY_WITHIN = 30 * 60
KEEP_SENT_SEC = 9 * 86400


def _state_path():
    return get_app_data_dir() / "notify_state.json"


def _cycle(resets_at: Any) -> str:
    return str(int(resets_at) // 600) if isinstance(resets_at, (int, float)) else "-"


def _when(epoch: Any, now: float) -> str:
    """'2 sa 15 dk sonra (14:30)' style reset time."""
    if not isinstance(epoch, (int, float)):
        return ""
    diff = int(epoch - now)
    if diff <= 0:
        return "şimdi"
    d, h, m = diff // 86400, (diff % 86400) // 3600, (diff % 3600) // 60
    if d:
        rel = f"{d} gün {h} sa"
    elif h:
        rel = f"{h} sa {m} dk"
    else:
        rel = f"{max(1, m)} dk"
    at = datetime.fromtimestamp(epoch).strftime("%H:%M")
    return f"{rel} sonra ({at})"


class Notifier:
    def __init__(self, send: Callable[[str, str], None]) -> None:
        self._send = send
        self._lock = threading.Lock()
        raw = read_json_safe(_state_path()) or {}
        self._sent: dict[str, float] = raw.get("sent") if isinstance(raw.get("sent"), dict) else {}
        # (provider, window key) -> last seen {"resets_at", "remaining", "label", "pid"}
        self._watch: dict[tuple[str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------ helpers

    def _mark(self, key: str, now: float) -> bool:
        """Record a notification; False if it was already sent."""
        if key in self._sent:
            return False
        self._sent[key] = now
        return True

    def _save(self, now: float) -> None:
        self._sent = {k: t for k, t in self._sent.items() if now - t < KEEP_SENT_SEC}
        try:
            write_json_atomic(_state_path(), {"sent": self._sent})
        except Exception:
            pass

    def _flush(self, out: list[tuple[str, str]]) -> None:
        """A tray icon shows one notification at a time: several become one."""
        if not out:
            return
        if len(out) == 1:
            title, message = out[0]
        else:
            title = f"QuotaTank: {len(out)} kota uyarısı"
            message = "\n".join(t for t, _ in out)
        try:
            self._send(title, message)
        except Exception:
            pass

    # ------------------------------------------------------------ checks

    def check(self, dashboard: dict[str, Any], now: float | None = None) -> None:
        """Warn about quotas that dropped to the user's thresholds (new data arrived)."""
        now = time.time() if now is None else now
        alerts = dashboard.get("alerts") or {}
        enabled = bool(alerts.get("enabled", True))
        levels = sorted({int(alerts.get("threshold", 20)), int(alerts.get("critical", 10))}, reverse=True)
        out: list[tuple[str, str]] = []
        with self._lock:
            for p in dashboard.get("providers") or []:
                pid = p.get("id")
                for w in p.get("windows") or []:
                    if w.get("disabled") or not w.get("key"):
                        continue
                    rem = effective_remaining(w, now)
                    self._watch[(pid, w["key"])] = {
                        "pid": pid,
                        "label": w.get("label") or w["key"],
                        "resets_at": w.get("resets_at"),
                        "remaining": rem,
                    }
                    if not enabled or reset_passed(w.get("resets_at"), now):
                        continue
                    hit = [lv for lv in levels if rem <= lv]
                    if not hit:
                        continue
                    cycle = _cycle(w.get("resets_at"))
                    fresh = [lv for lv in hit if self._mark(f"low:{pid}:{w['key']}:{cycle}:{lv}", now)]
                    if not fresh:
                        continue
                    critical = min(hit) == levels[-1] and len(levels) > 1 or rem <= 0
                    tool = TOOL_NAMES.get(pid, pid)
                    if rem <= 0:
                        title = f"{tool}: {w.get('label')} bitti"
                    elif critical:
                        title = f"{tool}: {w.get('label')} bitmek üzere — %{round(rem)}"
                    else:
                        title = f"{tool}: {w.get('label')} azaldı — %{round(rem)}"
                    reset = _when(w.get("resets_at"), now)
                    msg = f"Kalan: %{round(rem)}."
                    if reset:
                        msg += f" Yenilenme: {reset}."
                    out.append((title, msg))
            if out:
                self._save(now)
        self._flush(out)

    def check_resets(self, alerts: dict[str, Any], now: float | None = None) -> bool:
        """Tell when a quota that was running low has been renewed. Cheap; call often.

        Returns True if any watched window's reset time has passed (data is now stale).
        """
        now = time.time() if now is None else now
        out: list[tuple[str, str]] = []
        passed = False
        with self._lock:
            for (pid, key), info in list(self._watch.items()):
                resets_at = info.get("resets_at")
                if not reset_passed(resets_at, now):
                    continue
                passed = True
                del self._watch[(pid, key)]   # new data will re-add it with the next reset time
                if not (alerts.get("enabled", True) and alerts.get("reset", True)):
                    continue
                if info["remaining"] >= RESET_NOTIFY_BELOW or now - resets_at > RESET_NOTIFY_WITHIN:
                    continue
                if not self._mark(f"reset:{pid}:{key}:{_cycle(resets_at)}", now):
                    continue
                tool = TOOL_NAMES.get(pid, pid)
                out.append((
                    f"{tool}: {info['label']} yenilendi",
                    "Kotan yeniden dolu, tekrar kullanabilirsin.",
                ))
            if out:
                self._save(now)
        self._flush(out)
        return passed
