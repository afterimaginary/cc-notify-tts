"""Spike hook shim: the work a real shim does per Claude Code event, and nothing more.

Reads the hook payload from stdin, writes a small per-session state file, then
sends one line of JSON to the daemon's Unix socket and waits for its one-line
answer. If nothing answers, it starts the daemon as a detached process and
returns without waiting for it.

Standard library only, compatible back to the system Python 3.9, silent on
stdout, always exits 0. Paths come from the environment so the benchmark can
point it at a scratch directory:

- CC_SHIM_STATE_DIR: where the state file goes
- CC_SHIM_SOCKET: the daemon's socket path
- CC_SHIM_DAEMON: the command to start when the socket doesn't answer, split
  on whitespace (the benchmark passes a stub that exits at once)
"""

from __future__ import annotations

import json
import os
import socket
import sys


def write_state(state_dir: str, payload: dict[str, object]) -> None:
    session = str(payload.get("session_id") or "unknown")
    record = {"event": payload.get("hook_event_name"), "cwd": payload.get("cwd")}
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, session + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(record, fh)
    os.replace(tmp, path)


def send(socket_path: str, message: bytes) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            sock.connect(socket_path)
            sock.sendall(message)
            sock.shutdown(socket.SHUT_WR)
            return sock.recv(64).startswith(b"ok")
    except OSError:
        return False


def start_daemon(command: str) -> None:
    import subprocess  # only paid for when the daemon isn't running

    subprocess.Popen(
        command.split(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main() -> None:
    raw = sys.stdin.buffer.read()
    payload = json.loads(raw)
    env = os.environ
    write_state(env["CC_SHIM_STATE_DIR"], payload)
    if not send(env["CC_SHIM_SOCKET"], raw.rstrip(b"\n") + b"\n"):
        start_daemon(env["CC_SHIM_DAEMON"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # a hook must never disturb the session
        sys.stderr.write(f"shim: {exc!r}\n")
    sys.exit(0)
