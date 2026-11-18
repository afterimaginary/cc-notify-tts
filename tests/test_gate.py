"""The when-to-speak gate, every branch, with injected presence readings."""

from dataclasses import replace
from datetime import time
from types import MappingProxyType

import pytest

from cc_notify_tts.config import Config, Presence, QuietHours, Speak
from cc_notify_tts.events import EventKind
from cc_notify_tts.gate import (
    Decision,
    MuteState,
    NeverMuted,
    Readings,
    decide,
    read_presence,
)
from cc_notify_tts.presence import FrontTracker

TERMINAL = "com.apple.Terminal"
NOON = time(12, 0)


class Muted:
    def is_muted(self) -> bool:
        return True


class CountingMute:
    """Fails the test if mute is read when an earlier rule already decided."""

    def __init__(self) -> None:
        self.reads = 0

    def is_muted(self) -> bool:
        self.reads += 1
        return False


def with_speak(speak: Speak, config: Config | None = None) -> Config:
    config = config or Config()
    events = dict(config.events)
    events[EventKind.FINISHED] = speak
    return replace(config, events=MappingProxyType(events))


def present(idle: float | None = 3, host: str | None = TERMINAL, out: float | None = 0) -> Readings:
    return Readings(idle_seconds=idle, host_app=host, out_of_front_seconds=out)


def check(
    readings: Readings,
    config: Config | None = None,
    *,
    mute: MuteState | None = None,
    at: time = NOON,
    kind: EventKind = EventKind.FINISHED,
) -> Decision:
    return decide(
        kind,
        config or Config(),
        mute or NeverMuted(),
        at,
        readings,
    )


# --- order of the rules --------------------------------------------------------


def test_mute_wins_over_everything() -> None:
    d = check(present(idle=None, host=None, out=None), with_speak(Speak.ALWAYS), mute=Muted())
    assert d == Decision(False, "silent: muted")


def test_quiet_hours_win_over_always() -> None:
    config = with_speak(Speak.ALWAYS, Config(quiet_hours=QuietHours(time(22), time(8))))
    d = check(present(idle=None), config, at=time(23, 30))
    assert d == Decision(False, "silent: quiet hours, 22:00 to 08:00")


def test_never_wins_over_away() -> None:
    d = check(present(idle=None, host=None, out=None), with_speak(Speak.NEVER))
    assert d == Decision(False, "silent: finished is set to never")


def test_always_speaks_while_you_watch() -> None:
    d = check(present(idle=0, out=0), with_speak(Speak.ALWAYS))
    assert d == Decision(True, "speaking: finished is set to always")


def test_each_event_uses_its_own_setting() -> None:
    config = with_speak(Speak.NEVER)
    assert check(present(idle=100), config, kind=EventKind.PERMISSION).speak
    assert not check(present(idle=100), config, kind=EventKind.FINISHED).speak


# --- quiet hours ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "now", "quiet"),
    [
        (time(13), time(14), time(13), True),
        (time(13), time(14), time(13, 59), True),
        (time(13), time(14), time(14), False),
        (time(13), time(14), time(12, 59), False),
        (time(22), time(8), time(22), True),
        (time(22), time(8), time(0), True),
        (time(22), time(8), time(7, 59), True),
        (time(22), time(8), time(8), False),
        (time(22), time(8), time(21, 59), False),
    ],
)
def test_quiet_hours_window(start: time, end: time, now: time, quiet: bool) -> None:
    config = with_speak(Speak.ALWAYS, Config(quiet_hours=QuietHours(start, end)))
    assert check(present(), config, at=now).speak is not quiet


def test_no_quiet_hours_by_default() -> None:
    assert check(present(), with_speak(Speak.ALWAYS), at=time(3)).speak


# --- away ----------------------------------------------------------------------


def test_silent_while_typing_with_the_host_in_front() -> None:
    d = check(present(idle=3, out=0))
    assert d == Decision(False, "silent: you were active 3 s ago and Terminal is in front")


