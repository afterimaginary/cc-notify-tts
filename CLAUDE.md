# cc-notify-tts

Configurable, summarized voice notifications for Claude Code on macOS: a Claude Code plugin whose hooks send events to a small local daemon that writes one sentence and speaks it with Kokoro. What's being built: [docs/brief.md](docs/brief.md). Progress: [STATE.md](STATE.md).

## Verification

- Before `/refine`: `make ready`
- The full suite, `make verify`, runs as the PR's required `verify` check. Do not run it locally unless you are changing it or reproducing a failure.

## Project notes

- **Merging:** the orchestrator's supervisor merges PRs once `verify` passes at the PR head. Workers open PRs, get them through review and `verify`, then stop.
- **Toolchain:** Python 3.13 via `uv` (`uv sync` once). `ruff`, `pyright` (strict) and `pytest`. Run tools through `uv run`.
- **Git hooks:** a new clone runs `make hooks` once so `make ready` runs before every push.
- **macOS only.** Tests that need a model, Ollama or an audio device stay out of `ready` and `verify`.
- **Never commit** model weights, generated audio or real Claude Code session transcripts. Eval fixtures are hand-written.
- **Public repo.** Commits use the `afterimaginary` handle and the GitHub noreply email. No personal paths, hostnames or names in anything tracked.
