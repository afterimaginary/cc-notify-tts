# Project State

**Goal:** configurable, summarized voice notifications for Claude Code on macOS, with a reproducible eval of the local models that write them.

## Where We Are

Milestones 1 and 2 are built: on templates alone, the plugin's hooks reach a local daemon that classifies each event, decides whether to speak from the config and live presence, words it and speaks it with Kokoro, and the README covers install, `status`, `fire`, mute and uninstall. An end-to-end test runs the real hook commands, shim and daemon (with fake speech and presence) through a whole session in `verify`. Ollama wording with guards (ADR-0015) and the wording eval (ADR-0016) are in, and ten models were compared on it: `granite4:micro` is the default wording model, with `phi4-mini:3.8b` the runner-up (ADR-0018). Every event's stages are now logged and measured: the first sound comes about 0.75 s after the hook with templates and 1.0 s with `granite4:micro` (p95), and hooks stay under 60 ms p95 (ADR-0019). What's left is the launch.

How each part works is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); measured results and decisions are ADR-0001 to ADR-0019, ADR-0022 and ADR-0023 in [docs/decisions/](docs/decisions/).

## Features

- [x] Spoken notifications for finished turns, background work, permission requests, questions, denials and failures
- [x] Per-event "when to speak" setting (`away`, `always`, `never`), mute and quiet hours
- [x] Local wording with guards, falling back to templates
- [x] `cc-notify-tts status`, `fire`, `mute`, `doctor`, `setup`
- [x] `make eval`: reproducible eval of local wording models
- [x] Shipped default wording model, chosen by the eval (#19)
- [x] Measured end-to-end latency, stage by stage (#20)

## Recently Done

- A background command that runs all session, such as a dev server, is announced as still running once, not at every turn end (ADR-0023) (#33)
- A queued sentence no longer waits for `afplay` to exit: the silence between back-to-back sentences fell from 1083 ms to 404 ms p50 (ADR-0022) (#56)
- Latency instrumentation: a `timing` line per event in `daemon.log`, `bench.latency`, and the measured budget (ADR-0019) (#20)
- Model comparison: `granite4:micro` is the default wording model, `phi4-mini:3.8b` the runner-up (ADR-0018) (#19)
- Templates-only end-to-end test and the README's install and usage docs (#16)
- CLI: `status`, `fire`, `mute`/`unmute`, `doctor` (ADR-0017) (#13)
- Wording eval harness and 48 cases, with the noise band measured (ADR-0016) (#18)
- Plugin packaging, marketplace, hooks and the hook shim (ADR-0014) (#9)
- Ollama wording with guards and keep-warm (ADR-0015) (#17)

## Up Next

1. Milestone 4: show and launch

## Needs Your Decision

- Run the desktop-app steps of ADR-0001 and ADR-0002 (spike README) when convenient; the hook list itself is approved.
- Run ADR-0012's listening check (fp32 against int8, five pairs by ear) to confirm the int8 model.
- Run ADR-0006's one-line host check in Terminal (and iTerm2, if you use it) when convenient.

## Notes

- Design decisions and measured results go in [docs/decisions/](docs/decisions/).
