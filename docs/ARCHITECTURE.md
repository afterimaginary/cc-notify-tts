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

- **Hook shim:** never writes to stdout, always exits 0, and returns quickly. Anything expensive happens in the daemon. Hooks run it as a script with a uv-managed Python by absolute path and `-I -S`, never through `uv run` (ADR-0003).
- **Daemon:** keeps the speech model warm while Claude Code is in use and exits after an idle timeout. The hook shim starts it on demand as a detached process, with no LaunchAgent (ADR-0002).
- **Presence:** the hook shim records each session's host app; the daemon reads idle time and follows the frontmost app. None of it needs a macOS permission, and a signal it can't read counts as away (ADR-0006).
- **Classification and state:** `events.py` turns each hook payload into one of seven notification events (or none), using per-session files from `sessions.py` (turn start, live background work, what was heard), which survive a daemon restart. Duplicates of a kind within 5 s collapse (ADR-0007, [design](designs/event-classification.md)).

Decisions behind these choices are recorded in [decisions/](decisions/) as they're made.
