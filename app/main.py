"""PyWebView desktop entry point for AI Quota Monitor."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import webview

from app.config import PROVIDER_IDS, is_enabled, load_config, save_config
from app.paths import get_app_data_dir, get_snapshot_dir, read_json_safe, write_json_atomic
from app.service import (
    assemble_dashboard,
    collect_providers,
    get_catalog,
    get_snapshot_mtimes,
)
from app.single_instance import SingleInstance
from app.tray import TrayController
from app.jssender import JsSender
from app.collectors.codex import codex_activity_mtime
from app import autostart, insights, winutil
from app.hotkey import GlobalHotkey
from app.notifier import Notifier
import install_hooks


PROJECT_ROOT = Path(__file__).resolve().parent.parent
UI_INDEX = PROJECT_ROOT / "ui" / "index.html"
UI_WIDGET = PROJECT_ROOT / "ui" / "widget.html"
UI_WIDGET_COMPACT = PROJECT_ROOT / "ui" / "widget-compact.html"
APP_ICON = PROJECT_ROOT / "assets" / "quotatank.ico"
# Own taskbar identity: windows are grouped under QuotaTank with its icon, not under Python.
APP_USER_MODEL_ID = "QuotaTank.Monitor"

LOOP_INTERVAL_SEC = 2.5
# Codex RPC spawns a process (~1.5 s, ~0.5 s CPU), so it only runs when it can matter:
#   - Codex was used (a session log changed)      -> at most every GAP seconds
#   - a 5h / weekly window just reset             -> once, right after the reset
#   - otherwise a safety refresh every MAX_AGE (usage from other devices / cloud tasks)
CODEX_ACTIVITY_GAP_VISIBLE = 20.0
CODEX_ACTIVITY_GAP_HIDDEN = 60.0
CODEX_MAX_AGE_VISIBLE = 600.0
CODEX_MAX_AGE_HIDDEN = 1800.0
# Claude limits are shared with claude.ai / the desktop app, so the live usage API is
# re-read periodically (one small HTTPS request) and right after a window resets.
CLAUDE_MAX_AGE_VISIBLE = 120.0
CLAUDE_MAX_AGE_HIDDEN = 600.0

# Compact widget geometry (logical px): one column per tracked tool.
WIDGET_COL_W = 92
WIDGET_GAP = 8
WIDGET_PAD = 10
WIDGET_H = 232
# User resize limits (keep in sync with ui/widget.js)
WIDGET_MIN_COL_W, WIDGET_MAX_COL_W = 74, 320
WIDGET_MIN_H, WIDGET_MAX_H = 170, 900
WIDGET_MIN_W = 258   # header (brand + clock + 5 buttons) needs this much
# Horizontal "compact" design: the page measures itself and asks for its exact size;
# these are only the first guess and the sanity limits.
COMPACT_H = 60
COMPACT_BASE_W, COMPACT_COL_W = 116, 125
COMPACT_MIN, COMPACT_MAX = (80, 30), (1600, 700)
# Taskbar docking: gap to the notification area, and the spot used when it is not found.
DOCK_GAP = 6
DOCK_FALLBACK_OFF = 170
# Smallest size the native window may ever take (both designs set their own size).
WINDOW_MIN = COMPACT_MIN


def _ncols(columns: int) -> int:
    return max(1, min(3, int(columns)))


def widget_width(columns: int, col_w: float = WIDGET_COL_W) -> int:
    n = _ncols(columns)
    return int(max(WIDGET_MIN_W, 2 * WIDGET_PAD + n * col_w + (n - 1) * WIDGET_GAP))


def col_width_for(columns: int, width: float) -> float:
    """Inverse of widget_width: per-column width a total window width implies."""
    n = _ncols(columns)
    col = (width - 2 * WIDGET_PAD - (n - 1) * WIDGET_GAP) / n
    return max(WIDGET_MIN_COL_W, min(WIDGET_MAX_COL_W, col))


def _widget_size_path():
    return get_app_data_dir() / "widget.json"


def load_widget_state() -> dict[str, Any]:
    raw = read_json_safe(_widget_size_path())
    return raw if isinstance(raw, dict) else {}


def load_widget_pos() -> tuple[int, int] | None:
    """Saved top-left corner (physical px), or None to dock bottom-right."""
    raw = load_widget_state()
    try:
        return int(raw["x"]), int(raw["y"])
    except (KeyError, TypeError, ValueError):
        return None


def _clamp_compact(w: float, h: float) -> tuple[int, int]:
    return (
        int(max(COMPACT_MIN[0], min(COMPACT_MAX[0], round(w)))),
        int(max(COMPACT_MIN[1], min(COMPACT_MAX[1], round(h)))),
    )


def load_compact_size() -> tuple[int, int] | None:
    """Last measured size of the compact bar (logical px), so it reopens at the same spot."""
    raw = load_widget_state()
    try:
        w, h = float(raw["compact_w"]), float(raw["compact_h"])
    except (KeyError, TypeError, ValueError):
        return None
    return _clamp_compact(w, h)


def load_dock_offset() -> float | None:
    """Docked bar: distance of its right edge from the taskbar's right edge (logical px)."""
    try:
        return max(0.0, float(load_widget_state()["dock_off"]))
    except (KeyError, TypeError, ValueError):
        return None


