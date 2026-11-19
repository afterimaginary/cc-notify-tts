---
description: A finished turn whose last message says tests are still failing is never spoken as a success; a guard rejects model wording that leaves the failure out, so the template is spoken, and the template adds the sentence that says so when its own first sentence doesn't mention it.
status: accepted
---

# ADR-0021: A turn with failing tests is never worded as a success

## Context

In the ten-model comparison (ADR-0018), seven models answered the `finished-tests-still-fail` case with "Updated the CSV importer to handle quoted commas." for a message that went on to say 2 of 31 tests still fail. Every one of those answers passed the guards (ADR-0015): each name and number in it is in the input, and nothing in it is false. It is still the most harmful kind of wrong notification: someone away from the desk hears that the work is done, and doesn't come back to fix it.

Only `granite4:micro`, the default, kept the failure. A stronger prompt might help other models but can't be relied on for all of them, and the template had the same gap: it speaks the message's first sentence, so "Fixed the timezone handling. Three tests still fail." was spoken as "Fixed the timezone handling."

## Decision

- **Code decides whether tests are still failing, from the turn's last message.** A clause counts when it says tests still fail or are failing, aren't passing, a count of tests fail ("2 of the 31 tests still fail"), or holds a test summary ("2 failed, 41 passed"), unless it also says the failure is over ("no longer fails", "now pass") or was fixed ("Fixed 2 tests failing on CI"). So "31 of 33 tests now pass; the other 2 still fail" counts, and "Retried the 3 failed uploads" doesn't. The patterns, their examples and counter-examples are in `wording.failing_tests` and its tests; the behaviour is in the spoken-messages spec.
- **A guard rejects model wording that leaves the failure out.** For a finished turn whose input has such a sentence, the model's detail must say tests are failing: a fail word near tests, the suite, CI or the build, "still fails" or "not passing". A failure it says was fixed ("Fixed the failing auth tests") or one about something other than tests ("Fixed the broken import") doesn't count. Otherwise the guard rejects it and the template is spoken. The guard checks the answer, not the model, so it holds for any model a user configures.
- **The template keeps the failure.** If the message's first sentence doesn't mention it, the sentence that says tests fail (only its failing clauses, if it passes 20 words) is added after it, or spoken alone when the two together pass the 20-word cap: the failure is the part a listener most needs. The frame, "Finished up in …", stays: it says the turn ended, not that it succeeded.
- **The prompt is unchanged.** A prompt change would change every model's answers to every case and need its own comparison; the guard alone meets the outcome, and the default model already gets these cases right more often than any other.

## Results

`make eval` over 53 cases, the 48 of ADR-0016 and five new ones: three turns that end with failing tests, worded differently (`finished-tests-fail-second-sentence`, `finished-tests-not-passing-yet`, `finished-test-summary-failed`), and two successes that mention failing tests (`finished-failing-tests-fixed`, `finished-tests-now-pass`), which a guard that's too eager would reject. The same ten models and digests as ADR-0018, Ollama 0.40.16, Apple M3 Max with 48 GiB, macOS 27.2.1.

Before is the guards on `main` (`evals/results/2026-11-18-5.md`), after is this change (`2026-11-18-6.md`). Every answer is identical between the two runs, as ADR-0016 found at temperature 0, so every difference below is the guard's.

| Model | Pass before | Pass after | Spoken before | Spoken after | Success spoken for failing tests, before | After |
| --- | --- | --- | --- | --- | --- | --- |
| `granite4:micro` | 44 | 44 | 51 | 48 | 3 | 0 |
| `qwen3.5:4b` | 44 | 44 | 50 | 46 | 4 | 0 |
| `phi4-mini:3.8b` | 43 | 43 | 53 | 49 | 4 | 0 |
| `qwen3.5:2b` | 42 | 42 | 49 | 45 | 4 | 0 |
| `llama3.2:3b` | 42 | 42 | 53 | 49 | 4 | 0 |
| `gemma3:4b` | 41 | 41 | 48 | 46 | 2 | 0 |
| `gemma3:1b` | 37 | 37 | 53 | 50 | 3 | 0 |
| `llama3.2:1b` | 34 | 34 | 48 | 44 | 4 | 0 |
| `qwen3:1.7b` | 34 | 34 | 44 | 41 | 3 | 0 |
| `qwen3:4b` | 0 | 0 | 16 | 16 | 0 | 0 |

The last two columns count spoken answers to the four failing-tests cases that leave the failure out. Before, 31 of the 36 such answers from the nine usable models were spoken as a success; after, none are. `qwen3:4b`'s answers were already all rejected for other reasons (ADR-0018).

- **Pass counts don't change.** Each rejected answer had already failed the eval's `required` check, since the failure is required detail in those cases. What changes is what's heard: the template's sentence, which says tests still fail, in place of a success.
- **The default model doesn't drop.** `granite4:micro` passes 44 of 53 before and after. On the original 48 cases it passes 42, as in ADR-0018. It gets `finished-tests-still-fail` right, but leaves the failure out of the three new failing cases, which the guard now catches.
- **Answers that report the failure are still spoken.** Across the ten models, every answer that says tests failed ("Implemented the CSV export endpoint, but tests failed due to date serialization.") is spoken, and no answer to the two success cases is rejected. Two vaguer answers that hint at a problem without saying tests fail ("testing remains unresolved", "date serialization needs fixing") are rejected, and the template, which says so plainly, is spoken instead. Re-scoring the 480 recorded answers of ADR-0018's run with the new guard rejects exactly the seven answers that report the CSV fix as done, and nothing else.
- **Latency isn't comparable between these two runs.** The second ran at a load average of 9–13 while a demo was recording, and p50 rose 26–58% for every model with identical answers. The guard adds no request, only a regular expression over the input.

## What review found

Six rounds of testing with hand-written messages followed. The model guard held in every round: no message made it accept wording that hid a failure. Every defect found was in the template's rules for finding and quoting the failing-tests sentence. In turn, they were a "now pass" earlier in the sentence cancelling the failure, a fixed failure or one about something other than tests counting as admitting it, a success sentence spoken twice, the word cap cutting the failure off, a summary ("41 passed, 2 failed") split at its comma, and a fallback that replaced short sentences. Each fix was re-scored against both eval runs and changed no answer.

So the template rules are the fragile part, and they're now pinned by an invariant test: any sentence of 20 words or fewer, whether a failing one or a success that mentions a failure, from every detection rule and every phrasing a round found mishandled, is spoken exactly as written. Only a longer sentence is shortened, to the clauses that hold the failure, and if the cap would still cut the failure off, "Some tests are still failing." is spoken in its place.

## Consequences

- A turn that ends with failing tests is spoken as such whichever model writes the detail, at the cost of the template's wording for models that leave it out. On these cases that's most of them, `granite4:micro` included.
- The check reads English phrasing. A message that reports failing tests in other words ("CI won't go green") isn't caught. ADR-0033 adds phrasings such as "the suite is red", and makes the guard read the whole message, not only the 4000 characters the model is shown. New phrasings found in use are added to `failing_tests` with a test and, if they matter, an eval case.
- A sentence such as "Fixed the bug that made tests fail on CI" doesn't count, because no count or "still" says the failure is current. A message that mentions an old failure with a count ("3 tests failed before the fix") would count; the template then adds that sentence, and model wording must mention the failure. That's a wordier notification, not a wrong one.
- A prompt change that asks for the failure explicitly is still open, and would be measured with this eval against the counts above.
