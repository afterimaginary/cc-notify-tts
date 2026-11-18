"""The mute file: durations, expiry, and surviving a restart."""

import json
from pathlib import Path

import pytest

from cc_notify_tts.mute import Muted, MuteFile, parse_duration
from cc_notify_tts.sessions import SessionStore

NOW = 1_000_000.0


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30m", 1800), ("2h", 7200), ("90s", 90), ("1h30m", 5400), (" 45M ", 2700), ("1h0m5s", 3605)],
)
def test_durations(text: str, seconds: float) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "30", "m", "3x", "1.5h", "-5m", "0m", "30m2h"])
def test_bad_durations_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"duration|no time"):
        parse_duration(text)


def at(path: Path, now: float) -> MuteFile:
    return MuteFile(path, clock=lambda: now)


def test_not_muted_without_a_file(tmp_path: Path) -> None:
    assert at(tmp_path / "mute", NOW).current() is None
    assert not at(tmp_path / "mute", NOW).is_muted()


def test_mute_until_unmute_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "state" / "mute"
    assert at(path, NOW).mute(None) == Muted(None)
    # A new reader, as a restarted daemon would have, a day later.
    assert at(path, NOW + 86400).current() == Muted(None)
    at(path, NOW).unmute()
    assert not at(path, NOW).is_muted()


def test_timed_mute_expires_on_its_own(tmp_path: Path) -> None:
    path = tmp_path / "mute"
    assert at(path, NOW).mute(1800) == Muted(NOW + 1800)
    assert at(path, NOW + 1799).current() == Muted(NOW + 1800)
    assert not at(path, NOW + 1800).is_muted()


def test_a_later_mute_replaces_an_earlier_one(tmp_path: Path) -> None:
    path = tmp_path / "mute"
    at(path, NOW).mute(None)
    at(path, NOW).mute(60)
    assert not at(path, NOW + 61).is_muted()


def test_unmute_without_a_mute_is_fine(tmp_path: Path) -> None:
    at(tmp_path / "mute", NOW).unmute()


@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"until": "soon"}', "{}", '{"until": true}']
)
def test_unreadable_file_reads_as_not_muted(tmp_path: Path, content: str) -> None:
    path = tmp_path / "mute"
    path.write_text(content)
    assert not at(path, NOW).is_muted()


def test_session_pruning_leaves_the_mute_file_alone(tmp_path: Path) -> None:
    path = tmp_path / "mute"
    at(path, NOW).mute(None)
    SessionStore(tmp_path).prune(NOW)
    assert json.loads(path.read_text()) == {"until": None}
