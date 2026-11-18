"""The daemon's event inbox (ordering by hook time) and its single pending slot."""

import threading
import time

from cc_notify_tts.events import EventKind
from cc_notify_tts.queues import EventInbox, Heard, PendingSlot, Received, Utterance
from cc_notify_tts.timeline import Timeline

HOLD = 0.05


def event(name: str, fired_at: float) -> Received:
    return Received({"name": name}, fired_at, None, Timeline("Stop", "s1"))


def names(inbox: EventInbox, count: int) -> list[str]:
    out: list[str] = []
    for _ in range(count):
        item = inbox.get(timeout=1.0)
        assert isinstance(item, Received)
        out.append(item.payload["name"])
    return out


def test_events_come_out_in_the_order_their_hooks_fired() -> None:
    inbox = EventInbox(HOLD)
    inbox.put(event("stop", 3.0))
    inbox.put(event("prompt", 2.0))
    inbox.put(event("start", 1.0))
    assert names(inbox, 3) == ["start", "prompt", "stop"]


def test_events_are_held_for_the_window() -> None:
    inbox = EventInbox(HOLD)
    start = time.monotonic()
    inbox.put(event("stop", 1.0))
    assert inbox.get(timeout=1.0) is not None
    assert time.monotonic() - start >= HOLD


def test_an_event_arriving_after_the_window_is_not_reordered() -> None:
    inbox = EventInbox(HOLD)
    inbox.put(event("stop", 2.0))
    assert names(inbox, 1) == ["stop"]
    inbox.put(event("prompt", 1.0))
    assert names(inbox, 1) == ["prompt"]


def test_heard_goes_before_held_events() -> None:
    inbox = EventInbox(HOLD)
    inbox.put(event("stop", 1.0))
    inbox.put_heard(Heard("s1"))
    assert inbox.get(timeout=1.0) == Heard("s1")
    assert len(inbox) == 1


def test_get_returns_none_after_timeout() -> None:
    assert EventInbox(HOLD).get(timeout=0.01) is None


def test_get_wakes_for_an_event_put_while_waiting() -> None:
    inbox = EventInbox(HOLD)
    threading.Timer(0.02, lambda: inbox.put(event("late", 1.0))).start()
    item = inbox.get(timeout=2.0)
    assert isinstance(item, Received)
    assert item.payload["name"] == "late"


def utterance(text: str) -> Utterance:
    return Utterance(text, "af_heart", "s1", EventKind.FINISHED, 0.0, Timeline("Stop", "s1"))


def test_newer_utterance_replaces_the_pending_one() -> None:
    slot = PendingSlot()
    assert slot.put(utterance("one")) is None
    assert slot.put(utterance("two")) == utterance("one")
    assert slot.take(timeout=0.1) == utterance("two")
    assert slot.take(timeout=0.01) is None


def test_snapshot_shows_speaking_and_pending() -> None:
    slot = PendingSlot()
    slot.put(utterance("one"))
    slot.take(timeout=0.1)
    slot.put(utterance("two"))
    assert slot.snapshot() == (utterance("one"), utterance("two"))
    slot.done()
    assert slot.snapshot() == (None, utterance("two"))
