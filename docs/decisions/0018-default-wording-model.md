---
description: The default wording model is `granite4:micro`, which passed the most eval cases with the fewest harmful answers at 2.18 GiB and a p95 of about 250 ms; `phi4-mini:3.8b` is the runner-up, and `gemma3:1b` is the choice when memory matters more than detail.
status: accepted
---

# ADR-0018: The default wording model

## Context

The daemon may let a local Ollama model write the detail of a finished turn, a question or a permission request, behind guards that fall back to the template (ADR-0015). Until now the config named `llama3.2:1b` as a placeholder. The default should come from the wording eval (ADR-0016): which model most often writes a detail a listener can act on, without saying something false, inside the 1 s wording timeout (ADR-0003), and at what memory cost while a session is open.

## Method

`make eval` over the 48 cases of ADR-0016, with the daemon's own request, prompt and guards: temperature 0, `num_ctx` 2048, at most 40 output tokens, thinking off. Ten models from five families, 1.0 to 4.2 B parameters, the default Ollama tag of each:

| Model | Family | Parameters | Quantization |
| --- | --- | --- | --- |
| `llama3.2:1b` | Meta Llama 3.2 | 1.2 B | Q8_0 |
| `gemma3:1b` | Google Gemma 3 | 1.0 B | Q4_K_M |
| `qwen3:1.7b` | Alibaba Qwen 3 | 2.0 B | Q4_K_M |
| `qwen3.5:2b` | Alibaba Qwen 3.5 | 1.9 B | Q8_0 |
| `granite4:micro` | IBM Granite 4.0 | 3.4 B | Q4_K_M |
| `llama3.2:3b` | Meta Llama 3.2 | 3.2 B | Q4_K_M |
| `phi4-mini:3.8b` | Microsoft Phi-4 | 3.8 B | Q4_K_M |
| `qwen3:4b` | Alibaba Qwen 3 | 4.0 B | Q4_K_M |
| `qwen3.5:4b` | Alibaba Qwen 3.5 | 4.2 B | Q4_K_M |
| `gemma3:4b` | Google Gemma 3 | 3.9 B | Q4_K_M |

Ollama 0.40.16 on an Apple M3 Max with 48 GiB, macOS 27.2.1, with other work running (load average 6–8). All ten ran in one run (`evals/results/2026-11-18-3.md`, digests there). The three leading models and `gemma3:1b` then ran again (`2026-11-18-4.md`, load average 6–11): every answer was identical, and p50 moved by 1–6%, about ADR-0016's ±5% band.

## Results

From the full run; slowest is the slowest single request. Resident memory is `size` from `/api/ps` at `num_ctx` 2048.

| Model | Pass | Detail kept | Invented | Rejected by guards | Over length | p50 ms | p95 ms | Slowest ms | Resident |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `granite4:micro` | **42** | 43 | 1 | 2 | 0 | 176 | 250 | 659 | 2.18 GiB |
| `qwen3.5:4b` | **42** | 43 | 0 | 3 | 1 | 248 | 362 | 734 | 9.24 GiB |
| `phi4-mini:3.8b` | 41 | **45** | 0 | 0 | 4 | 197 | 336 | 918 | 2.64 GiB |
| `llama3.2:3b` | 40 | 42 | 2 | 0 | 0 | 166 | 242 | 680 | 2.16 GiB |
| `qwen3.5:2b` | 40 | 40 | 0 | 4 | 0 | 140 | 256 | 371 | 5.01 GiB |
| `gemma3:4b` | 39 | 39 | 0 | 4 | 0 | 271 | 379 | 861 | 2.17 GiB |
| `gemma3:1b` | 36 | 37 | 1 | 0 | 0 | 84 | 162 | 265 | 0.82 GiB |
| `llama3.2:1b` | 34 | 34 | 0 | 5 | 0 | 74 | 103 | 203 | 1.35 GiB |
| `qwen3:1.7b` | 33 | 35 | 0 | 8 | 2 | 107 | 231 | 303 | 1.13 GiB |
| `qwen3:4b` | 0 | 0 | 0 | 32 | 0 | 471 | 622 | 1218 | 2.68 GiB |

