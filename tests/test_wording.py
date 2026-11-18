"""Template wording for every notification event."""

import json
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts.events import EventKind, Notification, classify
from cc_notify_tts.sessions import BackgroundItem, SessionStore
from cc_notify_tts.wording import Wording, describe_action, failing_tests, project_name, word

FIXTURES = Path(__file__).parent / "fixtures" / "hooks"
SESSION = "5f0c2d1e-8a7b-4c3d-9e2f-1a2b3c4d5e6f"


def fixture(name: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    payload.update(overrides)
    return payload


def note(kind: EventKind, payload: dict[str, Any] | None = None, **kw: Any) -> Notification:
    return Notification(
        kind=kind,
        session_id=SESSION,
        cwd=kw.pop("cwd", "/Users/dev/src/weather-app"),
        payload=payload or {},
        turn_seconds=None,
        background=kw.pop("background", ()),
    )


def classified(store: SessionStore, name: str) -> Notification | None:
    """The notification a fixture gives on its own, in a fresh session."""
    if name == "stop_background_done":
        # Only a wake-up turn after a heard "still running" gives this event.
        classify(store, fixture("user_prompt_submit", prompt_id="p1"), 1.0)
        classify(store, fixture("stop_background_running", prompt_id="p1"), 2.0)
        store.mark_still_running_heard(SESSION)
        classify(store, fixture("user_prompt_submit_task_notification", prompt_id="p2"), 30.0)
        return classify(store, fixture(name, prompt_id="p2"), 31.0)
    return classify(store, fixture(name), 1.0)


# Every hook fixture, and what it's spoken as on its own (None: nothing).
SPOKEN: dict[str, str | None] = {
    "notification_idle_prompt": None,
    "notification_permission_prompt": None,
    "permission_denied": "Weather app was refused permission to clean build output.",
    "permission_request_bash": (
        "Weather app needs your okay to force-push the rebased branch to main."
    ),
    "permission_request_question": (
        "Weather app has a question for you. Should the retry give up after three attempts or five?"
    ),
    "session_end": None,
    "session_start": None,
    "stop": (
        "Finished up in weather app. Added exponential backoff to the forecast fetcher"
        " and two tests for the retry path."
    ),
    "stop_background_done": (
        "Background work's all done in weather app."
        " The integration suite finished: 112 passed, 0 failed."
    ),
    "stop_background_running": (
        "Done for now in weather app, but background work isn't finished."
        " Still going: audit the forecast cache and run the integration test suite."
    ),
    "stop_failure": "The turn failed in weather app. It hit a rate limit.",
    "subagent_start": None,
    "subagent_stop": None,
    "user_prompt_submit": None,
    "user_prompt_submit_agent_message": None,
    "user_prompt_submit_task_notification": None,
}


def test_every_fixture_is_listed() -> None:
    assert set(SPOKEN) == {p.stem for p in FIXTURES.glob("*.json")}


@pytest.mark.parametrize("name", sorted(SPOKEN))
def test_fixture_wording(tmp_path: Path, name: str) -> None:
    result = classified(SessionStore(tmp_path), name)
    assert (None if result is None else word(result).text()) == SPOKEN[name]


def test_every_event_kind_has_a_spoken_fixture(tmp_path: Path) -> None:
    kinds = {
        result.kind
        for name in SPOKEN
        if (result := classified(SessionStore(tmp_path / name), name)) is not None
    }
    assert kinds == set(EventKind)


@pytest.mark.parametrize(
    ("cwd", "expected"),
    [
        ("/Users/dev/src/weather-app", "weather app"),
        ("/Users/dev/src/my_tool", "my tool"),
        ("/Users/dev/src/WeatherApp", "weather app"),
        ("/Users/dev/src/parseAPIResponse", "parse API response"),
        ("/Users/dev/src/API-gateway/", "API gateway"),
        ("/Users/dev/src/next.js", "next.js"),
        ("", "your project"),
        ("/", "your project"),
        ("/Users/dev/src/weather-app/.claude/worktrees/issue-15", "weather app"),
        ("/Users/dev/src/weather-app/.claude/worktrees/issue-15/src", "weather app"),
    ],
)
def test_project_name(cwd: str, expected: str) -> None:
    assert project_name(cwd) == expected


def test_project_name_starts_a_sentence_capitalized() -> None:
    payload = fixture("permission_request_bash")
    assert word(note(EventKind.PERMISSION, payload, cwd="/x/api")).text().startswith("Api needs")
    assert word(note(EventKind.PERMISSION, payload, cwd="")).text().startswith("Your project")


@pytest.mark.parametrize(
    ("tool", "tool_input", "expected"),
    [
        ("Bash", {"command": "git status", "description": "Show status."}, "show status"),
        ("Bash", {"command": "git status"}, "run git status"),
        (
            "Bash",
            {"command": "python /Users/dev/src/app/scripts/build.py --release --verbose x y"},
            "run python build.py --release --verbose x y",
        ),
        ("Bash", {"command": "make\necho done"}, "run make"),
        ("Bash", {"command": "  "}, "run a shell command"),
        ("Bash", {"command": "ls", "description": "README check"}, "README check"),
        ("Edit", {"file_path": "/Users/dev/src/app/src/events.py"}, "edit events.py"),
        ("MultiEdit", {"file_path": "/a/b.ts"}, "edit b.ts"),
        ("Write", {"file_path": "/Users/dev/src/app/NOTES.md"}, "write NOTES.md"),
        ("Write", {}, "write a file"),
        ("Read", {"file_path": "/etc/hosts"}, "read hosts"),
        ("NotebookEdit", {"notebook_path": "/a/analysis.ipynb"}, "edit analysis.ipynb"),
        (
            "WebFetch",
            {"url": "https://www.example.com/docs/page?x=1"},
            "fetch a page from example.com",
        ),
        ("WebFetch", {}, "fetch a web page"),
        ("WebSearch", {"query": "kokoro onnx voices"}, "search the web for kokoro onnx voices"),
        (
            "Agent",
            {"description": "Audit the cache", "prompt": "..."},
            "start an agent to audit the cache",
        ),
        ("Task", {}, "start an agent"),
        ("Skill", {"skill": "code-review"}, "use the code review skill"),
        ("Skill", {"skill": "tools:deploy"}, "use the tools deploy skill"),
        ("mcp__github__create_issue", {}, "use create issue from github"),
        ("mcp__vault", {}, "use vault"),
        ("mcp__plugin_docs_docs__query_docs", {}, "use query docs from docs"),
        ("mcp__plugin_kit_search__find", {}, "use find from kit search"),
        ("TodoWrite", {}, "use todo write"),
        ("", {}, "use a tool"),
    ],
)
def test_describe_action(tool: str, tool_input: dict[str, Any], expected: str) -> None:
    assert describe_action({"tool_name": tool, "tool_input": tool_input}) == expected


def test_bash_command_is_shortened() -> None:
    command = "pytest -q tests/test_a.py tests/test_b.py tests/test_c.py tests/test_d.py"
    action = describe_action({"tool_name": "Bash", "tool_input": {"command": command}})
    assert action == "run pytest -q test_a.py test_b.py test_c.py test_d.py"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls build 2>/dev/null", "run ls build"),
        ("curl -s https://api.example.com/v1/items", "run curl -s api.example.com"),
    ],
)
def test_command_redirections_and_urls(command: str, expected: str) -> None:
    action = describe_action({"tool_name": "Bash", "tool_input": {"command": command}})
    assert action == expected


