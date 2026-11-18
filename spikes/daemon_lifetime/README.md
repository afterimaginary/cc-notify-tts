# Spike: on-demand daemon lifetime and scheduling priority

Answers two questions behind ADR-0002. Does a process started from a Claude Code hook outlive the hook, the session and the window it ran in? And does it get scheduled like a LaunchAgent with `ProcessType` Interactive, or like a throttled one?

| File | What it does |
| --- | --- |
| `probe.py` | Stand-in daemon. It times a fixed, single-threaded CPU-bound loop several times and appends one JSON line per round. Each line records the wall time, the share of CPU time spent on performance cores, CPU time by QoS class (from `proc_pid_rusage`), `nice`, and the parent, process group and session. |
| `hook.py` | The hook command. It starts the probe detached the way the real hook shim will start the daemon, then exits: new session, stdio to `/dev/null`. |
| `run_spike.py` | Runs every scenario side by side, so they share the same machine load, then writes `summary.md`. |

## Run it

```sh
uv run python spikes/daemon_lifetime/run_spike.py /tmp/cc-notify-tts-spike
```

The output directory must be new or empty, because probes append to their result files.

It needs Terminal.app, a logged-in `claude`, and Accessibility permission for the app running the command, which it uses to answer Terminal's "terminate running processes?" sheet. `claude -p` sends one short prompt. It opens four Terminal windows and closes them again.

The three LaunchAgents are loaded with `launchctl bootstrap` from plists in the output directory. Every one is booted out and its plist deleted before the script exits, even on failure. To check that nothing is left:

```sh
launchctl list | grep afterimaginary.cc-notify-tts.spike   # prints nothing
```

## Scenarios

| Scenario | How the probe starts | How its session ends |
| --- | --- | --- |
| `shell` | Directly, in a Terminal window (baseline) | It finishes |
| `hook-headless` | SessionStart hook in `claude -p` | `claude -p` answers and exits |
| `hook-terminal-close` | SessionStart hook in an interactive `claude` in Terminal | The window is closed mid-session (Terminate) |
| `hook-app-close-sim` | As above | SIGKILL to claude's process group and every descendant still found by parent pid. This simulates an app closing its window; the desktop app itself is not driven |
| `agent-default` | LaunchAgent, `ProcessType` unset | It finishes |
| `agent-interactive` | LaunchAgent, `ProcessType` Interactive | It finishes |
| `agent-background` | LaunchAgent, `ProcessType` Background | It finishes |

## Checking the desktop app by hand

The script can't close a Claude desktop app window, so that case is simulated. To confirm it for real:

1. From the repository root, create a scratch project whose settings run the spike hook:

   ```sh
   mkdir -p /tmp/cc-spike/project/.claude
   printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"python3 %s /tmp/cc-spike/desktop.jsonl desktop 60 2"}]}]}}\n' \
     "$PWD/spikes/daemon_lifetime/hook.py" > /tmp/cc-spike/project/.claude/settings.local.json
   ```

2. In the Claude desktop app, start a Claude Code session in `/tmp/cc-spike/project`. Within a few seconds, `/tmp/cc-spike/desktop.jsonl` starts to fill.
3. Close the session's window, or quit the app.
4. Wait 20 seconds, then run:

   ```sh
   tail -n 2 /tmp/cc-spike/desktop.jsonl
   ```

   The process survived if new lines kept arriving after the window closed (compare `wall`, in Unix seconds, with `date +%s`) and `ppid` is `1`. Its scheduling matches the other hook scenarios if `elapsed_ms` and `p_core_share` match them.
5. Clean up with `rm -rf /tmp/cc-spike`. The probe exits by itself after 60 rounds.
