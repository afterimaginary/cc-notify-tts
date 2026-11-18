"""Measure the latency budget from hook to first audio, stage by stage.

    uv run python -m bench.latency summarize [daemon.log ...] [--since UNIX_SECONDS]
    uv run python -m bench.latency run [--model granite4:micro] [--rounds 30]
    uv run python -m bench.latency afplay [--runs 20]

`summarize` reads the daemon's `timing` lines (ADR-0019) and prints p50, p95
and max per stage. With no file it reads the daemon's own log and its rotated
copies, so it works on everyday use as well as on a run.

`run` fires a scripted session through the plugin's real hook commands, as
Claude Code runs them, against the real daemon with real Kokoro speech: sound
plays. It uses a throwaway `HOME` laid out as `setup` leaves it, with this
checkout's `.venv` as the daemon's environment and the speech model files linked
from your own data folder, and a config that speaks every event. Each round is a
prompt, a permission request and a finished turn, spaced past the 5 s duplicate
window. It times every hook process from spawn to exit, records the daemon's
resident memory while idle, then summarizes the run's own log. With `--model`
the daemon words with that Ollama model, which must be pulled; it is unloaded
when the daemon exits. Without it, wording is templates only.

`afplay` times `afplay -d` on a 50 ms silent WAV under a pseudo-terminal, so its
debug lines arrive as they're printed: the last one comes once its audio queue
is set up, just before playback starts, which bounds how long after the
`playing` mark sound starts. Its exit time shows how long it lingers after a
short sound ends.

`run` needs the speech model (`cc-notify-tts setup`) and an audio device, so it's not
part of `make ready` or `make verify`.
"""

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any

from cc_notify_tts import client
from cc_notify_tts.models import default_models_dir
from cc_notify_tts.ollama import Ollama, OllamaError
from cc_notify_tts.timeline import Span, parse, percentile, summarize

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "hooks"
HOOKS: dict[str, Any] = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
EVENTS = (
    "finished",
    "still_running",
    "background_done",
    "permission",
    "question",
    "denied",
    "failed",
)
# Past the daemon's 5 s duplicate window, so every round's events are spoken.
ROUND_SECONDS = 6.0
# A turn's prompt comes this long before its permission request.
THINK_SECONDS = 0.5


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure the latency budget from hook to first audio."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("summarize", help="p50 and p95 per stage from daemon logs")
    s.add_argument("logs", nargs="*", type=Path)
    s.add_argument("--since", type=float, help="only events whose hook fired at or after this")
    r = sub.add_parser("run", help="fire a scripted session and summarize it (plays sound)")
    r.add_argument("--model", help="Ollama model for wording; templates only without it")
    r.add_argument("--rounds", type=int, default=30)
    r.add_argument("--idle-seconds", type=float, default=15.0, help="wait before reading memory")
    r.add_argument("--json", type=Path, help="also write the raw results here")
    a = sub.add_parser("afplay", help="time afplay's start-up and exit on a short sound")
    a.add_argument("--runs", type=int, default=20)
    args = parser.parse_args(argv)
    if args.command == "afplay":
        return afplay(args.runs)
    if args.command == "summarize":
        logs: list[Path] = args.logs or default_logs()
        records = parse(read_lines(logs))
        if args.since is not None:
            records = [r for r in records if first_mark(r) >= args.since]
        print(stage_table(summarize(records)))
        return 0
    return run(args.model, args.rounds, args.idle_seconds, args.json)


def first_mark(record: dict[str, Any]) -> float:
    marks: dict[str, Any] = record["marks"]
    return min((v for v in marks.values() if isinstance(v, int | float)), default=0.0)


def default_logs() -> list[Path]:
    log = client.log_dir() / "daemon.log"
    rotated = [log.with_name(f"daemon.log.{n}") for n in (2, 1)]
    return [p for p in [*rotated, log] if p.exists()]


