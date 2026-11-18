"""Stand-in hook shim for the install-path spike. Standard library only.

Records which interpreter Claude Code ran it with and from where, then starts
the stand-in daemon from the data folder the way the shipped shim will: the
tool's venv by its linked path, detached, without waiting. Never writes to
stdout and always exits 0.
"""

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DATA = Path.home() / "Library" / "Application Support" / "cc-notify-tts"


def main() -> None:
    sys.stdin.read()
    log = DATA / "spike" / "shim.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": time.time(),
        "event": sys.argv[1] if len(sys.argv) > 1 else "",
        "executable": sys.executable,
        "realpath": os.path.realpath(sys.executable),
        "version": sys.version.split()[0],
        "flags": {"isolated": sys.flags.isolated, "no_site": sys.flags.no_site},
        "script": __file__,
        "plugin_root": os.environ.get("CLAUDE_PLUGIN_ROOT", ""),
    }
    with log.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    daemon_python = DATA / "venv" / "bin" / "python"
    daemon = Path(__file__).with_name("daemon_standin.py")
    subprocess.Popen(
        [str(daemon_python), "-I", str(daemon)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


if __name__ == "__main__":
    with contextlib.suppress(Exception):
        main()
    sys.exit(0)
