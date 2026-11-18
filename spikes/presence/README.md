# Spike: presence from a hook

Answers the questions behind ADR-0006. Can the daemon tell that the user is away, without asking for any macOS permission? Which app hosts a Claude Code session, as seen from a hook? Can the daemon follow how long that app has been out of front, and what does each read cost?

`presence_probe.py` is a self-contained [`uv` script](https://docs.astral.sh/uv/guides/scripts/). Its one dependency, PyObjC's Cocoa bindings, is declared inline and used only for the `NSWorkspace` comparisons, so it adds nothing to the project's environment. It isn't part of `make ready` or `make verify`.

## Run it

```sh
uv run --script spikes/presence/presence_probe.py idle            # idle time: ioreg vs CoreGraphics
uv run --script spikes/presence/presence_probe.py front           # frontmost app: lsappinfo vs NSWorkspace
uv run --script spikes/presence/presence_probe.py host            # which app hosts this shell
uv run --script spikes/presence/presence_probe.py track --seconds 300
```

| Subcommand | Measures |
| --- | --- |
| `idle` | Seconds since the last keyboard or mouse event, read 200 times each with `ioreg -c IOHIDSystem` (a subprocess) and with `CGEventSourceSecondsSinceLastEventType` through ctypes. Reports wall and CPU time per read and how far the two readings differ. |
| `front` | The frontmost app's bundle id, read 200 times each with `lsappinfo` (two subprocesses) and with `NSWorkspace.frontmostApplication`. Also times importing AppKit and the memory it adds. |
| `host` | The app hosting the current process, three ways: `__CFBundleIdentifier`, `TERM_PROGRAM`, and the outermost `.app` bundle among its ancestor processes. Inside tmux it also asks tmux for the attached client's pid and walks that client's ancestors. `--out FILE` appends the result as a JSON line. |
| `track` | Watches the frontmost app for `--seconds`, polling every `--interval` seconds. One process runs a run loop, receives `NSWorkspace` activation notifications and polls both `lsappinfo` and `NSWorkspace`'s property. A second process polls the property without ever running a run loop. Reports what each method saw, whether the property went stale, and CPU time per poll. |

| `watch` | Activation notifications only, no polling, for `--seconds`: CPU time while watching, resident memory with AppKit loaded, and every switch seen. `--out FILE` appends the result as a JSON line, so it can run detached. |
| `listen` | Runs `lsappinfo listen +becameFrontmost` for `--seconds` and reads it through a pipe, as the daemon would. Reports each switch's bundle id, how long after the switch its line arrived, and the listener's CPU time and peak memory. |

`track`, `watch` and `listen` only watch: they never change which app is in front. Switch between apps yourself while they run to give them something to see.

To run `watch` the way the hook shim starts the daemon (new session, stdio to `/dev/null`, adopted by launchd):

```sh
python3 -c 'import subprocess, sys; subprocess.Popen(sys.argv[1:], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)' \
  uv run --script "$PWD/spikes/presence/presence_probe.py" watch --seconds 600 --out /tmp/presence-watch.jsonl
```

To check a terminal, open a new window in it and run the `host` subcommand there. A hook inherits Claude Code's environment and Claude Code inherits the shell's, so a shell in the terminal sees what a hook would.

## Cleaning up

Every subcommand stops by itself, and nothing is written outside the `--out` file you name. A detached `watch` runs until its `--seconds` end; to stop it early:

```sh
pkill -f 'presence_probe.py watch'
```
