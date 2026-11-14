"""Dashboard aggregation service, settings catalog and hook status checker."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from app.collectors.antigravity import QUOTA_POOL_META, collect_antigravity
from app.collectors.claude import RATE_LIMIT_META, collect_claude, rate_limit_key_label
from app.collectors.codex import collect_codex
from app.config import PROVIDER_IDS, filter_provider, is_enabled, load_config
from app.paths import get_snapshot_path, read_json_safe
from app import insights, tanks


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGY_SETTINGS = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CLAUDE_STATS = Path.home() / ".claude" / "stats-cache.json"

PROVIDER_NAMES = {
    "codex": "OpenAI Codex CLI",
    "antigravity": "Antigravity CLI (agy)",
    "claude": "Claude Code",
}

# Snapshot file name used by each statusLine-driven provider.
SNAPSHOT_SOURCES = {"antigravity": "agy", "claude": "claude"}


def check_hooks_status(cfg: dict[str, Any] | None = None) -> dict[str, bool]:
    cfg = cfg or load_config()
    agy_cmd = (PROJECT_ROOT / "collector" / "agy-statusline.cmd").as_posix()
    claude_cmd = (PROJECT_ROOT / "collector" / "claude-statusline.cmd").as_posix()

    agy_cfg = read_json_safe(AGY_SETTINGS) or {}
    claude_cfg = read_json_safe(CLAUDE_SETTINGS) or {}

    # A disabled provider does not need its hook; report it as fine.
    return {
        "agy": (not is_enabled(cfg, "antigravity"))
        or (agy_cfg.get("statusLine") or {}).get("command") == agy_cmd,
        "claude": (not is_enabled(cfg, "claude"))
        or (claude_cfg.get("statusLine") or {}).get("command") == claude_cmd,
    }


def get_snapshot_mtimes(cfg: dict[str, Any] | None = None) -> dict[str, float]:
    """mtime of each enabled statusLine provider's snapshot file (cheap stat only)."""
    cfg = cfg or load_config()
    out: dict[str, float] = {}
    for pid, source in SNAPSHOT_SOURCES.items():
        if not is_enabled(cfg, pid):
            continue
        p = get_snapshot_path(source)
        try:
            out[pid] = p.stat().st_mtime if p.exists() else 0.0
        except Exception:
            out[pid] = 0.0
    return out


def _error_payload(pid: str, exc: Exception) -> dict[str, Any]:
    return {
        "id": pid,
        "name": PROVIDER_NAMES[pid],
        "status": "error",
        "status_label": "Hata",
        "windows": [],
        "models": [],
        "extras": {},
        "error": str(exc),
    }


def collect_providers(
    provider_ids: list[str], force_models_refresh: bool = False
) -> dict[str, dict[str, Any]]:
    """Run only the given collectors (in parallel) and return raw, unfiltered payloads."""
    jobs: dict[str, Callable[[], dict[str, Any]]] = {
        "codex": collect_codex,
        "antigravity": lambda: collect_antigravity(force_models_refresh),
        "claude": collect_claude,
    }
    ids = [pid for pid in provider_ids if pid in jobs]
    if not ids:
        return {}

    out: dict[str, dict[str, Any]] = {}
    if len(ids) == 1:
        pid = ids[0]
        try:
            out[pid] = jobs[pid]()
        except Exception as exc:
            out[pid] = _error_payload(pid, exc)
    else:
        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            futures = {pid: pool.submit(jobs[pid]) for pid in ids}
            for pid in ids:
                try:
                    out[pid] = futures[pid].result()
                except Exception as exc:  # never let one provider kill the dashboard
                    out[pid] = _error_payload(pid, exc)

    try:
        tanks.observe(out)  # learn the 5h -> weekly ratio from every fresh sample
    except Exception:
        pass
    return out


