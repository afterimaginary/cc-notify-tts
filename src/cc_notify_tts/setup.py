"""`cc-notify-tts setup`: everything after `uv tool install` (ADR-0011).

1. Links `<data>/bin/python` to uv's per-minor-version Python 3.13, for the hooks.
2. Links `<data>/venv` to this tool's own environment, for starting the daemon.
3. Downloads and verifies the speech model into `<data>/models/`.
4. Installs the plugin with Claude Code's own command, or prints the in-app
   commands when `claude` isn't on PATH.
5. When the CLI's folder isn't on PATH, adds it with `uv tool update-shell` and
   says which profile changed.

Every step is safe to rerun, and rerunning repairs what an earlier run or a
moved uv folder left behind. A failed step is reported and the rest still run.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from cc_notify_tts import models

PLUGIN = "cc-notify-tts"
MARKETPLACE = "afterimaginary/cc-notify-tts"


class StepError(Exception):
    """A setup step failed. The message is for the user."""


def default_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "cc-notify-tts"


def run(data: Path | None = None) -> int:
    data = default_data_dir() if data is None else data
    steps = [
        ("Linking Python for the hooks", lambda: _link_hook_python(data)),
        ("Linking the daemon's environment", lambda: _link_venv(data)),
        ("Fetching the speech model", lambda: _fetch_models(data / "models")),
        ("Installing the Claude Code plugin", _install_plugin),
        ("Checking PATH", _ensure_on_path),
    ]
    failed: list[str] = []
    for title, step in steps:
        print(f"\n{title}")
        try:
            step()
        except StepError as e:
            print(f"  Failed: {e}")
            failed.append(title)
    if failed:
        print(f"\nSetup didn't finish. Failed: {'; '.join(failed)}.")
        print("Fix the problems above and run `cc-notify-tts setup` again.")
        return 1
    print("\nSetup finished.")
    return 0


def _link_hook_python(data: Path) -> None:
    # uv keeps cpython-3.13-<platform> pointing at its newest 3.13 patch, so a
    # link to it survives `uv python upgrade`. --system skips the venv of a
    # project setup happens to run in, or an active one.
    found = _uv("python", "find", "--managed-python", "--system", "3.13")
    if not found:
        raise StepError(
            "uv has no Python 3.13 of its own. Run "
            "`uv python install 3.13`, then `cc-notify-tts setup` again."
        )
    (data / "bin").mkdir(parents=True, exist_ok=True)
    _relink(data / "bin" / "python", Path(found))


def _link_venv(data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    _relink(data / "venv", Path(sys.prefix))


def _relink(link: Path, target: Path) -> None:
    try:
        link.unlink(missing_ok=True)
        link.symlink_to(target)
    except OSError as e:
        raise StepError(f"couldn't link {link} to {target}: {e}") from e
    print(f"  {link} -> {target}")


def _fetch_models(dest: Path) -> None:
    try:
        models.fetch_all(dest, say=lambda line: print(f"  {line}"), progress=_progress)
    except models.DownloadError as e:
        print()  # end a progress line the failure cut short
        raise StepError(f"{e}. Check your connection and run `cc-notify-tts setup` again.") from e
    except OSError as e:
        raise StepError(f"couldn't write to {dest}: {e}") from e


def _progress(done: int, total: int) -> None:
    end = "\n" if done >= total else ""
    print(f"\r  {done / 1e6:.0f} of {total / 1e6:.0f} MB", end=end, flush=True)


def _install_plugin() -> None:
    claude = shutil.which("claude")
    if claude is None:
        print("  `claude` isn't on PATH. In Claude Code, run these two commands:")
        print(f"    /plugin marketplace add {MARKETPLACE}")
        print(f"    /plugin install {PLUGIN}@{PLUGIN}")
        return
    # Adds the marketplace if needed and installs from it, in one command.
    done = subprocess.run(
        [claude, "plugin", "install", PLUGIN, "--marketplace", MARKETPLACE],
        capture_output=True,
        text=True,
        check=False,
    )
    output = _indent(done.stdout + done.stderr)
    if done.returncode != 0:
        raise StepError(f"`claude plugin install` exited {done.returncode}:\n{output}")
    if output:
        print(output)


def _ensure_on_path() -> None:
    tool_bin = _uv("tool", "dir", "--bin")
    if not tool_bin:
        raise StepError("couldn't ask uv where it puts tool commands (`uv tool dir --bin`).")
    if tool_bin in os.environ.get("PATH", "").split(os.pathsep):
        print(f"  {tool_bin} is on PATH.")
        return
    # uv's own way of adding its folder to the shell profile. It picks the
    # profile from $SHELL and says which file it changed.
    done = subprocess.run(["uv", "tool", "update-shell"], capture_output=True, text=True)
    output = _indent(done.stdout + done.stderr)
    if "already up-to-date" in output:
        # A rerun in the same terminal: the profile already has it.
        print(f"  Your shell profile already adds {tool_bin} to PATH.")
        print("  Open a new terminal to run `cc-notify-tts` by name.")
    elif done.returncode == 0:
        print(f"  Changed your shell profile to add {tool_bin} to PATH (uv tool update-shell):")
        print(output)
        print("  Open a new terminal to run `cc-notify-tts` by name.")
    else:
        raise StepError(
            f"{tool_bin} isn't on PATH and `uv tool update-shell` failed:\n{output}\n"
            f"  Add {tool_bin} to PATH in your shell profile yourself."
        )


def _uv(*args: str) -> str:
    """Run uv and return its trimmed stdout, or "" when it fails."""
    try:
        done = subprocess.run(["uv", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise StepError("`uv` isn't on PATH. Install it with `brew install uv`.") from e
    return done.stdout.strip() if done.returncode == 0 else ""


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.strip().splitlines())
