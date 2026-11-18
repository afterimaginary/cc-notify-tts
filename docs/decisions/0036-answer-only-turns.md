---
description: The finished prompt tells the model to start with "Explained" when nothing was changed and the message only answers a question, and to say so when it stopped partway to ask the user; with granite4:micro, the answer-only eval cases go from 1 of 3 to 2 of 3 passing, both stop-and-ask cases keep the stop, and the pass count goes from 46 to 47 of 60.
status: accepted
---

# ADR-0036: Answer-only turns in model wording

## Context

A finished turn doesn't always change anything. Asked "what does convert.py do?", Claude Code answers and stops, and the `Stop` hook fires as for any other turn. The acceptance run (ADR-0025) found `granite4:micro` wording one such answer, "convert.py provides three functions that convert temperatures between Celsius, Fahrenheit, and Kelvin.", as "Documented convert.py's three temperature conversion functions." The listener hears that work was done when none was.

The guards (ADR-0015) check names, numbers, length and failing tests (ADR-0021), and none of them can see a wrong verb. The finished prompt asked the model to "say what was done" and to "start with a past-tense verb", which invites exactly that verb. No eval case covered a turn that only answers a question.

## Decision

- **Three eval cases cover answer-only turns:** what a file does (`finished-answer-explains-code`), where a setting lives (`finished-answer-where`) and why a test is flaky, with a fix suggested but not made (`finished-answer-why`). Each forbids the verbs a model might claim, such as "updated", "added", "fixed", "documented" and "converted".
- **A turn that stops partway to ask the user must still say so.** `finished-stopped-midway` requires the stop or the question, and `finished-stopped-to-ask` is a second such case.
- **The finished prompt gains two sentences:** "If nothing was changed and the message only answers a question, start with Explained. If it stopped partway to ask the user something, say so." The rest of the prompt is unchanged, and so are the guards and the template.
- **Not a guard.** Telling an answer from a summary of work needs to understand the message. A word list for "nothing was changed" would misfire on summaries, and a check that the answer's verb appears in the message would reject many accurate paraphrases while still passing "Converted temperatures…", since "convert" appears in the answer.
- **Not a template change.** The template's detail is the message's first sentence, which already says what the answer said.

## Results

`make eval` with `granite4:micro` and `llama3.2:3b` over 60 cases: the 56 of ADR-0033, the three answer-only cases and `finished-stopped-to-ask`. Ollama 0.40.16, Apple M3 Max with 48 GiB, macOS 27.2.1. Before is `main`'s prompt (`evals/results/2026-11-18-11.md`), after is this change (`2026-11-18-12.md`). The guards are the same in both, so re-scoring either run leaves the comparison as it is.

| `granite4:micro` | Before | After |
| --- | --- | --- |
| Pass | 46/60 | 47/60 |
| Finished turns | 24/34 | 25/34 |
| Answer-only cases | 1/3 | 2/3 |
| Stop-and-ask cases | 2/2 | 2/2 |
| Invented | 3 | 2 |
| Spoken | 52/60 | 52/60 |

| Case | Before | After |
| --- | --- | --- |
| `finished-answer-explains-code` | Converted temperatures using c_to_f, f_to_c, and k_to_c functions. | Explained convert.py's three temperature conversion functions. |
| `finished-answer-where` | Updated retry limit to 3 in http.py. | Updated retry limit to 3 in config/http.py |
| `finished-answer-why` | Freezing time with freezegun would make it stable. | Explained the flaky checkout test and suggested using freezegun to freeze time. |
| `finished-stopped-midway` | Stopped database migration due to null customer_id rows. | Explained the issue with the orders table and asked about deletion or backfilling. |
| `finished-stopped-to-ask` | Stopped renaming UserService to AccountService due to external imports. | Stopped renaming UserService to AccountService due to external imports. |

- **The default model's pass count rises by one.** At temperature 0 almost every answer repeats across runs (ADR-0016): a review rerun of an earlier draft matched 58 of 59 answers, and the exception passed both times. The answer-only cases changed because of the new sentences, but the net gain is one case on 60.
- **`finished-answer-where` still claims a change.** Its message ends "Changing it there affects every outbound call.", and granite reads that as work done with every prompt tried that also covers stopped turns. A sentence asking for "Explained" even when the message says how a change could be made didn't help either.
- **Only the "Explained" sentence loses the stop.** A first draft had only the first sentence. It passed all three answer-only cases, but worded both stop-and-ask cases as "Explained …" ("Explained the conflict with 3 external plugins."), so the listener didn't hear that Claude was waiting (`2026-11-18-9.md` and `-10.md` hold the first draft's before and after). `finished-stopped-midway` now requires the stop or the question ("stopped", "asked", "should I", "waiting" and so on), and `finished-stopped-to-ask` was added so the fix isn't tuned to one case. The second sentence keeps both, at the cost of `finished-answer-where`.
- **Drafts that weren't used.** "Start with Stopped" for a stopped turn put "Stopped" in front of most finished turns (18 of 34). Without "nothing was changed", "Explained" went in front of real work ("Explained removed three unused feature flags…"). Putting the stop sentence first, or asking it to "say that it stopped", scored no better.
- **`llama3.2:3b` doesn't follow the new sentences.** It goes from 43 to 42 and still claims a change for the answer-only cases. It isn't the default (ADR-0018).

## Consequences

- With the default model, most answer-only turns are spoken as "Finished up in weather app. Explained …", which says what happened. One whose message talks about how to change something can still be worded as a change.
- A turn that stops partway to ask the user is spoken with the stop or the question. That's the same as before this change, and two eval cases now require it.
- Prompt wording moves a small model's answers on unrelated cases too. A later prompt change needs the same before-and-after run, and its failing answers read, not only the counts.
- Other models may ignore these sentences, as `llama3.2:3b` does. A user who picks another model gets the same behaviour as before for these turns.
