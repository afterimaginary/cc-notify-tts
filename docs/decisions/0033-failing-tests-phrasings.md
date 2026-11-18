---
description: The failing-tests check reads more phrasings, ties "broken", "red" and "N failures" to tests, keeps what failed when it cuts a long sentence, and the guard checks the whole message rather than the part the model was shown.
status: accepted
---

# ADR-0033: More failing-tests phrasings, and a guard over the whole message

## Context

ADR-0021 made sure a turn that ends with failing tests is never spoken as a success. Its review rounds left a list of phrasings that its rules handled badly. In each, the model guard held, but the template said something odd or missed the failure, or the guard rejected a correct model answer:

- A clause cut from a sentence over 20 words kept its leading "and" ("And the 2 billing tests still fail."), lost the subject of an appositive ("Still fail." for "two tests, test_a and test_b, still fail"), or lost the word "tests" ("The other 2 still fail.").
- "Still broken" counted as failing tests whatever it was about ("The CSV import is still broken"), so the template added that sentence and the guard rejected model wording that left it out.
- "The test suite still has 2 failures.", "2 tests are still red.", "test_a is failing." and "The test for leap years fails." weren't detected.
- A model answer with a bare summary ("Moved the retry logic; 41 passed, 2 failed.") wasn't accepted as admitting the failure.
- The check split sentences at "e.g.", which the first-sentence split no longer does.
- The guard read only the last 4000 characters of the message, the part the model is shown, while the template reads all of it. A failing-tests sentence early in a long message let a success through.
- No eval case covered the round 1 and 2 phrasings.

## Decision

- **"Broken", "red" and "N failures" count only about tests.** The same clause has to name tests, specs, the suite, CI, the build or a test by name (`test_a`, `parser_test`), at most six words before, none of them starting another clause ("that", "so", "checks"), "still broken", "still red", "is red" or "has" a count of failures. "The CSV import is still broken", "Retried the upload after 3 failures" and "Added regression tests for the two failures reported" don't count; "the suite is still red", "The test suite still has 2 failures" and "The tests now pass locally but CI is still red" do, the last because the clause after "but" is checked on its own. "Still fails" on its own still counts, as before.
- **A test by name or description counts.** "test_a is failing", "test_a fails", "the test for leap years fails" and "the tests in test_dates.py are failing" count. A description that turns into a condition ("a test for when login fails", "a test for the case where the upload fails") doesn't, since that's a test someone added.
- **A clause cut from a long sentence says what failed.** If the clauses that hold the failure don't name tests or the run, the span starts at the nearest earlier clause that does, so "two tests, test_a and test_b, still fail" and "31 of 33 tests now pass, the other 2 still fail" are kept whole. It never starts so far back that the span passes 20 words, which would replace the failure with "Some tests are still failing." A leading "and", "so", "yet", "or", "plus" or "then" is dropped. A test's own name doesn't count for this, since it can be one item of a list after its subject.
- **A test summary admits the failure** in model wording: "41 passed, 2 failed" or "2 failed, 41 passed", with a non-zero failure count.
- **Sentences are split the same way everywhere.** The failing-tests check uses the first-sentence splitter, so an abbreviation ("e.g.") doesn't end a sentence.
- **The guard checks the whole message.** Whether tests are still failing is decided from the full last message when the request is made, not from the 4000 characters the model sees. The model can't mention a failure it wasn't shown, so for such a message its answer is rejected and the template, which reads the whole message, is spoken. The prompt and its window are unchanged.
- **Three eval cases** cover the round 1 and 2 phrasings: a partial fix ("31 of 33 tests now pass, but the other 2 still fail"), a fixed failure followed by a current one ("Fixed the failing auth tests … The 2 billing tests still fail"), and failure-handling work followed by failing tests ("Fixed the broken import … added a fail-fast check … 2 tests still fail").

## Results

`make eval` with `granite4:micro` over 56 cases, the 53 of ADR-0021 and the three above, on Ollama 0.40.16, Apple M3 Max with 48 GiB, macOS 27.2.1. Before is `main`'s guards with the new cases (`evals/results/2026-11-18-7.md`), after is this change (`2026-11-18-8.md`). The before column is that run as it was recorded, scored with `main`'s guards at the time. Recorded runs are re-scored with the current guards, so the table on disk for `2026-11-18-7` may match the after run: the model's answers are the same in both.

| | Before | After |
| --- | --- | --- |
| Pass | 44/56 | 44/56 |
| Spoken | 48/56 | 48/56 |
| Answers that differ | | 0 of 56 |

- **The default model's pass count doesn't change.** It's 44 on the original 53 cases, as in ADR-0021, and it fails the three new cases both times: each answer leaves the failure out, and the guard rejects it, so the template, which says tests still fail, is spoken.
- **No recorded answer changes.** Re-scoring the 530 answers of ADR-0021's ten-model run (`2026-11-18-6.json`) with the new guards changes none of them: spoken, passed and detail are the same for every answer.

## Consequences

- The check still reads English phrasing. A failure put another way ("CI won't go green", "There are still 2 failures in the test suite", "pytest reports 2 failures") isn't caught, and new phrasings found in use are added to `failing_tests` with a test.
- A model answer that admits a failure only by naming a test ("test_a is failing") is accepted, since a test's name counts as being about tests.
- For a message over 4000 characters whose failing-tests sentence comes before the part the model sees, the template is always spoken. That's rare, and the template is the only wording that has read that sentence.
