"""Model wording: the prompt, each guard, and each fallback to the template."""

import json
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts.events import EventKind, Notification
from cc_notify_tts.model_wording import (
    INPUT_CHARS,
    MODEL_EVENTS,
    Attempt,
    Request,
    guard,
    request_for,
    write_detail,
)
from cc_notify_tts.ollama import NUM_CTX, KeepWarm, OllamaError
from cc_notify_tts.wording import word

FIXTURES = Path(__file__).parent / "fixtures" / "hooks"
MODEL = "tiny:1b"
PROJECT = "weather app"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def note(kind: EventKind, payload: dict[str, Any]) -> Notification:
    return Notification(kind, "s1", "/Users/dev/src/weather-app", payload, None, ())


FINISHED = note(EventKind.FINISHED, fixture("stop"))
PERMISSION = note(EventKind.PERMISSION, fixture("permission_request_bash"))
QUESTION = note(EventKind.QUESTION, fixture("permission_request_question"))


class FakeOllama:
    """Answers like Ollama would, or fails the way it's told to."""

    def __init__(
        self,
        reply: str = "",
        *,
        context: int | None = NUM_CTX,
        ps_error: str | None = None,
        chat_error: str | None = None,
    ) -> None:
        self.reply = reply
        self.context = context
        self.ps_error = ps_error
        self.chat_error = chat_error
        self.chats: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []

    def loaded_context(self, model: str) -> int | None:
        if self.ps_error:
            raise OllamaError(self.ps_error)
        return self.context

    def chat(self, model: str, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        self.chats.append({"model": model, "system": system, "user": user, "timeout": timeout})
        if self.chat_error:
            raise OllamaError(self.chat_error)
        return self.reply

    def load(self, model: str) -> None:
        self.calls.append(("load", model))

    def unload(self, model: str) -> None:
        self.calls.append(("unload", model))


def detail(raw: str, n: Notification = FINISHED) -> str | None:
    request = request_for(n)
    assert request is not None
    return guard(raw, request, word(n), PROJECT).detail


def spoken(raw: str, n: Notification = FINISHED) -> str:
    return word(n).text(detail(raw, n))


# --- which events use the model, and what it's shown ---------------------------


def test_only_the_three_prose_events_use_the_model() -> None:
    assert {EventKind.FINISHED, EventKind.QUESTION, EventKind.PERMISSION} == MODEL_EVENTS
    for kind in set(EventKind) - MODEL_EVENTS:
        assert request_for(note(kind, fixture("stop"))) is None


def test_a_plan_to_review_keeps_the_template() -> None:
    plan = note(EventKind.QUESTION, {"tool_name": "ExitPlanMode", "tool_input": {"plan": "x"}})
    assert request_for(plan) is None


def test_no_material_keeps_the_template() -> None:
    assert request_for(note(EventKind.FINISHED, {})) is None
    assert request_for(note(EventKind.PERMISSION, {"tool_input": {}})) is None


def test_the_model_never_sees_the_frame_or_project() -> None:
    for n in (FINISHED, PERMISSION, QUESTION):
        request = request_for(n)
        assert request is not None
        prompt = (request.system + request.user).lower()
        for frame_text in ("weather", "finished up", "needs your okay", "question for you"):
            assert frame_text not in prompt


def test_finished_shows_the_end_of_a_long_message() -> None:
    message = "Start. " + "x" * INPUT_CHARS + " The summary."
    request = request_for(note(EventKind.FINISHED, {"last_assistant_message": message}))
    assert request is not None
    assert request.source.endswith("The summary.")
    assert len(request.source) == INPUT_CHARS


def test_permission_shows_the_tool_call() -> None:
    request = request_for(PERMISSION)
    assert request is not None
    assert request.source == (
        "Tool: Bash\n"
        "command: git push --force origin main\n"
        "description: Force-push the rebased branch to main"
    )


def test_question_shows_the_questions_and_options() -> None:
    request = request_for(QUESTION)
    assert request is not None
    assert request.source == (
        "Should the retry give up after three attempts or five?\nOptions: Three; Five"
    )


# --- guards ----------------------------------------------------------------------


def test_a_good_answer_becomes_the_detail() -> None:
    assert spoken("Added exponential backoff to the forecast fetcher.") == (
        "Finished up in weather app. Added exponential backoff to the forecast fetcher."
    )


def test_first_sentence_only() -> None:
    assert detail("Added backoff to the fetcher. Then I ran the tests.") == (
        "Added backoff to the fetcher."
    )


def test_first_sentence_isnt_cut_at_an_abbreviation() -> None:
    # The same splitter as the template detail (`wording.first_sentence`).
    message = "Switched the cache to a local store, e.g. SQLite, to cut startup. Tests pass."
    n = note(EventKind.FINISHED, {"last_assistant_message": message})
    raw = "Switched the cache to a store, e.g. SQLite, to cut startup. Tests pass."
    assert detail(raw, n) == "Switched the cache to a store, e.g. SQLite, to cut startup."


def test_first_line_only_and_reasoning_dropped() -> None:
    raw = "<think>The user wants a summary.</think>\nAdded backoff to the fetcher\nTwo tests too"
    assert detail(raw) == "Added backoff to the fetcher."


def test_unclosed_reasoning_gives_nothing() -> None:
    assert detail("<think>Let me think about the fetcher and") is None


def test_quotes_labels_and_markdown_are_removed() -> None:
    assert detail('Summary: "Added **backoff** to the `forecast fetcher`."') == (
        "Added backoff to the forecast fetcher."
    )


@pytest.mark.parametrize(
    ("n", "raw", "expected"),
    [
        (
            FINISHED,
            "Finished up in weather app. Added backoff to the fetcher.",
            "Added backoff to the fetcher.",
        ),
        (
            PERMISSION,
            "Weather app needs your okay to force-push the branch to main.",
            "force-push the branch to main",
        ),
        (PERMISSION, "needs your okay to: force-push main.", "force-push main"),
        (
            QUESTION,
            "Has a question for you: should retries stop after three tries?",
            "Should retries stop after three tries?",
        ),
    ],
)
def test_frame_echoes_are_stripped(n: Notification, raw: str, expected: str) -> None:
    assert detail(raw, n) == expected


def test_echo_only_matches_whole_words() -> None:
    n = note(EventKind.FINISHED, {"last_assistant_message": "Updated the apis.", "cwd": "/x/api"})
    request = request_for(n)
    assert request is not None
    assert guard("apis updated.", request, word(n), "api").detail == "Apis updated."


@pytest.mark.parametrize(
    "raw",
    [
        "I added backoff to the fetcher.",
        "I've added backoff to the fetcher.",
        "We added backoff to the fetcher.",
        "It adds backoff to the fetcher.",
        "They\u2019re adding backoff to the fetcher.",
    ],
)
def test_a_leading_subject_pronoun_is_rejected(raw: str) -> None:
    request = request_for(FINISHED)
    assert request is not None
    checked = guard(raw, request, word(FINISHED), PROJECT)
    assert checked.detail is None
    assert "pronoun" in checked.reason


@pytest.mark.parametrize(
    "raw",
    [
        "Added backoff with Redis caching.",  # a name not in the input
        "Added backoff and 12 tests.",  # a number not in the input
        "Added backoff and seven tests.",  # a number word not in the input
        "Added backoff to fetch.go.",  # a file name not in the input
        "Added backoff in often-used code ten times.",  # "ten" isn't in "often"
    ],
)
def test_names_and_numbers_must_be_in_the_input(raw: str) -> None:
    request = request_for(FINISHED)
    assert request is not None
    checked = guard(raw, request, word(FINISHED), PROJECT)
    assert checked.detail is None
    assert "isn't in the input" in checked.reason


def test_names_and_numbers_from_the_input_pass() -> None:
    n = note(
        EventKind.FINISHED,
        {"last_assistant_message": "Moved auth/core.py into three files; 214 tests pass on CI."},
    )
    assert detail("Split core.py into 3 files and 214 tests pass on CI.", n) == (
        "Split core.py into 3 files and 214 tests pass on CI."
    )
    assert detail("Split core.py into three files.", n) == "Split core.py into three files."


def test_a_capitalized_first_word_isnt_a_name() -> None:
    assert detail("Added backoff to the fetcher.") is not None


@pytest.mark.parametrize("raw", ["Done.", " ", "", " ".join(["word"] * 21) + "."])
def test_length_cap(raw: str) -> None:
    assert detail(raw) is None


def test_permission_cap_is_shorter() -> None:
    assert detail(" ".join(["push"] * 13), PERMISSION) is None
    assert detail(" ".join(["push"] * 12), PERMISSION) == " ".join(["push"] * 12)


def test_permission_detail_fits_the_frame() -> None:
    assert spoken("Force-push the rebased branch to main.", PERMISSION) == (
        "Weather app needs your okay to force-push the rebased branch to main."
    )


def test_question_detail_ends_in_a_question_mark() -> None:
    assert spoken("Should the retry give up after three attempts or five", QUESTION) == (
        "Weather app has a question for you. Should the retry give up after three attempts or five?"
    )


# --- a turn that ends with tests failing (ADR-0021) --------------------------------

TESTS_FAIL = note(
    EventKind.FINISHED,
    {
        "last_assistant_message": (
            "Updated the CSV importer to handle quoted commas, but 2 of the 31 tests still"
            " fail: test_empty_rows and test_bom_header. I haven't touched those yet."
        )
    },
)


@pytest.mark.parametrize(
    "raw",
    [
        "Updated the CSV importer to handle quoted commas.",
        "Fixed the CSV importer and its tests.",
    ],
)
def test_success_is_rejected_when_tests_still_fail(raw: str) -> None:
    request = request_for(TESTS_FAIL)
    assert request is not None
    checked = guard(raw, request, word(TESTS_FAIL), PROJECT)
    assert checked.detail is None
    assert checked.reason == "leaves out that tests still fail"
    # The template is spoken instead, and it says so too.
    assert "2 of the 31 tests still fail" in word(TESTS_FAIL).text(checked.detail)


@pytest.mark.parametrize(
    "raw",
    [
        "Updated the CSV importer; 2 tests still fail.",
        "Handled quoted commas in the CSV importer, but 2 tests are failing.",
        "Updated the CSV importer, but 2 tests aren't passing.",
    ],
)
def test_wording_that_admits_the_failure_is_spoken(raw: str) -> None:
    assert detail(raw, TESTS_FAIL) is not None


@pytest.mark.parametrize(
    "raw",
    [
        "Fixed the failing auth tests.",
        "Fixed the auth test failures.",
        "Resolved the failing tests in auth.",
    ],
)
def test_a_fixed_failure_isnt_admitting_the_one_left(raw: str) -> None:
    n = note(
        EventKind.FINISHED,
        {
            "last_assistant_message": (
                "Fixed the failing auth tests. The 2 billing tests still fail because they"
                " need a database."
            )
        },
    )
    assert detail(raw, n) is None
    assert detail("Fixed the auth tests; 2 billing tests still fail.", n) is not None


@pytest.mark.parametrize(
    ("raw", "spoken_"),
    [
        ("Fixed the broken import in the CLI.", False),
        ("Added retry on upload failure.", False),
        ("Handled failed payments in checkout.", False),
        ("Added a fail-fast check to the loader.", False),
        ("Fixed the broken import; tests failed on the fixture.", True),
        ("Fixed the CLI import, but the suite is still broken.", True),
        ("Flagged failing tests in the CLI.", True),
    ],
)
def test_only_a_failure_about_tests_admits_one(raw: str, spoken_: bool) -> None:
    n = note(
        EventKind.FINISHED,
        {
            "last_assistant_message": (
                "Fixed the broken import in the CLI, added retry on upload failure, handled"
                " failed payments in checkout and added a fail-fast check to the loader."
                " 2 tests still fail on the fixture."
            )
        },
    )
    assert (detail(raw, n) is not None) is spoken_


def test_a_fixed_failure_in_the_input_doesnt_reject_correct_wording() -> None:
    n = note(
        EventKind.FINISHED,
        {"last_assistant_message": "Fixed the 3 failed tests in test_auth.py with a fake clock."},
    )
    assert detail("Used a fake clock in test_auth.py.", n) == "Used a fake clock in test_auth.py."


def test_a_test_summary_admits_the_failure() -> None:
    n = note(
        EventKind.FINISHED,
        {"last_assistant_message": "Moved the retry logic. Test run: 41 passed, 2 failed."},
    )
    raw = "Moved the retry logic; 41 passed, 2 failed."
    assert detail(raw, n) == raw


def test_failing_tests_before_the_models_window_still_reject_a_success() -> None:
    message = "2 tests still fail on the fixture. " + "Moved the retry logic. " * 200
    n = note(EventKind.FINISHED, {"last_assistant_message": message})
    request = request_for(n)
    assert request is not None
    assert "fail" not in request.source
    assert detail("Moved the retry logic.", n) is None


def test_something_else_still_broken_doesnt_reject_wording() -> None:
    n = note(
        EventKind.FINISHED,
        {"last_assistant_message": "Fixed the date parser. The CSV import is still broken."},
    )
    assert detail("Fixed the date parser.", n) == "Fixed the date parser."


def test_a_turn_without_failing_tests_isnt_checked_for_them() -> None:
    assert detail("Added backoff to the fetcher.") == "Added backoff to the fetcher."


# --- write_detail: asking Ollama, and each fallback ---------------------------------


def attempt(
    client: FakeOllama, n: Notification = FINISHED, keep_warm: KeepWarm | None = None
) -> Attempt:
    return write_detail(n, word(n), client, MODEL, 1.0, keep_warm)


def test_a_loaded_model_writes_the_detail() -> None:
    client = FakeOllama("Added backoff to the fetcher.")
    result = attempt(client)
    assert result.detail == "Added backoff to the fetcher."
    assert client.chats[0]["model"] == MODEL
    assert client.chats[0]["timeout"] == 1.0


def test_template_events_never_ask() -> None:
    client = FakeOllama("Anything.")
    result = attempt(client, note(EventKind.FAILED, {"error": "rate_limit"}))
    assert result.detail is None
    assert client.chats == []


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (FakeOllama(ps_error="not running"), "Ollama unavailable: not running"),
        (FakeOllama(context=None), f"{MODEL} isn't loaded yet"),
        (FakeOllama(context=32768), f"{MODEL} isn't loaded yet"),
        (FakeOllama(chat_error="no answer within 1 s"), "Ollama failed: no answer within 1 s"),
        (FakeOllama(chat_error="model not found"), "Ollama failed: model not found"),
        (FakeOllama("I did it."), "guard: starts with the pronoun 'I'"),
    ],
)
def test_every_failure_keeps_the_template_with_a_reason(client: FakeOllama, reason: str) -> None:
    result = attempt(client)
    assert result.detail is None
    assert result.reason.startswith(reason)
    assert word(FINISHED).text(result.detail) == word(FINISHED).text()


