"""Per-session state on disk.

One JSON file per Claude Code session under the state directory. Files are
written atomically (temp file, then rename), so a crash never leaves a torn
file, and the state survives a daemon restart. See docs/designs/event-classification.md.
"""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# A closed session's file stays this long, so a hook event that arrives late
# (hooks run async) doesn't recreate it as an open session.
CLOSED_GRACE_SECONDS = 300.0
# An open session with no event for this long is treated as gone (a crash or a
# killed terminal can skip SessionEnd).
STALE_OPEN_SECONDS = 24 * 3600.0
# How many finished background ids to remember for spotting stale Stop events.
FINISHED_MEMORY = 64

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def default_state_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "cc-notify-tts" / "state"


@dataclass
class BackgroundItem:
    """A background agent or shell command that was live at the last Stop."""

    id: str
    type: str
    description: str


@dataclass
class SessionState:
    session_id: str
    open: bool = True
    cwd: str = ""
    # Bundle id of the app the session runs in, as the hook shim named it.
    host_app: str = ""
    last_event_at: float = 0.0
    closed_at: float | None = None
    # When the current (or latest) user turn started, and its prompt id.
    turn_started_at: float | None = None
    turn_prompt_id: str | None = None
    # A user prompt was submitted and its Stop hasn't been seen yet.
    turn_open: bool = False
    # Prompt id of the latest Stop, to ignore a UserPromptSubmit that arrives after it.
    last_stop_prompt_id: str | None = None
    background: list[BackgroundItem] = field(default_factory=list[BackgroundItem])
    # Prompt ids of recent prompts, user and wake-up, in arrival order.
    prompt_ids: list[str] = field(default_factory=list[str])
    # Prompt ids of recent wake-up prompts (background work reporting back).
    wake_prompt_ids: list[str] = field(default_factory=list[str])
    # Ids that were live at one Stop and gone at a later one.
    finished_background: list[str] = field(default_factory=list[str])
    # Live ids already announced as still running, so a long-lived one (a dev
    # server) is announced once, not at every turn end.
    announced_background: list[str] = field(default_factory=list[str])
    # Ids the latest "still running" announced, which "background done" waits on.
    awaited_background: list[str] = field(default_factory=list[str])
    still_running_heard: bool = False
    # Notification kind -> when it was last emitted, for dedupe.
    last_emitted: dict[str, float] = field(default_factory=dict[str, float])


class SessionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else default_state_dir()

    def path(self, session_id: str) -> Path:
        # Session ids come from hook payloads, so never use one as a path unchecked.
        if _SAFE_ID.fullmatch(session_id):
            name = session_id
        else:
            name = hashlib.sha256(session_id.encode()).hexdigest()
        return self.root / f"{name}.json"

    def load(self, session_id: str) -> SessionState | None:
        try:
            raw = json.loads(self.path(session_id).read_text())
        except (OSError, ValueError):
            return None
        state = _from_json(raw)
        if state is None or state.session_id != session_id:
            return None
        return state

    def save(self, state: SessionState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(state.session_id)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(state), f)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, session_id: str) -> None:
        self.path(session_id).unlink(missing_ok=True)

    def all(self) -> list[SessionState]:
        states: list[SessionState] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                state = _from_json(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
            if state is not None:
                states.append(state)
        return states

    def open_sessions(self, now: float) -> list[SessionState]:
        """Open sessions, leaving out ones silent for so long they're presumed gone."""
        return [s for s in self.all() if s.open and now - s.last_event_at < STALE_OPEN_SECONDS]

    def mark_still_running_heard(self, session_id: str) -> None:
        """Record that a "still running" or "finished" announcement actually played.

        It counts only while work the latest "still running" announced is live: a
        turn that ends "finished" while that work runs keeps "background done" armed.
        """
        state = self.load(session_id)
        if state is not None and any(
            item.id in state.awaited_background for item in state.background
        ):
            state.still_running_heard = True
            self.save(state)

    def prune(self, now: float) -> None:
        """Delete closed sessions after a grace period, stale open ones and unreadable files."""
        if not self.root.is_dir():
            return
        for path in self.root.glob("*.json"):
            try:
                state = _from_json(json.loads(path.read_text()))
            except OSError:
                continue
            except ValueError:
                state = None
            if (
                state is None
                or (state.open and now - state.last_event_at >= STALE_OPEN_SECONDS)
                or (
                    not state.open
                    and now - (state.closed_at or state.last_event_at) >= CLOSED_GRACE_SECONDS
                )
            ):
                path.unlink(missing_ok=True)


def _from_json(raw: Any) -> SessionState | None:
    """Rebuild a SessionState, or None if the file isn't one this version wrote."""
    if not isinstance(raw, dict):
        return None
    try:
        data: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]
        background = [BackgroundItem(**item) for item in data.pop("background", [])]
        state = SessionState(**data, background=background)
    except TypeError:
        return None
    if not isinstance(state.session_id, str) or not state.session_id:  # pyright: ignore[reportUnnecessaryIsInstance]
        return None
    return state
