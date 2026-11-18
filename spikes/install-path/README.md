# Spike: install path

Answers the questions behind ADR-0011: can a Mac with only Homebrew get the plugin, the `cc-notify-tts` CLI, the daemon's Python environment and the speech model in three commands? Which interpreter do the hooks run, and how do the shim and the CLI find the daemon? What survives a plugin update, and what does uninstalling leave behind?

It isn't part of `make ready` or `make verify`.

## Run it

```sh
uv run --frozen python spikes/install-path/try_install.py \
  --models-from "$HOME/Library/Application Support/cc-notify-tts/models"
```

`--models-from` copies the two Kokoro files from a folder that already has them (the Kokoro spike downloads them there), so the run doesn't download 340 MB again. Leave it out to skip the model files. `--keep` keeps the throwaway folder for a look afterwards.

Needs `uv` from Homebrew and `claude` on `PATH`. A run takes about 15 s once uv's downloads are cached in the throwaway folder, which starts empty each time.

## What it does

Everything runs with `HOME` set to a new temporary folder and `PATH` cut down to Homebrew, the system folders and `claude` itself, and `SHELL` set to zsh as in a macOS terminal. So uv's Python and tools, Claude Code's settings and plugin cache, and the data folder all start empty. The real user's Claude Code settings, plugins and data folder are never touched.

1. **`brew install uv`**: checked with `brew list`, not run.
2. **`uv tool install --managed-python --python 3.13 <this checkout>`**, standing in for the git URL, with `--with kokoro-onnx==0.6.1` until the package declares it.
3. **`~/.local/bin/cc-notify-tts setup`**: `setup_standin.py`, run with the tool's own interpreter from inside a project with its own `.venv`. It links the interpreters into the data folder, copies the model files, installs the stand-in plugin with `claude plugin install cc-notify-tts --marketplace <path>` and runs `uv tool update-shell`. A new interactive zsh then has to find `cc-notify-tts` on `PATH`.

Then it checks, each time by opening a `claude -p` session whose prompt a hook blocks, so no model request is made:

- **After install:** the plugin's async `SessionStart` hook ran the shim with the data folder's `bin/python` and `-I -S`, and the shim started the stand-in daemon from the data folder's `venv`, which imports the package and `kokoro_onnx` and finds the model files.
- **After a plugin update** (0.0.1 to 0.0.2): the same, and the data folder is unchanged.
- **With the interpreter link missing** (plugin installed, `setup` not run): the hook exits 0 and nothing runs.
- **After `claude plugin uninstall`:** no hook runs.

Finally it removes everything the way the README will say to and checks that nothing is left but Claude Code's own cache copies, which it marks for its 14-day sweep.

## Files

| File | What it is |
| --- | --- |
| `try_install.py` | The run above |
| `setup_standin.py` | What `setup` does for the install path, minus the model download |
| `marketplace/` | A stand-in marketplace with one plugin: one async `SessionStart` hook |
| `marketplace/plugin/hooks/shim.py` | Stand-in shim: records its interpreter, starts the daemon detached |
| `marketplace/plugin/hooks/daemon_standin.py` | Stand-in daemon: records its environment and exits |

Claude Code takes `owner/repo`, an https URL or a path as a marketplace source, not a `file://` git URL, so the spike installs from a path. For a path source, `${CLAUDE_PLUGIN_ROOT}` is the source folder itself. For a GitHub source it's the versioned copy under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, which is why nothing lasting may live there.
