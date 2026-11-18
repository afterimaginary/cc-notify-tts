# cc-notify-tts

Configurable, summarized voice notifications for Claude Code on macOS.

It speaks one short sentence, either what finished or what Claude is waiting on, at the moments you choose. By default that's only when you're not watching. Speech and wording run locally on your Mac.

**Status:** in development. See [docs/brief.md](docs/brief.md) for what's being built and [STATE.md](STATE.md) for progress.

## What it says

Seven events, each worded from what Claude Code reports about it:

| Event | Example |
| - | - |
| A turn finished | "Finished up in weather app. Added exponential backoff to the forecast fetcher and two tests for the retry path." |
| Finished, with background work still running | "Done for now in weather app, but background work isn't finished. Still going: audit the forecast cache and run the integration test suite." |
| That background work is all done | "Background work's all done in weather app. The integration suite finished: 112 passed, 0 failed." |
| A permission request | "Weather app needs your okay to force-push the rebased branch to main." |
| A question or a plan to approve | "Weather app has a question for you. Should the retry give up after three attempts or five?" |
| An action was denied | "Weather app was refused permission to clean build output." |
| A turn failed | "The turn failed in weather app. It hit a rate limit." |

By default each one is spoken only when you're away: no keyboard or mouse input for 20 s, or the app the session runs in out of front for 10 s. If [Ollama](https://ollama.com) is running with the wording model loaded, it may write the detail of a finished turn, a question or a permission request; otherwise the built-in wording above is used.

## Install

You need macOS, [Homebrew](https://brew.sh) and Claude Code.

```sh
brew install uv
uv tool install --managed-python --python 3.13 git+https://github.com/afterimaginary/cc-notify-tts
~/.local/bin/cc-notify-tts setup
```

`setup` links Python for the hooks, downloads the speech model (about 120 MB), installs the Claude Code plugin and, if needed, adds `~/.local/bin` to your shell's `PATH`. Open a new terminal afterwards. Running `setup` again repairs the install.

If `claude` isn't on your `PATH`, as with only the desktop app, `setup` says so and skips the plugin. Install it from Claude Code instead. On Claude Code 2.1.275 or later that's one command:

```
/plugin install cc-notify-tts --marketplace afterimaginary/cc-notify-tts
```

On earlier versions, add the marketplace first:

```
/plugin marketplace add afterimaginary/cc-notify-tts
/plugin install cc-notify-tts@cc-notify-tts
```

Then run `cc-notify-tts doctor` to check that everything a notification needs is in place. Each problem it finds comes with a fix.

### Optional: model wording

For a finished turn, a question or a permission request, a local [Ollama](https://ollama.com) model can write a shorter, more specific detail than the built-in wording. The default model is `granite4:micro`, picked by the [wording eval](evals/README.md) from ten candidates (ADR-0018 in [docs/decisions/](docs/decisions/)). With Ollama installed:

```sh
ollama pull granite4:micro
```

It holds about 2.2 GiB of memory while a Claude Code session is open. Without it, the built-in wording is spoken. To use another model, set `wording.model` in the [config file](docs/specs/config.md).

## Use

New Claude Code sessions are spoken automatically. Nothing needs to stay running: a small background process starts with the first event and exits after two hours without one.

**See whether it would speak now, and why:**

```
$ cc-notify-tts status
Daemon    running, pid 4242, version 0.1.0, speech ready
Mute      off
Config    ~/Library/Application Support/cc-notify-tts/config.toml (no file, so every setting is its default)
Presence  last input 2 s ago (away after 20 s); Terminal in front

Would it speak now? Sessions in Terminal: weather app
  every event      silent: you were active 2 s ago and Terminal is in front
```

**Hear a sample notification.** `fire` goes through the same when-to-speak check as a real event, so while you're typing it's usually silent and says why. `--always` skips the check:

```
$ cc-notify-tts fire permission --always
Speaking: "Weather app needs your okay to run npm test."
```

The events are `finished`, `still_running`, `background_done`, `permission`, `question`, `denied` and `failed`.

**Mute it:**

```sh
cc-notify-tts mute            # until you unmute
cc-notify-tts mute --for 30m  # or 2h, 90s, 1h30m
cc-notify-tts unmute
```

**Change when each event is spoken,** quiet hours, the presence thresholds or the voice in `~/Library/Application Support/cc-notify-tts/config.toml`. Changes apply to the next notification. See the [config reference](docs/specs/config.md).

All commands are in the [CLI reference](docs/specs/cli.md).

## Uninstall

```sh
claude plugin uninstall cc-notify-tts@cc-notify-tts
claude plugin marketplace remove cc-notify-tts
uv tool uninstall cc-notify-tts
rm -rf ~/Library/Application\ Support/cc-notify-tts ~/Library/Logs/cc-notify-tts
```

With only the desktop app, run the first two as `/plugin uninstall cc-notify-tts@cc-notify-tts` and `/plugin marketplace remove cc-notify-tts` in Claude Code. A background process still running exits on its own after its idle timeout.

## How it works

Claude Code's hooks hand each event to a small local daemon over a Unix socket. The daemon works out which of the seven events it is, checks whether to speak, writes the sentence and speaks it with [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the decisions in [docs/decisions/](docs/decisions/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
