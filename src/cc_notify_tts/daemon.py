"""The daemon: takes hook events on a Unix socket and speaks the ones the gate lets through.

Run as `python -m cc_notify_tts.daemon`; the hook shim starts it on demand
(ADR-0002). It runs these threads:

- **Socket server:** one thread per connection, one JSON request and one reply
  each (docs/specs/daemon-protocol.md). It answers pings from the moment the
  socket is bound, before the speech model loads, so a slow start never makes a
  shim start a second daemon (ADR-0004).
- **Event worker:** takes events from the `EventInbox` in the order their hooks
  fired, then classifies, gates and words each one and puts the sentence in the
  `PendingSlot`. It also records "heard" and checks the idle timeout. All
  session state changes happen on this one thread (ADR-0007).
- **Speech worker:** loads the speech model, then speaks pending utterances one
  at a time, dropping any more than 30 s old.

Only one daemon runs: it holds an exclusive lock on a file next to the socket
for its whole life. Decisions: ADR-0013.
"""

import contextlib
import errno
import fcntl
import json
import logging
import logging.handlers
import math
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

from cc_notify_tts import __version__, config
from cc_notify_tts.events import EventKind, classify
from cc_notify_tts.gate import MuteState, NeverMuted, decide, read_presence
from cc_notify_tts.presence import FrontmostWatcher, FrontTracker, read_idle_seconds
from cc_notify_tts.queues import EventInbox, Heard, PendingSlot, Received, Utterance
from cc_notify_tts.sessions import SessionStore, default_state_dir
from cc_notify_tts.wording import project_name, word

# Named, not __name__, which is "__main__" when run with -m.
log = logging.getLogger("cc_notify_tts.daemon")

# A message waiting longer than this since its hook fired is dropped unspoken.
STALE_SECONDS = 30.0
# How long the inbox holds each event so one that fired earlier but arrived
# later can go first. A shim takes about 35 ms p95 from start to send (ADR-0004).
HOLD_SECONDS = 0.1
# Largest request accepted. Hook payloads carry tool input, such as a file to write.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
# A client that hasn't sent its request within this long is dropped.
CONNECTION_TIMEOUT = 2.0
# A starting daemon that finds the lock held waits this long for it before
# giving up, in case the holder is exiting.
LOCK_WAIT_SECONDS = 3.0
# Decisions kept for `status`.
RECENT_DECISIONS = 20


def data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "cc-notify-tts"


def default_socket_path() -> Path:
    return data_dir() / "daemon.sock"


def default_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "cc-notify-tts" / "daemon.log"


