# When to speak

How the daemon decides, for each notification, whether to speak or stay silent, and the reason it gives. How presence is read is decided in ADR-0006; the settings and their defaults are in the [config spec](../specs/config.md) (ADR-0009).

Code: `src/cc_notify_tts/gate.py` (the decision) and `src/cc_notify_tts/presence.py` (the readers). Both use only the standard library.

## Interface

- `decide(kind, config, mute, local_time, presence) -> Decision` is pure: everything it reads is passed in, so `cc-notify-tts status` explains a decision the same way the daemon makes it. `Decision` has `speak` and a one-line `reason` starting `speaking:` or `silent:`.
- `mute` is anything with `is_muted() -> bool`. The daemon passes `MuteFile` (`mute.py`), which reads the mute file that `cc-notify-tts mute` writes in the state folder, on every call ([CLI](../specs/cli.md#mute-and-unmute)).
- `read_presence(host_app, idle_seconds, tracker, now) -> Readings` combines an idle reading with the frontmost tracker's state for one session's host app. `now` is `time.monotonic()`.
- `read_idle_seconds()` returns seconds since the last keyboard or mouse input, or `None`.
- `FrontTracker` holds which app is in front and when each app last lost front. `FrontmostWatcher(tracker).start()` keeps it current from a background thread for as long as the daemon runs, and `stop()` ends the listener.

The host app is a bundle id named by the hook shim (ADR-0006 lists how). `None` or an empty string means it couldn't be named.

## The rules, in order

The first rule that applies decides.

| Rule | Decision | Example reason |
| - | - | - |
| Muted | silent | `silent: muted` |
| Inside quiet hours (`start` inclusive, `end` exclusive, crossing midnight when `end` is earlier) | silent | `silent: quiet hours, 22:00 to 08:00` |
| The event is set to `never` | silent | `silent: finished is set to never` |
| The event is set to `always` | speak | `speaking: finished is set to always` |
| `away`: idle time can't be read | speak | `speaking: can't read keyboard and mouse idle time` |
| `away`: no input for at least `idle_seconds` | speak | `speaking: no keyboard or mouse input for 45 s` |
| `away`: the session's host app is unknown | speak | `speaking: can't tell which app the session runs in` |
| `away`: the frontmost app can't be followed | speak | `speaking: can't follow the frontmost app` |
| `away`: the host app out of front for at least `away_seconds` | speak | `speaking: Terminal has been out of front for 30 s` |
| Otherwise | silent | `silent: you were active 3 s ago and Terminal is in front` |

Being in front never silences by itself: it only means the second reason to speak is missing. A threshold of 0 always counts as away, and the reason says so (`speaking: away_seconds is 0, so the app always counts as out of front`). Seconds in reasons are rounded down to whole seconds, so a reading just under a threshold never prints as the threshold. Hosts ADR-0006 identified (Terminal, Ghostty, iTerm2, VS Code, the Claude app) and the apps people most often switch to (common browsers such as Chrome, Safari, Arc and Firefox, editors such as Cursor, Zed and Xcode, and Finder, Mail, Messages and Slack) are named; any other app by its bundle id.

## Following the frontmost app

- `FrontmostWatcher` runs `lsappinfo listen +becameFrontmost wait -duration 3600` as a child process and reads its output line by line. A line containing `kLSNotifyBecameFrontmost` is a switch; its `"CFBundleIdentifier"` is the new front app (an app with no bundle id counts as an unnamed app, which is never a host).
- Before each listener starts, it reads the app in front once with `lsappinfo front` and `lsappinfo info -only bundleid`. After an hour the listener exits and the watcher starts the next one.
- On a switch from A to B, A lost front at that moment. A host's out-of-front time is 0 while it's in front, otherwise the time since it last lost front, or since tracking started if it hasn't been in front since. Switches between other apps don't touch a host's clock.
- The tracker is *following* only while a listener runs and the app in front is known. While it isn't, every `away` decision for that rule speaks.
- A listener that exits within 5 s of starting has failed. After each failure in a row the watcher waits 1, 2, 4… s, up to 60 s, before trying again; a listener that runs at least 5 s resets the count. `lsappinfo` that can't be started counts as a failure.
- Times are `time.monotonic()`, so a clock change doesn't move them.
