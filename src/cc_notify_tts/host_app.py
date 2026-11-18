"""Which app a Claude Code session runs in, named from inside one of its hooks (ADR-0006).

Standard library only: the hook shim imports it. The sources, best first:

1. Inside tmux, the app of a client attached to the pane's own session. Nothing
   else is trusted there, because the environment and the process tree name
   whichever app started the tmux server.
2. The outermost `.app` bundle among the hook's ancestor processes.
3. `__CFBundleIdentifier`, then `TERM_PROGRAM`, then the Claude desktop app's
   variable, all inherited from the environment.
"""

import ctypes
from collections.abc import Mapping
from pathlib import Path

# Values terminals document for TERM_PROGRAM. VS Code forks also set "vscode",
# which is why the process tree and the bundle id come first.
TERM_PROGRAM_BUNDLES = {
    "Apple_Terminal": "com.apple.Terminal",
    "iTerm.app": "com.googlecode.iterm2",
    "ghostty": "com.mitchellh.ghostty",
    "vscode": "com.microsoft.VSCode",
}
CLAUDE_DESKTOP = "com.anthropic.claudefordesktop"

# proc_pidinfo flavor PROC_PIDT_SHORTBSDINFO, a struct proc_bsdshortinfo with the
# parent pid at offset 4. The fuller PROC_PIDTBSDINFO is refused for the
# root-owned `login` between a terminal and its shell, which would end the walk.
_PROC_PIDT_SHORTBSDINFO = 13
_SHORTBSDINFO_SIZE = 64
_PPID_OFFSET = 4
_MAX_PATH = 4096
_MAX_DEPTH = 64

_libproc: ctypes.CDLL | None = None


def identify(env: Mapping[str, str], pid: int) -> str | None:
    """Bundle id of the app hosting the session that `pid` belongs to, or None."""
    if env.get("TMUX"):
        client = _tmux_client_pid(env.get("TMUX_PANE", ""))
        return None if client is None else from_ancestry(client)
    return (
        from_ancestry(pid)
        or env.get("__CFBundleIdentifier")
        or TERM_PROGRAM_BUNDLES.get(env.get("TERM_PROGRAM", ""))
        or (CLAUDE_DESKTOP if env.get("CLAUDE_CODE_DESKTOP_APP_VERSION") else None)
    )


def from_ancestry(pid: int) -> str | None:
    """Bundle id of the outermost app among `pid` and its ancestors."""
    for path in reversed(_ancestor_paths(pid)):
        bundle = outer_bundle(path)
        if bundle is not None:
            return bundle_id(bundle)
    return None


def outer_bundle(executable: str) -> Path | None:
    """The outermost `.app` folder an executable is inside, such as VS Code's for its helpers."""
    at = executable.find(".app/")
    return None if at < 0 else Path(executable[: at + len(".app")])


def bundle_id(bundle: Path) -> str | None:
    import plistlib

    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as f:
            value = plistlib.load(f).get("CFBundleIdentifier")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return value if isinstance(value, str) and value else None


def _ancestor_paths(pid: int) -> list[str]:
    """Executable paths of `pid` and its ancestors, nearest first, stopping before launchd."""
    paths: list[str] = []
    current: int | None = pid
    while current is not None and current > 1 and len(paths) < _MAX_DEPTH:
        path = _executable(current)
        if path:
            paths.append(path)
        current = _parent(current)
    return paths


def _lib() -> ctypes.CDLL:
    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    return _libproc


def _parent(pid: int) -> int | None:
    buf = ctypes.create_string_buffer(_SHORTBSDINFO_SIZE)
    got = _lib().proc_pidinfo(pid, _PROC_PIDT_SHORTBSDINFO, 0, buf, _SHORTBSDINFO_SIZE)
    if got != _SHORTBSDINFO_SIZE:
        return None
    return int.from_bytes(buf.raw[_PPID_OFFSET : _PPID_OFFSET + 4], "little")


def _executable(pid: int) -> str | None:
    buf = ctypes.create_string_buffer(_MAX_PATH)
    if _lib().proc_pidpath(pid, buf, _MAX_PATH) <= 0:
        return None
    return buf.value.decode(errors="replace")


def _tmux_client_pid(pane: str) -> int | None:
    """A client attached to this pane's session. None when it's detached.

    `list-clients -t` lists only that session's clients; `display-message` would
    fall back to the most recent client of any session.
    """
    if not pane:
        return None
    import subprocess

    try:
        out = subprocess.run(
            ["tmux", "list-clients", "-t", pane, "-F", "#{client_pid}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.split("\n", 1)[0].strip()
    return int(first) if first.isdigit() else None
