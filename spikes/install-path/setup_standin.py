"""Stand-in for `cc-notify-tts setup`, for the install-path spike.

Run it with the tool's own interpreter, as the installed `setup` command
would run:

    ~/.local/share/uv/tools/cc-notify-tts/bin/python setup_standin.py \
        --marketplace afterimaginary/cc-notify-tts [--models-from DIR]

It does what `setup` has to do for the install path, and nothing about how
the model is downloaded (that's `setup`'s own issue):

1. Creates the data folder outside Claude Code's plugin cache.
2. Links `bin/python` to uv's per-minor-version Python 3.13, for the hooks.
3. Links `venv` to the tool's own environment, for starting the daemon.
4. Puts the model files in `models/` (copied from `--models-from` here).
5. Installs the plugin through Claude Code's own command, or prints the
   in-app commands when `claude` isn't on PATH.
6. Adds the CLI's folder to PATH with `uv tool update-shell` when it's missing.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path.home() / "Library" / "Application Support" / "cc-notify-tts"
PLUGIN = "cc-notify-tts"


def relink(link: Path, target: Path) -> None:
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    print(f"linked {link} -> {target}")


def minor_version_python() -> Path:
    # uv keeps cpython-3.13-<platform> pointing at the newest 3.13 patch it
    # has installed, so a link to it survives `uv python upgrade`. --system
    # skips the venv of the project `setup` happens to run in, or an active one.
    found = subprocess.run(
        ["uv", "python", "find", "--managed-python", "--system", "3.13"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(found)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--marketplace", required=True, help="owner/repo, git URL or path")
    parser.add_argument("--models-from", type=Path, help="folder holding the model files")
    args = parser.parse_args()

    (DATA / "bin").mkdir(parents=True, exist_ok=True)
    relink(DATA / "bin" / "python", minor_version_python())
    relink(DATA / "venv", Path(sys.prefix))

    if args.models_from:
        models = DATA / "models"
        models.mkdir(exist_ok=True)
        for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
            shutil.copyfile(args.models_from / name, models / name)
            print(f"model file {models / name}")

    claude = shutil.which("claude")
    if claude:
        subprocess.run(
            [claude, "plugin", "install", PLUGIN, "--marketplace", args.marketplace], check=True
        )
    else:
        print("claude isn't on PATH. In Claude Code, run:")
        print(f"  /plugin marketplace add {args.marketplace}")
        print(f"  /plugin install {PLUGIN}@{PLUGIN}")

    tool_bin = Path(os.environ.get("UV_TOOL_BIN_DIR", Path.home() / ".local" / "bin"))
    path = os.environ.get("PATH", "").split(os.pathsep)
    if str(tool_bin) not in path:
        # uv's own way of adding its folder to the shell profile. It picks the
        # profile from $SHELL and says which file it changed.
        done = subprocess.run(["uv", "tool", "update-shell"], capture_output=True, text=True)
        if done.returncode == 0:
            print(f"Changed your shell profile to add {tool_bin} to PATH (uv tool update-shell):")
            print("  " + (done.stdout + done.stderr).strip().replace("\n", "\n  "))
            print("Open a new terminal to use `cc-notify-tts`.")
        else:
            print(f"{tool_bin} isn't on PATH, and `uv tool update-shell` failed:")
            print("  " + (done.stdout + done.stderr).strip().replace("\n", "\n  "))
            print(f"Add {tool_bin} to PATH yourself to use `cc-notify-tts`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
