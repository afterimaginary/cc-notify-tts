import hashlib
from pathlib import Path

import pytest

from cc_notify_tts import models
from cc_notify_tts.models import DownloadError, ModelFile, fetch_all, is_present

# Small stand-ins for the real files, served from a local folder by file:// URLs.
CONTENTS = {"model.onnx": b"model bytes " * 1000, "voices.bin": b"voice bytes " * 100}


def _file(name: str, data: bytes) -> ModelFile:
    return ModelFile(name, len(data), hashlib.sha256(data).hexdigest())


@pytest.fixture
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A fake release folder, and FILES pointed at its contents. Returns its URL."""
    folder = tmp_path / "release"
    folder.mkdir()
    for name, data in CONTENTS.items():
        (folder / name).write_bytes(data)
    monkeypatch.setattr(models, "FILES", tuple(_file(n, d) for n, d in CONTENTS.items()))
    return folder.as_uri()


def test_downloads_every_file_and_reports_progress(tmp_path: Path, release: str) -> None:
    dest = tmp_path / "models"
    lines: list[str] = []
    seen: list[tuple[int, int]] = []
    fetch_all(dest, release, say=lines.append, progress=lambda d, t: seen.append((d, t)))

    for name, data in CONTENTS.items():
        assert (dest / name).read_bytes() == data
    assert sorted(p.name for p in dest.iterdir()) == sorted(CONTENTS)
    assert any("downloading" in line for line in lines)
    assert seen[-1] == (len(CONTENTS["voices.bin"]), len(CONTENTS["voices.bin"]))


def test_rerun_keeps_files_that_already_match(tmp_path: Path, release: str) -> None:
    dest = tmp_path / "models"
    fetch_all(dest, release, say=lambda _: None)
    before = {p.name: p.stat().st_mtime_ns for p in dest.iterdir()}

    lines: list[str] = []
    fetch_all(dest, release, say=lines.append)

    assert {p.name: p.stat().st_mtime_ns for p in dest.iterdir()} == before
    assert all("already downloaded" in line for line in lines)


def test_rerun_replaces_a_corrupt_file(tmp_path: Path, release: str) -> None:
    dest = tmp_path / "models"
    fetch_all(dest, release, say=lambda _: None)
    corrupt = dest / "model.onnx"
    corrupt.write_bytes(b"x" * len(CONTENTS["model.onnx"]))

    fetch_all(dest, release, say=lambda _: None)

    assert corrupt.read_bytes() == CONTENTS["model.onnx"]


def test_checksum_mismatch_fails_and_leaves_nothing(
    tmp_path: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = ModelFile("model.onnx", len(CONTENTS["model.onnx"]), "0" * 64)
    monkeypatch.setattr(models, "FILES", (wrong,))
    dest = tmp_path / "models"

    with pytest.raises(DownloadError, match="didn't match"):
        fetch_all(dest, release, say=lambda _: None)

    assert list(dest.iterdir()) == []


def test_missing_file_fails_plainly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "FILES", (_file("gone.onnx", b"x"),))
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(DownloadError, match=r"couldn't download gone\.onnx"):
        fetch_all(tmp_path / "models", empty.as_uri(), say=lambda _: None)

    assert list((tmp_path / "models").iterdir()) == []


def test_is_present_needs_size_and_hash(tmp_path: Path) -> None:
    file = _file("f", b"abc")
    path = tmp_path / "f"
    assert not is_present(path, file)
    path.write_bytes(b"abd")
    assert not is_present(path, file)
    path.write_bytes(b"abc")
    assert is_present(path, file)
