"""`cc-notify-tts doctor`: each check, with a fix for every problem, and the exit code."""

from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts import __version__, doctor, models
from cc_notify_tts.config import Wording
from cc_notify_tts.doctor import PLUGIN_ID, Check, Level
from cc_notify_tts.ollama import ENDPOINT, Endpoint, OllamaError


def test_macos() -> None:
    assert doctor.check_macos("Darwin", "27.2") == Check(Level.OK, "macOS", "macOS 27.2")
    linux = doctor.check_macos("Linux", "")
    assert linux.level is Level.FAIL
    assert linux.fix == "cc-notify-tts runs only on macOS."


def test_hook_python(tmp_path: Path) -> None:
    missing = doctor.check_hook_python(tmp_path)
    assert missing.level is Level.FAIL
    assert "the hooks do nothing" in missing.detail
    assert missing.fix == "Run `cc-notify-tts setup`."
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "python").symlink_to("/bin/sh")
    assert doctor.check_hook_python(tmp_path).level is Level.OK


def plugin(tmp_path: Path, **changes: Any) -> dict[str, Any]:
    (tmp_path / "hooks").mkdir(exist_ok=True)
    (tmp_path / "hooks" / "hooks.json").write_text("{}")
    entry = {"id": PLUGIN_ID, "enabled": True, "version": "abc123", "installPath": str(tmp_path)}
    return {**entry, **changes}


def test_plugin_installed_and_enabled(tmp_path: Path) -> None:
    other = {"id": "other@market", "enabled": True}
    check = doctor.check_plugin(lambda: [other, plugin(tmp_path)])
    assert check == Check(Level.OK, "Plugin", "installed and enabled, version abc123")


def test_plugin_missing(tmp_path: Path) -> None:
    check = doctor.check_plugin(lambda: [{"id": "other@market", "enabled": True}])
    assert (check.level, check.detail) == (Level.FAIL, "not installed in Claude Code")
    assert check.fix == "Run `cc-notify-tts setup`."


def test_plugin_disabled(tmp_path: Path) -> None:
    check = doctor.check_plugin(lambda: [plugin(tmp_path, enabled=False)])
    assert check.level is Level.FAIL
    assert check.fix == f"Run `claude plugin enable {PLUGIN_ID}`."


def test_plugin_without_its_hooks_file(tmp_path: Path) -> None:
    entry = plugin(tmp_path)
    (tmp_path / "hooks" / "hooks.json").unlink()
    check = doctor.check_plugin(lambda: [entry])
    assert check.level is Level.FAIL
    assert "hooks file is missing" in check.detail


def test_plugin_cant_be_checked_without_claude() -> None:
    check = doctor.check_plugin(lambda: None)
    assert check.level is Level.WARN
    assert "`/plugin`" in (check.fix or "")


def test_plugin_listing_that_fails_is_a_warning() -> None:
    def broken() -> list[dict[str, Any]]:
        raise OSError("`claude plugin list` exited 1")

    assert doctor.check_plugin(broken).level is Level.WARN


def test_models(tmp_path: Path) -> None:
    def only_voices(path: Path, file: models.ModelFile) -> bool:
        return file is models.VOICES

    check = doctor.check_models(tmp_path, only_voices)
    assert check.level is Level.FAIL
    assert check.detail.startswith(f"{models.MODEL.name} missing or damaged")
    assert doctor.check_models(tmp_path, lambda _p, _f: True).level is Level.OK


