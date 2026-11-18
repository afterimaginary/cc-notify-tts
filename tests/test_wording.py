"""Template wording for every notification event."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts.events import EventKind, Notification, classify
from cc_notify_tts.sessions import BackgroundItem, SessionStore
from cc_notify_tts.wording import (
    Wording,
    capitalize,
    describe_action,
    failing_tests,
    first_sentence,
    project_name,
    word,
)
from evals.cases import load_cases

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
    "stop_dev_server_running": (
        "Done for now in weather app, but background work isn't finished."
        " Still going: run the dev server."
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


@pytest.mark.parametrize(
    ("text", "first"),
    [
        # Abbreviations don't end the sentence.
        ("Used a cache, e.g. Redis, for it. Then tested.", "Used a cache, e.g. Redis, for it."),
        ("Kept one store, i.e. disk. Then tested.", "Kept one store, i.e. disk."),
        ("Picked SQLite vs. Postgres for tests. Done.", "Picked SQLite vs. Postgres for tests."),
        ("Chose (e.g. here) the slower path. Done.", "Chose (e.g. here) the slower path."),
        ("Shipped it in the U.S. first today. Done.", "Shipped it in the U.S. first today."),
        ("Asked Dr. Lee about it. Done.", "Asked Dr. Lee about it."),
        ("Cut it by approx. half. Done.", "Cut it by approx. half."),
        ("Fixed lint, types, etc. and the docs. Done.", "Fixed lint, types, etc. and the docs."),
        ("Fixed lint, types, etc. Then tested.", "Fixed lint, types, etc."),
        # Decimals and versions never did.
        ("Cut latency from 3.5s to 1.2s. Done.", "Cut latency from 3.5s to 1.2s."),
        ("Bumped it to v1.2.3 and Node to 20.x. Done.", "Bumped it to v1.2.3 and Node to 20.x."),
        ("Removed version 0.9. Then tested.", "Removed version 0.9."),
        # "ms" is milliseconds, not a title.
        ("Startup now takes 120 ms. Tests pass.", "Startup now takes 120 ms."),
        # Real ends still do, and a text without one is all one sentence.
        ("Done. Everything passes.", "Done."),
        ("Is it fixed? Yes.", "Is it fixed?"),
        ("Fixed the bug in events.py. Done.", "Fixed the bug in events.py."),
        ("Compared it with the old one vs.", "Compared it with the old one vs."),
        ("No end mark", "No end mark"),
    ],
)
def test_first_sentence(text: str, first: str) -> None:
    assert first_sentence(text) == first


def test_finished_detail_isnt_cut_at_an_abbreviation() -> None:
    message = "Switched the cache to a local store, e.g. SQLite, to cut startup. Tests pass."
    wording = word(note(EventKind.FINISHED, {"last_assistant_message": message}))
    assert wording.detail == "Switched the cache to a local store, e.g. SQLite, to cut startup."


@pytest.mark.parametrize(
    ("message", "detail"),
    [
        # An inline lead-in gives way to what it introduces.
        (
            "Here's what was done: renamed the fetchUser helper to loadUser. Tests pass.",
            "Renamed the fetchUser helper to loadUser.",
        ),
        ("Here\u2019s a summary of the work: fixed the retry.", "Fixed the retry."),
        ("Summary: fixed the retry and added tests.", "Fixed the retry and added tests."),
        ("**Done:** fixed the retry.", "Fixed the retry."),
        ("In short: the cache was stale.", "The cache was stale."),
        # A lead-in to a choice stays, so the choice isn't heard as an instruction.
        (
            "Here are two options: keep the cache or drop it.",
            "Here are two options: keep the cache or drop it.",
        ),
        (
            "Here\u2019s the choice: retry now or wait for the fix.",
            "Here\u2019s the choice: retry now or wait for the fix.",
        ),
        (
            "Here are a few alternatives: pin the version, vendor it or drop it.",
            "Here are a few alternatives: pin the version, vendor it or drop it.",
        ),
        (
            "Here are two ways to fix it: retry the request or raise the timeout.",
            "Here are two ways to fix it: retry the request or raise the timeout.",
        ),
        # On its own line, a lead-in to a choice stays in front of what follows it.
        (
            "Here are two options:\n\n1. Keep the cache\n2. Drop it",
            "Here are two options: keep the cache.",
        ),
        ("**Options:**\n- Retry now\n- Wait for the fix", "Options: retry now."),
        (
            "Here are two options:\n\nKeep the cache or drop it.",
            "Here are two options: keep the cache or drop it.",
        ),
        # A label naming each option counts as the first option, like a list item.
        (
            "Here are two options:\n\n**Option A: Keep the cache**\nThis is faster.\n\n"
            "**Option B: Drop it**\nThis is safer.",
            "Here are two options: keep the cache.",
        ),
        ("Two options:\n\n### Option 1: keep the cache\nFaster.", "Two options: keep the cache."),
        (
            "Here are two ways to fix it:\n\n### Option A (recommended) - Retry the request\n"
            "Cheap.\n\n### Option B - Raise the timeout\nSafer.",
            "Here are two ways to fix it: retry the request.",
        ),
        # Without a lead-in to a choice, the label is spoken whole, so it's still heard as one.
        ("### Option 1: Keep the cache\nFaster.", "Option 1: Keep the cache."),
        (
            "Here's what I'd suggest:\n\n**Option A — keep the cache**\nThis is faster.",
            "Option A: keep the cache.",
        ),
        ("### Option 1. Keep the cache\nFaster.", "Option 1: Keep the cache."),
        # A label that only names an option keeps the lead-in for what follows it...
        (
            "Here are two options:\n\n### Option A\nKeep the cache.\n\n### Option B\nDrop it.",
            "Here are two options: keep the cache.",
        ),
        ("Two options:\n\n**Option A**\nKeep the cache.", "Two options: keep the cache."),
        # ...and without one, goes in front of it.
        ("**Option A**\nKeep the cache, it's faster.", "Option A: keep the cache, it's faster."),
        ("## Options\n\n### Option A\nKeep the cache.", "Option A: keep the cache."),
        ("## Optional: cleanup\n\nRemoved the old flag.", "Removed the old flag."),
        # A long one is left out, so the option isn't cut off.
        (
            "Looking at how the cache behaves under load and what the logs show, "
            "I think there are a couple of options:\n\n1. Keep the cache\n2. Drop it",
            "Keep the cache.",
        ),
        # A label line before it doesn't carry over.
        ("Here are two options:\n## Summary\n\n- Fixed the retry", "Fixed the retry."),
        # A summary lead-in on its own line still goes.
        ("Here's what changed:\n\n1. Fixed the retry\n2. Added tests", "Fixed the retry."),
        # A summary lead-in that only mentions a choice-like word elsewhere still goes.
        (
            "Here's what was done: added an option to drop the cache.",
            "Added an option to drop the cache.",
        ),
        # A lead-in with nothing after it says nothing, inline or on its own line.
        ("Here's what was done:", None),
        ("Here's what was done:\n\nFixed the retry.", "Fixed the retry."),
        # A colon mid-sentence after anything else is content.
        (
            "Fixed the parser: it dropped the last token.",
            "Fixed the parser: it dropped the last token.",
        ),
        ("Update nginx.conf: add caching.", "Update nginx.conf: add caching."),
        ("Benchmarked it at 10:30: twice as fast.", "Benchmarked it at 10:30: twice as fast."),
        (
            "Here's the PR. Fixed it: the cache was stale.",
            "Here's the PR.",
        ),
    ],
)
def test_finished_detail_after_a_lead_in(message: str, detail: str | None) -> None:
    assert word(note(EventKind.FINISHED, {"last_assistant_message": message})).detail == detail


@pytest.mark.parametrize(
    "message",
    [
        "Here's where things stand: 2 tests still fail.",
        "Summary: 2 failed, 41 passed.",
        "Here are the results: 2 failed, 41 passed.",
    ],
)
def test_failing_tests_after_an_inline_lead_in_are_spoken_once(message: str) -> None:
    after = message.partition(": ")[2]
    assert finished_text(message) == f"Finished up in weather app. {capitalize(after)}"


def test_a_long_here_lead_in() -> None:
    message = "Here's a summary of the changes I made to the auth flow: renamed it."
    assert finished_text(message) == "Finished up in weather app. Renamed it."


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
        "The test suite still has 2 failures.",
        "2 tests are still red.",
        "CI is red on the auth suite.",
        "test_a is failing.",
        "The test for leap years fails.",
        "The tests in test_dates.py are failing.",
        "Three tests, e.g. test_a and test_b, still fail.",
        "The tests now pass locally but CI is still red.",
        "Fixed the failing unit test but CI is still red.",
        "All tests now pass locally but the build still has 2 failures on Linux.",
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
        "The CSV import is still broken for files with a BOM.",
        "Retried the upload after 3 failures.",
        "The integration suite finished with 0 failures.",
        "Fixed the 2 test failures in test_auth.py.",
        "Added a test for when login fails.",
        "Added a test for the case where the upload fails.",
        "Made the error banner red.",
        "Added regression tests for the two failures reported in the issue.",
        "Added a test that the error banner is red.",
        "The snapshot test now checks the border is red.",
        "Updated the build so the warning badge is red.",
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
    "first",
    [
        "Fixed the broken import in the CLI.",
        "Added retry on upload failure.",
        "Handled failed payments in checkout.",
        "Added a fail-fast check to the loader.",
    ],
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


@pytest.mark.parametrize(
    "failing", ["Test run: 41 passed, 2 failed.", "Two tests, test_a and test_b, still fail."]
)
def test_a_short_failing_sentence_is_spoken_whole(failing: str) -> None:
    assert finished_text(f"Moved the retry logic. {failing}") == (
        f"Finished up in weather app. Moved the retry logic. {failing}"
    )


@pytest.mark.parametrize(
    ("message", "text"),
    [
        (
            "Moved the retry logic. Ran the full test suite again after rebasing onto main"
            " with the new fixtures and a clean cache: 41 passed, 2 failed.",
            "Finished up in weather app. Moved the retry logic. Some tests are still failing.",
        ),
        (
            "Refactored the slot generator so bookings across daylight saving changes and"
            " leap days land on the right local hour and 2 tests still fail.",
            "Finished up in weather app. Some tests are still failing.",
        ),
    ],
)
def test_a_failure_the_cap_would_cut_off_is_said_plainly(message: str, text: str) -> None:
    assert finished_text(message) == text


@pytest.mark.parametrize(
    ("message", "text"),
    [
        (
            "Moved the retry logic. Only test_a is failing.",
            "Finished up in weather app. Moved the retry logic. Only test_a is failing.",
        ),
        (
            "Moved the retry logic. Ran it again: 41 passed, 2 failed.",
            "Finished up in weather app. Moved the retry logic. Ran it again: 41 passed, 2 failed.",
        ),
    ],
)
def test_the_plain_fallback_is_only_for_a_cut_sentence(message: str, text: str) -> None:
    assert finished_text(message) == text


# Every failing-tests phrasing the template handles, and every phrasing a review
# round found mishandled: failing ones, and successes that mention a failure.
SHORT_SENTENCES = [
    "Updated the importer, but 2 of the 31 tests still fail: test_a and test_b.",
    "Three tests in test_slots.py still fail because they expect UTC.",
    "Added the endpoint, but its tests aren't passing yet.",
    "The tests are still failing on CI.",
    "Test run: 2 failed, 41 passed.",
    "Two integration tests fail on the new schema.",
    "Some failing tests remain in the parser.",
    "31 of 33 tests now pass; the other 2 still fail on leap years.",
    "Most tests now pass, but 3 still fail.",
    "It no longer crashes on empty input, but 2 tests still fail.",
    "The 2 billing tests still fail without a database.",
    "Test run: 41 passed, 2 failed.",
    "Tests: 41 passed, 2 failed, 43 total.",
    "Ran cargo test: 41 passed; 2 failed; 0 ignored.",
    "Two tests, test_a and test_b, still fail.",
    "The CSV import is still broken for files with a BOM.",
    "Ran it again: 41 passed, 2 failed.",
    "The test suite still has 2 failures.",
    "2 tests are still red.",
    "CI is red on the auth suite.",
    "Only test_a is failing.",
    "The test for leap years fails.",
    "Three tests, e.g. test_a and test_b, still fail.",
    "Added a test for when login fails.",
    "Retried the upload after 3 failures.",
    "Fixed the failing auth tests.",
    "Fixed the broken import in the CLI.",
    "Added retry on upload failure.",
    "Handled failed payments in checkout.",
    "Added a fail-fast check to the loader.",
    "Fixed the 2 failed tests; all 40 pass now.",
    "Fixed 2 tests failing on CI by pinning the clock.",
    "Retried the 3 failed uploads.",
    "The importer now gives up after 5 failed attempts.",
    "The two upload tests that were still failing now pass.",
    "The flaky test no longer fails.",
]


@pytest.mark.parametrize("sentence", SHORT_SENTENCES)
def test_a_sentence_of_20_words_or_fewer_is_spoken_as_written(sentence: str) -> None:
    assert len(sentence.split()) <= 20
    assert finished_text(sentence) == f"Finished up in weather app. {sentence}"
    # After another first sentence it's either added as written or left out, never changed.
    spoken = finished_text(f"Moved the retry logic. {sentence}")
    assert spoken in (
        "Finished up in weather app. Moved the retry logic.",
        f"Finished up in weather app. Moved the retry logic. {sentence}",
        f"Finished up in weather app. {sentence}",
    )
    if failing_tests(sentence) is not None:
        assert spoken.endswith(sentence)


def test_eval_cases_keep_a_short_failing_sentence_as_written() -> None:
    for case in load_cases():
        if case.kind is not EventKind.FINISHED:
            continue
        sentences = re.findall(r"\S.*?[.!?](?=\s|$)", case.request.source)
        failing = next((s for s in sentences if failing_tests(s) is not None), None)
        if failing is not None and len(failing.split()) <= 20:
            assert failing in case.wording.text(), case.id


def test_a_failure_at_the_end_of_a_long_sentence_isnt_cut_off() -> None:
    message = (
        "Rewrote the calendar slot generator so that bookings across daylight saving changes"
        " land on the right local hour, but 2 tests still fail."
    )
    assert finished_text(message) == "Finished up in weather app. 2 tests still fail."


@pytest.mark.parametrize(
    "message",
    [
        "Fixed the 2 failed tests; all 40 pass now.",
        "Fixed 2 tests failing on CI by pinning the clock.",
        "Retried the 3 failed uploads.",
        "Fixed the 3 failed tests in test_auth.py by mocking the clock.",
        "The importer now gives up after 5 failed attempts.",
    ],
)
def test_a_success_with_a_fail_word_is_spoken_once_as_it_is(message: str) -> None:
    assert failing_tests(message) is None
    assert finished_text(message) == f"Finished up in weather app. {message}"


def test_failing_tests_are_spoken_after_a_lead_in() -> None:
    message = "Here's where things stand:\n\n- Moved the retry logic\n- 2 tests still fail"
    assert finished_text(message) == (
        "Finished up in weather app. Moved the retry logic. 2 tests still fail."
    )


def test_something_else_still_broken_isnt_added() -> None:
    message = "Fixed the date parser. The CSV import is still broken for files with a BOM."
    assert finished_text(message) == "Finished up in weather app. Fixed the date parser."


LONG_START = (
    "Rewrote the calendar slot generator so that bookings across daylight saving changes"
    " land on the right local hour"
)


@pytest.mark.parametrize(
    ("end", "spoken"),
    [
        # A clause after a comma loses its conjunction.
        (", and the 2 billing tests still fail.", "The 2 billing tests still fail."),
        # An appositive keeps its subject.
        (
            ", and two tests, test_a and test_b, still fail.",
            "Two tests, test_a and test_b, still fail.",
        ),
        # A count keeps the clause that says it's a count of tests.
        (
            "; 31 of 33 tests now pass, the other 2 still fail.",
            "31 of 33 tests now pass, the other 2 still fail.",
        ),
    ],
)
def test_a_failure_cut_from_a_long_sentence_says_what_failed(end: str, spoken: str) -> None:
    assert finished_text(LONG_START + end) == f"Finished up in weather app. {spoken}"


def test_a_cut_failure_doesnt_reach_back_past_the_word_cap() -> None:
    message = (
        "Moved the retry logic, updated every import across the unit tests and integration"
        " tests and the generated API docs for the client library, and the billing check"
        " still fails."
    )
    assert finished_text(message) == "Finished up in weather app. The billing check still fails."


def test_a_failing_sentence_isnt_split_at_an_abbreviation() -> None:
    message = "Moved the retry logic. Three tests, e.g. test_a and test_b, still fail."
    assert failing_tests(message) == "Three tests, e.g. test_a and test_b, still fail"
