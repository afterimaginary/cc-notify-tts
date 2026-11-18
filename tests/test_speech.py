import time
import wave
from pathlib import Path

import numpy as np
import pytest

from cc_notify_tts import speech
from cc_notify_tts.speech import AfplayPlayer, Speaker, wav_seconds, write_wav
from cc_notify_tts.timeline import Timeline


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

    def play(self, wav: Path) -> bool:
        # The file must be complete when playback starts.
        with wave.open(str(wav)) as w:
            self.log.append(f"play {w.getnframes()} frames")
        self.played.append(wav)
        return True


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


def test_speaking_marks_synthesis_and_playback_on_the_timeline() -> None:
    log: list[str] = []
    timeline = Timeline("Stop", "s1")
    Speaker(FakeSynthesizer(log), FakePlayer(log)).speak("Done.", "af_heart", timeline)

    assert list(timeline.marks) == ["synthesized", "playing", "ended"]
    assert timeline.marks["synthesized"] <= timeline.marks["playing"] <= timeline.marks["ended"]
    assert timeline.audio_seconds == 0.1  # 2400 frames at 24 kHz


def test_wav_seconds_of_a_file_that_isnt_a_wav(tmp_path: Path) -> None:
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav")
    assert wav_seconds(junk) is None
    assert wav_seconds(tmp_path / "missing.wav") is None


def fake_afplay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, seconds: float = 0.1
) -> Path:
    """A stand-in for `afplay` that runs `body` and touches `exited` when it's done,
    given a silent WAV `seconds` long."""
    script = tmp_path / "afplay"
    script.write_text(f"#!/bin/sh\n{body}\ntouch {tmp_path / 'exited'}\n")
    script.chmod(0o755)
    monkeypatch.setattr(speech, "AFPLAY", str(script))
    wav = tmp_path / "speech.wav"
    write_wav(wav, np.zeros(round(24000 * seconds), dtype=np.float32), 24000)
    return wav


def test_play_returns_once_the_sound_ends_without_waiting_for_afplay_to_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # afplay lingers after the sound ends (ADR-0019); here, for 2 s.
    wav = fake_afplay(tmp_path, monkeypatch, "sleep 2")

    start = time.monotonic()
    played = AfplayPlayer().play(wav)
    elapsed = time.monotonic() - start

    assert played
    # Its start-up allowance plus the 0.1 s of audio, not afplay's exit.
    assert speech.AFPLAY_START_SECONDS + 0.1 <= elapsed < 1.0
    assert not (tmp_path / "exited").exists()


def test_play_reports_afplay_failing_at_start_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 5 s of audio, so the stand-in exits long before `play` would stop waiting,
    # however slowly the shell starts.
    wav = fake_afplay(tmp_path, monkeypatch, "echo 'no output device' >&2\nexit 1", seconds=5)
    assert not AfplayPlayer().play(wav)


def test_play_reports_afplay_finishing_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav = fake_afplay(tmp_path, monkeypatch, "true", seconds=5)
    assert AfplayPlayer().play(wav)
    assert (tmp_path / "exited").exists()