def test_long_description_is_capped() -> None:
    description = " ".join(["word"] * 30)
    action = describe_action({"tool_name": "Bash", "tool_input": {"description": description}})
    assert len(action.split()) == 12


def test_permission_without_tool_input() -> None:
    text = word(note(EventKind.PERMISSION, {"tool_name": "Bash"})).text()
    assert text == "Weather app needs your okay to run a shell command."


def test_exit_plan_mode_is_a_plan_to_review() -> None:
    payload = {"tool_name": "ExitPlanMode", "tool_input": {"plan": "# Plan\n..."}}
    wording = word(note(EventKind.QUESTION, payload))
    assert wording == Wording("Weather app has a plan ready for you to review.")


def test_several_questions() -> None:
    payload = fixture("permission_request_question")
    first = payload["tool_input"]["questions"][0]
    payload["tool_input"]["questions"] = [first, first, first]
    text = word(note(EventKind.QUESTION, payload)).text()
    assert text.endswith("three attempts or five? And two more questions.")


def test_question_without_question_mark_or_text() -> None:
    payload = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"header": "Retries"}]}}
    assert word(note(EventKind.QUESTION, payload)).detail == "Retries?"
    empty: dict[str, Any] = {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}}
    assert word(note(EventKind.QUESTION, empty)).text() == "Weather app has a question for you."


