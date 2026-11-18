"""Reaching the daemon: one request, and starting it when nothing answers."""

import json
import os
import shutil
import signal
import socketserver
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts import client

FAKE_DAEMON = Path(__file__).with_name("fake_daemon.py")


@pytest.fixture
def root() -> Iterator[Path]:
    # A Unix socket path must fit in 104 bytes, which pytest's tmp_path can exceed.
    path = Path(tempfile.mkdtemp(prefix="cnt-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def serve(path: Path, reply: bytes) -> socketserver.ThreadingUnixStreamServer:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.rfile.readline()
            self.wfile.write(reply + b"\n")

    server = socketserver.ThreadingUnixStreamServer(str(path), Handler)
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    return server


def fake_python(root: Path, socket: Path) -> Path:
    """An interpreter stand-in that starts the fake daemon, whatever arguments it gets."""
    script = root / "python"
    script.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_DAEMON}" "{socket}" "{root / "rec"}"\n'
    )
    script.chmod(0o755)
    return script


def test_answering_daemon_gets_the_message_and_none_is_started(root: Path) -> None:
    sock = root / "d.sock"
    server = serve(sock, b'{"ok": true}')
    try:
        reply, started = client.deliver({"type": "event"}, sock, root / "no-python")
    finally:
        server.shutdown()
        server.server_close()
    assert (reply, started) == ({"ok": True}, False)


def test_refusal_is_returned_not_retried(root: Path) -> None:
    sock = root / "d.sock"
    server = serve(sock, b'{"ok": false, "error": "not JSON"}')
    try:
        reply, started = client.deliver({"type": "event"}, sock, root / "no-python")
    finally:
        server.shutdown()
        server.server_close()
    assert reply == {"ok": False, "error": "not JSON"}
    assert not started


def test_starts_the_daemon_and_retries_until_it_answers(root: Path) -> None:
    sock = root / "d.sock"
    begin = time.monotonic()
    reply, started = client.deliver({"type": "event", "n": 1}, sock, fake_python(root, sock))
    assert (reply, started) == ({"ok": True}, True)
    assert time.monotonic() - begin < 2
    assert client.is_answering(sock)
    first = json.loads((root / "rec").read_text().splitlines()[0])
    os.kill(first["started"], signal.SIGTERM)


def test_gives_up_after_the_deadline(root: Path) -> None:
    silent = root / "python"
    silent.write_text("#!/bin/sh\nexit 0\n")
    silent.chmod(0o755)
    reply, started = client.deliver({"type": "event"}, root / "d.sock", silent, deadline=0.1)
    assert (reply, started) == (None, True)


def test_missing_interpreter_raises(root: Path) -> None:
    with pytest.raises(OSError):
        client.deliver({"type": "event"}, root / "d.sock", root / "missing")


def test_garbage_reply_is_no_answer(root: Path) -> None:
    sock = root / "d.sock"
    server = serve(sock, b"[]")
    try:
        assert not client.is_answering(sock)
        message: dict[str, Any] = {"type": "ping"}
        with pytest.raises(ValueError):
            client.request(sock, message)
    finally:
        server.shutdown()
        server.server_close()
