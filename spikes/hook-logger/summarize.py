#!/usr/bin/env python3
"""Summarize a hook-logger log: which events fired, sync vs async, field names, host env.

Usage: python3 summarize.py [path-to-hooks.jsonl]

Prints field names and environment variable names, plus the values of the
host-identifying variables only. Paste the output, not the log: the log holds
real prompts and paths.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT = Path.home() / "Library" / "Logs" / "cc-notify-tts-spike" / "hooks.jsonl"
COMMON = {"session_id", "transcript_path", "cwd", "hook_event_name", "scratchpad_dir"}
PRIVATE_VALUES = {"STY", "SSH_CONNECTION", "TMUX"}


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    modes: dict[str, set[str]] = defaultdict(set)
    fields: dict[str, set[str]] = defaultdict(set)
    notes: dict[str, set[str]] = defaultdict(set)
    host: dict[str, set[str]] = defaultdict(set)
    for line in path.read_text().splitlines():
        record = json.loads(line)
        event = str(record.get("event"))
        payload = record.get("payload") or {}
        modes[event].add(record.get("mode", "?"))
        fields[event] |= set(payload) - COMMON
        for key in ("tool_name", "notification_type", "error", "reason", "source"):
            if key in payload:
                first_line = str(payload[key]).splitlines()[0] if payload[key] else ""
                notes[event].add(f"{key}={first_line}")
        for key, value in (record.get("host_env") or {}).items():
            # These hold a hostname or IP addresses: report presence only.
            host[key].add("(set)" if key in PRIVATE_VALUES else str(value))
        entrypoint = (record.get("claude_env") or {}).get("CLAUDE_CODE_ENTRYPOINT")
        if entrypoint:
            host["CLAUDE_CODE_ENTRYPOINT"].add(entrypoint)
        if "CLAUDE_CODE_DESKTOP_APP_VERSION" in (record.get("claude_env") or {}):
            host["CLAUDE_CODE_DESKTOP_APP_VERSION"].add("(set)")

    print("| Event | sync | async | Fields | Seen |")
    print("| - | - | - | - | - |")
    for event in sorted(modes):
        print(
            f"| {event} | {'yes' if 'sync' in modes[event] else 'no'} "
            f"| {'yes' if 'async' in modes[event] else 'no'} "
            f"| {', '.join(sorted(fields[event]))} | {'; '.join(sorted(notes[event]))[:200]} |"
        )
    print("\nHost environment:")
    for key in sorted(host):
        print(f"- {key}: {', '.join(sorted(host[key]))}")


if __name__ == "__main__":
    main()
