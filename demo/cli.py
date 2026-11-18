"""`cc-notify-tts` for the demo: the real CLI, reading idle time from the demo's presence file.

`status` reads keyboard and mouse idle time itself rather than asking the
daemon, so without this it would report the real machine's. Everything else is
the real command.
"""

import json
import sys
from pathlib import Path

from cc_notify_tts import cli, status


def demo_idle_seconds() -> float:
    return float(json.loads((Path.home() / "presence.json").read_text())["idle_seconds"])


if __name__ == "__main__":
    status.read_idle_seconds = demo_idle_seconds
    sys.exit(cli.main())
