"""The real daemon with fake speech and presence, for the end-to-end test.

Run as `python fake_speech_daemon.py <folder>`, with `HOME` at a throwaway
folder so the daemon's socket, state, config and log live there. Everything is
the real daemon except three things, all read from or written to `<folder>`:

- **Speech:** each sentence is appended to `spoken.jsonl` instead of played.
- **Presence:** `presence.json` holds `{"idle_seconds": <number>, "front": <bundle
  id or null>}`, read for every notification. Without the file, idle time and
  the app in front can't be read, which the gate counts as away.
- **Wording:** templates only; Ollama is never asked.
"""

import json
import logging
import sys
import time
from pathlib import Path

from cc_notify_tts.daemon import Daemon, Paths, default_log_path
from cc_notify_tts.presence import FrontTracker
from cc_notify_tts.timeline import Timeline


class RecordingSpeaker:
    def __init__(self, record: Path) -> None:
        self.record = record

    def speak(self, text: str, voice: str, timeline: Timeline) -> bool:
        with self.record.open("a") as f:
            f.write(json.dumps({"text": text, "voice": voice, "at": time.time()}) + "\n")
        return True


def main() -> int:
    folder = Path(sys.argv[1])
    log_path = default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        filename=log_path,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    front = FrontTracker(time.monotonic())

    def read_idle() -> float | None:
        # Called once per notification, just before the gate reads `front`.
        try:
            presence = json.loads((folder / "presence.json").read_text())
        except (OSError, ValueError):
            front.set_following(False)
            return None
        app = presence.get("front")
        front.set_following(app is not None)
        if app is not None:
            front.set_front(app, time.monotonic())
        return float(presence["idle_seconds"])

    speaker = RecordingSpeaker(folder / "spoken.jsonl")
    daemon = Daemon(
        Paths.default(), lambda: speaker, read_idle=read_idle, follow_front=False, front=front
    )
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
