---
description: Ship Kokoro's int8 model (kokoro-v1.0.int8.onnx) instead of fp32, for 1.65× faster synthesis, about 300 MB less memory and a quarter of the download, pending the owner's listening check.
status: accepted
---

# ADR-0012: Ship the int8 Kokoro model

## Context

ADR-0005 measured two variants of Kokoro-82M from the `model-files-v1.0` release, through `kokoro-onnx` on onnxruntime's CPU provider, on an M3 Max:

| | fp32 `kokoro-v1.0.onnx` | int8 `kokoro-v1.0.int8.onnx` |
| - | - | - |
| Download | 326 MB | 92 MB |
| Load | 255–310 ms | 190 ms |
| Warm synthesis, p50 over 5–20-word sentences | 543 ms | 329 ms (1.65× faster) |
| 20-word sentence, p50 | 903 ms | 556 ms |
| Resident memory once warm (5–20-word runs) | 885 MB | 594 MB |
| Effect of nearby playback | at most 1.05× | at most 1.11× |

ADR-0005 shipped fp32 because int8's speech quality hadn't been judged, and left the choice to the speech module. The latency budget is "spoken within 3 s of the turn ending", shared with wording (up to 1 s, ADR-0003), so synthesis time is the largest part that can still shrink. A base M-series chip has fewer performance cores than the measured one, which makes the gap matter more there, not less.

## Decision

**`setup` downloads `kokoro-v1.0.int8.onnx` and `voices-v1.0.bin`, and the daemon loads int8.** This replaces the first point of ADR-0005's decision. The URL, sizes and SHA-256 sums are ADR-0005's.

- About 210 ms saved per sentence (350 ms on a 20-word one) is the biggest single cut available to the latency budget.
- The daemon holds about 300 MB less while Claude Code runs.
- `setup` downloads 120 MB instead of 354 MB.

Speech quality is the open risk: nobody has compared the two by ear yet. Quantizing to int8 usually costs little for a model this size, but that's a reason to try it, not evidence. **The owner's listening check confirms or reverses this decision.** Reversing it is one change: `MODEL` in `src/cc_notify_tts/models.py` back to fp32's name, size and hash from ADR-0005.

## Listening check (owner)

Needs both model files in `~/Library/Application Support/cc-notify-tts/models/` and speakers or headphones.

1. Fetch the fp32 file if it isn't there (`setup` only fetches int8), and check its SHA-256 matches ADR-0005's (`7d5df8ec…3636a6c5`):

   ```sh
   curl -fL -o ~/Library/Application\ Support/cc-notify-tts/models/kokoro-v1.0.onnx \
     https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
   shasum -a 256 ~/Library/Application\ Support/cc-notify-tts/models/kokoro-v1.0.onnx
   ```

2. From the repository, run `uv run python spikes/kokoro_listening/listen.py`. It plays five notification-shaped sentences, each once per model as A and B in a random order. After each pair, answer `A`, `B` or `same`. It then shows which was which and the totals.
3. To hear another voice, add `--voice <name>`, such as `--voice am_adam`.
4. Keep int8 unless fp32 wins clearly, say three or more of five. Then ship fp32 as described above.

## Consequences

- Faster speech and a smaller daemon on every Mac, for a quality difference not yet judged by the owner.
- The opt-in test (`uv run pytest -m model`) and the daemon load whatever `MODEL` names, so a reversal touches no other code.
- The fp16 file (177 MB) is still unmeasured; ADR-0005 explains why it's unlikely to help on the CPU provider.
