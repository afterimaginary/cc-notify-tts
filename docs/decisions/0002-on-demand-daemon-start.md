---
description: The hook shim starts the daemon on demand as a detached process; no LaunchAgent, because a hook-started process outlives its session and is scheduled like an Interactive LaunchAgent.
status: accepted
---

# ADR-0002: Start the daemon on demand from a hook

## Context

The daemon keeps the speech model loaded and does the expensive work for every event. It can be started in one of two ways:

- **On demand.** When the hook shim can't reach the daemon's socket, it starts the daemon as a detached process. The daemon exits after an idle timeout, so nothing runs while Claude Code isn't in use and installing the plugin is the whole install.
- **A LaunchAgent.** launchd starts the daemon and keeps it running. That needs an install step outside the plugin, and the daemon runs even when Claude Code isn't open.

On-demand start is the simpler product, but only if two things hold:

1. **Lifetime.** A process started from a hook must outlive the hook, the Claude Code session, and the window the session ran in. Otherwise the daemon dies mid-sentence whenever a session ends.
2. **Priority.** It must be scheduled like a foreground process. macOS gives background-QoS work to the efficiency cores only, and speech synthesis there is several times slower. A LaunchAgent with `ProcessType` Background lands in exactly that trap.

The spike in `spikes/daemon_lifetime/` measures both. Its README has the method and the commands to reproduce it.

## Method

A probe stands in for the daemon. It runs a fixed, single-threaded, CPU-bound loop 30 times, 2 s apart. For each round it records the wall time and the share of CPU time spent on performance cores. It also records CPU time by QoS class (both from `proc_pid_rusage`), `nice`, and its parent.

A SessionStart hook starts the probe detached, the way the real shim will start the daemon. The process gets a new session (`start_new_session`), stdio goes to `/dev/null`, and the hook exits at once.

All seven scenarios ran at the same time, so they saw the same machine load: three hook scenarios, a baseline from a Terminal window, and three LaunchAgents. The machine was an Apple M3 Max (12 performance and 4 efficiency cores) on macOS 27.2, with Claude Code 2.1.327 and Python 3.13. Other work was running (load average about 6), as it would be in normal use.

## Results

### Lifetime

| Session ended by | Probe rounds after the session ended | Probe's parent afterwards |
| --- | --- | --- |
| `claude -p` answering and exiting | 28 of 28 remaining | launchd (pid 1) |
| Closing the Terminal window mid-session (Terminate) | 21 of 21 remaining | launchd |
| SIGKILL to claude's process group and every descendant found by parent pid (simulated desktop-app close) | 21 of 21 remaining | launchd |

The probe ran its full 30 rounds every time. claude itself was confirmed dead after each ending. Starting the probe took 2–6 ms inside the hook.

Why it works: the hook exits immediately, so launchd adopts the probe before anything walks the process tree. The new session means no controlling terminal, so closing the window sends it no SIGHUP. Its own process group means a group kill doesn't reach it. And with stdio detached, Claude Code isn't left waiting on its pipes.

**Simulated, not measured:** closing a window of the Claude desktop app wasn't driven, because that would mean scripting the owner's running app. The simulation is the harshest plausible close: SIGKILL to the session's whole process tree. The spike README has the steps to confirm it by hand in a desktop session.

### Scheduling

Wall time is per round of the same workload. The P-core share is the median fraction of CPU time on performance cores.

| Started by | Median ms | p90 ms | P-core share | CPU time by QoS | nice |
| --- | --- | --- | --- | --- | --- |
| Terminal window (baseline) | 232 | 243 | 0.84 | legacy 100% | 0 |
| Hook, `claude -p` | 254 | 266 | 0.87 | legacy 100% | 0 |
| Hook, interactive session, window closed | 244 | 256 | 0.90 | legacy 100% | 0 |
| Hook, interactive session, tree killed | 232 | 253 | 0.99 | legacy 100% | 0 |
| LaunchAgent, `ProcessType` Interactive | 250 | 259 | 0.84 | legacy 100% | 0 |
| LaunchAgent, `ProcessType` unset | 246 | 363 | 0.85 | utility 100% | 0 |
| LaunchAgent, `ProcessType` Background | 900 | 1063 | 0.00 | background 100% | 0 |

`legacy` is the class the kernel bills to threads that never set a QoS, which is the normal case for a process started from a shell. It is scheduled at the default, foreground level.

The hook-started probe is scheduled the same as the Interactive LaunchAgent. It runs at the same speed, mostly on performance cores and at the same QoS, and keeps that priority after its session ends. Its spread across the hook scenarios (232–254 ms) is within the run-to-run noise of the baselines.

The unset `ProcessType` runs at utility QoS. It had the same median but a heavier tail (p90 363 ms) under load. Background runs only on efficiency cores and is 3.6× slower. Two other runs showed the same pattern. An earlier partial run had Background at 955 ms with a P-core share of 0.0. A full rerun after the review fixes had the hook scenarios at 234–250 ms, Interactive at 240 ms, unset at 237 ms (p90 296 ms) and Background at 946 ms (p90 3855 ms). In every run, all three ways of ending the session left the probe running.

## Decision

**Start the daemon on demand from the hook shim, detached, and exit it after an idle timeout. Don't install a LaunchAgent.**

The LaunchAgent fallback isn't needed: on-demand start meets both conditions. If it is ever needed, the agent must set `ProcessType` to Interactive. Unset gives utility QoS with a slower tail, and Background is 3.6× slower.

The shim must start the daemon the way the spike did:

- in a new session (`start_new_session=True`)
- with stdin, stdout and stderr redirected away from Claude Code's pipes
- by returning straight away, without waiting on the child.

## Consequences

- Nothing runs once no session has used the daemon for the idle timeout, and installing the plugin is the whole install.
- **The daemon inherits its priority from the session that starts it.** Started from a normal Terminal session, that's foreground priority, as measured above. A desktop-app session is expected to behave the same but wasn't measured; the manual check in the spike README covers it. A session that is itself niced or demoted, such as one started by a background job runner, passes that on: the spike's own control session ran at nice 5. So the daemon should report its synthesis time, which makes a demoted daemon visible.
- Two sessions starting at the same moment can both try to start a daemon. The daemon must hold its socket exclusively and the loser must exit quietly. That belongs to the daemon's own design, not this decision.
- The desktop-app window close is simulated until the manual check in the spike README has been run.
