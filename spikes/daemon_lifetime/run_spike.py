"""Runs every scenario of the daemon lifetime and priority spike at once and summarizes them.

Scenarios, all running the same probe (probe.py) side by side so they share the
same machine load:

- shell: the probe started straight from a Terminal window; the baseline.
- hook-headless: a SessionStart hook in `claude -p` starts the probe; the
  session then ends on its own.
- hook-terminal-close: a SessionStart hook in an interactive `claude` starts the
  probe; the Terminal window is then closed (Terminate) mid-session.
- hook-app-close-sim: as above, but the session is ended the way an app closing
  its window plausibly would: SIGKILL to the claude process group and to every
  descendant found by parent pid. Simulated; the desktop app is not driven.
- agent-default / agent-interactive / agent-background: LaunchAgents running the
  probe with ProcessType unset, Interactive and Background.

Needs Terminal.app, Accessibility permission for the caller (to confirm
Terminal's "terminate running processes" sheet), and a logged-in `claude`.
`claude -p` sends one short prompt. Every LaunchAgent is booted out and its plist
deleted before exit, even on failure.

Usage: uv run python spikes/daemon_lifetime/run_spike.py <out-dir>
"""

from __future__ import annotations

import contextlib
import json
import os
import plistlib
import shlex
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROBE = HERE / "probe.py"
HOOK = HERE / "hook.py"
PYTHON = sys.executable
ROUNDS = 30
INTERVAL = 2.0
EVENT_AFTER = 15.0  # seconds after a probe's first line before its session is ended
LABEL_PREFIX = "io.github.afterimaginary.cc-notify-tts.spike"
AGENTS = {
    "agent-default": None,
    "agent-interactive": "Interactive",
    "agent-background": "Background",
}


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def osascript(*lines: str) -> str:
    return run("osascript", *(arg for line in lines for arg in ("-e", line)))


def terminal_window(command: str) -> str:
    """Opens a Terminal window running `command`; returns its tty."""
    return osascript(
        'tell application "Terminal"',
        f"set t to do script {json.dumps(command)}",
        "return tty of t",
        "end tell",
    )


def close_terminal_window(tty: str, title_part: str) -> None:
    """Closes the window on `tty`, answering Terminate if Terminal asks."""
    osascript(
        'tell application "Terminal"',
        "repeat with w in windows",
        # Some windows have no selected tab; skip them. Stop after closing one,
        # because the window list changes underneath the loop.
        "try",
        f"if tty of selected tab of w is {json.dumps(tty)} then",
        "close w",
        "exit repeat",
        "end if",
        "end try",
        "end repeat",
        "end tell",
    )
    time.sleep(1)
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to tell process "Terminal" to '
            f'click button "Terminate" of sheet 1 of (first window whose name contains '
            f"{json.dumps(title_part)})",
        ],
        capture_output=True,
    )


def claude_pid_on(tty: str) -> int | None:
    out = run("ps", "-o", "pid=,comm=", "-t", tty.removeprefix("/dev/"))
    for line in out.splitlines():
        pid, comm = line.split(None, 1)
        if Path(comm).name == "claude":
            return int(pid)
    return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def descendants(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for line in run("ps", "-A", "-o", "pid=,ppid=").splitlines():
        pid, ppid = (int(x) for x in line.split())
        children.setdefault(ppid, []).append(pid)
    found: list[int] = []
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def kill_like_an_app(pid: int) -> list[int]:
    """SIGKILLs the process group and every descendant still parented under `pid`."""
    victims = [pid, *descendants(pid)]
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    for victim in victims:
        with contextlib.suppress(ProcessLookupError):
            os.kill(victim, signal.SIGKILL)
    return victims


def probe_args(out: Path, label: str) -> list[str]:
    return [
        PYTHON,
        str(PROBE),
        "--out",
        str(out),
        "--label",
        label,
        "--rounds",
        str(ROUNDS),
        "--interval",
        str(INTERVAL),
    ]


def hook_settings(out_dir: Path, label: str) -> Path:
    hook = shlex.join([PYTHON, str(HOOK), str(out_dir / f"{label}.jsonl"), label, str(ROUNDS)])
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": f"{hook} {INTERVAL}"}]}]
        }
    }
    path = out_dir / f"settings-{label}.json"
    path.write_text(json.dumps(settings))
    return path


def claude_command(project: Path, settings: Path, *extra: str) -> str:
    return (
        shlex.join(
            [
                "cd",
                str(project),
            ]
        )
        + " && exec "
        + shlex.join(
            [
                "claude",
                "--setting-sources",
                "project",
                "--strict-mcp-config",
                "--settings",
                str(settings),
                *extra,
            ]
        )
    )


def lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def wait_for(predicate: Any, timeout: float, what: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {what}")


def install_agent(out_dir: Path, name: str, process_type: str | None) -> tuple[str, Path]:
    label = f"{LABEL_PREFIX}-{name}"
    plist: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": probe_args(out_dir / f"{name}.jsonl", name),
        "RunAtLoad": True,
        "StandardErrorPath": str(out_dir / f"{name}.err"),
    }
    if process_type:
        plist["ProcessType"] = process_type
    path = out_dir / f"{label}.plist"
    path.write_bytes(plistlib.dumps(plist))
    run("launchctl", "bootstrap", f"gui/{os.getuid()}", str(path))
    return label, path


def remove_agent(label: str, path: Path) -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
    path.unlink(missing_ok=True)


def summarize(out_dir: Path, events: dict[str, float]) -> str:
    labels = ["shell", "hook-headless", "hook-terminal-close", "hook-app-close-sim", *AGENTS]
    rows = [
        "| Scenario | Rounds | Rounds after session ended | Median ms | p90 ms | "
        "P-core share | CPU time by QoS | nice | Parent at end |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label in labels:
        data = lines(out_dir / f"{label}.jsonl")
        if not data:
            rows.append(f"| {label} | 0 | | | | | | | |")
            continue
        ms = sorted(d["elapsed_ms"] for d in data)
        p90 = ms[min(len(ms) - 1, round(0.9 * (len(ms) - 1)))]
        shares = [d["p_core_share"] for d in data if d["p_core_share"] is not None]
        qos: dict[str, float] = {}
        for d in data:
            for k, v in d["qos_share"].items():
                qos[k] = qos.get(k, 0.0) + v / len(data)
        qos_text = ", ".join(f"{k} {v:.0%}" for k, v in sorted(qos.items(), key=lambda kv: -kv[1]))
        event = events.get(label)
        after = sum(1 for d in data if event is not None and d["wall"] > event)
        rows.append(
            f"| {label} | {len(data)} | {after if event is not None else 'n/a'} | "
            f"{statistics.median(ms):.0f} | {p90:.0f} | {statistics.median(shares):.2f} | "
            f"{qos_text} | {data[-1]['nice']} | pid {data[-1]['ppid']} |"
        )
    return "\n".join(rows)


def main() -> int:
    out_dir = Path(sys.argv[1]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    project = out_dir / "project"
    project.mkdir(exist_ok=True)
    events: dict[str, float] = {}
    agents: list[tuple[str, Path]] = []
    ttys: dict[str, str] = {}
    try:
        for name, process_type in AGENTS.items():
            agents.append(install_agent(out_dir, name, process_type))
        ttys["shell"] = terminal_window(
            "exec " + shlex.join(probe_args(out_dir / "shell.jsonl", "shell"))
        )
        ttys["hook-headless"] = terminal_window(
            claude_command(
                project,
                hook_settings(out_dir, "hook-headless"),
                "-p",
                "Reply with the single word OK.",
            )
        )
        for label in ("hook-terminal-close", "hook-app-close-sim"):
            ttys[label] = terminal_window(claude_command(project, hook_settings(out_dir, label)))

        hook_labels = ["hook-headless", "hook-terminal-close", "hook-app-close-sim"]
        for label in hook_labels:
            wait_for(lambda label=label: lines(out_dir / f"{label}.jsonl"), 60, f"{label} probe")
        claude_pids = {label: claude_pid_on(ttys[label]) for label in hook_labels}
        print("claude pids:", claude_pids, flush=True)

        # hook-headless ends by itself once `claude -p` has answered.
        headless = claude_pids["hook-headless"]
        if headless is not None:
            wait_for(lambda: not alive(headless), 120, "claude -p to exit")
        events["hook-headless"] = time.time()

        time.sleep(EVENT_AFTER)
        close_terminal_window(ttys["hook-terminal-close"], "settings-hook-terminal-close")
        events["hook-terminal-close"] = time.time()

        sim = claude_pids["hook-app-close-sim"]
        if sim is not None:
            victims = kill_like_an_app(sim)
            print("killed:", victims, flush=True)
        events["hook-app-close-sim"] = time.time()

        time.sleep(2)
        for label in ("hook-terminal-close", "hook-app-close-sim"):
            pid = claude_pids[label]
            print(f"{label}: claude {pid} alive after event: {pid is not None and alive(pid)}")

        all_labels = ["shell", *hook_labels, *AGENTS]
        wait_for(
            lambda: all(len(lines(out_dir / f"{label}.jsonl")) >= ROUNDS for label in all_labels),
            ROUNDS * (INTERVAL + 3) + 60,
            "every probe to finish",
        )
    finally:
        for label, path in agents:
            remove_agent(label, path)
        for label, tty in ttys.items():
            if label != "hook-terminal-close":
                # Closing with Terminate also ends a claude a failed run left behind.
                with contextlib.suppress(subprocess.CalledProcessError):
                    title = "--label shell" if label == "shell" else f"settings-{label}"
                    close_terminal_window(tty, title)
        (out_dir / "events.json").write_text(json.dumps(events, indent=2))

    summary = summarize(out_dir, events)
    (out_dir / "summary.md").write_text(summary + "\n")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
