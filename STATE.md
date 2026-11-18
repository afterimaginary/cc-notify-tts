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

- Repository setup

## Up Next

1. Plan milestone 1 issues from [docs/brief.md](docs/brief.md)

## Needs Your Decision

- Approve the plugin's hook list proposed in ADR-0001, and run its desktop-app steps (#1).

## Notes

- Design decisions and measured results go in [docs/decisions/](docs/decisions/).
