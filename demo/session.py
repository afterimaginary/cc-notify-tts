"""Plays one scene of the demo: a simulated Claude Code session whose hooks are real.

Run as `python demo/session.py <scene>` inside the demo's throwaway home. The
screen shows a short, made-up session in the `weather-app` project. Behind it,
each hook payload Claude Code would send goes through the plugin's own hook
command, the real shim and the real daemon (started with `demo/daemon.py`).
When the daemon decides to speak, the scene waits for the sentence's audio and
prints what was said; when it stays silent, the scene waits for its decision.

`<home>/presence.json` says whether the viewer is away, and each scene sets it.
`<home>/scenes/<scene>.json` records the WAV that scene produced, if any, for
the build to mix in.
"""

import json
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cc_notify_tts.client import request, socket_path

REPO = Path(__file__).resolve().parents[1]
HOOKS: dict[str, Any] = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
TERMINAL = "com.apple.Terminal"
BROWSER = "com.apple.Safari"
WAIT_TIMEOUT = 30.0
WIDTH = 96
HEADER = "a simulated Claude Code session; its hooks, daemon and voice are real"

SESSION: dict[str, Any] = {
    "session_id": "7d1c9a40-3b2e-4f6a-9c85-0e4b2d6f1a37",
    "transcript_path": "/Users/dev/.claude/projects/-Users-dev-src-weather-app/7d1c9a40.jsonl",
    "cwd": "/Users/dev/src/weather-app",
    "permission_mode": "default",
}

DIM, BOLD, GREEN, CYAN, YELLOW, RESET = (
    "\x1b[2m",
    "\x1b[1m",
    "\x1b[32m",
    "\x1b[36m",
    "\x1b[33m",
    "\x1b[0m",
)


def hook(event: str, **fields: Any) -> None:
    """Run the plugin's hook command for `event`, as Claude Code would."""
    payload = {**SESSION, "hook_event_name": event, **fields}
    env = {
        "HOME": str(Path.home()),
        "PATH": "/usr/bin:/bin",
        "CLAUDE_PLUGIN_ROOT": str(REPO),
        "__CFBundleIdentifier": TERMINAL,
    }
    subprocess.run(
        ["/bin/sh", "-c", HOOKS[event][0]["hooks"][0]["command"]],
        input=json.dumps(payload).encode(),
        env=env,
        timeout=10,
        check=True,
    )


def set_presence(idle_seconds: float, front: str) -> None:
    presence = {"idle_seconds": idle_seconds, "front": front}
    (Path.home() / "presence.json").write_text(json.dumps(presence))


def spoken_count() -> int:
    record = Path.home() / "spoken.jsonl"
    return len(record.read_text().splitlines()) if record.exists() else 0


def decision_count() -> int:
    return len(request(socket_path(), {"type": "status"})["recent"])


def wait_for(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT
    while not check():
        if time.monotonic() > deadline:
            raise SystemExit("timed out waiting for the daemon")
        time.sleep(0.02)


def record_scene(name: str, wav: str | None, text: str | None) -> None:
    folder = Path.home() / "scenes"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps({"wav": wav, "text": text}))


def say(line: str = "", pause: float = 0.35) -> None:
    print(line, flush=True)
    time.sleep(pause)


def type_prompt(text: str) -> None:
    print(f"{BOLD}>{RESET} ", end="", flush=True)
    for ch in text:
        print(ch, end="", flush=True)
        time.sleep(0.035)
    print(flush=True)
    time.sleep(0.6)


def header() -> None:
    say(f"{DIM}weather-app · {HEADER}{RESET}")
    say()


def tool(name: str, detail: str, result: str = "") -> None:
    tail = f"  {DIM}{result}{RESET}" if result else ""
    say(f"  {GREEN}●{RESET} {BOLD}{name}{RESET} {detail}{tail}", 0.55)


