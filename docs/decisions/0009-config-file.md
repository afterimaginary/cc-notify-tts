---
description: One TOML file, read again on every event with no cache (it takes about 0.09 ms), where a bad key falls back to its own default and a file that can't be parsed falls back to all defaults; "away" defaults to 20 s without input or 10 s with the session's app out of front.
status: accepted
---

# ADR-0009: The config file, its fallbacks and the away thresholds

## Context

Every part of the notifier reads one TOML file under `~/Library/Application Support/cc-notify-tts/`, with defaults in code, and the brief says it's re-read on every event so an edit applies without a restart. Three things needed deciding:

- whether reading the file on every event is cheap enough, or needs a cache;
- what a bad file does, given that a notifier that crashes on a typo is worse than one that ignores it;
- the default thresholds for "away". ADR-0006 settled how idle time and the frontmost app are read, but not how long either has to last.

## Measurement

`spikes/config_load/measure.py` times `config.load` 5000 times per case, after one warm-up load (the file is in the page cache for every load after the first, as it is in use), and the module's import in 20 fresh processes. Apple M3 Max, macOS 27.2.1, Python 3.13, load average about 4. Two runs agreed within 2 µs at p50.

| Case | p50 | p95 | Max |
| - | - | - | - |
| No file | 4 µs | 5 µs | 12 µs |
| The spec's example file (every key set) | 88 µs | 98 µs | 196 µs |
| A file with three problems | 35 µs | 40 µs | 89 µs |
| Importing `cc_notify_tts.config` in a fresh process | 19.6 ms | 20.1 ms | 20.1 ms |

Of the import, about 12 ms is the standard library modules it uses (`logging`, `tomllib`, `dataclasses`, `enum`, `pathlib`, `typing`, `datetime`), which the daemon has loaded long before any event. The file has fewer keys than the example when only a few are changed, so a real load is at most the example's 0.1 ms.

## Decision

1. **Read the file on every event, with no cache.** 0.1 ms is nothing against the 3 s from a turn's end to speech, and checking a modification time to skip the parse would cost a `stat` and add a way for an edit to be missed.
2. **A bad key falls back to its own default.** An unknown key is ignored, and an invalid value (wrong type, out of range, not one of the choices) is replaced by that key's default while the rest of the file applies. A misspelt key costs only that setting.
3. **A file that can't be read or parsed means all defaults.** A TOML syntax error, bytes that aren't UTF-8 or a permission error leave no trustworthy values, so nothing from the file is used. A missing file is simply all defaults, with no problem reported.
4. **Problems are returned with the config and logged when they change.** `load` returns the list for `doctor` to show, and logs it only when it differs from the last list logged, so a bad file doesn't add a line to the log for every event.
5. **Quiet hours are off unless both ends are set,** with no separate switch. One end alone, or both the same, is reported and leaves them off rather than guessing a window.
6. **`away` defaults to 20 s without input, or 10 s with the session's app out of front.** The two signals catch different situations, so they get different thresholds:
   - *Out of front* is the deliberate one: switching to a browser or another app while a turn runs is the main case the notifier exists for, and the brief's acceptance test wants that turn spoken within 3 s of its end. Ten seconds is long enough that a quick look at another window and back (checking a message, copying a path) doesn't count, and short enough that a user who switched away shortly after starting a turn is away by the time it ends.
   - *Idle* catches the user who stays in the session's app but stops working: reading something long elsewhere on screen, or stepping away from the desk with the terminal still in front. It has to be longer, because someone watching a turn stream in front of them makes no input for stretches while reading; 20 s leaves room for reading a reply without being talked over, and still speaks soon after the user leaves.

   Both are settings, so someone who wants it quieter or chattier changes a number. The values will be checked in daily use once the gate exists.

## Consequences

- An edit takes effect on the next event, including a broken one: a syntax error silently returns everything to its default until it's fixed, and `doctor` and the log say why.
- Importing the config module costs about 20 ms in a fresh process, mostly the standard library. That doesn't matter to the long-lived daemon. A hook shim that read the config itself would pay it on every hook, so the shim leaves the config to the daemon unless a hook needs a setting.
- With the default thresholds, a user watching a turn in front of them who makes no input for 20 s hears it. Being in front never silences anything by itself (ADR-0006), so that's intended, and `always` or `never` per event covers anyone who wants otherwise.
