"""Measure how long Claude Code waits on a hook, with and without `async: true`.

Usage: python3 claude_wait.py [--runs 50] [--python PATH]

Starts `claude -p` in a scratch directory with only this script's hooks loaded
(user and project settings off, no MCP servers, no session saved), streams it
one prompt that a hook blocks, so no model request is ever made, then closes
its input so the session ends. The hook under test runs on the two events that
fire without a model: SessionStart and SessionEnd.

- SessionStart: an async marker logs when Claude Code started the event's hooks
  and another logs when it got to the prompt's UserPromptSubmit. The gap is how
  long startup was held.
- SessionEnd: the time from closing Claude Code's input to the process exiting.

Each variant's figure minus the no-hook floor is the wait the hook adds.
Variants: the spike shim (sync and async) and a 2 s sleep followed by the shim (sync
and async), which shows the method sees a wait. This script serves the shim's
daemon socket and counts the messages that got through: an async hook that
Claude Code kills on exit never arrives. The sleep is longer than Claude Code
takes to exit, so an async SessionEnd message that arrives after the process
has exited shows the hook outlived it.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socketserver
import statistics
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SHIM = HERE / "shim.py"
SLEEP_MS = 2000

MARKER = """\
import sys, time
t = time.time_ns()
sys.stdin.read()
with open(sys.argv[2], "a") as fh:
    fh.write(sys.argv[1] + " " + str(t) + "\\n")
