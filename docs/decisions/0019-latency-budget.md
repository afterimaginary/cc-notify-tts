---
description: Every event logs when it reached each stage from hook to playback; measured on a real session, the first sound comes about 0.75 s after the hook fires with templates and about 1.0 s with granite4:micro (p95), each hook process takes at most 58 ms p95, and the idle daemon holds about 450 MiB, so both of the brief's latency targets hold.
status: accepted
---

# ADR-0019: The measured latency budget

## Context

The brief sets two latency targets: a finished turn is spoken within 3 s of the turn ending, and no hook adds more than 100 ms (p95) to a Claude Code event. Earlier decisions measured pieces in isolation: the hook shim's launch (ADR-0004, ADR-0014), Kokoro's synthesis (ADR-0005), and Ollama's wording time (ADR-0003, ADR-0018). Nothing measured the whole path, so how the pieces add up, and what sits between them, was an estimate.

## Decision

### Instrumentation

- **Every event carries a timeline**, a mark of when it reached each stage, and the daemon logs it as one `timing` line in `daemon.log` when the event ends, however it ends. The format is in the [daemon protocol](../specs/daemon-protocol.md#lifetime). The stages:

| Mark | When | Set by |
| - | - | - |
| `hook` | the hook process's Python started (`fired_at`) | shim |
| `sent` | the shim sent the event (new `sent_at` field) | shim |
| `received` | the daemon accepted it | socket thread |
| `dequeued` | the event worker took it from the inbox, after the 100 ms ordering hold (ADR-0007) | event worker |
| `gated` | classified and through the gate | event worker |
| `worded` | the sentence is written (template or model) and queued | event worker |
| `synthesizing` | the speech worker took it from the queue | speech worker |
| `synthesized` | the whole sentence is synthesized to a WAV file | speech worker |
| `playing` | `afplay` spawned | speech worker |
| `ended` | `afplay` exited (the sound finished, since ADR-0022) | speech worker |

- `dequeued` and `synthesizing` go beyond the issue's list so that the ordering hold and any wait behind earlier speech show up as their own stages rather than inside the gate and synthesis. `ended`, with the synthesized length, bounds how long `afplay` takes to start. Since ADR-0022, `ended` comes once the sound has finished, so it no longer does.
- The shim stamps `sent` before it tries the socket, so when nothing answers and it starts the daemon, `sent → received` includes the daemon's start-up. In an everyday `daemon.log` that shows as a large maximum in that row.
- All marks are wall-clock `time.time()`, the clock the shim already uses for `fired_at`, so marks from the hook process and the daemon compare directly.
- The log is always on. A line is under 500 bytes per event, against the log's 1 MB rotation.
- **`bench/latency.py`** reads the lines back (`summarize`, for any daemon log, including a user's own), drives a measured session (`run`, also `make latency`), and times `afplay` on its own (`afplay`). Its docstring has the method; `run` plays sound, so it stays out of `ready` and `verify`.

### Method

`python -m bench.latency run` with a throwaway `HOME` laid out as `setup` leaves it: the plugin's real hook commands from `hooks/hooks.json` run through `/bin/sh -c` as Claude Code runs them, the real daemon starts on demand from the project's environment with real Kokoro (int8, ADR-0012) and `afplay`, and the config speaks every event (`always`), so presence doesn't decide what's measured. The session starts with `SessionStart`, waits for the speech model (and, with a model, for Ollama to load it), then runs one warm-up round and 30 measured rounds of `UserPromptSubmit`, `PermissionRequest` 0.5 s later, and `Stop` once the permission sentence has played, six seconds apart so the 5 s duplicate window never drops one. It ends with `SessionEnd`, waits 15 s and reads the daemon's resident memory with `ps`.

Run once with templates only and once with `granite4:micro` (ADR-0018) on 2026-11-18: Apple M3 Max, 48 GiB, macOS 27.2.1, Python 3.13.13, `kokoro-onnx` 0.6.1, `onnxruntime` 1.32.0, Ollama 0.40.16, other work running (load average 3–4). Sentences were 3.9–6.9 s long as spoken.

## Results

### Stage by stage

p50 / p95 in ms, over the 30 measured rounds (60 spoken events per run; the first three rows also count the 31 events that are never spoken).

| Stage | Templates | `granite4:micro` |
| - | - | - |
| hook → sent: the shim's imports, parsing, building the request | 10.7 / 12.8 | 10.9 / 12.9 |
| sent → received: the socket (and, on the event that starts the daemon, its start) | 0.3 / 0.5 | 0.4 / 0.5 |
| received → dequeued: the ordering hold | 105.8 / 110.1 | 105.0 / 105.1 |
| dequeued → gated: classification, session state, config, gate | 1.3 / 2.2 | 1.4 / 2.1 |
| gated → worded: wording | 0.2 / 0.3 | 186.2 / 213.1 |
| worded → synthesizing: queue wait | 0.0 / 0.1 | 0.1 / 0.1 |
| synthesizing → synthesized: Kokoro | 503.6 / 515.7 | 464.9 / 564.9 |
| synthesized → playing: spawning `afplay` | 0.2 / 0.3 | 0.2 / 0.3 |
| **hook → playing** | **618.4 / 637.4** (max 751) | **745.7 / 890.6** (max 955) |

With the model, 59 of the 60 sentences used its detail; one fell back to the template. Its slowest wording took 305 ms, against the 1 s timeout.

### After `playing`: afplay

`afplay` gives no signal when sound starts. `python -m bench.latency afplay` runs `afplay -d` on a 50 ms silent WAV under a pseudo-terminal, 20 times, so its debug lines arrive as they're printed. Its last line, once the audio queue is set up, comes **94 ms p50, 103 ms p95** after spawn; it exits after 905 ms p50, 916 ms p95. In the measured sessions, `playing` to `ended` less the audio's length was 824 ms p50, 895 ms p95 in both runs. So `afplay` starts sound about 0.1 s after the `playing` mark and then lingers about 0.75 s after the sound ends.

### Hook processes

Wall time per hook process, spawn to exit, ms, p50 / p95 / max:

| Hook | Templates | `granite4:micro` |
| - | - | - |
| `UserPromptSubmit` (30) | 44.7 / 57.6 / 70.9 | 42.8 / 52.9 / 54.3 |
| `PermissionRequest` (30) | 47.1 / 51.4 / 51.8 | 44.1 / 50.5 / 52.3 |
| `Stop` (30) | 41.4 / 54.7 / 56.1 | 41.0 / 54.2 / 57.0 |
| `SessionStart`, starting the daemon (1) | 90.1 | 81.9 |

These are 15–25 ms slower than ADR-0014's 23 / 31 ms, which used a stand-in daemon answering from the measuring process and no speech running nearby; the cause wasn't investigated. The event that starts the daemon is one sample per run here; ADR-0014's 116 ms p95 over 50 runs remains the figure for it, and #46 tracks bringing it under 100 ms.

### Memory

The daemon's resident memory was 250–263 MiB once the speech model had loaded and warmed, and **442–457 MiB idle** 15 s after the last sentence. A two-round trial run read 464 MiB after four sentences, so the growth comes in the first few syntheses (`onnxruntime`'s buffers) and then holds rather than climbing with use. Ollama holds `granite4:micro` separately, 2.18 GiB while a session is open (ADR-0018); after `SessionEnd` it was unloaded, and `ollama ps` listed nothing after each run.

## The targets

- **Spoken within 3 s of the turn ending: holds.** From the hook's start to the first sound is `hook → playing` plus `afplay`'s start-up: about 0.74 s p95 with templates (637 + 103 ms) and 0.99 s with `granite4:micro` (891 + 103 ms). Two parts are outside the measurement and both are small: the hook process's interpreter start before `fired_at` (less than the whole process's 58 ms p95), and the audio device's own output latency. How long Claude Code takes to run the `Stop` hook after a turn ends isn't measured either. The margin is about 2 s with the model.
- **No hook adds more than 100 ms p95: holds.** Every steady-state hook was at most 58 ms p95 (71 ms max), and hooks are async (ADR-0001), so Claude Code doesn't wait for them. The cold-start event is #46.
- No new target missed, so no new issue. The one miss, the cold-start hook at 116 ms p95 (ADR-0014), is tracked in #46.

## Consequences

- Synthesis is most of the budget, about 0.5 s; then the model's wording, about 0.2 s; then the 100 ms ordering hold and `afplay`'s 0.1 s start-up. Everything else is a few milliseconds.
- `afplay` lingers about 0.75 s after each sentence, and the speech worker waits for it, so a second sentence queued behind a first starts that much later than it could. It doesn't affect a single notification. ADR-0022 stops waiting for it, and `ended` now marks the end of the sound rather than `afplay`'s exit.
- The daemon's idle cost is about 450 MiB of resident memory, plus 2.2 GiB in Ollama while a session is open with model wording.
- Anyone can measure their own machine: `uv run python -m bench.latency summarize` on their `daemon.log`, or `make latency [MODEL=…]`.
