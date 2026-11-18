# Project State

**Goal:** configurable, summarized voice notifications for Claude Code on macOS, with a reproducible eval of the local models that write them.

## Where We Are

The repository is set up: brief, build policy, Python toolchain, `make ready` / `make verify` and the `verify` check. No product code yet; milestone 1 (proving the risky parts) is under way. Hook coverage is measured in the CLI (ADR-0001) and synthetic payload fixtures are in `tests/fixtures/hooks/`. The daemon will start on demand from the hook (ADR-0002). The wording model stays warm with a matching ping, and wording waits at most 1 s, only when the model is loaded (ADR-0003). Kokoro synthesizes a 5–20-word sentence in 0.27–0.9 s warm, and nearby playback doesn't slow it (ADR-0005).

## Features

- [ ] Spoken notifications for finished turns, background work, permission requests, questions, denials and failures
- [ ] Per-event "when to speak" setting (`away`, `always`, `never`), mute and quiet hours
- [ ] Local wording with guards, falling back to templates
- [ ] `cc-notify-tts status`, `fire`, `mute`, `doctor`, `setup`
- [ ] `make eval`: reproducible eval of local wording models

## Recently Done

- Spike: small Ollama models answer in 0.07–0.6 s warm and 0.7–1.5 s after a load; `num_ctx` 2048 saves up to 3.5 GiB (ADR-0003) (#5)
- Spike: Kokoro latency, cold and warm, with and without nearby playback; the daemon keeps the model loaded (ADR-0005) (#4)
- Spike: a daemon started from a hook outlives its session and runs at foreground priority, so it starts on demand (ADR-0002) (#3)
- Repository setup

## Up Next

1. Plan milestone 1 issues from [docs/brief.md](docs/brief.md)

## Needs Your Decision

- Run the desktop-app steps of ADR-0001 and ADR-0002 (spike README) when convenient; the hook list itself is approved.

## Notes

- Design decisions and measured results go in [docs/decisions/](docs/decisions/).
