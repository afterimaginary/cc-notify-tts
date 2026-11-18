---
description: The finished prompt tells the model to start with "Explained" when nothing was changed and the message only answers a question, so an answer isn't worded as work done; with granite4:micro, the three answer-only eval cases go from 1 of 3 to 3 of 3 passing and the pass count from 45 to 46 of 59.
status: accepted
---

# ADR-0036: Answer-only turns in model wording

## Context

A finished turn doesn't always change anything. Asked "what does convert.py do?", Claude Code answers and stops, and the `Stop` hook fires as for any other turn. The acceptance run (ADR-0025) found `granite4:micro` wording one such answer, "convert.py provides three functions that convert temperatures between Celsius, Fahrenheit, and Kelvin.", as "Documented convert.py's three temperature conversion functions." The listener hears that work was done when none was.

The guards (ADR-0015) check names, numbers, length and failing tests (ADR-0021), and none of them can see a wrong verb. The finished prompt asked the model to "say what was done" and to "start with a past-tense verb", which invites exactly that verb. No eval case covered a turn that only answers a question.

## Decision

- **Three eval cases cover answer-only turns:** what a file does (`finished-answer-explains-code`), where a setting lives (`finished-answer-where`) and why a test is flaky, with a fix suggested but not made (`finished-answer-why`). Each forbids the verbs a model might claim, such as "updated", "added", "fixed", "documented" and "converted".
- **The finished prompt gains one sentence:** "If nothing was changed and the message only answers a question, start with Explained." The rest of the prompt is unchanged, and so are the guards and the template.
- **Not a guard.** Telling an answer from a summary of work needs to understand the message. A word list for "nothing was changed" would misfire on summaries, and a check that the answer's verb appears in the message would reject many accurate paraphrases while still passing "Converted temperatures…", since "convert" appears in the answer.
- **Not a template change.** The template's detail is the message's first sentence, which already says what the answer said.

## Results

`make eval` with `granite4:micro` and `llama3.2:3b` over 59 cases, the 56 of ADR-0033 and the three above, on Ollama 0.40.16, Apple M3 Max with 48 GiB, macOS 27.2.1. Before is `main`'s prompt (`evals/results/2026-11-18-9.md`), after is this change (`2026-11-18-10.md`). The guards are the same in both, so re-scoring either run leaves the comparison as it is.

| `granite4:micro` | Before | After |
| --- | --- | --- |
| Pass | 45/59 | 46/59 |
| Finished turns | 23/33 | 24/33 |
| Answer-only cases | 1/3 | 3/3 |
| Invented | 3 | 1 |
| Spoken | 51/59 | 50/59 |

After, the three answers are "Explained convert.py's three temperature conversion functions.", "Explained retry limit in config/http.py and client.py's session behavior." and "Explained the flaky checkout test issue and suggested using freezegun to freeze time." Before, they were "Converted temperatures using…", "Updated retry limit to 3 in http.py." and "Freezing time with freezegun would make it stable." (which passed).

- **The default model's pass count rises by one.** At temperature 0 the answers repeat exactly (ADR-0016), so the difference is real, but it is one case on 59.
- **One case that passed now falls back to the template.** For `finished-tests-still-fail`, granite drops "but 2 of the 31 tests still fail" from its answer, and the failing-tests guard rejects it, so the template, which says tests still fail, is spoken. Nothing wrong is spoken.
- **"Explained" stays on answer-only turns.** Granite uses it for the three answer-only cases and for `finished-stopped-midway`, a turn that stopped before changing anything ("Explained the issue with 12 null customer_id rows in orders table."). A draft that said only "If the message only answers a question, start with Explained." scored one more case, but put "Explained" in front of real work ("Explained removed three unused feature flags…"), which the eval's lists don't catch, so it wasn't used.
- **`llama3.2:3b` doesn't follow the new sentence.** It goes from 43 to 42: it still claims a change for all three answer-only cases, and now fails `finished-answer-explains-code` too ("Converted temperatures functions in convert.py…"). It isn't the default (ADR-0018).

## Consequences

- An answer-only turn with the default model is spoken as "Finished up in weather app. Explained …", which says what happened.
- Prompt wording moves a small model's answers on unrelated cases too. A later prompt change needs the same before-and-after run, and its failing answers read, not only the counts.
- Other models may ignore the sentence, as `llama3.2:3b` does. A user who picks another model gets the same behaviour as before for these turns.
