"""Turning a sentence into sound: Kokoro synthesis to a WAV file, then `afplay`.

The daemon builds one `KokoroSynthesizer` at start and keeps it for its whole
life, so the model is loaded and warmed once (ADR-0005). `Speaker.speak`
synthesizes the whole sentence to a temporary WAV before playback starts, so no
compute runs while audio plays and there's never a gap mid-sentence.
"""

import logging
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Protocol

import numpy as np
from kokoro_onnx import Kokoro

from cc_notify_tts.models import MODEL, VOICES, default_models_dir
from cc_notify_tts.timeline import Timeline

log = logging.getLogger(__name__)

DEFAULT_VOICE = "af_heart"
AFPLAY = "/usr/bin/afplay"


class Synthesizer(Protocol):
    def synthesize(self, text: str, voice: str, wav: Path) -> None:
        """Write `text`, spoken in `voice`, to `wav`. Returns once the file is complete."""
        ...


class Player(Protocol):
    def play(self, wav: Path) -> bool:
        """Play `wav` and return when it has finished, with whether it played."""
        ...


class KokoroSynthesizer:
    """Kokoro-82M through kokoro-onnx, loaded and warmed once."""

    def __init__(self, models: Path | None = None) -> None:
        models = default_models_dir() if models is None else models
        start = time.monotonic()
        self._kokoro = Kokoro(str(models / MODEL.name), str(models / VOICES.name))
        self._voices = frozenset(self._kokoro.get_voices())
        # The first synthesis is about 200 ms slower than later ones (ADR-0005),
        # so pay for it now rather than on the first notification.
        self._kokoro.create("Ready.", voice=DEFAULT_VOICE)
        log.info("speech model loaded and warmed in %.2f s", time.monotonic() - start)

    def synthesize(self, text: str, voice: str, wav: Path) -> None:
        if voice not in self._voices:
            log.warning("unknown voice %r, using %s", voice, DEFAULT_VOICE)
            voice = DEFAULT_VOICE
        start = time.monotonic()
        samples, rate = self._kokoro.create(text, voice=voice)
        write_wav(wav, samples, rate)
        # Reported so a daemon started at a low priority shows up (ADR-0002).
        log.info("synthesized %d words in %.2f s", len(text.split()), time.monotonic() - start)


class AfplayPlayer:
    def play(self, wav: Path) -> bool:
        done = subprocess.run([AFPLAY, str(wav)], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            log.warning("afplay exited %d: %s", done.returncode, done.stderr.strip())
        return done.returncode == 0


class Speaker:
    def __init__(self, synthesizer: Synthesizer, player: Player) -> None:
        self._synthesizer = synthesizer
        self._player = player

    def speak(
        self, text: str, voice: str = DEFAULT_VOICE, timeline: Timeline | None = None
    ) -> bool:
        """Synthesize `text` in full, then play it. Returns when playback ends, with
        whether it played. Marks `synthesized` and `playing` on `timeline`."""
        with tempfile.TemporaryDirectory(prefix="cc-notify-tts-") as tmp:
            wav = Path(tmp) / "speech.wav"
            self._synthesizer.synthesize(text, voice, wav)
            if timeline is None:
                return self._player.play(wav)
            timeline.mark("synthesized")
            timeline.audio_seconds = wav_seconds(wav)
            # afplay's own start-up comes after this mark: `ended` less the audio's
            # length bounds it (ADR-0019).
            timeline.mark("playing")
            played = self._player.play(wav)
            timeline.mark("ended")
            return played


def write_wav(path: Path, samples: np.typing.NDArray[np.float32], rate: int) -> None:
    """Write mono float samples in [-1, 1] as 16-bit PCM."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())


def wav_seconds(path: Path) -> float | None:
    """How long `path` plays, or None if it isn't a WAV file that can be read."""
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / wav.getframerate()
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        return None