@pytest.mark.parametrize(
    ("message", "detail"),
    [
        ("Done. Everything passes.", "Done."),
        ("**Fixed** the `parse` bug in [events](events.py).", "Fixed the parse bug in events."),
        ("Here's what changed:\n- Added one\n- Added two", "Added one."),
        ("## Summary\n\nFixed the retry. Tests pass.", "Fixed the retry."),
        ("**Summary**\n\nFixed the retry.", "Fixed the retry."),
        ("**Done:**\nFixed the retry.", "Fixed the retry."),
        ("- Fixed the retry\n- Added tests", "Fixed the retry."),
        ("1. Fixed the retry\n2. Added tests", "Fixed the retry."),
        ("Fixed two bugs, in order\n- one\n- two", "Fixed two bugs, in order."),
        ("Here's the diff:\n```\ncode\n```\nAll good.", "All good."),
        (
            "Done, the PR is at https://github.com/o/r/pull/3. Merge when ready.",
            "Done, the PR is at github.com.",
        ),
        ("Fixed the bug in /Users/dev/src/app/src/events.py.", "Fixed the bug in events.py."),
        ("Wrote ~/notes/plan.md, see ./docs/a.md.", "Wrote plan.md, see a.md."),
        ("Kept and/or dropped 3/4 of them.", "Kept and/or dropped 3/4 of them."),
        ("", None),
        ("```\ncode\n```", None),
        ("No full stop at the end", "No full stop at the end."),
        (" ".join(["word"] * 30) + ".", " ".join(["word"] * 20) + "."),
    ],
)
def test_finished_detail(message: str, detail: str | None) -> None:
    wording = word(note(EventKind.FINISHED, {"last_assistant_message": message}))
    assert wording.frame == "Finished up in weather app."
    assert wording.detail == detail


def test_finished_without_detail() -> None:
    assert word(note(EventKind.FINISHED)).text() == "Finished up in weather app."


@pytest.mark.parametrize(
    ("items", "detail"),
    [
        ([BackgroundItem("a", "subagent", "Audit the cache.")], "Still going: audit the cache."),
        (
            [BackgroundItem("a", "subagent", "Audit"), BackgroundItem("b", "shell", "")],
            "Still going: one agent and one command.",
        ),
        (
            [BackgroundItem(str(i), "shell", "Run it") for i in range(3)],
            "Still going: three commands.",
        ),
        ([BackgroundItem(str(i), "subagent", "x") for i in range(12)], "Still going: 12 agents."),
    ],
)
def test_still_running_detail(items: list[BackgroundItem], detail: str) -> None:
    assert word(note(EventKind.STILL_RUNNING, background=tuple(items))).detail == detail


