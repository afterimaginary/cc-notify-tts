---
description: Each background item is announced as "still running" once; a turn that ends with only already-announced work live is "finished", and "background done" waits only on the work the latest "still running" announced.
status: accepted
---

# ADR-0023: Long-running background work

## Context

ADR-0007 reads live background work from each `Stop`'s `background_tasks` and gives "still running" whenever the user's turn ends with any of it live. Some background commands are meant to outlive the turn that starts them: a dev server, a file watcher, a log tail. With one running, every later turn in the session ended with "finished, but background work isn't finished", which the user already knew and which hid whether this turn had started anything new. The same item also kept "background done" from ever being spoken, since the list was never empty.

The options considered:

- **Ignore items older than the turn.** A `Stop` carries no start time per item, and an item started early in a turn can't be told apart from one started in an earlier turn without tracking ids anyway.
- **Ignore shell commands that look like servers.** Guessing from the command line is fragile and would also hide a test run that happens to be slow.
- **Announce each item once.** Needs only the ids the plugin already sees, and doesn't care what the work is.

## Decision

- **Each background item is announced as "still running" once,** by its id, for as long as it stays live. A user turn that ends with only already-announced items live is "finished".
- **A turn that starts more work announces only the new items.** The spoken detail lists those, not the long-running ones.
- **"Background done" waits on the work the latest "still running" announced,** and on any item that has never been announced. Items announced before that are left to run.
- **A "finished" that plays while that work is still live keeps "background done" armed.** The user who asks a follow-up while tests run, then steps away, still hears when the tests finish. A new user prompt disarms it as before (ADR-0007), and the turn's played "finished" arms it again.
- **An item that disappears from the list and comes back** (an agent continued with `SendMessage` keeps its id) counts as new again.
- Items count as announced when "still running" is classified, whether or not the gate let it be spoken: the user was then at the session, where Claude Code shows the work.

The rules and the state fields are in [the event classification design](../designs/event-classification.md).

## Consequences

- A dev server started in the first turn is mentioned once; later turns say "finished".
- A turn that only checks on long-running work says "finished", even though the work is still going.
- Background work finishing while a long-running item stays up is announced as "background done", so "all done" can mean "all done except what you were told is still running".
- A long-running item that the latest "still running" announced, such as a dev server started on its own, is announced as "background done" when it stops, if a "still running" or "finished" played since the user's last prompt. One announced before that stops silently.
- A dev server started in the same turn as a test run is announced with it, so "background done" waits for both.
