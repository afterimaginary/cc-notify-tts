"""The wording eval's cases, scoring, runner and reports, without a model."""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import evals.run
from cc_notify_tts.events import EventKind
from cc_notify_tts.model_wording import ARGUMENT_CHARS, INPUT_CHARS
from cc_notify_tts.ollama import NUM_CTX, OllamaError
from evals.cases import CASES_DIR, Case, CaseError, load_cases
from evals.compare import compare
from evals.report import render
from evals.rescore import rescore
from evals.results import Results, load_results
from evals.run import run_model, write_results
from evals.score import (
    Score,
    contains,
    failed,
    is_lead_in,
    percentile,
    rejection,
    score,
    summarize,
)

CASES = {case.id: case for case in load_cases()}


def case(case_id: str) -> Case:
    return CASES[case_id]


# The case files


def test_there_are_at_least_40_cases_across_the_three_prose_events() -> None:
    kinds = [c.kind for c in CASES.values()]
    assert len(kinds) >= 40
    assert {k: kinds.count(k) >= 10 for k in kinds} == {
        EventKind.FINISHED: True,
        EventKind.QUESTION: True,
        EventKind.PERMISSION: True,
    }


def test_every_case_lists_required_and_forbidden_content() -> None:
    for c in CASES.values():
        assert c.required and all(c.required), c.id
        assert c.forbidden, c.id


def test_the_cases_include_each_kind_of_trap() -> None:
    traps = {t for c in CASES.values() for t in c.traps}
    assert {"numbers", "names", "long input", "multi-sentence", "lead-in"} <= traps


def test_long_inputs_are_long_enough_to_be_cut() -> None:
    long_message = case("finished-long-message")
    assert len(long_message.request.source) == INPUT_CHARS
    raw = json.loads((CASES_DIR / "permission.json").read_text())
    longest = max(
        len(str(value)) for entry in raw for value in entry["payload"]["tool_input"].values()
    )
    assert longest > ARGUMENT_CHARS


def test_payloads_use_only_the_synthetic_home_folder() -> None:
    for path in CASES_DIR.glob("*.json"):
        for entry in json.loads(path.read_text()):
            assert entry["payload"]["cwd"].startswith("/Users/dev/src/"), entry["id"]


def write_case_file(folder: Path, name: str, cases: list[dict[str, Any]]) -> None:
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps(cases))


def stop_case(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "c1",
        "payload": {
            "hook_event_name": "Stop",
            "cwd": "/Users/dev/src/demo",
            "last_assistant_message": "Added a test.",
        },
        "required": [["test"]],
        "forbidden": ["deployed"],
    }
    return entry | overrides


def test_a_case_must_classify_as_its_files_event(tmp_path: Path) -> None:
    write_case_file(tmp_path, "question.json", [stop_case()])
    with pytest.raises(CaseError, match="classifies as finished, not question"):
        load_cases(tmp_path)


def test_a_case_must_list_forbidden_content(tmp_path: Path) -> None:
    write_case_file(tmp_path, "finished.json", [stop_case(forbidden=[])])
    with pytest.raises(CaseError, match="forbidden"):
        load_cases(tmp_path)


def test_case_ids_are_unique(tmp_path: Path) -> None:
    write_case_file(tmp_path, "finished.json", [stop_case(), stop_case()])
    with pytest.raises(CaseError, match="duplicate"):
        load_cases(tmp_path)


def test_a_case_file_names_a_model_event(tmp_path: Path) -> None:
    write_case_file(tmp_path, "denied.json", [])
    with pytest.raises(CaseError, match="keeps the template"):
        load_cases(tmp_path)


def test_message_lines_are_joined(tmp_path: Path) -> None:
    payload = stop_case()["payload"] | {"last_assistant_message": ["Added a test.", "", "Done."]}
    write_case_file(tmp_path, "finished.json", [stop_case(payload=payload)])
    (loaded,) = load_cases(tmp_path)
    assert loaded.request.source == "Added a test.\n\nDone."


# Scoring one answer


def test_a_good_answer_passes() -> None:
    s = score(case("finished-backoff"), "Added exponential backoff to the forecast fetcher.")
    assert s.passed
    assert s.failures() == []


