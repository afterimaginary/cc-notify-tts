"""The user's config file.

One TOML file with every default in code. `load` is called on every event, so an
edit takes effect on the next one. A bad file never stops a notification: a key
that's unknown or invalid is reported and its default used, and a file that
can't be read or parsed means all defaults. Keys: docs/specs/config.md.

Standard library only, so the hook shim can read it too.
"""

import logging
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cc_notify_tts.events import EventKind

log = logging.getLogger(__name__)


class Speak(StrEnum):
    AWAY = "away"
    ALWAYS = "always"
    NEVER = "never"


def _all_away() -> Mapping[EventKind, Speak]:
    return MappingProxyType(dict.fromkeys(EventKind, Speak.AWAY))


@dataclass(frozen=True)
class QuietHours:
    """A daily window, local time. `end` before `start` crosses midnight."""

    start: time
    end: time


@dataclass(frozen=True)
class Presence:
    # No keyboard or mouse input for this long counts as away.
    idle_seconds: float = 20.0
    # The session's host app out of front for this long counts as away.
    away_seconds: float = 10.0


@dataclass(frozen=True)
class Daemon:
    # The daemon exits after this long with no event.
    idle_timeout_minutes: float = 120.0


@dataclass(frozen=True)
class Wording:
    ollama: bool = True
    # A placeholder until the model comparison picks the default.
    model: str = "llama3.2:1b"
    timeout_seconds: float = 1.0


@dataclass(frozen=True)
class Speech:
    voice: str = "af_heart"


@dataclass(frozen=True)
class Config:
    events: Mapping[EventKind, Speak] = field(default_factory=_all_away)
    quiet_hours: QuietHours | None = None
    presence: Presence = field(default_factory=Presence)
    daemon: Daemon = field(default_factory=Daemon)
    wording: Wording = field(default_factory=Wording)
    speech: Speech = field(default_factory=Speech)


@dataclass(frozen=True)
class Loaded:
    config: Config
    # One line per unknown key or invalid value, for `doctor`. Empty when the
    # file is missing or clean.
    problems: tuple[str, ...]


def default_config_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "cc-notify-tts" / "config.toml"


def load(path: Path | None = None) -> Loaded:
    path = default_config_path() if path is None else path
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        loaded = Loaded(Config(), ())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        loaded = Loaded(Config(), (f"{path}: can't read it, using all defaults: {e}",))
    else:
        loaded = _parse(data)
    _log_once(loaded.problems)
    return loaded


# Problems last logged, so a bad file is logged once, not on every event.
_logged: tuple[str, ...] = ()


def _log_once(problems: tuple[str, ...]) -> None:
    global _logged
    if problems != _logged:
        for problem in problems:
            log.warning("config: %s", problem)
        _logged = problems


def _parse(data: Mapping[str, Any]) -> Loaded:
    p = _Problems()
    tables = {
        "events",
        "quiet_hours",
        "presence",
        "daemon",
        "wording",
        "speech",
    }
    for key in data:
        if key not in tables:
            p.unknown(key)

    events_table = p.table(data, "events")
    events = dict(_all_away())
    for name in events_table:
        try:
            kind = EventKind(name)
        except ValueError:
            p.unknown(f"events.{name}")
            continue
        table = p.table(events_table, name, f"events.{name}")
        p.no_other_keys(table, {"speak"}, f"events.{name}")
        if "speak" in table:
            events[kind] = p.speak(table["speak"], f"events.{name}.speak")

    quiet = p.table(data, "quiet_hours")
    p.no_other_keys(quiet, {"start", "end"}, "quiet_hours")
    quiet_hours = _quiet_hours(p, quiet)

    presence = p.table(data, "presence")
    p.no_other_keys(presence, {"idle_seconds", "away_seconds"}, "presence")
    daemon = p.table(data, "daemon")
    p.no_other_keys(daemon, {"idle_timeout_minutes"}, "daemon")
    wording = p.table(data, "wording")
    p.no_other_keys(wording, {"ollama", "model", "timeout_seconds"}, "wording")
    speech = p.table(data, "speech")
    p.no_other_keys(speech, {"voice"}, "speech")

    d = Config()
    config = Config(
        events=MappingProxyType(events),
        quiet_hours=quiet_hours,
        presence=Presence(
            idle_seconds=p.seconds(presence, "idle_seconds", d.presence.idle_seconds, "presence"),
            away_seconds=p.seconds(presence, "away_seconds", d.presence.away_seconds, "presence"),
        ),
        daemon=Daemon(
            idle_timeout_minutes=p.positive(
                daemon, "idle_timeout_minutes", d.daemon.idle_timeout_minutes, "daemon"
            ),
        ),
        wording=Wording(
            ollama=p.boolean(wording, "ollama", d.wording.ollama, "wording"),
            model=p.text(wording, "model", d.wording.model, "wording"),
            timeout_seconds=p.positive(
                wording, "timeout_seconds", d.wording.timeout_seconds, "wording"
            ),
        ),
        speech=Speech(voice=p.text(speech, "voice", d.speech.voice, "speech")),
    )
    return Loaded(config, tuple(p.lines))


