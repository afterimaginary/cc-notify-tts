"""How the hook shim and the CLI reach the daemon: its socket, one request, and starting it.

Standard library only, and it imports no other module of the package: the hook
shim runs it with `python -I -S` from the plugin folder (ADR-0004, ADR-0014),
where nothing but the standard library is importable. `subprocess` is imported
only when the daemon has to start, which saves about 2 ms on every other event.

The protocol itself is in docs/specs/daemon-protocol.md.
"""

import json
import socket
import time
from pathlib import Path
from typing import Any

# Largest request the daemon accepts, and the longest reply line read back.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
# A daemon that doesn't answer within this long may be replaced (the protocol's ping rule).
ANSWER_TIMEOUT = 0.2
# After starting the daemon, how often to try the socket again. At most 50 ms keeps
# the events of several hooks inside the daemon's 100 ms ordering window.
RETRY_INTERVAL = 0.025
# How long to keep trying after starting the daemon. It answers about 50 ms after
# it starts; the first start after an install also compiles the package.
START_DEADLINE = 5.0


def data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "cc-notify-tts"


def socket_path() -> Path:
    return data_dir() / "daemon.sock"


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "cc-notify-tts"


def daemon_python() -> Path:
    """The interpreter of the tool's environment, linked by `setup` (ADR-0011)."""
    return data_dir() / "venv" / "bin" / "python"


def request(path: Path, message: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    """Send one request to the daemon and return its reply. Raises OSError or ValueError."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall(json.dumps(message).encode() + b"\n")
        with sock.makefile("rb") as f:
            line = f.readline(MAX_MESSAGE_BYTES)
    reply = json.loads(line)
    if not isinstance(reply, dict):
        raise ValueError(f"reply isn't a JSON object: {line!r}")
    return reply  # pyright: ignore[reportUnknownVariableType]


def is_answering(path: Path, timeout: float = ANSWER_TIMEOUT) -> bool:
    try:
        return request(path, {"type": "ping"}, timeout).get("ok") is True
    except (OSError, ValueError):
        return False


def start_daemon(python: Path) -> None:
    """Start `python -I -m cc_notify_tts.daemon` detached, and return without waiting.

    A new session and stdio away from the caller's pipes let it outlive the hook,
    the Claude Code session and its window (ADR-0002). A second daemon started by
    a race exits on its own (single instance, ADR-0013). Raises OSError when
    `python` can't be run.
    """
    import subprocess

    subprocess.Popen(
        [str(python), "-I", "-m", "cc_notify_tts.daemon"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def deliver(
    message: dict[str, Any],
    path: Path,
    python: Path,
    deadline: float = START_DEADLINE,
) -> tuple[dict[str, Any] | None, bool]:
    """Send `message`, starting the daemon first if nothing answers.

    Returns (reply, started): the daemon's reply, or None if it never answered,
    and whether this call started a daemon. Raises OSError when the daemon had
    to start and `python` can't be run.
    """
    reply = _send(message, path)
    if reply is not None:
        return reply, False
    start_daemon(python)
    give_up = time.monotonic() + deadline
    while time.monotonic() < give_up:
        time.sleep(RETRY_INTERVAL)
        reply = _send(message, path)
        if reply is not None:
            return reply, True
    return None, True


def _send(message: dict[str, Any], path: Path) -> dict[str, Any] | None:
    """The daemon's reply, or None when nothing answered."""
    try:
        return request(path, message, ANSWER_TIMEOUT)
    except (OSError, ValueError):
        return None
