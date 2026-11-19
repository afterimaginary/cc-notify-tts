"""The hook shim and the plugin's manifests: stdlib only, quiet, and the approved hooks."""

import ast
import errno
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from cc_notify_tts import client, setup, shim

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "src" / "cc_notify_tts"
# Everything the hook process may load: the entry script and these package modules.
SHIM_MODULES = {
    "cc_notify_tts",
    "cc_notify_tts.shim",
    "cc_notify_tts.client",
    "cc_notify_tts.host_app",
}
SHIM_FILES = [REPO / "hooks" / "shim.py"] + [
    PACKAGE / ("__init__.py" if m == "cc_notify_tts" else m.split(".")[1] + ".py")
    for m in SHIM_MODULES
]
# The hook list chosen in ADR-0001.
APPROVED_HOOKS = {
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "Stop",
    "StopFailure",
    "PermissionRequest",
    "PermissionDenied",
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            if node.module == "cc_notify_tts":
                names |= {f"cc_notify_tts.{alias.name}" for alias in node.names}
    return names


@pytest.mark.parametrize("path", SHIM_FILES, ids=lambda p: p.name)
def test_shim_files_import_only_the_standard_library(path: Path) -> None:
    for name in _imports(path):
        top = name.split(".")[0]
        if top == "cc_notify_tts":
            assert name in SHIM_MODULES | {"cc_notify_tts.__version__"}, f"{path.name}: {name}"
        else:
            assert top in sys.stdlib_module_names, f"{path.name} imports {name}"


def test_shim_loads_nothing_else_in_an_isolated_interpreter() -> None:
    # As the hook runs it: -I -S, so site-packages aren't even importable.
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from cc_notify_tts import shim, host_app;"
        "stdlib = sys.stdlib_module_names;"
        "print('\\n'.join(m for m in sys.modules if m.split('.')[0] not in stdlib))"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(REPO / "src")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert set(done.stdout.split()) == SHIM_MODULES | {"__main__"}


def test_log_failure_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def full(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(client, "log_dir", lambda: tmp_path)
    monkeypatch.setattr(Path, "open", full)
    shim.log("anything")
    shim.main(["Stop"], b"not json", {}, 0.0)


def test_log_rotates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(client, "log_dir", lambda: tmp_path)
    (tmp_path / "shim.log").write_text("x" * (shim.LOG_MAX_BYTES + 1))
    shim.log("fresh")
    assert (tmp_path / "shim.log.1").stat().st_size > shim.LOG_MAX_BYTES
    assert (tmp_path / "shim.log").read_text().endswith("fresh\n")


def test_unexpected_error_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(client, "log_dir", lambda: tmp_path)
    monkeypatch.setattr(client, "deliver", broken)
    shim.main(["Stop"], b'{"hook_event_name": "Stop"}', {}, 0.0)
    assert "unexpected RuntimeError: boom" in (tmp_path / "shim.log").read_text()


def test_refused_event_is_logged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []

    def deliver(message: dict[str, Any], *_args: Any) -> tuple[dict[str, Any], bool]:
        sent.append(message)
        return {"ok": False, "error": "event needs a payload object"}, False

    monkeypatch.setattr(client, "log_dir", lambda: tmp_path)
    monkeypatch.setattr(client, "deliver", deliver)
    before = time.time()
    shim.main(["Stop"], b'{"hook_event_name": "Stop"}', {}, 12.5)
    [message] = sent
    sent_at = message.pop("sent_at")
    assert message == {"type": "event", "fired_at": 12.5, "payload": {"hook_event_name": "Stop"}}
    assert before <= sent_at <= time.time()
    assert "refused the event" in (tmp_path / "shim.log").read_text()


def _json(path: str) -> dict[str, Any]:
    return json.loads((REPO / path).read_text())


def test_hooks_are_the_approved_list_all_async() -> None:
    hooks = _json("hooks/hooks.json")["hooks"]
    assert set(hooks) == APPROVED_HOOKS
    for event, groups in hooks.items():
        [group] = groups
        [hook] = group["hooks"]
        assert hook["type"] == "command"
        assert hook["async"] is True
        # ADR-0011: the data folder's interpreter, guarded, with -I -S and the event name.
        assert re.fullmatch(
            r'py="\$HOME/Library/Application Support/cc-notify-tts/bin/python"; '
            r'\[ -x "\$py" \] \|\| exit 0; '
            r'exec "\$py" -I -S "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/shim.py" (\w+)',
            hook["command"],
        ), event
        assert hook["command"].endswith(f" {event}")


def test_manifests_match_what_setup_installs() -> None:
    plugin = _json(".claude-plugin/plugin.json")
    marketplace = _json(".claude-plugin/marketplace.json")
    assert plugin["name"] == setup.PLUGIN
    # setup prints `/plugin install cc-notify-tts@cc-notify-tts` for the desktop app.
    assert marketplace["name"] == setup.PLUGIN
    assert marketplace["plugins"] == [
        {
            "name": setup.PLUGIN,
            "description": marketplace["plugins"][0]["description"],
            "source": "./",
        }
    ]
    # The plugin's version is the commit, so every push reaches `claude plugin update` (ADR-0014).
    assert "version" not in plugin
    assert "version" not in marketplace["plugins"][0]
