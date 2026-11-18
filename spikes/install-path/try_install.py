"""Prove the install path end to end in a throwaway HOME.

Usage: python3 try_install.py [--models-from DIR] [--keep]

Everything happens under a new temporary HOME with PATH cut down to Homebrew
and the system, so uv, Claude Code's plugin settings and the data folder all
start empty, as on a Mac with only Homebrew. Nothing outside that folder is
touched except uv's download of Python and packages into the throwaway cache.

Steps, each printed with its result:

1. `brew install uv`: checked, not run (uv must already be installed).
2. `uv tool install --managed-python --python 3.13 <repo>`: the CLI, from this
   checkout (standing in for the git URL), with kokoro-onnx added by `--with`
   until the package declares it.
3. `cc-notify-tts setup`: `setup_standin.py`, run with the tool's interpreter,
   installs the stand-in plugin from a copy of `marketplace/` through
   `claude plugin install --marketplace`.

Then it opens a Claude Code session whose prompt a hook blocks, so no model
request is made, and checks the plugin's SessionStart hook ran the shim with
the linked interpreter and the shim started the stand-in daemon in the tool's
environment. It repeats that after a plugin update, with the interpreter link
missing, and after uninstalling, and finally removes everything the way the
README will say to.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN_ID = "cc-notify-tts@cc-notify-tts"
BLOCK = """cat >/dev/null; printf '{"decision":"block","reason":"spike"}'"""


class Sandbox:
    def __init__(self, root: Path, claude: str) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        # Only `claude` itself joins PATH, not the rest of its folder.
        claude_bin = root / "claude-bin"
        claude_bin.mkdir()
        (claude_bin / "claude").symlink_to(claude)
        self.claude = str(claude_bin / "claude")
        self.data = self.home / "Library" / "Application Support" / "cc-notify-tts"
        self.tool_bin = self.home / ".local" / "bin"
        # A Mac with only Homebrew, plus the folder `claude` lives in.
        path = ["/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([*path, str(claude_bin)]),
            "USER": os.environ.get("USER", ""),
            "TMPDIR": str(root / "tmp"),
            "LANG": "en_US.UTF-8",
        }
        (root / "tmp").mkdir()

    def run(self, *cmd: str, check: bool = True, cwd: Path | None = None) -> str:
        print(f"$ {' '.join(cmd)}")
        proc = subprocess.run(
            cmd, env=self.env, cwd=cwd or self.root, capture_output=True, text=True
        )
        out = (proc.stdout + proc.stderr).strip()
        for line in out.splitlines()[-6:]:
            print(f"    {line}")
        if check and proc.returncode != 0:
            raise SystemExit(f"failed ({proc.returncode}): {' '.join(cmd)}")
        return out

    def records(self, name: str) -> list[dict[str, object]]:
        path = self.data / "spike" / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def session(self) -> list[dict[str, object]]:
        """One `claude -p` session, prompt blocked; returns its hook events."""
        project = self.root / "project"
        project.mkdir(exist_ok=True)
        settings = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                                                               "command": BLOCK}]}]}}  # fmt: skip
        line = {"type": "user", "message": {"role": "user", "content": "spike"}}
        proc = subprocess.run(
            [
                self.claude, "-p", "--input-format", "stream-json",
                "--output-format", "stream-json", "--verbose",
                "--settings", json.dumps(settings), "--strict-mcp-config",
                "--no-session-persistence",
            ],
            input=json.dumps(line) + "\n", env=self.env, cwd=project,
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        time.sleep(1.5)  # let the async hook and the detached daemon finish
        events: list[dict[str, object]] = []
        for raw in proc.stdout.splitlines():
            msg = json.loads(raw)
            if msg.get("subtype") in ("hook_started", "hook_response"):
                events.append(msg)
        return events


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{': ' + detail if detail else ''}")


def copy_marketplace(sb: Sandbox) -> Path:
    # Claude Code accepts owner/repo, an https URL or a path as a marketplace
    # source, not file:// git URLs, so the spike uses a path. A GitHub source
    # behaves the same once fetched; only where the plugin is read from differs.
    market = sb.root / "marketplace"
    shutil.copytree(HERE / "marketplace", market)
    return market


def bump_plugin(market: Path, version: str) -> None:
    manifest = market / "plugin" / ".claude-plugin" / "plugin.json"
    data = json.loads(manifest.read_text())
    data["version"] = version
    manifest.write_text(json.dumps(data, indent=2) + "\n")


def verify_session(sb: Sandbox, label: str, expect_hook: bool) -> dict[str, object] | None:
    shims_before = len(sb.records("shim.jsonl"))
    daemons_before = len(sb.records("daemon.jsonl"))
    events = sb.session()
    shims = sb.records("shim.jsonl")[shims_before:]
    daemons = sb.records("daemon.jsonl")[daemons_before:]
    started = [e for e in events if e.get("subtype") == "hook_started"]
    print(f"session ({label}): {len(started)} hook(s) started, {len(shims)} shim run(s)")
    for e in events:
        if e.get("subtype") == "hook_response" and e.get("hook_event") == "SessionStart":
            keep = {k: e.get(k) for k in ("hook_name", "exit_code", "outcome", "stderr")}
            print(f"    hook_response {json.dumps(keep)}")
    if not expect_hook:
        check("no shim run", not shims)
        return None
    check("shim ran once", len(shims) == 1)
    if not shims:
        return None
    shim = shims[0]
    check("shim interpreter is the data folder's link", shim["executable"] ==
          str(sb.data / "bin" / "python"),
          f"-> {os.readlink(sb.data / 'bin' / 'python')}, runs {shim['realpath']}")  # fmt: skip
    check("shim ran with -I -S", shim["flags"] == {"isolated": 1, "no_site": 1})
    check("daemon started in the tool's environment", len(daemons) == 1 and
          "error" not in daemons[0], json.dumps(daemons[0]) if daemons else "none")  # fmt: skip
    return shim


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-from", type=Path)
    parser.add_argument("--keep", action="store_true", help="keep the throwaway HOME")
    args = parser.parse_args()
    claude = shutil.which("claude")
    if claude is None:
        raise SystemExit("claude not found on PATH")
    claude = str(Path(claude).resolve())
    root = Path(tempfile.mkdtemp(prefix="install-path-"))
    sb = Sandbox(root, claude)
    claude = sb.claude
    print(f"throwaway HOME: {sb.home}")
    market = copy_marketplace(sb)

    print("\n== 1. brew install uv (checked, not run)")
    sb.run("brew", "list", "--versions", "uv")
    sb.run("uv", "--version")

    print("\n== 2. uv tool install")
    t = time.monotonic()
    sb.run("uv", "tool", "install", "--managed-python", "--python", "3.13",
           "--with", "kokoro-onnx==0.6.1", str(REPO))  # fmt: skip
    print(f"  took {time.monotonic() - t:.1f} s")
    tool_python = sb.home / ".local" / "share" / "uv" / "tools" / "cc-notify-tts" / "bin" / "python"
    print(f"  tool venv python -> {os.readlink(tool_python)}")
    check("CLI installed", (sb.tool_bin / "cc-notify-tts").exists())
    sb.run(str(sb.tool_bin / "cc-notify-tts"), "--version")

    print("\n== 3. cc-notify-tts setup (stand-in)")
    setup = [str(tool_python), str(HERE / "setup_standin.py"), "--marketplace",
             str(market)]  # fmt: skip
    if args.models_from:
        setup += ["--models-from", str(args.models_from)]
    sb.run(*setup)
    cache = sb.home / ".claude" / "plugins" / "cache"
    print(f"  plugin cache: {sorted(str(p.relative_to(cache)) for p in cache.glob('*/*/*'))}")

    print("\n== session after install")
    first = verify_session(sb, "installed 0.0.1", expect_hook=True)

    print("\n== plugin update to 0.0.2")
    bump_plugin(market, "0.0.2")
    sb.run(claude, "plugin", "marketplace", "update", "cc-notify-tts")
    sb.run(claude, "plugin", "update", PLUGIN_ID)
    print(f"  plugin cache: {sorted(str(p.relative_to(cache)) for p in cache.glob('*/*/*'))}")
    second = verify_session(sb, "updated 0.0.2", expect_hook=True)
    if first and second:
        print(f"  plugin root before: {first['plugin_root']}")
        print(f"  plugin root after:  {second['plugin_root']}")
    check("data folder kept its links and files",
          (sb.data / "bin" / "python").exists() and (sb.data / "venv").exists(),
          ", ".join(sorted(p.name for p in sb.data.iterdir())))  # fmt: skip

    print("\n== interpreter link missing (plugin installed, setup not run)")
    link = sb.data / "bin" / "python"
    target = os.readlink(link)
    link.unlink()
    verify_session(sb, "no interpreter", expect_hook=False)
    link.symlink_to(target)

    print("\n== uninstall")
    sb.run(claude, "plugin", "uninstall", PLUGIN_ID)
    verify_session(sb, "plugin uninstalled", expect_hook=False)
    sb.run(claude, "plugin", "marketplace", "remove", "cc-notify-tts")
    sb.run("uv", "tool", "uninstall", "cc-notify-tts")
    shutil.rmtree(sb.data)
    leftovers = [p for p in (sb.tool_bin / "cc-notify-tts", sb.data,
                             sb.home / ".local" / "share" / "uv" / "tools" / "cc-notify-tts")
                 if p.exists() or p.is_symlink()]  # fmt: skip
    check("CLI, tool environment and data folder gone", not leftovers, str(leftovers))
    cached = list(cache.glob("*/*/*")) if cache.exists() else []
    # Claude Code marks old copies orphaned and sweeps them 14 days later.
    orphaned = [p for p in cached if (p / ".orphaned_at").exists()]
    check("plugin cache copies marked orphaned", len(orphaned) == len(cached),
          f"{len(orphaned)} of {len(cached)}")  # fmt: skip
    if not args.keep:
        shutil.rmtree(root)
    print("\ndone")


if __name__ == "__main__":
    sys.exit(main())
