"""The daemon's two queues: events in the order their hooks fired, and one pending utterance.

`EventInbox` feeds the event worker. Hooks run async in separate processes, so
their messages can reach the socket out of order. The inbox holds each event
for a short window after it arrives and hands events out by the time their hook
fired. It also carries "heard" records from the speech worker, so the event
worker applies every state change on one queue (ADR-0007).

`PendingSlot` sits between the event worker and the speech worker: one
utterance plays at a time, and at most one waits. A newer one replaces it.
See ADR-0013.
"""

import heapq
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cc_notify_tts.events import EventKind
from cc_notify_tts.timeline import Timeline


@dataclass(frozen=True)
class Received:
    """A hook event as it came in on the socket."""

    payload: dict[str, Any]
    # Wall-clock time the hook fired, from the shim, or arrival time if it sent none.
    fired_at: float
    # The host app the shim named: a bundle id, "" when it found none (so it's
    # unknown now), or None when it didn't look (ADR-0030).
    host_app: str | None
    # Its stage marks, logged when it's done (ADR-0019).
    timeline: Timeline = field(compare=False)


@dataclass(frozen=True)
class Heard:
    """A "still running" or "finished" announcement for this session has ended.

    `played` is False when it was dropped without playing: replaced, stale or failed.
    """

    session_id: str
    played: bool = True


@dataclass(order=True)
class _Held:
    fired_at: float
    seq: int
    due: float = field(compare=False)
    event: Received = field(compare=False)


class EventInbox:
    def __init__(self, hold_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._hold = hold_seconds
        self._clock = clock
        self._held: list[_Held] = []
        self._heard: deque[Heard] = deque()
        self._seq = 0
        self._cond = threading.Condition()

    def put(self, event: Received) -> None:
        with self._cond:
            self._seq += 1
            due = self._clock() + self._hold
            heapq.heappush(self._held, _Held(event.fired_at, self._seq, due, event))
            self._cond.notify()

    def put_heard(self, heard: Heard) -> None:
        with self._cond:
            self._heard.append(heard)
            self._cond.notify()

    def get(self, timeout: float) -> Received | Heard | None:
        """The next item, or None after `timeout` seconds with none due.

        "Heard" records come first: playback finished in real time, before any
        event still held. Events come out earliest-fired first, each once it has
        been held for the window, so a later-fired event that arrived first waits
        for an earlier-fired one that arrived within the window.
        """
        deadline = self._clock() + timeout
        with self._cond:
            while True:
                if self._heard:
                    return self._heard.popleft()
                now = self._clock()
                wait = deadline - now
                if self._held:
                    head = self._held[0]
                    if head.due <= now:
                        return heapq.heappop(self._held).event
                    wait = min(wait, head.due - now)
                if wait <= 0:
                    return None
                self._cond.wait(wait)

    def __len__(self) -> int:
        with self._cond:
            return len(self._held) + len(self._heard)


@dataclass(frozen=True)
class Utterance:
    text: str
    voice: str
    session_id: str
    kind: EventKind
    # Wall-clock time the hook behind it fired, for dropping stale messages.
    fired_at: float
    timeline: Timeline = field(compare=False)


class PendingSlot:
    def __init__(self) -> None:
        self._pending: Utterance | None = None
        self._speaking: Utterance | None = None
        self._cond = threading.Condition()

    def put(self, utterance: Utterance) -> Utterance | None:
        """Make `utterance` the pending one. Returns the one it replaced, if any."""
        with self._cond:
            replaced, self._pending = self._pending, utterance
            self._cond.notify()
            return replaced

    def take(self, timeout: float) -> Utterance | None:
        """Wait up to `timeout` seconds for the pending utterance and mark it speaking."""
        with self._cond:
            if self._pending is None:
                self._cond.wait_for(lambda: self._pending is not None, timeout)
            utterance, self._pending = self._pending, None
            self._speaking = utterance
            return utterance

    def done(self) -> None:
        """The utterance taken last has finished or was dropped."""
        with self._cond:
            self._speaking = None

    def snapshot(self) -> tuple[Utterance | None, Utterance | None]:
        """(speaking, pending)."""
        with self._cond:
            return self._speaking, self._pending
