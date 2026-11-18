"""Naming the app a session runs in, from inside a hook (ADR-0006)."""

import os
import plistlib
from pathlib import Path

import pytest

from cc_notify_tts import host_app


def _none(_arg: object) -> None:
    return None


def _vscode(_pid: int) -> str:
    return "com.microsoft.VSCode"


def _client_of_pane_3(pane: str) -> int | None:
    return 777 if pane == "%3" else None


@pytest.fixture
def no_ancestry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_app, "from_ancestry", _none)


@pytest.mark.usefixtures("no_ancestry")
@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {"__CFBundleIdentifier": "com.mitchellh.ghostty", "TERM_PROGRAM": "vscode"},
            "com.mitchellh.ghostty",
        ),
        ({"TERM_PROGRAM": "Apple_Terminal"}, "com.apple.Terminal"),
        ({"TERM_PROGRAM": "iTerm.app"}, "com.googlecode.iterm2"),
        ({"TERM_PROGRAM": "something else"}, None),
        ({"CLAUDE_CODE_DESKTOP_APP_VERSION": "1.0"}, host_app.CLAUDE_DESKTOP),
        ({}, None),
    ],
)
def test_environment_fallbacks_in_order(env: dict[str, str], expected: str | None) -> None:
    assert host_app.identify(env, 1234) == expected


def test_ancestry_comes_before_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_app, "from_ancestry", _vscode)
    assert (
        host_app.identify({"__CFBundleIdentifier": "com.apple.Terminal"}, 1234)
        == "com.microsoft.VSCode"
    )


def test_tmux_trusts_only_the_attached_client(monkeypatch: pytest.MonkeyPatch) -> None:
    looked_up: list[int] = []

    def from_ancestry(pid: int) -> str:
        looked_up.append(pid)
        return "com.mitchellh.ghostty"

    monkeypatch.setattr(host_app, "from_ancestry", from_ancestry)
    env = {"TMUX": "/tmp/tmux-501/default,1,0", "TMUX_PANE": "%3", "__CFBundleIdentifier": "x"}

    monkeypatch.setattr(host_app, "_tmux_client_pid", _client_of_pane_3)
    assert host_app.identify(env, 1234) == "com.mitchellh.ghostty"
    assert looked_up == [777]

    # Detached: no client, so unknown, not whichever app started the server.
    monkeypatch.setattr(host_app, "_tmux_client_pid", _none)
    assert host_app.identify(env, 1234) is None


@pytest.mark.parametrize(
    ("path", "bundle"),
    [
        (
            "/Applications/Visual Studio Code.app/Contents/Frameworks/"
            "Code Helper.app/Contents/MacOS/Code Helper",
            "/Applications/Visual Studio Code.app",
        ),
        ("/Applications/Ghostty.app/Contents/MacOS/ghostty", "/Applications/Ghostty.app"),
        ("/bin/zsh", None),
    ],
)
def test_outer_bundle(path: str, bundle: str | None) -> None:
    assert host_app.outer_bundle(path) == (None if bundle is None else Path(bundle))


def test_bundle_id_reads_info_plist(tmp_path: Path) -> None:
    app = tmp_path / "Thing.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.example.thing"})
    )
    assert host_app.bundle_id(app) == "com.example.thing"
    assert host_app.bundle_id(tmp_path / "Missing.app") is None
    (app / "Contents" / "Info.plist").write_bytes(b"garbage")
    assert host_app.bundle_id(app) is None


def test_walks_this_process_without_error() -> None:
    # Whatever runs the tests may or may not be inside an app.
    found = host_app.from_ancestry(os.getpid())
    assert found is None or found.count(".") >= 1
    assert host_app.from_ancestry(999_999_999) is None
