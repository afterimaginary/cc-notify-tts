"""The hand-written hook payloads keep the field shapes recorded in ADR-0001."""

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "hooks"
COMMON = {"session_id", "transcript_path", "cwd", "hook_event_name"}

# Fields the design reads from each event, by fixture-name prefix.
REQUIRED: dict[str, tuple[str, set[str]]] = {
    "session_start": ("SessionStart", {"source"}),
    "session_end": ("SessionEnd", {"reason"}),
    "user_prompt_submit": ("UserPromptSubmit", {"prompt"}),
    "stop_failure": ("StopFailure", {"error", "last_assistant_message"}),
    "stop": ("Stop", {"last_assistant_message", "background_tasks", "stop_hook_active"}),
    "subagent_start": ("SubagentStart", {"agent_id", "agent_type"}),
    "subagent_stop": ("SubagentStop", {"agent_id", "agent_type", "background_tasks"}),
    "permission_request": ("PermissionRequest", {"tool_name", "tool_input"}),
    "permission_denied": ("PermissionDenied", {"tool_name", "tool_input", "reason"}),
    "notification": ("Notification", {"message", "notification_type"}),
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _kind(name: str) -> str:
    # Longest prefix wins, so "stop_failure" is not read as "stop".
    return max((k for k in REQUIRED if name.startswith(k)), key=len)


PATHS = sorted(FIXTURES.glob("*.json"))


def test_fixtures_exist() -> None:
    assert {_kind(p.stem) for p in PATHS} == set(REQUIRED)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.stem)
def test_fixture_shape(path: Path) -> None:
    payload = _load(path)
    event, fields = REQUIRED[_kind(path.stem)]
    assert payload["hook_event_name"] == event
    assert COMMON | fields <= payload.keys()


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.stem)
def test_background_tasks_shape(path: Path) -> None:
    for task in _load(path).get("background_tasks", []):
        assert {"id", "type", "status", "description"} <= task.keys()
        assert task["type"] in {"subagent", "shell"}


def test_background_states() -> None:
    assert _load(FIXTURES / "stop_background_running.json")["background_tasks"]
    assert _load(FIXTURES / "stop_background_done.json")["background_tasks"] == []
