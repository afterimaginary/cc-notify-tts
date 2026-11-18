---
description: Background work is read from each Stop's background_tasks, wake-up turns speak only "background done", stale Stops are dropped, and duplicates of a kind within 5 s collapse.
status: accepted
---

# ADR-0007: Event classification and session state

## Context

The plugin's hooks (ADR-0001) deliver raw payloads; the notifier speaks seven events: finished, finished with background work still running, background work done, permission, question, denied and failed. Some of these depend on what happened earlier in the session: whether background work was live at the last turn end, whether the user heard that it was, and whether the same event was just announced. That history has to survive a daemon restart, and hooks run async, so events can arrive out of order.

## Decision

- **Background work comes only from `Stop.background_tasks`.** ADR-0001 dropped the agent and task start/stop events, so the latest `Stop`'s list is the single source of what's live.
- **Wake-up turns are quiet.** When background work finishes, Claude Code submits a `<task-notification>` or `<agent-message>` prompt and ends another turn. That turn's `Stop` is never spoken as "finished". It gives "background done" only when nothing is left running and "still running" was actually heard (the daemon records that after playback), so the user is never told something finished that they didn't know was running.
- **A new user prompt clears "heard".** The user is back at the session, so a later end of background work needs no closing announcement.
- **Stale `Stop`s are dropped.** Stops are ordered by when their prompts arrived; one older than the latest handled `Stop` changes nothing. When the prompts aren't on record, a wake-up `Stop` listing work already shown finished is taken as old. Turns are matched by `prompt_id` when present.
- **`ExitPlanMode` counts as a question,** like `AskUserQuestion`: both wait on the user rather than ask permission for an action.
- **Dedupe per session and kind, 5 s from the last one emitted.** Different kinds, or different sessions, are never collapsed.
- **One JSON file per session, replaced atomically.** Closed sessions stay 5 minutes so late async events don't reopen them; open sessions silent for 24 hours are treated as crashed. Both are then pruned.

The rules and the file format are in [the event classification design](../designs/event-classification.md).

## Consequences

- If "still running" is still queued or playing when the last background item finishes, "heard" isn't recorded yet and "background done" is skipped. That needs work to finish within seconds of the turn ending, when the user has just heard about it anyway.
- A wake-up turn that does substantial new work after a background result is not announced as finished.
- Two different permission requests within 5 s collapse into one announcement.
- If another plugin's `Stop` hook makes Claude continue, the turn's second `Stop` more than 5 s later is announced as finished again; the first one can't be told apart from a real end.
