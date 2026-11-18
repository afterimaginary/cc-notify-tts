"""The CLI's commands: output and exit codes."""

from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts import __version__, cli, client, doctor, host_app, status
from cc_notify_tts.events import EventKind
from cc_notify_tts.mute import MuteFile


def test_version_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_no_arguments_exits_with_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert "usage: cc-notify-tts" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv", [["shout"], ["fire"], ["fire", "lunch"], ["mute", "--for"], ["status", "extra"]]
)
def test_bad_arguments_exit_2(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(argv) == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(("command", "module"), [("status", status), ("doctor", doctor)])
def test_commands_return_their_exit_code(
    monkeypatch: pytest.MonkeyPatch, command: str, module: Any
) -> None:
    monkeypatch.setattr(module, "run", lambda: 1)
    assert cli.main([command]) == 1


# --- mute -------------------------------------------------------------------------


@pytest.fixture
def mute_file(tmp_path: Path) -> MuteFile:
    return MuteFile(tmp_path / "state" / "mute", clock=lambda: 1_000_000.0)


def test_mute_until_unmute(mute_file: MuteFile, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.mute(None, mute_file) == 0
    assert capsys.readouterr().out == "Muted until `cc-notify-tts unmute`.\n"
    assert mute_file.is_muted()


def test_mute_for_a_while(mute_file: MuteFile, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.mute("30m", mute_file) == 0
    assert capsys.readouterr().out.startswith("Muted until ")
    current = mute_file.current()
    assert current is not None
    assert current.until == 1_000_000.0 + 1800


def test_mute_with_a_bad_duration(mute_file: MuteFile, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.mute("soon", mute_file) == 2
    assert "isn't a duration like 30m" in capsys.readouterr().err
    assert not mute_file.is_muted()


def test_unmute(mute_file: MuteFile, capsys: pytest.CaptureFixture[str]) -> None:
    cli.mute(None, mute_file)
    capsys.readouterr()
    assert cli.unmute(mute_file) == 0
    assert capsys.readouterr().out == "Unmuted.\n"
    assert cli.unmute(mute_file) == 0
    assert capsys.readouterr().out == "Wasn't muted.\n"


# --- fire -------------------------------------------------------------------------


class FakeDaemon:
    def __init__(self, reply: dict[str, Any], *, answers: bool = True, started: bool = False):
        self.reply = reply
        self.answers = answers
        self.started = started
        self.sent: list[dict[str, Any]] = []

    def deliver(
        self, message: dict[str, Any], path: Path, python: Path
    ) -> tuple[dict[str, Any] | None, bool]:
        return ({"ok": True} if self.answers else None), self.started

    def request(self, path: Path, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.sent.append(message)
        return self.reply


def fire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeDaemon, always: bool = False
) -> int:
    monkeypatch.setattr(client, "deliver", fake.deliver)
    monkeypatch.setattr(client, "request", fake.request)
    monkeypatch.setattr(host_app, "identify", lambda *_: "com.apple.Terminal")  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    monkeypatch.chdir(tmp_path)
    return cli.fire(EventKind.PERMISSION, always=always, data=tmp_path)


def spoken(text: str = "App needs your okay to run npm test.", speech: str = "ready") -> FakeDaemon:
    reply = {"ok": True, "speak": True, "reason": "speaking: x", "text": text, "speech": speech}
    return FakeDaemon(reply)


def test_fire_sends_the_event_with_this_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = spoken()
    assert fire(monkeypatch, tmp_path, fake, always=True) == 0
    assert fake.sent == [
        {
            "type": "fire",
            "event": "permission",
            "always": True,
            "cwd": str(tmp_path.resolve()),
            "host_app": "com.apple.Terminal",
        }
    ]
    assert capsys.readouterr().out == 'Speaking: "App needs your okay to run npm test."\n'


def test_fire_says_when_it_started_the_daemon_and_speech_is_loading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = spoken(speech="loading")
    fake.started = True
    assert fire(monkeypatch, tmp_path, fake) == 0
    out = capsys.readouterr().out
    assert out.startswith("Started the daemon.\n")
    assert "still loading" in out


def test_fire_kept_silent_by_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reply = {"ok": True, "speak": False, "reason": "silent: muted", "text": None, "speech": "ready"}
    assert fire(monkeypatch, tmp_path, FakeDaemon(reply)) == 0
    assert capsys.readouterr().out == (
        "Not spoken. Muted.\nTo hear it anyway: cc-notify-tts fire permission --always\n"
    )


def test_fire_without_speech_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert fire(monkeypatch, tmp_path, spoken(speech="unavailable")) == 1
    assert "speech is unavailable" in capsys.readouterr().err


def test_fire_when_the_daemon_never_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert fire(monkeypatch, tmp_path, FakeDaemon({}, answers=False)) == 1
    assert "cc-notify-tts doctor" in capsys.readouterr().err


def test_fire_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reply = {"ok": False, "error": "unknown type: 'fire'"}
    assert fire(monkeypatch, tmp_path, FakeDaemon(reply)) == 1
    assert "unknown type: 'fire'" in capsys.readouterr().err
