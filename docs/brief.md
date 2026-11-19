# cc-notify-tts

Configurable, summarized voice notifications for Claude Code on macOS.

## Problem

Long Claude Code turns finish or stall on a question while you're in another app
or away from the desk. Visual notifications are easy to miss and don't say what
happened, and a voice that talks every turn gets uninstalled within a week.
cc-notify-tts speaks one short sentence (what finished, or what Claude is waiting
on), lets you choose per event when it speaks (by default only when you're not
watching), and does everything on your machine.

## MVP

- A Claude Code plugin. Its hooks send events to a small local daemon. Install is
  three commands: `brew install uv`, `uv tool install` of the package, then
  `cc-notify-tts setup`, which installs the plugin through Claude Code's own
  plugin command. The user's settings.json is changed only by Claude Code
  (ADR-0011).
- Events: turn finished; turn finished with background agents or tasks still
  running; background work all done (only if the "still running" message was
  heard); permission request, naming the action; question for the user; action
  denied; turn failed.
- Speech: Kokoro-82M via kokoro-onnx, played with afplay. One utterance at a
  time, one pending slot; stale queued messages are dropped.
- When to speak, per event: `away` (default), `always` or `never`, with mute and
  quiet hours on top. `away` means no keyboard or mouse input for N seconds, or
  the app hosting the session hasn't been in front for M seconds; either is
  enough. Being in front never silences anything by itself. If presence can't be
  read, speak.
- Wording: code writes the frame and the project name; a local Ollama model may
  write the detail for events with prose to compress (finished, question,
  permission), behind guards. Template wording is always available and used
  whenever Ollama is missing, slow, or fails a guard.
- The daemon starts on demand from a hook and exits after an idle timeout.
- CLI: `cc-notify-tts status` (would it speak now, and why), `fire <event>`,
  `mute` / `unmute`, `doctor`, `setup` (downloads the speech model, links the
  interpreters and installs the plugin).
- One TOML config file, defaults in code, re-read on every event.
- `make eval`: a reproducible eval of local wording models over synthetic hook
  payloads: guard pass rate, required detail kept, nothing invented, latency,
  resident memory. Writes a dated results table.

## Done means

On a Mac with only Homebrew, following the README:
1. Three documented commands install the plugin and its models.
2. A real session given a multi-step task, with the user switched to a browser,
   is spoken within 3 s of the turn ending, with an accurate detail.
3. A permission prompt while away is spoken with the actual action.
4. With the default setting it stays silent while the user types in the session,
   and `status` says why; set to `always`, the same turn is spoken.
5. With Ollama stopped, template wording is spoken without errors.
6. `make eval` reproduces the README's eval table within run-to-run noise.
7. No hook adds more than 100 ms (p95) to a Claude Code event, and no process
   remains once no session has been open for the idle timeout.

## Design

- Python 3.13 with uv; ruff, pyright (strict), pytest. macOS only.
- The hook shim imports only the standard library, never writes to stdout,
  always exits 0, and logs to ~/Library/Logs/cc-notify-tts. Expensive work lives
  in the daemon, which it reaches over a Unix socket.
- Session state (turn start, live background agents and tasks, what was heard)
  lives on disk so it survives a daemon restart. Duplicate events of the same
  kind within 5 s collapse into one.
- All compute finishes before any audio plays.
- Models, state and config live under ~/Library/Application Support/cc-notify-tts.
- Record every measured result and consequential choice in docs/decisions/.

## Milestones

1. Prove the risky parts (tests and spikes, recorded as decisions): plugin hook
   coverage and payload fields in the CLI and the desktop app; hook overhead;
   on-demand daemon lifetime and scheduling priority compared with a LaunchAgent;
   Kokoro latency with and without nearby playback; Ollama warm, cold and evicted
   latency; reading the host app and its frontmost state from a hook.
2. Speaks when you're away: daemon, speech, queue, presence gate, template
   wording for every event, session state, dedupe, plugin packaging, setup and
   the CLI. Usable on templates alone.
3. Wording and the eval: Ollama wording with guards and keep-warm, the eval
   harness and fixtures, a model comparison that picks the default, and
   end-to-end latency instrumentation.
4. Show and launch: README with a demo video produced by script (real speech
   output, captions, YouTube-ready), eval and latency tables, v0.1.0.

## Non-goals

Linux or Windows; voice cloning or other TTS engines; cloud TTS or hosted LLMs;
agents other than Claude Code; reading whole replies aloud; phone push; banners;
per-project config; a GUI.
