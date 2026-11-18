---
description: The repository is the plugin and its own marketplace, with no version so every commit is an update; each hook runs a thin script that imports the stdlib-only shim and daemon client from the plugin's own src/, sends the event at once and starts the daemon only when nothing answers.
status: accepted
---

# ADR-0014: Plugin packaging and the hook shim

## Context

Earlier decisions fix most of the plugin's shape. The hook list and `async` on every handler come from ADR-0001, the launch command from ADR-0004 and ADR-0011, starting the daemon detached from ADR-0002, presence lookups from ADR-0006, and the socket protocol from ADR-0013. Four questions were left:

- Where the plugin's files live in the repository, and what the marketplace entry points at.
- How the plugin is versioned, since Claude Code only installs an update when the version changes.
- How the shim and the CLI share one start-the-daemon module when the hook runs Python with `-I -S`, which hides the tool's environment.
- How the shim reaches the daemon: ping first or send the event at once, how long to wait, how often to retry.

The budget is 100 ms p95 per hook process (brief), and the shim must stay on the standard library.

## Decision

### Layout

- **The repository root is the plugin, and also its marketplace.** `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` sit together, and the marketplace's one entry has `"source": "./"`, as Claude Code's docs describe for a plugin published from its own repository. The marketplace and the plugin are both named `cc-notify-tts`, which is what `setup` installs (`claude plugin install cc-notify-tts --marketplace afterimaginary/cc-notify-tts`) and what it prints for the desktop app (`/plugin install cc-notify-tts@cc-notify-tts`).
- Claude Code copies the whole repository into its plugin cache. It is small and holds no model or generated files, and `hooks/shim.py` needs `src/` next to it (below). A `plugin/` subfolder would keep the copy smaller but would need its own copy of the shared module.
- `hooks/hooks.json` registers `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `Stop`, `StopFailure`, `PermissionRequest` and `PermissionDenied`, each `async: true`, each with ADR-0011's guarded command and the event name as the shim's argument. The user's `settings.json` is changed only by Claude Code's own install command.

### Version

- **Neither manifest sets `version`.** Claude Code then uses the commit of the GitHub marketplace as the plugin's version, so every push is an update, and the plugin tracks the same commits `uv tool upgrade` installs. A fixed version would leave users on the cached copy until someone remembered to bump it. A test keeps `version` out of both manifests.
- ADR-0011 suggested the shim tell the daemon the plugin's version so `doctor` could spot drift. Without a version field there is nothing to send yet; `doctor` (#13) decides how to compare them.

### The shim's code

- `hooks/shim.py` is a few lines: it stamps `fired_at`, points `sys.stdout` and `sys.stderr` at `/dev/null`, puts the plugin's own `src/` first on `sys.path`, calls `cc_notify_tts.shim.main`, and leaves with `os._exit(0)` whatever happened.
- The work is in three package modules that import only the standard library and each other: `shim.py` (one hook: parse, name the host, deliver, log), `client.py` (socket path, one request, start the daemon, deliver) and `host_app.py` (ADR-0006's lookup). `cc_notify_tts/__init__.py` holds only the version. A test parses their imports, and another imports them in an `-I -S` interpreter and checks nothing else loaded.
- **`client.py` is the one start-the-daemon module.** The shim calls it with the data folder's `venv/bin/python`; the CLI (#13) will call it with its own `sys.executable`. The daemon imports its socket path and request size from it too, so there's one definition of each.
- Importing from `src/` rather than shipping one self-contained file means the shim and the CLI can't drift apart. It also lets pyright and the unit tests cover the shim like any other module.

### Reaching the daemon

- **Send the event at once.** Most events find the daemon running, and a `ping` first would cost a second round trip on every one of them.
- **When nothing answers within 200 ms**, the protocol's rule, start the daemon (`<data>/venv/bin/python -I -m cc_notify_tts.daemon`, new session, stdio to `/dev/null`, not waited on) and retry the event every 25 ms for up to 5 s. That's within ADR-0013's 50 ms, and 5 s covers the first start after an install, when Python compiles the package. Hooks are async, so the wait never holds Claude Code up.
- A reply with `ok: false` is the daemon refusing the event, not a missing daemon, so it's logged and not retried.
- `subprocess` is imported only to start the daemon, and `host_app` (with `ctypes` and `plistlib`) only when a host lookup is due: at `SessionStart`, and inside tmux on `Stop`, `StopFailure`, `PermissionRequest` and `PermissionDenied` (ADR-0006).

### Logging

- The shim logs to `~/Library/Logs/cc-notify-tts/shim.log` only when something went wrong or it started the daemon, so a normal event writes nothing. At 1 MB the file moves to `shim.log.1`, replacing the older one. Every log write swallows `OSError`, so a full disk or an unwritable folder never reaches Claude Code.

## Measurements

`spikes/hook-shim-bench/bench_plugin.py` is ADR-0004's benchmark pointed at the shipped plugin: it runs each event's command from `hooks/hooks.json` through `/bin/sh -c`, with `HOME` at a throwaway folder laid out as `setup` leaves it, the fixture on stdin, and times the process from spawn to exit.

Apple M3 Max, macOS 27.2.1, uv-managed Python 3.13. Other work was running, as in normal use. Wall time per hook process, ms:

| Case | Runs | p50 | p95 | max |
| - | - | - | - | - |
| Daemon running: `Stop` | 200 | 22.7 | 31.2 | 32.9 |
| Daemon running: `SessionStart`, with the host-app lookup | 200 | 23.9 | 27.2 | 29.5 |
| Daemon not running: `Stop`, stand-in daemon (a bare interpreter that answers) | 50 | 55.7 | 61.1 | 63.1 |
| Daemon not running: `Stop`, the real daemon from the project's environment, no speech model | 50 | 107.8 | 116.3 | 120.7 |

- **Per event, the shipped shim costs 23 ms p50 and 31 ms p95,** the same as ADR-0004's spike (24 and 35), well under the 100 ms budget. The host-app lookup at `SessionStart` is within the noise: the ancestry walk is a handful of `libproc` calls.
- **The event that has to start the daemon takes 108 ms p50 and 116 ms p95,** because the shim now waits until its event is delivered. Most of that is the daemon importing the package before it binds its socket; the stand-in daemon, which imports nothing, gets there in 56 ms. It happens once per daemon life: at the first event of the day, and again only after `daemon.idle_timeout_minutes` (default 120) with no event. The hook is async, so Claude Code doesn't wait for it either way (ADR-0004).
- Every run exited 0 with nothing on stdout or stderr, and each warm run's event reached the stand-in daemon.

To rerun: `.venv/bin/python spikes/hook-shim-bench/bench_plugin.py` (about 2 minutes).

## Consequences

- Every push to `main` is a plugin update for anyone who installed from the marketplace. That's intended while the package also installs from the git URL.
- A plugin update and `uv tool upgrade` can still land at different times. The shim uses only `client.py`'s protocol, which ADR-0013 keeps unversioned and internal, so a mismatch shows up as a refused event in `shim.log`.
- The hook process imports from the plugin cache's `src/`, never the tool's environment, so the shim always matches the hooks that run it.
- Inside tmux, a hook whose client lookup finds no attached client sends no `host_app`, and the daemon keeps the host it had. ADR-0006 counts a detached session's host as unknown; the protocol (ADR-0013) has no way to clear it.
