"""Requests the daemon accepts and rejects, without a socket."""

import json
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts import __version__
from cc_notify_tts.daemon import MAX_MESSAGE_BYTES, Daemon, Paths
from cc_notify_tts.queues import Received

NOW = 1_000_000.0


class NoSpeaker:
    def speak(self, text: str, voice: str) -> bool:
        raise AssertionError("not used")


@pytest.fixture
def daemon(tmp_path: Path) -> Daemon:
    paths = Paths(tmp_path / "d.sock", tmp_path / "state", tmp_path / "config.toml")
    return Daemon(paths, NoSpeaker, follow_front=False, clock=lambda: NOW)


def send(daemon: Daemon, message: Any) -> dict[str, Any]:
    return daemon.handle_request(json.dumps(message).encode() + b"\n")


def test_ping(daemon: Daemon) -> None:
    reply = send(daemon, {"type": "ping"})
    assert reply["ok"] is True
    assert reply["version"] == __version__


@pytest.mark.parametrize(
    ("line", "error"),
    [
        (b"not json\n", "not JSON"),
        (b"[1, 2]\n", "not a JSON object"),
        (b'{"type": "shout"}\n', "unknown type: 'shout'"),
        (b'{"type": "event"}\n', "event needs a payload object"),
        (b'{"type": "event", "payload": "x"}\n', "event needs a payload object"),
    ],
)
def test_bad_requests_get_an_error(daemon: Daemon, line: bytes, error: str) -> None:
    assert daemon.handle_request(line) == {"ok": False, "error": error}


def test_oversized_request_is_refused(daemon: Daemon) -> None:
    reply = daemon.handle_request(b"x" * (MAX_MESSAGE_BYTES + 1))
    assert reply == {"ok": False, "error": "message too large"}


def queued(daemon: Daemon) -> Received:
    item = daemon._inbox.get(timeout=1.0)  # pyright: ignore[reportPrivateUsage]
    assert isinstance(item, Received)
    return item


def test_event_is_queued_with_its_hook_time_and_host(daemon: Daemon) -> None:
    payload = {"session_id": "s1", "hook_event_name": "Stop"}
    message = {"type": "event", "fired_at": NOW - 2, "host_app": "com.apple.Terminal"}
    assert send(daemon, {**message, "payload": payload}) == {"ok": True}
    assert queued(daemon) == Received(payload, NOW - 2, "com.apple.Terminal")


@pytest.mark.parametrize("fired_at", [None, "soon", NOW + 60])
def test_missing_or_future_hook_time_is_arrival_time(daemon: Daemon, fired_at: Any) -> None:
    send(daemon, {"type": "event", "fired_at": fired_at, "host_app": "", "payload": {}})
    assert queued(daemon) == Received({}, NOW, None)


def test_status_before_any_session(daemon: Daemon) -> None:
    reply = send(daemon, {"type": "status"})
    assert reply["ok"] is True
    assert reply["speech"] == "loading"
    assert reply["sessions"] == []
    assert reply["idle_timeout_seconds"] == 7200
    assert (reply["speaking"], reply["pending"], reply["front_app"]) == (None, None, None)
