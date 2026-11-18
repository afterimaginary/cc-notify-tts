# hook-shim-bench (spike, not shipped)

Measures what a standard-library Python hook shim costs per Claude Code event, and how long Claude Code waits on it with and without `async: true`. Results and the chosen launch method are in ADR-0004 (`docs/decisions/`).

| File | What it does |
| - | - |
| `shim.py` | The work a real shim does per event: read the payload from stdin, write a small state file, send one line to the daemon's Unix socket and read its answer, or start the daemon detached when nothing answers. Standard library only, runs on Python 3.9 and later. |
| `bench.py` | Times `shim.py` under each launch option (system Python, uv-managed Python, the project venv, Homebrew, `uv run`), with the socket answering and with it absent, through `/bin/sh -c` as Claude Code runs hooks. |
| `bench_plugin.py` | The same timing against the shipped plugin: the commands in `hooks/hooks.json` and `hooks/shim.py`, in a throwaway `HOME` laid out as `setup` leaves it, with the daemon running and with the shim having to start it. Results in ADR-0014. |
| `claude_wait.py` | Runs `claude -p` with the shim on `SessionStart` and `SessionEnd`, sync and async, and reports how long each held the session and whether an async `SessionEnd` hook outlives Claude Code's exit. A hook blocks the prompt, so no model request is made. |

```sh
uv sync                                               # the uv-managed interpreter behind .venv
python3 spikes/hook-shim-bench/bench.py --runs 200    # launch options, about 5 minutes
python3 spikes/hook-shim-bench/claude_wait.py --runs 50   # Claude Code's wait, about 10 minutes
.venv/bin/python spikes/hook-shim-bench/bench_plugin.py   # the shipped shim, a few minutes
```

Both write only to a temporary directory they delete afterwards. In the "socket absent" case the shim starts `/usr/bin/true` in place of the daemon: the shim only pays for spawning a process, whatever that process then does.
