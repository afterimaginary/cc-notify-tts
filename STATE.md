# Project State

**Goal:** configurable, summarized voice notifications for Claude Code on macOS, with a reproducible eval of the local models that write them.

## Where We Are

The repository is set up: brief, build policy, Python toolchain, `make ready` / `make verify` and the `verify` check. Milestone 1 (proving the risky parts) is under way. Hook coverage is measured in the CLI (ADR-0001) and synthetic payload fixtures are in `tests/fixtures/hooks/`. The daemon will start on demand from the hook (ADR-0002). The wording model stays warm with a matching ping, and wording waits at most 1 s, only when the model is loaded (ADR-0003). Hooks will launch the shim with a uv-managed Python and `-I -S`, measured at 24 ms p50 and 35 ms p95 per event, with no wait for Claude Code because every handler is async (ADR-0004). Kokoro synthesizes a 5–20-word sentence in 0.27–0.9 s warm, and nearby playback doesn't slow it (ADR-0005). Presence can be read without any macOS permission: idle time from CoreGraphics, the host app from the hook's process ancestry, and frontmost changes from `lsappinfo listen` (ADR-0006). Hook payloads are classified into the seven notification events with per-session state on disk and 5 s dedupe (ADR-0007).

## Features

- [ ] Spoken notifications for finished turns, background work, permission requests, questions, denials and failures
- [ ] Per-event "when to speak" setting (`away`, `always`, `never`), mute and quiet hours
- [ ] Local wording with guards, falling back to templates
- [ ] `cc-notify-tts status`, `fire`, `mute`, `doctor`, `setup`
- [ ] `make eval`: reproducible eval of local wording models

## Recently Done

- Spike: hook shim overhead and launch method (ADR-0004) (#2)
- Spike: presence from a hook: idle time, host app for each terminal, and following the frontmost app, all without permissions (ADR-0006) (#6)
- Event classification, session state and dedupe (ADR-0007) (#14)
- Spike: small Ollama models answer in 0.07–0.6 s warm and 0.7–1.5 s after a load; `num_ctx` 2048 saves up to 3.5 GiB (ADR-0003) (#5)
- Spike: Kokoro latency, cold and warm, with and without nearby playback; the daemon keeps the model loaded (ADR-0005) (#4)
- Spike: a daemon started from a hook outlives its session and runs at foreground priority, so it starts on demand (ADR-0002) (#3)

## Up Next

1. Plan milestone 1 issues from [docs/brief.md](docs/brief.md)

## Needs Your Decision

- Run the desktop-app steps of ADR-0001 and ADR-0002 (spike README) when convenient; the hook list itself is approved.
- Run ADR-0006's one-line host check in Terminal (and iTerm2, if you use it) when convenient.

## Notes

- Design decisions and measured results go in [docs/decisions/](docs/decisions/).
