---
description: The daemon loads Kokoro-82M (fp32, kokoro-onnx) once at start, warms it with one synthesis and keeps it loaded until it exits, and synthesizes each sentence whole before any audio plays; nearby playback cost at most 5% on this hardware.
status: accepted
---

# ADR-0005: Kokoro latency and what follows for the speech pipeline

## Context

Every spoken notification runs Kokoro-82M through `kokoro-onnx` before anything is heard, so its latency is most of the budget in "spoken within 3 s of the turn ending". The pipeline needs answers to three questions:

1. How slow is a cold start (a freshly started daemon) compared with a warm model, and is it worth keeping the model loaded?
2. How does latency grow with sentence length, for the 5–20-word sentences the notifier speaks?
3. Does audio playing nearby slow synthesis down? On Apple silicon, the scheduler can move a process to the efficiency cores, and a process that plays audio, or sits next to one that does, is a plausible trigger. If it happens, the daemon must never synthesize while anything plays.

It also has to settle which model files `setup` downloads, from where, and how much memory the daemon holds once they're loaded.

## Method

`spikes/kokoro_latency/kokoro_latency.py` measures all of it in one run. Its README has the details and the commands to reproduce it.

- **Cold start:** three fresh processes each import `kokoro_onnx`, load the model, and synthesize the same 12-word sentence twice.
- **Warm:** one process with the model loaded synthesizes 15 notification-shaped sentences, three each of 5, 8, 12, 16 and 20 words, with voice `af_heart` at speed 1.0. The baseline makes 7 passes (21 samples per length); each other phase makes 3 (9 per length).
- **Playback:** the warm runs are repeated while `afplay` loops a long clip, once started by the measuring process itself (the way the daemon will play speech) and once by a separate `launchctl submit` job outside its process tree (like other audio on the Mac). A third phase plays a short clip to the end before each synthesis. A final baseline catches any slowdown that lingers. `coreaudiod` held a power assertion during both looping phases, so audio really was playing.
- **Memory:** resident set size from `ps`, once loaded, after the warm runs and after a long paragraph.

Machine: Apple M3 Max (12 performance and 4 efficiency cores), 48 GB, macOS 27.2.1, Python 3.13, `kokoro-onnx` 0.6.1, `onnxruntime` 1.32.0 on its CPU provider (the one `kokoro-onnx` picks by default; it also lists CoreML). Other work was running (load average about 10), as it would in normal use.

The main runs were started from a shell. ADR-0002 found that a daemon started from a hook is scheduled the same way as a process started from a shell, so these are the numbers the daemon will see. One extra run was started as a `launchctl submit` job, which launchd schedules at a lower priority, to see whether playback behaves differently for a throttled process.

## Results

### Model files

`setup` should download these from the `kokoro-onnx` project's `model-files-v1.0` release, `https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/<file>`:

