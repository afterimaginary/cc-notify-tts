---
description: The demo video is built from scratch by `make demo`, recording scripted VHS scenes in a throwaway home against the real hooks, shim and daemon, with Kokoro's real audio rendered to files instead of played and mixed in where each sentence appears on screen; presence is scripted and the wording model runs at temperature 0, so every run says the same sentences and makes the same decisions.
status: accepted
---

# ADR-0020: A scripted, reproducible demo video

## Context

The project needs a short video with sound for the release and a silent cut for the README. A screen recording made by hand can't be rebuilt after a change, catches whatever else is on the screen (notifications, other apps, paths and the user's name), and plays audio out loud while it records. The video has to show three things a reader can't get from text: a finished turn spoken while the viewer is away, a permission request naming the action, and `status` explaining why it stayed silent.

## Decision

- **One command, from nothing.** `make demo` lays out a throwaway home under `/tmp`, records each scene, assembles the cut and removes the home and the daemon it started. Its outputs are `build/demo/cc-notify-tts-demo.mp4` (1080p H.264 with AAC, untracked, for the release) and `docs/demo.gif` (committed). Generated audio is never committed.
- **Real hooks, shim and daemon; simulated Claude Code.** Each scene prints a short, made-up session and runs the plugin's own hook commands with hand-written payloads, as the end-to-end test does. A real session would need an account, cost tokens, vary between runs and show real paths. The session's header says it is simulated.
- **Real Kokoro audio, never played.** The daemon is the real one, except that its speaker synthesizes each sentence with the installed Kokoro model to a WAV file instead of calling `afplay`. Recording is silent and the audio is mixed in afterwards.
- **Scripted presence, real wording.** Whether the viewer is away comes from a file each scene writes, not the real keyboard and screen, and `status` reads idle time from the same file, so the owner's activity never changes the video. Wording is the daemon's own: the default Ollama model (ADR-0018), loaded before recording starts and unloaded when the session ends. Model-written summaries are the project's main feature, so the finished turn ends with a long reply that the spoken sentence condenses. At temperature 0 the model repeats its answers exactly (ADR-0016), so runs still match. Without Ollama or the model, `make demo` says so and uses templates.
- **Audio lands on the frame where its sentence appears.** Each scene prints the spoken sentence last, once the daemon has written its audio. The build finds the scene's last screen change with ffmpeg's `freezedetect` and starts the audio there, then holds the last frame until the sentence ends. This doesn't depend on VHS's timing.
- **VHS for the terminal, ImageMagick for text.** VHS records a headless terminal from a tape, so nothing outside it can appear. Captions and cards are rendered as images, since ffmpeg's text filters aren't in every build.

## Consequences

- The demo shows the real messages, decisions and `status` output, but not a real Claude Code window.
- `make demo` needs VHS, ffmpeg, ImageMagick, Ollama and the default wording model, which `ready` and `verify` don't. Only the build's timing helpers run in `ready`.
- The clock times and process ID that `status` prints differ between runs, so every rebuild changes the GIF slightly. Changing a template, the wording model or the `status` output changes the video on the next build; the committed GIF is rebuilt by hand with `make demo`.
