"""`cc-notify-tts status`: what it reads and what it prints."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts import __version__, client, status
from cc_notify_tts.config import load
from cc_notify_tts.mute import Muted, MuteFile
from cc_notify_tts.sessions import SessionState, SessionStore
from cc_notify_tts.status import Facts, Session, gather, render

NOON = datetime(2026, 11, 18, 12, 0, 0)
TERMINAL = "com.apple.Terminal"


def facts(tmp_path: Path, **changes: Any) -> Facts:
    path = tmp_path / "config.toml"
    base: dict[str, Any] = {
        "now": NOON,
        "daemon": None,
        "mute": None,
        "config_path": path,
        "config_exists": path.exists(),
        "loaded": load(path),
        "idle_seconds": 3.0,
        "front_app": TERMINAL,
        "sessions": [],
        "here": TERMINAL,
    }
    return Facts(**{**base, **changes})


def daemon_reply(**changes: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "pid": 4242,
        "version": __version__,
        "speech": "ready",
        "speaking": None,
        "front_app": TERMINAL,
        "sessions": [],
        "recent": [],
        **changes,
    }


def test_active_in_the_session_is_silent_and_says_why(tmp_path: Path) -> None:
    out = render(
        facts(
            tmp_path,
            daemon=daemon_reply(),
            sessions=[Session("weather app", TERMINAL, 0.0)],
        )
    )
    assert out.splitlines() == [
        f"Daemon    running, pid 4242, version {__version__}, speech ready",
        "Mute      off",
        "Config    "
        + str(tmp_path / "config.toml")
        + " (no file, so every setting is its default)",
        "Presence  last input 3 s ago (away after 20 s); Terminal in front",
        "",
        "Would it speak now? Sessions in Terminal: weather app",
        "  every event      silent: you were active 3 s ago and Terminal is in front",
    ]


def test_each_event_gets_its_own_reason_when_they_differ(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('[events.finished]\nspeak = "always"\n')
    out = render(facts(tmp_path, sessions=[Session("app", TERMINAL, 0.0)]))
    assert "  finished         speaking: finished is set to always" in out
    assert "  failed           silent: you were active 3 s ago and Terminal is in front" in out


def test_sessions_are_grouped_by_host_app(tmp_path: Path) -> None:
    out = render(
        facts(
            tmp_path,
            sessions=[
                Session("web", TERMINAL, 0.0),
                Session("api", TERMINAL, 0.0),
                Session("docs", "com.microsoft.VSCode", 40.0),
            ],
        )
    )
    assert "Would it speak now? Sessions in Terminal: web, api" in out
    assert "Would it speak now? Sessions in VS Code: docs" in out
    assert "speaking: VS Code has been out of front for 40 s" in out


def test_with_no_session_this_terminal_stands_in(tmp_path: Path) -> None:
    out = render(facts(tmp_path))
    assert "Would it speak now? (no session is open; as if one were in Terminal)" in out
    assert "silent: you were active 3 s ago and Terminal is in front" in out


def test_with_no_session_and_this_terminal_behind_another_app(tmp_path: Path) -> None:
    out = render(facts(tmp_path, front_app="com.google.Chrome"))
    assert "Terminal is in front" not in out
    assert "silent: you were active 3 s ago and Terminal has been out of front for only 0 s" in out


def test_with_no_session_and_no_known_app(tmp_path: Path) -> None:
    out = render(facts(tmp_path, here=None))
    assert "Would it speak now? (no session is open)" in out
    assert "speaking: can't tell which app the session runs in" in out


def test_mute_lines(tmp_path: Path) -> None:
    until = NOON.timestamp() + 1800
    timed = render(facts(tmp_path, mute=Muted(until)))
    assert "Mute      on, until 12:30 (30 min left)" in timed
    assert "every event      silent: muted" in timed
    forever = render(facts(tmp_path, mute=Muted(None)))
    assert "Mute      on, until `cc-notify-tts unmute`" in forever


def test_config_problems_point_to_doctor(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("[presence]\nidle_seconds = -1\n")
    out = render(facts(tmp_path))
    assert "(1 problem; `cc-notify-tts doctor` lists them)" in out


def test_unreadable_presence_is_said(tmp_path: Path) -> None:
    out = render(facts(tmp_path, idle_seconds=None, front_app=None))
    assert "Presence  idle time can't be read; the app in front can't be read" in out
    assert "speaking: can't read keyboard and mouse idle time" in out


def test_daemon_details_and_recent_decisions(tmp_path: Path) -> None:
    recent = [
        {"at": NOON.timestamp(), "project": "app", "event": "finished", "reason": f"silent: {n}"}
        for n in range(8)
    ]
    reply = daemon_reply(version="0.0.1", speaking="Finished up in app.", recent=recent)
    out = render(facts(tmp_path, daemon=reply))
    assert (
        f"running, pid 4242, version 0.0.1 (this command is {__version__}), speech ready, "
        'saying "Finished up in app."'
    ) in out
    lines = out.split("Recent decisions\n")[1].splitlines()
    assert lines == [f"  12:00:00  app, finished: silent: {n}" for n in range(3, 8)]


def test_daemon_not_running(tmp_path: Path) -> None:
    assert "Daemon    not running; the next event starts it" in render(facts(tmp_path))


# --- gather -----------------------------------------------------------------------


@pytest.fixture
def quiet_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status, "read_idle_seconds", lambda: 5.0)
    monkeypatch.setattr(status, "read_front_app", lambda: TERMINAL)


@pytest.mark.usefixtures("quiet_presence")
def test_gather_without_a_daemon_reads_sessions_from_disk(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    store.save(SessionState("s1", cwd="/src/weather-app", host_app=TERMINAL, last_event_at=9e9))
    store.save(SessionState("s2", open=False, cwd="/src/old", last_event_at=9e9))
    store.save(
        SessionState("s3", cwd="/src/docs", host_app="com.microsoft.VSCode", last_event_at=9e9)
    )
    MuteFile(tmp_path / "mute").mute(None)
    got = gather(
        tmp_path / "none.sock",
        tmp_path / "config.toml",
        store,
        MuteFile(tmp_path / "mute"),
        here="",
    )
    assert got.daemon is None
    # VS Code isn't in front, so it left front a moment ago, as a new daemon would count it.
    assert sorted(got.sessions, key=lambda s: s.project) == [
        Session("docs", "com.microsoft.VSCode", 0.1),
        Session("weather app", TERMINAL, 0.0),
    ]
    assert got.mute == Muted(None)
    assert got.idle_seconds == 5.0
    assert got.front_app == TERMINAL


@pytest.mark.usefixtures("quiet_presence")
def test_gather_takes_sessions_from_a_running_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = daemon_reply(
        front_app="com.google.Chrome",
        sessions=[
            {"project": "web", "host_app": TERMINAL, "out_of_front_seconds": 12.5},
            {"project": "api", "host_app": None, "out_of_front_seconds": None},
        ],
    )
    monkeypatch.setattr(client, "request", lambda *_: reply)  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    got = gather(
        tmp_path / "d.sock",
        tmp_path / "config.toml",
        SessionStore(tmp_path / "state"),
        MuteFile(tmp_path / "mute"),
        here="",
    )
    assert got.daemon == reply
    assert got.front_app == "com.google.Chrome"
    assert got.sessions == [Session("web", TERMINAL, 12.5), Session("api", None, None)]
