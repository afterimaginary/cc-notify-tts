"""Classification of the hook fixtures into notification events, with session state on disk."""

import json
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts.events import EventKind, Notification, classify
from cc_notify_tts.sessions import CLOSED_GRACE_SECONDS, STALE_OPEN_SECONDS, SessionStore

FIXTURES = Path(__file__).parent / "fixtures" / "hooks"
SESSION = "5f0c2d1e-8a7b-4c3d-9e2f-1a2b3c4d5e6f"


def fixture(name: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    payload.update(overrides)
    return payload


class Session:
    """Feeds fixtures to `classify` at controlled times."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = SessionStore(root)
        self.now = 1_000.0

    def send(self, name: str, after: float = 1.0, **overrides: Any) -> Notification | None:
        self.now += after
        return classify(self.store, fixture(name, **overrides), self.now)

    def kind(self, name: str, after: float = 1.0, **overrides: Any) -> EventKind | None:
        note = self.send(name, after, **overrides)
        return None if note is None else note.kind

    def restart(self) -> None:
        """A daemon restart: nothing survives but what's on disk."""
        self.store = SessionStore(self.root)


@pytest.fixture
def s(tmp_path: Path) -> Session:
    return Session(tmp_path)


def test_plain_turn_is_finished(s: Session) -> None:
    assert s.kind("session_start") is None
    assert s.kind("user_prompt_submit", prompt_id="p1") is None
    note = s.send("stop", after=42.0, prompt_id="p1")
    assert note is not None
    assert note.kind is EventKind.FINISHED
    assert note.turn_seconds == 42.0
    assert note.cwd == "/Users/dev/src/weather-app"
    assert note.payload["last_assistant_message"].startswith("Added exponential backoff")


@pytest.mark.parametrize(
    ("name", "overrides", "expected"),
    [
        ("permission_request_bash", {}, EventKind.PERMISSION),
        ("permission_request_question", {}, EventKind.QUESTION),
        ("permission_request_bash", {"tool_name": "ExitPlanMode"}, EventKind.QUESTION),
        ("permission_denied", {}, EventKind.DENIED),
        ("stop_failure", {}, EventKind.FAILED),
    ],
)
def test_waits_and_failures(
    s: Session, name: str, overrides: dict[str, Any], expected: EventKind
) -> None:
    assert s.kind(name, **overrides) is expected


@pytest.mark.parametrize(
    "name",
    [
        "notification_idle_prompt",
        "notification_permission_prompt",
        "subagent_start",
        "subagent_stop",
    ],
)
def test_unregistered_events_are_silent(s: Session, name: str) -> None:
    assert s.kind(name) is None


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_every_fixture_classifies(s: Session, path: Path) -> None:
    s.send(path.stem)
    assert s.store.load(SESSION) is not None or path.stem == "session_end"


def test_background_done_after_heard_still_running(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    note = s.send("stop_background_running", prompt_id="p1")
    assert note is not None
    assert note.kind is EventKind.STILL_RUNNING
    assert [b.id for b in note.background] == ["a3f9c2e17b4d5e6f8", "b7k2m9q4x"]
    s.store.mark_still_running_heard(SESSION)

    # The agent finishes first: a wake-up turn with the shell command still running.
    assert s.kind("user_prompt_submit_agent_message", prompt_id="p2") is None
    shell_only = fixture("stop_background_running")["background_tasks"][1:]
    assert s.kind("stop", prompt_id="p2", background_tasks=shell_only) is None

    # Then the shell command: everything is done.
    assert s.kind("user_prompt_submit_task_notification", prompt_id="p3") is None
    assert s.kind("stop_background_done", prompt_id="p3") is EventKind.BACKGROUND_DONE

    state = s.store.load(SESSION)
    assert state is not None
    assert state.background == []
    assert not state.still_running_heard


def test_background_done_is_silent_when_still_running_was_not_heard(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    assert s.kind("stop_background_running", prompt_id="p1") is EventKind.STILL_RUNNING
    s.send("user_prompt_submit_task_notification", prompt_id="p2")
    assert s.kind("stop_background_done", prompt_id="p2") is None


def test_heard_is_only_recorded_while_background_work_is_live(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    s.send("stop", prompt_id="p1")
    s.store.mark_still_running_heard(SESSION)
    state = s.store.load(SESSION)
    assert state is not None
    assert not state.still_running_heard


def test_new_user_prompt_supersedes_heard_still_running(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    s.send("stop_background_running", prompt_id="p1")
    s.store.mark_still_running_heard(SESSION)
    # The user is back and asks for something else; that turn ends with all work done.
    s.send("user_prompt_submit", prompt_id="p2")
    assert s.kind("stop_background_done", prompt_id="p2") is EventKind.FINISHED


def test_user_turn_ending_with_work_still_running(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    s.send("stop_background_running", prompt_id="p1")
    s.send("user_prompt_submit", prompt_id="p2", after=10.0)
    assert s.kind("stop_background_running", prompt_id="p2") is EventKind.STILL_RUNNING


def test_wake_up_after_work_finished_mid_turn_is_silent(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    s.send("stop_background_running", prompt_id="p1")
    # The user's next turn ends after the background work has already finished.
    s.send("user_prompt_submit", prompt_id="p2")
    assert s.kind("stop", prompt_id="p2") is EventKind.FINISHED
    # The wake-up turns reporting that work aren't announced as finished again.
    s.send("user_prompt_submit_task_notification", after=10.0, prompt_id="p3")
    assert s.kind("stop_background_done", prompt_id="p3") is None
    s.send("user_prompt_submit_agent_message", after=10.0, prompt_id="p4")
    assert s.kind("stop_background_done", prompt_id="p4") is None


def test_out_of_order_background_stops(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    s.send("stop_background_running", prompt_id="p1")
    s.store.mark_still_running_heard(SESSION)
    shell_only = fixture("stop_background_running")["background_tasks"][1:]

    # Both wake-up turns' hooks race, and the later Stop is handled first.
    assert s.kind("stop_background_done", prompt_id="p3") is EventKind.BACKGROUND_DONE
    assert s.kind("stop", prompt_id="p2", background_tasks=shell_only) is None

    state = s.store.load(SESSION)
    assert state is not None
    assert state.background == []


def test_out_of_order_stops_ordered_by_prompt(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    s.send("stop_background_running", prompt_id="p1")
    s.store.mark_still_running_heard(SESSION)
    s.send("user_prompt_submit_agent_message", prompt_id="p2")
    s.send("user_prompt_submit_task_notification", prompt_id="p3")
    agent_only = fixture("stop_background_running")["background_tasks"][:1]

    assert s.kind("stop_background_done", prompt_id="p3") is EventKind.BACKGROUND_DONE
    assert s.kind("stop", prompt_id="p2", background_tasks=agent_only) is None
    state = s.store.load(SESSION)
    assert state is not None
    assert state.background == []


def test_continued_agent_keeps_user_turns_audible(s: Session) -> None:
    agent = fixture("stop_background_running")["background_tasks"][:1]
    s.send("user_prompt_submit", prompt_id="p1")
    assert s.kind("stop", prompt_id="p1", background_tasks=agent) is EventKind.STILL_RUNNING
    s.store.mark_still_running_heard(SESSION)
    s.send("user_prompt_submit_agent_message", prompt_id="p2")
    assert s.kind("stop_background_done", prompt_id="p2") is EventKind.BACKGROUND_DONE

    # The user continues the same agent, which keeps its id.
    s.send("user_prompt_submit", after=10.0, prompt_id="p3")
    assert s.kind("stop", prompt_id="p3", background_tasks=agent) is EventKind.STILL_RUNNING
    s.store.mark_still_running_heard(SESSION)
    s.send("user_prompt_submit_agent_message", prompt_id="p4")
    assert s.kind("stop_background_done", prompt_id="p4") is EventKind.BACKGROUND_DONE
    s.send("user_prompt_submit", after=10.0, prompt_id="p5")
    assert s.kind("stop", prompt_id="p5") is EventKind.FINISHED


def test_prompt_arriving_after_its_own_stop(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    s.send("stop_background_running", prompt_id="p1")
    s.store.mark_still_running_heard(SESSION)
    s.send("stop_background_done", prompt_id="p2")
    # The late prompt doesn't start a new turn.
    s.send("user_prompt_submit", prompt_id="p2")
    state = s.store.load(SESSION)
    assert state is not None
    assert state.turn_prompt_id == "p1"
    assert not state.turn_open


def test_stop_without_prompt_ids_uses_turn_tracking(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id=None)
    assert s.kind("stop_background_running", prompt_id=None) is EventKind.STILL_RUNNING
    s.store.mark_still_running_heard(SESSION)
    assert s.kind("stop_background_done", prompt_id=None) is EventKind.BACKGROUND_DONE


def test_restart_mid_turn(s: Session) -> None:
    s.send("session_start")
    s.send("user_prompt_submit", prompt_id="p1")
    s.restart()
    note = s.send("stop", after=30.0, prompt_id="p1")
    assert note is not None
    assert note.kind is EventKind.FINISHED
    assert note.turn_seconds == 30.0


def test_restart_while_background_work_runs(s: Session) -> None:
    s.send("user_prompt_submit", prompt_id="p1")
    s.send("stop_background_running", prompt_id="p1")
    s.store.mark_still_running_heard(SESSION)
    s.restart()
    s.send("user_prompt_submit_task_notification", prompt_id="p2")
    assert s.kind("stop_background_done", prompt_id="p2") is EventKind.BACKGROUND_DONE


def test_duplicates_within_five_seconds_collapse(s: Session) -> None:
    assert s.kind("permission_request_bash") is EventKind.PERMISSION
    assert s.kind("permission_request_bash", after=4.9) is None
    assert s.kind("permission_request_bash", after=0.2) is EventKind.PERMISSION


def test_dedupe_is_per_kind_and_session(s: Session) -> None:
    assert s.kind("permission_request_bash") is EventKind.PERMISSION
    assert s.kind("permission_request_question", after=0.1) is EventKind.QUESTION
    assert s.kind("permission_request_bash", after=0.1, session_id="other") is EventKind.PERMISSION


def test_open_sessions(s: Session) -> None:
    s.send("session_start")
    s.send("session_start", session_id="second")
    assert sorted(st.session_id for st in s.store.open_sessions(s.now)) == [SESSION, "second"]
    s.send("session_end")
    assert [st.session_id for st in s.store.open_sessions(s.now)] == ["second"]


def test_sessions_silent_for_a_day_are_not_open(s: Session) -> None:
    s.send("session_start")
    s.now += STALE_OPEN_SECONDS
    assert s.store.open_sessions(s.now) == []


def test_event_without_session_start_opens_the_session(s: Session) -> None:
    assert s.kind("stop") is EventKind.FINISHED
    assert [st.session_id for st in s.store.open_sessions(s.now)] == [SESSION]


def test_closed_sessions_are_pruned_after_grace(s: Session) -> None:
    s.send("session_start")
    s.send("session_end")
    # A late async event in the grace period doesn't reopen the session.
    assert s.kind("stop_failure") is EventKind.FAILED
    assert s.store.open_sessions(s.now) == []
    assert s.store.load(SESSION) is not None

    # The next SessionEnd anywhere prunes it once the grace period is over.
    s.send("session_start", session_id="second")
    s.send("session_end", after=CLOSED_GRACE_SECONDS, session_id="second")
    assert s.store.load(SESSION) is None


def test_session_end_for_unknown_session_writes_nothing(s: Session) -> None:
    s.send("session_end")
    assert s.store.all() == []


def test_missing_session_id_is_ignored(s: Session) -> None:
    assert s.kind("stop", session_id=None) is None
    assert s.store.all() == []


def test_project_folder_is_where_the_session_started(s: Session) -> None:
    s.send("session_start")
    s.send("user_prompt_submit", prompt_id="p1", cwd="/Users/dev/src/weather-app/tests")
    note = s.send("stop", prompt_id="p1", cwd="/Users/dev/src/weather-app/tests")
    assert note is not None
    assert note.cwd == "/Users/dev/src/weather-app"


def test_host_app_from_the_shim_is_kept_and_replaced(s: Session) -> None:
    classify(s.store, fixture("session_start"), 1.0, host_app="com.apple.Terminal")
    classify(s.store, fixture("user_prompt_submit", prompt_id="p1"), 2.0)
    note = classify(s.store, fixture("stop", prompt_id="p1"), 3.0)
    assert note is not None
    assert note.host_app == "com.apple.Terminal"

    # Inside tmux the shim names it again on each hook that can announce something.
    note = classify(
        s.store, fixture("permission_request_bash"), 4.0, host_app="com.mitchellh.ghostty"
    )
    assert note is not None
    assert note.host_app == "com.mitchellh.ghostty"
