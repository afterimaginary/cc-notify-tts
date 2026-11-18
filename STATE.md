# Project State

**Goal:** configurable, summarized voice notifications for Claude Code on macOS, with a reproducible eval of the local models that write them.

## Where We Are

The repository is set up: brief, build policy, Python toolchain, `make ready` / `make verify` and the `verify` check. Milestone 1 (proving the risky parts) is under way. Hook coverage is measured in the CLI (ADR-0001) and synthetic payload fixtures are in `tests/fixtures/hooks/`. The daemon will start on demand from the hook (ADR-0002). The wording model stays warm with a matching ping, and wording waits at most 1 s, only when the model is loaded (ADR-0003). Hooks will launch the shim with a uv-managed Python and `-I -S`, measured at 24 ms p50 and 35 ms p95 per event, with no wait for Claude Code because every handler is async (ADR-0004). Kokoro synthesizes a 5–20-word sentence in 0.27–0.9 s warm, and nearby playback doesn't slow it (ADR-0005). Presence can be read without any macOS permission: idle time from CoreGraphics, the host app from the hook's process ancestry, and frontmost changes from `lsappinfo listen` (ADR-0006). Hook payloads are classified into the seven notification events with per-session state on disk and 5 s dedupe (ADR-0007). Settings come from one TOML file read on every event in about 0.1 ms, falling back per key on bad values; `away` defaults to 20 s idle or 10 s out of front (ADR-0009, [spec](docs/specs/config.md)). Every event has template wording: a frame naming the project plus a detail from the payload ([spoken messages](docs/specs/spoken-messages.md)). The when-to-speak gate decides for each event, with a plain reason, from the config and live presence ([design](docs/designs/when-to-speak.md)). Install is three commands: `brew install uv`, `uv tool install` of the package with a uv-managed Python 3.13, then `~/.local/bin/cc-notify-tts setup`, which puts the CLI on `PATH`, links the interpreters into the data folder, fetches the model and installs the plugin. Proven end to end in a throwaway home, with plugin update and uninstall (ADR-0011). `setup` is built: it links the interpreters, downloads and verifies Kokoro's int8 model (ADR-0012), installs the plugin and fixes `PATH`, and is safe to rerun. The speech module synthesizes a whole sentence to a temporary WAV and then plays it with `afplay`, with the model loaded once. The daemon ties it together: it takes events over a Unix socket in one-line JSON requests, answers pings before the model loads, keeps to one instance with a lock file, classifies each session's events in the order their hooks fired, gates and words them, plays one sentence at a time with one pending slot (dropping any over 30 s old), and exits once no event has arrived for the idle timeout (ADR-0013, [protocol](docs/specs/daemon-protocol.md)). For finished turns, questions and permission requests, a local Ollama model may write the detail when it's already loaded and answers within 1 s; its answer is cut to one sentence, stripped of frame echoes, and rejected for a leading pronoun, a bad length or any name or number not in its input, and the template is spoken whenever anything fails. The daemon keeps the model loaded only while a session is open (ADR-0015, [spoken messages](docs/specs/spoken-messages.md#model-wording)). The repository is the plugin and its own marketplace: seven async hooks run a standard-library shim that stamps each event, names the host app, and hands it to the daemon, starting it detached when nothing answers; `client.py` is the start-the-daemon code the CLI will share (ADR-0014). `make eval MODELS=…` scores wording models on 48 hand-written cases through the daemon's own prompt and guards, and writes a dated table with pass rate, latency, memory and digests to `evals/results/`; at temperature 0 answers repeat exactly across runs, and latency noise is about ±5% at a similar load (ADR-0016).

## Features

- [ ] Spoken notifications for finished turns, background work, permission requests, questions, denials and failures
- [ ] Per-event "when to speak" setting (`away`, `always`, `never`), mute and quiet hours
- [x] Local wording with guards, falling back to templates
- [ ] `cc-notify-tts status`, `fire`, `mute`, `doctor`, `setup`
- [x] `make eval`: reproducible eval of local wording models

## Recently Done

- Wording eval harness and 48 cases, with the noise band measured (ADR-0016) (#18)
- Plugin packaging, marketplace, hooks and the hook shim (ADR-0014) (#9)
- Ollama wording with guards and keep-warm (ADR-0015) (#17)
- Daemon, socket protocol and speech queue (ADR-0013) (#12)
- Speech: Kokoro synthesis and `afplay` playback, `cc-notify-tts setup`, int8 model chosen (ADR-0012) (#11)

## Up Next

1. CLI: `status`, `fire`, `mute`/`unmute`, `doctor` (#13)
2. Templates-only end-to-end test and install docs (#16)

## Needs Your Decision

- Run the desktop-app steps of ADR-0001 and ADR-0002 (spike README) when convenient; the hook list itself is approved.
- Run ADR-0012's listening check (fp32 against int8, five pairs by ear) to confirm the int8 model.
- Run ADR-0006's one-line host check in Terminal (and iTerm2, if you use it) when convenient.

## Notes

- Design decisions and measured results go in [docs/decisions/](docs/decisions/).
