# Architecture

```
Claude Code ── plugin hooks (async) ──▶ hook shim (standard library only)
                                          │ names the host app (SessionStart, tmux)
                                          │ sends the event over a Unix socket,
                                          │ starting the daemon if nothing answers
                                          ▼
                                   daemon (started on demand, exits when idle)
                                     inbox in hook order ─▶ classification ─▶ when-to-speak check
                                     ─▶ wording (template, then local model)
                                     ─▶ single-slot queue ─▶ speech synthesis ─▶ playback
```

- **Plugin and hook shim:** the repository is the plugin (`.claude-plugin/plugin.json`) and its own marketplace (`.claude-plugin/marketplace.json`). `hooks/hooks.json` registers seven events, all async (ADR-0001), each running `hooks/shim.py` with the data folder's uv-managed Python by absolute path and `-I -S`, never through `uv run` (ADR-0004, ADR-0011). That script imports `shim.py`, `client.py` and `host_app.py` from the plugin's own `src/`, all standard library only. The shim never writes to stdout, always exits 0, logs problems to `~/Library/Logs/cc-notify-tts/shim.log`, and leaves anything expensive to the daemon (ADR-0014).
- **Daemon client:** `client.py` is how the shim and the CLI reach the daemon: the socket path, one request, and starting `python -I -m cc_notify_tts.daemon` detached, then retrying until it answers.
- **Daemon:** `daemon.py` keeps the speech model warm while Claude Code is in use and exits after an idle timeout. The hook shim starts it on demand as a detached process, with no LaunchAgent (ADR-0002), and talks to it in one-line JSON requests over a Unix socket ([protocol](specs/daemon-protocol.md)). A lock file keeps it to one instance. Socket threads only enqueue; one event worker classifies events in the order their hooks fired, gates and words them, and records what was heard; one speech worker plays them through a single pending slot (`queues.py`, ADR-0013).
- **Presence and the gate:** the hook shim names each session's host app and the daemon keeps it in the session's state; `presence.py` reads idle time and follows the frontmost app with a long-running `lsappinfo listen`. None of it needs a macOS permission, and a signal it can't read counts as away (ADR-0006). `gate.py` applies mute, quiet hours, the event's setting and then `away`, and returns the decision with a reason ([design](designs/when-to-speak.md)).
- **Classification and state:** `events.py` turns each hook payload into one of seven notification events (or none), using per-session files from `sessions.py` (turn start, live background work, what was heard), which survive a daemon restart. Duplicates of a kind within 5 s collapse (ADR-0007, [design](designs/event-classification.md)).
- **Config:** `config.py` reads one TOML file under `~/Library/Application Support/cc-notify-tts/` on every event, with every default in code. A bad key falls back to its default and is reported for `doctor` rather than raising (ADR-0009, [spec](specs/config.md)).
- **Wording:** `wording.py` turns each notification into a frame naming the project and an optional detail from the payload, so every event has a sentence without a model ([spec](specs/spoken-messages.md)). For finished turns, questions and permission requests, `model_wording.py` may ask a local Ollama model for the detail instead, only when it's already loaded and within a 1 s timeout, and its answer must pass guards or the template's detail is used (ADR-0015). `ollama.py` talks to Ollama with the standard library and keeps the model loaded while a session is open (ADR-0003).
- **Speech:** `speech.py` holds Kokoro-82M (int8, ADR-0012) loaded and warmed for the daemon's life, synthesizes each sentence whole to a temporary WAV, then plays it with `afplay`, so no compute runs while audio plays (ADR-0005). `models.py` names the model files and fetches them with checksums.
- **Install:** `uv tool install` puts the package, its dependencies and the CLI in uv's tool folder; `cc-notify-tts setup` links uv's Python 3.13 (for the hooks) and the tool's environment (for the daemon) into the data folder, fetches the model and installs the plugin. Nothing lasting lives in the plugin's versioned cache folder (ADR-0011).

Decisions behind these choices are recorded in [decisions/](decisions/) as they're made.
