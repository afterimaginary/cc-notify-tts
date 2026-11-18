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

# Installs a git pre-push hook that runs `make ready`.
hooks:
	./scripts/install-pre-push.sh

.PHONY: ready verify eval hooks