def load_widget_size() -> tuple[float, int]:
    """Saved (column width, height); defaults when nothing was saved yet."""
    raw = load_widget_state()
    try:
        col_w = float(raw.get("col_w", WIDGET_COL_W))
        h = int(raw.get("h", WIDGET_H))
    except (TypeError, ValueError):
        return WIDGET_COL_W, WIDGET_H
    col_w = max(WIDGET_MIN_COL_W, min(WIDGET_MAX_COL_W, col_w))
    h = max(WIDGET_MIN_H, min(WIDGET_MAX_H, h))
    return col_w, h


class QuotaApi:
    def __init__(self, app_ref: "QuotaMonitorApp") -> None:
        self._app = app_ref

    def get_dashboard(self) -> dict[str, Any]:
        return self._app.dashboard()

    def refresh_now(self) -> dict[str, Any]:
        return self._app.refresh(list(PROVIDER_IDS), refresh_models=True)

    def install_cli_hooks(self) -> dict[str, Any]:
        install_hooks.install()
        return self._app.dashboard()

    def get_settings(self) -> dict[str, Any]:
        return {
            "config": load_config(),
            "catalog": get_catalog(),
            "autostart": autostart.is_enabled(),
            "hotkey_ok": self._app.hotkey_ok(),
        }

    def save_settings(self, config: dict[str, Any]) -> dict[str, Any]:
        system = config.get("system") if isinstance(config, dict) else None
        if isinstance(system, dict) and "autostart" in system:
            # lives in the Windows registry, not in config.json
            autostart.set_enabled(bool(system.pop("autostart")))
        saved = save_config(config)
        self._app.set_slosh_enabled(saved["ui"]["slosh"])
        self._app.apply_settings(saved)
        # Only providers that were just enabled need a fresh collection;
        # everything else is re-filtered from cache.
        data = self._app.refresh(self._app.missing_providers())
        # The caller renders the returned data itself; the other window (e.g. the
        # widget) must get it too, or it keeps the old animation/filter settings.
        threading.Thread(target=self._app._push_to_ui, args=(data,), daemon=True).start()
        return {**data, "hotkey_ok": self._app.hotkey_ok()}

    def toggle_always_on_top(self, enabled: bool) -> dict[str, bool]:
        self._app.set_on_top(bool(enabled))
        return {"on_top": bool(enabled)}

    def minimize_to_tray(self) -> None:
        self._app.hide_all()

    def show_widget(self) -> None:
        self._app.show_widget()

    def show_panel(self) -> None:
        self._app.show_window()

    def widget_fit(self, columns: int) -> None:
        self._app.fit_widget(int(columns))

    def widget_set_bounds(self, x: float, y: float, width: float, height: float) -> None:
        self._app.set_widget_bounds(x, y, width, height)

    def widget_fit_content(self, width: float, height: float, expanded: bool = False) -> None:
        self._app.fit_widget_content(float(width), float(height), bool(expanded))

    def widget_dock_info(self) -> dict[str, Any]:
        return self._app.dock_info()

    def widget_dock_drag(self, dx: float, done: bool = False) -> None:
        self._app.dock_drag(float(dx), bool(done))

    def widget_hot_rect(self, x: float, y: float, width: float, height: float) -> None:
        self._app.set_pass_button_rect(x, y, width, height)

    def toggle_click_through(self) -> bool:
        return self._app.toggle_click_through()

    def widget_resize_done(self, width: float, height: float) -> None:
        self._app.save_widget_size(width, height)

    def quit_app(self) -> None:
        self._app.quit()


