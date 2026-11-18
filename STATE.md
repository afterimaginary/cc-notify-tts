# Project State

**Goal:** configurable, summarized voice notifications for Claude Code on macOS, with a reproducible eval of the local models that write them.

## Where We Are

All four milestones are built and v0.1.0 is released. The plugin's hooks reach a local daemon that classifies each event, decides whether to speak from the config and live presence, words it with `granite4:micro` through Ollama (or a template) behind guards, and speaks it with Kokoro. The wording eval has 60 cases, and the default model passes 47 of them (ADR-0036). What's left is the manual checks that need a person (#88, #89, #90, #91) and the launch.

How each part works is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); decisions and measured results are in [docs/decisions/](docs/decisions/).

## Features

- [x] Spoken notifications for finished turns, background work, permission requests, questions, denials and failures
- [x] Per-event "when to speak" setting (`away`, `always`, `never`), mute and quiet hours
- [x] Local wording with guards, falling back to templates
- [x] `cc-notify-tts status`, `fire`, `mute`, `doctor`, `setup`
- [x] `make eval`: reproducible eval of local wording models
- [x] Shipped default wording model, chosen by the eval
- [x] Measured end-to-end latency, stage by stage
- [x] `make demo`: a scripted demo video with real speech, and a silent GIF in the README
- [x] README with the evidence, "How this was built", limitations and roadmap
- [x] Version 0.1.0 released with notes in `CHANGELOG.md`

## Recently Done

- The public docs agree with each other, and the manual checks are tracked as issues (#85)
- A choice is kept through a sub-label under an option (#84)
- A choice is kept when Claude labels each option (#81)
- A turn that only answers a question is no longer worded as work done; `granite4:micro` 46 to 47 of 60 (ADR-0036) (#78)
- A lead-in that introduces a one-line choice is kept (#79)

## Up Next

1. Run the acceptance checks that need a person (#91)
2. Listen to int8 against fp32 Kokoro speech (#88)
3. Check the hooks and daemon in the Claude desktop app (#89)

## Needs Your Decision

- None.

## Notes

- Design decisions and measured results go in [docs/decisions/](docs/decisions/).
