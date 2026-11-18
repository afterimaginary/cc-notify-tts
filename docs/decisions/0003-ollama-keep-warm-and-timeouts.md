---
description: Send num_ctx 2048 and keep_alive 1h on every Ollama request, cap the input at 4000 characters, keep the model loaded with a matching load-only ping, and wait at most 1 s for wording only when /api/ps shows the model loaded; otherwise speak the template and load the model in the background.
status: accepted
---

# ADR-0003: Keep the wording model warm, with short timeouts

## Context

The daemon may ask a local Ollama model to write the detail of a notification. That has to fit inside the 3 s from the end of a turn to speech, next to speech synthesis, and a slow answer must fall back to the template rather than delay the notification.

Notifications can be hours apart, and Ollama unloads a model after `keep_alive` runs out (5 minutes by default). A notifier that relies on the default pays a model load on almost every notification. Ollama also sizes the model's memory by its context length, which defaults to far more than a one-sentence prompt needs.

This decision needs the latency of a warm, a just-loaded and an evicted model, for short and long input, resident memory at different context lengths, and what keeps a model loaded. Which model writes the best sentence is a separate question for the eval.

## Method

`spikes/ollama_latency/run_spike.py` measures it through Ollama's HTTP API. Its README has the method and the command to reproduce it.

Every request is a chat request: a system line asking for one sentence of at most 15 words, and a user message describing a finished turn, at temperature 0 with at most 40 output tokens. The short message is one sentence (about 100 tokens evaluated). The long one quotes a 3283-character final message after a multi-step task (about 800 tokens evaluated), the kind of `last_assistant_message` a finished turn carries (ADR-0001). The user message starts with a request number, so Ollama reuses the cached system prompt but evaluates the rest, as it would for a real notification. Models that can think get `think: false`. Wall times are measured in the client.

Four models between 1 and 3.2 B parameters:

| Model | Digest | Parameters | Quantization | Disk |
| --- | --- | --- | --- | --- |
| `llama3.2:1b` | `baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878` | 1.2 B | Q8_0 | 1.23 GiB |
| `gemma3:1b` | `97558784bdc9175075a72655bf360b15da17df6b3b9e5182af9f8c0c8e58ba7c` | 1.0 B | Q4_K_M | 0.75 GiB |
| `qwen3:1.7b` | `8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7` | 2.0 B | Q4_K_M | 1.27 GiB |
| `llama3.2:3b` | `a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72` | 3.2 B | Q4_K_M | 1.88 GiB |

Ollama 0.40.16 (the macOS app, server defaults: `OLLAMA_KEEP_ALIVE` 5m, `OLLAMA_CONTEXT_LENGTH` unset) on an Apple M3 Max with 48 GiB, macOS 27.2.1. Other work was running, with a load average of 4–8.

## Results

### Latency

Cold is a request right after the model was unloaded with `keep_alive: 0`, three times; the first is the model's first load in the run. Evicted is a request right after Ollama unloaded the model by itself when a 5 s `keep_alive` ran out, three times. Both use the short message. Warm is back-to-back requests: 30 short, then 15 long. All at `num_ctx` 2048. Milliseconds.

| Model | Cold, first | Cold, median | Of which loading | Evicted, median | Warm short p50 | p95 | Warm long p50 | p95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `llama3.2:1b` | 1193 | 685 | 541 | 682 | 69 | 102 | 269 | 326 |
| `gemma3:1b` | 928 | 719 | 535 | 720 | 131 | 138 | 349 | 392 |
| `qwen3:1.7b` | 1229 | 730 | 534 | 744 | 140 | 210 | 422 | 440 |
| `llama3.2:3b` | 1532 | 773 | 545 | 785 | 178 | 180 | 573 | 592 |

- **A load costs 0.5–0.6 s, whatever the model size, and a first load up to 1.5 s.** Cold and evicted requests take 0.7–0.8 s; the slowest of all 24 was 1532 ms. Eviction is just a cold load: the request after Ollama evicts a model costs the same as one after an explicit unload.
- **Warm, a short input takes 0.07–0.2 s and a long one 0.27–0.6 s.** Evaluating the input dominates: each extra input token costs about 0.3 ms on the 1 B models, 0.4 ms on `qwen3:1.7b` and 0.6 ms on the 3 B. The slowest long request of all 60 took 595 ms.
- **Results move with machine load.** An earlier run at a load average of about 10 had `llama3.2:1b` at a short p50 of 144 ms and p95 of 205 ms. The other models' short p50 matched within 5 ms and p95 within 55 ms, and cold and evicted medians within 40 ms. A model's first cold load varied most: 704 ms against 1193 ms for `llama3.2:1b`.
- **Not measured: a load from disk.** The model files were in the OS page cache for every load, because dropping that cache needs root. A load after a reboot or under memory pressure will be slower.

### Context length and memory

