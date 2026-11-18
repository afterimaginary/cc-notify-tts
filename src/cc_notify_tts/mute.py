"""Mute: `cc-notify-tts mute` silences every notification until `unmute` or a set time.

Mute is CLI state, not config: a small JSON file in the state folder, written
by the CLI and read by the daemon's gate for every notification, so it survives
a daemon restart and needs no request to the daemon. A mute with an end time
expires on its own: once the time has passed it reads as not muted. A file that
can't be read also reads as not muted, since the gate speaks when unsure.

The file isn't named `*.json`, because the state folder's `*.json` files are
sessions (sessions.py) and an unrecognised one is pruned.
"""

import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cc_notify_tts.sessions import default_state_dir

log = logging.getLogger(__name__)

FILE_NAME = "mute"

_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def default_mute_path() -> Path:
    return default_state_dir() / FILE_NAME


def parse_duration(text: str) -> float:
    """Seconds in a duration like `30m`, `2h`, `90s` or `1h30m`. Raises ValueError."""
    match = _DURATION.fullmatch(text.strip().lower())
    if match is None or not any(match.groups()):
        raise ValueError(f"{text!r} isn't a duration like 30m, 2h or 1h30m")
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise ValueError(f"{text!r} is no time at all")
    return float(total)


@dataclass(frozen=True)
class Muted:
    # Wall-clock time the mute ends, or None for "until unmute".
    until: float | None


class MuteFile:
    """The mute file. Satisfies the gate's `MuteState`."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = default_mute_path() if path is None else path
        self._clock = clock

    def is_muted(self) -> bool:
        return self.current() is not None

    def current(self) -> Muted | None:
        """The mute in force now, or None."""
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            log.warning("mute: can't read %s, treating as not muted: %s", self.path, e)
            return None
        fields: dict[str, Any] = raw if isinstance(raw, dict) else {}  # pyright: ignore[reportUnknownVariableType]
        until = fields.get("until", "missing")
        if until is None:
            return Muted(None)
        if not isinstance(until, int | float) or isinstance(until, bool):
            log.warning("mute: %s has no valid end time, treating as not muted", self.path)
            return None
        if until <= self._clock():
            return None
        return Muted(float(until))

    def mute(self, seconds: float | None) -> Muted:
        """Mute for `seconds`, or until `unmute` when None. Replaces any earlier mute."""
        muted = Muted(None if seconds is None else self._clock() + seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".mute.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"until": muted.until}, f)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return muted

    def unmute(self) -> None:
        self.path.unlink(missing_ok=True)
