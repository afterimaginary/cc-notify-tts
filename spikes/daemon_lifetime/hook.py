"""Hook command for the spike: starts the probe detached and returns at once.

It detaches the way the real hook shim will start the daemon: a new session (so
no controlling terminal and no hangup when a window closes), its own process
group, stdio pointed away from Claude Code's pipes (or Claude Code would wait on
them), and an immediate exit so the probe is reparented to launchd.

Usage, from a hook: python3 hook.py <out.jsonl> <label> <rounds> <interval>
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    started = time.perf_counter()
    out, label, rounds, interval = sys.argv[1:5]
    sys.stdin.read()  # drain the hook payload so Claude Code's write never blocks
    probe = Path(__file__).with_name("probe.py")
    subprocess.Popen(
        [
            sys.executable,
            str(probe),
            "--out",
            out,
            "--label",
            label,
            "--rounds",
            rounds,
            "--interval",
            interval,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    with Path(out).with_suffix(".hook.log").open("a") as f:
        f.write(
            f"{label} hook_pid={os.getpid()} hook_ppid={os.getppid()} spawn_ms={elapsed_ms:.1f}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
