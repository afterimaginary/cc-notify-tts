# Project State

**Goal:** configurable, summarized voice notifications for Claude Code on macOS, with a reproducible eval of the local models that write them.

## Where We Are

The repository is set up: brief, build policy, Python toolchain, `make ready` / `make verify` and the `verify` check. No product code yet; milestone 1 (proving the risky parts) is under way. Hook coverage is measured in the CLI (ADR-0001) and synthetic payload fixtures are in `tests/fixtures/hooks/`.

## Features

- [ ] Spoken notifications for finished turns, background work, permission requests, questions, denials and failures
- [ ] Per-event "when to speak" setting (`away`, `always`, `never`), mute and quiet hours
- [ ] Local wording with guards, falling back to templates
- [ ] `cc-notify-tts status`, `fire`, `mute`, `doctor`, `setup`
- [ ] `make eval`: reproducible eval of local wording models

## Recently Done

- Spike: a daemon started from a hook outlives its session and runs at foreground priority, so it starts on demand (ADR-0001) (#3)
- Repository setup

## Up Next

1. Plan milestone 1 issues from [docs/brief.md](docs/brief.md)

## Needs Your Decision

- Run ADR-0001's desktop-app steps when convenient; the hook list itself is approved.

## Notes

- Design decisions and measured results go in [docs/decisions/](docs/decisions/).
