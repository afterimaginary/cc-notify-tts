"""Stand-in daemon: times a fixed CPU-bound workload and records how it was scheduled.

Each round appends one JSON line to the output file. A line contains the
workload's wall time, the round's CPU time split by the QoS class the kernel
billed it at, the share of CPU time that ran on performance cores, and the
process's parentage. The parentage shows whether the process was reparented to
launchd after its parent exited.

Standard library only, so it runs the same way from a hook, a shell or launchd.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Pure-Python loop: single-threaded and CPU-bound. On an idle performance core
# it takes about 0.2 s.
WORKLOAD_ITERATIONS = 6_000_000


class RusageInfoV6(ctypes.Structure):
    """`struct rusage_info_v6` from <sys/resource.h>, up to the fields read here."""

    _fields_ = (
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
        ("ri_child_user_time", ctypes.c_uint64),
        ("ri_child_system_time", ctypes.c_uint64),
        ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_child_interrupt_wkups", ctypes.c_uint64),
        ("ri_child_pageins", ctypes.c_uint64),
        ("ri_child_elapsed_abstime", ctypes.c_uint64),
        ("ri_diskio_bytesread", ctypes.c_uint64),
        ("ri_diskio_byteswritten", ctypes.c_uint64),
        ("ri_cpu_time_qos_default", ctypes.c_uint64),
        ("ri_cpu_time_qos_maintenance", ctypes.c_uint64),
        ("ri_cpu_time_qos_background", ctypes.c_uint64),
        ("ri_cpu_time_qos_utility", ctypes.c_uint64),
        ("ri_cpu_time_qos_legacy", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_initiated", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_interactive", ctypes.c_uint64),
        ("ri_billed_system_time", ctypes.c_uint64),
        ("ri_serviced_system_time", ctypes.c_uint64),
        ("ri_logical_writes", ctypes.c_uint64),
        ("ri_lifetime_max_phys_footprint", ctypes.c_uint64),
        ("ri_instructions", ctypes.c_uint64),
        ("ri_cycles", ctypes.c_uint64),
        ("ri_billed_energy", ctypes.c_uint64),
        ("ri_serviced_energy", ctypes.c_uint64),
        ("ri_interval_max_phys_footprint", ctypes.c_uint64),
        ("ri_runnable_time", ctypes.c_uint64),
        ("ri_flags", ctypes.c_uint64),
        ("ri_user_ptime", ctypes.c_uint64),
        ("ri_system_ptime", ctypes.c_uint64),
        # The kernel writes the whole v6 struct; leave room for the fields after these.
        ("_rest", ctypes.c_uint8 * 512),
    )


QOS_FIELDS = (
    "maintenance",
    "background",
    "utility",
    "default",
    "legacy",
    "user_initiated",
    "user_interactive",
)

# qos_class_t values from <sys/qos.h>.
QOS_CLASS_NAMES = {
    0x21: "user_interactive",
    0x19: "user_initiated",
    0x15: "default",
    0x11: "utility",
    0x09: "background",
    0x00: "unspecified",
}

_libc = ctypes.CDLL(None, use_errno=True)


def rusage() -> RusageInfoV6:
    info = RusageInfoV6()
    if _libc.proc_pid_rusage(os.getpid(), 6, ctypes.byref(info)) != 0:
        raise OSError(ctypes.get_errno(), "proc_pid_rusage failed")
    return info


def thread_qos() -> str:
    _libc.qos_class_self.restype = ctypes.c_uint
    value = int(_libc.qos_class_self())
    return QOS_CLASS_NAMES.get(value, hex(value))


def workload() -> int:
    total = 0
    for i in range(WORKLOAD_ITERATIONS):
        total += i * i
    return total


def measure_round() -> dict[str, Any]:
    before = rusage()
    start = time.perf_counter()
    workload()
    elapsed = time.perf_counter() - start
    after = rusage()

    def delta(field: str) -> int:
        return int(getattr(after, field)) - int(getattr(before, field))

    cpu = delta("ri_user_time") + delta("ri_system_time")
    p_cpu = delta("ri_user_ptime") + delta("ri_system_ptime")
    qos = {name: delta(f"ri_cpu_time_qos_{name}") for name in QOS_FIELDS}
    qos_total = sum(qos.values()) or 1
    return {
        "elapsed_ms": round(elapsed * 1000, 1),
        "p_core_share": round(p_cpu / cpu, 3) if cpu else None,
        "qos_share": {k: round(v / qos_total, 3) for k, v in qos.items() if v},
    }


def parentage() -> dict[str, Any]:
    pid = os.getpid()
    return {
        "pid": pid,
        "ppid": os.getppid(),
        "pgid": os.getpgid(0),
        "sid": os.getsid(0),
        "tty": os.isatty(0) or os.isatty(1) or os.isatty(2),
        "nice": os.getpriority(os.PRIO_PROCESS, 0),
        "thread_qos": thread_qos(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="JSON Lines file to append to")
    parser.add_argument("--label", required=True, help="scenario name stored on every line")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--interval", type=float, default=0.0, help="seconds between rounds")
    args = parser.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    for n in range(args.rounds):
        if n and args.interval:
            time.sleep(args.interval)
        line = {
            "label": args.label,
            "round": n,
            "t": round(time.time() - started, 2),
            "wall": time.time(),
            **parentage(),
            **measure_round(),
        }
        with args.out.open("a") as f:
            f.write(json.dumps(line) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
