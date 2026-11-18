---
description: Hooks launch the shim as a script with a uv-managed Python by absolute path and `-I -S`, every handler async; that costs about 24 ms p50 and 35 ms p95 per event, and Claude Code doesn't wait on it at all.
status: accepted
---

# ADR-0004: How hooks launch the shim

## Context

Every Claude Code event the plugin listens to (ADR-0001) starts a new hook process. The brief allows at most 100 ms p95 per event, and the shim must use only the standard library. Two things decide whether that holds: what one shim process costs, which depends mostly on how its interpreter is launched, and whether Claude Code waits for it at all.

A Mac offers several interpreters: the system `/usr/bin/python3` (present only with the Command Line Tools), a uv-managed Python, the project's venv, Homebrew's, or `uv run`. Flags like `-I` (isolated) and `-S` (skip `site`) change startup work.

## Method

The spike in `spikes/hook-shim-bench/` has the scripts and the commands to rerun them.

- **The shim** does what the shipped one will per event: read the payload from stdin, write a small state file, send one line to the daemon's Unix socket and read a one-line answer. If nothing answers, it starts the daemon as a detached process (ADR-0002) and exits without waiting.
- **Launch cost:** `bench.py` runs the shim 200 times per launch option and per case (socket answering, socket absent) after 5 warm-up runs, through `/bin/sh -c` as Claude Code runs hook commands, with the options interleaved. It times each process from spawn to exit and checks that every run reached the stand-in daemon, wrote its state file and printed nothing.
- **Claude Code's wait:** `claude_wait.py` runs `claude -p` 50 times per variant with only its own hooks loaded. A hook blocks the prompt, so no model request is made. The hook under test runs on the two events that fire without a model: `SessionStart` (time from the event's hooks starting to the prompt's `UserPromptSubmit`) and `SessionEnd` (time from closing Claude Code's input to the process exiting). A 2 s sleep run before the shim shows the method can see a wait. Claude Code takes well under 2 s to exit, so when that hook is async, the time its message reaches the stand-in daemon, before or after the `claude` process has exited, shows whether the hook outlives the exit.

Machine: Apple M3 Max, macOS 27.2.1, Claude Code 2.1.327, uv 0.11. Other work was running, as in normal use.

## Results

### Launch cost per event

Wall time per hook process, ms, 200 runs per cell.

| Launch | Socket answering p50 | p95 | Socket absent p50 | p95 |
| - | - | - | - | - |
| Floor: `sh -c true`, no Python | 5.3 | 6.8 | 5.2 | 7.5 |
| System `/usr/bin/python3` (3.9) | 34.5 | 41.3 | 39.0 | 48.1 |
| System `/usr/bin/python3 -I -S` | 67.9 | 77.4 | 83.3 | 98.7 |
| uv-managed 3.13 | 24.7 | 29.1 | 28.3 | 34.6 |
| **uv-managed 3.13 `-I -S`** | **23.7** | **27.9** | **27.6** | **34.9** |
| Project venv python | 24.3 | 28.7 | 29.1 | 35.6 |
| Project venv python `-I -S` | 23.4 | 28.0 | 28.0 | 33.9 |
| Homebrew python3 (3.14) `-I -S` | 27.2 | 32.1 | 33.4 | 41.7 |
| `uv run --frozen python` | 36.7 | 43.3 | 41.4 | 52.4 |

Every option is under 100 ms p95. The uv-managed interpreter is the fastest, and the venv adds nothing measurable over it. Starting the daemon when the socket is absent adds about 4 ms p50 (importing `subprocess` and spawning).

Two findings:

- **`-I` makes the system Python twice as slow.** Its launcher points `PYTHONPYCACHEPREFIX` at a cache in the user's Library, because the stdlib directory isn't writable. `-I` (and `-E`) ignore that variable, so every import is compiled again on every run. The system Python is also absent on a Mac without the Command Line Tools, where calling it opens an install dialog.
- **`uv run` costs about 13 ms p50 per event** over calling the same interpreter directly, because it checks the project and lockfile each time.

On the uv-managed interpreter, `-X importtime` shows the shim's imports cost about 7 ms: `json` 4 ms (it pulls in `re` and `enum`), `subprocess` 2 ms (only when starting the daemon), `socket` 1 ms. Interpreter startup is the rest.

