---
description: The wording eval sends each hand-written case once, at temperature 0, with the daemon's own request, prompt and guards, and scores whether the answer is spoken, keeps the case's required detail, avoids its forbidden content and fits the prompt's length; at temperature 0 the answers repeat exactly across runs, so any difference in counts is real, while latency has a noise band of about ±5% at a similar load and is compared only between models in the same run.
status: accepted
---

# ADR-0016: The wording eval and its noise band

## Context

The daemon may let a local Ollama model write a notification's detail behind guards (ADR-0015). Which model, if any, should be the default is decided from evidence (#19), and that evidence has to be reproducible: the brief's "done" list asks for `make eval` to reproduce the published table within run-to-run noise. That needs three things settled first: what the eval sends, how an answer is judged without a person reading it, and how large the run-to-run noise is, so a difference between two models can be told apart from it.

## Decision

- **Send exactly what the daemon sends.** Each case is a synthetic hook payload that goes through the daemon's classification, `request_for` and `guard`, with the client's own options (`num_ctx` 2048, temperature 0, at most 40 output tokens, thinking off). The eval scores the sentence a user would hear, not a variant of it.
- **Hand-written cases, each naming its required detail and its forbidden content.** 48 cases: 22 finished turns, 12 questions and 14 permission requests, with traps for numbers, names, long tool inputs, final messages past the 4000-character input cap, several sentences, markdown and lead-ins. `required` names the fact a listener most needs (that 2 tests still fail, which invoice is refunded), not just a topic word, because a vague answer ("Edited the nginx configuration file with new settings") is accurate but tells the listener nothing. `forbidden` lists plausible mistakes the name-and-number guard can't see: a step considered but not taken, a claim the input never makes, a count that's wrong in words.
- **Four deterministic checks per answer:** spoken (passes the guards), detail kept, nothing invented, and within the length the prompt asks for (15 words, or 10 for a permission). An answer passes when all four hold. Lead-ins ("Here's what was done: …") are counted separately, spoken and rejected, so the guard can be tightened from evidence.
- **One warm-up request, recorded but not scored, then every case once.** Each case's prompt differs, so the prompt cache only helps the shared system prompt, as it does in use (ADR-0003). Latency is the client's wall time per request; p50 and p95 are nearest-rank over all cases.
- **Record what the numbers depend on:** model digest, Ollama version, date, machine class (chip, memory, macOS version; never a host name), the load average when each model's run started, and a hash of the case files. Resident memory is `size` from `/api/ps` while the model is loaded.
- **Results are dated Markdown with raw JSON beside it**, in `evals/results/`, never overwritten; `evals.compare` diffs two runs model by model.
- **Model runs stay out of `ready` and `verify`.** Those test the scoring, the case files and the report with a stand-in client.

## Noise band

`llama3.2:3b` (digest `a80c4f17acd5…`), Ollama 0.40.16, Apple M3 Max with 48 GiB, macOS 27.2.1, with other work running (load average 4.5–5.2). Six runs of the same 48 prompts, each loading the model afresh: four while the cases' `required` and `forbidden` lists were still being settled, and the two committed with the final lists (`evals/results/2026-11-18.md` and `2026-11-18-2.md`). The prompts were the same in all six.

| | Spread over six runs |
| --- | --- |
| Answers that differ between any two runs | 0 of 48 |
| Pass, spoken, kept, invented, over length, lead-ins | identical in runs scored with the same lists (40, 48, 42, 2, 0, 0 in the committed two) |
| p50 latency | 162–168 ms (±2% around the middle) |
| p95 latency | 214–218 ms (±1%) |
| Warm-up | 210–230 ms |
| Resident memory | 2.16 GiB every time |

- **At temperature 0 the answers repeat exactly**, through a fresh load each time. A difference in pass counts between two models is therefore a real difference on this case set, not run noise. It is still a difference on 48 cases: one or two cases either way says less than it seems, and #19 should read the failing answers, not only the counts.
- **Latency noise is about ±5% at a similar load**, allowing some margin over the ±2% seen. ADR-0003 found it moves much more with load: `llama3.2:1b`'s short-input p50 was 69 ms at a load average of 4–8 and 144 ms at about 10. So compare latency only between models in the same run, or between runs whose load averages are close, and treat differences inside ±5% as equal.
- **Memory doesn't vary** for a given digest and context length.
- **The answers could change with a different Ollama version or machine**, since both change the arithmetic. A comparison across either is a new baseline, not a test of noise; `evals.compare` prints both when they differ.

## Consequences

- `make eval MODELS=…` gives #19 a pass rate, failing answers, latency and memory for each candidate, with nothing left to judgement except reading why answers failed.
- The checks are only as good as the cases' `required` and `forbidden` lists. A first version that accepted any topic word passed 47 of 48 `llama3.2:3b` answers, including "Reset the repository to the last commit three times" for `git reset --hard HEAD~3`. Tightening `required` to the fact that matters brought it to 40, every remaining failure a real one. New cases need the same care.
- `llama3.2:3b` produced no lead-ins on this set. The count is in every report, so the guard can be tightened if another model does.
- No request in these runs exceeded the default 1 s wording timeout. The slowest, 0.61 s in each committed run, was the final message cut at the 4000-character cap, as ADR-0003 predicted for a 3 B model with long input.
