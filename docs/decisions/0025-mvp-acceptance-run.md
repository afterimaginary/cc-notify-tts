---
description: The brief's "Done means" was run on the build Mac from a clean install of this project; the three install commands, the spoken finished turn and permission request, and both hook-cost targets pass, the silent-while-active gate passes with input posted by the run, and `make eval` and template wording without the model pass in part, with the full eval, Ollama stopped, a fresh account and the other checks that need a person listed as owner steps.
status: accepted
---

# ADR-0025: MVP acceptance run

## Context

The brief's "Done means" lists seven checks, run on a Mac with only Homebrew and following the README. Each part was measured on its own before (ADR-0011, ADR-0014, ADR-0016, ADR-0018, ADR-0019), but never as one install-and-use run in a real Claude Code session. This records that run: what was done, what was spoken (from `daemon.log`), the timings and the `status` output, and what still needs a person.

## Method

Run on 2026-11-18 on the build Mac: Apple M3 Max, 48 GiB, macOS 27.2.1, Claude Code 2.1.327, Ollama 0.40.16 with `granite4:micro` pulled, other Claude Code sessions running.

- **Clean state.** Before the run, the plugin and its marketplace weren't installed, `cc-notify-tts` wasn't a uv tool, and the data and log folders held only earlier files. Both folders were moved aside, so `setup` started from nothing, and were put back afterwards. `~/.local/bin` was already on `PATH`, and uv already had a Python 3.13 of its own.
- **Fresh clone.** `git clone` of the repository at `a3128f5`, used for `make eval`. The install itself comes from GitHub, as the README says.
- **Real sessions.** An interactive `claude` (Haiku 5.5) in a detached tmux pane, driven with `tmux send-keys`, in a throwaway two-file project named `weather-app`. With no terminal window attached, the app hosting the session was named through `__CFBundleIdentifier`, which is how a Terminal window names itself to its children: `com.apple.Terminal` for items 2 and 3, and `com.google.Chrome` (the app in front) for item 4. TMUX was cleared for the session, so the shim used that variable rather than tmux's client.
- **Away, for real.** Items 2 and 3 ran with Chrome in front and the Terminal-hosted session out of front, as if the user had switched to a browser.
- **Active input.** Item 4 needs recent keyboard or mouse input. The run posted a mouse-moved event at the pointer's own position every 2 s (`CGEventPost`, nothing moves on screen), which CoreGraphics counts as input, so the idle reading was 0–2 s while the turn ran.
- **Little sound.** With the plugin installed for the user, every Claude Code session on the Mac reaches the daemon. It was muted between checks and unmuted only while each check ran.
- **What was spoken.** The daemon logged each gate decision but not the sentence. This PR adds a `queued` line with the text (see Failures), and the run's daemon was installed from that commit so the sentences below come from the log.
- **Build tested.** `a3128f5` (main before #33's and #42's fixes merged) plus this PR's logging commit, installed with `uv tool install … git+https://github.com/afterimaginary/cc-notify-tts@issue-24` before the branch was rebased onto them. The plugin's hooks were `a3128f5`'s.

## Results

| # | Check | Result |
| - | - | - |
| 1 | Three documented commands install the plugin and its models | Pass, on a Mac that already had a uv Python and `~/.local/bin` on `PATH` |
| 2 | A multi-step turn, user in a browser, spoken within 3 s with an accurate detail | Pass |
| 3 | A permission prompt while away is spoken with the action | Pass |
| 4 | Silent while active with the default setting, `status` says why; spoken with `always` | Pass with a stand-in: input posted by the run, the session's app named as the one in front; typing is an owner step |
| 5 | With Ollama stopped, template wording is spoken without errors | Partial: run with the model missing; Ollama stopped is an owner step |
| 6 | `make eval` reproduces the README's table within run-to-run noise | Partial: 2 of the 10 models, both matching; the full table is an owner step |
| 7 | No hook over 100 ms p95; no process left after the idle timeout | Pass |

### 1. Install

| Command | Took | Notes |
| - | - | - |
| `brew install uv` | 4.9 s | Upgraded an existing uv 0.11.10 to 0.13.9 |
| `uv tool install --managed-python --python 3.13 git+https://github.com/afterimaginary/cc-notify-tts` | 3.3 s | 21 packages |
| `~/.local/bin/cc-notify-tts setup` | 14.8 s | Linked Python and the environment, downloaded and verified `kokoro-v1.0.int8.onnx` (92 MB) and `voices-v1.0.bin` (28 MB), added the marketplace and installed the plugin at user scope, found `~/.local/bin` on `PATH` |

Then `cc-notify-tts doctor` passed every check:

```
ok    macOS: macOS 27.2.1
ok    Hook Python: ~/Library/Application Support/cc-notify-tts/bin/python
ok    Plugin: installed and enabled, version a3128f5f8b63
ok    Speech model: kokoro-v1.0.int8.onnx and voices-v1.0.bin, verified
ok    Daemon: running, pid 11791, started it to check; speech ready
ok    Idle time: readable
ok    Frontmost app: readable
ok    Config: ~/Library/Application Support/cc-notify-tts/config.toml not found, so every setting is its default
ok    Ollama (optional): running, with granite4:micro

All checks passed.
```

Not exercised, because this Mac already had them: uv downloading its own Python 3.13, and `setup` adding `~/.local/bin` to the shell profile. Both are owner steps on a fresh account.

### 2. A finished turn, user in a browser

Prompt: "Now add a k_to_c function for kelvin, add asserts for it to test_convert.py, run the tests again and commit everything with a short message." Claude edited two files, ran the tests and committed `6ba444c`. Its reply: "Added k_to_c (k - 273.15) and its asserts, and the tests pass. Committed as 6ba444c."

```
weather app, finished: speaking: Terminal has been out of front for 18 s
weather app, finished: queued 'Finished up in weather app. Added k_to_c conversion and updated tests, committing as 6ba444c.'
```

The detail is accurate: the function, the tests and the commit hash all match. Stages, ms after the `Stop` hook fired:

| sent | received | dequeued | gated | worded | synthesized | playing |
| - | - | - | - | - | - | - |
| 10.9 | 11.2 | 111.6 | 112.4 | 451.2 | 1025.0 | 1025.2 |

First sound came about 1.1 s after the hook: `playing` plus `afplay`'s 0.1 s start-up (ADR-0019). That's under the 3 s target. An earlier turn in the same session, before the sentence was logged, was spoken at 0.90 s to `playing`. The time between the turn ending on screen and Claude Code running the `Stop` hook isn't measured.

### 3. A permission prompt while away

Prompt: "Delete the __pycache__ folder with rm -rf __pycache__." in a session started with `--permission-mode default`. Claude asked to run `rm -rf __pycache__ && ls -la __pycache__ 2>&1; git status --short`.

```
weather app, permission: speaking: Terminal has been out of front for 76 s
weather app, permission: queued 'Weather app needs your okay to remove pycache folder and verify removal.'
```

The sentence names the action. 701 ms from the hook to `playing`; the model's wording took 224 ms of it.

### 4. Silent while active, then `always`

With no config file (every setting at its default), input active and the session's app in front, a turn that asked permission for an edit and then finished was silent twice:

```
weather app, permission: silent: you were active 2 s ago and com.google.Chrome is in front
weather app, finished: silent: you were active 0 s ago and com.google.Chrome is in front
```

`cc-notify-tts status` during the turn:

```
Daemon    running, pid 27118, version 0.0.0, speech ready
Mute      off
Config    ~/Library/Application Support/cc-notify-tts/config.toml (no file, so every setting is its default)
Presence  last input 1 s ago (away after 20 s); com.google.Chrome in front

Would it speak now? Sessions in the Claude app: cc notify tts
  every event      speaking: the Claude app has been out of front for 190 s

Would it speak now? Sessions in com.google.Chrome: weather app
  every event      silent: you were active 1 s ago and com.google.Chrome is in front

Would it speak now? Sessions in an unknown app: cc notify tts
  every event      speaking: can't tell which app the session runs in
```

The other two groups are the other Claude Code sessions running on the Mac. With `[events.finished] speak = "always"` and the same presence, the next turn was spoken:

```
weather app, finished: speaking: finished is set to always
weather app, finished: queued "Finished up in weather app. Documented convert.py's three temperature conversion functions."
```

The decision is right, but the detail isn't: that turn only described `convert.py` ("convert.py provides three functions that convert temperatures between Celsius, Fahrenheit, and Kelvin."). The docstrings had been added by the turn before. See Failures (#63).

### 5. Template wording without the model

Quitting the Ollama app wasn't allowed in this run, so the model was made unavailable a different way: `wording.model` was set to a model that isn't pulled, which sends every sentence down the same template path as a stopped Ollama (ADR-0003).

```
weather app, finished: speaking: finished is set to always
template wording: acceptance-missing-model isn't loaded yet
weather app, finished: queued 'Finished up in weather app. The folder contains convert.py, README.md, and test_convert.py, plus a .git directory.'
```

It was spoken, and no `ERROR` or traceback appeared in `daemon.log` or `shim.log` during the run. The one other line was the expected `couldn't load acceptance-missing-model: model not found` at INFO. The check with Ollama itself stopped is an owner step.

### 6. `make eval`

`make eval MODELS="granite4:micro llama3.2:3b"` from the fresh clone took 20 s, including creating its environment. These are the two models in the README's table that were already on this Mac; pulling the other eight was out of scope.

| Model | Pass | Detail kept | Invented | p50 ms | p95 ms | Resident |
| - | - | - | - | - | - | - |
| `granite4:micro`, README | 42 | 43 | 1 | 176 | 250 | 2.18 GiB |
| `granite4:micro`, this run | 42 | 43 | 1 | 176 | 250 | 2.18 GiB |
| `llama3.2:3b`, README | 40 | 42 | 2 | 166 | 242 | 2.16 GiB |
| `llama3.2:3b`, this run | 40 | 42 | 2 | 168 | 225 | 2.16 GiB |

The counts match exactly, as they should at temperature 0 (ADR-0016). Latency is within the noise: p50 is within 1.2%, and `llama3.2:3b`'s p95 was 7% lower.

### 7. Hook cost and cleanup

The installed plugin's own hook commands were run through `/bin/sh -c`, as Claude Code runs them, against the running daemon, timed from spawn to exit. There were 30 rounds of a prompt, a permission request and a finished turn, 6 s apart, all muted. Every event reached the daemon (30 `permission` and 30 `finished` decisions in the log).

| Hook | p50 ms | p95 ms | max ms |
| - | - | - | - |
| `UserPromptSubmit` (30) | 40.6 | 48.5 | 50.0 |
| `PermissionRequest` (30) | 36.4 | 48.1 | 49.3 |
| `Stop` (30) | 37.9 | 49.0 | 52.2 |
| The hook that finds no daemon and starts one (10) | 85.3 | 93.2 | 93.2 |

The cold-start hook came in under 100 ms here, against ADR-0014's 116 ms p95 over 50 runs. These are 10 samples, so ADR-0014 remains the figure.

**Idle exit.** Other Claude Code sessions on the Mac kept sending events, so the hooks were made no-ops: the hook Python link was renamed, and each hook exits at once when it's missing. `daemon.idle_timeout_minutes` was set to 1. The daemon logged `no event for 60 s, exiting` 65 s after the last event. Then `ps` showed no daemon, no `lsappinfo listen` and no `afplay`, and `ollama ps` listed no model.

**Uninstall.** The README's uninstall commands removed the plugin, the marketplace and the tool. Claude Code left the plugin's cache folder (`~/.claude/plugins/cache/cc-notify-tts`) behind, which is its own behaviour; it was removed by hand.

## Failures

- **The spoken sentence wasn't logged,** so a run couldn't show what was said. Fixed in this PR: the daemon logs `<project>, <event>: queued '<sentence>'` for each sentence it queues (daemon protocol spec, Log).
- **Model wording turned a description into a claimed action.** "Documented convert.py's three temperature conversion functions" was spoken for a turn that only read and described the file. The guards check names and numbers, not verbs (ADR-0015). Filed as #63.
- **Reasons name most apps by bundle id.** `status` and the log say `com.google.Chrome` where they say "Terminal" or "the Claude app" for the apps the code knows. Filed as #64.
- **A phonemizer warning on every sentence.** `WARNING phonemizer: words count mismatch on 100.0% of the lines (1/1)` is logged each time a sentence is synthesized. It's harmless, but it sits in the log `doctor` points to. Filed as #65.
- **Seen, already fixed:** another session's turns, with long-running monitors as background work, were spoken as "still running" after each turn. The install was from `a3128f5`, before #33's fix (ADR-0023) merged, so this is the behaviour that fix removes.

Not failures: `status` and `doctor` report version 0.0.0 until the release sets it (#23). `brew install uv` upgrades an existing uv. A user-scope install speaks for every Claude Code session on the Mac, as designed.

## Owner steps

These need a person, a fresh account or an app the run didn't use.

1. **Fresh macOS user account (item 1).** In System Settings, Users & Groups, add a Standard user and log in to it. Install [Homebrew](https://brew.sh) and Claude Code, and log in to Claude Code. Run the README's three commands. `setup` should say it changed your shell profile to add `~/.local/bin` (`uv tool update-shell`), and uv should download its own Python 3.13 during `uv tool install`. Open a new terminal and run `cc-notify-tts doctor`: every line should be `ok` except, without Ollama, a `warn` for Ollama (optional), and it should end with "0 problems, 1 warning." (or "All checks passed." with Ollama and `granite4:micro`).
2. **Hear it (item 2).** In that account, or your own after the three commands, start `claude` in Terminal in any small project. Give it a task that takes a minute, such as "add a function and a test for it, then run the tests", and switch to a browser. You should hear one sentence within about 3 s of the turn ending, naming the project and what was done.
3. **Typing in the session (item 4).** Run a short task in a Terminal window and keep typing in that window (for example, in another tab) until it finishes. Nothing should be spoken. `cc-notify-tts status` should say `silent: you were active … ago and Terminal is in front`. Add `[events.finished]` with `speak = "always"` to `~/Library/Application Support/cc-notify-tts/config.toml`, repeat, and it should be spoken. Then remove those lines.
4. **Ollama stopped (item 5).** Quit Ollama from its menu bar icon (or `brew services stop ollama` if Homebrew installed it) and check that `curl -s localhost:11434` fails. Run a short task with the session's window in the background. The sentence should be spoken with template wording, `daemon.log` should show `template wording: …` and no `ERROR` lines, and `doctor` should show Ollama as optional and not running. Start Ollama again (`brew services start ollama` for Homebrew).
5. **The full eval (item 6).** From a clone, pull the README table's other eight models (`ollama pull` each name in its first column), then run `make eval MODELS="granite4:micro qwen3.5:4b phi4-mini:3.8b llama3.2:3b qwen3.5:2b gemma3:4b gemma3:1b llama3.2:1b qwen3:1.7b qwen3:4b"`. Each model's Pass, Detail kept and Invented counts should match the README exactly, and its p50 and p95 should be within about 5% (p95 varies more). Remove the models you don't keep with `ollama rm`.
6. **The listening check:** ADR-0012's five pairs by ear (`uv run python spikes/kokoro_listening/listen.py` from a clone).
7. **The desktop app:** ADR-0001's and ADR-0002's owner steps, which cover hook coverage and closing a desktop session.
8. **Terminal's and iTerm2's environment:** ADR-0006's one-line host check, run in each.

## Consequences

- Items 1–3 and 7 pass on this Mac, item 4 passes with a stand-in, and items 5 and 6 pass in part. The MVP is ready for the owner steps above and the launch.
- The run's own method can be repeated: mute between checks, drive a tmux session, and read each decision and sentence from `daemon.log`.
- Three small issues came out of the run. None blocks the launch.
