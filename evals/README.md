# Wording eval

How well a local Ollama model writes the detail of a spoken notification: whether the daemon would speak its answer, whether the answer keeps the detail that matters, whether it says anything wrong, how long it takes, and how much memory the model holds. The method and the run-to-run noise are in ADR-0016.

## Run it

Ollama must be running, with the models already pulled. The eval never pulls a model.

```sh
make eval MODELS="llama3.2:1b gemma3:1b"
```

Each model is scored in turn and the results go to `evals/results/<date>.md`, with the raw answers beside it in `<date>.json`. A second run on the same day writes `<date>-2.md`, and so on, so earlier results are never overwritten.

To compare two runs, for example the same model on two days:

```sh
uv run python -m evals.compare evals/results/2026-11-18.json evals/results/2026-11-18-2.json
```

## What a run does

For each model:

1. Load it at the daemon's context length (`num_ctx` 2048), then send one warm-up request that isn't a case. The warm-up's time is recorded and not scored.
2. Send every case once, with exactly the request the daemon would send: the same prompt, temperature 0, at most 40 output tokens, thinking off. Each request's latency is the client's wall time.
3. Read the model's digest, resident memory and context length from Ollama's `/api/ps` while it's loaded. Unload it, unless it was already loaded before the run.

The results also record the date, the Ollama version, the machine class (chip, memory and macOS version, never the host name), the load average when each model's run started, and a hash of the case files.

## Checks

Every answer goes through the daemon's own guards (`model_wording.guard`, ADR-0015). Then:

| Check | Passes when |
| --- | --- |
| Spoken | The guards accept the answer, so the daemon would speak it instead of the template's detail. |
| Detail kept | The spoken detail contains one alternative from each of the case's `required` groups. |
| Nothing invented | The spoken detail contains none of the case's `forbidden` phrases. The guards already reject names and numbers missing from the input; `forbidden` catches wrong claims they can't see, such as "added Redis" when the input says Redis wasn't used. |
| Length | The detail (without code's own "And one more question.") is no longer than the prompt asks: 15 words for a finished turn or a question, 10 for a permission. The guards allow a little more. |

An answer **passes** when it's spoken, keeps the detail, invents nothing and fits the length. The report also counts lead-ins ("Here's what was done: …"): spoken details that open by introducing themselves, and answers the guards rejected for being nothing but one.

Matching ignores case, curly quotes and hyphens ("force-push" matches `force push`) and works on whole words. A phrase ending in `*` matches any word that starts with it: `retr*` matches "retry" and "retries".

## Cases

`cases/finished.json`, `cases/question.json` and `cases/permission.json` hold hand-written synthetic hook payloads, one file per event. They're not taken from real sessions, and every path is under `/Users/dev/src/`. Each case:

```json
{
  "id": "finished-backoff",
  "traps": ["numbers"],
  "payload": {
    "hook_event_name": "Stop",
    "cwd": "/Users/dev/src/weather-app",
    "last_assistant_message": "Added exponential backoff to the forecast fetcher…"
  },
  "required": [["backoff", "back off", "retr*"]],
  "forbidden": ["redis", "50 tests"]
}
```

- `payload` is a hook payload with the fields that matter. The loader adds a `session_id` and runs it through the daemon's own classification, which must produce the file's event. A long `last_assistant_message` may be written as a list of lines.
- `required` lists groups of alternatives. Pick the fact a listener most needs, not any word from the input.
- `forbidden` lists plausible but wrong content: a number off by one, a step that was considered but not taken, a claim the input doesn't make. Every case needs at least one.
- `traps` names what makes the case hard: `numbers`, `names`, `long input`, `multi-sentence`, `lead-in` and so on.

`make ready` checks that every case loads, classifies as its file's event and lists both kinds of content, and tests the scoring itself. It never runs a model.
