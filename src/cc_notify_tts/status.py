"""`cc-notify-tts status`: would it speak now, for each event, and why.

The CLI runs the same gate the daemon runs (`gate.decide`), with the same
inputs: the config file, the mute file, idle time read now, and each open
session's host app and how long it has been out of front. When the daemon is
running, sessions and out-of-front times come from it, since only it follows
the frontmost app. When it isn't, sessions come from the state folder and the
out-of-front times are what a daemon starting now would see: 0 s.

`gather` reads all of that; `render` turns it into text and is pure, for tests.
"""

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cc_notify_tts import __version__, client, config, host_app
from cc_notify_tts.events import EventKind
from cc_notify_tts.gate import Readings, decide
from cc_notify_tts.mute import Muted, MuteFile
from cc_notify_tts.presence import app_name, read_front_app, read_idle_seconds
from cc_notify_tts.sessions import SessionStore
from cc_notify_tts.wording import project_name

# How many of the daemon's recent decisions to show.
RECENT_SHOWN = 5
# How long to wait for the daemon's status reply.
STATUS_TIMEOUT = 1.0


@dataclass(frozen=True)
class Session:
    project: str
    # Bundle id, or None when unknown.
    host_app: str | None
    out_of_front_seconds: float | None


@dataclass(frozen=True)
class Facts:
    now: datetime
    # The daemon's `status` reply, or None when it isn't running.
    daemon: dict[str, Any] | None
    mute: Muted | None
    config_path: Path
    config_exists: bool
    loaded: config.Loaded
    idle_seconds: float | None
    front_app: str | None
    sessions: Sequence[Session]
    # The app this command runs in, standing in for a session's host when none is open.
    here: str | None


def gather(
    socket: Path | None = None,
    config_path: Path | None = None,
    store: SessionStore | None = None,
    mute: MuteFile | None = None,
    here: str | None = None,
) -> Facts:
    socket = client.socket_path() if socket is None else socket
    config_path = config.default_config_path() if config_path is None else config_path
    store = SessionStore() if store is None else store
    mute = MuteFile() if mute is None else mute
    try:
        daemon: dict[str, Any] | None = client.request(socket, {"type": "status"}, STATUS_TIMEOUT)
        if daemon.get("ok") is not True:
            daemon = None
    except (OSError, ValueError):
        daemon = None
    if daemon is not None:
        front = daemon.get("front_app")
        front_app = front if isinstance(front, str) else None
        sessions = _daemon_sessions(daemon)
    else:
        front_app = read_front_app()
        sessions = [
            Session(
                project_name(s.cwd),
                s.host_app or None,
                # A daemon starting now would count from its start.
                None if front_app is None or not s.host_app else 0.0,
            )
            for s in store.open_sessions(time.time())
        ]
    return Facts(
        now=datetime.now(),
        daemon=daemon,
        mute=mute.current(),
        config_path=config_path,
        config_exists=config_path.exists(),
        loaded=config.load(config_path),
        idle_seconds=read_idle_seconds(),
        front_app=front_app,
        sessions=sessions,
        here=(here if here is not None else host_app.identify(os.environ, os.getpid())) or None,
    )


def _daemon_sessions(reply: dict[str, Any]) -> list[Session]:
    raw: Any = reply.get("sessions")
    sessions: list[Session] = []
    for entry in raw if isinstance(raw, list) else []:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(entry, dict):
            continue
        fields: dict[str, Any] = entry  # pyright: ignore[reportUnknownVariableType]
        host = fields.get("host_app")
        out = fields.get("out_of_front_seconds")
        project = fields.get("project")
        sessions.append(
            Session(
                project if isinstance(project, str) else "",
                host if isinstance(host, str) and host else None,
                float(out) if isinstance(out, int | float) else None,
            )
        )
    return sessions


class _Fixed:
    """The mute state read once, so every event's decision sees the same one."""

    def __init__(self, muted: bool) -> None:
        self._muted = muted

    def is_muted(self) -> bool:
        return self._muted