### Claude Code's wait

ms, 50 runs per variant. "Added" is the variant's p50 minus the no-hook floor.

| Hook under test | `SessionStart` held p50 / p95 | Added | Exit after `SessionEnd` p50 / p95 | Added | Shim delivered (start / end) | `SessionEnd` message arrived after exit |
| - | - | - | - | - | - | - |
| None (floor) | 39 / 45 | 0 | 609 / 726 | 0 | n/a | n/a |
| Shim, sync | 55 / 63 | +16 | 669 / 807 | +60 | 50/50, 50/50 | 0/50 |
| Shim, `async` | 41 / 46 | +2 | 615 / 809 | +6 | 50/50, 50/50 | 0/50 |
| 2 s sleep, then shim, sync | 2086 / 2092 | +2047 | 2599 / 2799 | +1990 | 50/50, 50/50 | 0/50 |
| 2 s sleep, then shim, `async` | 40 / 49 | +1 | 562 / 722 | −47 | 50/50, 50/50 | 50/50 |

- **Sync:** Claude Code waits for the whole hook. The 2 s sleep adds about 2 s on both events.
- **`async`:** Claude Code doesn't wait. The shim and even the 2 s hook add 2 ms or less at session start, which is within noise.
- **At exit**, exit time is noisy (p95 about 100–200 ms above p50), so differences under about 50 ms there aren't meaningful. The async 2 s hook didn't delay the exit: `claude` exited about 0.6 s after its input closed, and the hook's message, sent only after the sleep, arrived after the process had exited in all 50 runs. So Claude Code exits without waiting for async hooks, and they keep running after it has gone. Sync hooks finish before the exit, as expected. ADR-0001 saw an async `StopFailure` handler lost when `claude -p` exited; that was a different event and may have been killed some other way. The shipped daemon doesn't need `SessionEnd` delivered to work correctly.

`UserPromptSubmit`, `Stop` and the permission events need a model turn, which costs tokens, so they weren't measured. Claude Code runs every event's hooks the same way, so `SessionStart` stands in for them.

## Decision

- The plugin's hook commands run the shim as a **script file with a uv-managed Python 3.13 by absolute path, with `-I -S`**: `<python> -I -S <shim script>`. The installer (#7) decides which uv-managed interpreter that is. `-I -S` gains little speed on this interpreter, but it keeps a user's `PYTHONPATH`, `PYTHONSTARTUP` and user site-packages out of the hook.
- **Not `uv run`** (13 ms more per event, plus a project check), and **not the system Python** (may be missing, and slow with `-I`).
- **Every handler stays `async: true`** (ADR-0001), so the shim's 24 ms p50 and 35 ms p95 never hold up the session. Sync would still have been under budget.
- These numbers cover the shim's core per event: read the payload, one small file write and one socket round trip, with `subprocess` imported only when the daemon has to start. Later decisions add to it, such as the host-app lookup at `SessionStart` and, inside tmux, `tmux list-clients` (ADR-0006); hooks are async, so that doesn't hold up the session either. The spike's file write is only a stand-in: the per-session state files are written by the daemon alone (ADR-0007), so the shipped shim passes what it learns to the daemon rather than writing them.

## Consequences

- The plugin depends on the installed uv-managed interpreter staying at the path the installer wrote. If uv removes or upgrades it, hooks fail silently until the installer runs again. uv keeps a per-minor-version link (`.../uv/python/cpython-3.13-macos-aarch64-none`) that follows patch upgrades, so the installer should write that path rather than a patch-specific one. The `doctor` command should check the path.
- `-I` keeps the script's own directory off `sys.path` and `-S` skips site-packages, so the shim can't import a module next to it or from the package's venv. The shipped shim is one self-contained file, or it sets up `sys.path` itself.
- The spike shim waits up to 200 ms for an answer and then starts the daemon. A daemon that has bound its socket but isn't answering yet, such as one still loading the speech model, would get a second daemon started. The daemon should answer on its socket before it loads the model.
- The 100 ms budget has about 65 ms of headroom at p95, sync or not. Dropping `json` from the shim would save 4 ms, which isn't worth the hand parsing.
