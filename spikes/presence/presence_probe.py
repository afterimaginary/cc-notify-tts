#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyobjc-framework-Cocoa==13.0"]
# ///
"""Spike: can the daemon tell whether the user is away, and what does each read cost?

Subcommands:

- `idle`: reads keyboard and mouse idle time with `ioreg` (a subprocess) and with
  CoreGraphics through ctypes, and times both.
- `front`: reads the frontmost app with `lsappinfo` (subprocesses) and with
  `NSWorkspace` (PyObjC), and times both. Also times importing AppKit.
- `host`: identifies the app hosting this process from its environment and from
  its process ancestry, the way a hook would. Run it inside each terminal.
- `track`: watches the frontmost app for a while with three methods at once:
  polling `lsappinfo`, polling `NSWorkspace.frontmostApplication` in a process
  that runs a run loop and one that doesn't, and `NSWorkspace` activation
  notifications. Reports what each saw and what tracking cost in CPU time.
- `watch`: activation notifications only, to measure an event-driven tracker
  at rest (CPU time and memory with AppKit loaded).
- `listen`: `lsappinfo listen +becameFrontmost` read through a pipe: what
  arrives, how soon after each switch, and what the listener costs.

Nothing here needs Accessibility, Input Monitoring or Screen Recording permission,
and nothing changes which app is in front: `track`, `watch` and `listen` only watch.

    uv run --script spikes/presence/presence_probe.py idle
    uv run --script spikes/presence/presence_probe.py track --seconds 300

Results are recorded in ADR-0006.
"""

from __future__ import annotations

import argparse
import calendar
import ctypes
import ctypes.util
import json
import os
import plistlib
import re
import resource
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# --- timing helpers -----------------------------------------------------------


def cpu_seconds() -> float:
    """CPU time used by this process and its finished children."""
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime


def time_reads(read: Callable[[], object], runs: int) -> dict[str, Any]:
    """Wall and CPU milliseconds per call, with the last value read."""
    walls: list[float] = []
    value: object = None
    cpu0 = cpu_seconds()
    for _ in range(runs):
        t0 = time.perf_counter()
        value = read()
        walls.append((time.perf_counter() - t0) * 1000)
    cpu_ms = (cpu_seconds() - cpu0) * 1000 / runs
    walls.sort()
    return {
        "runs": runs,
        "p50_ms": round(statistics.median(walls), 4),
        "p95_ms": round(walls[int(0.95 * (runs - 1))], 4),
        "max_ms": round(walls[-1], 4),
        "cpu_ms_per_read": round(cpu_ms, 4),
        "last_value": value,
    }


def rss_mb() -> float:
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True
    )
    return int(out.stdout.strip()) / 1024


# --- idle time ----------------------------------------------------------------

_HID_IDLE = re.compile(r'"HIDIdleTime" = (\d+)')


def idle_ioreg() -> float | None:
    """Seconds since the last keyboard or mouse event, from IOHIDSystem."""
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem", "-w", "0"], capture_output=True, text=True, timeout=3
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = _HID_IDLE.search(out)
    return int(match.group(1)) / 1e9 if match else None


_K_CG_EVENT_SOURCE_STATE_HID_SYSTEM = 1
_K_CG_ANY_INPUT_EVENT_TYPE = 0xFFFFFFFF
_cg: ctypes.CDLL | None = None


def idle_coregraphics() -> float | None:
    """Same reading through CGEventSourceSecondsSinceLastEventType, in-process."""
    global _cg
    if _cg is None:
        path = ctypes.util.find_library("CoreGraphics")
        if path is None:
            return None
        _cg = ctypes.CDLL(path)
        _cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
        _cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]
    return float(
        _cg.CGEventSourceSecondsSinceLastEventType(
            _K_CG_EVENT_SOURCE_STATE_HID_SYSTEM, _K_CG_ANY_INPUT_EVENT_TYPE
        )
    )


def cmd_idle(args: argparse.Namespace) -> dict[str, Any]:
    pairs: list[tuple[float | None, float | None]] = []
    for _ in range(5):
        pairs.append((idle_ioreg(), idle_coregraphics()))
        time.sleep(0.2)
    diffs = [abs(a - b) for a, b in pairs if a is not None and b is not None]
    return {
        "ioreg": time_reads(idle_ioreg, args.runs),
        "coregraphics": time_reads(idle_coregraphics, args.runs),
        "agreement_max_diff_s": round(max(diffs), 4) if diffs else None,
        "samples": [[None if v is None else round(v, 3) for v in p] for p in pairs],
    }


# --- frontmost app ------------------------------------------------------------

_BUNDLE_ID = re.compile(r'bundleID="([^"]+)"')