def request(path: Path, message: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    """Send one request to the daemon and return its reply. Raises OSError or ValueError."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall(json.dumps(message).encode() + b"\n")
        with sock.makefile("rb") as f:
            line = f.readline(MAX_MESSAGE_BYTES)
    reply = json.loads(line)
    if not isinstance(reply, dict):
        raise ValueError(f"reply isn't a JSON object: {line!r}")
    return reply  # pyright: ignore[reportUnknownVariableType]


def is_answering(path: Path, timeout: float = 0.2) -> bool:
    try:
        return request(path, {"type": "ping"}, timeout).get("ok") is True
    except (OSError, ValueError):
        return False


class Speaker(Protocol):
    def speak(self, text: str, voice: str) -> bool:
        """Speak `text`; return when playback ends, with whether it played."""
        ...


@dataclass(frozen=True)
class Paths:
    socket: Path
    state: Path
    config: Path

    @property
    def lock(self) -> Path:
        return self.socket.with_name(self.socket.name + ".lock")

    @classmethod
    def default(cls) -> "Paths":
        return cls(default_socket_path(), default_state_dir(), config.default_config_path())


@dataclass(frozen=True)
class _Decided:
    at: float
    session_id: str
    project: str
    kind: EventKind
    speak: bool
    reason: str


class Daemon:
    def __init__(
        self,
        paths: Paths,
        make_speaker: Callable[[], Speaker],
        *,
        mute: MuteState | None = None,
        read_idle: Callable[[], float | None] = read_idle_seconds,
        follow_front: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.paths = paths
        self._make_speaker = make_speaker
        self._mute = mute if mute is not None else NeverMuted()
        self._read_idle = read_idle
        self._follow_front = follow_front
        self._clock = clock
        self._store = SessionStore(paths.state)
        self._inbox = EventInbox(HOLD_SECONDS)
        self._slot = PendingSlot()
        self._tracker = FrontTracker(time.monotonic())
        self._stop = threading.Event()
        self._started_at = clock()
        self._last_event_at = self._started_at
        self._speech = "loading"
        self._recent: deque[_Decided] = deque(maxlen=RECENT_DECISIONS)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> int:
        """Serve until the idle timeout or `stop()`. Returns the process exit code."""
        lock = _acquire_lock(self.paths.lock, self.paths.socket)
        if lock is None:
            log.info("another daemon is running, exiting")
            return 0
        try:
            return self._run_locked()
        finally:
            os.close(lock)

    def _run_locked(self) -> int:
        try:
            server = self._bind()
        except OSError as e:
            log.error("can't listen on %s: %s", self.paths.socket, e)
            return 1
        log.info("daemon %s listening on %s (pid %d)", __version__, self.paths.socket, os.getpid())
        threads = [
            threading.Thread(target=server.serve_forever, args=(0.2,), name="socket", daemon=True),
            threading.Thread(target=self._event_worker, name="events", daemon=True),
            threading.Thread(target=self._speech_worker, name="speech", daemon=True),
        ]
        watcher = FrontmostWatcher(self._tracker) if self._follow_front else None
        try:
            for thread in threads:
                thread.start()
            if watcher is not None:
                watcher.start()
            # A timeout keeps the main thread responsive to signals.
            while not self._stop.wait(1.0):
                pass
        finally:
            # Remove the socket before releasing the lock, so a shim that finds
            # no socket starts a daemon that can take the lock.
            server.shutdown()
            server.server_close()
            self.paths.socket.unlink(missing_ok=True)
            if watcher is not None:
                watcher.stop()
        log.info("daemon exiting")
        return 0

    def _bind(self) -> "_Server":
        self.paths.socket.parent.mkdir(parents=True, exist_ok=True)
        # We hold the lock, so any socket file here is a dead daemon's.
        self.paths.socket.unlink(missing_ok=True)
        server = _Server(str(self.paths.socket), self.handle_request)
        os.chmod(self.paths.socket, 0o600)
        return server

    # --- requests (socket threads) ------------------------------------------------

    def handle_request(self, line: bytes) -> dict[str, Any]:
        if len(line) > MAX_MESSAGE_BYTES:
            return _error("message too large")
        try:
            message: Any = json.loads(line)
        except ValueError:
            return _error("not JSON")
        if not isinstance(message, dict):
            return _error("not a JSON object")
        fields: dict[str, Any] = message  # pyright: ignore[reportUnknownVariableType]
        kind = fields.get("type")
        if kind == "ping":
            return {"ok": True, "pid": os.getpid(), "version": __version__}
        if kind == "status":
            return self.status()
        if kind == "event":
            return self._accept_event(fields)
        return _error(f"unknown type: {kind!r}")

    def _accept_event(self, message: dict[str, Any]) -> dict[str, Any]:
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return _error("event needs a payload object")
        now = self._clock()
        fired_at = message.get("fired_at")
        if not isinstance(fired_at, int | float) or not math.isfinite(fired_at):
            fired_at = now
        host_app = message.get("host_app")
        self._last_event_at = now
        self._inbox.put(
            Received(
                payload=payload,  # pyright: ignore[reportUnknownArgumentType]
                # A clock can't make an event newer than its arrival.
                fired_at=min(float(fired_at), now),
                host_app=host_app if isinstance(host_app, str) and host_app else None,
            )
        )
        return {"ok": True}

    def status(self) -> dict[str, Any]:
        now = self._clock()
        mono = time.monotonic()
        speaking, pending = self._slot.snapshot()
        sessions = [
            {
                "session_id": s.session_id,
                "project": project_name(s.cwd),
                "host_app": s.host_app or None,
                "out_of_front_seconds": (
                    self._tracker.out_of_front_seconds(s.host_app, mono) if s.host_app else None
                ),
            }
            for s in self._store.open_sessions(now)
        ]
        return {
            "ok": True,
            "pid": os.getpid(),
            "version": __version__,
            "started_at": self._started_at,
            "last_event_at": self._last_event_at,
            "idle_timeout_seconds": self._idle_timeout(),
            "speech": self._speech,
            "speaking": speaking.text if speaking else None,
            "pending": pending.text if pending else None,
            "front_app": self._tracker.front(),
            "sessions": sessions,
            "recent": [
                {
                    "at": d.at,
                    "session_id": d.session_id,
                    "project": d.project,
                    "event": str(d.kind),
                    "speak": d.speak,
                    "reason": d.reason,
                }
                for d in list(self._recent)
            ],
        }

    # --- event worker ---------------------------------------------------------------

    def _event_worker(self) -> None:
        while not self._stop.is_set():
            timeout = self._idle_timeout()
            item = self._inbox.get(timeout=min(1.0, timeout / 4))
            try:
                if isinstance(item, Received):
                    self._handle_event(item)
                elif isinstance(item, Heard):
                    self._store.mark_still_running_heard(item.session_id)
            except Exception:
                log.exception("event worker: failed on %r", item)
            if self._is_idle(timeout):
                log.info("no event for %g s, exiting", timeout)
                self._stop.set()

    def _handle_event(self, event: Received) -> None:
        note = classify(self._store, event.payload, event.fired_at, event.host_app)
        if note is None:
            return
        cfg = config.load(self.paths.config).config
        presence = read_presence(note.host_app, self._read_idle(), self._tracker, time.monotonic())
        decision = decide(note.kind, cfg, self._mute, datetime.now().time(), presence)
        project = project_name(note.cwd)
        log.info("%s, %s: %s", project, note.kind, decision.reason)
        self._recent.append(
            _Decided(
                self._clock(), note.session_id, project, note.kind, decision.speak, decision.reason
            )
        )
        if not decision.speak:
            return
        utterance = Utterance(
            text=word(note).text(),
            voice=cfg.speech.voice,
            session_id=note.session_id,
            kind=note.kind,
            fired_at=event.fired_at,
        )
        replaced = self._slot.put(utterance)
        if replaced is not None:
            log.info("replaced pending %r with a newer message", replaced.text)

    def _idle_timeout(self) -> float:
        return config.load(self.paths.config).config.daemon.idle_timeout_minutes * 60

    def _is_idle(self, timeout: float) -> bool:
        """No event for the idle timeout, and nothing left to say.

        Every event reaches the daemon, so this is the same as no session open for
        the timeout: a session ends with an event, and one without `SessionEnd`
        counts as closed once the timeout has passed since its last event.
        """
        if self._clock() - self._last_event_at < timeout or len(self._inbox):
            return False
        speaking, pending = self._slot.snapshot()
        return speaking is None and pending is None

    # --- speech worker --------------------------------------------------------------

    def _speech_worker(self) -> None:
        try:
            speaker = self._make_speaker()
        except Exception:
            log.exception("speech unavailable: the model didn't load; run `cc-notify-tts setup`")
            self._speech = "unavailable"
            speaker = None
        else:
            self._speech = "ready"
        while not self._stop.is_set():
            utterance = self._slot.take(timeout=0.5)
            if utterance is None:
                continue
            try:
                played = self._say(speaker, utterance)
            finally:
                self._slot.done()
            if played and utterance.kind is EventKind.STILL_RUNNING:
                self._inbox.put_heard(Heard(utterance.session_id))

    def _say(self, speaker: Speaker | None, utterance: Utterance) -> bool:
        age = self._clock() - utterance.fired_at
        if age > STALE_SECONDS:
            log.info("dropped %r: %d s old", utterance.text, age)
            return False
        if speaker is None:
            log.warning("not spoken, speech unavailable: %r", utterance.text)
            return False
        try:
            return speaker.speak(utterance.text, utterance.voice)
        except Exception:
            log.exception("speaking %r failed", utterance.text)
            return False


def _error(why: str) -> dict[str, Any]:
    return {"ok": False, "error": why}


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, handle: Callable[[bytes], dict[str, Any]]) -> None:
        self.handle = handle
        super().__init__(path, _Handler)


class _Handler(socketserver.StreamRequestHandler):
    timeout = CONNECTION_TIMEOUT

    def handle(self) -> None:
        assert isinstance(self.server, _Server)
        try:
            line = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
        except OSError:
            return
        if not line:
            return
        reply = self.server.handle(line)
        with contextlib.suppress(OSError):
            self.wfile.write(json.dumps(reply).encode() + b"\n")


def _acquire_lock(lock_path: Path, socket_path: Path) -> int | None:
    """Take the single-instance lock, or None if another daemon holds it."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as e:
            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                os.close(fd)
                raise
        # Held: a running daemon answers at once. One that doesn't answer may be
        # exiting, so wait a little for its lock.
        if is_answering(socket_path) or time.monotonic() >= deadline:
            os.close(fd)
            return None
        time.sleep(0.05)


def _speaker() -> Speaker:
    # Imported here, after the socket is bound: loading kokoro-onnx takes a while.
    from cc_notify_tts.speech import AfplayPlayer, KokoroSynthesizer
    from cc_notify_tts.speech import Speaker as KokoroSpeaker

    return KokoroSpeaker(KokoroSynthesizer(), AfplayPlayer())


def main() -> int:
    log_path = default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    daemon = Daemon(Paths.default(), _speaker)

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("signal %d, stopping", signum)
        daemon.stop()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