@pytest.mark.parametrize(
    ("error", "text"),
    [
        ("overloaded", "The turn failed in weather app. It hit an overloaded server."),
        ("unknown", "The turn failed in weather app."),
        ("something_new", "The turn failed in weather app."),
    ],
)
def test_failed(error: str, text: str) -> None:
    assert word(note(EventKind.FAILED, {"error": error})).text() == text


def test_model_detail_replaces_the_template_detail() -> None:
    wording = word(note(EventKind.PERMISSION, fixture("permission_request_bash")))
    assert (
        wording.text("force-push to main") == "Weather app needs your okay to force-push to main."
    )
    finished = word(note(EventKind.FINISHED, fixture("stop")))
    assert finished.text("fixed the retry.") == "Finished up in weather app. Fixed the retry."


# --- a turn that ends with tests failing (ADR-0021) ------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Updated the importer, but 2 of the 31 tests still fail: test_a and test_b.",
        "Three tests in test_slots.py still fail because they expect UTC.",
        "Added the endpoint, but its tests aren't passing yet.",
        "The tests are still failing on CI.",
        "Test run: 2 failed, 41 passed.",
        "Two integration tests fail on the new schema.",
        "Some failing tests remain in the parser.",
        "Refactored the module.\n\n- test_refund still fails",
        "31 of 33 tests now pass; the other 2 still fail on leap years.",
        "Most tests now pass, but 3 still fail.",
        "It no longer crashes on empty input, but 2 tests still fail.",
    ],
)
def test_failing_tests_are_found(message: str) -> None:
    assert failing_tests(message) is not None


@pytest.mark.parametrize(
    "message",
    [
        "Added backoff and two tests for the retry path.",
        "Fixed the 2 failing tests in test_parser.py; all 33 tests pass now.",
        "The two upload tests that were still failing now pass.",
        "The flaky test no longer fails.",
        "The integration suite finished: 112 passed, 0 failed.",
        "Fixed the bug that made tests fail on CI.",
    ],
)
def test_passing_tests_arent_failing(message: str) -> None:
    assert failing_tests(message) is None


def finished_text(message: str) -> str:
    return word(note(EventKind.FINISHED, {"last_assistant_message": message})).text()


def test_failing_tests_already_in_the_first_sentence_stay_as_they_are() -> None:
    assert finished_text("Updated the importer, but 2 tests still fail. More later.") == (
        "Finished up in weather app. Updated the importer, but 2 tests still fail."
    )


def test_failing_tests_in_a_later_sentence_are_added() -> None:
    assert finished_text("Fixed the timezone handling. Three tests still fail. Sorry.") == (
        "Finished up in weather app. Fixed the timezone handling. Three tests still fail."
    )


def test_a_fixed_failure_doesnt_count_as_admitting_one() -> None:
    message = "Fixed the failing auth tests. The 2 billing tests still fail without a database."
    assert finished_text(message) == (
        "Finished up in weather app. Fixed the failing auth tests."
        " The 2 billing tests still fail without a database."
    )


@pytest.mark.parametrize(
    "first", ["Fixed the broken import in the CLI.", "Added retry on upload failure."]
)
def test_a_failure_thats_not_about_tests_doesnt_count_as_admitting_one(first: str) -> None:
    assert finished_text(f"{first} 2 tests still fail on the fixture.") == (
        f"Finished up in weather app. {first} 2 tests still fail on the fixture."
    )


def test_failing_tests_are_spoken_alone_when_both_dont_fit() -> None:
    message = (
        "Rewrote the calendar slot generator so that bookings across daylight saving changes"
        " land on the right local hour every time. Test run: 2 failed, 41 passed."
    )
    assert finished_text(message) == "Finished up in weather app. Test run: 2 failed, 41 passed."


def test_failing_tests_are_spoken_after_a_lead_in() -> None:
    message = "Here's where things stand:\n\n- Moved the retry logic\n- 2 tests still fail"
    assert finished_text(message) == (
        "Finished up in weather app. Moved the retry logic. 2 tests still fail."
    )
