"""The daemon end to end: hook fixtures sent through its socket, spoken by a fake speaker."""

import json
import logging
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts.client import is_answering, request
from cc_notify_tts.daemon import Daemon, Paths
from cc_notify_tts.timeline import STAGES, Timeline, parse

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures" / "hooks"
TERMINAL = "com.apple.Terminal"

STILL_RUNNING = (
    "Done for now in weather app, but background work isn't finished. "
    "Still going: audit the forecast cache and run the integration test suite."
)
BACKGROUND_DONE = (
    "Background work's all done in weather app. "
    "The integration suite finished: 112 passed, 0 failed."
)
PERMISSION = "Weather app needs your okay to force-push the rebased branch to main."
QUESTION = (
    "Weather app has a question for you. Should the retry give up after three attempts or five?"
)
FAILED = "The turn failed in weather app. It hit a rate limit."

# Every event spoken whatever the presence, except `denied`, to show the gate runs.
ALWAYS = "\n".join(
    f'[events.{kind}]\nspeak = "{"never" if kind == "denied" else "always"}"'
    for kind in (
        "finished",
        "still_running",
        "background_done",
        "permission",
        "question",
        "denied",
        "failed",
    )
)


def fixture(name: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    payload.update(overrides)
    return payload


def wait_for(check: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


class FakeSpeaker:
    """Records what it's asked to say. With `blocked` set, the first call waits for it."""

    def __init__(self, blocked: threading.Event | None = None) -> None:
        self.spoken: list[str] = []
        self.blocked = blocked
        self.started = threading.Event()

    def speak(self, text: str, voice: str, timeline: Timeline) -> bool:
        timeline.mark("playing")
        self.started.set()
        if self.blocked is not None and not self.spoken:
            self.blocked.wait(5)
        self.spoken.append(text)
        return True


class Running:
    """A daemon serving on its own thread."""

    def __init__(self, paths: Paths, make_speaker: Callable[[], FakeSpeaker]) -> None:
        self.daemon = Daemon(paths, make_speaker, read_idle=lambda: None, follow_front=False)
        self.exit_code: int | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        self.exit_code = self.daemon.run()

    def send(self, name: str, fired_at: float | None = None, **overrides: Any) -> None:
        message: dict[str, Any] = {"type": "event", "payload": fixture(name, **overrides)}
        message["fired_at"] = time.time() if fired_at is None else fired_at
        message["host_app"] = TERMINAL
        assert request(self.daemon.paths.socket, message) == {"ok": True}

    def stop(self) -> None:
        self.daemon.stop()
        self.thread.join(5)


@pytest.fixture
def root() -> Iterator[Path]:
    # A Unix socket path must fit in 104 bytes, which pytest's tmp_path can exceed.
    path = Path(tempfile.mkdtemp(prefix="cnt-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def paths(root: Path, config: str = ALWAYS) -> Paths:
    (root / "config.toml").write_text(config)
    return Paths(root / "d.sock", root / "state", root / "config.toml")


@pytest.fixture
def running(root: Path) -> Iterator[tuple[Running, FakeSpeaker]]:
    speaker = FakeSpeaker()
    daemon = Running(paths(root), lambda: speaker)
    wait_for(lambda: is_answering(daemon.daemon.paths.socket))
    yield daemon, speaker
    daemon.stop()


def test_spoken_event_logs_every_stage_in_order(
    running: tuple[Running, FakeSpeaker], caplog: pytest.LogCaptureFixture
) -> None:
    d, speaker = running
    t = time.time()
    with caplog.at_level(logging.INFO):
        d.send("user_prompt_submit", t, prompt_id="p1")
        message = {"type": "event", "fired_at": t + 0.01, "sent_at": t + 0.02, "host_app": TERMINAL}
        payload = fixture("stop", prompt_id="p1")
        assert request(d.daemon.paths.socket, {**message, "payload": payload}) == {"ok": True}
        wait_for(lambda: any(r["outcome"] == "played" for r in parse(caplog.messages)))

    [played] = [r for r in parse(caplog.messages) if r["outcome"] == "played"]
    assert (played["hook"], played["kind"]) == ("Stop", "finished")
    expected = [s for s in STAGES if s not in ("synthesized", "ended")]
    assert list(played["marks"]) == expected
    times = list(played["marks"].values())
    assert times == sorted(times)
    assert speaker.spoken


def test_session_spoken_in_hook_order_then_idle_exit(root: Path) -> None:
    speaker = FakeSpeaker()
    config = ALWAYS + "\n[daemon]\nidle_timeout_minutes = 0.02\n"
    d = Running(paths(root, config), lambda: speaker)
    wait_for(lambda: is_answering(d.daemon.paths.socket))
    sock = d.daemon.paths.socket

    # A turn that leaves background work running, 10 s ago (past the 5 s dedupe).
    t = time.time() - 10
    d.send("session_start", t)
    d.send("user_prompt_submit", t + 0.01, prompt_id="p1")
    d.send("stop_background_running", t + 0.02, prompt_id="p1")
    wait_for(lambda: len(speaker.spoken) == 1)

    # The next turn's Stop reaches the socket before its prompt. The daemon must
    # classify them in the order the hooks fired, or the Stop is taken for a
    # wake-up turn while work is live and the user's turn goes silent.
    t = time.time()
    d.send("stop_background_running", t, prompt_id="p2")
    d.send("user_prompt_submit", t - 0.01, prompt_id="p2")
    wait_for(lambda: len(speaker.spoken) == 2)

    # "Background done" needs the "still running" announcement recorded as heard.
    d.send("user_prompt_submit_task_notification", prompt_id="w1")
    d.send("stop_background_done", prompt_id="w1")
    wait_for(lambda: len(speaker.spoken) == 3)

    d.send("permission_denied")  # set to never
    d.send("permission_request_bash")
    wait_for(lambda: len(speaker.spoken) == 4)
    status = request(sock, {"type": "status"})
    assert status["speech"] == "ready"
    assert [s["host_app"] for s in status["sessions"]] == [TERMINAL]
    assert [(r["event"], r["speak"]) for r in status["recent"]] == [
        ("still_running", True),
        ("still_running", True),
        ("background_done", True),
        ("denied", False),
        ("permission", True),
    ]
    assert status["recent"][3]["reason"] == "silent: denied is set to never"

    d.send("session_end")
    d.thread.join(10)
    assert not d.thread.is_alive()
    assert d.exit_code == 0
    assert not sock.exists()
    assert speaker.spoken == [STILL_RUNNING, STILL_RUNNING, BACKGROUND_DONE, PERMISSION]


def test_newer_message_replaces_the_pending_one(root: Path) -> None:
    release = threading.Event()
    speaker = FakeSpeaker(blocked=release)
    d = Running(paths(root), lambda: speaker)
    try:
        wait_for(lambda: is_answering(d.daemon.paths.socket))
        d.send("permission_request_bash")
        assert speaker.started.wait(5)
        d.send("permission_request_question")
        d.send("stop_failure")
        wait_for(lambda: request(d.daemon.paths.socket, {"type": "status"})["pending"] == FAILED)
        release.set()
        wait_for(lambda: len(speaker.spoken) == 2)
        time.sleep(0.2)
        assert speaker.spoken == [PERMISSION, FAILED]
    finally:
        release.set()
        d.stop()


def test_message_older_than_30_s_is_dropped(running: tuple[Running, FakeSpeaker]) -> None:
    d, speaker = running
    d.send("permission_request_bash", time.time() - 31)
    d.send("stop_failure")
    wait_for(lambda: len(speaker.spoken) == 1)
    time.sleep(0.2)
    assert speaker.spoken == [FAILED]


def test_pings_are_answered_while_speech_loads(root: Path) -> None:
    loaded = threading.Event()
    speaker = FakeSpeaker()

    def slow_speaker() -> FakeSpeaker:
        loaded.wait(5)
        return speaker

    d = Running(paths(root), slow_speaker)
    try:
        wait_for(lambda: is_answering(d.daemon.paths.socket))
        assert request(d.daemon.paths.socket, {"type": "status"})["speech"] == "loading"
        d.send("permission_request_bash")
        loaded.set()
        wait_for(lambda: speaker.spoken == [PERMISSION])
    finally:
        loaded.set()
        d.stop()


def test_second_daemon_exits_and_first_keeps_serving(
    root: Path, running: tuple[Running, FakeSpeaker]
) -> None:
    first, _ = running
    second = Daemon(first.daemon.paths, FakeSpeaker, follow_front=False)
    assert second.run() == 0
    assert is_answering(first.daemon.paths.socket)


def test_stale_socket_file_is_recovered(root: Path) -> None:
    p = paths(root)
    # A daemon that died without cleaning up leaves its socket file behind.
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(str(p.socket))
    dead.close()
    assert p.socket.exists()
    assert not is_answering(p.socket)

    d = Running(p, FakeSpeaker)
    try:
        wait_for(lambda: is_answering(p.socket))
    finally:
        d.stop()