def front_lsappinfo() -> str | None:
    """Bundle id of the frontmost app: `lsappinfo front`, then `lsappinfo info`."""
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
    return match.group(1) if match else None


def front_nsworkspace() -> str | None:
    from AppKit import NSWorkspace  # type: ignore[import-not-found]

    app = NSWorkspace.sharedWorkspace().frontmostApplication()  # type: ignore[no-untyped-call]
    return None if app is None else str(app.bundleIdentifier())


def cmd_front(args: argparse.Namespace) -> dict[str, Any]:
    before = rss_mb()
    t0 = time.perf_counter()
    import AppKit  # type: ignore[import-not-found]  # noqa: F401

    import_ms = (time.perf_counter() - t0) * 1000
    return {
        "lsappinfo": time_reads(front_lsappinfo, args.runs),
        "nsworkspace": time_reads(front_nsworkspace, args.runs),
        "appkit_import_ms": round(import_ms, 1),
        "appkit_import_rss_mb": round(rss_mb() - before, 1),
    }


# --- host app -----------------------------------------------------------------

# Values the terminals document for TERM_PROGRAM. VS Code forks (Cursor and others)
# also set "vscode", so the bundle id is the better answer when it is there.
TERM_PROGRAM_BUNDLES = {
    "Apple_Terminal": "com.apple.Terminal",
    "iTerm.app": "com.googlecode.iterm2",
    "ghostty": "com.mitchellh.ghostty",
    "vscode": "com.microsoft.VSCode",
}

_libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
# PROC_PIDT_SHORTBSDINFO (struct proc_bsdshortinfo: pid, then ppid). Unlike the full
# PROC_PIDTBSDINFO, it also answers for root-owned `login`, which sits between a
# terminal app and its shell.
_PROC_PIDT_SHORTBSDINFO = 13
_PROC_BSDSHORTINFO_SIZE = 64
_PPID_OFFSET = 4


def parent_pid(pid: int) -> int | None:
    buf = ctypes.create_string_buffer(_PROC_BSDSHORTINFO_SIZE)
    got = _libproc.proc_pidinfo(pid, _PROC_PIDT_SHORTBSDINFO, 0, buf, _PROC_BSDSHORTINFO_SIZE)
    if got != _PROC_BSDSHORTINFO_SIZE:
        return None
    return int.from_bytes(buf.raw[_PPID_OFFSET : _PPID_OFFSET + 4], "little")


def exe_path(pid: int) -> str | None:
    buf = ctypes.create_string_buffer(4096)
    if _libproc.proc_pidpath(pid, buf, 4096) <= 0:
        return None
    return buf.value.decode(errors="replace")


def ancestry(pid: int) -> list[tuple[int, str | None]]:
    chain: list[tuple[int, str | None]] = []
    current: int | None = pid
    while current and current > 1 and len(chain) < 64:
        chain.append((current, exe_path(current)))
        current = parent_pid(current)
    return chain


def outer_bundle(path: str | None) -> Path | None:
    """The outermost .app bundle an executable lives in."""
    if not path or ".app/" not in path:
        return None
    return Path(path[: path.index(".app/") + 4])


def bundle_id(bundle: Path) -> str | None:
    try:
        with (bundle / "Contents/Info.plist").open("rb") as f:
            value = plistlib.load(f).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        return None
    return value if isinstance(value, str) else None


def host_from_ancestry(pid: int) -> tuple[str | None, str | None]:
    """Bundle id of the outermost app among a process's ancestors, and its path."""
    for _, path in reversed(ancestry(pid)):
        bundle = outer_bundle(path)
        if bundle is not None:
            return bundle_id(bundle), str(bundle)
    return None, None


def tmux_client_pid() -> int | None:
    """The pid of the tmux client showing this pane, if any."""
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "#{client_pid}"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return int(out) if out.isdigit() else None


def identify_host(env: dict[str, str], pid: int) -> dict[str, Any]:
    t0 = time.perf_counter()
    by_env = env.get("__CFBundleIdentifier")
    by_term = TERM_PROGRAM_BUNDLES.get(env.get("TERM_PROGRAM", ""))
    env_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    by_ancestry, bundle_path = host_from_ancestry(pid)
    ancestry_ms = (time.perf_counter() - t0) * 1000

    by_tmux: str | None = None
    tmux_ms: float | None = None
    if env.get("TMUX"):
        t0 = time.perf_counter()
        client = tmux_client_pid()
        if client is not None:
            by_tmux, _ = host_from_ancestry(client)
        tmux_ms = (time.perf_counter() - t0) * 1000

    return {
        "by_cfbundleidentifier": by_env,
        "by_term_program": by_term,
        "by_ancestry": by_ancestry,
        "ancestry_bundle_path": bundle_path,
        "by_tmux_client": by_tmux,
        "env_ms": round(env_ms, 3),
        "ancestry_ms": round(ancestry_ms, 3),
        "tmux_ms": None if tmux_ms is None else round(tmux_ms, 3),
    }


