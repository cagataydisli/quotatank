"""User configuration: which providers, quota windows and models are tracked.

Selection semantics (per provider):
- ``enabled``: False -> the collector never runs (no RPC, no subprocess, no file watch).
- ``windows``: None -> every window is shown; a list -> only those window keys.
- ``models``:  None -> every model is shown;  a list -> only those model ids.
"""

from __future__ import annotations

import copy
import threading
from typing import Any

from app.paths import get_app_data_dir, read_json_safe, write_json_atomic


PROVIDER_IDS = ("codex", "antigravity", "claude")

DEFAULT_PROVIDER_CFG: dict[str, Any] = {"enabled": True, "windows": None, "models": None}

# Appearance options for the liquid tanks.
#   idle_waves: gentle ripple while nothing moves (costs a little GPU while visible)
#   slosh:      liquid reacts when the window is dragged / shaken
#   minimize_to_widget: the native minimize button swaps the panel for the compact widget
#   waves_only_active: idle ripple pauses while another program is in use
DEFAULT_UI_CFG: dict[str, bool] = {
    "idle_waves": True,
    "slosh": True,
    "minimize_to_widget": True,
    "waves_only_active": True,
}

# Warnings shown as Windows notifications.
#   threshold: warn when a visible quota drops to this many % remaining
#   critical:  second, louder warning
#   reset:     tell when a quota that was running low has been renewed
DEFAULT_ALERTS_CFG: dict[str, Any] = {"enabled": True, "threshold": 20, "critical": 10, "reset": True}

# Compact widget look: opacity in %, click_through lets the mouse pass to what is below,
# style picks the design: "tank" (vertical liquid tanks) or "compact" (one horizontal bar).
WIDGET_STYLES = ("tank", "compact")
# dock: the compact bar sits on the taskbar, as thick as the taskbar (compact style only).
DEFAULT_WIDGET_CFG: dict[str, Any] = {"opacity": 100, "click_through": False, "style": "tank", "dock": False}

# System integration. hotkey: "ctrl+shift+q" style; start_mode: what an autostart shows.
DEFAULT_SYSTEM_CFG: dict[str, Any] = {"hotkey_enabled": True, "hotkey": "ctrl+shift+q", "start_mode": "widget"}

_lock = threading.Lock()


def _int_in(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def get_config_path():
    return get_app_data_dir() / "config.json"


def _clean_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(v) for v in value if isinstance(v, (str, int, float))]


def normalize_config(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    raw_providers = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}
    providers: dict[str, Any] = {}
    for pid in PROVIDER_IDS:
        src = raw_providers.get(pid) if isinstance(raw_providers.get(pid), dict) else {}
        cfg = copy.deepcopy(DEFAULT_PROVIDER_CFG)
        if "enabled" in src:
            cfg["enabled"] = bool(src["enabled"])
        if "windows" in src:
            cfg["windows"] = _clean_list(src["windows"])
        if "models" in src:
            cfg["models"] = _clean_list(src["models"])
        providers[pid] = cfg

    raw_ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    ui = dict(DEFAULT_UI_CFG)
    for key in DEFAULT_UI_CFG:
        if key in raw_ui:
            ui[key] = bool(raw_ui[key])
    raw_alerts = raw.get("alerts") if isinstance(raw.get("alerts"), dict) else {}
    alerts = dict(DEFAULT_ALERTS_CFG)
    for key in ("enabled", "reset"):
        if key in raw_alerts:
            alerts[key] = bool(raw_alerts[key])
    alerts["threshold"] = _int_in(raw_alerts.get("threshold"), DEFAULT_ALERTS_CFG["threshold"], 5, 90)
    alerts["critical"] = _int_in(raw_alerts.get("critical"), DEFAULT_ALERTS_CFG["critical"], 1, 50)
    alerts["critical"] = min(alerts["critical"], alerts["threshold"])

    raw_widget = raw.get("widget") if isinstance(raw.get("widget"), dict) else {}
    widget = {
        "opacity": _int_in(raw_widget.get("opacity"), DEFAULT_WIDGET_CFG["opacity"], 30, 100),
        "click_through": bool(raw_widget.get("click_through", DEFAULT_WIDGET_CFG["click_through"])),
        "style": raw_widget["style"] if raw_widget.get("style") in WIDGET_STYLES else DEFAULT_WIDGET_CFG["style"],
        "dock": bool(raw_widget.get("dock", DEFAULT_WIDGET_CFG["dock"])),
    }

    raw_sys = raw.get("system") if isinstance(raw.get("system"), dict) else {}
    system = dict(DEFAULT_SYSTEM_CFG)
    if "hotkey_enabled" in raw_sys:
        system["hotkey_enabled"] = bool(raw_sys["hotkey_enabled"])
    if isinstance(raw_sys.get("hotkey"), str) and raw_sys["hotkey"].strip():
        system["hotkey"] = raw_sys["hotkey"].strip().lower()[:40]
    if raw_sys.get("start_mode") in ("widget", "tray"):
        system["start_mode"] = raw_sys["start_mode"]

    return {"providers": providers, "ui": ui, "alerts": alerts, "widget": widget, "system": system}


def load_config() -> dict[str, Any]:
    with _lock:
        return normalize_config(read_json_safe(get_config_path()))


def save_config(raw: Any) -> dict[str, Any]:
    cfg = normalize_config(raw)
    with _lock:
        write_json_atomic(get_config_path(), cfg)
    return cfg


def is_enabled(cfg: dict[str, Any], provider_id: str) -> bool:
    return bool(cfg["providers"].get(provider_id, DEFAULT_PROVIDER_CFG)["enabled"])


def filter_provider(provider: dict[str, Any], pcfg: dict[str, Any]) -> dict[str, Any]:
    """Apply window/model selection to a collected provider payload."""
    out = dict(provider)
    windows = provider.get("windows") or []
    models = provider.get("models") or []

    out["all_windows"] = [
        {"key": w.get("key"), "label": w.get("label")} for w in windows if w.get("key")
    ]

    sel_w = pcfg.get("windows")
    if sel_w is not None:
        allowed = set(sel_w)
        out["windows"] = [w for w in windows if w.get("key") in allowed]
        out["hidden_window_count"] = len(windows) - len(out["windows"])
    else:
        out["hidden_window_count"] = 0

    sel_m = pcfg.get("models")
    if sel_m is not None:
        allowed_m = set(sel_m)
        out["models"] = [m for m in models if model_key(m) in allowed_m]

    return out


def model_key(model: dict[str, Any]) -> str:
    return str(model.get("id") or model.get("name") or "")
