"""Talking to a local Ollama server, and keeping the wording model warm.

Every request carries `num_ctx` 2048 and `keep_alive` 1h, so the model stays
loaded at one small context (ADR-0003). `KeepWarm` loads the model while a
session is open, repeats a load-only ping inside the `keep_alive`, and unloads
it once no session is open or the daemon exits.

Standard library only: four small JSON requests don't need an HTTP library.
"""

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

ENDPOINT = "http://127.0.0.1:11434"
# Context length for every request and ping. Leaving it out reloads the model at
# Ollama's default context, which costs up to 3.5 GiB more (ADR-0003).
NUM_CTX = 2048
KEEP_ALIVE = "1h"
# A load-only ping repeats this often, inside KEEP_ALIVE.
PING_SECONDS = 45 * 60
# How long a load may take before giving up. A load from cache takes 0.7-1.5 s.
LOAD_TIMEOUT = 30.0
# Reading /api/ps takes under 1 ms; anything slower means Ollama is in trouble.
PS_TIMEOUT = 0.5
UNLOAD_TIMEOUT = 2.0


class OllamaError(Exception):
    """Ollama is missing, not running, slow, lacks the model or answered badly."""


class Client(Protocol):
    def loaded_context(self, model: str) -> int | None:
        """The context length `model` is loaded with, or None if it isn't loaded."""
        ...

    def chat(self, model: str, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        """One chat turn; returns the reply's text."""
        ...

    def load(self, model: str) -> None:
        """Load `model` (or reset its expiry) without generating anything."""
        ...

    def unload(self, model: str) -> None: ...


class Ollama:
    """Ollama's HTTP API. Every method raises `OllamaError` on any failure."""

    def __init__(self, endpoint: str = ENDPOINT) -> None:
        self.endpoint = endpoint.rstrip("/")

    def loaded_context(self, model: str) -> int | None:
        reply = self._call("GET", "/api/ps", None, PS_TIMEOUT)
        models: Any = reply.get("models")
        for entry in models if isinstance(models, list) else []:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(entry, dict):
                continue
            fields: dict[str, Any] = entry  # pyright: ignore[reportUnknownVariableType]
            if same_model(fields.get("name"), model) or same_model(fields.get("model"), model):
                context = fields.get("context_length")
                return context if isinstance(context, int) else 0
        return None

    def chat(self, model: str, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        body = {
            "model": model,
            "stream": False,
            # A one-line answer gains nothing from a reasoning pass, and it costs
            # latency. Ollama ignores it for models that don't think.
            "think": False,
            "keep_alive": KEEP_ALIVE,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"num_ctx": NUM_CTX, "temperature": 0, "num_predict": max_tokens},
        }
        reply = self._call("POST", "/api/chat", body, timeout)
        message: Any = reply.get("message")
        fields: dict[str, Any] = message if isinstance(message, dict) else {}  # pyright: ignore[reportUnknownVariableType]
        content = fields.get("content")
        if not isinstance(content, str):
            raise OllamaError("the reply had no message")
        return content

    def load(self, model: str) -> None:
        body = {"model": model, "keep_alive": KEEP_ALIVE, "options": {"num_ctx": NUM_CTX}}
        self._call("POST", "/api/generate", body, LOAD_TIMEOUT)

    def unload(self, model: str) -> None:
        self._call("POST", "/api/generate", {"model": model, "keep_alive": 0}, UNLOAD_TIMEOUT)

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.endpoint + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise OllamaError(_http_error(e)) from e
        except TimeoutError as e:
            raise OllamaError(f"no answer within {timeout:g} s") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise OllamaError(f"no answer within {timeout:g} s") from e
            raise OllamaError(f"not running at {self.endpoint} ({e.reason})") from e
        except OSError as e:
            raise OllamaError(str(e)) from e
        try:
            reply: Any = json.loads(raw)
        except ValueError as e:
            raise OllamaError("the reply wasn't JSON") from e
        if not isinstance(reply, dict):
            raise OllamaError("the reply wasn't a JSON object")
        return reply  # pyright: ignore[reportUnknownVariableType]


def same_model(name: Any, model: str) -> bool:
    """Whether Ollama's `name` is `model`; a name without a tag means `:latest`."""
    if not isinstance(name, str):
        return False
    return _tagged(name) == _tagged(model)


def _tagged(name: str) -> str:
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def _http_error(e: urllib.error.HTTPError) -> str:
    try:
        detail: Any = json.loads(e.read()).get("error")
    except (OSError, ValueError, AttributeError):
        detail = None
    if e.code == 404:
        return f"model not found ({detail})" if detail else "model not found"
    return f"HTTP {e.code}: {detail}" if detail else f"HTTP {e.code}"


class KeepWarm:
    """Keeps the wording model loaded while a session is open, off the caller's thread.

    The daemon's event worker calls `update` with the model to keep warm, or
    None when no session is open or wording is off, after each event and when
    `ping_due()` says so. Loads, pings and unloads run in order on one
    background thread, so a slow load never holds up an event.
    """

    def __init__(self, client: Client, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._clock = clock
        self._jobs: queue.SimpleQueue[tuple[str, str] | None] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._work, name="keep-warm", daemon=True)
        self._started = False
        # The model being kept warm, and when it was last pinged.
        self._model: str | None = None
        self._pinged_at = 0.0

    def ping_due(self) -> bool:
        return self._model is not None and self._clock() - self._pinged_at >= PING_SECONDS

    def update(self, model: str | None) -> None:
        if model != self._model:
            if self._model is not None:
                self._submit("unload", self._model)
            self._model = model
            if model is not None:
                self._ping(model)
        elif model is not None and self.ping_due():
            self._ping(model)

    def load_soon(self, model: str) -> None:
        """The model wasn't loaded when wording needed it: load it for next time."""
        self._ping(model)

    def close(self, timeout: float = UNLOAD_TIMEOUT + 1) -> None:
        """Unload the model, if one is warm, and stop the background thread."""
        self.update(None)
        if self._started:
            self._jobs.put(None)
            self._thread.join(timeout)

    def _ping(self, model: str) -> None:
        self._pinged_at = self._clock()
        self._submit("load", model)

    def _submit(self, job: str, model: str) -> None:
        if not self._started:
            self._thread.start()
            self._started = True
        self._jobs.put((job, model))

    def _work(self) -> None:
        while (item := self._jobs.get()) is not None:
            job, model = item
            try:
                if job == "load":
                    self._client.load(model)
                else:
                    self._client.unload(model)
            except OllamaError as e:
                log.info("couldn't %s %s: %s", job, model, e)
            except Exception:
                log.exception("couldn't %s %s", job, model)
