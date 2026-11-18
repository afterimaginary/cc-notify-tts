"""Listen to the fp32 and int8 Kokoro models side by side (ADR-0012).

Plays each notification-shaped sentence twice, once per model, in an order
that changes per sentence and isn't shown until you've picked, so you judge by
ear. Needs both model files and the voices file in the data folder's `models/`,
and an audio device. Not part of `make ready` or `make verify`.

    uv run python spikes/kokoro_listening/listen.py [--voice af_heart]

The fp32 file isn't downloaded by `setup`. ADR-0012 has the command to fetch it.
"""

import argparse
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from kokoro_onnx import Kokoro

from cc_notify_tts.models import VOICES, default_models_dir
from cc_notify_tts.speech import AFPLAY, write_wav

VARIANTS = {"fp32": "kokoro-v1.0.onnx", "int8": "kokoro-v1.0.int8.onnx"}
SENTENCES = [
    "Finished up in webapp. Fixed the login test.",
    "Claude needs your okay in api to run the database migration.",
    "Claude has a question in docs. Should the guide cover Windows too?",
    "Turn failed in billing after a rate limit from the model provider.",
    "Background work done in infra. Three agents checked every Terraform module.",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default="af_heart")
    args = parser.parse_args()

    folder = default_models_dir()
    missing = [n for n in [*VARIANTS.values(), VOICES.name] if not (folder / n).exists()]
    if missing:
        print(f"Missing from {folder}: {', '.join(missing)}. See this script's docstring.")
        return 1
    models = {v: Kokoro(str(folder / n), str(folder / VOICES.name)) for v, n in VARIANTS.items()}

    picks: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, sentence in enumerate(SENTENCES, 1):
            order = random.sample(list(VARIANTS), 2)
            for label, variant in zip("AB", order, strict=True):
                samples, rate = models[variant].create(sentence, voice=args.voice)
                wav = Path(tmp) / f"{i}{label}.wav"
                write_wav(wav, samples, rate)
                print(f"{i}{label}: {sentence}")
                subprocess.run([AFPLAY, str(wav)], check=False)
            answer = input("Which sounded better? [A/B/same] ").strip().upper()
            choice = dict(zip("AB", order, strict=True)).get(answer, "same")
            picks.append((sentence, choice))
            print(f"  A was {order[0]}, B was {order[1]}\n")

    for variant in [*VARIANTS, "same"]:
        print(f"{variant}: {sum(1 for _, c in picks if c == variant)} of {len(picks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
