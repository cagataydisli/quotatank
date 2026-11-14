"""Tank model: weekly quota as the tank, the 5-hour window as the part you can spend now.

No CLI tells us how much of the weekly quota one full 5-hour window is worth, so
we learn it: inside each 5-hour window we track how much the weekly usage moved
while the 5-hour usage moved, and keep a usage-weighted average across windows.

The CLIs report whole percents, so each window's numbers are off by up to +-0.5 at
both ends. The estimate therefore comes with a range that narrows as more usage is
observed (rounding noise + how much the windows disagree with each other).

    ratio            = Δweekly_used / Δ5h_used     (weekly points per 5h point)
    full_short_cost  = ratio * 100                 (weekly % one full 5h window costs)
    spendable        = min(weekly_remaining, 5h_remaining * ratio)
"""

from __future__ import annotations

import threading
from typing import Any

from app.paths import get_app_data_dir, read_json_safe, write_json_atomic


# provider id -> list of (short window key | None, long window key, tank label)
PAIRS: dict[str, list[tuple[str | None, str, str]]] = {
    "codex": [("5h", "7d", "Codex")],
    "claude": [("5h", "7d", "Claude")],
    "antigravity": [
        ("gemini-5h", "gemini-weekly", "Gemini"),
        ("3p-5h", "3p-weekly", "3P"),
        (None, "gemini-argon-wtu-weekly", "Argon"),
    ],
}

MIN_WINDOW_DELTA = 3.0      # ignore 5h windows with less movement than this (noise)
MIN_TOTAL_DELTA = 15.0      # need this much observed 5h usage before trusting the ratio
MAX_WINDOWS = 40            # history kept per pair
BUCKET_SEC = 600            # tolerate reset-time jitter of a few minutes

_lock = threading.Lock()


def _state_path():
    return get_app_data_dir() / "tank_ratios.json"


def _bucket(epoch: Any) -> int | None:
    try:
        return int(round(int(epoch) / BUCKET_SEC))
    except (TypeError, ValueError):
        return None