def test_an_answer_missing_the_required_detail_fails() -> None:
    s = score(case("finished-backoff"), "Updated the forecast fetcher and its tests.")
    assert s.spoken and not s.kept and not s.passed
    assert s.missing == ("backoff",)
    assert s.failures() == ["missing 'backoff'"]


def test_forbidden_content_counts_as_invented_even_when_the_guards_pass() -> None:
    # Redis is in the input, so the name guard can't catch the wrong claim.
    s = score(case("finished-considered-not-used"), "Added Redis caching for map tile responses.")
    assert s.spoken
    assert s.invented == ("added redis", "redis caching")
    assert not s.passed


def test_an_answer_longer_than_the_prompt_asks_fails_on_length() -> None:
    answer = (
        "Added exponential backoff to the forecast fetcher along with two new tests "
        "covering the retry path so that it holds."
    )
    s = score(case("finished-backoff"), answer)
    assert s.spoken and s.kept
    assert s.words == 20
    assert not s.within_length and not s.passed


def test_a_rejected_answer_records_the_guards_reason() -> None:
    s = score(case("finished-backoff"), "I added exponential backoff to the fetcher.")
    assert not s.spoken
    assert s.reason == "starts with the pronoun 'I'"
    assert s.failures() == ["guard: starts with the pronoun 'I'"]


def test_a_spoken_lead_in_is_flagged() -> None:
    answer = "Here's what was done: renamed fetchUser to loadUser in 14 files."
    s = score(case("finished-lead-in-inline"), answer)
    assert s.spoken and s.lead_in and s.passed


def test_a_plain_detail_is_not_a_lead_in() -> None:
    s = score(case("finished-lead-in-inline"), "Renamed fetchUser to loadUser in 14 files.")
    assert not s.lead_in


def test_a_lead_in_is_counted_even_when_the_guards_reject_the_answer() -> None:
    answer = (
        "Here's what was done: renamed the fetchUser helper to loadUser across all of the "
        "14 files and then updated every single import."
    )
    s = score(case("finished-lead-in-inline"), answer)
    assert not s.spoken and s.reason.endswith("not 2 to 20")
    assert s.lead_in


def test_a_question_that_echoes_its_lead_in_is_flagged() -> None:
    answer = "Before I continue: should the migration run on deploy or by hand?"
    assert score(case("question-lead-in"), answer).lead_in


@pytest.mark.parametrize(
    ("text", "lead_in"),
    [
        ("Here's what was done: renamed it.", True),
        ("Update: added caching.", True),
        ("I've made these changes: added caching.", True),
        ("Update nginx.conf: add caching for map tiles", False),
        ("Added caching for map tiles.", False),
    ],
)
def test_lead_in_forms(text: str, lead_in: bool) -> None:
    assert is_lead_in(text) is lead_in


def test_an_accurate_answer_naming_the_old_value_isnt_invented() -> None:
    s = score(case("permission-edit-setting"), "Change DEBUG from True to False in settings.py")
    assert s.passed


def test_the_suffix_for_more_questions_doesnt_count_toward_length() -> None:
    s = score(case("question-two-questions"), "Should dates be ISO 8601 strings or timestamps?")
    assert s.detail is not None and s.detail.endswith("And one more question.")
    assert s.words == 8
    assert s.passed


@pytest.mark.parametrize(
    ("text", "phrase", "found"),
    [
        ("Force-pushed to main", "force push*", True),
        ("Retries three times", "retr*", True),
        ("Added a test", "tests", False),
        ("Added tests", "test", False),
        ("Kept the “UTF-8” export", "utf 8", True),
        ("Fixed an N+1 query", "n+1", True),
        ("Remained on main", "main", True),
        ("Remained on the branch", "main", False),
    ],
)
def test_contains_matches_whole_words(text: str, phrase: str, found: bool) -> None:
    assert contains(text, phrase) is found


