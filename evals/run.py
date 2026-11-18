"""Run the wording eval against local Ollama models and write the dated results.

    uv run python -m evals.run llama3.2:1b gemma3:1b

For each model, in turn: load it at the daemon's context length, send one
warm-up request (timed and recorded, not scored), then every case once with the
daemon's own request options. Latency is the client's wall time per request.
Resident memory, digest and context length come from Ollama's /api/ps while the
model is loaded. A model that wasn't loaded before its run is unloaded after it.

Writes `evals/results/<date>.md` and the raw results beside it as `.json`.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from cc_notify_tts.events import EventKind, Notification
from cc_notify_tts.model_wording import MAX_TOKENS, request_for
from cc_notify_tts.ollama import ENDPOINT, NUM_CTX, Ollama, OllamaError
from evals.cases import CASES_DIR, Case, cases_digest, load_cases
from evals.report import render
from evals.results import ModelRun, Results
from evals.score import Score, failed, score

RESULTS_DIR = Path(__file__).parent / "results"
# Generous, unlike the daemon's: the eval measures how long an answer takes.
CHAT_TIMEOUT = 30.0
# A request unlike any case, so the warm-up doesn't prime the prompt cache for one.
_WARM_UP = Notification(
    EventKind.FINISHED,
    "eval-warm-up",
    "/Users/dev/src/warm-up",
    {"last_assistant_message": "Renamed the settings page and updated the two links to it."},
    None,
    (),
)


class EvalClient(Protocol):
    def version(self) -> str: ...

    def running(self, model: str) -> dict[str, Any] | None: ...

    def chat(
        self, model: str, system: str, user: str, *, timeout: float, max_tokens: int
    ) -> str: ...

    def load(self, model: str) -> None: ...

    def unload(self, model: str) -> None: ...


def run_model(
    client: EvalClient,
    model: str,
    cases: Sequence[Case],
    clock: Callable[[], float] = time.perf_counter,
    load_average: Callable[[], float] = lambda: os.getloadavg()[0],
) -> ModelRun:
    load = load_average()
    was_loaded = client.running(model) is not None
    client.load(model)
    warm_up = request_for(_WARM_UP)
    if warm_up is None:
        raise AssertionError("the warm-up notification has no model request")
    start = clock()
    client.chat(model, warm_up.system, warm_up.user, timeout=CHAT_TIMEOUT, max_tokens=MAX_TOKENS)
    warm_up_ms = (clock() - start) * 1000

    latencies: list[float] = []
    scores: list[Score] = []
    for case in cases:
        start = clock()
        try:
            answer = client.chat(
                model,
                case.request.system,
                case.request.user,
                timeout=CHAT_TIMEOUT,
                max_tokens=MAX_TOKENS,
            )
        except OllamaError as e:
            scores.append(failed(case, str(e)))
        else:
            scores.append(score(case, answer))
        latencies.append((clock() - start) * 1000)

    entry = client.running(model) or {}
    if not was_loaded:
        client.unload(model)
    return ModelRun(
        model=model,
        digest=_str(entry.get("digest")),
        resident_bytes=_int(entry.get("size")),
        vram_bytes=_int(entry.get("size_vram")),
        context_length=_int(entry.get("context_length")),
        warm_up_ms=warm_up_ms,
        load_average=load,
        latencies_ms=tuple(latencies),
        scores=tuple(scores),
    )


def machine_class() -> str:
    """Chip, memory and macOS version, and nothing that names the machine."""
    chip = _command("sysctl", "-n", "machdep.cpu.brand_string") or "unknown chip"
    memory = _command("sysctl", "-n", "hw.memsize")
    macos = _command("sw_vers", "-productVersion")
    parts = [chip]
    if memory.isdigit():
        parts.append(f"{int(memory) / 2**30:.0f} GiB")
    if macos:
        parts.append(f"macOS {macos}")
    return ", ".join(parts)


def write_results(results: Results, folder: Path) -> Path:
    """Write `<date>.md` and `<date>.json`, adding `-2`, `-3`… to keep earlier runs."""
    folder.mkdir(parents=True, exist_ok=True)
    stem, n = results.date, 1
    while (folder / f"{stem}.md").exists() or (folder / f"{stem}.json").exists():
        n += 1
        stem = f"{results.date}-{n}"
    (folder / f"{stem}.json").write_text(json.dumps(results.to_json(), indent=2) + "\n")
    markdown = folder / f"{stem}.md"
    markdown.write_text(render(results))
    return markdown


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__)
    parser.add_argument("models", nargs="+", help="Ollama model names, such as llama3.2:1b")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="results folder")
    parser.add_argument("--endpoint", default=ENDPOINT, help="Ollama's address")
    args = parser.parse_args(argv)

    cases = load_cases()
    client = Ollama(args.endpoint)
    try:
        version = client.version()
    except OllamaError as e:
        print(f"Ollama isn't available: {e}", file=sys.stderr)
        return 1
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.kind.value] = counts.get(case.kind.value, 0) + 1

    runs: list[ModelRun] = []
    for model in args.models:
        print(f"{model}: {len(cases)} cases…", file=sys.stderr)
        try:
            runs.append(run_model(client, model, cases))
        except OllamaError as e:
            print(f"{model}: {e}", file=sys.stderr)
            return 1
    results = Results(
        date=date.today().isoformat(),
        ollama_version=version,
        machine=machine_class(),
        cases_digest=cases_digest(CASES_DIR),
        case_counts=counts,
        settings={"num_ctx": NUM_CTX, "max_tokens": MAX_TOKENS, "temperature": 0},
        runs=tuple(runs),
    )
    print(write_results(results, args.out))
    return 0


def _command(*args: str) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


if __name__ == "__main__":
    sys.exit(main())
