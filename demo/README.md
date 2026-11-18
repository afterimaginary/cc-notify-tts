# Demo

`make demo` builds the demo video from scratch:

- `build/demo/cc-notify-tts-demo.mp4`: the full cut, 1080p H.264 with AAC audio, with captions and title and end cards. It isn't committed.
- `docs/demo.gif`: a short silent cut of the same scenes, for the README.

It needs [VHS](https://github.com/charmbracelet/vhs), ffmpeg and ImageMagick (`brew install vhs ffmpeg imagemagick`), and the speech model, which `cc-notify-tts setup` downloads (otherwise the build fetches it into `build/demo/models`). Nothing is played out loud while it records.

## What it shows

1. A turn finishes while you're in another app, and the finished turn is spoken.
2. A permission request, spoken with the command it's waiting on.
3. A turn finishes while you're typing in the session, so nothing is said, and `cc-notify-tts status` says why.

## How it works

The session on screen is simulated, but everything behind it is real: each scene runs the plugin's own hook commands through the real shim and the real daemon, and the sentences are synthesized by Kokoro. Two things are scripted so every run is the same: whether you're away, and the wording, which uses templates rather than Ollama. See ADR-0020 in [docs/decisions/](../docs/decisions/).

| File | Role |
| - | - |
| `build.py` | Lays out a throwaway home, records each scene with VHS, then assembles the cut and the GIF with ffmpeg and ImageMagick. |
| `session.py` | Plays one scene: prints the simulated session, runs the hooks and waits for the daemon. |
| `daemon.py` | The real daemon, with Kokoro writing to files instead of the speakers and presence read from a file. |
| `cli.py` | The real `cc-notify-tts` command, with idle time read from the same file. |
