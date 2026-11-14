"""Claude Code CLI collector via passive statusLine snapshots and stats-cache."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.paths import get_snapshot_path, read_json_safe


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

    if snap and isinstance(snap.get("rate_limits"), dict):
        rl = snap["rate_limits"]
        ordered = [k for k in RATE_LIMIT_META if k in rl] + [
            k for k in rl if k not in RATE_LIMIT_META
        ]
        for raw_key in ordered:
            key, label = rate_limit_key_label(raw_key)
            w = _normalize_rate_limit(label, key, rl.get(raw_key))
            if w:
                windows.append(w)
        captured_at = snap.get("rate_limits_captured_at_epoch") or snap.get(
            "captured_at_epoch"
        )
        m = snap.get("model") or {}
        active_model = m.get("display_name") or m.get("id") or active_model

    if not windows:
        status = "waiting"
        status_label = "Oturum Bekleniyor"
    else:
        age = int(time.time()) - int(captured_at or 0)
        if age > 1800:
            status = "stale"
            status_label = "Önbellek (statusLine)"
        else:
            status = "live"
            status_label = "Yakalandı (statusLine)"

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
