"""The hook shim: hands one Claude Code hook event to the daemon, starting it if needed.

`hooks/shim.py` runs this for every hook the plugin registers, with a
uv-managed Python and `-I -S` (ADR-0004, ADR-0011, ADR-0014). It must never
disturb Claude Code, so it:

- imports only the standard library and the package's other stdlib-only
  modules (`client`, `host_app`; a test enforces this);
- never writes to stdout or stderr, and always exits 0;
- logs anything that went wrong to `~/Library/Logs/cc-notify-tts/shim.log`,
  and nothing when all went well.

What it sends is the protocol's `event` request (docs/specs/daemon-protocol.md):
the payload unchanged, `fired_at` read first thing, `sent_at` just before
sending (both for the latency log, ADR-0019), and `host_app` at
`SessionStart` and, inside tmux, on every hook that can announce something:
`null` when it can't be named, such as a detached tmux session (ADR-0030).
"""

import json
import os
import time
from collections.abc import Mapping
from typing import Any

from cc_notify_tts import client

# Hooks that can end in a sentence. Inside tmux their host app is named again,
# because clients attach, detach and switch sessions mid-session (ADR-0006).
ANNOUNCING = frozenset({"Stop", "StopFailure", "PermissionRequest", "PermissionDenied"})
LOG_NAME = "shim.log"
LOG_MAX_BYTES = 1_000_000


def main(argv: list[str], raw: bytes, env: Mapping[str, str], fired_at: float) -> None:
    """Handle one hook. `argv` is the event name hooks.json passes, `raw` the hook's stdin."""
    hook = argv[0] if argv else "?"
    try:
        handle(hook, raw, env, fired_at)
    except Exception as e:  # a hook must never disturb the session
        log(f"{hook}: unexpected {type(e).__name__}: {e}")


def handle(hook: str, raw: bytes, env: Mapping[str, str], fired_at: float) -> None:
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        log(f"{hook}: the hook's input isn't JSON ({len(raw)} bytes); nothing sent")
        return
    if not isinstance(payload, dict):
        log(f"{hook}: the hook's input isn't a JSON object; nothing sent")
        return
    fields: dict[str, Any] = payload  # pyright: ignore[reportUnknownVariableType]
    event = fields.get("hook_event_name")
    if not isinstance(event, str):
        event = hook
    message: dict[str, Any] = {"type": "event", "fired_at": fired_at, "payload": fields}
    if event == "SessionStart" or (env.get("TMUX") and event in ANNOUNCING):
        from cc_notify_tts import host_app  # ctypes and plistlib: only when needed

        # None goes out as null: the host is unknown now, so the one on record is stale.
        message["host_app"] = host_app.identify(env, os.getpid())

    python = client.daemon_python()
    message["sent_at"] = time.time()
    try:
        reply, started = client.deliver(message, client.socket_path(), python)
    except OSError as e:
        log(f"{event}: couldn't start the daemon with {python}: {e}. Run `cc-notify-tts setup`.")
        return
    if started:
        log(f"{event}: nothing answered on the socket, so started the daemon")
    if reply is None:
        log(f"{event}: the daemon didn't answer within {client.START_DEADLINE:.0f} s; event lost")
    elif reply.get("ok") is not True:
        log(f"{event}: the daemon refused the event: {reply.get('error')}")


def log(line: str) -> None:
    """Append one line to the shim's log. Fails silently: a full disk mustn't reach Claude Code."""
    try:
        folder = client.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / LOG_NAME
        try:
            if path.stat().st_size > LOG_MAX_BYTES:
                os.replace(path, path.with_name(LOG_NAME + ".1"))
        except FileNotFoundError:
            pass
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} [{os.getpid()}] {line}\n")
    except OSError:
        pass