def _by_key(windows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {w["key"]: w for w in windows or [] if isinstance(w, dict) and w.get("key")}


def observe(raw_providers: dict[str, dict[str, Any]]) -> None:
    """Record one sample per (5h, weekly) pair from freshly collected raw payloads."""
    with _lock:
        state = read_json_safe(_state_path()) or {}
        changed = False
        for pid, payload in raw_providers.items():
            wins = _by_key(payload.get("windows"))
            for short_key, long_key, _label in PAIRS.get(pid, []):
                ws, wl = wins.get(short_key or ""), wins.get(long_key)
                if not ws or not wl:
                    continue
                su, lu = float(ws["used_percent"]), float(wl["used_percent"])
                long_reset = _bucket(wl.get("resets_at"))
                entry = state.setdefault(f"{pid}:{short_key}:{long_key}", {"windows": {}})

                if su <= 0:
                    # Unused 5h window: its reset time just rolls forward with the clock, so
                    # it is not a window yet. Remember the weekly level it will start from.
                    idle = {"long": lu, "long_reset": long_reset}
                    if entry.get("idle") != idle:
                        entry["idle"] = idle
                        changed = True
                    if _prune(entry, ""):
                        changed = True
                    continue

                wid = _bucket(ws.get("resets_at"))
                if wid is None:
                    continue
                rec = entry["windows"].get(str(wid))
                restarted = rec is not None and (
                    rec.get("long_reset") != long_reset or su < rec["last_short"] - 0.5
                )
                if rec is None or restarted:
                    idle = entry.pop("idle", None)
                    if idle and idle.get("long_reset") == long_reset and idle["long"] <= lu:
                        start_short, start_long = 0.0, float(idle["long"])  # seen it start from 0
                    else:
                        start_short, start_long = su, lu
                    rec = {
                        "start_short": start_short,
                        "start_long": start_long,
                        "last_short": su,
                        "last_long": lu,
                        "long_reset": long_reset,
                    }
                    entry["windows"][str(wid)] = rec
                    changed = True
                elif rec["last_short"] != su or rec["last_long"] != lu:
                    rec["last_short"], rec["last_long"] = su, lu
                    changed = True

                if _prune(entry, str(wid)):
                    changed = True
        if changed:
            write_json_atomic(_state_path(), state)


def _prune(entry: dict[str, Any], current: str) -> bool:
    """Drop windows that never moved (except the current one) and keep the newest MAX_WINDOWS."""
    wins = entry["windows"]
    before = len(wins)
    for wid in [
        k for k, r in wins.items()
        if k != current and r["last_short"] == r["start_short"] and r["last_long"] == r["start_long"]
    ]:
        del wins[wid]
    if len(wins) > MAX_WINDOWS:
        for old in sorted(wins, key=int)[:-MAX_WINDOWS]:
            del wins[old]
    return len(wins) != before


# Std-dev of a difference of two readings each rounded to a whole percent: sqrt(2/12).
ROUNDING_SD = 0.41


def _estimate(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Learned ratio with an uncertainty range.

    Returns {"ratio", "low", "high", "observed", "windows"}; ratio/low/high are None
    until at least MIN_TOTAL_DELTA 5h points have been seen.
    """
    out: dict[str, Any] = {"ratio": None, "low": None, "high": None, "observed": 0.0, "windows": 0}
    if not entry:
        return out
    pairs = []
    for rec in entry.get("windows", {}).values():
        ds = rec["last_short"] - rec["start_short"]
        dl = rec["last_long"] - rec["start_long"]
        if ds >= MIN_WINDOW_DELTA and dl >= 0:
            pairs.append((ds, dl))
    sum_ds = sum(p[0] for p in pairs)
    sum_dl = sum(p[1] for p in pairs)
    out["observed"], out["windows"] = sum_ds, len(pairs)
    if sum_ds < MIN_TOTAL_DELTA:
        return out
    ratio = sum_dl / sum_ds
    n = len(pairs)
    # rounding noise of all windows together, propagated to the ratio
    var_round = n * ROUNDING_SD ** 2 * (1.0 + ratio ** 2) / sum_ds ** 2
    # how much the windows disagree (usage-weighted), shrinking with more windows
    var_spread = 0.0
    if n >= 2:
        var_spread = sum(ds * (dl / ds - ratio) ** 2 for ds, dl in pairs) / sum_ds / n
    half = 2.0 * (var_round + var_spread) ** 0.5   # ~95 %
    out.update(ratio=ratio, low=max(0.0, ratio - half), high=ratio + half)
    return out


def build_tanks(
    pid: str, raw_windows: list[dict[str, Any]], visible_keys: set[str]
) -> list[dict[str, Any]]:
    """Tank descriptors for the card background (only for visible weekly windows)."""
    state = read_json_safe(_state_path()) or {}
    wins = _by_key(raw_windows)
    tanks: list[dict[str, Any]] = []
    for short_key, long_key, label in PAIRS.get(pid, []):
        wl = wins.get(long_key)
        if not wl or long_key not in visible_keys or wl.get("disabled"):
            continue
        ws = wins.get(short_key or "")
        if ws and short_key not in visible_keys:
            ws = None

        long_rem = float(wl["remaining_percent"])
        tank: dict[str, Any] = {
            "label": label,
            "long_key": long_key,
            "long_remaining": long_rem,
            "long_resets_at": wl.get("resets_at"),
            "short_key": short_key if ws else None,
            "short_remaining": float(ws["remaining_percent"]) if ws else None,
            "short_resets_at": ws.get("resets_at") if ws else None,
            "ratio": None,
            "full_short_cost": None,
            "full_short_cost_low": None,
            "full_short_cost_high": None,
            "learned_windows": 0,
            "spendable": None,
            "learn_progress": None,
        }
        if ws:
            est = _estimate(state.get(f"{pid}:{short_key}:{long_key}"))
            tank["learned_windows"] = est["windows"]
            if est["ratio"] is None:
                tank["learn_progress"] = round(min(1.0, est["observed"] / MIN_TOTAL_DELTA), 2)
            else:
                ratio = est["ratio"]
                spend = min(long_rem, tank["short_remaining"] * ratio)
                tank["ratio"] = round(ratio, 4)
                tank["full_short_cost"] = round(ratio * 100.0, 1)
                tank["full_short_cost_low"] = round(est["low"] * 100.0, 1)
                tank["full_short_cost_high"] = round(min(100.0, est["high"] * 100.0), 1)
                tank["spendable"] = round(max(0.0, spend), 1)
        tanks.append(tank)
    return tanks
