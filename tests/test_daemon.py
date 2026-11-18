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


FIXTURES = Path(__file__).parent / "fixtures" / "hooks"


class FakeOllama:
    """A loaded model that answers `reply`, recording loads and unloads."""

    def __init__(self, reply: str, context: int | None = 2048) -> None:
        self.reply = reply
        self.context = context
        self.calls: list[tuple[str, str]] = []

    def loaded_context(self, model: str) -> int | None:
        return self.context

    def chat(self, model: str, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        self.calls.append(("chat", model))
        return self.reply

    def load(self, model: str) -> None:
        self.calls.append(("load", model))

    def unload(self, model: str) -> None:
        self.calls.append(("unload", model))


def worded(tmp_path: Path, ollama: FakeOllama, config_text: str = "") -> tuple[Daemon, str | None]:
    """The sentence a daemon puts in the slot for the `stop` fixture, spoken always."""
    config = tmp_path / "config.toml"
    config.write_text('[events.finished]\nspeak = "always"\n' + config_text)
    paths = Paths(tmp_path / "d.sock", tmp_path / "state", config)
    daemon = Daemon(paths, NoSpeaker, follow_front=False, clock=lambda: NOW, ollama=ollama)
    payload = json.loads((FIXTURES / "stop.json").read_text())
    daemon._handle_event(Received(payload, NOW, None))  # pyright: ignore[reportPrivateUsage]
    _, pending = daemon._slot.snapshot()  # pyright: ignore[reportPrivateUsage]
    return daemon, pending.text if pending else None


def test_model_writes_the_detail_under_the_template_frame(tmp_path: Path) -> None:
    _, text = worded(tmp_path, FakeOllama("Added backoff to the forecast fetcher."))
    assert text == "Finished up in weather app. Added backoff to the forecast fetcher."


def test_a_guard_failure_speaks_the_template(tmp_path: Path) -> None:
    _, text = worded(tmp_path, FakeOllama("I added Redis."))
    assert text == (
        "Finished up in weather app. Added exponential backoff to the forecast fetcher"
        " and two tests for the retry path."
    )


def test_wording_off_never_asks_the_model(tmp_path: Path) -> None:
    ollama = FakeOllama("Added backoff to the forecast fetcher.")
    _, text = worded(tmp_path, ollama, "[wording]\nollama = false\n")
    assert text is not None and text.startswith("Finished up in weather app. Added exponential")
    assert ollama.calls == []


def test_model_kept_warm_while_a_session_is_open_and_unloaded_after(tmp_path: Path) -> None:
    ollama = FakeOllama("Added backoff to the forecast fetcher.")
    daemon, _ = worded(tmp_path, ollama, '[wording]\nmodel = "tiny:1b"\n')
    daemon._update_keep_warm()  # pyright: ignore[reportPrivateUsage]
    end = json.loads((FIXTURES / "session_end.json").read_text())
    daemon._handle_event(Received(end, NOW, None))  # pyright: ignore[reportPrivateUsage]
    daemon._update_keep_warm()  # pyright: ignore[reportPrivateUsage]
    assert daemon._keep_warm is not None  # pyright: ignore[reportPrivateUsage]
    daemon._keep_warm.close()  # pyright: ignore[reportPrivateUsage]
    assert ollama.calls == [("chat", "tiny:1b"), ("load", "tiny:1b"), ("unload", "tiny:1b")]