`size` and `context_length` as reported by `/api/ps` (all of it in GPU memory). Without `num_ctx`, Ollama chose a 32768-token context on this machine; its default scales with GPU memory, from 4096 up to 262144 tokens.

| Model | 2048 | 8192 | Ollama default (32768) |
| --- | --- | --- | --- |
| `llama3.2:1b` | 1.35 GiB | 1.61 GiB | 2.41 GiB |
| `gemma3:1b` | 0.82 GiB | 0.89 GiB | 0.94 GiB |
| `qwen3:1.7b` | 1.13 GiB | 1.85 GiB | 4.60 GiB |
| `llama3.2:3b` | 2.16 GiB | 2.90 GiB | 5.63 GiB |

- **`num_ctx` 2048 cuts resident memory by up to 3.5 GiB.** Short-input latency was the same at all three sizes, within the run's noise.
- **Changing `num_ctx` reloads the model.** The first request with a different context cost 0.6–1.0 s, like a cold load.

### What keeps a model loaded

| Check | Result |
| --- | --- |
| Request with `keep_alive: 1h` | Model expires in 3600 s |
| Reading `/api/ps` and `/api/show` 3 s later | Expiry not extended (3597 s left) |
| Request with no `keep_alive` | Keeps the loaded model's current duration (3600 s after a 1 h request, 30 s after a 30 s one). Only a model loaded by such a request gets the server's 5 minutes. |
| Request with a shorter `keep_alive` | Shortens it: a 30 s request after a 1 h one leaves 30 s |
| Load-only ping (`/api/generate`, no prompt) **without** `num_ctx` | Reloads the model at Ollama's default context: 0.54–0.81 s, and resident memory grows to the default-context size |
| The same ping **with** `num_ctx` 2048 | 1–2 ms, model untouched, expiry reset to 1 h |
| `GET /api/ps` | 0.2–0.4 ms (p50), 0.2–0.5 ms (p95) |

## Decision

**Wording requests:**

- Send `num_ctx: 2048` on every request and every ping. A request or ping that leaves `num_ctx` out reloads the model at a context that costs up to 3.5 GiB more.
- **Cap the input.** Send at most the last 4000 characters of the final message (about 1000 tokens). With the system prompt, the frame and 40 output tokens, that fits in 2048 tokens, and keeps warm latency near the long-input figures above. The end of a final message is usually its summary, so the cap keeps the part worth speaking.
- Send `keep_alive: "1h"` on every request.

**Keep-warm**, while the daemon runs and wording is enabled:

- When the daemon starts, load the model with a load-only `/api/generate` request carrying the same `num_ctx` and `keep_alive: "1h"`.
- Repeat that ping every 45 minutes, inside the 1 h `keep_alive`. A loaded model makes it cost 1–2 ms. Another client that shortens the expiry is corrected by the next ping.
- When the daemon exits on its idle timeout, unload the model with `keep_alive: 0`, so nothing stays resident after Claude Code stops being used.

**Timeouts:**

- Before each wording request, read `/api/ps` (under 1 ms).
- If it lists the model with a 2048 context, send the request with a **1.0 s timeout**. That is about 1.7 times the slowest long-input request (595 ms, `llama3.2:3b`, 3283 characters) and 4.5 times the worst short-input p95 seen (223 ms, at a load average of 10), and leaves most of the 3 s budget for speech. On timeout or error, speak the template.
- Otherwise, don't wait for a load. Speak the template now, and send the load-only ping in the background with a **30 s timeout**, so the next notification finds the model warm. A load from cache costs 0.7–1.5 s before the sentence is even written, and a load from disk wasn't measured. Neither fits next to speech synthesis in 3 s.

## Consequences

- **Keeping the model warm holds 0.8–2.2 GiB** of GPU memory at `num_ctx` 2048 for these models, for as long as the daemon runs. The eval picks the model and reports its resident memory.
- The first notification after the daemon starts, after another client changes the model's context, or after Ollama evicts it under memory pressure, is spoken with template wording. The `/api/ps` check sees all three, and the background ping makes the next one warm.
- Another client using the same model without `num_ctx` shares one loaded copy and reloads it at its own context; the daemon notices through `/api/ps`. Unloading on exit also unloads it for that client, which then pays one load. Other models the user loads in Ollama are not affected.
- The 1.0 s timeout leaves the least headroom for a 3 B model with long input under heavy load. Input at the 4000-character cap, or text dense with paths and code that tokenizes into more tokens per character, could put such a model's p95 over 1.0 s; the 1 B models stay under 0.5 s either way. If the eval picks a model of that size, its latency figures should confirm the timeout, and end-to-end timing in milestone 3 should confirm it with speech synthesis alongside.
- The wording module owns the guards, the prompt and the cap. Its eval should keep the request-number technique or vary real payloads, so the prompt cache doesn't flatter warm latency: with an identical prompt every time, prompt evaluation dropped to about 5 ms.
