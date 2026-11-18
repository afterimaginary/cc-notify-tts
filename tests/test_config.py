"""Config loading: defaults, every key, and falling back on bad input."""

import logging
from datetime import time
from pathlib import Path

import pytest

from cc_notify_tts import config
from cc_notify_tts.config import Config, QuietHours, Speak, load
from cc_notify_tts.events import EventKind

SPEC = Path(__file__).parent.parent / "docs" / "specs" / "config.md"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_missing_file_means_all_defaults(tmp_path: Path) -> None:
    loaded = load(tmp_path / "nope.toml")
    assert loaded.config == Config()
    assert loaded.problems == ()


def test_defaults() -> None:
    c = Config()
    assert set(c.events) == set(EventKind)
    assert all(speak is Speak.AWAY for speak in c.events.values())
    assert c.quiet_hours is None
    assert (c.presence.idle_seconds, c.presence.away_seconds) == (20.0, 10.0)
    assert c.daemon.idle_timeout_minutes == 120.0
    assert (c.wording.ollama, c.wording.model, c.wording.timeout_seconds) == (
        True,
        "llama3.2:1b",
        1.0,
    )
    assert c.speech.voice == "af_heart"


def test_every_key_is_read(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        [events.finished]
        speak = "always"
        [events.still_running]
        speak = "never"
        [events.background_done]
        speak = "never"
        [events.permission]
        speak = "always"
        [events.question]
        speak = "always"
        [events.denied]
        speak = "away"
        [events.failed]
        speak = "never"

        [quiet_hours]
        start = "22:30"
        end = "07:00"

        [presence]
        idle_seconds = 45
        away_seconds = 0

        [daemon]
        idle_timeout_minutes = 30

        [wording]
        ollama = false
        model = "qwen3:1.7b"
        timeout_seconds = 0.5

        [speech]
        voice = "bf_emma"
        """,
    )
    loaded = load(path)
    assert loaded.problems == ()
    c = loaded.config
    assert dict(c.events) == {
        EventKind.FINISHED: Speak.ALWAYS,
        EventKind.STILL_RUNNING: Speak.NEVER,
        EventKind.BACKGROUND_DONE: Speak.NEVER,
        EventKind.PERMISSION: Speak.ALWAYS,
        EventKind.QUESTION: Speak.ALWAYS,
        EventKind.DENIED: Speak.AWAY,
        EventKind.FAILED: Speak.NEVER,
    }
    assert c.quiet_hours == QuietHours(time(22, 30), time(7, 0))
    assert (c.presence.idle_seconds, c.presence.away_seconds) == (45.0, 0.0)
    assert c.daemon.idle_timeout_minutes == 30.0
    assert (c.wording.ollama, c.wording.model, c.wording.timeout_seconds) == (
        False,
        "qwen3:1.7b",
        0.5,
    )
    assert c.speech.voice == "bf_emma"


def test_unset_events_keep_their_default(tmp_path: Path) -> None:
    c = load(write(tmp_path, '[events.question]\nspeak = "always"\n')).config
    assert c.events[EventKind.QUESTION] is Speak.ALWAYS
    assert c.events[EventKind.FINISHED] is Speak.AWAY


def test_toml_local_times_work_for_quiet_hours(tmp_path: Path) -> None:
    c = load(write(tmp_path, "[quiet_hours]\nstart = 22:00:00\nend = 08:00:00\n")).config
    assert c.quiet_hours == QuietHours(time(22, 0), time(8, 0))


def test_unknown_keys_are_reported_and_ignored(tmp_path: Path) -> None:
    loaded = load(
        write(
            tmp_path,
            """
            volume = 3
            [events.idle]
            speak = "always"
            [events.finished]
            speak = "always"
            chime = "Glass"
            [presence]
            idle_secs = 5
            """,
        )
    )
    assert loaded.problems == (
        "volume: unknown key, ignored",
        "events.idle: unknown key, ignored",
        "events.finished.chime: unknown key, ignored",
        "presence.idle_secs: unknown key, ignored",
    )
    assert loaded.config.events[EventKind.FINISHED] is Speak.ALWAYS
    assert loaded.config.presence == Config().presence


@pytest.mark.parametrize(
    ("toml", "problem"),
    [
        ('[events.finished]\nspeak = "sometimes"', "events.finished.speak: 'sometimes' isn't"),
        ('[events]\nfinished = "always"', "events.finished: must be a table"),
        ('presence = "fast"', "presence: must be a table"),
        ("[presence]\nidle_seconds = -1", "presence.idle_seconds: -1 isn't a number, 0 or more"),
        ('[presence]\naway_seconds = "10"', "presence.away_seconds: '10' isn't a number"),
        ("[presence]\nidle_seconds = true", "presence.idle_seconds: True isn't a number"),
        ("[presence]\nidle_seconds = nan", "presence.idle_seconds: nan isn't a number"),
        ("[daemon]\nidle_timeout_minutes = 0", "daemon.idle_timeout_minutes: 0 isn't a number"),
        ('[wording]\nollama = "yes"', "wording.ollama: 'yes' isn't true or false"),
        ('[wording]\nmodel = ""', "wording.model: '' isn't a name"),
        ("[wording]\ntimeout_seconds = -0.5", "wording.timeout_seconds: -0.5 isn't a number above"),
        ("[speech]\nvoice = 7", "speech.voice: 7 isn't a name"),
    ],
)
def test_invalid_values_fall_back_to_their_default(tmp_path: Path, toml: str, problem: str) -> None:
    loaded = load(write(tmp_path, toml))
    assert loaded.config == Config()
    assert len(loaded.problems) == 1
    assert loaded.problems[0].startswith(problem)


def test_one_bad_value_leaves_the_rest_alone(tmp_path: Path) -> None:
    c = load(write(tmp_path, "[presence]\nidle_seconds = -5\naway_seconds = 30\n")).config
    assert (c.presence.idle_seconds, c.presence.away_seconds) == (20.0, 30.0)


@pytest.mark.parametrize(
    ("toml", "problem"),
    [
        ('start = "22:00"', "quiet_hours: needs both start and end"),
        ('end = "08:00"', "quiet_hours: needs both start and end"),
        ('start = "22:00"\nend = "22:00"', "quiet_hours: start and end are the same"),
        ('start = "10pm"\nend = "08:00"', "quiet_hours.start: '10pm' isn't a time"),
        ('start = "22:00"\nend = "24:00"', "quiet_hours.end: '24:00' isn't a time"),
        ('start = "22:00"\nend = "8:0"', "quiet_hours.end: '8:0' isn't a time"),
    ],
)
def test_bad_quiet_hours_are_off(tmp_path: Path, toml: str, problem: str) -> None:
    loaded = load(write(tmp_path, f"[quiet_hours]\n{toml}\n"))
    assert loaded.config.quiet_hours is None
    assert loaded.problems[0].startswith(problem)


def test_syntax_error_means_all_defaults(tmp_path: Path) -> None:
    toml = '[presence]\nidle_seconds = 45\n[events.finished\nspeak = "always"'
    loaded = load(write(tmp_path, toml))
    assert loaded.config == Config()
    assert len(loaded.problems) == 1
    assert "can't read it, using all defaults" in loaded.problems[0]


def test_unreadable_bytes_mean_all_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff\xfe[presence]")
    loaded = load(path)
    assert loaded.config == Config()
    assert "can't read it" in loaded.problems[0]


def test_reloads_on_every_call(tmp_path: Path) -> None:
    path = write(tmp_path, "[presence]\nidle_seconds = 45\n")
    assert load(path).config.presence.idle_seconds == 45.0
    path.write_text("[presence]\nidle_seconds = 90\n")
    assert load(path).config.presence.idle_seconds == 90.0


def test_problems_are_logged_once_until_they_change(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "_logged", ())
    path = write(tmp_path, "volume = 3\n")
    with caplog.at_level(logging.WARNING, logger="cc_notify_tts.config"):
        load(path)
        load(path)
        path.write_text("pitch = 3\n")
        load(path)
        # Deleted, then restored with the same problem: logged again.
        path.unlink()
        load(path)
        path.write_text("pitch = 3\n")
        load(path)
    assert [r.getMessage() for r in caplog.records] == [
        "config: volume: unknown key, ignored",
        "config: pitch: unknown key, ignored",
        "config: pitch: unknown key, ignored",
    ]


def test_default_path() -> None:
    path = config.default_config_path()
    assert path.parts[-3:] == ("Application Support", "cc-notify-tts", "config.toml")


def test_spec_example_loads_cleanly_and_names_every_key(tmp_path: Path) -> None:
    text = SPEC.read_text()
    example = text.split("```toml\n", 1)[1].split("```", 1)[0]
    loaded = load(write(tmp_path, example))
    assert loaded.problems == ()
    for kind in EventKind:
        assert f"[events.{kind}]" in example
    for key in (
        "[quiet_hours]",
        "idle_seconds",
        "away_seconds",
        "idle_timeout_minutes",
        "ollama",
        "model",
        "timeout_seconds",
        "voice",
    ):
        assert key in example