class FakeDaemon:
    """Stands in for client.request and client.deliver."""

    def __init__(
        self, running: bool, reply: dict[str, Any] | None = None, speech: list[str] | None = None
    ) -> None:
        self.running = running
        self.reply = {"ok": True, "pid": 7, "version": __version__, "speech": "ready"}
        self.reply.update(reply or {})
        # Speech states for successive status replies; the last one repeats.
        self.speech = speech or [str(self.reply["speech"])]
        self.started = False

    def request(self, path: Path, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not self.running:
            raise ConnectionRefusedError("nothing listening")
        speech = self.speech.pop(0) if len(self.speech) > 1 else self.speech[0]
        return {**self.reply, "speech": speech}

    def deliver(
        self, message: dict[str, Any], path: Path, python: Path
    ) -> tuple[dict[str, Any] | None, bool]:
        self.started = self.running = True
        return {"ok": True}, True


def daemon_check(tmp_path: Path, fake: FakeDaemon, python_exists: bool = True) -> Check:
    python = tmp_path / "python"
    if python_exists:
        python.touch()
    return doctor.check_daemon(
        tmp_path / "d.sock",
        python,
        fake.request,
        fake.deliver,
        speech_wait=1.0,
        sleep=lambda _: None,
    )


def test_running_daemon(tmp_path: Path) -> None:
    check = daemon_check(tmp_path, FakeDaemon(running=True))
    assert check == Check(Level.OK, "Daemon", "running, pid 7, speech ready")


def test_stopped_daemon_is_started_and_its_speech_waited_for(tmp_path: Path) -> None:
    fake = FakeDaemon(running=False, speech=["loading", "loading", "ready"])
    check = daemon_check(tmp_path, fake)
    assert fake.started
    assert check == Check(Level.OK, "Daemon", "running, pid 7, started it to check; speech ready")


def test_speech_that_fails_to_load_after_starting_fails(tmp_path: Path) -> None:
    fake = FakeDaemon(running=False, speech=["loading", "unavailable"])
    check = daemon_check(tmp_path, fake)
    assert check.level is Level.FAIL
    assert "the speech model didn't load" in check.detail


def test_speech_still_loading_at_the_deadline_warns(tmp_path: Path) -> None:
    check = daemon_check(tmp_path, FakeDaemon(running=False, speech=["loading"]))
    assert check.level is Level.WARN
    assert "still loading after 1 s" in check.detail


def test_daemon_that_cant_start(tmp_path: Path) -> None:
    check = daemon_check(tmp_path, FakeDaemon(running=False), python_exists=False)
    assert check.level is Level.FAIL
    assert "can't start" in check.detail


def test_daemon_without_speech(tmp_path: Path) -> None:
    check = daemon_check(tmp_path, FakeDaemon(running=True, reply={"speech": "unavailable"}))
    assert check.level is Level.FAIL
    assert "daemon.log" in (check.fix or "")


def test_daemon_from_another_version(tmp_path: Path) -> None:
    check = daemon_check(tmp_path, FakeDaemon(running=True, reply={"version": "0.0.1"}))
    assert check.level is Level.WARN
    assert "`kill 7`" in (check.fix or "")


def test_presence_readings() -> None:
    assert doctor.check_idle(lambda: 4.0).level is Level.OK
    assert doctor.check_idle(lambda: None).level is Level.FAIL
    assert doctor.check_front(lambda: "com.apple.Terminal").level is Level.OK
    assert doctor.check_front(lambda: None).level is Level.FAIL


def test_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    assert doctor.check_config(path)[0].level is Level.OK
    path.write_text("[presence]\nidle_seconds = 5\n")
    assert [c.level for c in doctor.check_config(path)] == [Level.OK]
    path.write_text('[presence]\nidle_seconds = -1\n[wording]\nmodle = "x"\n')
    checks = doctor.check_config(path)
    assert [c.level for c in checks] == [Level.FAIL, Level.FAIL]
    assert "presence.idle_seconds: -1 isn't a number, 0 or more, using 20" in [
        c.detail for c in checks
    ]
    assert all(c.fix for c in checks)


class FakeOllama:
    def __init__(self, models: list[str] | None) -> None:
        self.models = models

    def installed(self, model: str) -> bool:
        if self.models is None:
            raise OllamaError("not running at http://127.0.0.1:11434 (refused)")
        return model in self.models


LOCAL = Endpoint("http://127.0.0.1:11435")
IGNORED = Endpoint(ENDPOINT, ignored="192.168.1.20:11434")


def test_ollama() -> None:
    on = Wording(model="llama3.2:1b")
    running = doctor.check_ollama(on, FakeOllama(["llama3.2:1b"]), LOCAL)
    assert running == Check(
        Level.OK, "Ollama (optional)", "running at http://127.0.0.1:11435, with llama3.2:1b"
    )
    missing = doctor.check_ollama(on, FakeOllama([]), LOCAL)
    assert missing.level is Level.WARN
    assert "running at http://127.0.0.1:11435" in missing.detail
    assert "ollama pull llama3.2:1b" in (missing.fix or "")
    down = doctor.check_ollama(on, FakeOllama(None), LOCAL)
    assert down.level is Level.WARN
    assert "template wording is used" in down.detail
    assert "http://127.0.0.1:11435" in down.detail
    off = doctor.check_ollama(Wording(ollama=False), FakeOllama(None), LOCAL)
    assert off.level is Level.OK


def test_ollama_says_when_ollama_host_is_ignored() -> None:
    on = Wording(model="llama3.2:1b")
    note = "OLLAMA_HOST='192.168.1.20:11434' is ignored, since it isn't on this machine"
    running = doctor.check_ollama(on, FakeOllama(["llama3.2:1b"]), IGNORED)
    assert running.level is Level.WARN
    assert running.detail == f"running at {ENDPOINT}, with llama3.2:1b; {note}"
    assert "Unset OLLAMA_HOST" in (running.fix or "")
    for fake in (FakeOllama([]), FakeOllama(None)):
        check = doctor.check_ollama(on, fake, IGNORED)
        assert check.level is Level.WARN
        assert check.detail.endswith(note)


def test_render_and_exit_code() -> None:
    checks = [
        Check(Level.OK, "macOS", "macOS 27.2"),
        Check(Level.WARN, "Ollama (optional)", "not running", "Start it."),
        Check(Level.FAIL, "Plugin", "not installed", "Run `cc-notify-tts setup`."),
    ]
    assert doctor.render(checks).splitlines() == [
        "ok    macOS: macOS 27.2",
        "warn  Ollama (optional): not running",
        "      Fix: Start it.",
        "FAIL  Plugin: not installed",
        "      Fix: Run `cc-notify-tts setup`.",
        "",
        "1 problem, 1 warning.",
    ]
    assert doctor.exit_code(checks) == 1
    assert doctor.exit_code(checks[:2]) == 0
    assert doctor.render(checks[:1]).endswith("All checks passed.")


@pytest.mark.parametrize(
    ("levels", "code"), [([Level.OK], 0), ([Level.OK, Level.WARN], 0), ([Level.FAIL], 1)]
)
def test_run_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    levels: list[Level],
    code: int,
) -> None:
    checks = [Check(level, "x", "y", None if level is Level.OK else "z") for level in levels]
    monkeypatch.setattr(doctor, "run_checks", lambda: checks)
    assert doctor.run() == code
    assert capsys.readouterr().out.startswith(f"{levels[0]:<4}  x: y")
