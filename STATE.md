# Project State

**Goal:** configurable, summarized voice notifications for Claude Code on macOS, with a reproducible eval of the local models that write them.

## Where We Are

Milestones 1 and 2 are built: on templates alone, the plugin's hooks reach a local daemon that classifies each event, decides whether to speak from the config and live presence, words it and speaks it with Kokoro, and the README covers install, `status`, `fire`, mute and uninstall. An end-to-end test runs the real hook commands, shim and daemon (with fake speech and presence) through a whole session in `verify`. Ollama wording with guards (ADR-0015) and the wording eval (ADR-0016) are in; what's left is the model comparison, latency instrumentation and the launch.

How each part works is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); measured results and decisions are ADR-0001 to ADR-0017 in [docs/decisions/](docs/decisions/).

## Features

- [x] Spoken notifications for finished turns, background work, permission requests, questions, denials and failures
- [x] Per-event "when to speak" setting (`away`, `always`, `never`), mute and quiet hours
- [x] Local wording with guards, falling back to templates
- [x] `cc-notify-tts status`, `fire`, `mute`, `doctor`, `setup`
- [x] `make eval`: reproducible eval of local wording models
- [ ] Shipped default wording model, chosen by the eval (#19)
- [ ] Measured end-to-end latency (#20)

## Recently Done

- Templates-only end-to-end test and the README's install and usage docs (#16)
- CLI: `status`, `fire`, `mute`/`unmute`, `doctor` (ADR-0017) (#13)
- Wording eval harness and 48 cases, with the noise band measured (ADR-0016) (#18)
- Plugin packaging, marketplace, hooks and the hook shim (ADR-0014) (#9)
- Ollama wording with guards and keep-warm (ADR-0015) (#17)

## Up Next

1. Model comparison and default model (#19)
2. End-to-end latency instrumentation (#20)

## Needs Your Decision

- Run the desktop-app steps of ADR-0001 and ADR-0002 (spike README) when convenient; the hook list itself is approved.
- Run ADR-0012's listening check (fp32 against int8, five pairs by ear) to confirm the int8 model.
- Run ADR-0006's one-line host check in Terminal (and iTerm2, if you use it) when convenient.

## Notes

- Design decisions and measured results go in [docs/decisions/](docs/decisions/).
