"""A stand-in daemon for the shim's tests: answers the protocol and records what it got.

Run as `python fake_daemon.py <socket> <record file>`. It writes one JSON line
when it starts (its pid, parent and session) and one per request, answers every
request with `{"ok": true}`, and exits after 3 s without a request. Standard
library only, so the tests can start it with any interpreter.
"""

import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

IDLE_EXIT_SECONDS = 3.0


def main() -> None:
    socket_path, record_path = Path(sys.argv[1]), Path(sys.argv[2])
    last = [time.monotonic()]
    lock = threading.Lock()

    def record(entry: dict[str, object]) -> None:
        with lock, record_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            line = self.rfile.readline()
            last[0] = time.monotonic()
            record({"request": json.loads(line), "at": time.time()})
            self.wfile.write(b'{"ok": true}\n')

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = socketserver.ThreadingUnixStreamServer(str(socket_path), Handler)
    record({"started": os.getpid(), "sid": os.getsid(0), "argv": sys.argv})
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    while time.monotonic() - last[0] < IDLE_EXIT_SECONDS:
        time.sleep(0.1)
    server.shutdown()
    socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