def render(facts: Facts) -> str:
    lines = [
        f"Daemon    {_daemon_line(facts.daemon)}",
        f"Mute      {_mute_line(facts.mute, facts.now)}",
        f"Config    {_config_line(facts)}",
        f"Presence  {_presence_line(facts)}",
    ]
    cfg = facts.loaded.config
    mute = _Fixed(facts.mute is not None)
    width = max(len(kind) for kind in EventKind)
    for title, readings in _groups(facts):
        lines.append("")
        lines.append(f"Would it speak now? {title}")
        reasons = [decide(k, cfg, mute, facts.now.time(), readings).reason for k in EventKind]
        if len(set(reasons)) == 1:
            lines.append(f"  {'every event':<{width}}  {reasons[0]}")
            continue
        lines.extend(
            f"  {kind:<{width}}  {reason}" for kind, reason in zip(EventKind, reasons, strict=True)
        )
    recent = _recent(facts.daemon)
    if recent:
        lines.append("")
        lines.append("Recent decisions")
        lines.extend(f"  {line}" for line in recent)
    return "\n".join(lines)


def _daemon_line(reply: dict[str, Any] | None) -> str:
    if reply is None:
        return "not running; the next event starts it"
    version = reply.get("version")
    shown = f"version {version}"
    if version != __version__:
        shown += f" (this command is {__version__})"
    parts = [f"running, pid {reply.get('pid')}", shown, f"speech {reply.get('speech')}"]
    speaking = reply.get("speaking")
    if isinstance(speaking, str):
        parts.append(f'saying "{speaking}"')
    return ", ".join(parts)


def _mute_line(muted: Muted | None, now: datetime) -> str:
    if muted is None:
        return "off"
    if muted.until is None:
        return "on, until `cc-notify-tts unmute`"
    left = _duration(muted.until - now.timestamp())
    return f"on, until {muted.ends(now.timestamp())} ({left} left)"


def _config_line(facts: Facts) -> str:
    path = _tilde(facts.config_path)
    if not facts.config_exists:
        return f"{path} (no file, so every setting is its default)"
    problems = len(facts.loaded.problems)
    if problems:
        noun = "problem" if problems == 1 else "problems"
        return f"{path} ({problems} {noun}; `cc-notify-tts doctor` lists them)"
    return path


def _presence_line(facts: Facts) -> str:
    threshold = facts.loaded.config.presence.idle_seconds
    idle = facts.idle_seconds
    input_part = (
        "idle time can't be read"
        if idle is None
        else f"last input {int(idle)} s ago (away after {threshold:g} s)"
    )
    front = facts.front_app
    front_part = (
        "the app in front can't be read" if front is None else f"{app_name(front)} in front"
    )
    return f"{input_part}; {front_part}"


def _groups(facts: Facts) -> list[tuple[str, Readings]]:
    """One gate input per host app, since the gate's answer depends only on that.

    With no session open, the app this command runs in stands in for the host,
    out of front for 0 s if it isn't in front, as a daemon starting now would see.
    """
    if not facts.sessions:
        here = facts.here
        if here is None:
            return [("(no session is open)", Readings(facts.idle_seconds, None, None))]
        out = None if facts.front_app is None else 0.0
        title = f"(no session is open; as if one were in {app_name(here)})"
        return [(title, Readings(facts.idle_seconds, here, out))]
    groups: dict[str | None, tuple[list[str], float | None]] = {}
    for s in facts.sessions:
        projects, _ = groups.setdefault(s.host_app, ([], s.out_of_front_seconds))
        if s.project not in projects:
            projects.append(s.project)
    return [
        (
            f"Sessions in {'an unknown app' if host is None else app_name(host)}: "
            + ", ".join(projects),
            Readings(facts.idle_seconds, host, out),
        )
        for host, (projects, out) in groups.items()
    ]


def _recent(reply: dict[str, Any] | None) -> list[str]:
    raw: Any = None if reply is None else reply.get("recent")
    entries: list[Any] = raw if isinstance(raw, list) else []  # pyright: ignore[reportUnknownVariableType]
    lines: list[str] = []
    for entry in entries[-RECENT_SHOWN:]:
        if not isinstance(entry, dict):
            continue
        fields: dict[str, Any] = entry  # pyright: ignore[reportUnknownVariableType]
        at = fields.get("at")
        clock = (
            datetime.fromtimestamp(at).strftime("%H:%M:%S")
            if isinstance(at, int | float)
            else "--:--:--"
        )
        lines.append(
            f"{clock}  {fields.get('project')}, {fields.get('event')}: {fields.get('reason')}"
        )
    return lines


def _duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)} s"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def _tilde(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def run() -> int:
    print(render(gather()))
    return 0
