# Config load time

Times `cc_notify_tts.config.load`, which runs on every event, and the module's import in a fresh process. Results and the decision they support are in ADR-0009.

```sh
uv run python spikes/config_load/measure.py
```

It loads a missing file, the example from `docs/specs/config.md` and a file with three problems, 5000 times each, and prints p50, p95 and max. It writes only to a temporary directory.
