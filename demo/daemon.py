"""The real daemon for the demo, with real Kokoro speech written to files instead of played.

Run as `python demo/daemon.py <home> <models folder>`, with `HOME` at the demo's
throwaway home, as the hook shim does when it starts the daemon. Everything is
the real daemon except:

- **Playback:** each sentence is synthesized by Kokoro to `<home>/spoken/NN.wav`
  and logged in `<home>/spoken.jsonl` rather than played, so recording the demo
  is silent and the audio is mixed in afterwards.
- **Presence:** `<home>/presence.json` holds `{"idle_seconds": <number>, "front":
  <bundle id>}`, so the demo decides whether the viewer is away, not the real
  keyboard and screen.
- **Wording:** templates only, so every run says the same sentences.
"""

import json
import logging
import sys
import time
from pathlib import Path

from cc_notify_tts.daemon import Daemon, Paths, default_log_path
from cc_notify_tts.presence import FrontTracker
from cc_notify_tts.speech import KokoroSynthesizer


class SpeechToFiles:
    """Synthesizes each sentence with Kokoro and keeps the WAV instead of playing it."""

    def __init__(self, home: Path, synthesizer: KokoroSynthesizer) -> None:
        self.folder = home / "spoken"
        self.record = home / "spoken.jsonl"
        self.synthesizer = synthesizer

    def speak(self, text: str, voice: str) -> bool:
        self.folder.mkdir(exist_ok=True)
        wav = self.folder / f"{len(list(self.folder.glob('*.wav'))) + 1:02d}.wav"
        self.synthesizer.synthesize(text, voice, wav)
        with self.record.open("a") as f:
            f.write(json.dumps({"text": text, "wav": str(wav), "at": time.time()}) + "\n")
        return True


def main() -> int:
    home, models = Path(sys.argv[1]), Path(sys.argv[2])
    log_path = default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        filename=log_path,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    front = FrontTracker(time.monotonic())

    def read_idle() -> float:
        # Called once per notification, just before the gate reads `front`.
        presence = json.loads((home / "presence.json").read_text())
        front.set_following(True)
        front.set_front(presence["front"], time.monotonic())
        return float(presence["idle_seconds"])

    def speaker() -> SpeechToFiles:
        return SpeechToFiles(home, KokoroSynthesizer(models))

    daemon = Daemon(Paths.default(), speaker, read_idle=read_idle, follow_front=False, front=front)
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
