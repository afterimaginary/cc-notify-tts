# cc-notify-tts

Configurable, summarized voice notifications for Claude Code on macOS.

It speaks one short sentence, either what finished or what Claude is waiting on, at the moments you choose. By default that's only when you're not watching. Speech and wording run locally on your Mac.

**Status:** in development. See [docs/brief.md](docs/brief.md) for what's being built and [STATE.md](STATE.md) for progress.

## Commands

`cc-notify-tts status` says whether it would speak now and why, `fire <event>` plays a sample notification, `mute [--for 30m]` and `unmute` silence it, `doctor` checks the install, and `setup` installs it. See the [CLI spec](docs/specs/cli.md).

## License

[MIT](LICENSE)
