---
description: The daemon serves one-line JSON requests on a Unix socket, guards single instance with a lock file, classifies events in hook-firing order after a 100 ms hold, speaks through a single pending slot that drops sentences over 30 s old, and exits after the idle timeout with no event.
status: accepted
---

# ADR-0013: Daemon protocol, ordering and speech queue

## Context

The daemon is the one long-lived process: hooks hand it raw events, and it classifies, gates, words and speaks them. Earlier decisions constrain it:

- The shim waits at most 200 ms for an answer before starting a daemon, so the daemon must answer as soon as it's listening, before the speech model loads (ADR-0004).
- Two hooks can start two daemons at the same moment; one must win and the other exit quietly (ADR-0002).
- Hooks run async in separate processes, so their messages can arrive out of order. Classification assumes a session's events come in the order the hooks fired, and its state has no locking, so classification and "heard" records must run one at a time on one queue (ADR-0007).
- The brief asks for one utterance at a time, one pending slot, and stale messages dropped.

## Decision

- **Transport: one JSON request per connection on a Unix stream socket**, a line each way. Three requests: `ping`, `event` and `status` ([protocol spec](../specs/daemon-protocol.md)). The standard library has everything needed on both sides, so the stdlib-only shim stays small, and one request per connection means no framing or session state. HTTP would add a dependency or a hand-written parser for no gain on a local socket.
- **The socket is bound before anything slow.** The daemon module imports only standard-library code and the project's stdlib modules; the speech model is imported and loaded on the speech worker after the socket answers. Measured from process start to the first answered `ping`: 52 ms.
- **Single instance: an exclusive `flock` on a lock file next to the socket**, held for the daemon's life. The kernel releases it when the process dies, so a crash never leaves a false "running". With the lock held, any socket file on disk is a dead daemon's and is replaced, which recovers a stale socket without guessing. Probing the socket alone can't do that safely: two starting daemons could each find it dead and unlink each other's socket. A starter that finds the lock held exits at once if the socket answers, and otherwise waits up to 3 s, since the holder may be exiting and has already removed its socket.
- **Events are classified in the order their hooks fired.** The shim stamps each event with the wall-clock time the hook started. The daemon holds each event 100 ms after arrival and hands out held events earliest-stamped first. A shim takes about 35 ms p95 from start to send (ADR-0004), so 100 ms covers normal jitter, and on a cold start every waiting shim connects within one retry interval of the socket appearing. It adds 100 ms to every notification, which already waits on synthesis taking 0.27–0.9 s (ADR-0005). Ordering only by arrival leaves a turn silent whenever its `Stop` overtakes its `UserPromptSubmit` while background work is live.
- **Three threads, one owner per piece of state.** Socket threads only parse and enqueue. One event worker classifies, gates, words, records "heard" and checks the idle timeout, so session state has a single writer. One speech worker loads the model and plays. "Heard" goes from the speech worker back through the event inbox, ahead of held events, because playback finished before any event still held was classified.
- **A single pending slot between the two workers.** A newer sentence replaces the waiting one, since the latest news is what matters. Age is checked when a sentence is about to play, against the time its hook fired, and over 30 s is dropped: a sentence about something that long gone is noise. A sentence still plays when presence changed while it waited; the gate isn't asked twice.
- **Idle exit: no event for `daemon.idle_timeout_minutes`, and nothing waiting or playing.** Every session event reaches the daemon, so the time since the last event is the time since a session was last open, counting a session that skipped `SessionEnd` as closed once the timeout has passed since its last event. Reading the session files would give the same answer with more I/O. Pings and status requests don't count, so a `status` loop can't keep it alive.
- **Speech that fails to load doesn't stop the daemon.** It keeps classifying, so session state stays right for when it's restarted, logs each sentence it couldn't speak, and `status` reports `speech: unavailable`.

## Consequences

- The shim must stamp `fired_at` as its first step and, after starting a daemon, retry sending its event until the socket answers, at least every 50 ms. #9's shim builds on this.
- An event that arrives more than 100 ms late, behind a later-fired event already classified, is still classified out of order. That needs one hook process to stall for longer than a whole turn's gap to the next hook.
- Wall-clock stamps from the shim are compared with the daemon's clock. Both run on the same Mac, but a clock change between a hook firing and its sentence playing could drop or keep a sentence wrongly at the 30 s line.
- `status` is read-only. `fire` and `mute` (#13) will add requests or state of their own.
