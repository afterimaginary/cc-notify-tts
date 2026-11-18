"""Synthetic notifications for `cc-notify-tts fire <event>`.

Each event kind gets a made-up but typical payload, so the template wording (and
the model wording, when it's on) produces the sentence a real event of that
kind would. The notification never touches session state: it skips
classification and goes straight to the gate, the wording and the queue.
"""

from typing import Any

from cc_notify_tts.events import EventKind, Notification
from cc_notify_tts.sessions import BackgroundItem

# The session id fired notifications carry, in the daemon's log and `status`.
SESSION_ID = "fire"

_PAYLOADS: dict[EventKind, dict[str, Any]] = {
    EventKind.FINISHED: {
        "hook_event_name": "Stop",
        "last_assistant_message": "I fixed the date parsing bug, and all the tests pass now.",
    },
    EventKind.STILL_RUNNING: {"hook_event_name": "Stop"},
    EventKind.BACKGROUND_DONE: {
        "hook_event_name": "Stop",
        "last_assistant_message": "The full test run finished with no failures.",
    },
    EventKind.PERMISSION: {
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "npm test"},
    },
    EventKind.QUESTION: {
        "hook_event_name": "PermissionRequest",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "Which database should the app use?"}]},
    },
    EventKind.DENIED: {
        "hook_event_name": "PermissionDenied",
        "tool_name": "Bash",
        "tool_input": {"command": "git push --force"},
    },
    EventKind.FAILED: {"hook_event_name": "StopFailure", "error": "rate_limit"},
}

_BACKGROUND = (BackgroundItem("fire-1", "subagent", "Run the full test suite"),)


def notification(kind: EventKind, cwd: str, host_app: str) -> Notification:
    """A notification of `kind` for a session in `cwd`, hosted by `host_app` ("" if unknown)."""
    return Notification(
        kind=kind,
        session_id=SESSION_ID,
        cwd=cwd,
        payload={**_PAYLOADS[kind], "session_id": SESSION_ID, "cwd": cwd},
        turn_seconds=None,
        background=_BACKGROUND if kind is EventKind.STILL_RUNNING else (),
        host_app=host_app,
    )