def _attach_forecasts(item: dict[str, Any], now: float) -> None:
    """Add a depletion forecast to every visible window and to the tanks built from them."""
    by_key = {}
    windows = []   # copies: the list may be the cached raw payload's own list
    for w in item.get("windows") or []:
        w = dict(w)
        try:
            w["forecast"] = insights.forecast(w, now)
        except Exception:
            w["forecast"] = None
        windows.append(w)
        by_key[w.get("key")] = w["forecast"]
    item["windows"] = windows
    for tank in item.get("tanks") or []:
        tank["long_forecast"] = by_key.get(tank.get("long_key"))
        tank["short_forecast"] = by_key.get(tank.get("short_key")) if tank.get("short_key") else None


def assemble_dashboard(
    raw: dict[str, dict[str, Any]], cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Apply the user's selection to cached raw payloads (no collector runs here)."""
    cfg = cfg or load_config()
    now = time.time()
    active = [pid for pid in PROVIDER_IDS if is_enabled(cfg, pid)]
    providers = []
    for pid in active:
        if pid not in raw:
            continue
        pcfg = cfg["providers"][pid]
        item = filter_provider(raw[pid], pcfg)
        visible = {w["key"] for w in item.get("windows") or []}
        try:
            item["tanks"] = tanks.build_tanks(pid, raw[pid].get("windows") or [], visible)
        except Exception:
            item["tanks"] = []
        _attach_forecasts(item, now)
        providers.append(item)
    try:
        advice = insights.advice(providers, now) if len(providers) > 1 else None
    except Exception:
        advice = None
    return {
        "generated_at_epoch": int(now),
        "advice": advice,
        "hooks": check_hooks_status(cfg),
        "providers": providers,
        "enabled_providers": active,
        "ui": cfg["ui"],
        "alerts": cfg["alerts"],
        "widget": cfg["widget"],
    }


def collect_dashboard(force_models_refresh: bool = False) -> dict[str, Any]:
    cfg = load_config()
    active = [pid for pid in PROVIDER_IDS if is_enabled(cfg, pid)]
    return assemble_dashboard(collect_providers(active, force_models_refresh), cfg)


def get_catalog() -> dict[str, Any]:
    """Everything the user can pick from in settings.

    Built only from static metadata and already-cached files: it never starts
    a CLI, so it is safe to call for disabled providers too.
    """
    # Codex: single shared pool, fixed windows.
    codex = {
        "windows": [
            {"key": "5h", "label": "5 Saatlik Pencere"},
            {"key": "7d", "label": "Haftalık Pencere"},
        ],
        "models": [],
    }

    # Antigravity: known pools + any extra pools seen in the last snapshot.
    agy_snap = read_json_safe(get_snapshot_path("agy")) or {}
    agy_keys = list(QUOTA_POOL_META.keys()) + [
        k for k in (agy_snap.get("quota") or {}) if k not in QUOTA_POOL_META
    ]
    agy_models_cache = read_json_safe(get_snapshot_path("agy_models")) or {}
    antigravity = {
        "windows": [
            {"key": k, "label": QUOTA_POOL_META.get(k, (k, ""))[0]} for k in agy_keys
        ],
        "models": [
            {"id": m.get("id"), "name": m.get("name"), "group": m.get("pool")}
            for m in (agy_models_cache.get("models") or [])
            if isinstance(m, dict) and m.get("id")
        ],
    }

    # Claude: known limits + any extra limits seen in the last snapshot.
    claude_snap = read_json_safe(get_snapshot_path("claude")) or {}
    base_keys = ["five_hour", "seven_day"]
    raw_keys = base_keys + [
        k for k in (claude_snap.get("rate_limits") or {}) if k not in base_keys
    ]
    stats = read_json_safe(CLAUDE_STATS) or {}
    claude = {
        "windows": [
            {"key": key, "label": label}
            for key, label in (rate_limit_key_label(k) for k in raw_keys)
        ],
        "models": [
            {"id": name, "name": name, "group": None}
            for name in (stats.get("modelUsage") or {})
        ],
    }

    return {
        "providers": [
            {"id": "codex", "name": PROVIDER_NAMES["codex"], **codex},
            {"id": "antigravity", "name": PROVIDER_NAMES["antigravity"], **antigravity},
            {"id": "claude", "name": PROVIDER_NAMES["claude"], **claude},
        ]
    }
