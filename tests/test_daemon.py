"""Requests the daemon accepts and rejects, without a socket."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts import __version__
from cc_notify_tts.client import MAX_MESSAGE_BYTES
from cc_notify_tts.config import Wording
from cc_notify_tts.daemon import Daemon, Paths
from cc_notify_tts.mute import MuteFile
from cc_notify_tts.queues import Received, Utterance
from cc_notify_tts.sessions import SessionState, SessionStore
from cc_notify_tts.timeline import Timeline, parse

NOW = 1_000_000.0


class NoSpeaker:
    def speak(self, text: str, voice: str, timeline: Timeline) -> bool:
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
    assert queued(daemon) == Received(
        payload, NOW - 2, "com.apple.Terminal", Timeline("Stop", "s1")
    )


def test_event_timeline_has_the_shim_marks_and_arrival(daemon: Daemon) -> None:
    payload = {"session_id": "s1", "hook_event_name": "Stop"}
    send(daemon, {"type": "event", "fired_at": NOW - 2, "sent_at": NOW - 1, "payload": payload})
    timeline = queued(daemon).timeline
    assert (timeline.hook, timeline.session_id) == ("Stop", "s1")
    assert timeline.marks == {"hook": NOW - 2, "sent": NOW - 1, "received": NOW}


@pytest.mark.parametrize(("sent_at", "logged"), [("soon", NOW), (NOW + 60, NOW), (True, NOW)])
def test_bad_or_future_send_time_is_arrival_time(
    daemon: Daemon, sent_at: Any, logged: float
) -> None:
    send(daemon, {"type": "event", "fired_at": NOW - 2, "sent_at": sent_at, "payload": {}})
    assert queued(daemon).timeline.marks["sent"] == logged


def test_event_without_send_time_has_no_sent_mark(daemon: Daemon) -> None:
    send(daemon, {"type": "event", "fired_at": NOW - 2, "payload": {}})
    assert "sent" not in queued(daemon).timeline.marks


@pytest.mark.parametrize("fired_at", [None, "soon", NOW + 60])
def test_missing_or_future_hook_time_is_arrival_time(daemon: Daemon, fired_at: Any) -> None:
    send(daemon, {"type": "event", "fired_at": fired_at, "host_app": "", "payload": {}})
    assert queued(daemon) == Received({}, NOW, None, Timeline("?", ""))


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
    daemon._handle_event(Received(payload, NOW, None, Timeline("Stop", "s1")))  # pyright: ignore[reportPrivateUsage]
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
    daemon._handle_event(Received(end, NOW, None, Timeline("SessionEnd", "s1")))  # pyright: ignore[reportPrivateUsage]
    daemon._update_keep_warm()  # pyright: ignore[reportPrivateUsage]
    assert daemon._keep_warm is not None  # pyright: ignore[reportPrivateUsage]
    daemon._keep_warm.close()  # pyright: ignore[reportPrivateUsage]
    assert ollama.calls == [("chat", "tiny:1b"), ("load", "tiny:1b"), ("unload", "tiny:1b")]


def test_queued_sentence_carries_the_event_timeline(tmp_path: Path) -> None:
    daemon, _ = worded(tmp_path, FakeOllama("Added backoff to the forecast fetcher."))
    _, pending = daemon._slot.snapshot()  # pyright: ignore[reportPrivateUsage]
    assert pending is not None
    assert pending.timeline.kind == "finished"
    assert list(pending.timeline.marks) == ["dequeued", "gated", "worded"]


def timing_outcomes(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r["outcome"] for r in parse(caplog.messages)]


def test_silenced_and_unannounced_events_log_their_timeline(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[events.finished]\nspeak = "never"\n')
    paths = Paths(tmp_path / "d.sock", tmp_path / "state", config)
    daemon = Daemon(paths, NoSpeaker, follow_front=False, clock=lambda: NOW)
    prompt = json.loads((FIXTURES / "user_prompt_submit.json").read_text())
    stop = json.loads((FIXTURES / "stop.json").read_text())
    with caplog.at_level(logging.INFO):
        for payload in (prompt, stop):
            timeline = Timeline(payload["hook_event_name"], payload["session_id"])
            daemon._handle_event(Received(payload, NOW, None, timeline))  # pyright: ignore[reportPrivateUsage]
    assert timing_outcomes(caplog) == ["no notification", "silenced"]


def test_replaced_sentence_logs_its_timeline(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    daemon, _ = worded(tmp_path, FakeOllama("Added backoff to the forecast fetcher."))
    with caplog.at_level(logging.INFO):
        send(daemon, {"type": "fire", "event": "failed", "always": True})
    [record] = parse(caplog.messages)
    assert (record["hook"], record["kind"], record["outcome"]) == ("Stop", "finished", "replaced")


# --- fire -------------------------------------------------------------------------


def pending(daemon: Daemon) -> Utterance | None:
    return daemon._slot.snapshot()[1]  # pyright: ignore[reportPrivateUsage]


def test_fire_always_queues_the_sample_sentence(daemon: Daemon) -> None:
    reply = send(
        daemon,
        {"type": "fire", "event": "permission", "always": True, "cwd": "/src/weather-app"},
    )
    assert reply == {
        "ok": True,
        "speak": True,
        "reason": "speaking: fired with --always",
        "text": "Weather app needs your okay to run npm test.",
        "speech": "loading",
    }
    utterance = pending(daemon)
    assert utterance is not None
    assert utterance.text == "Weather app needs your okay to run npm test."
    assert utterance.fired_at == NOW


def test_fire_goes_through_the_gate(tmp_path: Path) -> None:
    paths = Paths(tmp_path / "d.sock", tmp_path / "state", tmp_path / "config.toml")
    paths.config.write_text('[events.finished]\nspeak = "never"\n')
    daemon = Daemon(paths, NoSpeaker, follow_front=False, clock=lambda: NOW)
    reply = send(daemon, {"type": "fire", "event": "finished", "cwd": "/src/app"})
    assert reply["speak"] is False
    assert reply["reason"] == "silent: finished is set to never"
    assert reply["text"] is None
    assert pending(daemon) is None
    recent = daemon.status()["recent"]
    assert [(r["event"], r["reason"]) for r in recent] == [
        ("finished", "silent: finished is set to never")
    ]


def test_fire_is_silent_while_the_mute_file_says_so(tmp_path: Path) -> None:
    paths = Paths(tmp_path / "d.sock", tmp_path / "state", tmp_path / "config.toml")
    MuteFile(paths.state / "mute").mute(None)
    daemon = Daemon(paths, NoSpeaker, follow_front=False, clock=lambda: NOW)
    reply = send(daemon, {"type": "fire", "event": "failed"})
    assert reply["reason"] == "silent: muted"
    # --always skips the whole gate, mute included.
    assert send(daemon, {"type": "fire", "event": "failed", "always": True})["speak"] is True


def test_fire_leaves_session_state_alone(daemon: Daemon) -> None:
    send(daemon, {"type": "fire", "event": "still_running", "always": True})
    assert not daemon.paths.state.exists() or not list(daemon.paths.state.glob("*.json"))
    assert daemon.status()["sessions"] == []


@pytest.mark.parametrize("event", [None, "shout", 3])
def test_fire_needs_a_known_event(daemon: Daemon, event: Any) -> None:
    reply = send(daemon, {"type": "fire", "event": event})
    assert reply == {"ok": False, "error": f"unknown event: {event!r}"}


def cold_fire(tmp_path: Path, session_open: bool) -> list[tuple[str, str]]:
    """What a cold model is asked to do after a `fire --always finished`."""
    ollama = FakeOllama("Fixed it.", context=None)
    paths = Paths(tmp_path / "d.sock", tmp_path / "state", tmp_path / "config.toml")
    if session_open:
        SessionStore(paths.state).save(SessionState("s1", last_event_at=NOW))
    daemon = Daemon(paths, NoSpeaker, follow_front=False, clock=lambda: NOW, ollama=ollama)
    send(daemon, {"type": "fire", "event": "finished", "always": True})
    assert daemon._keep_warm is not None  # pyright: ignore[reportPrivateUsage]
    daemon._keep_warm.close()  # pyright: ignore[reportPrivateUsage]
    return ollama.calls


def test_fire_with_no_session_open_never_loads_the_model(tmp_path: Path) -> None:
    assert cold_fire(tmp_path, session_open=False) == []


def test_fire_with_a_session_open_loads_a_cold_model_for_next_time(tmp_path: Path) -> None:
    assert ("load", Wording().model) in cold_fire(tmp_path, session_open=True)
