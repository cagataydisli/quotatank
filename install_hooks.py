"""Safely wire or restore passive statusLine hooks for Antigravity CLI and Claude Code.

The two small .cmd wrappers in collector/ are generated here, on each machine:
they point to this machine's Node.js and pass the CLI's data on to whatever
statusLine command was configured before (so an existing status bar keeps working).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
AGY_CMD = (PROJECT_ROOT / "collector" / "agy-statusline.cmd").as_posix()
CLAUDE_CMD = (PROJECT_ROOT / "collector" / "claude-statusline.cmd").as_posix()

AGY_SETTINGS = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
STATE_FILE = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    / "AIQuotaMonitor" / "hook-backup.json"
)
BRIDGE_JS = PROJECT_ROOT / "collector" / "statusline-bridge.js"


def _node() -> str:
    found = shutil.which("node")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"
    return str(default) if default.exists() else "node"


def _previous_command(prev) -> str | None:
    """The command of a statusLine that was configured before ours (to keep it working)."""
    if isinstance(prev, dict) and isinstance(prev.get("command"), str):
        cmd = prev["command"].strip()
        if cmd and cmd not in (AGY_CMD, CLAUDE_CMD):
            return cmd
    return None


def write_wrapper(cmd_path: str, source: str, forward: str | None) -> None:
    """collector/<source>-statusline.cmd: save a snapshot, then hand the data on."""
    fwd = f' --forward "{forward}"' if forward else ""
    text = (
        "@echo off\r\n"
        "setlocal DisableDelayedExpansion\r\n"
        f'"{_node()}" "%~dp0{BRIDGE_JS.name}" --source {source}{fwd}\r\n'
    )
    Path(cmd_path).write_text(text, encoding="utf-8", newline="")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_with_backup(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def install() -> None:
    state = load_json(STATE_FILE)

    # Antigravity CLI
    if AGY_SETTINGS.parent.exists():
        agy_cfg = load_json(AGY_SETTINGS)
        prev = agy_cfg.get("statusLine")
        if not (isinstance(prev, dict) and prev.get("command") == AGY_CMD):
            state["agy_previous_statusLine"] = prev
        write_wrapper(AGY_CMD, "agy", _previous_command(state.get("agy_previous_statusLine")))
        agy_cfg["statusLine"] = {"type": "command", "command": AGY_CMD}
        save_json_with_backup(AGY_SETTINGS, agy_cfg)
        print(f"[OK] Antigravity CLI statusLine -> {AGY_CMD}")
    else:
        print(f"[SKIP] Antigravity config folder not found: {AGY_SETTINGS.parent}")

    # Claude Code
    if CLAUDE_SETTINGS.parent.exists():
        claude_cfg = load_json(CLAUDE_SETTINGS)
        prev = claude_cfg.get("statusLine")
        if not (isinstance(prev, dict) and prev.get("command") == CLAUDE_CMD):
            state["claude_previous_statusLine"] = prev
        write_wrapper(CLAUDE_CMD, "claude", _previous_command(state.get("claude_previous_statusLine")))
        claude_cfg["statusLine"] = {"type": "command", "command": CLAUDE_CMD}
        save_json_with_backup(CLAUDE_SETTINGS, claude_cfg)
        print(f"[OK] Claude Code statusLine -> {CLAUDE_CMD}")
    else:
        print(f"[SKIP] Claude config folder not found: {CLAUDE_SETTINGS.parent}")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def uninstall() -> None:
    state = load_json(STATE_FILE)

    if AGY_SETTINGS.exists():
        agy_cfg = load_json(AGY_SETTINGS)
        if agy_cfg.get("statusLine", {}).get("command") == AGY_CMD:
            prev = state.get("agy_previous_statusLine")
            if prev is None:
                agy_cfg.pop("statusLine", None)
            else:
                agy_cfg["statusLine"] = prev
            save_json_with_backup(AGY_SETTINGS, agy_cfg)
            print("[OK] Restored Antigravity CLI statusLine")

    if CLAUDE_SETTINGS.exists():
        claude_cfg = load_json(CLAUDE_SETTINGS)
        if claude_cfg.get("statusLine", {}).get("command") == CLAUDE_CMD:
            prev = state.get("claude_previous_statusLine")
            if prev is None:
                claude_cfg.pop("statusLine", None)
            else:
                claude_cfg["statusLine"] = prev
            save_json_with_backup(CLAUDE_SETTINGS, claude_cfg)
            print("[OK] Restored Claude Code statusLine")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage AI Quota Monitor statusLine hooks.")
    parser.add_argument("--uninstall", action="store_true", help="Restore previous statusLine settings.")
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
