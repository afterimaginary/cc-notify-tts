# Architecture

```
Claude Code ── plugin hooks (async) ──▶ hook shim (standard library only)
                                          │ updates per-session state on disk
                                          │ sends the event over a Unix socket
                                          ▼
                                   daemon (started on demand, exits when idle)
                                     when-to-speak check ─▶ wording (template, then local model)
                                     ─▶ speech synthesis ─▶ single-slot queue ─▶ playback
```

- **Hook shim:** never writes to stdout, always exits 0, and returns quickly. Anything expensive happens in the daemon. Hooks run it as a script with a uv-managed Python by absolute path and `-I -S`, never through `uv run` (ADR-0004).
- **Daemon:** keeps the speech model warm while Claude Code is in use and exits after an idle timeout. The hook shim starts it on demand as a detached process, with no LaunchAgent (ADR-0002).
- **Presence and the gate:** the hook shim records each session's host app; `presence.py` reads idle time and follows the frontmost app with a long-running `lsappinfo listen`. None of it needs a macOS permission, and a signal it can't read counts as away (ADR-0006). `gate.py` applies mute, quiet hours, the event's setting and then `away`, and returns the decision with a reason ([design](designs/when-to-speak.md)).
- **Classification and state:** `events.py` turns each hook payload into one of seven notification events (or none), using per-session files from `sessions.py` (turn start, live background work, what was heard), which survive a daemon restart. Duplicates of a kind within 5 s collapse (ADR-0007, [design](designs/event-classification.md)).
- **Config:** `config.py` reads one TOML file under `~/Library/Application Support/cc-notify-tts/` on every event, with every default in code. A bad key falls back to its default and is reported for `doctor` rather than raising (ADR-0009, [spec](specs/config.md)).
- **Wording:** `wording.py` turns each notification into a frame naming the project and an optional detail from the payload, so every event has a sentence without a model ([spec](specs/spoken-messages.md)).
- **Install:** `uv tool install` puts the package, its dependencies and the CLI in uv's tool folder; `cc-notify-tts setup` links uv's Python 3.13 (for the hooks) and the tool's environment (for the daemon) into the data folder, fetches the model and installs the plugin. Nothing lasting lives in the plugin's versioned cache folder (ADR-0011).

Decisions behind these choices are recorded in [decisions/](decisions/) as they're made.