def test_a_model_that_isnt_loaded_is_loaded_for_next_time_without_waiting() -> None:
    client = FakeOllama("Added backoff.", context=None)
    keep_warm = KeepWarm(client)
    attempt(client, keep_warm=keep_warm)
    keep_warm.close()
    assert client.chats == []
    assert client.calls[0] == ("load", MODEL)


def test_request_shape_is_stable() -> None:
    request = request_for(FINISHED)
    assert isinstance(request, Request)
    assert request.kind is EventKind.FINISHED
    assert request.source in request.user


@pytest.mark.parametrize(
    ("n", "raw"),
    [
        (FINISHED, "Here's a summary of what was done:"),
        (QUESTION, "Here is the question again:"),
    ],
)
def test_a_lead_in_is_rejected(n: Notification, raw: str) -> None:
    request = request_for(n)
    assert request is not None
    assert guard(raw, request, word(n), PROJECT).reason == "a lead-in, not a detail"


def test_more_questions_are_still_counted() -> None:
    payload = fixture("permission_request_question")
    payload["tool_input"]["questions"] *= 3
    n = note(EventKind.QUESTION, payload)
    assert spoken("Should the retry stop after three attempts or five", n) == (
        "Weather app has a question for you."
        " Should the retry stop after three attempts or five? And two more questions."
    )


def test_a_leading_to_isnt_doubled() -> None:
    assert spoken("To force-push the branch to main", PERMISSION) == (
        "Weather app needs your okay to force-push the branch to main."
    )
