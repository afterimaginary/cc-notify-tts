---
description: The daemon reads idle time from CoreGraphics through ctypes and follows the frontmost app with a long-running `lsappinfo listen`; the hook shim names the host app from its process ancestry (or the tmux client's), and any presence signal that can't be read counts as away, so the daemon speaks.
status: proposed
---

# ADR-0006: Reading presence from a hook

## Context

With the default `away` setting, the daemon speaks when either of two things is true (see the brief):

- **Idle:** no keyboard or mouse input for N seconds.
- **Out of front:** the app hosting the session hasn't been the frontmost app for M seconds.

Being in front never silences anything by itself, and if presence can't be read, the daemon speaks.

That needs three readings, all without asking the user for a macOS permission: system idle time, which app hosts each session (from what a hook can see), and how long that app has been out of front. Each must be cheap, because idle time is read on every event and the frontmost app has to be followed the whole time the daemon is alive. ADR-0001 found that the host app's environment variables are missing or misleading in some cases (tmux, desktop-app background sessions), so the environment alone may not be enough.

## Method

`spikes/presence/presence_probe.py` measures all of it. Its README has the details and the commands to reproduce it.

- **Idle time:** 200 reads each of `ioreg -c IOHIDSystem` (a subprocess, parsing `HIDIdleTime`) and `CGEventSourceSecondsSinceLastEventType` through ctypes, timing wall and CPU time per read, plus paired reads to compare the values.
- **Frontmost app:** 200 reads each of `lsappinfo front` plus `lsappinfo info -only bundleid` (two subprocesses) and `NSWorkspace.frontmostApplication` through PyObjC, plus the cost of importing AppKit.
- **Host app:** in a shell inside each host, the probe reads `__CFBundleIdentifier` and `TERM_PROGRAM`, and walks its ancestor processes (through `libproc` and ctypes) to the outermost `.app` bundle. Inside tmux, it also asks tmux for the attached client's pid and walks that client's ancestors. A hook inherits Claude Code's environment and Claude Code inherits the shell's (ADR-0001), so the shell sees what a hook would. For hosts where the spike couldn't start its own window, the ancestry walk ran read-only against a shell or Claude Code process already open there.
- **Following the frontmost app:** for 300 s, one process ran a run loop, received `NSWorkspace` activation notifications, and polled both `lsappinfo` and `NSWorkspace`'s property every second. A second process polled the property without ever running a run loop. Then a notification-only watcher ran for 600 s as a detached process (new session, stdio to `/dev/null`, adopted by launchd, as the hook shim starts the daemon per ADR-0002) to measure its cost at rest. Last, `lsappinfo listen +becameFrontmost` ran three times for 150–240 s through a pipe (the `listen` subcommand), with a reader timestamping each line on arrival. All three only watched the app switches made during normal use.

Machine: Apple M3 Max, macOS 27.2.1, Python 3.13, PyObjC 13.0. Other work was running, as it would in normal use. None of the probes asked for, or were granted, Accessibility, Input Monitoring, Screen Recording or Full Disk Access, and no permission prompt appeared.

## Results

### Idle time

| Reader | p50 | p95 | CPU per read | Permission |
| --- | --- | --- | --- | --- |
| `ioreg -c IOHIDSystem -w 0` | 31 ms | 34 ms | 31 ms | None |
| `CGEventSourceSecondsSinceLastEventType` (HID system state, any input), ctypes | 1 µs | 1 µs | 1 µs | None |

Both read the same counter. CoreGraphics read about 0.27 s more than `ioreg` taken just before it, consistently, which doesn't matter against thresholds of tens of seconds. `ioreg` prints about 400 KB of the HID registry on every call, and almost all of its cost is `ioreg` itself, not parsing its output. Limiting the depth with `-d 1` drops `HIDIdleTime` from the output entirely, so it can't be made cheaper that way.

### Host app

| Host | `__CFBundleIdentifier` | `TERM_PROGRAM` | Ancestry walk | How measured |
| --- | --- | --- | --- | --- |
| Claude desktop app, ordinary session | `com.anthropic.claudefordesktop` | absent | `Claude.app` (0.4–0.8 ms) | Read-only, against running desktop-app sessions |
| Claude desktop app, background session | absent | absent | none: the session's tree ends at launchd | The spike ran in one |
| Ghostty | `com.mitchellh.ghostty` | `ghostty` | `Ghostty.app` (0.3 ms) | Own window for the environment; read-only against an existing shell for the ancestry |
| Terminal | not measured; Terminal sets `com.apple.Terminal` | not measured; documented as `Apple_Terminal` | `Terminal.app` (0.3 ms) | Read-only against an existing shell |
| VS Code | `com.microsoft.VSCode` | `vscode` | `Visual Studio Code.app` (1.1 ms) | Own isolated instance (separate profile), probe run as a task |
| tmux, server started from the same app | the app's (inherited by the server) | `tmux` | none: the pane's tree ends at the tmux server, then launchd | Inside VS Code |
| tmux, server started elsewhere, attached from VS Code | absent | `tmux` | none | Inside VS Code |
| tmux client lookup (`tmux display-message -p '#{client_pid}'`, then the client's ancestry) | | | the attaching app, in both tmux cases (8–10 ms) | Inside VS Code |
| iTerm2 | not measured | not measured | not measured | Not installed on this Mac |

Findings:

- **The ancestry walk is the most reliable source.** It names the app that actually owns the terminal, takes about a millisecond, and needs no environment. It must use `PROC_PIDT_SHORTBSDINFO` to read each parent pid: the fuller `PROC_PIDTBSDINFO` is refused for the root-owned `login` process that sits between Terminal or Ghostty and the shell, and the walk then stops short. It walks to the outermost bundle, so `Code Helper.app` inside `Visual Studio Code.app` resolves to VS Code, and the `claude.app` bundle that the desktop app runs Claude Code from resolves to `Claude.app`.
- **The environment is a fallback, not the answer.** `__CFBundleIdentifier` is inherited, so inside tmux it names the app that started the tmux server, which may not be the app showing it now. `TERM_PROGRAM` is `tmux` inside tmux, and VS Code forks also set `vscode`. A terminal launched from a shell inherits that shell's variables, so the spike's own Ghostty window, opened from a desktop-app session, also carried `CLAUDE_CODE_DESKTOP_APP_VERSION`. ADR-0001 saw the same leak.
- **tmux needs the client lookup.** Neither the environment nor the ancestry names the right app when the server was started elsewhere. Asking tmux for the attached client's pid and walking that client's ancestors does, for 8–10 ms. A detached tmux session has no client, so its host can't be named, and nobody is looking at it.
- **Desktop-app background sessions have only `CLAUDE_CODE_DESKTOP_APP_VERSION`.** Their tree ends at launchd, and nothing else names an app. Because that variable leaks into anything started from a desktop-app session, it can only be used once everything else has failed.

Not identified: desktop-app background sessions (except through that last fallback), detached tmux sessions, sessions over SSH (the tree ends at `sshd`) and headless `claude -p` runs from scripts or launchd jobs. iTerm2 is untested. Its documentation and issue tracker say its sessions run under a long-lived `iTermServer` process that it copies to `~/Library/Application Support/iTerm2/`, outside the app bundle, so that sessions survive a crash or upgrade. If so, the ancestry walk finds no bundle, and iTerm2 is named by the `__CFBundleIdentifier` and `TERM_PROGRAM` (`iTerm.app`) its server inherits. That needs confirming (steps below).

### Frontmost app

| Reader | p50 | p95 | CPU per read | Notes |
| --- | --- | --- | --- | --- |
| `lsappinfo front` + `lsappinfo info -only bundleid` | 11–13 ms | 13–15 ms | 10–11 ms | Two subprocesses |
| `NSWorkspace.frontmostApplication` (PyObjC) | 1.5 µs | 2 µs | 5 µs | Correct only in a process that runs a run loop; see below |

Importing AppKit through PyObjC took 1.07 s cold and 158 ms warm, and added 91–97 MB of resident memory.

### Following the frontmost app over time

The 300 s run saw six app switches.

- **`NSWorkspace` notifications** arrived for every switch, at the moment it happened.
- **Polling `lsappinfo` every second** saw every switch, 0.1–1.0 s after its notification, and cost 22 ms of CPU per poll with the property read alongside it (about 2% of one core at a 1 s interval).
- **`NSWorkspace.frontmostApplication` with a run loop** matched `lsappinfo` on all 292 polls.
- **`NSWorkspace.frontmostApplication` without a run loop goes stale.** The second process kept returning the app that was in front when it started: 113 of its 292 reads, every read after the first switch, were wrong. The property is only updated by notifications, which only arrive while a run loop runs.

Two event-driven trackers, at rest:

| Tracker | Switches seen | CPU while watching | Resident memory | Notes |
| --- | --- | --- | --- | --- |
| PyObjC notifications, detached process adopted by launchd, 600 s | 7 of 7 | 20 ms in total | 115 MB (92 MB of it AppKit) | 163 ms to set up with AppKit already in the file cache |
| `lsappinfo listen +becameFrontmost`, read through a pipe; three runs of 150–240 s | 5 of 5 in the run that overlapped the PyObjC watcher (the same switches), 10 switches over all three runs | 5–10 ms in total per run | 2–3 MB | Each line arrived 1–3 ms after the switch: `lsappinfo` flushes every notification, so a pipe doesn't hold it back |

`lsappinfo listen` prints one line per switch with the new app's bundle id (`"CFBundleIdentifier"="com.apple.mail"`), and runs until its `wait -duration` ends. Both trackers worked in a process with no window, no `NSApplication` and no permission. The notification watcher did so after being detached the way the daemon will be (ADR-0002), which shows a detached daemon receives workspace notifications.

## Decision

1. **Idle time:** the daemon reads it with `CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)` through ctypes, on every event. It costs about a microsecond and needs no permission or dependency. Not `ioreg`, which costs 31 ms of CPU per read.
2. **Host app:** the hook shim names it at `SessionStart` and stores it in the session's state, trying in order:
   1. inside tmux (`TMUX` set): the attached client's ancestry;
   2. the outermost `.app` among its own ancestors (through `libproc` with ctypes, which keeps the shim on the standard library);
   3. `__CFBundleIdentifier`;
   4. `TERM_PROGRAM`, mapped to a bundle id;
   5. `CLAUDE_CODE_DESKTOP_APP_VERSION`, meaning the Claude desktop app.

   Inside tmux it names it again on each `UserPromptSubmit`, because a client from another app can attach mid-session. Hooks are async (ADR-0001), so the lookup's 8–10 ms doesn't delay Claude Code.
3. **Out of front:** the daemon runs `lsappinfo listen +becameFrontmost wait -duration <seconds>` as a child process for as long as it lives. It keeps the bundle id now in front, and for each app, when it last lost front. A switch between two other apps doesn't reset the host's clock, and sessions in different apps each have their own. A session's host is out of front for M seconds when it isn't the app in front and lost front at least M seconds ago. If the daemon hasn't seen it in front since starting, that time counts from the daemon's start. Whenever the daemon starts the child, at its own start and again whenever the child exits, it first reads the frontmost app with `lsappinfo front` and `lsappinfo info -only bundleid <asn>`. Then a switch made while no listener was running can't leave the app in front stale. Nothing is read at event time. It costs nothing between switches, needs no dependency and no run loop, and arrives within milliseconds. PyObjC's notifications work as well, but would add about 100 MB and a run loop to the daemon for the same answer. Polling `lsappinfo` costs about 1% of a core at one-second resolution.
4. **When presence can't be read, the daemon speaks.** Each signal that can't be read counts as a reason to speak: idle time when CoreGraphics returns nothing, and out of front when the session's host app is unknown or the frontmost app can't be followed. `status` names the signal it couldn't read. In practice this means sessions over SSH, detached tmux sessions and headless runs are spoken whenever the setting is `away`.
5. **No permission is needed,** and the daemon must never ask for one. None of these readings needs Accessibility, Input Monitoring, Screen Recording or Full Disk Access.

## Consequences

- Idle time counts any keyboard or mouse input on the Mac, including input from remote-control software and "mouse jiggler" tools, which make the user look present.
- `lsappinfo` has a man page, but its output format isn't a documented interface. The daemon must treat output it can't parse, or a listener that keeps exiting, as "can't follow the frontmost app", which makes it speak, and `doctor` should check it. If a macOS release breaks it, PyObjC's notifications are the replacement.
- The host app is fixed per session except inside tmux. Moving a session between apps any other way isn't followed, and nothing in normal use does that.
- iTerm2 and the environment Terminal sets are untested. The owner steps below confirm them; if either fails the ancestry walk, it is listed as not identified.

## Owner steps for what's untested

In Terminal, and in iTerm2 if you use it, open a new window and run:

```sh
uv run --script <repo>/spikes/presence/presence_probe.py host --label Terminal
```

Paste the `env` and `identified` blocks on the issue. In Terminal, `by_ancestry` should name the app. In iTerm2, `by_ancestry` may be empty, and `by_cfbundleidentifier` should then name it.
