#!/usr/bin/env python3
"""Spike hook handler: append one JSON line per hook invocation.

Usage (from hooks.json): log_hook.py <sync|async>

Writes to $CC_HOOK_SPIKE_LOG, or ~/Library/Logs/cc-notify-tts-spike/hooks.jsonl.
The log holds real payloads (paths, prompts, transcripts paths), so it lives
outside the repository and is never committed.

Follows the rules the real shim must follow: standard library only, nothing on
stdout, always exit 0.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

# Environment variables that might identify the app hosting the session.
HOST_VARS = (
    "__CFBundleIdentifier",
    "TERM_PROGRAM",
    "TERM_PROGRAM_VERSION",
    "TERM",
    "LC_TERMINAL",
    "TERMINAL_EMULATOR",
    "TMUX",
    "STY",
    "VSCODE_PID",
    "VSCODE_GIT_IPC_HANDLE",
    "CURSOR_TRACE_ID",
    "ITERM_SESSION_ID",
    "KITTY_WINDOW_ID",
    "WEZTERM_PANE",
    "GHOSTTY_RESOURCES_DIR",
    "ALACRITTY_WINDOW_ID",
    "WARP_IS_LOCAL_SHELL_SESSION",
    "XPC_SERVICE_NAME",
    "SSH_CONNECTION",
)


def _log_path() -> Path:
    override = os.environ.get("CC_HOOK_SPIKE_LOG")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Logs" / "cc-notify-tts-spike" / "hooks.jsonl"


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    raw = sys.stdin.read()
    try:
        payload: object = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"_unparsed": raw[:2000]}
    env = os.environ
    record = {
        "ts": time.time(),
        "mode": mode,
        "event": payload.get("hook_event_name") if isinstance(payload, dict) else None,
        "payload": payload,
        "host_env": {k: env[k] for k in HOST_VARS if k in env},
        "claude_env": {k: v for k, v in env.items() if k.startswith(("CLAUDE", "CC_"))},
        "env_names": sorted(env),
        "pid": os.getpid(),
        "ppid": os.getppid(),
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    with contextlib.suppress(Exception):  # a spike logger must never disturb the session
        main()
    sys.exit(0)