def _quiet_hours(p: "_Problems", table: Mapping[str, Any]) -> QuietHours | None:
    if "start" not in table and "end" not in table:
        return None
    if "start" not in table or "end" not in table:
        p.invalid("quiet_hours", "needs both start and end; quiet hours are off")
        return None
    start = p.clock(table["start"], "quiet_hours.start")
    end = p.clock(table["end"], "quiet_hours.end")
    if start is None or end is None:
        return None
    if start == end:
        p.invalid("quiet_hours", "start and end are the same; quiet hours are off")
        return None
    return QuietHours(start, end)


class _Problems:
    """Collects problems while reading values, returning the default for any bad one."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def unknown(self, key: str) -> None:
        self.lines.append(f"{key}: unknown key, ignored")

    def invalid(self, key: str, why: str) -> None:
        self.lines.append(f"{key}: {why}")

    def table(
        self, parent: Mapping[str, Any], key: str, name: str | None = None
    ) -> Mapping[str, Any]:
        value = parent.get(key, {})
        if not isinstance(value, dict):
            self.invalid(name or key, "must be a table, using its defaults")
            return {}
        return value  # pyright: ignore[reportUnknownVariableType]

    def no_other_keys(self, table: Mapping[str, Any], known: set[str], name: str) -> None:
        for key in table:
            if key not in known:
                self.unknown(f"{name}.{key}")

    def speak(self, value: Any, name: str) -> Speak:
        try:
            return Speak(value)
        except ValueError:
            choices = ", ".join(f'"{s}"' for s in Speak)
            self.invalid(name, f'{value!r} isn\'t one of {choices}, using "away"')
            return Speak.AWAY

    def clock(self, value: Any, name: str) -> time | None:
        if isinstance(value, time):
            return value
        if isinstance(value, str):
            try:
                hours, minutes = value.split(":")
                if len(minutes) == 2:
                    return time(int(hours), int(minutes))
            except ValueError:
                pass
        self.invalid(name, f'{value!r} isn\'t a time like "22:00"; quiet hours are off')
        return None

    def seconds(self, table: Mapping[str, Any], key: str, default: float, name: str) -> float:
        return self._number(table, key, default, name, allow_zero=True)

    def positive(self, table: Mapping[str, Any], key: str, default: float, name: str) -> float:
        return self._number(table, key, default, name, allow_zero=False)

    def _number(
        self,
        table: Mapping[str, Any],
        key: str,
        default: float,
        name: str,
        *,
        allow_zero: bool,
    ) -> float:
        if key not in table:
            return default
        value = table[key]
        ok = (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and (value >= 0 if allow_zero else value > 0)
        )
        if not ok:
            want = "a number, 0 or more" if allow_zero else "a number above 0"
            self.invalid(f"{name}.{key}", f"{value!r} isn't {want}, using {default:g}")
            return default
        return float(value)

    def boolean(self, table: Mapping[str, Any], key: str, default: bool, name: str) -> bool:
        if key not in table:
            return default
        value = table[key]
        if not isinstance(value, bool):
            self.invalid(f"{name}.{key}", f"{value!r} isn't true or false, using {default}")
            return default
        return value

    def text(self, table: Mapping[str, Any], key: str, default: str, name: str) -> str:
        if key not in table:
            return default
        value = table[key]
        if not isinstance(value, str) or not value.strip():
            self.invalid(f"{name}.{key}", f'{value!r} isn\'t a name, using "{default}"')
            return default
        return value