class QuotaMonitorApp:
    def __init__(self, start_hidden: bool = False) -> None:
        self._start_hidden = start_hidden
        self._window: webview.Window | None = None
        self._widget: webview.Window | None = None
        self._widget_ready = threading.Event()
        self._widget_cols = 3
        self._widget_col_w, self._widget_h = load_widget_size()
        self._widget_style = load_config()["widget"]["style"]   # "tank" | "compact"
        self._compact_size = load_compact_size()   # bar without its detail card
        self._compact_expanded = False             # detail card open (window taller)
        self._dock_pref = bool(load_config()["widget"]["dock"])
        self._dock_applied = self._docked
        self._dock_off = load_dock_offset()        # None = next to the notification area
        self._dock_drag_off0: float | None = None
        self._dock_tb_h = 48.0                     # taskbar thickness (logical px)
        self._dock_bg: str | None = None           # sampled taskbar colour
        self._dock_hidden = False                  # hidden for a fullscreen app
        self._dock_thread: threading.Thread | None = None
        self._mode = "tray" if start_hidden else "main"   # "main" | "widget" | "tray"
        self._tray: TrayController | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._quitting = False
        self._raw: dict[str, dict[str, Any]] = {}   # provider id -> raw payload
        self._last_codex_poll = 0.0
        self._last_claude_poll = 0.0
        self._codex_activity = codex_activity_mtime()
        self._collecting: set[str] = set()
        self._last_mtimes: dict[str, float] = {}
        # Python -> JS goes through one worker; hidden windows are updated when shown.
        self._js = JsSender()
        self._latest: dict[str, Any] | None = None
        self._latest_ver = 0
        self._window_ver: dict[str, int] = {}
        self._widget_pos = load_widget_pos()
        self._pos_timer: threading.Timer | None = None
        # Window drag samples for the liquid slosh effect: (x, y, t_ms)
        self._move_samples: list[tuple[int, int, float]] = []
        self._move_lock = threading.Lock()
        self._move_wakeup = threading.Event()
        self._slosh_enabled = bool(load_config()["ui"]["slosh"])
        self._notifier = Notifier(self._send_notification)
        # pass-through button: its rect inside the widget (CSS px) and the watcher thread
        self._widget_look: dict[str, Any] | None = None
        self._ct_hot: tuple[float, float, float, float] | None = None
        self._ct_pass_now = False
        self._ct_thread: threading.Thread | None = None
        self._hotkey: GlobalHotkey | None = None

    # ------------------------------------------------------------ data

    def missing_providers(self) -> list[str]:
        cfg = load_config()
        return [pid for pid in PROVIDER_IDS if is_enabled(cfg, pid) and pid not in self._raw]

    def refresh(self, provider_ids: list[str], refresh_models: bool = False) -> dict[str, Any]:
        """Collect only the given (enabled) providers, then rebuild the dashboard."""
        cfg = load_config()
        with self._lock:
            # A provider already being collected by another thread is not collected twice.
            ids = [
                pid for pid in provider_ids
                if is_enabled(cfg, pid) and pid not in self._collecting
            ]
            self._collecting.update(ids)
        fresh: dict[str, dict[str, Any]] = {}
        try:
            if ids:
                # Slow part (Codex spawns a process) runs WITHOUT the lock, so UI calls
                # like get_dashboard / save_settings never wait for it.
                fresh = collect_providers(ids, force_models_refresh=refresh_models)
        finally:
            with self._lock:
                self._collecting.difference_update(ids)
                self._raw.update(fresh)
                if "codex" in ids:
                    self._last_codex_poll = time.time()
                if "claude" in ids:
                    self._last_claude_poll = time.time()
                if ids:
                    self._last_mtimes = get_snapshot_mtimes(cfg)
        with self._lock:
            dash = assemble_dashboard(self._raw, cfg)
        if ids:
            self._after_data(dash)
        return dash

    def _rebuild(self) -> dict[str, Any]:
        """Dashboard from cached data (no collection), e.g. after a reset time passed."""
        with self._lock:
            dash = assemble_dashboard(self._raw)
        self._after_data(dash)
        return dash

    def _after_data(self, dash: dict[str, Any]) -> None:
        """New numbers: warn if a quota dropped low, recolour the tray icon."""
        try:
            self._notifier.check(dash)
        except Exception:
            pass
        self._update_tray(dash)

    def _update_tray(self, dash: dict[str, Any]) -> None:
        if self._tray is None:
            return
        try:
            info = insights.summary(dash.get("providers") or [], dash.get("alerts") or {})
            lines = list(info["lines"])
            adv = dash.get("advice") or {}
            best = next((it for it in adv.get("items") or [] if it["id"] == adv.get("best")), None)
            if best is not None:
                lines.append(f"En uygun: {best['name']}")
            self._tray.update(info["lowest"], info["status"], lines)
        except Exception:
            pass

    def _send_notification(self, title: str, message: str) -> None:
        if self._tray is not None and not self._quitting:
            self._tray.notify(title, message)

    def dashboard(self) -> dict[str, Any]:
        missing = self.missing_providers()
        if missing:
            return self.refresh(missing)
        with self._lock:
            return assemble_dashboard(self._raw)

    @property
    def _visible(self) -> bool:
        return self._mode != "tray"

    def _windows(self) -> list[webview.Window]:
        wins = [self._window]
        if self._widget is not None and self._widget_ready.is_set():
            wins.append(self._widget)
        return [w for w in wins if w is not None]

    def _active_window(self) -> webview.Window | None:
        if self._mode == "widget":
            return self._widget
        if self._mode == "main":
            return self._window
        return None

    def _push_to_ui(self, payload: dict[str, Any]) -> None:
        """Render new data in the visible window only; hidden ones catch up when shown."""
        if self._quitting:
            return
        with self._lock:
            self._latest = payload
            self._latest_ver += 1
        self._send_latest(self._active_window())

    def _send_latest(self, window: webview.Window | None) -> None:
        if window is None or self._latest is None:
            return
        if window is self._widget and not self._widget_ready.is_set():
            return  # it fetches the dashboard itself once loaded
        uid = str(window.uid)
        with self._lock:
            if self._window_ver.get(uid) == self._latest_ver:
                return
            self._window_ver[uid] = self._latest_ver
            encoded = json.dumps(self._latest, ensure_ascii=False)
        self._js.send(
            window, "dashboard",
            f"window.__onDashboardUpdate && window.__onDashboardUpdate({encoded});",
        )

    def _background_loop(self) -> None:
        while not self._stop_event.wait(LOOP_INTERVAL_SEC):
            if self._quitting:
                break
            try:
                cfg = load_config()
                due: list[str] = []

                # A watched quota's reset time passed: say so, and redraw with it counted as full.
                if self._notifier.check_resets(cfg["alerts"]) and not due:
                    self._push_to_ui(self._rebuild())

                # statusLine providers: re-read only the snapshot that changed (cheap file read).
                mtimes = get_snapshot_mtimes(cfg)
                for pid, mtime in mtimes.items():
                    if mtime != self._last_mtimes.get(pid):
                        due.append(pid)

                # Codex: only when it can have changed (see CODEX_* above).
                if is_enabled(cfg, "codex") and self._codex_due():
                    due.append("codex")
                if "claude" not in due and is_enabled(cfg, "claude") and self._claude_due():
                    due.append("claude")

                if due:
                    self._push_to_ui(self.refresh(due))
            except Exception:
                pass

    def _claude_due(self) -> bool:
        now = time.time()
        since = now - self._last_claude_poll
        windows = (self._raw.get("claude") or {}).get("windows") or []
        for w in windows:
            reset = w.get("resets_at")
            if isinstance(reset, (int, float)) and self._last_claude_poll < reset + 5 <= now:
                return True
        return since >= (CLAUDE_MAX_AGE_VISIBLE if self._visible else CLAUDE_MAX_AGE_HIDDEN)

    def _codex_due(self) -> bool:
        now = time.time()
        since = now - self._last_codex_poll
        visible = self._visible

        activity = codex_activity_mtime()
        if activity > self._codex_activity:
            gap = CODEX_ACTIVITY_GAP_VISIBLE if visible else CODEX_ACTIVITY_GAP_HIDDEN
            if since >= gap:
                self._codex_activity = activity
                return True

        # A window reset since the last poll: usage dropped without any activity.
        windows = (self._raw.get("codex") or {}).get("windows") or []
        for w in windows:
            reset = w.get("resets_at")
            if isinstance(reset, (int, float)) and self._last_codex_poll < reset + 5 <= now:
                return True

        return since >= (CODEX_MAX_AGE_VISIBLE if visible else CODEX_MAX_AGE_HIDDEN)

    # ------------------------------------------------------------ window

    def toggle_window(self) -> None:
        if self._mode == "main":
            self.hide_all()
        else:
            self.show_window()

    def toggle_widget(self) -> None:
        """Tray icon click: show the widget, or put it back into the tray if it is showing."""
        if self._mode == "widget":
            self.hide_all()
        else:
            self.show_widget()

    def _set_mode(self, mode: str) -> None:
        was_visible = self._visible
        self._mode = mode
        self._notify_visibility(self._window, mode == "main")
        self._notify_visibility(self._widget, mode == "widget")
        self._send_latest(self._active_window())   # catch up on updates made while hidden
        # Codex may be stale after a long stay in the tray.
        if (
            not was_visible
            and self._visible
            and time.time() - self._last_codex_poll >= CODEX_MAX_AGE_VISIBLE
        ):
            threading.Thread(
                target=lambda: self._push_to_ui(self.refresh(["codex"])), daemon=True
            ).start()

    def show_window(self) -> None:
        """Full panel; the widget steps aside."""
        if self._window is None:
            return
        self._hide_widget()
        try:
            self._window.show()
            self._window.restore()
        except Exception:
            pass
        self._set_mode("main")

    def show_widget(self) -> None:
        """Compact always-on-top widget in the bottom-right corner; the panel hides."""
        if self._quitting:
            return
        if self._window is not None:
            try:
                self._window.hide()
            except Exception:
                pass
        if self._widget is None:
            self._create_widget()   # becomes visible on its own
        else:
            try:
                self._widget.show()
            except Exception:
                pass
            self.apply_widget_look()
            if self._docked:
                self._dock_hidden = False
                self._place_docked()
        self._set_mode("widget")
        if self._docked:
            self._start_dock_watch()

    def hide_all(self) -> None:
        """Everything into the tray."""
        for win in (self._window, self._widget):
            if win is not None:
                try:
                    win.hide()
                except Exception:
                    pass
        self._set_mode("tray")

    def _hide_widget(self) -> None:
        if self._widget is not None:
            try:
                self._widget.hide()
            except Exception:
                pass

    def _widget_url(self) -> str:
        return (UI_WIDGET_COMPACT if self._widget_style == "compact" else UI_WIDGET).as_uri()

    def _widget_geometry(self) -> tuple[int, int]:
        """Window size (logical px) for the current design."""
        if self._widget_style == "compact":
            if self._compact_size is not None:
                return self._compact_size
            return COMPACT_BASE_W + self._widget_cols * COMPACT_COL_W, COMPACT_H
        return widget_width(self._widget_cols, self._widget_col_w), self._widget_h

    def _create_widget(self) -> None:
        cfg = load_config()
        self._widget_cols = max(1, sum(1 for pid in PROVIDER_IDS if is_enabled(cfg, pid)))
        width, height = self._widget_geometry()
        main_hwnd = winutil.hwnd_of(self._window) if self._window is not None else None
        x, y = winutil.bottom_right_logical(width, height, main_hwnd)

        widget = webview.create_window(
            title="QuotaTank Widget",
            url=self._widget_url(),
            js_api=QuotaApi(self),
            width=width,
            height=height,
            min_size=WINDOW_MIN,
            x=x,
            y=y,
            resizable=False,
            frameless=True,
            easy_drag=False,
            on_top=True,
            shadow=True,
            background_color="#0b0e15",
        )

        def on_shown() -> None:
            hwnd = winutil.hwnd_of(widget)
            if hwnd:
                w, h = self._widget_geometry()
                winutil.dock_widget(hwnd, w, h, main_hwnd, pos=self._widget_pos)
                if self._docked:
                    winutil.set_square_corners(hwnd, True)
                    self._place_docked()
                    self._start_dock_watch()
                self.apply_widget_look()

        def on_loaded() -> None:
            self._widget_ready.set()
            self._notify_visibility(widget, self._mode == "widget")

        def on_closing() -> bool:
            if self._quitting:
                return True
            # Runs on the UI thread and pywebview waits for it: hand the work off,
            # anything that talks to a window from here would deadlock.
            threading.Thread(target=self.hide_all, daemon=True).start()
            return False

        widget.events.shown += on_shown
        widget.events.loaded += on_loaded
        widget.events.closing += on_closing
        widget.events.moved += self._on_moved
        widget.events.moved += self._on_widget_moved
        self._widget = widget

    def apply_widget_look(self, cfg: dict[str, Any] | None = None) -> None:
        """Opacity and click-through from the settings."""
        wcfg = dict((cfg or load_config())["widget"])
        self._widget_look = wcfg
        if self._widget is None:
            return
        hwnd = winutil.hwnd_of(self._widget)
        if not hwnd:
            return
        self._ct_pass_now = bool(wcfg["click_through"])
        try:
            winutil.set_see_through(hwnd, int(wcfg["opacity"]), self._ct_pass_now)
        except Exception:
            pass
        if wcfg["click_through"]:
            self._start_pass_watch()

    def is_click_through(self) -> bool:
        return bool(load_config()["widget"]["click_through"])

    def toggle_click_through(self) -> bool:
        cfg = load_config()
        cfg["widget"]["click_through"] = not cfg["widget"]["click_through"]
        saved = save_config(cfg)
        on = bool(saved["widget"]["click_through"])
        self.apply_widget_look(saved)
        if self._tray is not None:
            self._tray.refresh_menu()
        if self._widget is not None and self._widget_ready.is_set():
            self._js.send(
                self._widget, "clickthrough",
                f"window.__setClickThrough && window.__setClickThrough({'true' if on else 'false'});",
            )
        return on

    def set_pass_button_rect(self, x: float, y: float, w: float, h: float) -> None:
        self._ct_hot = (float(x), float(y), float(w), float(h))

    def _start_pass_watch(self) -> None:
        if self._ct_thread is not None and self._ct_thread.is_alive():
            return
        self._ct_thread = threading.Thread(target=self._pass_watch, daemon=True, name="pass-watch")
        self._ct_thread.start()

    def _pass_watch(self) -> None:
        """While clicks pass through, keep the widget's own toggle button clickable.

        A window that lets clicks through gets no mouse input at all, so the button
        could never be pressed. Instead the cursor is checked ~20 times a second and
        pass-through is lifted only while the cursor is over that button.
        """
        while not self._stop_event.wait(0.05):
            look = self._widget_look or {}
            if not look.get("click_through") or self._quitting:
                break
            if self._widget is None or self._mode != "widget":
                continue
            hwnd = winutil.hwnd_of(self._widget)
            if not hwnd:
                continue
            want_pass = not winutil.cursor_in_client_rect(hwnd, self._ct_hot, pad=3)
            if want_pass != self._ct_pass_now:
                self._ct_pass_now = want_pass
                try:
                    winutil.set_see_through(hwnd, int(look["opacity"]), want_pass)
                except Exception:
                    pass
        self._ct_thread = None

    # ------------------------------------------------------------ settings side effects

    def apply_settings(self, cfg: dict[str, Any]) -> None:
        self.apply_widget_look(cfg)
        self.apply_widget_style(cfg["widget"]["style"])
        self.apply_widget_dock(bool(cfg["widget"]["dock"]))
        self._apply_hotkey(cfg)
        if self._tray is not None:
            self._tray.refresh_menu()
        try:
            self._update_tray(self._rebuild_quiet())
        except Exception:
            pass

    def _rebuild_quiet(self) -> dict[str, Any]:
        with self._lock:
            return assemble_dashboard(self._raw)

    def _apply_hotkey(self, cfg: dict[str, Any]) -> None:
        sys_cfg = cfg["system"]
        spec = sys_cfg["hotkey"] if sys_cfg["hotkey_enabled"] else None
        if self._hotkey is not None:
            if spec == self._hotkey.spec and self._hotkey.ok:
                return
            self._hotkey.stop()
            self._hotkey = None
        if spec:
            self._hotkey = GlobalHotkey(spec, self.toggle_widget)
            self._hotkey.start()

    def hotkey_ok(self) -> bool:
        return self._hotkey is not None and self._hotkey.ok

    # ------------------------------------------------------------ widget design

    def is_compact_widget(self) -> bool:
        return self._widget_style == "compact"

    def toggle_widget_style(self) -> None:
        """Tray menu: switch between the tank and the compact design."""
        cfg = load_config()
        cfg["widget"]["style"] = "tank" if cfg["widget"]["style"] == "compact" else "compact"
        saved = save_config(cfg)
        self.apply_widget_style(saved["widget"]["style"])

    def apply_widget_style(self, style: str) -> None:
        """Swap the widget's page in place (same window, same spot) when the design changes."""
        if style == self._widget_style:
            return
        self._widget_style = style
        self._compact_expanded = False
        if self._tray is not None:
            self._tray.refresh_menu()
        if self._widget is None:
            return   # created with the right page when first shown
        cfg = load_config()
        self._widget_cols = max(1, sum(1 for pid in PROVIDER_IDS if is_enabled(cfg, pid)))
        # The new page fetches the dashboard itself once loaded (on_loaded sets ready again).
        self._widget_ready.clear()
        hwnd = winutil.hwnd_of(self._widget)
        if hwnd:
            w, h = self._widget_geometry()
            winutil.resize_keep_bottom_right(hwnd, w, h)
        try:
            self._widget.load_url(self._widget_url())
        except Exception:
            self._widget_ready.set()   # old page stays: keep feeding it
        self._sync_dock()

    def fit_widget_content(self, width: float, height: float, expanded: bool) -> None:
        """Compact design: the page measured itself; give the window exactly that size.

        The bottom edge stays put, so the detail card grows the window upwards and the
        bar itself does not move.
        """
        if self._widget is None or self._widget_style != "compact":
            return
        w, h = _clamp_compact(width, height)
        self._compact_expanded = expanded
        if self._docked:
            self._place_docked(w, h)
            return
        hwnd = winutil.hwnd_of(self._widget)
        if hwnd:
            winutil.resize_keep_bottom_right(hwnd, w, h)
        if not expanded and (w, h) != self._compact_size:
            self._compact_size = (w, h)
            self._save_widget_state()

    # ------------------------------------------------------------ taskbar docking

    @property
    def _docked(self) -> bool:
        return self._widget_style == "compact" and self._dock_pref

    def toggle_widget_dock(self) -> None:
        """Tray menu: put the bar on the taskbar (switches to the compact design) or take it off."""
        cfg = load_config()
        on = not self._docked
        cfg["widget"]["dock"] = on
        if on:
            cfg["widget"]["style"] = "compact"
        saved = save_config(cfg)
        self.apply_widget_style(saved["widget"]["style"])
        self.apply_widget_dock(bool(saved["widget"]["dock"]))

    def apply_widget_dock(self, on: bool) -> None:
        self._dock_pref = bool(on)
        self._sync_dock()

    def _sync_dock(self) -> None:
        """Move the widget onto / off the taskbar when the docked state changed."""
        docked = self._docked
        if docked == self._dock_applied:
            return
        self._dock_applied = docked
        self._compact_expanded = False
        if self._tray is not None:
            self._tray.refresh_menu()
        if self._widget is None:
            return
        hwnd = winutil.hwnd_of(self._widget)
        if not hwnd:
            return
        winutil.set_square_corners(hwnd, docked)
        if docked:
            self._place_docked()
            if self._mode == "widget":
                self._start_dock_watch()
        else:
            if self._dock_hidden and self._mode == "widget":
                winutil.show_no_activate(hwnd, True)
            self._dock_hidden = False
            w, h = self._widget_geometry()
            s = winutil.scale_of(hwnd)
            pos = self._widget_pos
            if pos is not None:
                winutil.set_rect(hwnd, pos[0], pos[1], int(w * s), int(h * s))
            else:
                winutil.place_bottom_right(hwnd, w, h, hwnd)
        self._push_dock_info()

    def dock_info(self) -> dict[str, Any]:
        if self._docked and self._widget is not None:
            hwnd = winutil.hwnd_of(self._widget)
            tb = winutil.taskbar_for(hwnd)
            rect = winutil.window_rect(tb) if tb else None
            if rect and (rect[2] - rect[0]) > (rect[3] - rect[1]):
                self._dock_tb_h = (rect[3] - rect[1]) / winutil.scale_of(tb)
                self._sample_dock_bg(hwnd, rect)
        return {"docked": self._docked, "height": round(self._dock_tb_h), "bg": self._dock_bg}

    def _sample_dock_bg(self, hwnd: int | None, tb_rect: tuple[int, int, int, int]) -> bool:
        """Read the taskbar colour beside the bar; True if it changed."""
        own = winutil.window_rect(hwnd) if hwnd else None
        try:
            color = winutil.taskbar_color(tb_rect, (own[0], own[2]) if own else None)
        except Exception:
            color = None
        if color and color != self._dock_bg:
            self._dock_bg = color
            return True
        return False

    def _push_dock_info(self) -> None:
        if self._widget is None or not self._widget_ready.is_set():
            return   # a page still loading asks for it itself
        info = json.dumps(self.dock_info())
        self._js.send(self._widget, "dock", f"window.__setDock && window.__setDock({info});")

    def _place_docked(self, width: float | None = None, height: float | None = None) -> bool:
        """Put the bar on the taskbar: bottom-aligned, right edge `_dock_off` from the taskbar's end."""
        if self._widget is None:
            return False
        hwnd = winutil.hwnd_of(self._widget)
        if not hwnd:
            return False
        tb = winutil.taskbar_for(hwnd)
        tbr = winutil.window_rect(tb) if tb else None
        if not tbr or (tbr[2] - tbr[0]) <= (tbr[3] - tbr[1]):
            return False   # no taskbar, or a vertical one: stay where it is
        s = winutil.scale_of(hwnd)
        cur = winutil.window_rect(hwnd)
        if width is None or height is None:
            if cur is None:
                return False
            w, h = cur[2] - cur[0], cur[3] - cur[1]
        else:
            w, h = int(round(width * s)), int(round(height * s))
        self._dock_tb_h = (tbr[3] - tbr[1]) / s
        left, right, bottom = tbr[0], tbr[2], tbr[3]
        if winutil.taskbar_autohide():
            mon = winutil.monitor_rect(tb)
            if mon:
                bottom = mon[3]   # where the taskbar appears, not where it hides
        if self._dock_off is None:
            tray = winutil.tray_area_left(tb)
            off = (right - tray) / s + DOCK_GAP if tray else DOCK_FALLBACK_OFF
        else:
            off = self._dock_off
        x = int(round(right - off * s)) - w
        x = max(left, min(right - w, x))
        if self._dock_off is not None or self._dock_drag_off0 is not None:
            self._dock_off = (right - (x + w)) / s   # keep what the clamp allowed
        winutil.set_rect(hwnd, x, bottom - h, w, h)
        return True

    def dock_drag(self, dx: float, done: bool) -> None:
        """Horizontal drag along the taskbar (dx: logical px since the press)."""
        if not self._docked:
            return
        if self._dock_drag_off0 is None:
            if self._dock_off is None:
                hwnd = winutil.hwnd_of(self._widget) if self._widget else None
                tb = winutil.taskbar_for(hwnd) if hwnd else None
                tbr = winutil.window_rect(tb) if tb else None
                cur = winutil.window_rect(hwnd) if hwnd else None
                if not (tbr and cur):
                    return
                self._dock_off = (tbr[2] - cur[2]) / winutil.scale_of(hwnd)
            self._dock_drag_off0 = self._dock_off
        self._dock_off = max(0.0, self._dock_drag_off0 - dx)
        self._place_docked()
        if done:
            self._dock_drag_off0 = None
            self._save_widget_state()

    def _start_dock_watch(self) -> None:
        if self._dock_thread is not None and self._dock_thread.is_alive():
            return
        self._dock_thread = threading.Thread(target=self._dock_watch, daemon=True, name="dock-watch")
        self._dock_thread.start()

    def _dock_watch(self) -> None:
        """Keep the docked bar on top of the taskbar, out of the way of fullscreen apps.

        The taskbar raises itself whenever it is clicked; checking the z-order a few
        times a second is a handful of cheap Win32 calls.
        """
        tick = 0
        last_tb: tuple[int, int, int, int] | None = None
        while not self._stop_event.wait(0.15):
            if not self._docked or self._quitting:
                break
            if self._widget is None or self._mode != "widget":
                continue
            hwnd = winutil.hwnd_of(self._widget)
            tb = winutil.taskbar_for(hwnd) if hwnd else None
            if not hwnd or not tb:
                continue
            try:
                mon = winutil.monitor_rect(tb)
                full = bool(mon) and winutil.fullscreen_foreground(mon)
                if full != self._dock_hidden:
                    self._dock_hidden = full
                    winutil.show_no_activate(hwnd, not full)
                if full:
                    continue
                if winutil.is_covered_by(hwnd, tb):
                    winutil.raise_topmost(hwnd)
                tick += 1
                if tick % 13 == 0:   # ~2 s: resolution / taskbar size / explorer restart
                    tbr = winutil.window_rect(tb)
                    if tbr != last_tb:
                        old_h = self._dock_tb_h
                        last_tb = tbr
                        self._place_docked()
                        if round(self._dock_tb_h) != round(old_h):
                            self._push_dock_info()
                if tick % 40 == 0 and last_tb:   # ~6 s: taskbar tint follows the wallpaper
                    if self._sample_dock_bg(hwnd, last_tb):
                        self._push_dock_info()
            except Exception:
                pass
        self._dock_thread = None

    def fit_widget(self, columns: int) -> None:
        columns = max(1, min(3, columns))
        if self._widget is None or columns == self._widget_cols or self._widget_style != "tank":
            return
        self._widget_cols = columns
        hwnd = winutil.hwnd_of(self._widget)
        if hwnd:
            # keep the user's column width, so the widget just gains/loses a column
            winutil.resize_keep_bottom_right(
                hwnd, widget_width(columns, self._widget_col_w), self._widget_h
            )

    def set_widget_bounds(self, x: float, y: float, width: float, height: float) -> None:
        """Live resize from the widget's corner/edge grips (logical px)."""
        if self._widget is None:
            return
        hwnd = winutil.hwnd_of(self._widget)
        if not hwnd:
            return
        min_w = widget_width(self._widget_cols, WIDGET_MIN_COL_W)
        max_w = widget_width(self._widget_cols, WIDGET_MAX_COL_W)
        w = max(min_w, min(max_w, float(width)))
        h = max(WIDGET_MIN_H, min(WIDGET_MAX_H, float(height)))
        winutil.set_bounds_logical(hwnd, float(x), float(y), w, h)

    def save_widget_size(self, width: float, height: float) -> None:
        self._widget_col_w = col_width_for(self._widget_cols, float(width))
        self._widget_h = int(max(WIDGET_MIN_H, min(WIDGET_MAX_H, float(height))))
        self._save_widget_state()

    def _on_widget_moved(self, x: int, y: int) -> None:
        # Fires for every pixel of a drag: save once the widget has been still for a moment.
        if self._pos_timer is not None:
            self._pos_timer.cancel()
        self._pos_timer = threading.Timer(0.8, self._remember_widget_pos)
        self._pos_timer.daemon = True
        self._pos_timer.start()

    def _remember_widget_pos(self) -> None:
        if self._widget is None or self._quitting or self._compact_expanded or self._docked:
            return   # taller window with the card open / taskbar spot: not the floating spot
        hwnd = winutil.hwnd_of(self._widget)
        rect = winutil.window_rect(hwnd) if hwnd else None
        if rect is None:
            return
        pos = (rect[0], rect[1])
        if pos != self._widget_pos:
            self._widget_pos = pos
            self._save_widget_state()

    def _save_widget_state(self) -> None:
        state: dict[str, Any] = {"col_w": round(self._widget_col_w, 1), "h": self._widget_h}
        if self._compact_size is not None:
            state["compact_w"], state["compact_h"] = self._compact_size
        if self._widget_pos is not None:
            state["x"], state["y"] = self._widget_pos
        if self._dock_off is not None:
            state["dock_off"] = round(self._dock_off, 1)
        try:
            write_json_atomic(_widget_size_path(), state)
        except Exception:
            pass

    def _on_minimized(self) -> None:
        if self._quitting or self._mode != "main":
            return
        if load_config()["ui"].get("minimize_to_widget", True):
            self.show_widget()

    # ------------------------------------------------------------ slosh

    def _on_moved(self, x: int, y: int) -> None:
        # Called by pywebview on every window move; keep it trivial.
        if not self._slosh_enabled:
            return
        with self._move_lock:
            self._move_samples.append((int(x), int(y), round(time.perf_counter() * 1000.0, 1)))
            if len(self._move_samples) > 240:
                del self._move_samples[:-240]
        self._move_wakeup.set()

    def set_slosh_enabled(self, enabled: bool) -> None:
        self._slosh_enabled = bool(enabled)
        if not enabled:
            with self._move_lock:
                self._move_samples.clear()

    def _move_forwarder(self) -> None:
        """Batch move samples to the UI at most ~60 times per second.

        Sleeps on an event, so it costs nothing while the window is not being moved.
        """
        while not self._stop_event.is_set():
            if not self._move_wakeup.wait(1.0):
                continue
            self._move_wakeup.clear()
            with self._move_lock:
                samples, self._move_samples = self._move_samples, []
            target = self._active_window()
            if samples and target is not None and not self._quitting:
                try:
                    target.evaluate_js(
                        f"window.__onWindowMoved && window.__onWindowMoved({json.dumps(samples)});"
                    )
                except Exception:
                    pass
            time.sleep(1 / 60)

    def _notify_visibility(self, window: webview.Window | None, visible: bool) -> None:
        """Tell a window to start/stop its liquid animation (hidden window = zero redraws)."""
        if window is None or self._quitting:
            return
        if window is self._widget and not self._widget_ready.is_set():
            return  # it reports its state itself once loaded
        self._js.send(
            window, "visibility",
            f"window.__setAppVisible && window.__setAppVisible({'true' if visible else 'false'});",
        )

    def set_on_top(self, enabled: bool) -> None:
        if self._window is None:
            return
        try:
            self._window.on_top = enabled
        except Exception:
            pass

    def refresh_from_tray(self) -> None:
        self._push_to_ui(self.refresh(list(PROVIDER_IDS)))

    def quit(self) -> None:
        self._quitting = True
        self._stop_event.set()
        self._js.stop()
        if self._hotkey is not None:
            self._hotkey.stop()
        if self._pos_timer is not None:
            self._pos_timer.cancel()
        if self._tray is not None:
            self._tray.stop()
        for win in (self._widget, self._window):
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass

    def _on_closing(self) -> bool:
        if self._quitting:
            return True
        # Minimize to system tray instead of closing completely. This handler runs on
        # the UI thread and pywebview waits for it, so the work happens elsewhere
        # (calling into a window from here deadlocks -> "not responding").
        threading.Thread(target=self.hide_all, daemon=True).start()
        return False

    def run(self, instance: SingleInstance) -> None:
        get_snapshot_dir().mkdir(parents=True, exist_ok=True)
        api = QuotaApi(self)

        self._window = webview.create_window(
            title="QuotaTank — AI CLI Kota Monitörü",
            url=UI_INDEX.as_uri(),
            js_api=api,
            width=1180,
            height=900,
            min_size=(860, 640),
            background_color="#080a0f",
            text_select=True,
            hidden=self._start_hidden,
        )
        self._window.events.closing += self._on_closing
        self._window.events.moved += self._on_moved
        self._window.events.minimized += self._on_minimized

        self._tray = TrayController(
            on_toggle_window=self.show_window,
            on_show_widget=self.toggle_widget,
            on_refresh=self.refresh_from_tray,
            on_quit=self.quit,
            on_toggle_click_through=self.toggle_click_through,
            is_click_through=self.is_click_through,
            on_toggle_compact=self.toggle_widget_style,
            is_compact=self.is_compact_widget,
            on_toggle_dock=self.toggle_widget_dock,
            is_docked=lambda: self._docked,
        )

        def on_started() -> None:
            self._tray.start()
            cfg = load_config()
            self._apply_hotkey(cfg)
            if self._start_hidden and cfg["system"]["start_mode"] == "widget":
                self.show_widget()
            instance.listen(self.show_window, self._stop_event)
            threading.Thread(target=self._background_loop, daemon=True).start()
            threading.Thread(target=self._move_forwarder, daemon=True).start()

        icon = str(APP_ICON) if APP_ICON.exists() else None
        webview.start(on_started, debug=False, icon=icon)


def _set_app_identity() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def main() -> int:
    _set_app_identity()
    instance = SingleInstance()
    if not instance.is_primary:
        # Already running: bring that window to the front instead of starting a copy.
        instance.signal_primary()
        return 0
    app = QuotaMonitorApp(start_hidden=autostart.AUTOSTART_FLAG in sys.argv[1:])
    app.run(instance)
    return 0


if __name__ == "__main__":
    sys.exit(main())
