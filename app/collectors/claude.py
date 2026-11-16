"""Claude Code CLI collector: live usage API, statusLine snapshots and stats-cache.

The 5h / weekly limits are shared with claude.ai and the desktop app, so the passive
statusLine snapshot misses usage made outside the terminal. The same endpoint Claude
Code's /usage command reads is polled (throttled) with the CLI's own OAuth token; the
statusLine snapshot remains the fallback when that is not possible.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from app.paths import get_snapshot_path, parse_iso_to_epoch, read_json_safe, write_json_atomic

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
LIVE_MIN_INTERVAL_SEC = 60
LIVE_TIMEOUT_SEC = 8


RATE_LIMIT_META = {
    "five_hour": ("5h", "5 Saatlik Oturum"),
    "seven_day": ("7d", "7 Günlük Haftalık"),
    "seven_day_opus": ("7d_opus", "7 Günlük • Opus"),
    "seven_day_sonnet": ("7d_sonnet", "7 Günlük • Sonnet"),
}


def rate_limit_key_label(raw_key: str) -> tuple[str, str]:
    return RATE_LIMIT_META.get(raw_key, (raw_key, raw_key.replace("_", " ").title()))


def _normalize_rate_limit(
    label: str, key: str, raw: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percentage")
    if used is None:
        return None
    used_f = round(max(0.0, min(100.0, float(used))), 1)
    rem_f = round(max(0.0, min(100.0, 100.0 - used_f)), 1)
    resets_at = raw.get("resets_at")
    return {
        "key": key,
        "label": label,
        "used_percent": used_f,
        "remaining_percent": rem_f,
        "resets_at": int(resets_at) if resets_at else None,
    }


def _usage_to_rate_limits(data: dict[str, Any]) -> dict[str, Any]:
    """/api/oauth/usage payload -> statusLine-style {key: {used_percentage, resets_at}}."""
    out: dict[str, Any] = {}
    for key, val in data.items():
        if not isinstance(val, dict) or not isinstance(val.get("resets_at"), str):
            continue
        used = val.get("utilization")
        if not isinstance(used, (int, float)):
            continue
        out[key] = {"used_percentage": float(used), "resets_at": parse_iso_to_epoch(val["resets_at"])}
    return out


def fetch_live_usage(min_interval: float = LIVE_MIN_INTERVAL_SEC) -> dict[str, Any] | None:
    """Live limits from Anthropic, cached on disk; None when never fetched successfully.

    Never refreshes the OAuth token itself (that would rotate Claude Code's refresh
    token); an expired token simply leaves the last cached value / statusLine in place.
    """
    path = get_snapshot_path("claude_api")
    cached = read_json_safe(path)
    now = time.time()
    if cached and now - float(cached.get("captured_at_epoch") or 0) < min_interval:
        return cached
    try:
        oauth = (read_json_safe(CREDENTIALS) or {}).get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        expires_ms = oauth.get("expiresAt")
        if not token or (isinstance(expires_ms, (int, float)) and expires_ms / 1000 <= now + 30):
            return cached
        req = urllib.request.Request(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "QuotaTank",
            },
        )
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rate_limits = _usage_to_rate_limits(data if isinstance(data, dict) else {})
        if not rate_limits:
            return cached
        live = {
            "captured_at_epoch": int(now),
            "plan": oauth.get("subscriptionType"),
            "rate_limits": rate_limits,
        }
        write_json_atomic(path, live)
        return live
    except Exception:
        return cached


def collect_claude() -> dict[str, Any]:
    snap = read_json_safe(get_snapshot_path("claude"))
    stats = read_json_safe(Path.home() / ".claude" / "stats-cache.json") or {}
    settings = read_json_safe(Path.home() / ".claude" / "settings.json") or {}

    model_usage_list: list[dict[str, Any]] = []
    raw_model_usage = stats.get("modelUsage") or {}
    if isinstance(raw_model_usage, dict):
        for model_name, mu in raw_model_usage.items():
            if not isinstance(mu, dict):
                continue
            model_usage_list.append(
                {
                    "id": model_name,
                    "name": model_name,
                    "input_tokens": int(mu.get("inputTokens") or 0),
                    "output_tokens": int(mu.get("outputTokens") or 0),
                    "cache_read_tokens": int(mu.get("cacheReadInputTokens") or 0),
                    "cache_creation_tokens": int(
                        mu.get("cacheCreationInputTokens") or 0
                    ),
                }
            )

    windows: list[dict[str, Any]] = []
    captured_at = None
    active_model = settings.get("model")

    source = "statusLine"
    rl = None
    if snap and isinstance(snap.get("rate_limits"), dict):
        rl = snap["rate_limits"]
        captured_at = snap.get("rate_limits_captured_at_epoch") or snap.get(
            "captured_at_epoch"
        )
    live = fetch_live_usage()
    if live and isinstance(live.get("rate_limits"), dict) and (
        rl is None or int(live.get("captured_at_epoch") or 0) >= int(captured_at or 0)
    ):
        rl = live["rate_limits"]
        captured_at = live.get("captured_at_epoch")
        source = "API"

    if rl:
        ordered = [k for k in RATE_LIMIT_META if k in rl] + [
            k for k in rl if k not in RATE_LIMIT_META
        ]
        for raw_key in ordered:
            key, label = rate_limit_key_label(raw_key)
            w = _normalize_rate_limit(label, key, rl.get(raw_key))
            if w:
                windows.append(w)
    if snap:
        m = snap.get("model") or {}
        active_model = m.get("display_name") or m.get("id") or active_model

    if not windows:
        status = "waiting"
        status_label = "Oturum Bekleniyor"
    else:
        age = int(time.time()) - int(captured_at or 0)
        if age > 1800:
            status = "stale"
            status_label = f"Önbellek ({source})"
        else:
            status = "live"
            status_label = "Canlı (API)" if source == "API" else "Yakalandı (statusLine)"

    ctx = (snap or {}).get("context_window") or {}

    return {
        "id": "claude",
        "name": "Claude Code",
        "status": status,
        "status_label": status_label,
        "plan": "Claude Pro / Max",
        "active_model": active_model,
        "captured_at_epoch": captured_at,
        "windows": windows,
        "models": model_usage_list,
        "extras": {
            "total_sessions": stats.get("totalSessions"),
            "total_messages": stats.get("totalMessages"),
            "stats_date": stats.get("lastComputedDate"),
            "context_used_percent": ctx.get("used_percentage"),
            "hint": None
            if windows
            else "claude içinde ilk yanıt alındıktan sonra 5s & 7g kotası otomatik yakalanır.",
        },
        "error": None,
    }
