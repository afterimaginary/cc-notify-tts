#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["kokoro-onnx==0.6.1"]
# ///
"""Spike: how fast is Kokoro-82M through kokoro-onnx on this Mac?

Measures, in one run:

- cold start in fresh processes: import, model load, first synthesis, resident memory
- warm synthesis p50/p95 by sentence length (5 to 20 words)
- warm synthesis while `afplay` plays, started by this process (a child, as the
  daemon would play speech) and by an unrelated launchd job (other audio on the Mac)
- warm synthesis straight after a clip finished playing, and a final baseline to
  catch any slowdown that lingers

It is not part of `make ready` or `make verify`: it needs the model files (about
350 MB) and an audio device, and it plays quiet audio while it runs.

    uv run --script spikes/kokoro_latency/kokoro_latency.py --download
    uv run --script spikes/kokoro_latency/kokoro_latency.py --variant int8 --json out.json

Model files go in ~/Library/Application Support/cc-notify-tts/models by default,
never in the repository. Results are recorded in ADR-0005.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_FILES = {
    "fp32": "kokoro-v1.0.onnx",
    "fp16": "kokoro-v1.0.fp16.onnx",
    "int8": "kokoro-v1.0.int8.onnx",
}
VOICES_FILE = "voices-v1.0.bin"
DEFAULT_MODELS_DIR = Path.home() / "Library/Application Support/cc-notify-tts/models"
LAUNCHD_LABEL = "dev.cc-notify-tts.spike.playback"

# Notification-shaped sentences, three per length. Word counts are checked below.
SENTENCES: dict[int, list[str]] = {
    5: [
        "Finished up in the webapp.",
        "Claude needs you in api.",
        "Background work done in docs.",
    ],
    8: [
        "Finished up in webapp. Fixed the login test.",
        "Claude needs your okay to run the migration.",
        "Turn failed in api after a rate limit.",
    ],
    12: [
        "Finished up in webapp. Refactored the auth module and updated its tests.",
        "Claude needs your okay in api to force-push the release branch now.",
        "Still going in docs. Two background agents are checking every broken link.",
    ],
    16: [
        "Finished up in webapp. Moved the session store to Redis and added "
        "integration tests for expiry.",
        "Claude has a question in api. Should the endpoint return paginated "
        "results or the full list?",
        "Background work done in docs. The link checker found broken links in "
        "the getting started guide.",
    ],
    20: [
        "Finished up in webapp. Rewrote the date parser to handle time zones, "
        "fixed two flaky tests, and updated the changelog.",
        "Claude needs your okay in api to delete the old staging database and "
        "recreate it from last night's full backup.",
        "Turn failed in docs. The build stopped because the theme package could "
        "not be downloaded from the npm registry again.",
    ],
}
COLD_SENTENCE = SENTENCES[12][0]
# Long enough to cover a whole measurement phase without gaps between loops.
PLAYBACK_TEXT = " ".join(s for group in SENTENCES.values() for s in group)
SHORT_CLIP_TEXT = "Finished up in webapp."

for _n, _group in SENTENCES.items():
    for _s in _group:
        assert len(_s.split()) == _n, (_n, _s)


# -- measurement helpers ------------------------------------------------------


def rss_mb(pid: int | None = None) -> float:
    """Current resident memory of a process, from `ps` (KiB)."""
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid or os.getpid())],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(out.strip()) / 1024


def peak_rss_mb() -> float:
    # ru_maxrss is in bytes on macOS.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    rank = max(1, round(q / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarize(samples: dict[int, list[float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for n, values in samples.items():
        out[str(n)] = {
            "n": len(values),
            "p50_ms": round(statistics.median(values), 1),
            "p95_ms": round(pct(values, 95), 1),
            "min_ms": round(min(values), 1),
            "max_ms": round(max(values), 1),
        }
    every = [v for values in samples.values() for v in values]
    out["all"] = {
        "n": len(every),
        "p50_ms": round(statistics.median(every), 1),
        "p95_ms": round(pct(every, 95), 1),
        "min_ms": round(min(every), 1),
        "max_ms": round(max(every), 1),
    }
    return out


def timed(fn: Callable[[], object]) -> float:
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


# -- model files --------------------------------------------------------------


def ensure_models(models_dir: Path, variant: str, download: bool) -> tuple[Path, Path]:
    models_dir.mkdir(parents=True, exist_ok=True)
    paths = (models_dir / MODEL_FILES[variant], models_dir / VOICES_FILE)
    for path in paths:
        if path.exists() and path.stat().st_size > 0:
            continue
        if not download:
            sys.exit(f"missing {path}; pass --download to fetch it from {RELEASE_URL}")
        url = f"{RELEASE_URL}/{path.name}"
        print(f"downloading {url}", file=sys.stderr)
        partial = path.with_suffix(path.suffix + ".partial")
        with urllib.request.urlopen(url) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        partial.rename(path)
    return paths


def write_wav(path: Path, samples: Any, rate: int) -> None:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())


# -- playback -----------------------------------------------------------------


@contextlib.contextmanager
def child_playback(wav: Path, volume: float) -> Iterator[None]:
    """Loop `afplay` as a child of this process, the way the daemon plays speech."""
    stop = threading.Event()
    current: list[subprocess.Popen[bytes]] = []

    def loop() -> None:
        while not stop.is_set():
            proc = subprocess.Popen(["afplay", "-v", str(volume), str(wav)])
            current[:] = [proc]
            proc.wait()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    time.sleep(0.5)  # let playback actually start
    try:
        yield
    finally:
        stop.set()
        for proc in current:
            proc.terminate()
        thread.join()


@contextlib.contextmanager
def separate_playback(wav: Path, volume: float) -> Iterator[None]:
    """Loop `afplay` from a launchd job: outside this process tree and its coalition."""
    subprocess.run(["launchctl", "remove", LAUNCHD_LABEL], capture_output=True)
    subprocess.run(
        [
            "launchctl",
            "submit",
            "-l",
            LAUNCHD_LABEL,
            "--",
            "/bin/sh",
            "-c",
            'while :; do /usr/bin/afplay -v "$0" "$1"; done',
            str(volume),
            str(wav),
        ],
        check=True,
    )
    time.sleep(1.0)
    try:
        yield
    finally:
        subprocess.run(["launchctl", "remove", LAUNCHD_LABEL], check=False)
        time.sleep(0.5)


def audio_assertions() -> list[str]:
    """Power assertions held by coreaudiod, i.e. is anything already playing?"""
    out = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if "coreaudiod" in line]


# -- phases -------------------------------------------------------------------


def cold_start(model: Path, voices: Path, voice: str) -> dict[str, float]:
    """Everything a freshly started daemon pays before its first sentence."""
    start = time.perf_counter()
    from kokoro_onnx import Kokoro

    import_ms = (time.perf_counter() - start) * 1000
    rss_before_load = rss_mb()

    holder: list[Kokoro] = []
    load_ms = timed(lambda: holder.append(Kokoro(str(model), str(voices))))
    kokoro = holder[0]
    rss_loaded = rss_mb()
    first_ms = timed(lambda: kokoro.create(COLD_SENTENCE, voice=voice, lang="en-us"))
    second_ms = timed(lambda: kokoro.create(COLD_SENTENCE, voice=voice, lang="en-us"))
    return {
        "import_ms": round(import_ms, 1),
        "load_ms": round(load_ms, 1),
        "first_synthesis_ms": round(first_ms, 1),
        "second_synthesis_ms": round(second_ms, 1),
        "rss_before_load_mb": round(rss_before_load, 1),
        "rss_loaded_mb": round(rss_loaded, 1),
        "rss_after_two_mb": round(rss_mb(), 1),
    }


def warm_phase(kokoro: Any, voice: str, runs: int, between: Callable[[], object] | None = None):
    samples: dict[int, list[float]] = {n: [] for n in SENTENCES}
    for _ in range(runs):
        for n, group in SENTENCES.items():
            for text in group:
                if between:
                    between()
                samples[n].append(timed(lambda t=text: kokoro.create(t, voice=voice, lang="en-us")))
    return summarize(samples)


def run_cold_children(args: argparse.Namespace) -> list[dict[str, float]]:
    results: list[dict[str, float]] = []
    for i in range(args.cold_runs):
        print(f"cold start {i + 1}/{args.cold_runs}", file=sys.stderr)
        out = subprocess.run(
            [
                sys.executable,
                __file__,
                "--cold-only",
                "--variant",
                args.variant,
                "--models-dir",
                str(args.models_dir),
                "--voice",
                args.voice,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        results.append(json.loads(out))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--variant", choices=sorted(MODEL_FILES), default="fp32")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--download", action="store_true", help="fetch missing model files")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--runs", type=int, default=7, help="warm passes over all sentences")
    parser.add_argument("--playback-runs", type=int, default=3, help="passes under playback")
    parser.add_argument("--cold-runs", type=int, default=3, help="fresh-process cold starts")
    parser.add_argument("--volume", type=float, default=0.15, help="afplay volume, 0 to 1")
    parser.add_argument("--json", type=Path, help="also write results here")
    parser.add_argument("--label", default="", help="how this run was started, for the report")
    parser.add_argument("--cold-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    model, voices = ensure_models(args.models_dir, args.variant, args.download)
    if args.cold_only:
        print(json.dumps(cold_start(model, voices, args.voice)))
        return

    import kokoro_onnx
    import onnxruntime  # pyright: ignore[reportMissingTypeStubs]

    results: dict[str, Any] = {
        "machine": {
            "chip": subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
            ).stdout.strip(),
            "macos": platform.mac_ver()[0],
            "python": platform.python_version(),
            "kokoro_onnx": kokoro_onnx.__version__ if hasattr(kokoro_onnx, "__version__") else "",
            "onnxruntime": onnxruntime.__version__,
            "providers": onnxruntime.get_available_providers(),
            "audio_already_playing": audio_assertions(),
            "context": args.label,
        },
        "model": {
            "variant": args.variant,
            "files": {p.name: p.stat().st_size for p in (model, voices)},
            "source": RELEASE_URL,
            "voice": args.voice,
        },
    }

    results["cold"] = run_cold_children(args)

    print("loading model in this process", file=sys.stderr)
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(model), str(voices))
    results["memory"] = {"rss_loaded_mb": round(rss_mb(), 1)}

    phases: dict[str, Any] = {}
    print("warm baseline", file=sys.stderr)
    phases["baseline"] = warm_phase(kokoro, args.voice, args.runs)
    # Measured before the long playback clip is synthesized: onnxruntime keeps
    # buffers sized for the longest input it has seen.
    results["memory"]["rss_after_warm_mb"] = round(rss_mb(), 1)

    tmp = Path(tempfile.mkdtemp(prefix="kokoro-spike-"))
    try:
        samples, rate = kokoro.create(PLAYBACK_TEXT, voice=args.voice, lang="en-us")
        long_wav = tmp / "long.wav"
        write_wav(long_wav, samples, rate)
        samples, rate = kokoro.create(SHORT_CLIP_TEXT, voice=args.voice, lang="en-us")
        short_wav = tmp / "short.wav"
        write_wav(short_wav, samples, rate)
        results["memory"]["rss_after_long_text_mb"] = round(rss_mb(), 1)

        playing: dict[str, list[str]] = {}
        print("warm, afplay as a child of this process", file=sys.stderr)
        with child_playback(long_wav, args.volume):
            playing["child"] = audio_assertions()
            phases["during_child_playback"] = warm_phase(kokoro, args.voice, args.playback_runs)
        time.sleep(3)
        print("warm, afplay from a separate launchd job", file=sys.stderr)
        with separate_playback(long_wav, args.volume):
            playing["separate"] = audio_assertions()
            phases["during_separate_playback"] = warm_phase(kokoro, args.voice, args.playback_runs)
        time.sleep(3)
        # Proof the playback phases really had audio running.
        results["audio_during_playback"] = playing
        print("warm, straight after a short clip finished playing", file=sys.stderr)
        phases["after_child_playback"] = warm_phase(
            kokoro,
            args.voice,
            args.playback_runs,
            between=lambda: subprocess.run(
                ["afplay", "-v", str(args.volume), str(short_wav)], check=True
            ),
        )
        time.sleep(3)
        print("warm baseline again", file=sys.stderr)
        phases["baseline_again"] = warm_phase(kokoro, args.voice, args.runs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    base = phases["baseline"]["all"]["p50_ms"]
    for phase in phases.values():
        phase["all"]["slowdown_vs_baseline"] = round(phase["all"]["p50_ms"] / base, 2)
    results["warm"] = phases
    results["memory"]["peak_rss_mb"] = round(peak_rss_mb(), 1)

    print_report(results)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n")


def print_report(results: dict[str, Any]) -> None:
    m = results["machine"]
    print(f"\n{m['chip']}, macOS {m['macos']}, onnxruntime {m['onnxruntime']} {m['context']}")
    print(f"providers: {', '.join(m['providers'])}")
    if m["audio_already_playing"]:
        print("WARNING: audio was already playing:", *m["audio_already_playing"], sep="\n  ")
    model = results["model"]
    print(f"\nmodel {model['variant']} from {model['source']}")
    for name, size in model["files"].items():
        print(f"  {name}: {size / 1e6:.1f} MB")

    print("\ncold start (fresh process)")
    keys = list(results["cold"][0])
    print("  " + " | ".join(keys))
    for row in results["cold"]:
        print("  " + " | ".join(str(row[k]) for k in keys))

    print("\nwarm synthesis, ms (p50 / p95)")
    phases = results["warm"]
    lengths = [k for k in phases["baseline"] if k != "all"]
    print("  phase | " + " | ".join(f"{n} words" for n in lengths) + " | all | slowdown")
    for name, phase in phases.items():
        cells = [f"{phase[n]['p50_ms']:.0f} / {phase[n]['p95_ms']:.0f}" for n in lengths]
        total = phase["all"]
        print(
            f"  {name} | "
            + " | ".join(cells)
            + f" | {total['p50_ms']:.0f} / {total['p95_ms']:.0f}"
            + f" | {total['slowdown_vs_baseline']}x"
        )

    mem = results["memory"]
    print(
        f"\nresident memory: {mem['rss_loaded_mb']} MB once loaded, "
        f"{mem['rss_after_warm_mb']} MB after warm 5-20 word sentences, "
        f"{mem['rss_after_long_text_mb']} MB after a long paragraph, {mem['peak_rss_mb']} MB peak"
    )
    for where, lines in results["audio_during_playback"].items():
        print(f"coreaudiod assertions during {where} playback: {len(lines)}")


if __name__ == "__main__":
    main()
