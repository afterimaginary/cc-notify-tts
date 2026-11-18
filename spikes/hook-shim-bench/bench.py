"""Time the spike shim under each way of launching it, with the daemon socket
answering and with it absent.

Usage: python3 bench.py [--runs 200] [--json results.json]

Each run starts the shim the way Claude Code starts a hook command (through
`/bin/sh -c`), feeds it a fixture payload on stdin and times the whole process
from spawn to exit. Launch options are run round-robin so drift on the machine
spreads evenly across them. Options whose interpreter isn't installed are
skipped and listed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import socketserver
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SHIM = HERE / "shim.py"
FIXTURE = REPO / "tests" / "fixtures" / "hooks" / "stop.json"
WARMUP_RUNS = 5


@dataclass
class Option:
    name: str
    command: str  # run with `sh -c`; the shim path is already in it


def launch_options() -> tuple[list[Option], list[str]]:
    shim = shlex.quote(str(SHIM))
    venv_python = REPO / ".venv" / "bin" / "python"
    # The project venv's python is a symlink to the uv-managed interpreter.
    uv_python = venv_python.resolve() if venv_python.exists() else None
    candidates: list[tuple[str, Path | None, str]] = [
        ("baseline: sh -c true (no Python)", None, "true"),
        ("system /usr/bin/python3", Path("/usr/bin/python3"), "{py} {shim}"),
        ("system /usr/bin/python3 -I -S", Path("/usr/bin/python3"), "{py} -I -S {shim}"),
        ("uv-managed 3.13", uv_python, "{py} {shim}"),
        ("uv-managed 3.13 -I -S", uv_python, "{py} -I -S {shim}"),
        ("project venv python", venv_python, "{py} {shim}"),
        ("project venv python -I -S", venv_python, "{py} -I -S {shim}"),
        ("Homebrew python3 -I -S", Path("/opt/homebrew/bin/python3"), "{py} -I -S {shim}"),
        ("uv run --frozen python", Path(shutil.which("uv") or "/nonexistent/uv"), ""),
    ]
    options: list[Option] = []
    skipped: list[str] = []
    for name, py, template in candidates:
        if py is not None and not py.exists():
            skipped.append(name)
            continue
        if name.startswith("uv run"):
            project = shlex.quote(str(REPO))
            command = f"{shlex.quote(str(py))} run --frozen --project {project} python {shim}"
        else:
            command = template.format(py=shlex.quote(str(py)), shim=shim)
        options.append(Option(name, command))
    return options, skipped


class AnswerOk(socketserver.StreamRequestHandler):
    """Stand-in daemon: read one message, answer `ok`."""

    received = 0

    def handle(self) -> None:
        self.rfile.readline()
        AnswerOk.received += 1
        self.wfile.write(b"ok\n")


def run_once(command: str, payload: bytes, env: dict[str, str]) -> float:
    start = time.perf_counter_ns()
    result = subprocess.run(
        ["/bin/sh", "-c", command], input=payload, env=env, capture_output=True, check=False
    )
    elapsed_ms = (time.perf_counter_ns() - start) / 1e6
    if result.returncode != 0 or result.stdout or result.stderr:
        raise SystemExit(
            f"shim misbehaved ({command}): rc={result.returncode} "
            f"stdout={result.stdout[:200]!r} stderr={result.stderr[:200]!r}"
        )
    return elapsed_ms


def percentile(samples: list[float], pct: int) -> float:
    return statistics.quantiles(samples, n=100, method="inclusive")[pct - 1]


def machine() -> str:
    chip = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return f"{chip or platform.machine()}, macOS {platform.mac_ver()[0]}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--json", type=Path, help="also write raw samples here")
    args = parser.parse_args()

    options, skipped = launch_options()
    payload = FIXTURE.read_bytes()
    scratch = Path(tempfile.mkdtemp(prefix="shimbench-"))
    sock_path = scratch / "d.sock"
    base_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
        "CC_SHIM_STATE_DIR": str(scratch / "state"),
        "CC_SHIM_DAEMON": "/usr/bin/true",
    }
    answering_env = {**base_env, "CC_SHIM_SOCKET": str(sock_path)}
    absent_env = {**base_env, "CC_SHIM_SOCKET": str(scratch / "absent.sock")}

    server = socketserver.ThreadingUnixStreamServer(str(sock_path), AnswerOk)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    scenarios = {"socket answering": answering_env, "socket absent": absent_env}
    samples: dict[str, dict[str, list[float]]] = {
        s: {o.name: [] for o in options} for s in scenarios
    }
    try:
        for scenario, env in scenarios.items():
            for option in options:
                for _ in range(WARMUP_RUNS):
                    run_once(option.command, payload, env)
            for i in range(args.runs):
                for option in options:
                    samples[scenario][option.name].append(run_once(option.command, payload, env))
                if (i + 1) % 50 == 0:
                    print(f"  {scenario}: {i + 1}/{args.runs}", file=sys.stderr)
    finally:
        server.shutdown()
        server.server_close()

    shim_runs = (WARMUP_RUNS + args.runs) * sum(1 for o in options if o.command != "true")
    if AnswerOk.received != shim_runs:
        raise SystemExit(f"daemon got {AnswerOk.received} messages, expected {shim_runs}")
    if not any((scratch / "state").iterdir()):
        raise SystemExit("shim wrote no state file")
    shutil.rmtree(scratch, ignore_errors=True)

    print(f"Machine: {machine()}; {args.runs} runs per cell; wall time per hook process, ms\n")
    print("| Launch | Socket answering p50 | p95 | Socket absent p50 | p95 |")
    print("| - | - | - | - | - |")
    for option in options:
        cells: list[str] = []
        for scenario in scenarios:
            s = samples[scenario][option.name]
            cells += [f"{statistics.median(s):.1f}", f"{percentile(s, 95):.1f}"]
        print(f"| {option.name} | " + " | ".join(cells) + " |")
    if skipped:
        print(f"\nSkipped (not installed): {', '.join(skipped)}")
    if args.json:
        commands = {o.name: o.command for o in options}
        args.json.write_text(
            json.dumps({"machine": machine(), "commands": commands, "samples": samples})
        )


if __name__ == "__main__":
    main()
