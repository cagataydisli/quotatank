"""Derived information on top of the raw quotas.

- forecast: at the pace used so far in this window, when does it run out?
- advice:   which tool has the most room right now?
- summary:  worst status for the tray icon / tooltip.

All of it is pure arithmetic on data already collected (no extra polling).
"""

from __future__ import annotations

import time
from typing import Any

SHORT_NAMES = {"claude": "Claude", "codex": "Codex", "antigravity": "Gemini"}

FIVE_HOURS = 5 * 3600
WEEK = 7 * 86400

# Weekly % one full 5-hour window costs, until the real ratio has been learned.
DEFAULT_FULL_SHORT_COST = 15.0


def window_seconds(w: dict[str, Any]) -> int | None:
    """Length of a quota window, from the CLI if it says so, else from its key."""
    mins = w.get("window_minutes")
    if isinstance(mins, (int, float)) and mins > 0:
        return int(mins * 60)
    key = str(w.get("key") or "").lower()
    if "5h" in key or "five_hour" in key:
        return FIVE_HOURS
    if "7d" in key or "week" in key or "seven_day" in key:
        return WEEK
    return None


def reset_passed(resets_at: Any, now: float) -> bool:
    return isinstance(resets_at, (int, float)) and resets_at <= now


def effective_remaining(w: dict[str, Any], now: float | None = None) -> float:
    """Remaining %, counting a window whose reset time has passed as full again."""
    now = time.time() if now is None else now
    if reset_passed(w.get("resets_at"), now):
        return 100.0
    return float(w.get("remaining_percent") or 0.0)


def forecast(w: dict[str, Any], now: float | None = None) -> dict[str, Any] | None:
    """Depletion estimate from the average pace since this window started.

    Returns None when there is not enough to go on (window just started, nothing used,
    unknown length or reset time).
    """
    now = time.time() if now is None else now
    resets_at = w.get("resets_at")
    dur = window_seconds(w)
    if not isinstance(resets_at, (int, float)) or not dur or resets_at <= now:
        return None
    used = float(w.get("used_percent") or 0.0)
    remaining = max(0.0, 100.0 - used)
    elapsed = now - (resets_at - dur)
    if elapsed < max(15 * 60, dur * 0.04) or elapsed > dur + 600:
        return None
    if used < 2.0:
        return {"runs_out": False, "depletes_at": None, "end_used": round(used, 1)}
    rate = used / elapsed                    # % per second
    end_used = used + rate * (resets_at - now)
    if remaining <= 0:
        return {"runs_out": True, "depletes_at": int(now), "end_used": round(end_used, 1)}
    depletes_at = now + remaining / rate
    runs_out = depletes_at < resets_at - 60
    return {
        "runs_out": runs_out,
        "depletes_at": int(depletes_at) if runs_out else None,
        "end_used": round(min(999.0, end_used), 1),
    }


def _tank_room(tank: dict[str, Any], now: float) -> tuple[float, str, Any]:
    """How much can be used right now, as % of one full 5-hour window.

    Returns (room 0..100, which limit binds: "short" | "long", that limit's reset time).
    """
    long_rem = 100.0 if reset_passed(tank.get("long_resets_at"), now) else float(tank["long_remaining"])
    if tank.get("short_remaining") is None:
        return long_rem, "long", tank.get("long_resets_at")
    short_rem = 100.0 if reset_passed(tank.get("short_resets_at"), now) else float(tank["short_remaining"])
    cost = tank.get("full_short_cost") or DEFAULT_FULL_SHORT_COST
    by_long = min(100.0, long_rem * 100.0 / cost) if cost > 0 else 100.0
    if by_long < short_rem:
        return by_long, "long", tank.get("long_resets_at")
    return short_rem, "short", tank.get("short_resets_at")


def advice(providers: list[dict[str, Any]], now: float | None = None) -> dict[str, Any] | None:
    """Rank the tracked tools by how much they can be used right now."""
    now = time.time() if now is None else now
    items = []
    for p in providers:
        if p.get("status") == "error":
            continue
        best = None
        for tank in p.get("tanks") or []:
            room, limiter, resets_at = _tank_room(tank, now)
            if best is None or room > best["room"]:
                best = {
                    "id": p["id"],
                    "name": SHORT_NAMES.get(p["id"], p["id"]),
                    "pool": tank.get("label") if len(p.get("tanks") or []) > 1 else None,
                    "room": round(room, 1),
                    "limiter": limiter,
                    "resets_at": resets_at,
                }
        if best is not None:
            items.append(best)
    if not items:
        return None
    items.sort(key=lambda it: it["room"], reverse=True)
    return {"best": items[0]["id"] if items[0]["room"] >= 25 else None, "items": items}


def summary(providers: list[dict[str, Any]], cfg_alerts: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Lowest visible quota → tray colour; one short line per tool → tray tooltip."""
    now = time.time() if now is None else now
    lowest = None
    lines = []
    for p in providers:
        parts = []
        for tank in (p.get("tanks") or [])[:1]:
            if tank.get("short_remaining") is not None:
                short = 100.0 if reset_passed(tank.get("short_resets_at"), now) else tank["short_remaining"]
                parts.append(f"5s %{round(short)}")
            long_rem = 100.0 if reset_passed(tank.get("long_resets_at"), now) else tank["long_remaining"]
            parts.append(f"hafta %{round(long_rem)}")
        for w in p.get("windows") or []:
            if w.get("disabled"):
                continue
            rem = effective_remaining(w, now)
            if lowest is None or rem < lowest:
                lowest = rem
        if parts:
            lines.append(f"{SHORT_NAMES.get(p['id'], p['id'])}: " + " · ".join(parts))
    if lowest is None:
        status = "none"
    elif lowest <= cfg_alerts.get("critical", 10):
        status = "crit"
    elif lowest <= cfg_alerts.get("threshold", 20):
        status = "warn"
    else:
        status = "ok"
    return {"lowest": lowest, "status": status, "lines": lines}
