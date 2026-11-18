"""Stand-in daemon for the install-path spike.

Proves the process the shim starts runs in the tool's environment: it imports
the package and kokoro-onnx, and finds the model files in the data folder. The
shipped daemon will be `python -m cc_notify_tts.daemon` from the same venv.
"""

import json
import os
import sys
import time
from pathlib import Path

DATA = Path.home() / "Library" / "Application Support" / "cc-notify-tts"

record: dict[str, object] = {
    "at": time.time(),
    "executable": sys.executable,
    "prefix": sys.prefix,
    "parent_pid": os.getppid(),
}
try:
    import kokoro_onnx

    import cc_notify_tts

    record["package"] = cc_notify_tts.__file__
    record["kokoro_onnx"] = kokoro_onnx.__file__
    models = DATA / "models"
    record["models"] = sorted(p.name for p in models.glob("*")) if models.is_dir() else []
except Exception as exc:
    record["error"] = repr(exc)
out = DATA / "spike" / "daemon.jsonl"
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("a") as fh:
    fh.write(json.dumps(record) + "\n")