def read_lines(paths: Iterable[Path]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def stage_table(spans: list[Span]) -> str:
    rows = ["| Stage | Events | p50 ms | p95 ms | max ms |", "| - | - | - | - | - |"]
    for s in spans:
        rows.append(f"| {s.name} | {s.count} | {s.p50_ms:.1f} | {s.p95_ms:.1f} | {s.max_ms:.1f} |")
    return "\n".join(rows)


# --- run ----------------------------------------------------------------------------


class Session:
    """A throwaway HOME as `setup` leaves it, and the hooks of one Claude Code session."""

    def __init__(self, model: str | None) -> None:
        # A Unix socket path must fit in 104 bytes.
        self.home = Path(tempfile.mkdtemp(prefix="cnt-latency-", dir="/tmp"))
        self.data = self.home / "Library" / "Application Support" / "cc-notify-tts"
        (self.data / "bin").mkdir(parents=True)
        uv_python = subprocess.run(
            ["uv", "python", "find", "--managed-python", "--system", "3.13"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (self.data / "bin" / "python").symlink_to(uv_python)
        (self.data / "venv").symlink_to(REPO / ".venv")
        (self.data / "models").symlink_to(default_models_dir())
        config = [f'[events.{e}]\nspeak = "always"' for e in EVENTS]
        config.append(f"[wording]\nollama = {'true' if model else 'false'}")
        if model:
            config.append(f'model = "{model}"')
        (self.data / "config.toml").write_text("\n".join(config) + "\n")
        self.socket = self.data / "daemon.sock"
        self.log = self.home / "Library" / "Logs" / "cc-notify-tts" / "daemon.log"
        self.session_id = str(uuid.uuid4())
        self.hook_ms: dict[str, list[float]] = {}

    def hook(self, fixture: str, **overrides: Any) -> float:
        """Run one hook's real command with `fixture` as its input; returns its wall time, ms."""
        payload: dict[str, Any] = json.loads((FIXTURES / f"{fixture}.json").read_text())
        payload.update(overrides, session_id=self.session_id)
        event = payload["hook_event_name"]
        command = HOOKS[event][0]["hooks"][0]["command"]
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_ROOT": str(REPO)}
        start = time.perf_counter_ns()
        done = subprocess.run(
            ["/bin/sh", "-c", command],
            input=json.dumps(payload).encode(),
            env=env,
            capture_output=True,
            check=False,
        )
        elapsed = (time.perf_counter_ns() - start) / 1e6
        if done.returncode or done.stdout or done.stderr:
            raise SystemExit(f"the {event} hook misbehaved: {done}")
        self.hook_ms.setdefault(event, []).append(elapsed)
        return elapsed

    def status(self) -> dict[str, Any]:
        return client.request(self.socket, {"type": "status"})

    def records(self) -> list[dict[str, Any]]:
        return parse(read_lines([self.log])) if self.log.exists() else []

    def close(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)


def wait_for(what: str, check: Callable[[], bool], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for {what}")
        time.sleep(0.05)


def ended(session: Session, hook: str, count: int) -> Callable[[], bool]:
    """True once `count` events from `hook` have finished their timeline."""
    return lambda: sum(r["hook"] == hook for r in session.records()) >= count


def rss_mib(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
    return int(out.stdout.strip()) / 1024


def run(model: str | None, rounds: int, idle_seconds: float, json_path: Path | None) -> int:
    if not (default_models_dir() / "voices-v1.0.bin").exists():
        print("No speech model: run `cc-notify-tts setup` first.", file=sys.stderr)
        return 1
    ollama = Ollama()
    if model and not ollama.installed(model):
        print(f"{model} isn't pulled: `ollama pull {model}`.", file=sys.stderr)
        return 1
    session = Session(model)
    pid: int | None = None
    try:
        cold_ms = session.hook("session_start")
        pid = int(session.status()["pid"])
        wait_for("the speech model", lambda: session.status()["speech"] == "ready", 60)
        if model:
            wait_for(f"{model} to load", lambda: ollama.running(model) is not None, 120)
        memory = {"after_load_mib": rss_mib(pid)}

        start = 0.0
        # One warm-up round, then the measured ones.
        for i in range(rounds + 1):
            if i == 1:
                start = time.time()
                session.hook_ms.clear()
            began = time.monotonic()
            prompt_id = str(uuid.uuid4())
            session.hook("user_prompt_submit", prompt_id=prompt_id)
            time.sleep(THINK_SECONDS)
            session.hook("permission_request_bash", prompt_id=prompt_id)
            wait_for("the permission request", ended(session, "PermissionRequest", i + 1), 30)
            session.hook("stop", prompt_id=prompt_id)
            wait_for("the finished turn", ended(session, "Stop", i + 1), 30)
            time.sleep(max(0.0, ROUND_SECONDS - (time.monotonic() - began)))
            print(f"  round {i}/{rounds}", file=sys.stderr)

        session.hook("session_end")
        time.sleep(idle_seconds)
        status = session.status()
        if status["speaking"] or status["pending"]:
            raise SystemExit("the daemon wasn't idle when its memory was read")
        memory["idle_mib"] = rss_mib(pid)
        loaded = ollama.running(model) if model else None
    finally:
        if pid is not None:
            stop_daemon(pid)
        if model and ollama_loaded(ollama, model):
            ollama.unload(model)
    records = [r for r in session.records() if first_mark(r) >= start]
    print(report(session, model, records, cold_ms, memory, loaded, ollama))
    if json_path:
        json_path.write_text(
            json.dumps(
                {
                    "machine": machine(),
                    "model": model,
                    "hook_ms": session.hook_ms,
                    "memory": memory,
                    "records": records,
                },
                indent=1,
            )
        )
    session.close()
    return 0


def afplay(runs: int) -> int:
    """Time afplay to its last setup line and to exit, on a 50 ms silent WAV."""
    import pty

    import numpy as np

    from cc_notify_tts.speech import AFPLAY, write_wav

    folder = Path(tempfile.mkdtemp(prefix="cnt-afplay-"))
    wav = folder / "short.wav"
    write_wav(wav, np.zeros(1200, dtype=np.float32), 24000)
    ready: list[float] = []
    exited: list[float] = []
    try:
        for _ in range(runs):
            start = time.perf_counter()
            pid, fd = pty.fork()
            if pid == 0:
                os.execv(AFPLAY, [AFPLAY, "-d", str(wav)])
            last = 0.0
            while True:
                try:
                    data = os.read(fd, 1024)
                except OSError:
                    break
                if not data:
                    break
                last = (time.perf_counter() - start) * 1000
            os.waitpid(pid, 0)
            os.close(fd)
            ready.append(last)
            exited.append((time.perf_counter() - start) * 1000)
            time.sleep(0.2)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print(f"Machine: {machine()}; afplay on a 50 ms WAV, {runs} runs, ms\n")
    print("| Measure | p50 | p95 | max |\n| - | - | - | - |")
    for name, samples in (("Spawn to last setup line", ready), ("Spawn to exit", exited)):
        print(
            f"| {name} | {percentile(samples, 50):.1f} | {percentile(samples, 95):.1f} | "
            f"{max(samples):.1f} |"
        )
    return 0


def stop_daemon(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)


def ollama_loaded(ollama: Ollama, model: str) -> bool:
    try:
        return ollama.running(model) is not None
    except OllamaError:
        return False


def report(
    session: Session,
    model: str | None,
    records: list[dict[str, Any]],
    cold_ms: float,
    memory: dict[str, float],
    loaded: dict[str, Any] | None,
    ollama: Ollama,
) -> str:
    template_lines = sum("template wording:" in line for line in read_lines([session.log]))
    outcomes: dict[str, int] = {}
    for r in records:
        outcomes[f"{r['hook']}: {r['outcome']}"] = (
            outcomes.get(f"{r['hook']}: {r['outcome']}", 0) + 1
        )
    lines = [
        f"Machine: {machine()}",
        f"Wording: {model or 'templates only'}"
        + (f" (Ollama {ollama.version()})" if model else ""),
        "",
        "Hook process, spawn to exit, ms, measured rounds and the closing SessionEnd:",
        "",
        "| Hook | Runs | p50 | p95 | max |",
        "| - | - | - | - | - |",
    ]
    for event, samples in session.hook_ms.items():
        lines.append(
            f"| {event} | {len(samples)} | {percentile(samples, 50):.1f} | "
            f"{percentile(samples, 95):.1f} | {max(samples):.1f} |"
        )
    lines += [
        "",
        f"The first hook started the daemon: {cold_ms:.1f} ms.",
        "",
        "Stages, measured rounds only:",
        "",
        stage_table(summarize(records)),
        "",
        "Outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())),
        f"Fell back to the template (whole log): {template_lines}",
        f"Daemon resident memory: {memory['after_load_mib']:.0f} MiB after the speech model "
        f"loaded, {memory['idle_mib']:.0f} MiB idle after the run",
    ]
    if loaded is not None:
        lines.append(f"Ollama resident for {model}: {loaded.get('size', 0) / 2**30:.2f} GiB")
    return "\n".join(lines)


def machine() -> str:
    def sysctl(name: str) -> str:
        return subprocess.run(
            ["sysctl", "-n", name], capture_output=True, text=True, check=False
        ).stdout.strip()

    memory = int(sysctl("hw.memsize") or 0) / 2**30
    return (
        f"{sysctl('machdep.cpu.brand_string') or platform.machine()}, {memory:.0f} GiB, "
        f"macOS {platform.mac_ver()[0]}, Python {platform.python_version()}, "
        f"kokoro-onnx {version('kokoro-onnx')}, onnxruntime {version('onnxruntime')}, "
        f"load average {os.getloadavg()[0]:.1f}"
    )


if __name__ == "__main__":
    sys.exit(main())
