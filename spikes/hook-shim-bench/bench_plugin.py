"""Time the plugin's real hook commands, as Claude Code runs them, per event.

Usage: python3 bench_plugin.py [--runs 200] [--json results.json]

The same method as bench.py (ADR-0004), pointed at the shipped shim instead of
the spike's: each run takes the command for one event from hooks/hooks.json,
runs it through `/bin/sh -c` with `HOME` at a throwaway folder laid out as
`setup` leaves it and `CLAUDE_PLUGIN_ROOT` at this checkout, feeds it the
event's fixture and times the process from spawn to exit. Cases run round-robin.

- **Daemon running:** a stand-in daemon in this process answers `{"ok": true}`.
  `Stop` is the common event; `SessionStart` adds the host-app lookup.
- **Daemon not running:** the shim starts the daemon and retries until it
  answers, so the time includes the daemon's start. Once with the stand-in
  daemon (tests/fake_daemon.py, a bare interpreter) and once with the real one
  (`python -I -m cc_notify_tts.daemon` from this checkout's .venv, without a
  speech model). Each run's daemon is stopped before the next.

Nothing outside the throwaway folder is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socketserver
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from bench import machine, percentile

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "hooks"
FAKE_DAEMON = REPO / "tests" / "fake_daemon.py"
HOOKS = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
WARMUP_RUNS = 5


class AnswerOk(socketserver.StreamRequestHandler):
    received = 0

    def handle(self) -> None:
        self.rfile.readline()
        AnswerOk.received += 1
        self.wfile.write(b'{"ok": true}\n')


def make_home(daemon: str) -> Path:
    """A throwaway HOME as `setup` leaves it. `daemon` is "fake" or "real"."""
    home = Path(tempfile.mkdtemp(prefix="cnt-bench-", dir="/tmp"))
    data = home / "Library" / "Application Support" / "cc-notify-tts"
    (data / "bin").mkdir(parents=True)
    uv_python = subprocess.run(
        ["uv", "python", "find", "--managed-python", "--system", "3.13"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (data / "bin" / "python").symlink_to(uv_python)
    if daemon == "real":
        (data / "venv").symlink_to(REPO / ".venv")
    else:
        script = data / "venv" / "bin" / "python"
        script.parent.mkdir(parents=True)
        sock, record = data / "daemon.sock", home / "record.jsonl"
        script.write_text(f'#!/bin/sh\nexec "{uv_python}" -I "{FAKE_DAEMON}" "{sock}" "{record}"\n')
        script.chmod(0o755)
    return home


def socket_of(home: Path) -> Path:
    return home / "Library" / "Application Support" / "cc-notify-tts" / "daemon.sock"


def run_hook(event: str, home: Path) -> float:
    command = HOOKS[event][0]["hooks"][0]["command"]
    payload = (FIXTURES / f"{fixture_name(event)}.json").read_bytes()
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_ROOT": str(REPO)}
    start = time.perf_counter_ns()
    done = subprocess.run(
        ["/bin/sh", "-c", command], input=payload, env=env, capture_output=True, check=False
    )
    elapsed = (time.perf_counter_ns() - start) / 1e6
    if done.returncode or done.stdout or done.stderr:
        raise SystemExit(f"hook misbehaved: {done}")
    return elapsed


def fixture_name(event: str) -> str:
    return {"Stop": "stop", "SessionStart": "session_start"}[event]


def stop_daemon(home: Path) -> None:
    """Stop whatever daemon the last run started, and wait for its socket to go."""
    sock = socket_of(home)
    out = subprocess.run(["lsof", "-t", str(sock)], capture_output=True, text=True).stdout
    for pid in {int(p) for p in out.split()}:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while sock.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    sock.unlink(missing_ok=True)
    # The real daemon releases its lock after removing the socket.
    time.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--cold-runs", type=int, default=50)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    running_home = make_home("fake")
    server = socketserver.ThreadingUnixStreamServer(str(socket_of(running_home)), AnswerOk)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake_home, real_home = make_home("fake"), make_home("real")

    samples: dict[str, list[float]] = {}

    def timed(name: str, event: str, home: Path, cold: bool) -> None:
        elapsed = run_hook(event, home)
        if cold:
            stop_daemon(home)
        samples.setdefault(name, []).append(elapsed)

    warm_cases = [
        ("Daemon running: Stop", "Stop"),
        ("Daemon running: SessionStart (host lookup)", "SessionStart"),
    ]
    cold_cases = [
        ("Daemon not running: Stop, stand-in daemon", "Stop", fake_home),
        ("Daemon not running: Stop, real daemon", "Stop", real_home),
    ]
    try:
        for _, event in warm_cases:
            for _ in range(WARMUP_RUNS):
                run_hook(event, running_home)
        for _, event, home in cold_cases:
            for _ in range(WARMUP_RUNS):
                run_hook(event, home)
                stop_daemon(home)
        for i in range(args.runs):
            for name, event in warm_cases:
                timed(name, event, running_home, cold=False)
            if i < args.cold_runs:
                for name, event, home in cold_cases:
                    timed(name, event, home, cold=True)
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{args.runs}", file=sys.stderr)
    finally:
        server.shutdown()
        server.server_close()
        for home in (running_home, fake_home, real_home):
            stop_daemon(home)
            shutil.rmtree(home, ignore_errors=True)

    expected = (WARMUP_RUNS + args.runs) * len(warm_cases)
    if AnswerOk.received != expected:
        raise SystemExit(f"daemon got {AnswerOk.received} messages, expected {expected}")

    print(f"Machine: {machine()}; wall time per hook process, ms\n")
    print("| Case | Runs | p50 | p95 | max |")
    print("| - | - | - | - | - |")
    for name, s in samples.items():
        p50, p95 = statistics.median(s), percentile(s, 95)
        print(f"| {name} | {len(s)} | {p50:.1f} | {p95:.1f} | {max(s):.1f} |")
    if args.json:
        args.json.write_text(json.dumps({"machine": machine(), "samples": samples}))


if __name__ == "__main__":
    main()
