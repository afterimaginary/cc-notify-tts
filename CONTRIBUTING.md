# Contributing

Thanks for your interest in improving cc-notify-tts.

## Before you start

- **Bugs:** open an issue with what you ran, what you expected and what happened, including your macOS version and `claude --version`.
- **Features and larger changes:** open an issue first so the approach can be agreed before you write code.

## Development setup

Requires macOS and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
make hooks   # runs `make ready` before every push
```

| Command       | Does                                                                                  |
| ------------- | ------------------------------------------------------------------------------------- |
| `make ready`  | Lint, format check, type check and unit tests. Run it before opening a pull request.   |
| `make verify` | `ready` plus integration tests. CI runs it on every PR.                                |

Neither needs a speech model, Ollama or an audio device. Two more need them and stay out of CI:

| Command | Does |
| - | - |
| `make eval MODELS="granite4:micro"` | Scores local wording models ([evals/README.md](evals/README.md)). |
| `make latency` | Fires a scripted session through the real hooks, daemon and speech (sound plays) and prints p50 and p95 per stage. `MODEL=granite4:micro` adds model wording. `uv run python -m bench.latency summarize` does the same for your own `daemon.log`. |

## Pull requests

- Keep each pull request to one change, and link the issue it resolves.
- Add or update tests for any change in behaviour.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for the pull request title, e.g. `fix(hook): never write to stdout`.
- Fill in the pull request template, including what you ran to verify the change.
