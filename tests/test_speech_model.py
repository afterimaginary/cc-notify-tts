"""The real Kokoro model, synthesizing and playing a sentence.

Opt-in, outside `make ready` and `make verify`: it needs the model files that
`cc-notify-tts setup` downloads, and an audio device. Run it with:

    uv run pytest -m model
"""

import wave
from pathlib import Path

import pytest

from cc_notify_tts import models
from cc_notify_tts.speech import AfplayPlayer, KokoroSynthesizer, Speaker

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def synthesizer() -> KokoroSynthesizer:
    folder = models.default_models_dir()
    missing = [f.name for f in models.FILES if not (folder / f.name).exists()]
    if missing:
        pytest.skip(f"model files missing from {folder}: run `cc-notify-tts setup`")
    return KokoroSynthesizer(folder)


def test_synthesizes_a_sentence_to_a_wav(synthesizer: KokoroSynthesizer, tmp_path: Path) -> None:
    wav = tmp_path / "out.wav"
    synthesizer.synthesize("Finished up in the webapp. Fixed the login test.", "af_heart", wav)

    with wave.open(str(wav)) as w:
        seconds = w.getnframes() / w.getframerate()
    assert 1.0 < seconds < 8.0


def test_unknown_voice_falls_back(synthesizer: KokoroSynthesizer, tmp_path: Path) -> None:
    wav = tmp_path / "out.wav"
    synthesizer.synthesize("Done.", "no_such_voice", wav)
    assert wav.stat().st_size > 1000


def test_speaks_out_loud(synthesizer: KokoroSynthesizer) -> None:
    Speaker(synthesizer, AfplayPlayer()).speak("Claude needs your okay in api.")
