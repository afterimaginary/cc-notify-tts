"""The Kokoro model files: which ones, where they live, and fetching them.

`setup` downloads them from a pinned release and checks each against its size
and SHA-256 (ADR-0005), so a truncated or swapped file is never loaded. Which
model variant ships is ADR-0012.
"""

import hashlib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# The URL names the release tag: a later release ships different files under
# the same names.
RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str


MODEL = ModelFile(
    "kokoro-v1.0.int8.onnx",
    92_361_271,
    "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
)
VOICES = ModelFile(
    "voices-v1.0.bin",
    28_214_398,
    "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
)
FILES = (MODEL, VOICES)

_CHUNK = 1 << 20


class DownloadError(Exception):
    """A model file couldn't be fetched or didn't match. The message is for the user."""


def default_models_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "cc-notify-tts" / "models"


def is_present(path: Path, file: ModelFile) -> bool:
    """True when `path` holds exactly `file`. Hashes it, so it costs a read."""
    try:
        if path.stat().st_size != file.size:
            return False
        return _sha256(path) == file.sha256
    except OSError:
        return False


def fetch_all(
    dest: Path,
    base_url: str = RELEASE_URL,
    say: Callable[[str], None] = print,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Make sure every model file is in `dest`, downloading what's missing or wrong.

    Safe to rerun: a file that already matches is left alone. A download goes to
    a `.part` file and is renamed only once it matches, so a failed or
    interrupted run never leaves a bad file under the real name.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for file in FILES:
        path = dest / file.name
        if is_present(path, file):
            say(f"{file.name}: already downloaded and verified.")
            continue
        say(f"{file.name}: downloading {file.size / 1e6:.0f} MB from {base_url}")
        _download(f"{base_url}/{file.name}", path, file, progress)
        say(f"{file.name}: downloaded and verified.")


def _download(
    url: str, path: Path, file: ModelFile, progress: Callable[[int, int], None] | None
) -> None:
    part = path.with_name(path.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(url, timeout=30) as response, part.open("wb") as out:
            while chunk := response.read(_CHUNK):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, file.size)
    except urllib.error.HTTPError as e:
        part.unlink(missing_ok=True)
        raise DownloadError(f"couldn't download {file.name} from {url}: {e}") from e
    except urllib.error.URLError as e:
        # The socket error is wrapped in `reason`.
        part.unlink(missing_ok=True)
        raise DownloadError(f"couldn't download {file.name} from {url}: {e.reason}") from e
    except OSError as e:
        part.unlink(missing_ok=True)
        raise DownloadError(f"couldn't download {file.name} from {url}: {e}") from e
    if done != file.size or digest.hexdigest() != file.sha256:
        part.unlink(missing_ok=True)
        raise DownloadError(
            f"{file.name} from {url} didn't match: got {done:,} bytes with SHA-256 "
            f"{digest.hexdigest()}, expected {file.size:,} bytes with {file.sha256}"
        )
    part.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
