---
description: Install is `brew install uv`, `uv tool install` of the package with a uv-managed Python 3.13, then `~/.local/bin/cc-notify-tts setup`, which puts the CLI on PATH, links the interpreters into the data folder, fetches the model and installs the plugin through Claude Code's own command.
status: accepted
---

# ADR-0011: Install path in three commands

## Context

The brief's first "done means" item: on a Mac with only Homebrew, three documented commands install the plugin and its models. Four things have to arrive:

- **The plugin:** `hooks/hooks.json` and the shim. Claude Code copies each version into its own cache folder, `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, and `${CLAUDE_PLUGIN_ROOT}` points at it, so the path changes with every update.
- **The `cc-notify-tts` CLI** on `PATH`.
- **The daemon's Python environment**, with `kokoro-onnx` and its dependencies (about 190 MB with Python).
- **The speech model**, about 340 MB.

Code, models and state must live outside the plugin's cache folder, or a plugin update loses them. The brief puts models, state and config under `~/Library/Application Support/cc-notify-tts` (the data folder below). ADR-0004 has the hooks run the shim as `<python> -I -S <shim>` with a uv-managed Python 3.13 by absolute path, through uv's per-minor-version link so a patch upgrade doesn't break it. ADR-0002 has the shim start the daemon on demand, detached.

## Method

`spikes/install-path/` has the scripts and the command to rerun them. `try_install.py` sets `HOME` to a new empty folder and cuts `PATH` down to Homebrew, the system folders and `claude`. uv's Pythons and tools, Claude Code's settings and plugin cache, and the data folder all start empty there, as on a new Mac. It then runs the install, opens Claude Code sessions to see what the hooks do, updates the plugin, uninstalls everything and checks what's left.

Stand-ins replace the pieces other issues build: a one-hook plugin from a local marketplace, a shim that records how it was run and starts a stand-in daemon, and `setup_standin.py` for `setup`. The package is installed from the checkout instead of the git URL, with `kokoro-onnx` added by `--with` because the package doesn't declare it yet. Each session is `claude -p` with its prompt blocked by a hook, so no model request is made.

Apple M3 Max, macOS 27.2, Claude Code 2.1.327, uv 0.11.10 from Homebrew.

## Results

| Step | Result |
| - | - |
| `uv tool install --managed-python --python 3.13 <repo>` | uv downloaded Python 3.13.13 and the packages, made the tool's environment in `~/.local/share/uv/tools/cc-notify-tts/` and linked the CLI into `~/.local/bin`. About 2.5 s here with a fast connection. `cc-notify-tts --version` ran. |
| `setup` (stand-in), run inside a project with its own `.venv` | Linked `bin/python` to uv's minor-version link (not the project's venv) and `venv` to the tool's environment, copied the model files and installed the plugin with `claude plugin install cc-notify-tts --marketplace <source>`, which adds the marketplace and installs in one command. `uv tool update-shell` created `~/.zshenv`, and a new interactive zsh found `cc-notify-tts` on `PATH`. |
| Session after install | The async `SessionStart` hook ran the shim through the data folder's `bin/python` with `-I -S`. The shim started the stand-in daemon from the data folder's `venv`, detached (its parent became launchd). The daemon imported the package and `kokoro_onnx` and found both model files. |
| Plugin update 0.0.1 → 0.0.2 | Claude Code added a second cache folder. The next session ran the hook the same way. The data folder was untouched. |
| Interpreter link missing | With the guarded hook command below, the hook exits 0 and nothing runs. Unguarded, Claude Code records a hook error: exit 127, `bin/python: No such file or directory`. |
| `claude plugin uninstall` | No hook ran in the next session. |
| Removal | After `claude plugin marketplace remove`, `uv tool uninstall` and deleting the data folder, nothing was left but Claude Code's two cache copies, both marked `.orphaned_at`. Claude Code deletes those 14 days later. |

Findings on the way:

- **uv may pick a Python it didn't install.** Without `--managed-python`, `uv tool install` used the first Python 3.13 it found on `PATH`, which on a Mac with Homebrew can be Homebrew's. That one changes with `brew upgrade`, and ADR-0004 measured the uv-managed one. `--managed-python` makes uv download its own.
- **The tool's environment may be pinned to a patch version.** When `uv tool install` itself downloads Python, the environment's `python` links to the patch folder (`cpython-3.13.13-…`). When Python 3.13 was already installed by uv, it links to the per-minor-version link (`cpython-3.13-…`). A patch upgrade with `uv python upgrade 3.13` moved the minor-version link to the new patch and kept the old patch installed, so both kinds of environment kept working. So the hooks link to the minor-version link directly, as `uv python find --managed-python 3.13` returns it, never through the environment's `python`.
- **Linking a venv's `python` from elsewhere loses the venv.** Run through a symlink in another folder, the interpreter doesn't find `pyvenv.cfg` and has no site-packages. Linking the venv's folder instead works: `<data>/venv/bin/python` finds `<data>/venv/pyvenv.cfg` and imports the package and `kokoro_onnx`.
- **`uv python find` can return a project's venv.** Run inside a folder with a `.venv`, or with a venv active, `uv python find --managed-python 3.13` returns that venv's `python`. Adding `--system` returns uv's per-minor-version link in both cases.
- **`~/.local/bin` isn't on `PATH` on a Mac with only Homebrew,** so the CLI can't be run by name straight after `uv tool install`. uv warns about it. `uv tool update-shell` adds it to the profile of the shell named in `$SHELL` (`~/.zshenv` for zsh), and it fails when `$SHELL` isn't set, which a terminal always sets.
- **Marketplace sources:** Claude Code takes `owner/repo`, an https URL or a path, not a `file://` git URL, so the spike installs from a path. For a path source `${CLAUDE_PLUGIN_ROOT}` is the source folder. For a GitHub or git source it's the versioned cache copy (seen in installed GitHub plugins and in Claude Code's plugin docs).