Pass counts out of 48. The confirming run gave `granite4:micro` p50 182 ms and p95 260 ms, `phi4-mini:3.8b` 186 and 351, `llama3.2:3b` 164 and 223, `gemma3:1b` 87 and 125.

Three models at the top differ by one or two cases, which ADR-0016 warns says less than it seems, so the choice rests on what their failing answers would have sounded like:

- **`granite4:micro`** is the only model that kept "2 tests still fail" within the length in `finished-tests-still-fail`; seven others reported the CSV fix as done and left out the failures. Its misses are vague ("Switched to streaming decoder improved performance…" without naming the resizer) or harmless ("Force update main branch with changes" for a force push). Two answers named something not in the input ("BOM", "14-minute"); the guards rejected both, so the template was spoken. Its one forbidden word came in an accurate answer: "Create pull request for fixing rounding issue in invoice totals", where the case forbids "issue" to catch a pull request mistaken for one.
- **`phi4-mini:3.8b`** kept the required detail most often and never invented anything, but four answers ran 1–3 words over the 15 the prompt asks for, one answer was four sentences of which only the first would be spoken, and its slowest request (918 ms) came closest to the 1 s timeout.
- **`llama3.2:3b`** made the two most harmful mistakes in the set: "Deleted the 12 rows with null customer_id" when the assistant had stopped before deleting anything, and "Reset the repository to the last commit three times" for `git reset --hard HEAD~3`. Both pass the guards, since every name and number is in the input.
- **`qwen3.5:4b`** ties for the most passes, but Ollama reports it resident at 9.24 GiB at a 2048 context, and `qwen3.5:2b` at 5.01 GiB, two to four times the other models of their size, held for as long as a session is open.
- **`gemma3:1b`** is the smallest and twice as fast, but its answers are often too vague to act on ("Completed work, including tests and adjustments.").
- **`qwen3:4b`**'s default tag is a thinking-only variant that ignores `think: false` and writes its reasoning as the answer. The guards rejected 32 answers and the other 16 failed the checks, so it is unusable here, but safely so.
- **`llama3.2:1b` and `qwen3:1.7b`** often reduce a permission request to one word ("uses", "fetch"), which the guards reject.

## Decision

- **The default `wording.model` is `granite4:micro`.** It passed the most cases, made no harmful claim the guards let through, ran a p95 of 250–260 ms with its slowest request at 659–680 ms, and holds 2.18 GiB, the same as `llama3.2:3b`. It is Apache 2.0 licensed.
- **The runner-up is `phi4-mini:3.8b`.** It keeps the most detail and invents nothing; its answers are a little long, and it costs 0.46 GiB more and comes closest to the timeout under load.
- **For a machine short on memory, `gemma3:1b`** (0.82 GiB) is the one to suggest: every answer spoken and only one invented, with less detail.
- The 1 s wording timeout stays. No candidate except `qwen3:4b` exceeded it, which confirms ADR-0003's figure for 3–4 B models at the 4000-character input cap.

## Consequences

- A user who wants model wording pulls `granite4:micro` (2.1 GB download). Without it, the daemon speaks templates, as it does for any missing model.
- The model is resident at 2.18 GiB while a session is open (ADR-0015), against 1.35 GiB for the old placeholder.
- `finished-tests-still-fail` is the case nearly every model gets wrong, and the one that matters most: a turn that ends with failing tests is reported as a success. It's worth a stronger instruction in the prompt, measured with the same eval.
- The comparison is one machine, one Ollama version and one case set. A new candidate is compared by running it in the same `make eval` as `granite4:micro`, and reading its failing answers, not only its pass count.
