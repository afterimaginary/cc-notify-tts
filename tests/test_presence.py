"""Presence readers: the frontmost tracker, the listener's output and the watcher."""

import sys
import time
from collections.abc import Callable

import pytest

from cc_notify_tts import presence
from cc_notify_tts.presence import (
    UNNAMED_APP,
    FrontmostWatcher,
    FrontTracker,
    app_name,
    parse_listen_line,
)

TERMINAL = "com.apple.Terminal"
SAFARI = "com.apple.Safari"
MAIL = "com.apple.mail"
GHOSTTY = "com.mitchellh.ghostty"


def following(front: str, started_at: float = 0) -> FrontTracker:
    tracker = FrontTracker(started_at=started_at)
    tracker.set_front(front, started_at)
    tracker.set_following(True)
    return tracker


# --- FrontTracker --------------------------------------------------------------


def test_host_in_front_is_zero() -> None:
    assert following(TERMINAL).out_of_front_seconds(TERMINAL, 100) == 0


def test_out_of_front_counts_from_when_it_lost_front() -> None:
    tracker = following(TERMINAL)
    tracker.set_front(SAFARI, 40)
    assert tracker.out_of_front_seconds(TERMINAL, 55) == 15


def test_switching_between_other_apps_keeps_the_hosts_clock() -> None:
    tracker = following(TERMINAL)
    tracker.set_front(SAFARI, 40)
    tracker.set_front(MAIL, 50)
    tracker.set_front(SAFARI, 52)
    assert tracker.out_of_front_seconds(TERMINAL, 60) == 20


def test_coming_back_to_front_resets_the_clock() -> None:
    tracker = following(TERMINAL)
    tracker.set_front(SAFARI, 40)
    tracker.set_front(TERMINAL, 50)
    assert tracker.out_of_front_seconds(TERMINAL, 52) == 0
    tracker.set_front(SAFARI, 55)
    assert tracker.out_of_front_seconds(TERMINAL, 58) == 3


def test_each_host_has_its_own_clock() -> None:
    tracker = following(TERMINAL)
    tracker.set_front(GHOSTTY, 30)
    tracker.set_front(SAFARI, 40)
    assert tracker.out_of_front_seconds(TERMINAL, 50) == 20
    assert tracker.out_of_front_seconds(GHOSTTY, 50) == 10


def test_host_never_seen_in_front_counts_from_the_start() -> None:
    tracker = following(SAFARI, started_at=10)
    assert tracker.out_of_front_seconds(TERMINAL, 25) == 15


def test_setting_the_same_front_again_changes_nothing() -> None:
    tracker = following(TERMINAL)
    tracker.set_front(SAFARI, 40)
    tracker.set_front(SAFARI, 50)
    assert tracker.out_of_front_seconds(TERMINAL, 60) == 20


def test_not_following_is_unknown() -> None:
    tracker = following(TERMINAL)
    tracker.set_following(False)
    assert tracker.out_of_front_seconds(TERMINAL, 10) is None
    assert tracker.front() is None


def test_front_never_known_is_unknown() -> None:
    tracker = FrontTracker(started_at=0)
    tracker.set_following(True)
    assert tracker.out_of_front_seconds(TERMINAL, 10) is None


# --- names and parsing ---------------------------------------------------------


def test_app_names() -> None:
    assert app_name(TERMINAL) == "Terminal"
    assert app_name("dev.example.term") == "dev.example.term"
    assert app_name(UNNAMED_APP) == "an unnamed app"


SWITCH = (
    "Notification: kLSNotifyBecameFrontmost time=11/18/2026 15:01:02.345 "
    'dataRef={ "ApplicationType"="Foreground", "CFBundleIdentifier"="com.apple.mail", '
    '"LSDisplayName"="Mail" } context=0x0'
)


def test_parse_a_switch() -> None:
    assert parse_listen_line(SWITCH) == MAIL


def test_parse_a_switch_without_bundle_id() -> None:
    line = 'Notification: kLSNotifyBecameFrontmost time=+1.5s dataRef={ "LSDisplayName"="x" }'
    assert parse_listen_line(line) == UNNAMED_APP


@pytest.mark.parametrize("line", ["", "Listening for notifications...", "garbage"])
def test_parse_ignores_other_lines(line: str) -> None:
    assert parse_listen_line(line) is None


# --- FrontmostWatcher ----------------------------------------------------------


def fake_listener(lines: list[str], then_sleep: float) -> list[str]:
    """A command that prints `lines` like `lsappinfo listen`, then sleeps."""
    script = (
        "import sys, time\n"
        f"for line in {lines!r}:\n"
        "    print(line, flush=True)\n"
        f"time.sleep({then_sleep})\n"
    )
    return [sys.executable, "-c", script]


def wait_for(condition: Callable[[], bool], seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_watcher_reads_front_then_follows_switches() -> None:
    tracker = FrontTracker(started_at=time.monotonic())
    watcher = FrontmostWatcher(
        tracker,
        read_front=lambda: TERMINAL,
        command=fake_listener([SWITCH], then_sleep=30),
    )
    watcher.start()
    try:
        assert wait_for(lambda: tracker.front() == MAIL)
        out = tracker.out_of_front_seconds(TERMINAL, time.monotonic())
        assert out is not None and out >= 0
    finally:
        watcher.stop()
    assert tracker.front() is None


def test_watcher_follows_once_a_switch_arrives_even_if_the_first_read_failed() -> None:
    tracker = FrontTracker(started_at=time.monotonic())
    watcher = FrontmostWatcher(
        tracker, read_front=lambda: None, command=fake_listener([SWITCH], then_sleep=30)
    )
    watcher.start()
    try:
        assert wait_for(lambda: tracker.front() == MAIL)
    finally:
        watcher.stop()


def test_watcher_is_not_following_after_the_listener_dies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(presence, "MAX_RETRY_SECONDS", 30.0)
    reads: list[int] = []

    def read_front() -> str:
        reads.append(1)
        return TERMINAL

    tracker = FrontTracker(started_at=time.monotonic())
    watcher = FrontmostWatcher(
        tracker, read_front=read_front, command=fake_listener([], then_sleep=0)
    )
    watcher.start()
    try:
        assert wait_for(lambda: len(reads) >= 1)
        # The listener exits at once, so the watcher stops following and backs off.
        assert wait_for(lambda: tracker.front() is None)
    finally:
        watcher.stop()


def test_watcher_without_lsappinfo_is_not_following() -> None:
    tracker = FrontTracker(started_at=time.monotonic())
    watcher = FrontmostWatcher(
        tracker, read_front=lambda: TERMINAL, command=["/nonexistent/lsappinfo"]
    )
    watcher.start()
    try:
        time.sleep(0.2)
        assert tracker.front() is None
    finally:
        watcher.stop()


# --- real readers --------------------------------------------------------------


@pytest.mark.integration
def test_idle_time_reads_on_this_mac() -> None:
    idle = presence.read_idle_seconds()
    assert idle is not None and idle >= 0


@pytest.mark.integration
def test_front_app_reads_on_this_mac() -> None:
    assert presence.read_front_app() is not None