@pytest.mark.parametrize(
    ("answer", "category"),
    [
        ("I added backoff.", "pronoun"),
        ("Added", "length"),
        ("Added backoff with Redis.", "not in input"),
        ("Here's what was done:", "lead-in"),
        ("", "empty"),
    ],
)
def test_each_guard_rejection_has_a_category(answer: str, category: str) -> None:
    s = score(case("finished-backoff"), answer)
    assert not s.spoken
    assert rejection(s.reason) == category


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 21)]
    assert percentile(values, 50) == 10
    assert percentile(values, 95) == 19
    assert percentile([5.0], 95) == 5


def test_summary_counts_each_check() -> None:
    backoff = case("finished-backoff")
    scores = [
        score(backoff, "Added exponential backoff to the forecast fetcher."),
        score(backoff, "Updated the forecast fetcher."),
        score(backoff, "I added backoff."),
        score(case("finished-lead-in-inline"), "Here's what was done: renamed it to loadUser."),
        score(case("finished-lead-in-inline"), "Here's what was done:"),
    ]
    s = summarize(scores, [100.0, 200.0, 300.0, 1200.0, 50.0], budget_ms=1000)
    assert (s.cases, s.passed, s.spoken, s.kept) == (5, 2, 3, 2)
    assert (s.lead_ins_spoken, s.lead_ins_rejected) == (1, 1)
    assert s.rejections == {"lead-in": 1, "pronoun": 1}
    assert s.by_event == {"finished": [2, 5]}
    assert (s.p50_ms, s.p95_ms, s.over_budget) == (200, 1200, 1)


# Running a model


