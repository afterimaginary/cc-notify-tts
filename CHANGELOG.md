# Changelog

All notable changes to this project are recorded here. Versions follow [Semantic Versioning](https://semver.org).

## [Unreleased]

### Added

- **`OLLAMA_HOST` on this machine:** wording asks Ollama at `OLLAMA_HOST` when it names this machine (`localhost`, a loopback address, `0.0.0.0` or `::`), so Ollama on another local port still writes the detail. An `OLLAMA_HOST` on another machine is ignored with a log line, so wording stays local, and `doctor` shows the address in use (ADR-0029, #44).

### Fixed

- **Eval results match the current guards:** the recorded eval runs in `evals/results/` are re-scored from their recorded answers with today's guards, `python -m evals.rescore` does it again after a guard change, and `make ready` fails until it has. Answers recorded before ADR-0021's guard that report failing tests as a success now count as rejected; no pass count changes (ADR-0016, ADR-0018, #74).
- **Detached tmux sessions:** a session in a detached tmux no longer keeps the app it was last attached from, so it counts as away and is spoken, and the next hook after reattaching names the new app (ADR-0030, #47).

## [0.1.0] - 2026-11-18

The first release: Claude Code speaks one sentence about what it finished or what it needs from you, at the moments you choose, with the speech and the wording made on your Mac.

### Added

- **Spoken notifications for seven events:** a finished turn, a finished turn with background work still running, background work all done, a permission request, a question or plan to approve, a denied action and a failed turn. Background work that runs all session, such as a dev server, is announced as still running once (ADR-0023), and "background done" waits for a still-queued announcement to play first (ADR-0026).
- **When to speak, per event:** `away` (the default), `always` or `never`, with mute and quiet hours. Away means no keyboard or mouse input for 20 s, or the session's app out of front for 10 s, read from a hook without extra permissions (ADR-0006, ADR-0009).
- **Local speech** with Kokoro-82M (int8) through `kokoro-onnx`, played with `afplay`, in a small daemon that hooks start on demand and that exits after two hours without an event (ADR-0002, ADR-0012, ADR-0013).
- **Optional local wording** by an Ollama model, `granite4:micro` by default, with guards that reject names and numbers not in Claude's reply and fall back to built-in templates. A turn that ends with failing tests is never worded as a success (ADR-0015, ADR-0018, ADR-0021).
- **Claude Code plugin** with seven async hooks and a standard-library hook shim, installed from the repository's own marketplace (ADR-0014).
- **CLI:** `setup`, `status`, `fire`, `mute`/`unmute` and `doctor` (ADR-0011, ADR-0017).
- **`make eval`:** a reproducible eval of local wording models over 53 hand-written cases; ten models compared (ADR-0016, ADR-0018).
- **Measured latency:** a `timing` line per event in `daemon.log` and `bench.latency`. The first sound comes 0.74 s after the hook with templates and 0.99 s with `granite4:micro` (p95), and hooks stay under 60 ms p95 (ADR-0019, ADR-0022).
- **`make demo`:** a scripted demo video with real speech, rebuilt from scratch; the full cut is attached to this release (ADR-0020).

### Versioning

The Python package is versioned 0.1.0. The plugin manifests deliberately carry no version, so Claude Code treats each commit as the plugin's version: every push reaches `claude plugin update`, and the hooks stay in step with the package that `uv tool upgrade` installs from the same repository (ADR-0014).

[Unreleased]: https://github.com/afterimaginary/cc-notify-tts/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/afterimaginary/cc-notify-tts/releases/tag/v0.1.0
