"""Turn raw hook payloads into notification events.

`classify` reads and updates the session's state on disk and returns at most one
notification. The rules are in docs/designs/event-classification.md.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cc_notify_tts.sessions import (
    FINISHED_MEMORY,
    BackgroundItem,
    SessionState,
    SessionStore,
)

DEDUPE_SECONDS = 5.0

# Tools that wait on the user rather than ask permission for an action.
QUESTION_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})
# Prompts Claude Code submits itself when background work finishes.
WAKE_UP_PREFIXES = ("<task-notification>", "<agent-message")
# Background task statuses that no longer count as live work.
FINISHED_STATUSES = frozenset({"completed", "failed", "killed", "stopped", "cancelled"})


class EventKind(StrEnum):
    FINISHED = "finished"
    STILL_RUNNING = "still_running"
    BACKGROUND_DONE = "background_done"
    PERMISSION = "permission"
    QUESTION = "question"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True)
class Notification:
    kind: EventKind
    session_id: str
    cwd: str
    # The hook payload the notification came from, for wording.
    payload: Mapping[str, Any]
    # Seconds since the user's latest prompt, if known.
    turn_seconds: float | None
    # Background work still live (for STILL_RUNNING).
    background: tuple[BackgroundItem, ...]


def classify(store: SessionStore, payload: Mapping[str, Any], now: float) -> Notification | None:
    session_id = _str(payload, "session_id")
    event = _str(payload, "hook_event_name")
    if not session_id or not event:
        return None

    state = store.load(session_id)
    if state is None:
        if event == "SessionEnd":
            return None
        state = SessionState(session_id=session_id)
    state.last_event_at = now
    if cwd := _str(payload, "cwd"):
        state.cwd = cwd

    kind: EventKind | None = None
    if event == "SessionStart":
        state.open = True
        state.closed_at = None
    elif event == "SessionEnd":
        state.open = False
        state.closed_at = now
    elif event == "UserPromptSubmit":
        _user_prompt(state, payload, now)
    elif event == "Stop":
        kind = _stop(state, payload)
    elif event == "StopFailure":
        state.turn_open = False
        kind = EventKind.FAILED
    elif event == "PermissionRequest":
        tool = _str(payload, "tool_name")
        kind = EventKind.QUESTION if tool in QUESTION_TOOLS else EventKind.PERMISSION
    elif event == "PermissionDenied":
        kind = EventKind.DENIED

    if kind is not None and _is_duplicate(state, kind, now):
        kind = None
    store.save(state)
    if event == "SessionEnd":
        store.prune(now)
    if kind is None:
        return None

    return Notification(
        kind=kind,
        session_id=session_id,
        cwd=state.cwd,
        payload=payload,
        turn_seconds=None if state.turn_started_at is None else now - state.turn_started_at,
        background=tuple(state.background) if kind is EventKind.STILL_RUNNING else (),
    )


def _user_prompt(state: SessionState, payload: Mapping[str, Any], now: float) -> None:
    prompt = (_str(payload, "prompt") or "").lstrip()
    prompt_id = _str(payload, "prompt_id")
    if prompt_id is not None and prompt_id not in state.prompt_ids:
        state.prompt_ids = [*state.prompt_ids, prompt_id][-FINISHED_MEMORY:]
    if prompt.startswith(WAKE_UP_PREFIXES):
        if prompt_id is not None:
            state.wake_prompt_ids = [*state.wake_prompt_ids, prompt_id][-FINISHED_MEMORY:]
        return
    if prompt_id is not None and prompt_id == state.last_stop_prompt_id:
        return  # Arrived after its own Stop.
    state.turn_started_at = now
    state.turn_prompt_id = prompt_id
    state.turn_open = True
    # The user is back and typing, so whatever they heard before is moot.
    state.still_running_heard = False


def _stop(state: SessionState, payload: Mapping[str, Any]) -> EventKind | None:
    live = _live_background(payload)
    live_ids = {item.id for item in live}
    prompt_id = _str(payload, "prompt_id")
    if prompt_id is not None and state.turn_prompt_id is not None:
        own_turn = prompt_id == state.turn_prompt_id
    else:
        own_turn = state.turn_open
    if _is_stale(state, prompt_id, live_ids, own_turn):
        return None

    # A wake-up turn is known from its prompt, or, if that hasn't arrived yet,
    # from a Stop outside the user's turn while background work was live.
    wake_up = (prompt_id is not None and prompt_id in state.wake_prompt_ids) or (
        bool(state.background) and not own_turn
    )

    gone = [item.id for item in state.background if item.id not in live_ids]
    # An id can come back: a finished agent continued with SendMessage keeps its id.
    kept = [i for i in state.finished_background if i not in live_ids]
    state.finished_background = (kept + gone)[-FINISHED_MEMORY:]
    state.background = live
    state.last_stop_prompt_id = prompt_id
    if own_turn:
        state.turn_open = False

    if wake_up:
        # A wake-up turn after background work: only the end of all of it matters,
        # and only if the user heard that it was still running.
        if live or not state.still_running_heard:
            return None
        state.still_running_heard = False
        return EventKind.BACKGROUND_DONE
    if live:
        return EventKind.STILL_RUNNING
    state.still_running_heard = False
    return EventKind.FINISHED


def _is_stale(
    state: SessionState, prompt_id: str | None, live_ids: set[str], own_turn: bool
) -> bool:
    """Whether this Stop is older than one already handled (hooks run async)."""
    order = state.prompt_ids
    last = state.last_stop_prompt_id
    if prompt_id in order and last in order:
        return order.index(prompt_id) < order.index(last)
    # Without both prompts on record, a Stop listing work a later Stop showed as
    # finished is old. The user's own turn is never dropped this way.
    return not own_turn and bool(live_ids & set(state.finished_background))


def _live_background(payload: Mapping[str, Any]) -> list[BackgroundItem]:
    tasks: Any = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return []
    live: list[BackgroundItem] = []
    for task in tasks:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(task, Mapping):
            continue
        item: Mapping[str, Any] = task  # pyright: ignore[reportUnknownVariableType]
        task_id = _str(item, "id")
        if not task_id or _str(item, "status") in FINISHED_STATUSES:
            continue
        live.append(
            BackgroundItem(
                id=task_id,
                type=_str(item, "type") or "",
                description=_str(item, "description") or "",
            )
        )
    return live


def _is_duplicate(state: SessionState, kind: EventKind, now: float) -> bool:
    last = state.last_emitted.get(kind)
    if last is not None and 0 <= now - last < DEDUPE_SECONDS:
        return True
    state.last_emitted[kind] = now
    return False


def _str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None