"""
BLOCK = """cat >/dev/null; printf '{"decision":"block","reason":"benchmark"}'"""


class AnswerOk(socketserver.StreamRequestHandler):
    received = 0
    last_at = 0.0  # perf_counter() when the latest message arrived

    def handle(self) -> None:
        self.rfile.readline()
        AnswerOk.received += 1
        AnswerOk.last_at = time.perf_counter()
        self.wfile.write(b"ok\n")


def settings(python: str, marker: Path, log: Path, hook: str | None, is_async: bool) -> str:
    q = shlex.quote

    def mark(name: str) -> dict[str, object]:
        command = f"{q(python)} -I -S {q(str(marker))} {name} {q(str(log))}"
        return {"type": "command", "command": command, "async": True}

    start = [mark("start")]
    end: list[dict[str, object]] = []
    if hook is not None:
        under_test = {"type": "command", "command": hook, "async": is_async, "timeout": 10}
        start.append(under_test)
        end.append(under_test)
    hooks: dict[str, object] = {
        "SessionStart": [{"hooks": start}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": BLOCK}, mark("prompt")]}],
    }
    if end:
        hooks["SessionEnd"] = [{"hooks": end}]
    return json.dumps({"hooks": hooks})


def wait_for(condition: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False


class Run:
    def __init__(self, start_held: float, exit_after: float, at_start: int, at_end: int,
                 end_after_exit: int) -> None:  # fmt: skip
        self.start_held = start_held
        self.exit_after = exit_after
        self.at_start = at_start  # shim messages that arrived from SessionStart
        self.at_end = at_end  # and from SessionEnd
        self.end_after_exit = end_after_exit  # SessionEnd messages that arrived after exit


def one_run(claude: str, cwd: Path, env: dict[str, str], settings_json: str, log: Path,
            has_hook: bool) -> Run:  # fmt: skip
    log.unlink(missing_ok=True)
    before = AnswerOk.received
    proc = subprocess.Popen(
        [
            claude, "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--setting-sources", "project", "--settings", settings_json,
            "--strict-mcp-config", "--no-session-persistence",
        ],
        cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )  # fmt: skip
    assert proc.stdin is not None and proc.stderr is not None
    line = {"type": "user", "message": {"role": "user", "content": "benchmark"}}
    proc.stdin.write((json.dumps(line) + "\n").encode())
    proc.stdin.flush()

    def markers() -> dict[str, int]:
        if not log.exists():
            return {}
        return {k: int(v) for k, v in (x.split() for x in log.read_text().splitlines())}

    ok = wait_for(lambda: len(markers()) == 2, 15)
    # Let the SessionStart shim (async included) land before the session ends.
    wait_for(lambda: AnswerOk.received - before >= int(has_hook), SLEEP_MS / 1000 + 2)
    at_start = AnswerOk.received - before
    closed = time.perf_counter()
    proc.stdin.close()
    proc.wait(timeout=30)
    exited = time.perf_counter()
    exit_after = (exited - closed) * 1000
    err = proc.stderr.read()
    # An async SessionEnd hook that survived the exit lands now.
    wait_for(lambda: AnswerOk.received - before - at_start >= int(has_hook), SLEEP_MS / 1000 + 2)
    at_end = AnswerOk.received - before - at_start
    end_after_exit = int(at_end > 0 and AnswerOk.last_at > exited)
    if not ok:
        raise SystemExit(f"markers missing: {markers()}; {err.decode()[-500:]}")
    m = markers()
    return Run((m["prompt"] - m["start"]) / 1e6, exit_after, at_start, at_end, end_after_exit)


def p50_p95(samples: list[float]) -> tuple[float, float]:
    return statistics.median(samples), statistics.quantiles(samples, n=100, method="inclusive")[94]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument(
        "--python",
        default=str((REPO / ".venv" / "bin" / "python").resolve()),
        help="interpreter for the shim and markers (default: the uv-managed one behind .venv)",
    )
    args = parser.parse_args()
    claude = shutil.which("claude")
    if claude is None:
        raise SystemExit("claude not found on PATH")

    scratch = Path(tempfile.mkdtemp(prefix="claudewait-"))
    marker = scratch / "marker.py"
    marker.write_text(MARKER)
    log = scratch / "markers.log"
    sock = scratch / "d.sock"
    server = socketserver.ThreadingUnixStreamServer(str(sock), AnswerOk)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    q = shlex.quote
    shim = f"{q(args.python)} -I -S {q(str(SHIM))}"
    # The sleep comes first, so a delivered message proves the hook outlived the wait.
    slow = f"sleep {SLEEP_MS / 1000}; {shim}"
    variants: list[tuple[str, str | None, bool]] = [
        ("no hook (floor)", None, False),
        ("shim, sync", shim, False),
        ("shim, async", shim, True),
        (f"{SLEEP_MS / 1000:g} s sleep, then shim, sync", slow, False),
        (f"{SLEEP_MS / 1000:g} s sleep, then shim, async", slow, True),
    ]
    env = {
        **os.environ,
        "CC_SHIM_STATE_DIR": str(scratch / "state"),
        "CC_SHIM_SOCKET": str(sock),
        "CC_SHIM_DAEMON": "/usr/bin/true",
    }
    runs: dict[str, list[Run]] = {name: [] for name, _, _ in variants}
    try:
        for i in range(args.runs):
            for name, hook, is_async in variants:
                settings_json = settings(args.python, marker, log, hook, is_async)
                runs[name].append(
                    one_run(claude, scratch, env, settings_json, log, hook is not None)
                )
            if (i + 1) % 10 == 0:
                print(f"  run {i + 1}/{args.runs}", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(scratch, ignore_errors=True)

    floor_start = statistics.median(r.start_held for r in runs["no hook (floor)"])
    floor_end = statistics.median(r.exit_after for r in runs["no hook (floor)"])
    print(f"\n{args.runs} runs per variant; ms\n")
    print(
        "| Hook under test | SessionStart held p50 / p95 | Added | Exit after SessionEnd "
        "p50 / p95 | Added | Shim delivered at start / end | End delivered after exit |"
    )
    print("| - | - | - | - | - | - | - |")
    for name, hook, _ in variants:
        s50, s95 = p50_p95([r.start_held for r in runs[name]])
        e50, e95 = p50_p95([r.exit_after for r in runs[name]])
        delivered = after_exit = "n/a"
        if hook is not None:
            starts = sum(r.at_start for r in runs[name])
            ends = sum(r.at_end for r in runs[name])
            delivered = f"{starts}/{args.runs} / {ends}/{args.runs}"
            after_exit = f"{sum(r.end_after_exit for r in runs[name])}/{args.runs}"
        print(
            f"| {name} | {s50:.0f} / {s95:.0f} | {s50 - floor_start:+.0f} "
            f"| {e50:.0f} / {e95:.0f} | {e50 - floor_end:+.0f} | {delivered} | {after_exit} |"
        )


if __name__ == "__main__":
    main()