## Decision

### The three commands

```sh
brew install uv
uv tool install --managed-python --python 3.13 git+https://github.com/afterimaginary/cc-notify-tts
~/.local/bin/cc-notify-tts setup
```

1. **`brew install uv`**: uv is the only thing Homebrew installs.
2. **`uv tool install`** puts the CLI on `PATH` and makes the daemon's environment, with `kokoro-onnx` as one of the package's own dependencies. `--managed-python --python 3.13` makes it a uv-managed 3.13, the interpreter ADR-0004 measured. If the package is published to PyPI later, this becomes `uv tool install --managed-python --python 3.13 cc-notify-tts`.
3. **`~/.local/bin/cc-notify-tts setup`** does the rest, and running it again repairs it. It's called by its full path because `~/.local/bin` may not be on `PATH` yet. It:
   - creates the data folder;
   - links `<data>/bin/python` to uv's per-minor-version Python 3.13 (from `uv python find --managed-python --system 3.13`, so a project's or an active venv is never picked), for the hooks;
   - links `<data>/venv` to the tool's environment (`sys.prefix` of the running CLI), for starting the daemon;
   - downloads the model into `<data>/models/` (the download itself is #11);
   - installs the plugin with `claude plugin install cc-notify-tts --marketplace afterimaginary/cc-notify-tts`. The marketplace and the plugin come from the same repository as the package. If `claude` isn't on `PATH`, such as with only the desktop app, it prints the two commands to run in Claude Code instead: `/plugin marketplace add afterimaginary/cc-notify-tts` and `/plugin install cc-notify-tts@cc-notify-tts`;
   - when `~/.local/bin` isn't on `PATH`, runs `uv tool update-shell` and says plainly that it changed the shell profile, naming the file uv reports, and that a new terminal is needed. If that fails, it says to add the folder to `PATH` by hand.

The user's `settings.json` is changed only by Claude Code's own plugin command, as when installing any plugin.

The owner approved this path, including the broader `setup`: it downloads the model, links the interpreters, installs the plugin (printing the `/plugin` commands when `claude` isn't on `PATH`), and runs `uv tool update-shell` when `~/.local/bin` isn't on `PATH`, saying that it changed the shell profile. The brief had `setup` only download the model.

### What lives where

| What | Where | Survives a plugin update |
| - | - | - |
| Hooks and the shim | Plugin cache, `${CLAUDE_PLUGIN_ROOT}` | Replaced by the new version |
| Package, `kokoro-onnx`, the CLI | uv's tool folder, `~/.local/share/uv/tools/cc-notify-tts/`, CLI linked from `~/.local/bin` | Yes; updated by `uv tool upgrade cc-notify-tts` |
| Python 3.13 | uv's Python folder, `~/.local/share/uv/python/` | Yes |
| Interpreter links, models, config, session state | Data folder, `~/Library/Application Support/cc-notify-tts/` | Yes |
| Logs | `~/Library/Logs/cc-notify-tts/` | Yes |

### How the hooks run the shim

Each hook command, in the plugin's `hooks/hooks.json`:

```sh
py="$HOME/Library/Application Support/cc-notify-tts/bin/python"; [ -x "$py" ] || exit 0; exec "$py" -I -S "${CLAUDE_PLUGIN_ROOT}/hooks/shim.py" <Event>
```

- The interpreter is the data folder's `bin/python`, a link to uv's per-minor-version Python 3.13, so it's the same path on every Mac and follows patch upgrades (ADR-0004).
- The shim is the plugin's own file, run with `-I -S`, so it uses only the standard library.
- If the link is missing because `setup` hasn't run, the hook exits 0 without a Claude Code hook error. `doctor` reports the missing link. `[ -x ]` is a shell builtin and `exec` replaces the shell, so the guard adds no process.

The shipped manifests and shim are #9's.

### How the shim and the CLI find and start the daemon

- **The shim** starts `<data>/venv/bin/python -I -m cc_notify_tts.daemon` when the socket doesn't answer, detached as ADR-0002 says. The data folder's `venv` link means it doesn't need to know where uv keeps tools. `-I` keeps the user's `PYTHONPATH` out without losing the venv's site-packages.
- **The CLI** already runs in that environment, so it starts the same module with its own `sys.executable`, in the same detached way.
- Both reach the daemon on its socket in the data folder.

### Uninstall

```sh
claude plugin uninstall cc-notify-tts@cc-notify-tts
claude plugin marketplace remove cc-notify-tts
uv tool uninstall cc-notify-tts
rm -rf ~/Library/Application\ Support/cc-notify-tts ~/Library/Logs/cc-notify-tts
```

The `PATH` line `setup` may have added to the shell profile can stay: other uv tools and Claude Code's native install use the same folder. Uninstalling the plugin stops the hooks at the next session. A running daemon exits after its idle timeout. The CLI could do the first, third and fourth steps in an `uninstall` command, but that isn't part of the agreed CLI.

## Alternatives

- **Plugin first, then the tool:** `brew install uv`, `claude plugin install … --marketplace …`, `uv tool install …`, `cc-notify-tts setup`. That works, but it's four commands, because the CLI and the model still need their own steps.
- **A first-run bootstrap from a `SessionStart` hook:** install the plugin, and let its first hook run `uv tool install` and download the model. Three commands, but it's a 340 MB download with no progress or errors the user can see, nothing speaks until it finishes, and the CLI isn't on `PATH` until a session has run. A hook that installs software is also a surprise.
- **The environment in `${CLAUDE_PLUGIN_DATA}`** (`~/.claude/plugins/data/<id>/`), which survives updates and goes away on uninstall. The CLI and the daemon run outside Claude Code, where the variable isn't set. The path depends on the marketplace's name, and uninstalling would delete the user's config without asking. The brief puts the data folder in `~/Library/Application Support`.
- **The environment inside the plugin folder:** lost on every update.
- **Hooks pointing at the tool environment's `python`:** may be pinned to a patch version (above), and the path depends on where uv keeps tools.
- **Hooks running `uv run` or `cc-notify-tts` itself:** `uv run` costs 13 ms more per event (ADR-0004), and the CLI's entry point would load site-packages on every event.
- **`uvx --from git+… cc-notify-tts setup` installing itself as a tool:** two commands, but an installer that installs itself is hard to follow and to repair.

## Consequences

- The package declares `kokoro-onnx` as a dependency (the spike added it with `--with`).
- `setup` does more than download the model: it links the two interpreters and installs the plugin. Rerunning it repairs both links, such as after uv's tool folder moves.
- The plugin and the package can drift apart, because Claude Code updates the plugin and uv updates the package. Both come from the same repository, so the shim's message to the daemon should carry the plugin's version, and `doctor` compares the plugin's version with the CLI's.
- `doctor` checks that `bin/python` and `venv` resolve and that the CLI's folder is on `PATH`.
- `setup` edits the shell profile, through uv, only when `~/.local/bin` isn't on `PATH`, and says so.
- If uv removes the Python patch the tool's environment is pinned to, the daemon fails to start until `uv tool upgrade --reinstall cc-notify-tts` or `setup` runs again. The hooks keep working through the minor-version link, so the shim can log why.
- The README's install section uses the three commands and the uninstall steps above.
