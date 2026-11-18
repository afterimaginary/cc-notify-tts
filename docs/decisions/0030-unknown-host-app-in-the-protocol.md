---
description: Whenever the hook shim looks for the session's host app it sends `host_app`, as `null` when it finds none, and the daemon then clears the host on record, so a detached tmux session counts as having an unknown host.
status: accepted
---

# ADR-0030: Saying the host app is unknown

## Context

The hook shim names the session's host app at `SessionStart` and, inside tmux, again on every hook that can announce something, because tmux clients attach, detach and switch sessions mid-session (ADR-0006). The daemon keeps the latest one in the session's state, and the gate reads it to decide whether the user is watching.

The `event` request could only set a host, not clear one: when the shim found no app it left `host_app` out, and the daemon kept the host it had. A tmux session attached from Terminal and then detached kept Terminal as its host. With Terminal in front for another reason, the gate decided the user was watching a session nobody could see, and stayed silent. ADR-0006 says a detached session's host is unknown, and an unknown host makes the gate speak.

## Decision

- **When the shim looks for the host app, it always sends `host_app`:** the bundle id it found, or `null` when it found none. A hook that doesn't look leaves the field out.
- **The daemon treats `null` as "unknown now"** and clears the host on record. A missing field leaves it unchanged, and a non-empty string replaces it, as before.

The protocol is internal, and the shim ships with the daemon, so no older shim has to be understood. The wire format is in the [daemon protocol spec](../specs/daemon-protocol.md).

## Consequences

- A detached tmux session's announcements are spoken under `away`, as ADR-0006 intends. When a client attaches again, the next announcing hook names its app.
- A session whose host can't be named at `SessionStart`, such as one resumed over SSH, also drops any host left from before, which is the same rule applied at the start.
- With several clients attached, the shim still takes the first one listed; which of them is in front isn't detected.
