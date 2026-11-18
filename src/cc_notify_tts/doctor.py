"""`cc-notify-tts doctor`: checks everything a notification needs, with a fix for each problem.

Each check returns `ok`, `warn` (it still works, but not as well as it could) or
`fail` (something it needs is missing or broken). Every warning and failure
comes with a line saying how to fix it. `doctor` exits 1 when any check fails.

If the daemon isn't running, `doctor` starts it the way a hook would, with the
interpreter `setup` linked, so the check covers the path hooks use. A daemon
started this way exits on its own after the idle timeout.
"""

import json
import platform
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from cc_notify_tts import __version__, client, config, models
from cc_notify_tts.ollama import Ollama, OllamaError
from cc_notify_tts.presence import read_front_app, read_idle_seconds

PLUGIN_ID = "cc-notify-tts@cc-notify-tts"
SETUP = "Run `cc-notify-tts setup`."
SEE_LOG = "See ~/Library/Logs/cc-notify-tts/daemon.log, then run `cc-notify-tts setup`."
LIST_TIMEOUT = 30.0
# How long to wait for a starting daemon's speech model to load or fail. It loads
# in a few seconds once warm; the first load after an install takes longer.
SPEECH_WAIT_SECONDS = 20.0
SPEECH_POLL_SECONDS = 0.25


