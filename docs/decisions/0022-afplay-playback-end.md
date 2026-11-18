---
description: The speech worker counts a sentence as played once afplay's start-up allowance (0.15 s) and the audio's length have passed, instead of waiting for afplay to exit, which cut the silence between back-to-back sentences from 1083 ms to 404 ms (p50).
status: accepted
---

# ADR-0022: Playback ends with the sound, not with afplay

## Context

ADR-0019 measured that `afplay` starts sound about 0.1 s after it's spawned (103 ms p95) and keeps running about 0.75 s after the sound ends. The speech worker plays one sentence at a time and waited for `afplay` to exit, so a sentence queued behind another started that much later than it could. A single notification isn't affected.

`afplay` has no option that avoids the wait. On a 2 s silent WAV it exits about 0.95 s after the audio's length, and about 0.65 s with `-t` set to the audio's length, so even a time limit leaves most of the delay.

"Heard" must still be recorded only once a `still_running` sentence has actually played (ADR-0013), so the player can't simply return as soon as `afplay` is spawned.

## Decision

- `AfplayPlayer.play` spawns `afplay` and waits until it exits or until 0.15 s plus the audio's length has passed, whichever comes first. 0.15 s covers `afplay`'s measured start-up (103 ms p95) with margin.
- If `afplay` is still running at that point, the sound has played: `play` returns `True` and a background thread reaps the process, logging a non-zero exit.
- If `afplay` exits first, its exit code decides, as before. `afplay` fails at start-up (an unreadable file, no output device), so a failure is still reported before `play` returns and "heard" isn't recorded for it.
- If the WAV's length can't be read, `play` waits for `afplay` to exit, as before.
- The timeline's `ended` mark now means the sound has finished, not that `afplay` exited. `playing` to `ended` less the audio's length is now the 0.15 s allowance rather than `afplay`'s start-up and exit.

The next sentence's `afplay` can start while the previous one is still exiting. Measured with `afplay -d` under a pseudo-terminal, 10 runs each, start-up was 79 ms median (102 ms max) alone and 76 ms (92 ms max) with another `afplay` still running, so the overlap costs nothing.

## Method

`uv run python -m bench.latency gap --runs 20` speaks four sentences in turn through `Speaker`, with real Kokoro (int8, ADR-0012) and real `afplay`, but each WAV is overwritten with silence of the same length, so nothing is heard. Each sentence's sound starts the same start-up after its `playing` mark, so the silence between two sentences is the second's `playing` less the first's `playing` and audio length. Its parts are how long the player held on past the audio, then the next sentence's synthesis. Run on 2026-11-18: Apple M3 Max, 48 GiB, macOS 27.2.1, Python 3.13.13, `kokoro-onnx` 0.6.1, `onnxruntime` 1.32.0, other work running (load average 3.6 before, 9.5 after).

## Results

20 back-to-back pairs, ms, p50 / p95 / max:

| Measure | Waiting for `afplay` to exit | Waiting for the sound |
| - | - | - |
| **Silence between sentences** | **1083 / 1147 / 1151** | **404 / 469 / 713** |
| Player held past the audio | 813 / 907 / 919 | 158 / 162 / 162 |
| Next sentence's synthesis | 245 / 277 / 277 | 246 / 310 / 551 |

## Consequences

- A queued sentence now follows about 0.4 s after the one before it ends, down from about 1.1 s. What's left is the next sentence's synthesis (about 0.25 s for these sentences) and the 0.15 s allowance less `afplay`'s actual start-up.
- If `afplay` ever takes longer than 0.15 s to start, `play` returns slightly before the sound ends. The next sentence still has to be synthesized first, and the next `afplay` has its own start-up, so the two don't overlap unless start-up runs past about 0.5 s; "heard" would be recorded only that start-up less 0.15 s early.
- An `afplay` that hangs after start-up no longer blocks the speech worker; it's left running and reaped when it exits.
- Synthesizing the next sentence while the current one plays would remove most of the remaining silence, but ADR-0005 kept all compute ahead of playback, so it isn't done here.
