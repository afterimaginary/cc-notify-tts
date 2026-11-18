"""The daemon: takes hook events on a Unix socket and speaks the ones the gate lets through.

Run as `python -m cc_notify_tts.daemon`; the hook shim starts it on demand
(ADR-0002). It runs these threads:

- **Socket server:** one thread per connection, one JSON request and one reply
  each (docs/specs/daemon-protocol.md). It answers pings from the moment the
  socket is bound, before the speech model loads, so a slow start never makes a
  shim start a second daemon (ADR-0004).
- **Event worker:** takes events from the `EventInbox` in the order their hooks
  fired, then classifies, gates and words each one and puts the sentence in the
  `PendingSlot`. It also records "heard", keeps the wording model warm while a
  session is open and checks the idle timeout. All session state changes happen
  on this one thread (ADR-0007).
- **Speech worker:** loads the speech model, then speaks pending utterances one
  at a time, dropping any more than 30 s old.

Each event carries a `Timeline` of when it reached each stage, logged as one
`timing` line when the event is done (ADR-0019).

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

from cc_notify_tts import __version__, config, fire
from cc_notify_tts.client import MAX_MESSAGE_BYTES, is_answering, socket_path
from cc_notify_tts.events import EventKind, Notification, classify
from cc_notify_tts.gate import Decision, MuteState, decide, read_presence
from cc_notify_tts.model_wording import MODEL_EVENTS, write_detail
from cc_notify_tts.mute import FILE_NAME as MUTE_FILE_NAME
from cc_notify_tts.mute import MuteFile
from cc_notify_tts.ollama import Client, KeepWarm, Ollama
from cc_notify_tts.presence import FrontmostWatcher, FrontTracker, read_idle_seconds
from cc_notify_tts.queues import EventInbox, Heard, PendingSlot, Received, Utterance
from cc_notify_tts.sessions import SessionStore, default_state_dir
from cc_notify_tts.timeline import Timeline
from cc_notify_tts.wording import Wording, project_name, word

# Named, not __name__, which is "__main__" when run with -m.
log = logging.getLogger("cc_notify_tts.daemon")

# A message waiting longer than this since its hook fired is dropped unspoken.
STALE_SECONDS = 30.0
# How long the inbox holds each event so one that fired earlier but arrived
# later can go first. A shim takes about 35 ms p95 from start to send (ADR-0004).
HOLD_SECONDS = 0.1
# A client that hasn't sent its request within this long is dropped.
CONNECTION_TIMEOUT = 2.0
# A starting daemon that finds the lock held waits this long for it before
# giving up, in case the holder is exiting.
LOCK_WAIT_SECONDS = 3.0
# Decisions kept for `status`.
RECENT_DECISIONS = 20


def default_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "cc-notify-tts" / "daemon.log"


class Speaker(Protocol):
    def speak(self, text: str, voice: str, timeline: Timeline) -> bool:
        """Speak `text`; return when playback ends, with whether it played.

        Marks `synthesized` and `playing` on `timeline` as it gets there.
        """
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
        return cls(socket_path(), default_state_dir(), config.default_config_path())


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
        front: FrontTracker | None = None,
        clock: Callable[[], float] = time.time,
        ollama: Client | None = None,
    ) -> None:
        """`ollama` is None for template wording only, whatever the config says.

        `mute` defaults to the mute file in the state folder. `front` is the
        frontmost-app state the gate reads, fed by `lsappinfo` when `follow_front`
        is set; tests pass their own with `follow_front=False` and feed it.
        """
        self.paths = paths
        self._make_speaker = make_speaker
        self._mute = mute if mute is not None else MuteFile(paths.state / MUTE_FILE_NAME)
        self._read_idle = read_idle
        self._follow_front = follow_front
        self._clock = clock
        self._ollama = ollama
        self._keep_warm = KeepWarm(ollama) if ollama is not None else None
        self._store = SessionStore(paths.state)
        self._inbox = EventInbox(HOLD_SECONDS)
        self._slot = PendingSlot()
        self._tracker = front if front is not None else FrontTracker(time.monotonic())
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
            # Let the event worker finish an event, so nothing loads the model
            # again after it's unloaded below.
            threads[1].join(timeout=3.0)
        finally:
            # Remove the socket before releasing the lock, so a shim that finds
            # no socket starts a daemon that can take the lock.
            server.shutdown()
            server.server_close()
            self.paths.socket.unlink(missing_ok=True)
            if watcher is not None:
                watcher.stop()
            if self._keep_warm is not None:
                self._keep_warm.close()
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
        if kind == "fire":
            return self._fire(fields)
        return _error(f"unknown type: {kind!r}")

    def _accept_event(self, message: dict[str, Any]) -> dict[str, Any]:
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return _error("event needs a payload object")
        fields: dict[str, Any] = payload  # pyright: ignore[reportUnknownVariableType]
        now = self._clock()
        # A clock can't make an event newer than its arrival.
        fired_at = min(_seconds(message.get("fired_at"), now), now)
        hook, session_id = fields.get("hook_event_name"), fields.get("session_id")
        timeline = Timeline(
            hook if isinstance(hook, str) else "?",
            session_id if isinstance(session_id, str) else "",
        )
        timeline.mark("hook", fired_at)
        if "sent_at" in message:
            timeline.mark("sent", min(_seconds(message["sent_at"], now), now))
        timeline.mark("received", now)
        host_app = message.get("host_app")
        self._last_event_at = now
        self._inbox.put(
            Received(
                payload=fields,
                fired_at=fired_at,
                host_app=host_app if isinstance(host_app, str) and host_app else None,
                timeline=timeline,
            )
        )
        return {"ok": True}

    def _fire(self, message: dict[str, Any]) -> dict[str, Any]:
        """A synthetic notification for `cc-notify-tts fire`, gated, worded and queued.

        It runs on the socket thread and never touches session state, so it
        doesn't wait behind held events. The reply comes once it's queued (or
        silenced), not once it's spoken.
        """
        try:
            kind = EventKind(message.get("event"))
        except ValueError:
            return _error(f"unknown event: {message.get('event')!r}")
        cwd = message.get("cwd")
        host_app = message.get("host_app")
        note = fire.notification(
            kind,
            cwd if isinstance(cwd, str) else "",
            host_app if isinstance(host_app, str) else "",
        )
        timeline = Timeline("fire", "fire")
        timeline.mark("received")
        cfg = config.load(self.paths.config).config
        if message.get("always") is True:
            decision = Decision(True, "speaking: fired with --always")
        else:
            decision = self._decide(note, cfg)
        text = self._speak_if_allowed(note, cfg, decision, self._clock(), timeline)
        return {
            "ok": True,
            "speak": decision.speak,
            "reason": decision.reason,
            "text": text,
            "speech": self._speech,
        }

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
                if isinstance(item, Received) or (
                    self._keep_warm is not None and self._keep_warm.ping_due()
                ):
                    self._update_keep_warm()
            except Exception:
                log.exception("event worker: failed on %r", item)
            if self._is_idle(timeout):
                log.info("no event for %g s, exiting", timeout)
                self._stop.set()

    def _handle_event(self, event: Received) -> None:
        event.timeline.mark("dequeued")
        note = classify(self._store, event.payload, event.fired_at, event.host_app)
        if note is None:
            event.timeline.finish("no notification")
            return
        cfg = config.load(self.paths.config).config
        decision = self._decide(note, cfg)
        self._speak_if_allowed(note, cfg, decision, event.fired_at, event.timeline)

    def _decide(self, note: Notification, cfg: config.Config) -> Decision:
        presence = read_presence(note.host_app, self._read_idle(), self._tracker, time.monotonic())
        return decide(note.kind, cfg, self._mute, datetime.now().time(), presence)

    def _speak_if_allowed(
        self,
        note: Notification,
        cfg: config.Config,
        decision: Decision,
        fired_at: float,
        timeline: Timeline,
    ) -> str | None:
        """Record the decision and, if it speaks, word the notification and queue it.

        Returns the sentence queued, or None when the decision was silent.
        """
        timeline.kind = str(note.kind)
        timeline.mark("gated")
        project = project_name(note.cwd)
        log.info("%s, %s: %s", project, note.kind, decision.reason)
        self._recent.append(
            _Decided(
                self._clock(), note.session_id, project, note.kind, decision.speak, decision.reason
            )
        )
        if not decision.speak:
            timeline.finish("silenced")
            return None
        wording = word(note)
        utterance = Utterance(
            text=wording.text(self._model_detail(note, wording, cfg.wording)),
            voice=cfg.speech.voice,
            session_id=note.session_id,
            kind=note.kind,
            fired_at=fired_at,
            timeline=timeline,
        )
        timeline.mark("worded")
        replaced = self._slot.put(utterance)
        if replaced is not None:
            log.info("replaced pending %r with a newer message", replaced.text)
            replaced.timeline.finish("replaced")
        return utterance.text

    def _model_detail(
        self, note: Notification, wording: Wording, cfg: config.Wording
    ) -> str | None:
        """A model-written detail, or None to keep the template's."""
        if self._ollama is None or not cfg.ollama or note.kind not in MODEL_EVENTS:
            return None
        # Load a cold model for next time only while a session is open, as
        # `_update_keep_warm` would; a `fire` with none open mustn't leave it loaded.
        open_session = bool(self._store.open_sessions(self._clock()))
        keep_warm = self._keep_warm if open_session else None
        attempt = write_detail(
            note, wording, self._ollama, cfg.model, cfg.timeout_seconds, keep_warm
        )
        if attempt.detail is None:
            log.info("template wording: %s", attempt.reason)
        return attempt.detail

    def _update_keep_warm(self) -> None:
        """Keep the model warm while wording is on and a session is open; otherwise unload it."""
        if self._keep_warm is None:
            return
        cfg = config.load(self.paths.config).config.wording
        wanted = cfg.ollama and bool(self._store.open_sessions(self._clock()))
        self._keep_warm.update(cfg.model if wanted else None)

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
        timeline = utterance.timeline
        timeline.mark("synthesizing")
        age = self._clock() - utterance.fired_at
        if age > STALE_SECONDS:
            log.info("dropped %r: %d s old", utterance.text, age)
            timeline.finish("dropped as stale")
            return False
        if speaker is None:
            log.warning("not spoken, speech unavailable: %r", utterance.text)
            timeline.finish("speech unavailable")
            return False
        try:
            played = speaker.speak(utterance.text, utterance.voice, timeline)
        except Exception:
            log.exception("speaking %r failed", utterance.text)
            timeline.finish("speech failed")
            return False
        timeline.finish("played" if played else "playback failed")
        return played


def _error(why: str) -> dict[str, Any]:
    return {"ok": False, "error": why}


def _seconds(value: Any, default: float) -> float:
    """A finite number of seconds from a request, or `default`."""
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return default


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
    daemon = Daemon(Paths.default(), _speaker, ollama=Ollama())

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("signal %d, stopping", signum)
        daemon.stop()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