HOST_ENV_KEYS = (
    "__CFBundleIdentifier",
    "TERM_PROGRAM",
    "TERM_PROGRAM_VERSION",
    "TMUX",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_DESKTOP_APP_VERSION",
)


def cmd_host(args: argparse.Namespace) -> dict[str, Any]:
    env = dict(os.environ)
    return {
        "label": args.label,
        "env": {k: ("set" if k == "TMUX" else env[k]) for k in HOST_ENV_KEYS if k in env},
        "ancestry": [Path(p).name if p else None for _, p in ancestry(os.getpid())],
        "identified": identify_host(env, os.getpid()),
    }


# --- tracking -----------------------------------------------------------------


def track_without_run_loop(seconds: float, interval: float) -> dict[str, Any]:
    """Poll NSWorkspace's property from a plain loop that never runs a run loop."""
    samples: list[tuple[float, str | None, str | None]] = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        samples.append((time.monotonic(), front_nsworkspace(), front_lsappinfo()))
        time.sleep(interval)
    stale = sum(1 for _, ns, ls in samples if ns != ls)
    return {"samples": len(samples), "disagreements_with_lsappinfo": stale}


def track_with_run_loop(seconds: float, interval: float) -> dict[str, Any]:
    """Notifications plus polling both readers, in a process that runs a run loop."""
    from AppKit import (  # type: ignore[import-not-found]
        NSWorkspace,
        NSWorkspaceApplicationKey,
        NSWorkspaceDidActivateApplicationNotification,
    )
    from Foundation import (  # type: ignore[import-not-found]
        NSDate,
        NSDefaultRunLoopMode,
        NSObject,
        NSRunLoop,
    )

    start = time.monotonic()
    notified: list[tuple[float, str]] = []

    class Observer(NSObject):  # type: ignore[misc]
        def activated_(self, note: Any) -> None:
            app = note.userInfo()[NSWorkspaceApplicationKey]
            notified.append((time.monotonic() - start, str(app.bundleIdentifier())))

    observer = Observer.alloc().init()
    center = NSWorkspace.sharedWorkspace().notificationCenter()
    center.addObserver_selector_name_object_(
        observer, "activated:", NSWorkspaceDidActivateApplicationNotification, None
    )

    polled_ls: list[tuple[float, str | None]] = []
    polled_ns: list[tuple[float, str | None]] = []
    poll_cpu = 0.0
    loop = NSRunLoop.currentRunLoop()
    end = start + seconds
    while time.monotonic() < end:
        loop.runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(interval)
        )
        c0 = cpu_seconds()
        now = time.monotonic() - start
        polled_ls.append((now, front_lsappinfo()))
        polled_ns.append((now, front_nsworkspace()))
        poll_cpu += cpu_seconds() - c0

    def changes(samples: list[tuple[float, str | None]]) -> list[tuple[float, str | None]]:
        out: list[tuple[float, str | None]] = []
        for t, v in samples:
            if not out or out[-1][1] != v:
                out.append((round(t, 2), v))
        return out

    ls_changes = changes(polled_ls)
    ns_changes = changes(polled_ns)
    lags: list[float] = []
    for t, bundle in notified:
        seen = next((pt for pt, v in ls_changes if v == bundle and pt >= t), None)
        if seen is not None:
            lags.append(seen - t)
    return {
        "seconds": seconds,
        "poll_interval_s": interval,
        "notifications": [(round(t, 2), b) for t, b in notified],
        "lsappinfo_changes": ls_changes,
        "nsworkspace_property_changes": ns_changes,
        "nsworkspace_disagrees_with_lsappinfo": sum(
            1 for (_, a), (_, b) in zip(polled_ls, polled_ns, strict=True) if a != b
        ),
        "poll_lag_after_notification_s": [round(x, 2) for x in lags],
        "polls": len(polled_ls),
        "cpu_ms_per_poll_both_readers": round(poll_cpu * 1000 / max(len(polled_ls), 1), 3),
        "cpu_s_total": round(cpu_seconds(), 3),
    }


