import wave
from pathlib import Path

import numpy as np

from cc_notify_tts.speech import Speaker, write_wav


class FakeSynthesizer:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def synthesize(self, text: str, voice: str, wav: Path) -> None:
        self.log.append(f"synthesize start {text!r} {voice}")
        write_wav(wav, np.zeros(2400, dtype=np.float32), 24000)
        self.log.append("synthesize end")


class FakePlayer:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.played: list[Path] = []

    def play(self, wav: Path) -> None:
        # The file must be complete when playback starts.
        with wave.open(str(wav)) as w:
            self.log.append(f"play {w.getnframes()} frames")
        self.played.append(wav)


def test_synthesis_finishes_before_playback_starts() -> None:
    log: list[str] = []
    Speaker(FakeSynthesizer(log), FakePlayer(log)).speak("Finished up in api.", "am_adam")

    assert log == [
        "synthesize start 'Finished up in api.' am_adam",
        "synthesize end",
        "play 2400 frames",
    ]


def test_temporary_wav_is_removed_after_playback() -> None:
    log: list[str] = []
    player = FakePlayer(log)
    Speaker(FakeSynthesizer(log), player).speak("Done.")

    assert player.played[0].suffix == ".wav"
    assert not player.played[0].exists()
    assert not player.played[0].parent.exists()


def test_write_wav_is_16_bit_mono_and_clips(tmp_path: Path) -> None:
    path = tmp_path / "out.wav"
    write_wav(path, np.array([0.0, 0.5, 2.0, -2.0], dtype=np.float32), 24000)

    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        frames = np.frombuffer(w.readframes(4), dtype="<i2")
    assert frames.tolist() == [0, 16383, 32767, -32767]
