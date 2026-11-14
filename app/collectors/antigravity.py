"""Antigravity CLI collector via passive statusLine snapshots and `agy models`."""

from __future__ import annotations

import subprocess
import time
from typing import Any

from app.paths import (
    get_snapshot_path,
    hidden_popen_kwargs,
    parse_iso_to_epoch,
    read_json_safe,
    write_json_atomic,
)


QUOTA_POOL_META = {
    "gemini-5h": ("Gemini • 5 Saatlik", "gemini"),
    "gemini-weekly": ("Gemini • Haftalık", "gemini"),
    "3p-5h": ("3P Modeller (Claude / GPT-OSS) • 5 Saatlik", "3p"),
    "3p-weekly": ("3P Modeller (Claude / GPT-OSS) • Haftalık", "3p"),
    "gemini-argon-wtu-weekly": ("Gemini Argon • Haftalık", "argon"),
}

MODELS_CACHE_TTL_SEC = 6 * 3600


def _load_agy_models(force_refresh: bool = False) -> list[dict[str, str]]:
    cache_path = get_snapshot_path("agy_models")
    cached = read_json_safe(cache_path)
    now = int(time.time())
    if (
        not force_refresh
        and cached
        and isinstance(cached.get("models"), list)
        and (now - int(cached.get("fetched_at_epoch") or 0)) < MODELS_CACHE_TTL_SEC
    ):
        return cached["models"]

    try:
        res = subprocess.run(
            "agy models",
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            **hidden_popen_kwargs(),
        )
        models: list[dict[str, str]] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Fetching"):
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                model_id, display = parts[0].strip(), parts[1].strip()
                lower = model_id.lower()
                display_lower = display.lower()
                if "argon" in lower or "argon" in display_lower:
                    pool = "Argon"
                elif lower.startswith("claude") or lower.startswith("gpt-"):
                    pool = "3P"
                else:
                    pool = "Gemini"
                models.append({"id": model_id, "name": display, "pool": pool})
        if models:
            write_json_atomic(
                cache_path, {"fetched_at_epoch": now, "models": models}
            )
            return models
    except Exception:
        pass

    if cached and isinstance(cached.get("models"), list):
        return cached["models"]
    return []


def collect_antigravity(force_models_refresh: bool = False) -> dict[str, Any]:
    snap = read_json_safe(get_snapshot_path("agy"))
    models = _load_agy_models(force_refresh=force_models_refresh)

    if not snap or not isinstance(snap.get("quota"), dict):
        return {
            "id": "antigravity",
            "name": "Antigravity CLI (agy)",
            "status": "waiting",
            "status_label": "Oturum Bekleniyor",
            "plan": "Google AI",
            "active_model": None,
            "captured_at_epoch": None,
            "windows": [],
            "models": models,
            "extras": {
                "hint": "agy içinde bir mesaj gönderdiğinizde kota otomatik yakalanır."
            },
            "error": None,
        }

    quota = snap["quota"]
    windows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    ordered_keys = list(QUOTA_POOL_META.keys()) + [
        k for k in quota.keys() if k not in QUOTA_POOL_META
    ]

    for key in ordered_keys:
        if key in seen_keys or key not in quota:
            continue
        seen_keys.add(key)
        entry = quota.get(key)
        if not isinstance(entry, dict):
            continue
        frac = entry.get("remaining_fraction")
        if frac is None:
            continue
        rem = round(max(0.0, min(100.0, float(frac) * 100.0)), 1)
        used = round(100.0 - rem, 1)
        label, group = QUOTA_POOL_META.get(key, (key, "other"))
        reset_epoch = parse_iso_to_epoch(entry.get("reset_time"))
        if reset_epoch is None and entry.get("reset_in_seconds") is not None:
            base_ts = int(
                snap.get("quota_captured_at_epoch")
                or snap.get("captured_at_epoch")
                or time.time()
            )
            reset_epoch = base_ts + int(entry["reset_in_seconds"])

        windows.append(
            {
                "key": key,
                "label": label,
                "group": group,
                "used_percent": used,
                "remaining_percent": rem,
                "resets_at": reset_epoch,
                "disabled": bool(entry.get("disabled", False)),
            }
        )

    captured_at = snap.get("quota_captured_at_epoch") or snap.get("captured_at_epoch")
    age_sec = int(time.time()) - int(captured_at) if captured_at else None
    is_stale = age_sec is not None and age_sec > 1800

    model_obj = snap.get("model") or {}
    ctx = snap.get("context_window") or {}

    return {
        "id": "antigravity",
        "name": "Antigravity CLI (agy)",
        "status": "stale" if is_stale else "live",
        "status_label": "Önbellek (statusLine)" if is_stale else "Yakalandı (statusLine)",
        "plan": snap.get("plan_tier") or "Google AI",
        "active_model": model_obj.get("display_name") or model_obj.get("id"),
        "captured_at_epoch": captured_at,
        "windows": windows,
        "models": models,
        "extras": {
            "version": snap.get("version"),
            "context_used_percent": ctx.get("used_percentage"),
            "session_input_tokens": ctx.get("total_input_tokens"),
            "session_output_tokens": ctx.get("total_output_tokens"),
        },
        "error": None,
    }