class FakeOllama:
    def __init__(self, answers: dict[str, str], *, loaded: bool = False) -> None:
        self.answers = answers
        self.loaded = loaded
        self.calls: list[str] = []

    def version(self) -> str:
        return "0.0.1"

    def running(self, model: str) -> dict[str, Any] | None:
        if not self.loaded:
            return None
        return {"digest": "abc123", "size": 2**30, "size_vram": 2**30, "context_length": NUM_CTX}

    def chat(self, model: str, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        self.calls.append("chat")
        for needle, answer in self.answers.items():
            if needle in user:
                if answer == "fail":
                    raise OllamaError("no answer within 30 s")
                return answer
        return "Did something."

    def load(self, model: str) -> None:
        self.calls.append("load")
        self.loaded = True

    def unload(self, model: str) -> None:
        self.calls.append("unload")
        self.loaded = False


def ticking(step: float = 0.1) -> Iterator[float]:
    t = 0.0
    while True:
        yield t
        t += step


def test_a_run_warms_up_first_and_scores_every_case() -> None:
    cases = [case("finished-backoff"), case("permission-force-push")]
    client = FakeOllama(
        {"backoff": "Added exponential backoff to the forecast fetcher.", "git push": "fail"}
    )
    clock = ticking()
    run = run_model(client, "tiny:1b", cases, clock=lambda: next(clock))
    assert client.calls == ["load", "chat", "chat", "chat", "unload"]
    assert run.warm_up_ms == pytest.approx(100)
    assert run.latencies_ms == pytest.approx((100, 100))
    assert [s.passed for s in run.scores] == [True, False]
    assert run.scores[1].reason == "Ollama failed: no answer within 30 s"
    assert (run.digest, run.resident_bytes, run.context_length) == ("abc123", 2**30, NUM_CTX)


def test_a_model_loaded_before_the_run_stays_loaded() -> None:
    client = FakeOllama({}, loaded=True)
    run_model(client, "tiny:1b", [case("finished-backoff")])
    assert "unload" not in client.calls


def test_a_failed_warm_up_still_unloads_the_model() -> None:
    client = FakeOllama({"Renamed the settings page": "fail"})
    with pytest.raises(OllamaError):
        run_model(client, "tiny:1b", [case("finished-backoff")])
    assert client.calls == ["load", "chat", "unload"]


def test_a_missing_model_is_skipped_and_the_others_are_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Missing(FakeOllama):
        def load(self, model: str) -> None:
            if model == "missing:1b":
                raise OllamaError("model not found")
            super().load(model)

    def client(endpoint: str) -> Missing:
        return Missing({})

    monkeypatch.setattr(evals.run, "Ollama", client)
    monkeypatch.setattr(evals.run, "machine_class", lambda: "Apple M1, 16 GiB, macOS 27.0")
    assert evals.run.main(["missing:1b", "tiny:1b", "--out", str(tmp_path)]) == 1
    (written,) = tmp_path.glob("*.json")
    assert [r.model for r in load_results(written).runs] == ["tiny:1b"]


# Results and reports


def results(answer: str, day: str = "2026-11-18") -> Results:
    client = FakeOllama({"backoff": answer})
    cases = [case("finished-backoff"), case("question-retries")]
    run = run_model(client, "tiny:1b", cases, load_average=lambda: 2.5)
    return Results(
        date=day,
        ollama_version="0.0.1",
        machine="Apple M1, 16 GiB, macOS 27.0",
        cases_digest="digest",
        case_counts={"finished": 1, "question": 1},
        settings={"num_ctx": NUM_CTX, "max_tokens": 40, "temperature": 0},
        runs=(run,),
    )


def test_results_are_written_as_markdown_and_json_without_overwriting(tmp_path: Path) -> None:
    first = write_results(results("Added backoff to the fetcher."), tmp_path)
    second = write_results(results("Added backoff to the fetcher."), tmp_path)
    assert (first.name, second.name) == ("2026-11-18.md", "2026-11-18-2.md")
    assert second.with_suffix(".json").exists()


def test_results_round_trip_through_json(tmp_path: Path) -> None:
    original = results("Added backoff to the fetcher.")
    path = write_results(original, tmp_path)
    assert load_results(path.with_suffix(".json")) == original


def test_the_report_has_the_table_and_the_failures() -> None:
    text = render(results("Added backoff to the fetcher."))
    assert text.startswith("# Wording eval, 2026-11-18\n")
    assert "Ollama 0.0.1 on Apple M1, 16 GiB, macOS 27.0. 2 cases" in text
    assert "| `tiny:1b` | 1/2 (50%) | 2/2 (100%) | 1/2 (50%) | 0 | 0 | 0 | 0 |" in text
    assert "| `tiny:1b` | `abc123` | 2048 | 1.00 GiB | 1.00 GiB | 0 | 2.5 |" in text
    assert "| `question-retries` | missing 'retr*' | Did something. |" in text


def test_rescore_scores_recorded_answers_with_the_current_guards() -> None:
    recorded = results("Added backoff to the fetcher.")
    run = recorded.runs[0]
    # As if an older guard had rejected the first answer, and Ollama never answered the second.
    stale = (
        replace(run.scores[0], detail=None, reason="an older guard's reason"),
        failed(case("question-retries"), "no answer within 30 s"),
    )
    old = replace(recorded, runs=(replace(run, scores=stale),))
    new = rescore(old, list(CASES.values()), "2026-11-19")
    assert new.runs[0].scores == (run.scores[0], stale[1])
    assert new.runs[0].latencies_ms == run.latencies_ms
    assert new.scoring.startswith("Re-scored on 2026-11-19 from the recorded answers")


def test_rescore_needs_every_case() -> None:
    with pytest.raises(CaseError, match="finished-backoff"):
        rescore(results("Added backoff."), [case("question-retries")], "2026-11-19")


def test_how_a_run_was_scored_is_in_the_report_and_the_json(tmp_path: Path) -> None:
    noted = replace(results("Added backoff to the fetcher."), scoring="Scored with old guards.")
    assert "\n\nScored with old guards.\n\n## Results\n" in render(noted)
    path = write_results(noted, tmp_path)
    assert load_results(path.with_suffix(".json")) == noted


def test_compare_shows_differences_and_changed_answers() -> None:
    a = results("Added backoff to the fetcher.")
    b = results("Updated the fetcher.", day="2026-11-19")
    text = compare(a, b)
    assert "| Pass | 1 | 0 | -1 |" in text
    assert "Answers that differ: 1 of 2." in text
    assert "- `finished-backoff`: “Added backoff to the fetcher.” → “Updated the fetcher.”" in text


def test_score_failures_list_each_check() -> None:
    s = Score("c", EventKind.PERMISSION, "x", "push it", "ok", ("force push",), ("pull",), 11)
    assert s.failures() == ["missing 'force push'", "invented 'pull'", "11 words, asked for 10"]
