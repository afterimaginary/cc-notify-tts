# Spike: Kokoro latency

Answers the questions behind ADR-0005. How long does Kokoro-82M take through `kokoro-onnx` on this hardware, cold and warm, for notification-length sentences? How much memory does it hold once loaded? Does audio playing nearby slow it down?

`kokoro_latency.py` is a self-contained [`uv` script](https://docs.astral.sh/uv/guides/scripts/): its dependencies (`kokoro-onnx` and what it pulls in) are declared inline, so it doesn't add anything to the project's environment. It isn't part of `make ready` or `make verify`.

## Run it

```sh
uv run --script spikes/kokoro_latency/kokoro_latency.py --download
```

`--download` fetches any missing model files into `~/Library/Application Support/cc-notify-tts/models` from the [`kokoro-onnx` model release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0). Nothing is written inside the repository.

| Option | Default | Does |
| --- | --- | --- |
| `--variant` | `fp32` | Model file: `fp32`, `fp16` or `int8` |
| `--voice` | `af_heart` | Kokoro voice |
| `--runs` | 7 | Warm passes over all 15 sentences, for each baseline |
| `--playback-runs` | 3 | Passes for each playback phase |
| `--cold-runs` | 3 | Fresh processes for the cold-start numbers |
| `--volume` | 0.15 | `afplay` volume for the playback phases |
| `--label` | none | Free text describing how the run was started, shown in the report |
| `--json` | none | Also write every number to this file |

A run takes a few minutes and plays quiet speech through the default output device for part of it. Run it on an otherwise quiet Mac: the report warns if audio was already playing when it started, and other heavy work skews the numbers.

## What it measures

1. **Cold start**, in fresh processes: importing `kokoro_onnx`, loading the model, the first synthesis of a 12-word sentence and the one after it, and resident memory at each step.
2. **Warm synthesis**, in one long-lived process with the model loaded, over 15 notification-shaped sentences, three each of 5, 8, 12, 16 and 20 words. It reports p50 and p95 per length. Phases, in order:
   - `baseline`: nothing playing.
   - `during_child_playback`: `afplay` loops a long clip, started by the measuring process itself, the way the daemon will play speech.
   - `during_separate_playback`: `afplay` loops the same clip from a `launchctl submit` job, outside the measuring process's tree, like other audio on the Mac.
   - `after_child_playback`: before each synthesis, a short clip plays to the end, so each one starts straight after playback.
   - `baseline_again`: nothing playing, to catch a slowdown that lingers.
3. **Resident memory** once loaded, after the warm runs, and after a long paragraph.

The report also records whether `coreaudiod` held a power assertion during each playback phase, which proves audio really was playing.

## Running it the way a LaunchAgent would

To see the same numbers for a process started by launchd rather than a shell:

```sh
launchctl submit -l dev.cc-notify-tts.spike.run -o /tmp/kokoro-launchd.out -e /tmp/kokoro-launchd.err -- \
  /usr/bin/env HOME="$HOME" PATH=/opt/homebrew/bin:/usr/bin:/bin \
  "$(command -v uv)" run --script "$PWD/spikes/kokoro_latency/kokoro_latency.py" --label "(launchd job)"
```

A submitted job is restarted whenever it exits, so remove it once `/tmp/kokoro-launchd.out` has the report:

```sh
launchctl remove dev.cc-notify-tts.spike.run
```

## Cleaning up

The script removes its playback job (`dev.cc-notify-tts.spike.playback`) before it exits. If it's killed mid-run, remove it by hand. To check that nothing is left:

```sh
launchctl list | grep dev.cc-notify-tts.spike   # prints nothing
```

Delete `~/Library/Application Support/cc-notify-tts/models` to remove the model files.
