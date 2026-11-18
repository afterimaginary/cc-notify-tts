# Daemon protocol

How the hook shim and the CLI talk to the daemon. The protocol is internal: the shim and the CLI ship with the daemon, so there's no versioning. The decisions behind it are in ADR-0013.

Code: `src/cc_notify_tts/daemon.py` (`Daemon.handle_request`, and `request()` / `is_answering()` for Python clients).

## Transport

- A Unix stream socket at `~/Library/Application Support/cc-notify-tts/daemon.sock`, mode `0600`.
- One request per connection: the client connects, sends one JSON object on one line ending in `\n`, reads one JSON object on one line, and closes.
- A request is at most 8 MiB. A client that sends nothing for 2 s is disconnected without a reply.
- Every reply has `ok`. When it's `false`, `error` says why in a few words, and the request had no effect.

## Requests

### `ping`

Answered as soon as the daemon has bound its socket, before the speech model loads. A client that gets no answer within 200 ms may start a daemon; a second daemon exits on its own (see "Single instance").

```json
{"type": "ping"}
```

```json
{"ok": true, "pid": 4242, "version": "0.1.0"}
```

### `event`

One hook event. The reply means it was queued, not that it was classified or spoken.

```json
{"type": "event", "fired_at": 1763452800.125, "host_app": "com.apple.Terminal", "payload": {"session_id": "…", "hook_event_name": "Stop", "…": "…"}}
```

| Field | Required | Meaning |
| - | - | - |
| `payload` | yes | The hook's JSON input, unchanged. |
| `fired_at` | no | Wall-clock time (Unix seconds) the hook started, read as the shim's first step. Events are classified in this order. Missing, not a number, or later than arrival: arrival time is used. |
| `host_app` | no | Bundle id of the app the session runs in (ADR-0006). The shim sends it at `SessionStart`, and inside tmux on every hook that can announce something. When present and non-empty, it replaces the one on record for the session. |

```json
{"ok": true}
```

### `status`

What the daemon is doing, for `cc-notify-tts status`.

```json
{"type": "status"}
```

```json
{
  "ok": true,
  "pid": 4242,
  "version": "0.1.0",
  "started_at": 1763452700.0,
  "last_event_at": 1763452800.2,
  "idle_timeout_seconds": 7200.0,
  "speech": "ready",
  "speaking": "Finished up in weather app.",
  "pending": null,
  "front_app": "com.apple.Terminal",
  "sessions": [
    {"session_id": "…", "project": "weather app", "host_app": "com.apple.Terminal", "out_of_front_seconds": 0.0}
  ],
  "recent": [
    {"at": 1763452800.2, "session_id": "…", "project": "weather app", "event": "finished", "speak": true, "reason": "speaking: finished is set to always"}
  ]
}
```

| Field | Meaning |
| - | - |
| `speech` | `loading` while the model loads, `ready`, or `unavailable` if it failed to load (the log says why). |
| `speaking`, `pending` | The sentence playing now and the one waiting, or `null`. |
| `front_app` | Bundle id of the app in front, or `null` when the frontmost app can't be followed. |
| `sessions` | Open sessions, with how long each one's host app has been out of front (`null` when unknown). |
| `recent` | The last 20 gate decisions, oldest first, with the gate's reason. |

## Processing

1. **Order.** Each event is held for 100 ms after it arrives, and held events are classified earliest `fired_at` first. Hooks run async in separate processes, so a turn's `Stop` can reach the socket before its own `UserPromptSubmit`; classifying them in arrival order would make the user's turn silent (ADR-0007). An event that arrives more than 100 ms after a later-fired one was classified is classified on arrival.
2. **Classification** updates the session's state and returns at most one notification ([event classification](../designs/event-classification.md)).
3. **The gate** decides whether to speak, with a reason ([when to speak](../designs/when-to-speak.md)). The config file is read for each notification.
4. **Wording:** the template sentence ([spoken messages](spoken-messages.md)).
5. **Queue.** One sentence plays at a time, and at most one waits. A newer sentence replaces the waiting one. A sentence whose hook fired more than 30 s before it would start playing is dropped.
6. **Speech** synthesizes and plays it. After a `still_running` sentence actually plays, the daemon records it as heard in the session's state, on the same worker as classification.

## Lifetime

- **Start:** `<data>/venv/bin/python -I -m cc_notify_tts.daemon`, detached (ADR-0002, ADR-0011). The shim starts it when `ping` gets no answer, then retries sending its event until the socket answers. Retrying at least every 50 ms keeps events from several hooks within the 100 ms ordering window.
- **Single instance:** the daemon holds an exclusive `flock` on `daemon.sock.lock` for its whole life. A daemon that can't take it exits with status 0: at once if the socket answers a `ping`, otherwise after waiting up to 3 s for the lock, in case the holder is exiting. The holder removes any socket file before binding, since it can only be a dead daemon's.
- **Idle exit:** once no event has arrived for `daemon.idle_timeout_minutes` (config, default 120) and nothing is waiting or playing, the daemon removes its socket and exits with status 0. A session ends with an event, and a session without `SessionEnd` counts as closed once the timeout has passed since its last event, so this is also "no session open for the timeout". `ping` and `status` don't count as events.
- **Signals:** `SIGTERM` and `SIGINT` stop it the same way.
- **Log:** `~/Library/Logs/cc-notify-tts/daemon.log`, rotated at 1 MB with two old files kept. Each gate decision is logged with its reason.
