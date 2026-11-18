---
description: Mute is a small file in the state folder that the daemon's gate reads on every notification; `status` runs the gate itself with the daemon's presence readings; `fire` is a daemon request that skips classification and session state; `doctor` starts the daemon the way a hook would and fails only on what stops or distorts a notification.
status: accepted
---

# ADR-0017: How the CLI's `status`, `fire`, `mute` and `doctor` work

## Context

The CLI's commands are fixed by the brief and the plan: `status` (would it speak now, and why), `fire <event>` (with `--always`), `mute` and `unmute` (with `mute --for 30m`), `doctor` and `setup`. Mute is CLI state with an expiry, not a config key (ADR-0009). Four questions were left:

- Where mute lives, and how the daemon learns about it.
- Where `status` gets its answer, given that only the daemon follows the frontmost app over time (ADR-0006).
- How `fire` gets a notification through the daemon without disturbing real sessions.
- What `doctor` treats as a failure, and whether it may start the daemon.

## Decision

### Mute is a file the gate reads

- `cc-notify-tts mute` writes `state/mute` in the data folder: `{"until": <unix time>}`, or `{"until": null}` for "until unmute". `unmute` deletes it. Writes are atomic (temp file, then rename).
- The daemon's gate reads it through `MuteFile.is_muted()` for every notification. That costs one small file read per notification, which is nothing next to the config read already done there (ADR-0009). In return there's no mute request in the protocol, a mute works whether or not the daemon is running, and it survives a restart without the daemon persisting anything.
- A timed mute expires by being read after its time: nothing has to wake up to end it. An unreadable file counts as not muted, because the gate speaks when unsure (ADR-0006).
- The file isn't named `*.json`. The state folder's `*.json` files are sessions, and pruning deletes any it can't read as one (ADR-0007).

### `status` runs the gate itself

- `gate.decide` is pure, so the CLI calls it with the same inputs the daemon would use: the config file, the mute file, idle time read now, and a host app with its out-of-front time.
- **Host apps and out-of-front times come from the daemon** when it's running: its `status` reply already lists open sessions with both. Sessions in the same host app get the same answer, so they're shown as one group.
- **When the daemon isn't running,** sessions come from the state folder and a host counts as out of front for 0 s, which is what a daemon started by the next event would see. With no session open, the app `status` runs in stands in for the host, so the answer is about a session there.
- The daemon's recent decisions are shown too, because "why was that turn silent" is answered by the decision the daemon actually made, not by a new one.
- `status` never starts the daemon: it reports, it doesn't change anything.

### `fire` is its own request

- `fire` sends a new protocol request, `{"type": "fire", "event": …, "always": …, "cwd": …, "host_app": …}`. The daemon builds a sample notification for that event (`fire.py`), gates it unless `always` is set, words it and queues it like any other, and replies with the decision and the sentence.
- **Not a sequence of fake hook events.** Sending synthetic `SessionStart` and `Stop` events would write session files, open a session that keeps the wording model loaded (ADR-0015), be caught by the 5 s dedupe on a second try, and need a staged sequence for `still_running` and `background_done`. A request that skips classification has none of those effects.
- It runs on the socket thread, since it touches no session state; the event worker owns that state (ADR-0013). Wording may wait up to the wording timeout for the model, so the CLI first makes sure the daemon is up with a `ping` through `client.deliver` (starting it with its own interpreter, ADR-0014), then sends `fire` with a longer timeout. Sending `fire` itself through `deliver` would let a reply slower than 200 ms start a second daemon and resend, speaking twice.
- The gate sees the sample as a session in the app `fire` runs in. `--always` skips the whole gate, mute and quiet hours included: it's for hearing the voice on demand.

### `doctor`

- Each check is `ok`, `warn` or `fail`, and every warning and failure carries a fix. It exits 1 on any failure.
- A failure is anything that stops a notification or changes when one is spoken: a missing hook Python, model file or plugin, a daemon that can't start or load speech, an unreadable presence signal (so `away` always speaks), or a config problem (so a setting isn't applied). Ollama is optional, so it only ever warns, as do checks it can't run (`claude` not on PATH) and a daemon of another version.
- If the daemon isn't running, `doctor` starts it with `<data>/venv/bin/python`, the interpreter hooks use, so the check proves the path hooks take rather than the CLI's own. A daemon that just started is still loading the speech model, so `doctor` waits up to 20 s for it to load or fail: otherwise a model that can't load would pass. A daemon started this way exits after the idle timeout, like any other.
- The plugin is checked with `claude plugin list --json`: an enabled `cc-notify-tts@cc-notify-tts` entry whose `hooks/hooks.json` exists. There's no plugin version to compare yet (ADR-0014), so a stale plugin isn't detected.

## Consequences

- The protocol gains one request, `fire` ([protocol](../specs/daemon-protocol.md)). Like the rest, it's internal and unversioned.
- `gate.NeverMuted` is left for tests; the daemon defaults to `MuteFile`.
- `status` shows what the gate would decide, not what the daemon last decided, unless you read its recent decisions. The two can differ by a few seconds of idle time: running `status` is itself input.
- Usage and exit codes are in the [CLI spec](../specs/cli.md).
