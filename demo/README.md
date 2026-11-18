# Demo

`make demo` builds the demo video from scratch:

- `build/demo/cc-notify-tts-demo.mp4`: the full cut, 1080p H.264 with AAC audio, with captions and title and end cards. It isn't committed.
- `docs/demo.gif`: a short silent loop of the first scene, for the README.

It needs [VHS](https://github.com/charmbracelet/vhs), ffmpeg and ImageMagick (`brew install vhs ffmpeg imagemagick`), [Ollama](https://ollama.com) running with the default wording model (`ollama pull granite4:micro`), and the speech model, which `cc-notify-tts setup` downloads (otherwise the build fetches it into `build/demo/models`). Nothing is played out loud while it records, and the wording model is unloaded when it ends. VHS keeps a headless Chromium under `~/.cache/rod`, which can be deleted afterwards.

## What it shows

1. A turn finishes while you're in another app, and a one-sentence summary of Claude's long reply is spoken.
2. A permission request, spoken with the command it's waiting on.
3. A turn finishes while you're typing in the session, so nothing is said, and `cc-notify-tts status` says why.

## How it works

The session on screen is simulated, but everything behind it is real: each scene runs the plugin's own hook commands through the real shim and the real daemon, and the sentences are synthesized by Kokoro. The wording comes from the default local model, which at temperature 0 says the same thing every run. Whether you're away is scripted, so your own keyboard and screen never change the video. See ADR-0020 in [docs/decisions/](../docs/decisions/).

| File | Role |
| - | - |
| `build.py` | Lays out a throwaway home, records each scene with VHS, then assembles the cut and the GIF with ffmpeg and ImageMagick. |
| `session.py` | Plays one scene: prints the simulated session, runs the hooks and waits for the daemon. |
| `daemon.py` | The real daemon, model wording included, with Kokoro writing to files instead of the speakers and presence read from a file. |
| `cli.py` | The real `cc-notify-tts` command, with idle time read from the same file. |
