"""OpenAI Codex CLI collector via official app-server JSON-RPC with session fallback."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.paths import hidden_popen_kwargs


def _normalize_window(
    label: str,
    window_key: str,
    raw: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("usedPercent")
    if used is None:
        used = raw.get("used_percent")
    if used is None:
        return None
    used_f = round(float(used), 1)
    rem_f = round(max(0.0, min(100.0, 100.0 - used_f)), 1)
    resets_at = raw.get("resetsAt") or raw.get("resets_at")
    mins = raw.get("windowDurationMins") or raw.get("window_minutes")
    return {
        "key": window_key,
        "label": label,
        "used_percent": used_f,
        "remaining_percent": rem_f,
        "resets_at": int(resets_at) if resets_at else None,
        "window_minutes": int(mins) if mins else None,
    }


def _query_app_server(timeout_sec: float = 8.0) -> dict[str, Any]:
    proc = subprocess.Popen(
        "codex app-server --stdio",
        shell=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        **hidden_popen_kwargs(),
    )

    try:
        assert proc.stdin is not None and proc.stdout is not None

        def send(payload: dict[str, Any]) -> None:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "ai-quota-monitor", "version": "1.0.0"},
                    "capabilities": None,
                },
            }
        )

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("Codex app-server closed before initialize.")
            msg = json.loads(line)
            if msg.get("id") == 1:
                break
        else:
            raise TimeoutError("Codex app-server initialize timed out.")

        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read", "params": None})
        send({"id": 3, "method": "account/read", "params": {"refreshToken": False}})
        send({"id": 4, "method": "account/usage/read", "params": {}})

        responses: dict[int, dict[str, Any]] = {}
        while time.time() < deadline and len(responses) < 3:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except Exception:
                continue
            msg_id = msg.get("id")
            if msg_id in (2, 3, 4):
                responses[msg_id] = msg

        return responses
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _fallback_from_sessions() -> dict[str, Any] | None:
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.exists():
        return None
    files = sorted(
        sessions_dir.rglob("rollout-*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in files[:5]:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line in reversed(lines):
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") == "event_msg":
                payload = obj.get("payload") or {}
                if payload.get("type") == "token_count" and payload.get("rate_limits"):
                    return {
                        "rate_limits": payload["rate_limits"],
                        "captured_at": int(path.stat().st_mtime),
                    }
    return None


def collect_codex() -> dict[str, Any]:
    now_epoch = int(time.time())
    try:
        responses = _query_app_server()
        rate_res = (responses.get(2) or {}).get("result")
        acct_res = (responses.get(3) or {}).get("result") or {}
        usage_res = (responses.get(4) or {}).get("result") or {}

        if not isinstance(rate_res, dict) or not rate_res.get("rateLimits"):
            raise RuntimeError("rateLimits payload missing from Codex app-server")

        rl = rate_res["rateLimits"]
        windows = []
        primary = _normalize_window("5 Saatlik Pencere", "5h", rl.get("primary"))
        secondary = _normalize_window("Haftalık Pencere", "7d", rl.get("secondary"))
        if primary:
            windows.append(primary)
        if secondary:
            windows.append(secondary)

        account = acct_res.get("account") or {}
        plan_type = rl.get("planType") or account.get("planType") or "Bilinmiyor"
        credits_info = rl.get("credits") or {}
        reset_credits = (rate_res.get("rateLimitResetCredits") or {}).get(
            "availableCount", 0
        )

        usage_summary = usage_res.get("summary") or {}
        buckets = usage_res.get("dailyUsageBuckets") or []
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_tokens = 0
        last_7d_tokens = 0
        for b in buckets[-7:]:
            last_7d_tokens += int(b.get("tokens") or 0)
            if b.get("startDate") == today_str:
                today_tokens = int(b.get("tokens") or 0)

        return {
            "id": "codex",
            "name": "OpenAI Codex CLI",
            "status": "live",
            "status_label": "Canlı (RPC)",
            "plan": str(plan_type).capitalize(),
            "captured_at_epoch": now_epoch,
            "windows": windows,
            "models": [
                {"name": "Codex (GPT-5 / o-series)", "note": "Ortak 5s & 7g kota havuzu"}
            ],
            "extras": {
                "ordinary_usage_allowed": rate_res.get("ordinaryUsageAllowed", True),
                "reset_credits_available": reset_credits,
                "credits_balance": credits_info.get("balance", "0"),
                "today_tokens": today_tokens,
                "week_tokens": last_7d_tokens,
                "lifetime_tokens": usage_summary.get("lifetimeTokens"),
                "streak_days": usage_summary.get("currentStreakDays"),
            },
            "error": None,
        }
    except Exception as exc:
        fb = _fallback_from_sessions()
        if fb:
            rl = fb["rate_limits"]
            windows = []
            primary = _normalize_window("5 Saatlik Pencere", "5h", rl.get("primary"))
            secondary = _normalize_window("Haftalık Pencere", "7d", rl.get("secondary"))
            if primary:
                windows.append(primary)
            if secondary:
                windows.append(secondary)
            return {
                "id": "codex",
                "name": "OpenAI Codex CLI",
                "status": "cached",
                "status_label": "Son Oturumdan",
                "plan": str(rl.get("plan_type") or "Bilinmiyor").capitalize(),
                "captured_at_epoch": fb["captured_at"],
                "windows": windows,
                "models": [
                    {"name": "Codex CLI", "note": "Son oturum kaydından okundu"}
                ],
                "extras": {},
                "error": f"Canlı RPC başarısız ({exc}); son oturum gösteriliyor.",
            }

        return {
            "id": "codex",
            "name": "OpenAI Codex CLI",
            "status": "error",
            "status_label": "Bağlantı Hatası",
            "plan": "—",
            "captured_at_epoch": now_epoch,
            "windows": [],
            "models": [],
            "extras": {},
            "error": str(exc),
        }


def codex_activity_mtime() -> float:
    """Newest modification time among recent Codex session logs (0 if none).

    Rate limits only move when Codex is used, and every use appends to a rollout file
    under ~/.codex/sessions/YYYY/MM/DD. Checking today's and yesterday's folders is a
    couple of directory listings, far cheaper than spawning the app-server.
    """
    root = Path.home() / ".codex" / "sessions"
    newest = 0.0
    now = datetime.now()
    for day in (now, now - timedelta(days=1)):
        folder = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.name.endswith(".jsonl"):
                        try:
                            newest = max(newest, entry.stat().st_mtime)
                        except OSError:
                            pass
        except OSError:
            continue
    return newest
