"""The when-to-speak gate: speak or stay silent, and why.

`decide` applies, in order: mute, quiet hours, the event's `never` or `always`,
then `away`. `away` speaks when there's been no input for `idle_seconds` or the
session's host app has been out of front for `away_seconds`. Being in front
never silences by itself, and a presence reading that's missing speaks.
See docs/designs/when-to-speak.md.
"""

import math
from dataclasses import dataclass
from datetime import time
from typing import Protocol

from cc_notify_tts.config import Config, QuietHours, Speak
from cc_notify_tts.events import EventKind
from cc_notify_tts.presence import FrontTracker, app_name


class MuteState(Protocol):
    """Whether the user has muted notifications. `mute.MuteFile` is the real one."""

    def is_muted(self) -> bool: ...


class NeverMuted:
    """For callers that don't track mute, such as tests."""

    def is_muted(self) -> bool:
        return False


@dataclass(frozen=True)
class Readings:
    """Presence at the moment of an event. None means it couldn't be read."""

    idle_seconds: float | None
    # The session's host app (bundle id), or None if it's unknown.
    host_app: str | None
    # How long the host app has been out of front: 0 while it's in front. None
    # when the host is unknown or the frontmost app can't be followed.
    out_of_front_seconds: float | None


def read_presence(
    host_app: str | None, idle_seconds: float | None, tracker: FrontTracker, now: float
) -> Readings:
    """Combine an idle reading and the tracker's state for one session's host app."""
    host = host_app or None
    out = None if host is None else tracker.out_of_front_seconds(host, now)
    return Readings(idle_seconds=idle_seconds, host_app=host, out_of_front_seconds=out)


@dataclass(frozen=True)
class Decision:
    speak: bool
    # One line a person reads, starting "speaking: " or "silent: ".
    reason: str


def decide(
    kind: EventKind,
    config: Config,
    mute: MuteState,
    local_time: time,
    presence: Readings,
) -> Decision:
    if mute.is_muted():
        return _silent("muted")
    quiet = config.quiet_hours
    if quiet is not None and _in_quiet_hours(quiet, local_time):
        return _silent(f"quiet hours, {_clock(quiet.start)} to {_clock(quiet.end)}")
    speak = config.events[kind]
    if speak is Speak.NEVER:
        return _silent(f"{kind} is set to never")
    if speak is Speak.ALWAYS:
        return _speak(f"{kind} is set to always")
    return _away(config, presence)


def _away(config: Config, presence: Readings) -> Decision:
    idle = presence.idle_seconds
    if idle is None:
        return _speak("can't read keyboard and mouse idle time")
    if config.presence.idle_seconds == 0:
        return _speak("idle_seconds is 0, so you always count as idle")
    if idle >= config.presence.idle_seconds:
        return _speak(f"no keyboard or mouse input for {_seconds(idle)}")
    host = presence.host_app
    if host is None:
        return _speak("can't tell which app the session runs in")
    out = presence.out_of_front_seconds
    name = app_name(host)
    if out is None:
        return _speak("can't follow the frontmost app")
    if config.presence.away_seconds == 0:
        return _speak("away_seconds is 0, so the app always counts as out of front")
    if out >= config.presence.away_seconds:
        return _speak(f"{name} has been out of front for {_seconds(out)}")
    active = f"you were active {_seconds(idle)} ago"
    if out == 0:
        return _silent(f"{active} and {name} is in front")
    return _silent(f"{active} and {name} has been out of front for only {_seconds(out)}")


def _in_quiet_hours(quiet: QuietHours, now: time) -> bool:
    if quiet.start < quiet.end:
        return quiet.start <= now < quiet.end
    # Crosses midnight.
    return now >= quiet.start or now < quiet.end


def _speak(why: str) -> Decision:
    return Decision(True, f"speaking: {why}")


def _silent(why: str) -> Decision:
    return Decision(False, f"silent: {why}")


def _seconds(value: float) -> str:
    # Rounded down, so a reading just under a threshold never prints as the threshold.
    return f"{math.floor(value)} s"


def _clock(value: time) -> str:
    return value.strftime("%H:%M")
