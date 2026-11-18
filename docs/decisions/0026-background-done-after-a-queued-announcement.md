---
description: When background work ends while its "still running" or "finished" is still queued or playing, the wake-up Stop holds "background done" until that announcement ends, and speaks it only if the announcement played.
status: accepted
---

# ADR-0026: Background done after an announcement still queued or playing

## Context

"Background done" is spoken only if the user heard that the work was running (ADR-0007), and "heard" is recorded only once a "still running", or a "finished" while the work ran (ADR-0023), has actually played (ADR-0013, ADR-0022). Work that ends within seconds of the turn, while that sentence is still queued behind another or still playing, reaches its wake-up `Stop` before "heard" is recorded, so "background done" was skipped. The user then heard that work was running and never heard it end.

Two fixes were considered:

- **Count it heard once queued, and undo that if it's dropped.** The wake-up `Stop` would give "background done" at once and queue it behind the announcement. But "background done" would then replace a "still running" still waiting in the single pending slot, so the user would hear the work end without hearing it start. Undoing "heard" after "background done" was already queued can't take it back.
- **Have the wake-up `Stop` check the speech queue.** If an announcement for the session is queued or playing, the `Stop` holds "background done" until it ends. Nothing is spoken out of order and nothing has to be undone.

## Decision

- **The wake-up `Stop` checks for an announcement still queued or playing.** The event worker counts, per session, the "still running" and "finished" sentences it has queued and not yet seen end. When a wake-up `Stop` ends all the work, nothing was heard yet, the session had live work and one of those is in flight, classification gives a "background done" that waits.
- **It's spoken when the announcement plays, and dropped when the announcement is dropped.** A sentence replaced in the pending slot, dropped as stale, or one that fails to play doesn't count as heard. If another announcement for the session is still coming, the wait passes to it.
- **The gate decides when it's spoken,** with presence at that moment, as for any other sentence.
- **A new user prompt, or a newer `Stop` for the session, cancels the wait,** as a new prompt clears "heard" (ADR-0007). A second wake-up `Stop` with nothing live, from two items finishing together, keeps it.
- **The count lives on the event worker,** which applies every "heard" record (ADR-0013). The speech worker reports each announcement's end, played or not, through the event inbox before it frees the slot, so the count and the session state change on one thread in one order.

The rules and the state field are in [the event classification design](../designs/event-classification.md).

## Consequences

- Work that finishes while "still running" plays is followed by "background done" straight after it.
- The waiting "background done" is held in the daemon's memory. A daemon restart loses it, along with the queued announcement it waits on, so neither is spoken.
- The 30 s staleness limit still counts from the wake-up `Stop`'s hook, so a "background done" held behind a long queue can be dropped as stale.
