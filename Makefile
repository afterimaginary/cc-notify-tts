UV ?= uv

# Fast checks. Runs before every push once `make hooks` has installed the hook.
ready:
	$(UV) run --frozen ruff check
	$(UV) run --frozen ruff format --check
	$(UV) run --frozen pyright
	$(UV) run --frozen pytest -q

# Everything CI checks: ready plus integration tests. Never needs a model
# download, Ollama or an audio device.
verify: ready
	$(UV) run --frozen pytest -q -m integration

# Scores local wording models and writes evals/results/<date>.md. Needs Ollama
# and the models already pulled: make eval MODELS="llama3.2:1b gemma3:1b"
eval:
	@test -n "$(MODELS)" || { echo 'Usage: make eval MODELS="llama3.2:1b gemma3:1b"'; exit 2; }
	$(UV) run --frozen python -m evals.run $(MODELS)

# Fires a scripted session through the real hooks, daemon and Kokoro (sound
# plays) and prints p50 and p95 per stage. Needs the speech model; MODEL adds
# Ollama wording: make latency MODEL=granite4:micro
latency:
	$(UV) run --frozen python -m bench.latency run $(if $(MODEL),--model $(MODEL))

# Records the demo from scratch: build/demo/cc-notify-tts-demo.mp4 (1080p, with
# Kokoro's real audio) and docs/demo.gif (the first scene, short and silent). Needs vhs, ffmpeg and
# ImageMagick; plays nothing out loud. See demo/README.md.
demo:
	$(UV) run --frozen python demo/build.py

# Installs a git pre-push hook that runs `make ready`.
hooks:
	./scripts/install-pre-push.sh

.PHONY: ready verify eval latency demo hooks