| File | Bytes | SHA-256 |
| --- | --- | --- |
| `kokoro-v1.0.onnx` (fp32) | 325,532,387 | `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5` |
| `voices-v1.0.bin` | 28,214,398 | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` |
| `kokoro-v1.0.int8.onnx` (measured for comparison) | 92,361,271 | `6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb` |

The URL must name the release tag. A later release, `model-files-v1.1`, ships a `kokoro-v1.0.onnx` with a different size. The release also has an fp16 file (177 MB), which wasn't measured: fp16 rarely helps on onnxruntime's CPU provider.

### Cold start (fp32, fresh process)

| Step | Time |
| --- | --- |
| Import `kokoro_onnx` | 70–220 ms (the high end when the OS file cache is cold) |
| Load the model | 255–310 ms |
| First synthesis, 12 words | 675–760 ms |
| Second synthesis, same sentence | 520–525 ms |

A cold daemon needs about 1.0–1.3 s from start to its first finished sentence. The first synthesis is also about 200 ms slower than every later one.

### Warm synthesis (fp32)

Milliseconds, p50 / p95. Slowdown is the p50 over all 15 sentences, against the baseline. The baselines have 21 samples per length; the other phases have 9, so their p95 is in effect the maximum.

| Phase | 5 words | 8 words | 12 words | 16 words | 20 words | Slowdown |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline | 266 / 275 | 368 / 374 | 543 / 561 | 724 / 749 | 903 / 920 | 1.00× |
| `afplay` from this process | 268 / 272 | 373 / 387 | 561 / 590 | 730 / 771 | 905 / 982 | 1.03× |
| `afplay` from a separate job | 264 / 268 | 365 / 374 | 543 / 562 | 724 / 727 | 892 / 927 | 1.00× |
| Straight after a clip finished | 283 / 310 | 392 / 408 | 568 / 602 | 738 / 770 | 917 / 959 | 1.05× |
| Baseline again | 266 / 310 | 376 / 397 | 550 / 579 | 728 / 781 | 945 / 996 | 1.01× |

Latency grows linearly with length, at about 43 ms per word on top of about 50 ms. An earlier run on the same machine gave the same baseline to within 2%.

**Playback doesn't slow synthesis here.** Every playback phase is within 5% of the baseline, which is about the run-to-run noise. Nothing lingers after playback stops.

**A throttled process behaves the opposite way.** In the run started as a `launchctl submit` job, the baseline was 2.8× slower (1538 ms p50 over all sentences) and very noisy. It got faster while audio played (0.75–0.79× its own baseline), and straight after a clip finished it ran at near full speed (599 ms, 1.1× the shell baseline). So for a process launchd schedules at a low priority, playback lifts it rather than demotes it. That doesn't affect the daemon, which ADR-0002 shows runs at the default priority.

### Resident memory

| | fp32 | int8 |
| --- | --- | --- |
| Before loading (Python plus imports) | 71 MB | 63 MB |
| Model loaded | 487 MB | 224 MB |
| After two 12-word syntheses | 670 MB | 405 MB |
| After the warm 5–20-word runs | 885 MB | 594 MB |
| After a 180-word paragraph | 974 MB | 905 MB |

Memory keeps growing after the model loads, most likely because onnxruntime's CPU memory arena keeps buffers sized for the largest inputs it has seen and doesn't give them back.

### int8, for comparison

From a shell, the int8 model was 1.65× faster than fp32 (329 ms p50 over all sentences against 543 ms; 556 ms for 20 words), loaded in 190 ms, and used about 300 MB less memory once warm. Playback had the same lack of effect (at most 1.11× straight after a clip). Its speech quality wasn't judged here.

## Decision

1. **Ship fp32 `kokoro-v1.0.onnx` and `voices-v1.0.bin`,** downloaded by `setup` from the `model-files-v1.0` URL above and checked against these SHA-256 sums. Whether int8 sounds good enough to replace it is a listening question for the speech module. (ADR-0012 ships int8 instead.)
2. **Keep the model loaded for the daemon's whole life,** and warm it with one throwaway synthesis right after loading. Loading per message would add about 0.5 s (the load plus the slower first synthesis) to every notification, and more in a fresh process. Because `SessionStart` starts the daemon early (ADR-0001), the load and warm-up happen before the first turn ends. The cost is 0.5–0.9 GB of resident memory, but only while Claude Code is in use, because the daemon exits when idle (ADR-0002).
3. **Keep the brief's rule: all compute finishes before any audio plays.** Each sentence is synthesized whole, then played, with no streaming. Playback didn't slow synthesis here, so the rule isn't forced by a measured slowdown on this machine. It still costs nothing, because a sentence takes well under a second to synthesize, and it removes any chance of a gap mid-sentence on a slower Mac. Synthesizing the next queued sentence while one plays cost at most 5% here, but it would also run the next message's wording, which wasn't measured, so it isn't adopted.
4. **Keep spoken sentences short.** At about 43 ms per word, a 20-word sentence costs 0.9 s of the 3 s budget before Ollama's share. The wording length cap should keep sentences near 20 words.

## Consequences

- A notification's speech costs 0.3–0.9 s with a warm model. A cold daemon adds 0.5–0.7 s more to its first message, if `SessionStart` didn't give it time to load.
- If the daemon is ever started by launchd instead, it must be `ProcessType` Interactive. A default `launchctl submit` job synthesized 2.8× slower, which matches ADR-0002's warning.
- The daemon's memory grows with the longest text it has synthesized. The wording length cap bounds that too. Turning off onnxruntime's CPU memory arena might reduce it, which wasn't measured.
- These numbers come from one M3 Max. A base M-series chip has fewer performance cores, so it will be slower and may react to playback differently. The spike script reproduces all of it on any Mac.
