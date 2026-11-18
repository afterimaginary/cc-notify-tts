"""The Ollama client against a stand-in HTTP server, and keeping the model warm."""

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from cc_notify_tts.ollama import (
    KEEP_ALIVE,
    NUM_CTX,
    PING_SECONDS,
    KeepWarm,
    Ollama,
    OllamaError,
    same_model,
)


class StandIn:
    """What the stand-in server answers, and the requests it saw."""

    def __init__(self) -> None:
        self.status = 200
        self.reply: Any = {}
        self.delay = 0.0
        self.raw: bytes | None = None
        self.requests: list[tuple[str, str, Any]] = []


@pytest.fixture
def server() -> Iterator[tuple[StandIn, Ollama]]:
    stand_in = StandIn()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._answer(None)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self._answer(json.loads(self.rfile.read(length)))

        def _answer(self, body: Any) -> None:
            stand_in.requests.append((self.command, self.path, body))
            time.sleep(stand_in.delay)
            data = stand_in.raw if stand_in.raw is not None else json.dumps(stand_in.reply).encode()
            self.send_response(stand_in.status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield stand_in, Ollama(f"http://127.0.0.1:{httpd.server_address[1]}")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_loaded_context_reads_ps(server: tuple[StandIn, Ollama]) -> None:
    stand_in, client = server
    stand_in.reply = {
        "models": [
            {"name": "other:1b", "context_length": 4096},
            {"name": "tiny:1b", "model": "tiny:1b", "context_length": 2048},
        ]
    }
    assert client.loaded_context("tiny:1b") == 2048
    assert client.loaded_context("missing:1b") is None
    assert stand_in.requests[0][:2] == ("GET", "/api/ps")


def test_running_returns_the_models_ps_entry(server: tuple[StandIn, Ollama]) -> None:
    stand_in, client = server
    entry = {"name": "tiny:1b", "digest": "abc", "size": 10, "size_vram": 8}
    stand_in.reply = {"models": [entry]}
    assert client.running("tiny") is None
    assert client.running("tiny:1b") == entry


def test_version(server: tuple[StandIn, Ollama]) -> None:
    stand_in, client = server
    stand_in.reply = {"version": "0.40.16"}
    assert client.version() == "0.40.16"
    assert stand_in.requests[0][:2] == ("GET", "/api/version")
    stand_in.reply = {}
    with pytest.raises(OllamaError, match="no version"):
        client.version()


def test_chat_sends_context_keep_alive_and_no_thinking(server: tuple[StandIn, Ollama]) -> None:
    stand_in, client = server
    stand_in.reply = {"message": {"role": "assistant", "content": "Added backoff."}}
    assert client.chat("tiny:1b", "sys", "user", timeout=1.0, max_tokens=40) == "Added backoff."
    _, path, body = stand_in.requests[0]
    assert path == "/api/chat"
    assert body["keep_alive"] == KEEP_ALIVE
    assert body["think"] is False
    assert body["stream"] is False
    assert body["options"]["num_ctx"] == NUM_CTX
    assert body["options"]["num_predict"] == 40
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_load_and_unload(server: tuple[StandIn, Ollama]) -> None:
    stand_in, client = server
    client.load("tiny:1b")
    client.unload("tiny:1b")
    load, unload = (body for _, _, body in stand_in.requests)
    assert load == {"model": "tiny:1b", "keep_alive": KEEP_ALIVE, "options": {"num_ctx": NUM_CTX}}
    assert unload == {"model": "tiny:1b", "keep_alive": 0}


def test_missing_model(server: tuple[StandIn, Ollama]) -> None:
    stand_in, client = server
    stand_in.status = 404
    stand_in.reply = {"error": "model 'tiny:1b' not found"}
    with pytest.raises(OllamaError, match="model not found"):
        client.chat("tiny:1b", "s", "u", timeout=1.0, max_tokens=40)


def test_slow_answer_times_out(server: tuple[StandIn, Ollama]) -> None:
    stand_in, client = server
    stand_in.delay = 0.3
    with pytest.raises(OllamaError, match=r"no answer within 0.1 s"):
        client.chat("tiny:1b", "s", "u", timeout=0.1, max_tokens=40)


@pytest.mark.parametrize(
    ("raw", "error"),
    [(b"not json", "wasn't JSON"), (b"[]", "wasn't a JSON object"), (b"{}", "no message")],
)
def test_bad_replies(server: tuple[StandIn, Ollama], raw: bytes, error: str) -> None:
    stand_in, client = server
    stand_in.raw = raw
    with pytest.raises(OllamaError, match=error):
        client.chat("tiny:1b", "s", "u", timeout=1.0, max_tokens=40)


def test_not_running() -> None:
    with pytest.raises(OllamaError, match="not running"):
        Ollama("http://127.0.0.1:1").loaded_context("tiny:1b")


@pytest.mark.parametrize(
    ("name", "model", "same"),
    [
        ("tiny:1b", "tiny:1b", True),
        ("tiny:latest", "tiny", True),
        ("tiny", "tiny:latest", True),
        ("tiny:1b", "tiny", False),
        ("me/tiny:latest", "me/tiny", True),
        (None, "tiny", False),
    ],
)
def test_same_model(name: Any, model: str, same: bool) -> None:
    assert same_model(name, model) is same


class Recorder:
    """A client that records loads and unloads, and can fail them."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def loaded_context(self, model: str) -> int | None:
        return None

    def chat(self, model: str, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        raise AssertionError("not used")

    def load(self, model: str) -> None:
        self.calls.append(("load", model))
        if self.fail:
            raise OllamaError("not running")

    def unload(self, model: str) -> None:
        self.calls.append(("unload", model))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_keep_warm_loads_pings_and_unloads() -> None:
    client, clock = Recorder(), Clock()
    warm = KeepWarm(client, clock)
    warm.update("tiny:1b")  # a session opened
    warm.update("tiny:1b")  # another event: nothing to do yet
    assert not warm.ping_due()
    clock.now += PING_SECONDS
    assert warm.ping_due()
    warm.update("tiny:1b")  # the ping
    warm.update(None)  # no session open
    warm.update(None)
    warm.close()
    assert client.calls == [("load", "tiny:1b"), ("load", "tiny:1b"), ("unload", "tiny:1b")]


def test_keep_warm_is_idle_with_no_session() -> None:
    client, clock = Recorder(), Clock()
    warm = KeepWarm(client, clock)
    warm.update(None)
    clock.now += PING_SECONDS
    assert not warm.ping_due()
    warm.close()
    assert client.calls == []


def test_changing_model_unloads_the_old_one() -> None:
    client = Recorder()
    warm = KeepWarm(client, Clock())
    warm.update("tiny:1b")
    warm.update("small:3b")
    warm.close()
    assert client.calls == [
        ("load", "tiny:1b"),
        ("unload", "tiny:1b"),
        ("load", "small:3b"),
        ("unload", "small:3b"),
    ]


def test_close_unloads_the_warm_model() -> None:
    client = Recorder()
    warm = KeepWarm(client, Clock())
    warm.update("tiny:1b")
    warm.close()
    assert client.calls[-1] == ("unload", "tiny:1b")


def test_a_failed_load_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    client = Recorder(fail=True)
    warm = KeepWarm(client, Clock())
    with caplog.at_level("INFO"):
        warm.update("tiny:1b")
        warm.close()
    assert "couldn't load tiny:1b: not running" in caplog.text
