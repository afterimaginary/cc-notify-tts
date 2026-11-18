---
description: Ask Ollama at OLLAMA_HOST when it names an address on this machine (localhost, a loopback address, or 0.0.0.0 and :: meaning this machine), and at 127.0.0.1:11434 otherwise, logging the ignored value and showing it in doctor.
status: accepted
---

# ADR-0029: Use OLLAMA_HOST only when it points at this machine

## Context

The daemon, `doctor` and the eval asked Ollama at `127.0.0.1:11434`, Ollama's default. Someone who runs Ollama on another port sets `OLLAMA_HOST` (for the server and for the `ollama` CLI), and got template wording with no clue why.

`OLLAMA_HOST` can also point at another machine, such as a GPU box on the network or a hosted service. The project's promise is that wording stays on this machine: notification text is drawn from session transcripts, and the latency budget (ADR-0019) assumes a local model. So a remote address must not be used, even when it's set on purpose.

## Decision

- **Read `OLLAMA_HOST` the way Ollama's CLI does.** `host`, `host:port`, `:port` or an `http`/`https` URL, with surrounding quotes and whitespace removed and any path dropped. The port is 11434 unless a scheme is given without a port, when it's 80 or 443, as in Ollama.
- **Use it only when it names this machine.** `localhost`, an IPv4 or IPv6 loopback address, or the listen-everywhere addresses `0.0.0.0` and `::`, which a server is often told to bind and which mean this machine to a client; those are asked at `127.0.0.1` on the same port. Any other hostname is treated as another machine without resolving it, since a name can resolve differently later. An unparseable value or a port out of range is treated the same way.
- **Otherwise use the default and say so.** The daemon logs a warning naming the ignored value once, when it starts. `doctor` shows the address in use in every Ollama line, and when `OLLAMA_HOST` was ignored it warns, even if the default address answers, so someone who meant a remote server learns why it isn't used.
- **The eval's `--endpoint` is unchanged.** It's a developer tool, so an explicit address is used as given; without one it follows the rule above.

## Consequences

- The daemon reads the environment it was started with, which comes from the Claude Code session whose hook started it. `doctor` reads the shell it's run from, so the two can differ if `OLLAMA_HOST` is set in one and not the other.
- Remote and hosted models stay out of scope: there's no setting that sends wording off the machine.
