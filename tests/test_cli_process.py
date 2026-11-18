import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from cc_notify_tts import __version__
from cc_notify_tts.client import request


@pytest.mark.integration
def test_module_entry_point_runs_in_a_fresh_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cc_notify_tts", "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == __version__


@pytest.fixture
def home() -> Iterator[Path]:
    # Short, under /tmp: a Unix socket path must fit in 104 bytes.
    path = Path(tempfile.mkdtemp(prefix="cnt-", dir="/tmp"))
    yield path
    sock = path / "Library" / "Application Support" / "cc-notify-tts" / "daemon.sock"
    try:
        pid = request(sock, {"type": "ping"}, 1.0)["pid"]
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while sock.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    except (OSError, ValueError, KeyError):
        pass
    shutil.rmtree(path, ignore_errors=True)


def cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_notify_tts", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={**os.environ, "HOME": str(home)},
        cwd=home,
    )


@pytest.mark.integration
def test_mute_fire_and_status_against_a_real_daemon(home: Path) -> None:
    muted = cli(home, "mute", "--for", "30m")
    assert muted.returncode == 0, muted.stderr
    assert muted.stdout.startswith("Muted until ")

    before = cli(home, "status")
    assert before.returncode == 0, before.stderr
    assert "Daemon    not running; the next event starts it" in before.stdout
    assert "every event      silent: muted" in before.stdout

    # Starts the daemon with the CLI's interpreter; the gate reads the mute file.
    silent = cli(home, "fire", "finished")
    assert silent.returncode == 0, silent.stderr
    assert "Started the daemon." in silent.stdout
    assert "Not spoken. Muted." in silent.stdout

    # --always skips the gate. There's no speech model here, so speech is
    # either still loading (0) or already unavailable (1).
    loud = cli(home, "fire", "failed", "--always")
    assert loud.returncode in (0, 1), loud.stderr
    assert 'Speaking: "The turn failed in ' in loud.stdout

    assert cli(home, "unmute").stdout == "Unmuted.\n"
    after = cli(home, "status")
    assert "Daemon    running, pid " in after.stdout
    assert "Mute      off" in after.stdout
    assert "Recent decisions" in after.stdout
    assert "finished: silent: muted" in after.stdout
    assert "failed: speaking: fired with --always" in after.stdout
