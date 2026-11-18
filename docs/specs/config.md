# Config file

What the config file holds and how it's read. Code: `src/cc_notify_tts/config.py`.

## Where it lives and when it's read

- `~/Library/Application Support/cc-notify-tts/config.toml`, in [TOML](https://toml.io).
- Every key is optional and has its default in code. A missing file means all defaults, and the file is never created for you.
- It's read again for every event, so an edit takes effect on the next notification without restarting anything. Reading it takes about 0.1 ms (ADR-0009).
- Mute isn't in the file: it's CLI state (`cc-notify-tts mute`).

## When something's wrong

A bad config file never stops a notification and never crashes anything.

- **Unknown key** (a misspelling, a table or event that doesn't exist): ignored.
- **Invalid value** (wrong type, out of range, not one of the choices): that key alone falls back to its default; the rest of the file still applies.
- **A file that can't be read or parsed** (a TOML syntax error, bytes that aren't UTF-8, no permission): all defaults.

Each problem is a one-line message naming the key, for example `presence.idle_seconds: -1 isn't a number, 0 or more, using 20`. They're written to the log when they change (not on every event) and listed by `cc-notify-tts doctor`.

## Keys

| Key | Type | Default | Meaning |
| - | - | - | - |
| `events.<event>.speak` | `"away"`, `"always"` or `"never"` | `"away"` | When to speak this event. `away`: only when you're away (see `presence`). `always`: whenever it happens. `never`: not at all. |
| `quiet_hours.start` | time, `"HH:MM"` (24-hour, local) | unset | Start of a daily window with no speech. |
| `quiet_hours.end` | time, `"HH:MM"` (24-hour, local) | unset | End of the window. An end earlier than the start crosses midnight. |
| `presence.idle_seconds` | number of seconds, 0 or more | `20` | No keyboard or mouse input for this long counts as away. |
| `presence.away_seconds` | number of seconds, 0 or more | `10` | The app the session runs in out of front for this long counts as away. |
| `daemon.idle_timeout_minutes` | number of minutes, above 0 | `120` | The background process exits after this long with no event, and starts again with the next one. |
| `wording.ollama` | `true` or `false` | `true` | Let a local Ollama model write the detail of a finished turn, a question or a permission request. When it's off, missing, slow or its answer fails a check, the built-in template wording is used. |
| `wording.model` | Ollama model name | `"granite4:micro"` | The Ollama model that writes the detail, picked by the wording eval's model comparison (ADR-0018). `phi4-mini:3.8b` is the runner-up; `gemma3:1b` holds the least memory. |
| `wording.timeout_seconds` | number of seconds, above 0 | `1` | How long to wait for the model before using the template. |
| `speech.voice` | Kokoro voice name | `"af_heart"` | The voice that speaks every notification. |

`<event>` is one of the seven events:

| Event | Spoken when |
| - | - |
| `finished` | A turn finished. |
| `still_running` | A turn finished with background agents or commands still running. Each is announced once; later turns while it runs are `finished`. |
| `background_done` | All background work finished, if you heard `still_running` or a `finished` while it ran. |
| `permission` | Claude asks permission for an action. |
| `question` | Claude asks you a question or presents a plan. |
| `denied` | An action was denied. |
| `failed` | A turn failed, such as on an API error. |

Quiet hours are off unless both `start` and `end` are set. Setting only one, or the same time for both, is reported as a problem and leaves quiet hours off. Either form of time works: a string such as `"22:00"` or a TOML local time such as `22:00:00`.

Numbers can be written with or without a decimal point (`20` or `20.0`). A `presence` threshold of `0` means that signal always counts as away.

## Example

Every key at its default, with quiet hours shown but left off. Copy it to the path above and change what you need; keys you leave out keep their defaults.

```toml
# cc-notify-tts config. Read again for every event; no restart needed.

# When to speak each event: "away" (only when you're away), "always" or "never".
[events.finished]
speak = "away"

[events.still_running]
speak = "away"

[events.background_done]
speak = "away"

[events.permission]
speak = "away"

[events.question]
speak = "away"

[events.denied]
speak = "away"

[events.failed]
speak = "away"

# A daily window with no speech, in local 24-hour time. Off unless both are set.
# An end earlier than the start crosses midnight.
[quiet_hours]
# start = "22:00"
# end = "08:00"

# "away" means either one of these, not both.
[presence]
# No keyboard or mouse input for this many seconds.
idle_seconds = 20
# The app the session runs in hasn't been in front for this many seconds.
away_seconds = 10

[daemon]
# The background process exits after this many minutes with no event.
idle_timeout_minutes = 120

[wording]
# Let a local Ollama model write the detail. Templates are used whenever it's
# off, missing, slow or fails a check.
ollama = true
model = "granite4:micro"
# Seconds to wait for the model before using the template.
timeout_seconds = 1

[speech]
voice = "af_heart"
```
