import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts import models, setup

UV_PYTHON = "/uv/python/cpython-3.13-macos-aarch64-none/bin/python3.13"
TOOL_BIN = "/home/.local/bin"


class FakeCommands:
    """Stands in for subprocess.run: answers uv and claude, records every call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: dict[tuple[str, ...], tuple[int, str]] = {
            ("uv", "python", "find", "--managed-python", "--system", "3.13"): (0, UV_PYTHON),
            ("uv", "tool", "dir", "--bin"): (0, TOOL_BIN),
            ("uv", "tool", "update-shell"): (0, "Updated shell profile: ~/.zshenv"),
        }

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        key = tuple(Path(cmd[0]).name if i == 0 else c for i, c in enumerate(cmd))
        code, out = self.answers.get(key, (0, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")

    def ran(self, *prefix: str) -> bool:
        return any(tuple(c[: len(prefix)]) == prefix for c in self.calls)


def _bin_command(name: str) -> str:
    return f"/bin/{name}"


def _no_command(name: str) -> None:
    return None


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> FakeCommands:
    fake = FakeCommands()
    monkeypatch.setattr(setup.subprocess, "run", fake)
    monkeypatch.setattr(setup.shutil, "which", _bin_command)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return fake


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    dests: list[Path] = []

    def fake_fetch(dest: Path, *_: Any, **__: Any) -> None:
        dests.append(dest)

    monkeypatch.setattr(models, "fetch_all", fake_fetch)
    return dests


def test_full_run_links_fetches_installs_and_fixes_path(
    tmp_path: Path, commands: FakeCommands, fetched: list[Path], capsys: Any
) -> None:
    assert setup.run(tmp_path) == 0

    assert (tmp_path / "bin" / "python").readlink() == Path(UV_PYTHON)
    assert (tmp_path / "venv").readlink() == Path(sys.prefix)
    assert fetched == [tmp_path / "models"]
    assert commands.ran(
        "/bin/claude", "plugin", "install", "cc-notify-tts", "--marketplace", setup.MARKETPLACE
    )
    assert commands.ran("uv", "tool", "update-shell")
    out = capsys.readouterr().out
    assert "Changed your shell profile" in out
    assert "~/.zshenv" in out


def test_rerun_replaces_stale_links(
    tmp_path: Path, commands: FakeCommands, fetched: list[Path]
) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "python").symlink_to("/gone/python")
    (tmp_path / "venv").symlink_to("/gone/venv")

    assert setup.run(tmp_path) == 0

    assert (tmp_path / "bin" / "python").readlink() == Path(UV_PYTHON)
    assert (tmp_path / "venv").readlink() == Path(sys.prefix)


def test_without_claude_prints_the_plugin_commands(
    tmp_path: Path,
    commands: FakeCommands,
    fetched: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    monkeypatch.setattr(setup.shutil, "which", _no_command)

    assert setup.run(tmp_path) == 0

    out = capsys.readouterr().out
    assert "/plugin marketplace add afterimaginary/cc-notify-tts" in out
    assert "/plugin install cc-notify-tts@cc-notify-tts" in out
    assert not commands.ran("/bin/claude")


def test_leaves_path_alone_when_tool_folder_is_on_it(
    tmp_path: Path,
    commands: FakeCommands,
    fetched: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", f"/usr/bin:{TOOL_BIN}")

    assert setup.run(tmp_path) == 0

    assert not commands.ran("uv", "tool", "update-shell")


def test_update_shell_already_done_says_open_a_new_terminal(
    tmp_path: Path, commands: FakeCommands, fetched: list[Path], capsys: Any
) -> None:
    commands.answers[("uv", "tool", "update-shell")] = (2, "already up-to-date")

    assert setup.run(tmp_path) == 0

    assert "Open a new terminal" in capsys.readouterr().out


def test_a_failed_step_is_reported_and_the_rest_still_run(
    tmp_path: Path,
    commands: FakeCommands,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    def failing_fetch(*_: Any, **__: Any) -> None:
        raise models.DownloadError("couldn't download voices-v1.0.bin: timed out")

    monkeypatch.setattr(models, "fetch_all", failing_fetch)

    assert setup.run(tmp_path) == 1

    out = capsys.readouterr().out
    assert "couldn't download voices-v1.0.bin: timed out" in out
    assert "Failed: Fetching the speech model" in out
    assert commands.ran("/bin/claude", "plugin", "install")


@pytest.mark.parametrize(
    ("command", "message"),
    [
        (("uv", "python", "find", "--managed-python", "--system", "3.13"), "uv python install"),
        (
            ("claude", "plugin", "install", "cc-notify-tts", "--marketplace", setup.MARKETPLACE),
            "`claude plugin install` exited 1",
        ),
    ],
)
def test_failed_commands_fail_setup_with_a_plain_message(
    tmp_path: Path,
    commands: FakeCommands,
    fetched: list[Path],
    capsys: Any,
    command: tuple[str, ...],
    message: str,
) -> None:
    commands.answers[command] = (1, "")

    assert setup.run(tmp_path) == 1

    assert message in capsys.readouterr().out


def test_without_uv_says_how_to_install_it(
    tmp_path: Path,
    commands: FakeCommands,
    fetched: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    def no_uv(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "uv":
            raise FileNotFoundError("uv")
        return commands(cmd, **kwargs)

    run: Callable[..., subprocess.CompletedProcess[str]] = no_uv
    monkeypatch.setattr(setup.subprocess, "run", run)

    assert setup.run(tmp_path) == 1

    assert "brew install uv" in capsys.readouterr().out
