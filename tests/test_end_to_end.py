"""A session's worth of hooks, from the plugin's hook commands to what would be spoken.

Each event runs the hook command from hooks/hooks.json through `/bin/sh -c`,
as Claude Code runs it: the real shim, which starts the real daemon on the
first event. `HOME` is a throwaway folder laid out as `setup` leaves it, with no
config file, so every setting is its default. The daemon is the real one with
template wording, but its speech and presence are fakes (tests/fake_speech_daemon.py):
it writes each sentence to a file instead of playing it, and reads idle time
and the app in front from a file the test writes.
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts.client import request
from cc_notify_tts.presence import app_name

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "hooks"
LAUNCHER = REPO / "tests" / "fake_speech_daemon.py"
HOOKS: dict[str, Any] = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]

AWAY_IDLE = 600.0

FINISHED = (
    "Finished up in weather app. Added exponential backoff to the forecast fetcher "
    "and two tests for the retry path."
)
PERMISSION = "Weather app needs your okay to force-push the rebased branch to main."
QUESTION = (
    "Weather app has a question for you. Should the retry give up after three attempts or five?"
)
STILL_RUNNING = (
    "Done for now in weather app, but background work isn't finished. "
    "Still going: audit the forecast cache and run the integration test suite."
)
BACKGROUND_DONE = (
    "Background work's all done in weather app. "
    "The integration suite finished: 112 passed, 0 failed."
)
FAILED = "The turn failed in weather app. It hit a rate limit."


def wait_for(check: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


class Mac:
    """A throwaway home with the hooks installed and the fake-speech daemon behind them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "Library" / "Application Support" / "cc-notify-tts"
        self.socket = self.data / "daemon.sock"
        self.log = root / "Library" / "Logs" / "cc-notify-tts" / "daemon.log"
        (self.data / "bin").mkdir(parents=True)
        (self.data / "bin" / "python").symlink_to(sys.executable)
        daemon = self.data / "venv" / "bin" / "python"
        daemon.parent.mkdir(parents=True)
        # The shim runs `<venv>/bin/python -I -m cc_notify_tts.daemon`; this runs
        # the same daemon with fake speech and presence instead.
        daemon.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{LAUNCHER}" "{root}"\n')
        daemon.chmod(0o755)

    def hook(self, fixture: str, **overrides: Any) -> None:
        payload: dict[str, Any] = json.loads((FIXTURES / f"{fixture}.json").read_text())
        payload.update(overrides)
        event = payload["hook_event_name"]
        env = {
            "HOME": str(self.root),
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO),
            # Names the host where the process tree has no app, as on a CI runner.
            "__CFBundleIdentifier": "com.apple.Terminal",
        }
        done = subprocess.run(
            ["/bin/sh", "-c", HOOKS[event][0]["hooks"][0]["command"]],
            input=json.dumps(payload).encode(),
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert (done.returncode, done.stdout, done.stderr) == (0, b"", b""), fixture

    def set_presence(self, idle_seconds: float, front: str | None) -> None:
        presence = {"idle_seconds": idle_seconds, "front": front}
        (self.root / "presence.json").write_text(json.dumps(presence))

    def spoken(self) -> list[str]:
        path = self.root / "spoken.jsonl"
        if not path.exists():
            return []
        return [json.loads(line)["text"] for line in path.read_text().splitlines()]

    def status(self) -> dict[str, Any]:
        return request(self.socket, {"type": "status"})

    def decisions(self) -> list[tuple[str, bool, str]]:
        return [(d["event"], d["speak"], d["reason"]) for d in self.status()["recent"]]

    def say_next(self, fixture: str, expected: str, **overrides: Any) -> None:
        """Run a hook and wait for it to be spoken."""
        before = len(self.spoken())
        self.hook(fixture, **overrides)
        wait_for(lambda: len(self.spoken()) > before)
        assert self.spoken()[before:] == [expected], self.log_text()

    def log_text(self) -> str:
        return self.log.read_text() if self.log.exists() else ""


@pytest.fixture
def mac() -> Iterator[Mac]:
    # A Unix socket path must fit in 104 bytes, which pytest's tmp_path can exceed.
    root = Path(tempfile.mkdtemp(prefix="cnt-", dir="/tmp"))
    m = Mac(root)
    yield m
    with contextlib.suppress(OSError, ValueError, KeyError):
        os.kill(int(request(m.socket, {"type": "ping"}, 1.0)["pid"]), signal.SIGTERM)
    shutil.rmtree(root, ignore_errors=True)


def test_a_session_is_spoken_while_away_and_silent_while_present(mac: Mac) -> None:
    mac.set_presence(AWAY_IDLE, front=None)
    mac.hook("session_start")  # starts the daemon
    wait_for(lambda: bool(mac.status()["sessions"]))
    host = mac.status()["sessions"][0]["host_app"]
    assert host

    # A turn finishes.
    mac.hook("user_prompt_submit", prompt_id="p1")
    mac.say_next("stop", FINISHED, prompt_id="p1")

    # Permission, then the same request again within 5 s: said once.
    mac.hook("user_prompt_submit", prompt_id="p2")
    mac.say_next("permission_request_bash", PERMISSION, prompt_id="p2")
    mac.hook("permission_request_bash", prompt_id="p2")
    mac.say_next("permission_request_question", QUESTION, prompt_id="p2")

    # A turn leaves background work running; a wake-up as one of the two
    # finishes stays silent, and the one after the last says it's all done.
    mac.hook("user_prompt_submit", prompt_id="p3")
    mac.say_next("stop_background_running", STILL_RUNNING, prompt_id="p3")
    running: list[dict[str, Any]] = json.loads(
        (FIXTURES / "stop_background_running.json").read_text()
    )["background_tasks"]
    one_left = [running[0], {**running[1], "status": "completed"}]
    mac.hook("user_prompt_submit_task_notification", prompt_id="w1")
    mac.hook("stop_background_running", prompt_id="w1", background_tasks=one_left)
    mac.hook("user_prompt_submit_task_notification", prompt_id="w2")
    mac.say_next("stop_background_done", BACKGROUND_DONE, prompt_id="w2")

    # Back at the keyboard with the session's app in front: a denial stays silent.
    mac.set_presence(idle_seconds=1, front=host)
    mac.hook("permission_denied")
    wait_for(lambda: len(mac.decisions()) == 6)

    # Away again: a failed turn is spoken.
    mac.set_presence(AWAY_IDLE, front=None)
    mac.hook("user_prompt_submit", prompt_id="p4")
    mac.say_next("stop_failure", FAILED, prompt_id="p4")
    mac.hook("session_end")
    wait_for(lambda: mac.status()["sessions"] == [])

    # Nothing else was said or decided: the repeated permission request and the
    # wake-up with work still running made no notification at all.
    assert mac.spoken() == [FINISHED, PERMISSION, QUESTION, STILL_RUNNING, BACKGROUND_DONE, FAILED]
    away = "speaking: no keyboard or mouse input for 600 s"
    assert mac.decisions() == [
        ("finished", True, away),
        ("permission", True, away),
        ("question", True, away),
        ("still_running", True, away),
        ("background_done", True, away),
        ("denied", False, f"silent: you were active 1 s ago and {app_name(host)} is in front"),
        ("failed", True, away),
    ]