def reply(text: str) -> None:
    say()
    for line in textwrap.wrap(text, WIDTH):
        say(f"  {line}", 0.1)
    say()


def note(text: str) -> None:
    say(f"  {DIM}{text}{RESET}", 0.8)


def speak_and_show(name: str, before: int) -> None:
    """Wait for the daemon's sentence, then print it last, as the audio starts."""
    wait_for(lambda: spoken_count() > before)
    entry = json.loads((Path.home() / "spoken.jsonl").read_text().splitlines()[-1])
    text: str = entry["text"]
    record_scene(name, entry["wav"], text)
    lines = textwrap.wrap(f'"{text}"', WIDTH - 4)
    print(f"  {YELLOW}🔊 {lines[0]}{RESET}")
    for line in lines[1:]:
        print(f"     {YELLOW}{line}{RESET}")
    sys.stdout.flush()


def finished() -> None:
    set_presence(600, BROWSER)
    header()
    type_prompt("Add a retry to the forecast fetcher and update its tests.")
    hook("UserPromptSubmit", prompt="Add a retry to the forecast fetcher and update its tests.")
    say()
    tool("Read", "src/weather/forecast.py")
    note("(you switch to the browser while it works)")
    tool("Edit", "src/weather/forecast.py", "+18 -3")
    tool("Edit", "tests/test_forecast.py", "+41")
    tool("Bash", "pytest -q", "48 passed")
    message = (
        "Added exponential backoff to the forecast fetcher and two tests for the retry "
        "path. All 48 tests pass."
    )
    reply(message)
    before = spoken_count()
    hook("Stop", stop_hook_active=False, last_assistant_message=message, background_tasks=[])
    speak_and_show("finished", before)


def permission() -> None:
    set_presence(600, BROWSER)
    header()
    type_prompt("Rebase onto main and push it.")
    hook("UserPromptSubmit", prompt="Rebase onto main and push it.")
    say()
    tool("Bash", "git fetch origin && git rebase origin/main", "rebased 3 commits")
    say()
    say(f"  {BOLD}Allow this command?{RESET}  {CYAN}git push --force origin main{RESET}", 0.2)
    say(f"  {DIM}1. Yes   2. No{RESET}", 0.2)
    say()
    before = spoken_count()
    hook(
        "PermissionRequest",
        tool_name="Bash",
        tool_input={
            "command": "git push --force origin main",
            "description": "Force-push the rebased branch to main",
        },
    )
    speak_and_show("permission", before)


def silent() -> None:
    set_presence(2, TERMINAL)
    header()
    type_prompt("Mention the retry in the changelog.")
    hook("UserPromptSubmit", prompt="Mention the retry in the changelog.")
    say()
    tool("Edit", "CHANGELOG.md", "+2")
    message = "Added the forecast retry to the changelog under Unreleased."
    reply(message)
    before = decision_count()
    hook("Stop", stop_hook_active=False, last_assistant_message=message, background_tasks=[])
    wait_for(lambda: decision_count() > before)
    record_scene("silent", None, None)
    note("(nothing is said: you're typing in this window)")


def start() -> None:
    """Open the session before recording, so the speech model is loaded and warm."""
    set_presence(600, BROWSER)
    hook("SessionStart", source="startup", model="claude-sonnet-5-5")
    wait_for(lambda: _speech_ready())


def end() -> None:
    hook("SessionEnd", reason="other")


def _speech_ready() -> bool:
    try:
        return request(socket_path(), {"type": "status"}).get("speech") == "ready"
    except (OSError, ValueError):
        return False


SCENES: dict[str, Callable[[], None]] = {
    "start": start,
    "finished": finished,
    "permission": permission,
    "silent": silent,
    "end": end,
}


if __name__ == "__main__":
    # Let the recording resume before anything is drawn.
    if sys.argv[1] in ("finished", "permission", "silent"):
        time.sleep(0.8)
    SCENES[sys.argv[1]]()
