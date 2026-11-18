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

- **Hook shim:** never writes to stdout, always exits 0, and returns quickly. Anything expensive happens in the daemon.
- **Daemon:** keeps the speech model warm while Claude Code is in use and exits after an idle timeout.
- **State:** per-session files (turn start, live background work, what was heard) survive a daemon restart.

Decisions behind these choices are recorded in [decisions/](decisions/) as they're made.