def cmd_track(args: argparse.Namespace) -> dict[str, Any]:
    child = subprocess.Popen(
        [
            sys.executable,
            __file__,
            "track-noloop",
            "--seconds",
            str(args.seconds),
            "--interval",
            str(args.interval),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    with_loop = track_with_run_loop(args.seconds, args.interval)
    out, _ = child.communicate()
    return {"with_run_loop": with_loop, "without_run_loop": json.loads(out)}


def cmd_track_noloop(args: argparse.Namespace) -> dict[str, Any]:
    return track_without_run_loop(args.seconds, args.interval)


def cmd_watch(args: argparse.Namespace) -> dict[str, Any]:
    """Notifications only, no polling: what an event-driven tracker costs at rest."""
    t0 = time.perf_counter()
    rss0 = rss_mb()
    from AppKit import (  # type: ignore[import-not-found]
        NSWorkspace,
        NSWorkspaceApplicationKey,
        NSWorkspaceDidActivateApplicationNotification,
    )
    from Foundation import NSDate, NSObject, NSRunLoop  # type: ignore[import-not-found]

    notified: list[tuple[float, str]] = []

    class Observer(NSObject):  # type: ignore[misc]
        def activated_(self, note: Any) -> None:
            app = note.userInfo()[NSWorkspaceApplicationKey]
            notified.append((time.monotonic() - start, str(app.bundleIdentifier())))

    observer = Observer.alloc().init()
    NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
        observer, "activated:", NSWorkspaceDidActivateApplicationNotification, None
    )
    setup_ms = (time.perf_counter() - t0) * 1000
    cpu0 = cpu_seconds()
    start = time.monotonic()
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(args.seconds))
    return {
        "seconds": args.seconds,
        "parent_pid": os.getppid(),
        "setup_ms": round(setup_ms, 1),
        "rss_mb_before_appkit": round(rss0, 1),
        "rss_mb_end": round(rss_mb(), 1),
        "cpu_ms_while_watching": round((cpu_seconds() - cpu0) * 1000, 2),
        "notifications": [(round(t, 2), b) for t, b in notified],
    }


def cmd_listen(args: argparse.Namespace) -> dict[str, Any]:
    """`lsappinfo listen` through a pipe: what arrives, how fast, at what cost."""
    proc = subprocess.Popen(
        ["lsappinfo", "listen", "+becameFrontmost", "wait", "-duration", str(args.seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    events: list[dict[str, Any]] = []
    event_time: float | None = None
    for line in proc.stdout:
        if "kLSNotifyBecameFrontmost" not in line:
            continue
        arrived = time.time()
        match = re.search(r'"CFBundleIdentifier"="([^"]+)"', line)
        # The first line carries an absolute UTC time; each later one, the time since
        # the line before it.
        absolute = re.search(r"time=(\d+/\d+/\d+ [\d:.]+)", line)
        relative = re.search(r"time=\+([\d.]+)s", line)
        if absolute:
            stamp = time.strptime(absolute.group(1).split(".")[0], "%m/%d/%Y %H:%M:%S")
            fraction = float("0." + absolute.group(1).split(".")[1])
            event_time = calendar.timegm(stamp) + fraction
        elif relative and event_time is not None:
            event_time += float(relative.group(1))
        lag_ms = None if event_time is None else (arrived - event_time) * 1000
        events.append(
            {
                "bundle": match.group(1) if match else None,
                "arrival_lag_ms": None if lag_ms is None else round(lag_ms, 1),
            }
        )
    proc.wait()
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "seconds": args.seconds,
        "events": events,
        "listener_cpu_ms": round((kids.ru_utime + kids.ru_stime) * 1000, 1),
        "listener_max_rss_mb": round(kids.ru_maxrss / 1024 / 1024, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("idle", "front"):
        p = sub.add_parser(name)
        p.add_argument("--runs", type=int, default=200)
    p = sub.add_parser("host")
    p.add_argument("--label", default="")
    p.add_argument("--out", type=Path, help="append the result as one JSON line to this file")
    for name in ("track", "track-noloop"):
        p = sub.add_parser(name)
        p.add_argument("--seconds", type=float, default=300)
        p.add_argument("--interval", type=float, default=1.0)
    p = sub.add_parser("listen")
    p.add_argument("--seconds", type=int, default=300)
    p = sub.add_parser("watch")
    p.add_argument("--seconds", type=float, default=300)
    p.add_argument("--out", type=Path, help="append the result as one JSON line to this file")
    args = parser.parse_args(argv)

    commands: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
        "idle": cmd_idle,
        "front": cmd_front,
        "host": cmd_host,
        "track": cmd_track,
        "track-noloop": cmd_track_noloop,
        "watch": cmd_watch,
        "listen": cmd_listen,
    }
    result = commands[args.cmd](args)
    text = json.dumps(result, indent=None if args.cmd == "track-noloop" else 2)
    if getattr(args, "out", None):
        with args.out.open("a") as f:
            f.write(json.dumps(result) + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
