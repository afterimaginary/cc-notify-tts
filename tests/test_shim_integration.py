"""The plugin's hook commands, run as Claude Code runs them, against a fake daemon.

Each command from hooks/hooks.json runs through `/bin/sh -c` with the hook
fixtures on stdin, `CLAUDE_PLUGIN_ROOT` at this checkout and `HOME` at a
throwaway folder laid out as `setup` leaves it: `bin/python` for the hooks and
`venv/bin/python` for the daemon. That second one is a script that starts
tests/fake_daemon.py, so the shim's own start-the-daemon path runs for real.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "hooks"
FAKE_DAEMON = REPO / "tests" / "fake_daemon.py"
HOOKS: dict[str, Any] = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]


def command(event: str) -> str:
    return HOOKS[event][0]["hooks"][0]["command"]


class Home:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "Library" / "Application Support" / "cc-notify-tts"
        self.logs = root / "Library" / "Logs" / "cc-notify-tts"
        self.socket = self.data / "daemon.sock"
        self.record = root / "record.jsonl"

    def install(self, hook_python: bool = True, daemon_python: bool = True) -> None:
        if hook_python:
            (self.data / "bin").mkdir(parents=True)
            (self.data / "bin" / "python").symlink_to(sys.executable)
        if daemon_python:
            script = self.data / "venv" / "bin" / "python"
            script.parent.mkdir(parents=True)
            script.write_text(
                f'#!/bin/sh\nexec "{sys.executable}" -I "{FAKE_DAEMON}" '
                f'"{self.socket}" "{self.record}"\n'
            )
            script.chmod(0o755)

    def run_hook(
        self, event: str, payload: bytes, **env: str
    ) -> subprocess.CompletedProcess[bytes]:
        environment = {
            "HOME": str(self.root),
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(REPO),
            **env,
        }
        return subprocess.run(
            ["/bin/sh", "-c", command(event)],
            input=payload,
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def records(self) -> list[dict[str, Any]]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def requests(self) -> list[dict[str, Any]]:
        return [r["request"] for r in self.records() if "request" in r]

    def shim_log(self) -> str:
        path = self.logs / "shim.log"
        return path.read_text() if path.exists() else ""


@pytest.fixture
def home() -> Iterator[Home]:
    # A Unix socket path must fit in 104 bytes, which pytest's tmp_path can exceed.
    root = Path(tempfile.mkdtemp(prefix="cnt-", dir="/tmp"))
    h = Home(root)
    yield h
    for entry in h.records():
        if "started" in entry:
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(entry["started"]), 15)
    shutil.rmtree(root, ignore_errors=True)


def registered_fixtures() -> list[Path]:
    paths = sorted(FIXTURES.glob("*.json"))
    return [p for p in paths if json.loads(p.read_text())["hook_event_name"] in HOOKS]


def test_every_fixture_reaches_the_daemon_which_the_first_hook_starts(home: Home) -> None:
    home.install()
    fixtures = registered_fixtures()
    assert {json.loads(p.read_text())["hook_event_name"] for p in fixtures} == set(HOOKS)

    for path in fixtures:
        payload = json.loads(path.read_text())
        before = time.time()
        done = home.run_hook(payload["hook_event_name"], path.read_bytes())
        assert (done.returncode, done.stdout, done.stderr) == (0, b"", b""), path.name
        sent = home.requests()[-1]
        assert sent["type"] == "event"
        assert sent["payload"] == payload
        assert before <= sent["fired_at"] <= time.time()
        if payload["hook_event_name"] == "SessionStart":
            # Named from the test's own ancestry or environment; may be unknown in CI.
            host = sent.get("host_app")
            assert host is None or (isinstance(host, str) and host)
        else:
            assert "host_app" not in sent

    starts = [r for r in home.records() if "started" in r]
    assert len(starts) == 1, "one daemon, started by the first hook"
    started = starts[0]
    assert started["sid"] == started["started"], "the daemon leads its own session"
    assert started["cwd"] == "/", "the daemon holds no project folder open"
    assert len(home.requests()) == len(fixtures)
    assert "started the daemon" in home.shim_log()


def test_hooks_inside_tmux_name_the_host_on_announcing_events(home: Home) -> None:
    home.install()
    # No tmux client answers for this pane, so the host is unknown and not sent,
    # and nothing from the environment is trusted in its place.
    tmux = {"TMUX": "/tmp/none,1,0", "TMUX_PANE": "%999", "__CFBundleIdentifier": "x.y"}
    done = home.run_hook("SessionStart", (FIXTURES / "session_start.json").read_bytes(), **tmux)
    assert done.returncode == 0
    assert "host_app" not in home.requests()[-1]


def test_running_daemon_is_reused(home: Home) -> None:
    home.install()
    stop = (FIXTURES / "stop.json").read_bytes()
    home.run_hook("Stop", stop)
    home.run_hook("Stop", stop)
    assert len([r for r in home.records() if "started" in r]) == 1
    assert len(home.requests()) == 2


def test_without_setup_the_hook_does_nothing(home: Home) -> None:
    home.install(hook_python=False, daemon_python=False)
    done = home.run_hook("Stop", (FIXTURES / "stop.json").read_bytes())
    assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")
    assert not home.logs.exists()


def test_missing_daemon_environment_is_logged(home: Home) -> None:
    home.install(daemon_python=False)
    done = home.run_hook("Stop", (FIXTURES / "stop.json").read_bytes())
    assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")
    assert "Run `cc-notify-tts setup`" in home.shim_log()


@pytest.mark.parametrize("stdin", [b"", b"not json", b"[1, 2]", b"\xff\xfe"])
def test_bad_input_exits_quietly(home: Home, stdin: bytes) -> None:
    home.install()
    done = home.run_hook("Stop", stdin)
    assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")
    assert home.requests() == []
    assert "nothing sent" in home.shim_log()


@pytest.mark.skipif(shutil.which("claude") is None, reason="needs Claude Code's CLI")
def test_plugin_validator_passes() -> None:
    done = subprocess.run(
        ["claude", "plugin", "validate", str(REPO)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Validation passed" in done.stdout
