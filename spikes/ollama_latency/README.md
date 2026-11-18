# Spike: Ollama latency and memory

Answers the questions behind ADR-0003. How long does a small local model take to write one sentence when it's warm, when it has to load, and after Ollama has evicted it? How much memory does it hold while loaded, and how does `num_ctx` change that? What keeps a model loaded between notifications that can be hours apart?

`run_spike.py` measures all of it through Ollama's HTTP API and writes three files to the output directory:

| File | Contents |
| --- | --- |
| `samples.jsonl` | One line per request or check, with wall time and Ollama's own timings |
| `results.json` | Everything, grouped by model |
| `summary.md` | The tables ADR-0003 quotes |

## Run it

```sh
for m in llama3.2:1b gemma3:1b qwen3:1.7b llama3.2:3b; do ollama pull "$m"; done
uv run python spikes/ollama_latency/run_spike.py /tmp/ollama-spike
```

Pass model names after the directory to measure other models. It needs a running Ollama on `127.0.0.1:11434`; set `OLLAMA_HOST_URL` to use another address. A run of four models takes a few minutes.

It unloads every model it measured before it exits, even on failure, and doesn't touch models it didn't measure. To check nothing is left loaded:

```sh
ollama ps
```

The script is not part of `make ready` or `make verify`, because it needs Ollama and the models.

## What each phase does

Every request is the same fixed chat prompt (a finished turn to describe in one sentence), with temperature 0, `num_predict` 40 and `num_ctx` 2048 unless the phase says otherwise. Models that can think get `think: false`.

| Phase | What it measures |
| --- | --- |
| cold | Three requests, each after unloading the model with `keep_alive: 0`. The first is the first load in the run; the files may or may not be in the OS page cache. |
| warm | 30 back-to-back requests. p50 and p95 of wall time. |
| num_ctx | For `num_ctx` 2048, 8192 and Ollama's default (not sent): the request that switches context, 10 warm requests, then `size`, `size_vram` and `context_length` from `/api/ps`. |
| evicted | Three times: a request with `keep_alive: 5s`, wait until `/api/ps` no longer lists the model, then time the next request. |
| keep-warm | `expires_at` after a request with `keep_alive: 1h`; after reading `/api/ps` and `/api/show`; after a request with no `keep_alive`. Then a load-only ping (`/api/generate` with no prompt), with and without `num_ctx`, and the context the model is left with. |

Wall times are measured in the client and include HTTP overhead. Other work on the machine affects them, so the summary records the load average at the start.
