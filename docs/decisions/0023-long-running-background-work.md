---
description: Each background item is announced as "still running" once; a turn that ends with only already-announced work live is "finished", and a played "finished" keeps "background done" armed while that work runs.
status: accepted
---

# ADR-0023: Long-running background work

## Context

ADR-0007 reads live background work from each `Stop`'s `background_tasks` and gives "still running" whenever the user's turn ends with any of it live. Some background commands are meant to outlive the turn that starts them: a dev server, a file watcher, a log tail. With one running, every later turn in the session ended with "finished, but background work isn't finished", which the user already knew and which hid whether this turn had started anything new.

The options considered:

- **Ignore items older than the turn.** A `Stop` carries no start time per item, and an item started early in a turn can't be told apart from one started in an earlier turn without tracking ids anyway.
- **Ignore shell commands that look like servers.** Guessing from the command line is fragile and would also hide a test run that happens to be slow.
- **Announce each item once.** Needs only the ids the plugin already sees, and doesn't care what the work is.

## Decision

- **Each background item is announced as "still running" once,** by its id, for as long as it stays live. A user turn that ends with only already-announced items live is "finished".
- **A turn that starts more work announces only the new items.** The spoken detail lists those, not the long-running ones.
- **"Background done" still waits for all background work to end** (ADR-0007).
- **A "finished" that plays while background work is live keeps "background done" armed.** The user who asks a follow-up while tests run, then steps away, still hears when the tests finish. A new user prompt disarms it as before (ADR-0007), and the turn's played "finished" arms it again.
- **An item that disappears from the list and comes back** (an agent continued with `SendMessage` keeps its id) counts as new again.
- Items count as announced when "still running" is classified, whether or not the gate let it be spoken: the user was then at the session, where Claude Code shows the work.

The rules and the state fields are in [the event classification design](../designs/event-classification.md).

## Consequences

- A dev server started in the first turn is mentioned once; later turns say "finished".
- A turn that only checks on long-running work says "finished", even though the work is still going.
- A dev server that runs all session also holds up "background done" for later work, such as a test run, until the server stops. "Background done" can't tell a server from a slow test run any better than "still running" can: letting long-announced work stop holding it up said "all done" while tests started a turn earlier still ran.
- When the dev server stops, "background done" is spoken if a "still running" or "finished" played since the user's last prompt.
- With `finished` set to `never`, a follow-up turn while work runs plays nothing, so "background done" stays disarmed and the work's end is silent. Before, that turn was a "still running", which played and re-armed it.