def test_silent_when_host_only_briefly_out_of_front() -> None:
    d = check(present(idle=3, out=4))
    assert d == Decision(
        False, "silent: you were active 3 s ago and Terminal has been out of front for only 4 s"
    )


def test_idle_long_enough_speaks_even_with_host_in_front() -> None:
    d = check(present(idle=45, out=0))
    assert d == Decision(True, "speaking: no keyboard or mouse input for 45 s")


def test_idle_exactly_at_threshold_speaks() -> None:
    assert check(present(idle=20, out=0)).speak


def test_host_out_of_front_long_enough_speaks() -> None:
    d = check(present(idle=1, out=30))
    assert d == Decision(True, "speaking: Terminal has been out of front for 30 s")


def test_out_of_front_exactly_at_threshold_speaks() -> None:
    assert check(present(idle=1, out=10)).speak


def test_thresholds_come_from_config() -> None:
    config = Config(presence=Presence(idle_seconds=60, away_seconds=120))
    assert not check(present(idle=45, out=100), config).speak
    assert check(present(idle=60, out=0), config).speak
    assert check(present(idle=1, out=120), config).speak


def test_zero_threshold_always_counts_as_away() -> None:
    d = check(present(idle=0.2, out=0), Config(presence=Presence(idle_seconds=0)))
    assert d == Decision(True, "speaking: idle_seconds is 0, so you always count as idle")
    d = check(present(idle=1, out=0), Config(presence=Presence(away_seconds=0)))
    assert d == Decision(
        True, "speaking: away_seconds is 0, so the app always counts as out of front"
    )


def test_seconds_just_under_a_threshold_never_print_as_the_threshold() -> None:
    d = check(present(idle=19.6, out=9.7))
    assert d == Decision(
        False, "silent: you were active 19 s ago and Terminal has been out of front for only 9 s"
    )


def test_unknown_app_is_named_by_bundle_id() -> None:
    d = check(present(idle=1, host="dev.example.term", out=0))
    assert d.reason == "silent: you were active 1 s ago and dev.example.term is in front"


# --- unreadable presence -------------------------------------------------------


def test_unreadable_idle_time_speaks() -> None:
    d = check(present(idle=None, out=0))
    assert d == Decision(True, "speaking: can't read keyboard and mouse idle time")


def test_unknown_host_app_speaks() -> None:
    d = check(present(idle=1, host=None, out=None))
    assert d == Decision(True, "speaking: can't tell which app the session runs in")


def test_frontmost_app_not_followed_speaks() -> None:
    d = check(present(idle=1, out=None))
    assert d == Decision(True, "speaking: can't follow the frontmost app")


def test_mute_is_read_first_and_once() -> None:
    mute = CountingMute()
    check(present(), mute=mute)
    assert mute.reads == 1


# --- read_presence -------------------------------------------------------------


def following(front: str, now: float = 0) -> FrontTracker:
    tracker = FrontTracker(started_at=0)
    tracker.set_front(front, now)
    tracker.set_following(True)
    return tracker


def test_read_presence_host_in_front() -> None:
    r = read_presence(TERMINAL, 3, following(TERMINAL), now=50)
    assert r == Readings(idle_seconds=3, host_app=TERMINAL, out_of_front_seconds=0)


def test_read_presence_host_out_of_front() -> None:
    tracker = following(TERMINAL)
    tracker.set_front("com.apple.Safari", 40)
    assert read_presence(TERMINAL, 3, tracker, now=55).out_of_front_seconds == 15


def test_read_presence_unknown_host() -> None:
    for host in (None, ""):
        r = read_presence(host, 3, following(TERMINAL), now=50)
        assert (r.host_app, r.out_of_front_seconds) == (None, None)


def test_read_presence_not_following() -> None:
    tracker = following(TERMINAL)
    tracker.set_following(False)
    assert read_presence(TERMINAL, 3, tracker, now=50).out_of_front_seconds is None