class Level(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    level: Level
    name: str
    detail: str
    # How to fix it; set for every warning and failure.
    fix: str | None = None


def ok(name: str, detail: str) -> Check:
    return Check(Level.OK, name, detail)


def warn(name: str, detail: str, fix: str) -> Check:
    return Check(Level.WARN, name, detail, fix)


def fail(name: str, detail: str, fix: str) -> Check:
    return Check(Level.FAIL, name, detail, fix)


# --- checks -------------------------------------------------------------------------


def check_macos(system: str, version: str) -> Check:
    if system != "Darwin":
        return fail("macOS", f"this is {system}", "cc-notify-tts runs only on macOS.")
    return ok("macOS", f"macOS {version}" if version else "macOS")


def check_hook_python(data: Path) -> Check:
    python = data / "bin" / "python"
    if not python.exists():
        return fail(
            "Hook Python",
            f"{_tilde(python)} is missing, so the hooks do nothing",
            SETUP,
        )
    return ok("Hook Python", _tilde(python))


def list_plugins() -> list[dict[str, Any]] | None:
    """Claude Code's installed plugins, or None when `claude` isn't on PATH.

    Raises OSError or ValueError when the listing fails.
    """
    claude = shutil.which("claude")
    if claude is None:
        return None
    try:
        done = subprocess.run(
            [claude, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=LIST_TIMEOUT,
            check=False,
        )
    except subprocess.SubprocessError as e:
        raise OSError(str(e)) from e
    if done.returncode != 0:
        raise OSError(f"`claude plugin list` exited {done.returncode}: {done.stderr.strip()}")
    entries: Any = json.loads(done.stdout)
    if not isinstance(entries, list):
        raise ValueError("`claude plugin list --json` didn't print a list")
    return [e for e in entries if isinstance(e, dict)]  # pyright: ignore[reportUnknownVariableType]


def check_plugin(list_plugins: Callable[[], list[dict[str, Any]] | None]) -> Check:
    name = "Plugin"
    try:
        plugins = list_plugins()
    except (OSError, ValueError) as e:
        return warn(
            name,
            f"couldn't list Claude Code's plugins: {e}",
            "In Claude Code, `/plugin` shows whether cc-notify-tts is installed and enabled.",
        )
    if plugins is None:
        return warn(
            name,
            "can't check: `claude` isn't on PATH",
            "In Claude Code, `/plugin` shows whether cc-notify-tts is installed and enabled.",
        )
    ours = [p for p in plugins if p.get("id") == PLUGIN_ID]
    if not ours:
        return fail(name, "not installed in Claude Code", SETUP)
    enabled = [p for p in ours if p.get("enabled") is True]
    if not enabled:
        return fail(name, "installed but disabled", f"Run `claude plugin enable {PLUGIN_ID}`.")
    plugin = enabled[0]
    install_path = plugin.get("installPath")
    if isinstance(install_path, str) and not (Path(install_path) / "hooks" / "hooks.json").exists():
        return fail(
            name,
            "installed, but its hooks file is missing",
            f"Run `claude plugin uninstall {PLUGIN_ID}`, then `cc-notify-tts setup`.",
        )
    return ok(name, f"installed and enabled, version {plugin.get('version')}")


def check_models(
    models_dir: Path, is_present: Callable[[Path, models.ModelFile], bool] = models.is_present
) -> Check:
    missing = [f.name for f in models.FILES if not is_present(models_dir / f.name, f)]
    if missing:
        return fail(
            "Speech model",
            f"{', '.join(missing)} missing or damaged in {_tilde(models_dir)}",
            SETUP,
        )
    return ok("Speech model", f"{models.MODEL.name} and {models.VOICES.name}, verified")


def check_daemon(
    socket: Path,
    python: Path,
    request: Callable[[Path, dict[str, Any], float], dict[str, Any]] = client.request,
    deliver: Callable[
        [dict[str, Any], Path, Path], tuple[dict[str, Any] | None, bool]
    ] = client.deliver,
    speech_wait: float = SPEECH_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> Check:
    """The daemon answers and its speech model loads, starting it if it isn't running.

    A daemon that has just started is still loading the model, so wait up to
    `speech_wait` seconds for the load to finish or fail.
    """
    name = "Daemon"
    started = False
    try:
        reply = request(socket, {"type": "status"}, 1.0)
    except (OSError, ValueError):
        if not python.exists():
            return fail(
                name, f"not running, and it can't start: {_tilde(python)} is missing", SETUP
            )
        try:
            pinged, started = deliver({"type": "ping"}, socket, python)
        except OSError as e:
            return fail(name, f"not running, and it couldn't start: {e}", SETUP)
        if pinged is None:
            return fail(
                name,
                f"not running, and it didn't answer within {client.START_DEADLINE:g} s of starting",
                SEE_LOG,
            )
        try:
            reply = request(socket, {"type": "status"}, 1.0)
        except (OSError, ValueError) as e:
            return fail(name, f"started, but it didn't answer: {e}", SETUP)
    waited = 0.0
    while reply.get("speech") == "loading" and waited < speech_wait:
        sleep(SPEECH_POLL_SECONDS)
        waited += SPEECH_POLL_SECONDS
        try:
            reply = request(socket, {"type": "status"}, 1.0)
        except (OSError, ValueError) as e:
            return fail(name, f"stopped answering while the speech model loaded: {e}", SEE_LOG)
    pid = reply.get("pid")
    speech = reply.get("speech")
    if speech == "loading":
        return warn(
            name,
            f"running (pid {pid}), but the speech model was still loading after {speech_wait:g} s",
            "Run `cc-notify-tts doctor` again in a minute. If it's still loading, "
            "see ~/Library/Logs/cc-notify-tts/daemon.log.",
        )
    if speech == "unavailable":
        return fail(
            name,
            f"running (pid {pid}), but the speech model didn't load",
            SEE_LOG,
        )
    version = reply.get("version")
    if version != __version__:
        return warn(
            name,
            f"running version {version} (pid {pid}), but this command is {__version__}",
            "It picks up the new version once it exits after its idle timeout. "
            f"To switch now, run `kill {pid}`; the next event starts it again.",
        )
    how = "started it to check; " if started else ""
    return ok(name, f"running, pid {pid}, {how}speech {speech}")


def check_idle(read_idle: Callable[[], float | None]) -> Check:
    if read_idle() is None:
        return fail(
            "Idle time",
            "can't read keyboard and mouse idle time, so `away` events always speak",
            "It comes from CoreGraphics and needs no permission. "
            "Run `cc-notify-tts doctor` from a logged-in desktop session, not over SSH.",
        )
    return ok("Idle time", "readable")


def check_front(read_front: Callable[[], str | None]) -> Check:
    if read_front() is None:
        return fail(
            "Frontmost app",
            "can't read the app in front with `lsappinfo`, so `away` events speak "
            "unless you're typing",
            "`lsappinfo` ships with macOS and needs no permission. "
            "Run `cc-notify-tts doctor` from a logged-in desktop session, not over SSH.",
        )
    return ok("Frontmost app", "readable")


def check_config(path: Path) -> list[Check]:
    if not path.exists():
        return [ok("Config", f"{_tilde(path)} not found, so every setting is its default")]
    problems = config.load(path).problems
    if not problems:
        return [ok("Config", _tilde(path))]
    fix = f"Edit {_tilde(path)}; docs/specs/config.md lists every key."
    return [fail("Config", problem, fix) for problem in problems]


class ModelList(Protocol):
    def installed(self, model: str) -> bool:
        """Raises OllamaError when Ollama can't be asked."""
        ...


def check_ollama(wording: config.Wording, ollama: ModelList) -> Check:
    name = "Ollama (optional)"
    if not wording.ollama:
        return ok(name, "off (wording.ollama = false), so template wording is used")
    model = wording.model
    try:
        installed = ollama.installed(model)
    except OllamaError as e:
        return warn(
            name,
            f"{e}, so template wording is used",
            "For model-written details, install and start Ollama (`brew install ollama`, "
            f"then `brew services start ollama`) and run `ollama pull {model}`. "
            "To stop this warning, set wording.ollama = false.",
        )
    if not installed:
        return warn(
            name,
            f"running, but {model} isn't pulled, so template wording is used",
            f"Run `ollama pull {model}`, or set wording.model to a model you have.",
        )
    return ok(name, f"running, with {model}")


# --- running them -------------------------------------------------------------------


def run_checks(data: Path | None = None, config_path: Path | None = None) -> list[Check]:
    data = client.data_dir() if data is None else data
    config_path = config.default_config_path() if config_path is None else config_path
    return [
        check_macos(platform.system(), platform.mac_ver()[0]),
        check_hook_python(data),
        check_plugin(list_plugins),
        check_models(data / "models"),
        check_daemon(data / "daemon.sock", data / "venv" / "bin" / "python"),
        check_idle(read_idle_seconds),
        check_front(read_front_app),
        *check_config(config_path),
        check_ollama(config.load(config_path).config.wording, Ollama()),
    ]


def render(checks: Sequence[Check]) -> str:
    lines: list[str] = []
    for c in checks:
        lines.append(f"{c.level:<4}  {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"      Fix: {c.fix}")
    failures = sum(1 for c in checks if c.level is Level.FAIL)
    warnings = sum(1 for c in checks if c.level is Level.WARN)
    lines.append("")
    if not failures and not warnings:
        lines.append("All checks passed.")
    else:
        lines.append(f"{_count(failures, 'problem')}, {_count(warnings, 'warning')}.")
    return "\n".join(lines)


def exit_code(checks: Sequence[Check]) -> int:
    return 1 if any(c.level is Level.FAIL for c in checks) else 0


def run() -> int:
    checks = run_checks()
    print(render(checks))
    return exit_code(checks)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _tilde(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)
