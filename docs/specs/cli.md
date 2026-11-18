# Command line

`cc-notify-tts` explains and controls the notifier. Code: `src/cc_notify_tts/cli.py`, with `status.py`, `doctor.py`, `mute.py` and `setup.py` behind it. The decisions behind it are in ADR-0017.

```
cc-notify-tts status
cc-notify-tts fire <event> [--always]
cc-notify-tts mute [--for DURATION]
cc-notify-tts unmute
cc-notify-tts doctor
cc-notify-tts setup
cc-notify-tts --version
```

`<event>` is one of `finished`, `still_running`, `background_done`, `permission`, `question`, `denied` and `failed` ([config](config.md#keys) says when each is spoken).

## Exit codes

| Code | Meaning |
| - | - |
| 0 | It did what was asked. For `fire`, that includes a notification the when-to-speak settings kept silent; for `doctor`, warnings. |
| 1 | It failed: `fire` couldn't reach or start the daemon, the daemon refused it, or speech is unavailable; `doctor` found a problem; `setup` had a step fail. |
| 2 | Usage error: an unknown command or event, a missing argument, or a duration that can't be read. |

## `status`

Whether it would speak now for each event, and why. It prints:

- **Daemon:** running (pid, version, whether speech is `loading`, `ready` or `unavailable`, and what it's saying), or not running. A running daemon of another version says so.
- **Mute:** off, on until `unmute`, or on until a time, with what's left.
- **Config:** the config file in use, whether it exists, and how many problems it has (`doctor` lists them).
- **Presence:** seconds since the last keyboard or mouse input, the idle threshold, and the app in front.
- **Would it speak now?** For each open session's host app, the gate's decision and reason for every event, computed the same way the daemon computes it ([when to speak](../designs/when-to-speak.md)). Sessions in the same app share one answer, so they're listed together. When every event gets the same reason, it's printed once as `every event`. With no session open, the app `status` runs in stands in for the host.
- **Recent decisions:** the daemon's last five gate decisions, with their reasons, when it's running.

```
Daemon    running, pid 4242, version 0.1.0, speech ready
Mute      off
Config    ~/Library/Application Support/cc-notify-tts/config.toml (no file, so every setting is its default)
Presence  last input 2 s ago (away after 20 s); Terminal in front

Would it speak now? Sessions in Terminal: weather app
  every event      silent: you were active 2 s ago and Terminal is in front

Recent decisions
  14:02:11  weather app, finished: speaking: no keyboard or mouse input for 45 s
```

Where the readings come from:

- Idle time is read when `status` runs. Running it is input, so a reading of a few seconds is normal.
- When the daemon is running, sessions, the app in front and each host's out-of-front time come from its `status` reply, since only the daemon follows the frontmost app over time.
- When it isn't, sessions come from the state folder, the app in front is read once, and a host that isn't in front counts as having left it a moment ago, which is what a daemon starting with the next event would see.

`status` never starts the daemon. Exit code 0.

## `fire <event>`

Speaks a sample notification of that kind through the daemon, so you can hear it. It starts the daemon if nothing answers, the same way a hook does, but with the CLI's own interpreter.

- The sample has a typical payload for the event, so the sentence is worded like a real one, naming the folder `fire` runs in as the project. Model wording applies as it would to a real event.
- It goes through the when-to-speak gate as a session in the app `fire` runs in. That's usually the app in front with you typing, so with the default `away` setting it's usually silent, and `fire` prints the reason and the `--always` command.
- `--always` skips the whole gate, mute and quiet hours included.
- It never touches session state, and it doesn't count as an event for the daemon's idle timeout.

```
$ cc-notify-tts fire permission --always
Speaking: "Weather app needs your okay to run npm test."
```

`fire` returns once the sentence is queued, not once it's spoken. When the speech model is still loading, it says the sentence plays in a few seconds. Exit code 1 when the daemon can't be reached or started, refuses the request, or reports speech as unavailable.

## `mute` and `unmute`

- `mute` silences every notification until `unmute`.
- `mute --for DURATION` silences them until that much time has passed. The end is shown as a clock time, with the date when it isn't today. A duration is hours, minutes and seconds in that order, each optional: `30m`, `2h`, `90s`, `1h30m`.
- A new `mute` replaces the one before it.
- `unmute` ends any mute. It's fine to run when nothing is muted.

Mute is CLI state, not config. It lives in `~/Library/Application Support/cc-notify-tts/state/mute`, a JSON object whose `until` is the Unix time the mute ends, or `null` for "until unmute". The daemon reads it for every notification, so a mute takes effect on the next one, survives a daemon restart, and a timed mute expires on its own. A file that can't be read counts as not muted.

## `doctor`

Checks everything a notification needs, one line each, marked `ok`, `warn` or `FAIL`, with a `Fix:` line under every warning and failure.

| Check | Fails when | Fix |
| - | - | - |
| macOS | not macOS | none: it runs only on macOS |
| Hook Python | `<data>/bin/python` is missing, so every hook exits doing nothing | `cc-notify-tts setup` |
| Plugin | not installed in Claude Code, disabled, or its `hooks/hooks.json` is missing. Warns when `claude` isn't on PATH or its plugin list can't be read. | `setup`, `claude plugin enable`, or reinstall |
| Speech model | a model file is missing or fails its checksum | `cc-notify-tts setup` |
| Daemon | it can't start, doesn't answer within 5 s of starting, or its speech model didn't load. Warns when it's another version than the CLI. | `setup`, the daemon log, or `kill <pid>` |
| Idle time | keyboard and mouse idle time can't be read | run it in a logged-in desktop session |
| Frontmost app | `lsappinfo` can't name the app in front | run it in a logged-in desktop session |
| Config | each unknown key or invalid value, one line each | edit the file |
| Ollama (optional) | never fails. Warns when wording is on but Ollama isn't running or the model isn't pulled. | start Ollama, `ollama pull <model>`, or `wording.ollama = false` |

When the daemon isn't running, `doctor` starts it with the interpreter `setup` linked for the hooks (`<data>/venv/bin/python`), so the check covers the path hooks use. It exits on its own after the idle timeout. While the speech model is loading, `doctor` waits up to 20 s for it to load or fail, and warns if it's still loading then.

It ends with `All checks passed.` or a count of problems and warnings. Exit code 1 when any check failed, otherwise 0.

## `setup`

Links Python, fetches the speech model and installs the plugin (ADR-0011). Exit code 1 when a step failed.
