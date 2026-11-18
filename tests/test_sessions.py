"""Per-session state files: atomic writes, bad input and pruning."""

from pathlib import Path

from cc_notify_tts.sessions import (
    CLOSED_GRACE_SECONDS,
    STALE_OPEN_SECONDS,
    BackgroundItem,
    SessionState,
    SessionStore,
)


def test_round_trip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    state = SessionState(
        session_id="abc",
        cwd="/tmp/project",
        turn_started_at=5.0,
        background=[BackgroundItem(id="t1", type="shell", description="Run tests")],
        last_emitted={"finished": 7.0},
    )
    store.save(state)
    assert SessionStore(tmp_path).load("abc") == state
    # Only the state file remains: the temp file was renamed into place.
    assert [p.name for p in tmp_path.iterdir()] == ["abc.json"]


def test_unsafe_session_id_never_escapes_the_state_dir(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state")
    path = store.path("../../etc/passwd")
    assert path.parent == tmp_path / "state"
    store.save(SessionState(session_id="../../etc/passwd"))
    assert store.load("../../etc/passwd") is not None


def test_corrupt_or_foreign_files_read_as_missing(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "list.json").write_text("[]")
    (tmp_path / "extra.json").write_text('{"session_id": "extra", "unknown_field": 1}')
    assert store.load("bad") is None
    assert store.load("list") is None
    assert store.load("extra") is None
    assert store.all() == []


def test_prune(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    now = 100_000.0
    store.save(SessionState(session_id="open", last_event_at=now - 60))
    store.save(SessionState(session_id="stale", last_event_at=now - STALE_OPEN_SECONDS))
    store.save(SessionState(session_id="just-closed", open=False, closed_at=now - 1))
    store.save(SessionState(session_id="closed", open=False, closed_at=now - CLOSED_GRACE_SECONDS))
    (tmp_path / "junk.json").write_text("nope")
    store.prune(now)
    assert sorted(p.stem for p in tmp_path.glob("*.json")) == ["just-closed", "open"]


def test_prune_without_a_state_dir(tmp_path: Path) -> None:
    SessionStore(tmp_path / "missing").prune(0.0)
