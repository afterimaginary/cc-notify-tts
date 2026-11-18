"""Whether the user is at the Mac: idle time and how long each app has been out of front.

Both readings need no macOS permission (ADR-0006). Idle time comes from
CoreGraphics through ctypes, read on every event. The frontmost app is followed
by a long-running `lsappinfo listen` child, so nothing is read at event time.
Any reading that fails is None, which the gate treats as a reason to speak.
See docs/designs/when-to-speak.md.
"""

import ctypes
import ctypes.util
import logging
import math
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# --- idle time ------------------------------------------------------------------

_K_CG_EVENT_SOURCE_STATE_HID_SYSTEM = 1
_K_CG_ANY_INPUT_EVENT_TYPE = 0xFFFFFFFF
_seconds_since_input: Callable[[int, int], float] | None = None


def read_idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input anywhere on the Mac, or None."""
    global _seconds_since_input
    try:
        if _seconds_since_input is None:
            path = ctypes.util.find_library("CoreGraphics")
            if path is None:
                return None
            fn = ctypes.CDLL(path).CGEventSourceSecondsSinceLastEventType
            fn.restype = ctypes.c_double
            fn.argtypes = [ctypes.c_int32, ctypes.c_uint32]
            _seconds_since_input = fn
        value = float(
            _seconds_since_input(_K_CG_EVENT_SOURCE_STATE_HID_SYSTEM, _K_CG_ANY_INPUT_EVENT_TYPE)
        )
    except (OSError, AttributeError, ctypes.ArgumentError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


# --- following the frontmost app --------------------------------------------------

# Names for the hosts ADR-0006 identified, for reasons a person reads. Any other
# app is named by its bundle id.
_APP_NAMES = {
    "com.apple.Terminal": "Terminal",
    "com.mitchellh.ghostty": "Ghostty",
    "com.googlecode.iterm2": "iTerm2",
    "com.microsoft.VSCode": "VS Code",
    "com.anthropic.claudefordesktop": "the Claude app",
}

# An app without a bundle id came to front. It's never a session's host.
UNNAMED_APP = ""


def app_name(bundle_id: str) -> str:
    return _APP_NAMES.get(bundle_id, bundle_id or "an unnamed app")


class FrontTracker:
    """Which app is in front, and when each app last lost front.

    Pure state, fed by `FrontmostWatcher`. Times are `time.monotonic()` seconds.
    Thread-safe: the watcher writes from its thread and the gate reads from another.
    """

    def __init__(self, started_at: float) -> None:
        self._started_at = started_at
        self._front: str | None = None
        self._lost_front_at: dict[str, float] = {}
        self._following = False
        self._lock = threading.Lock()

    def set_front(self, bundle_id: str, now: float) -> None:
        with self._lock:
            if self._front is not None and self._front != bundle_id:
                self._lost_front_at[self._front] = now
            self._front = bundle_id

    def set_following(self, following: bool) -> None:
        with self._lock:
            self._following = following

    def front(self) -> str | None:
        """The app in front, or None when the frontmost app can't be followed."""
        with self._lock:
            return self._front if self._following else None

    def out_of_front_seconds(self, host: str, now: float) -> float | None:
        """How long `host` has been out of front: 0 while it's in front, None if unknown."""
        with self._lock:
            if not self._following or self._front is None:
                return None
            if host == self._front:
                return 0.0
            # Not seen in front since tracking started: count from the start.
            return max(0.0, now - self._lost_front_at.get(host, self._started_at))


_BUNDLE_ID = re.compile(r'bundleID="([^"]+)"')
_LISTEN_BUNDLE_ID = re.compile(r'"CFBundleIdentifier"="([^"]*)"')
_BECAME_FRONTMOST = "kLSNotifyBecameFrontmost"


def read_front_app() -> str | None:
    """The frontmost app's bundle id from `lsappinfo`, or None if it can't be read."""
    try:
        asn = subprocess.run(
            ["lsappinfo", "front"], capture_output=True, text=True, timeout=3
        ).stdout.strip()
        if not asn:
            return None
        out = subprocess.run(
            ["lsappinfo", "info", "-only", "bundleid", asn],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = _BUNDLE_ID.search(out)
    return match.group(1) if match else UNNAMED_APP


def parse_listen_line(line: str) -> str | None:
    """The bundle id in one line from `lsappinfo listen`, or None if it isn't a switch."""
    if _BECAME_FRONTMOST not in line:
        return None
    match = _LISTEN_BUNDLE_ID.search(line)
    return match.group(1) if match else UNNAMED_APP


# Each listener runs this long, then exits and is started again.
LISTEN_SECONDS = 3600
# A listener that exits sooner than this failed. After each failure in a row the
# watcher waits twice as long before trying again, up to the cap.
MIN_HEALTHY_RUN_SECONDS = 5.0
MAX_RETRY_SECONDS = 60.0


def _listen_command(seconds: int) -> list[str]:
    return ["lsappinfo", "listen", "+becameFrontmost", "wait", "-duration", str(seconds)]


@dataclass
class FrontmostWatcher:
    """Runs `lsappinfo listen` in a background thread and feeds a `FrontTracker`.

    Before each listener starts, the app in front is read once, so a switch made
    while no listener ran can't leave it stale. While no listener is running, or
    the app in front is unknown, the tracker isn't following.
    """

    tracker: FrontTracker
    read_front: Callable[[], str | None] = read_front_app
    command: list[str] | None = None
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._stop = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="frontmost-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.tracker.set_following(False)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            started = self.clock()
            self._listen_once()
            self.tracker.set_following(False)
            if self._stop.is_set():
                break
            if self.clock() - started >= MIN_HEALTHY_RUN_SECONDS:
                failures = 0
                continue
            failures += 1
            delay = min(MAX_RETRY_SECONDS, 2.0 ** (failures - 1))
            log.warning("presence: frontmost listener exited early, retrying in %g s", delay)
            self._stop.wait(delay)

    def _listen_once(self) -> None:
        front = self.read_front()
        try:
            process = subprocess.Popen(
                self.command or _listen_command(LISTEN_SECONDS),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as e:
            log.warning("presence: can't start the frontmost listener: %s", e)
            return
        self._process = process
        try:
            if self._stop.is_set():
                return
            if front is not None:
                self.tracker.set_front(front, self.clock())
                self.tracker.set_following(True)
            assert process.stdout is not None
            self._follow(process.stdout)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait()
            self._process = None

    def _follow(self, lines: Iterable[str]) -> None:
        for line in lines:
            bundle_id = parse_listen_line(line)
            if bundle_id is not None:
                self.tracker.set_front(bundle_id, self.clock())
                # A first switch makes the front known even if the initial read failed.
                self.tracker.set_following(True)
