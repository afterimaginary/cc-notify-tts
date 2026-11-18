# Event classification and session state

How raw hook payloads become the seven notification events, and what each session keeps on disk. The decisions behind it are in ADR-0007; the hook list it reads is ADR-0001.

Code: `src/cc_notify_tts/events.py` (classification, dedupe) and `src/cc_notify_tts/sessions.py` (state files). Both use only the standard library. There is no file locking: only the daemon calls `classify` and `mark_still_running_heard`, and it must run both on the same queue, one at a time.

## Interface

- `classify(store, payload, now) -> Notification | None` reads the session's state, applies the payload, writes the state back and returns at most one notification.
- `Notification` carries the `kind`, `session_id`, `cwd`, the raw `payload` (for wording), `turn_seconds` (since the user's latest prompt, if known) and, for `still_running`, the live `background` items.
- `SessionStore.mark_still_running_heard(session_id)`: the daemon calls it after a `still_running` utterance actually plays. It's ignored once no background work is live.
- `SessionStore.open_sessions(now)` lists sessions that are open and have had an event in the last 24 hours. `SessionStore.prune(now)` deletes closed and stale sessions; `classify` runs it on every `SessionEnd`.

## Events

| Kind | From |
| - | - |
| `finished` | `Stop` ending the user's turn with no live background work |
| `still_running` | `Stop` ending the user's turn with live background work |
| `background_done` | `Stop` of a wake-up turn that leaves no live background work, if `still_running` was heard |
| `permission` | `PermissionRequest` for any tool except the two below |
| `question` | `PermissionRequest` for `AskUserQuestion` or `ExitPlanMode` |
| `denied` | `PermissionDenied` |
| `failed` | `StopFailure` |

`SessionStart`, `SessionEnd` and `UserPromptSubmit` only update state. Any other hook event is ignored.

## Turns and background work

- A `UserPromptSubmit` whose prompt starts with `<task-notification>` or `<agent-message` is a wake-up Claude Code sends when background work finishes, not a user turn. Any other prompt starts a user turn: it records the time and `prompt_id`, and clears "still running heard", since the user is back.
- A `Stop` belongs to the user's turn when its `prompt_id` matches the turn's. Without prompt ids, the first `Stop` after a user prompt does.
- Live background work is the `background_tasks` list of the latest `Stop`, minus items whose `status` says they ended. Items live at one `Stop` and absent from a later one are remembered as finished (the latest 64).
- A `Stop` is a wake-up turn's when its `prompt_id` is a recorded wake-up prompt's or, if that prompt hasn't arrived yet, when it doesn't belong to the user's turn and follows a `Stop` that had live work. A wake-up turn is never `finished`. It says nothing while work is still live. When the list is empty, it gives `background_done` if "still running" was heard, and nothing otherwise.

## Out-of-order events

Hooks run async, so two events can be handled out of order.

- A `Stop` whose prompt arrived before the latest handled `Stop`'s prompt is older than it. It's dropped and changes nothing.
- When either prompt isn't on record, a `Stop` outside the user's turn that lists an item already known to be finished is dropped the same way. The user's own turn is never dropped, and an id that shows up live again (an agent continued with `SendMessage` keeps its id) stops counting as finished.
- A `UserPromptSubmit` whose `prompt_id` matches the latest `Stop`'s arrived after its own turn ended, and is ignored.

## Dedupe

A notification of the same kind for the same session within 5 s of the last one emitted is dropped. State updates still apply.

## State file

One JSON file per session, `<session_id>.json` under `~/Library/Application Support/cc-notify-tts/state/`. A session id that isn't 1–128 letters, digits, `-` or `_` is replaced by its SHA-256, so a payload can't choose a path. Each write goes to a temp file in the same directory, then `os.replace`. A file that can't be read or doesn't match the fields below counts as missing.

| Field | Meaning |
| - | - |
| `session_id` | From the payloads |
| `cwd` | From the session's first payload that has one, so a later `cd` doesn't rename the project |
| `open`, `closed_at` | Cleared and set by `SessionEnd`; `SessionStart` reopens |
| `last_event_at` | Time of the latest hook event |
| `turn_started_at`, `turn_prompt_id`, `turn_open` | The user's latest turn, and whether its `Stop` is still to come |
| `last_stop_prompt_id` | `prompt_id` of the latest `Stop` |
| `background` | Live items at the latest `Stop`: `id`, `type`, `description` |
| `prompt_ids` | `prompt_id`s of recent prompts, user and wake-up, in arrival order (the latest 64) |
| `wake_prompt_ids` | `prompt_id`s of recent wake-up prompts (the latest 64) |
| `finished_background` | Ids known to have finished |
| `still_running_heard` | A `still_running` announcement played and no `background_done` or new user turn has followed |
| `last_emitted` | Kind to time last emitted, for dedupe |

Any event for a session without a file creates it as open, so sessions that started before the plugin was installed still work. `SessionEnd` for an unknown session writes nothing.

## Pruning

- A closed session's file stays for 5 minutes, so a late async event doesn't recreate it as open, then is deleted.
- An open session with no event for 24 hours is deleted, since a crash or a killed terminal can skip `SessionEnd`.
- Unreadable files are deleted.
