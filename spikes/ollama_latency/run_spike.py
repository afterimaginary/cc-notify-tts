"""Measures how fast small Ollama models write one sentence, and what keeps them warm.

For each model it records, through Ollama's HTTP API:

- cold: the first request after the model was unloaded (`keep_alive: 0`).
- warm: back-to-back requests while the model stays loaded; p50 and p95.
- num_ctx: resident memory (`size` from /api/ps), the context Ollama loaded and
  warm latency at each context length, including Ollama's own default.
- evicted: the first request after Ollama unloaded the model by itself because
  its `keep_alive` ran out.

Then it checks what keeps a model loaded: whether reading /api/ps or /api/show
extends `expires_at`, what a request without `keep_alive` does to a model loaded
with a long one, and whether a load-only ping without `num_ctx` reloads a model
loaded with one.

Every model it touched is unloaded before it exits, even on failure. Models
loaded by anyone else are left alone. Needs a running Ollama and the models
already pulled.

Usage: uv run python spikes/ollama_latency/run_spike.py <out-dir> [model ...]
"""

from __future__ import annotations

import json
import os
import platform
import signal
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ENDPOINT = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")
DEFAULT_MODELS = ["llama3.2:1b", "gemma3:1b", "qwen3:1.7b", "llama3.2:3b"]
COLD_RUNS = 3
WARM_RUNS = 30
CTX_RUNS = 10
EVICT_RUNS = 3
NUM_CTX = 2048
CTX_SWEEP: list[int | None] = [2048, 8192, None]  # None: Ollama's default
EVICT_KEEP_ALIVE_S = 5

SYSTEM = (
    "You write the detail of a spoken desktop notification. Reply with one short "
    "sentence of at most 15 words and nothing else."
)
PROMPT = (
    "A coding assistant finished a turn in the project accounts-api. Its last "
    "message was: I split the auth module into three files, moved token refresh "
    "into a background task, and all 214 tests pass. Say what was done."
)
# A long final message after a multi-step task: about 3300 characters, 800 tokens.
LONG_MESSAGE = " ".join(
    [
        "Here's a summary of everything I changed in this session.",
        "First, I split the auth module. auth/core.py had grown to about 1,400 lines and",
        "mixed session handling, token issuing and password hashing. It's now three files:",
        "auth/sessions.py owns session creation, lookup and expiry; auth/tokens.py issues,",
        "verifies and refreshes access and refresh tokens; and auth/passwords.py wraps the",
        "hashing library and the rehash-on-login check. The public functions keep their",
        "names and are re-exported from auth/__init__.py, so no caller had to change.",
        "Second, token refresh no longer happens inside the request. Before, a request with",
        "an access token close to expiry called the identity provider synchronously, which",
        "added 300 to 900 milliseconds to roughly one request in forty. Refresh now runs in",
        "a background task that wakes every 60 seconds, refreshes any token expiring in the",
        "next five minutes, and records failures in the refresh_failures table. A request",
        "that finds an expired token still refreshes inline as a fallback, and logs a",
        "warning so we can see how often the background task falls behind.",
        "Third, I fixed two bugs I found on the way. The session cleanup job compared",
        "expiry times in local time instead of UTC, so on servers outside UTC it deleted",
        "sessions up to eight hours early. And the password rehash check ran on every",
        "request instead of only at login, which is where most of the hashing time in the",
        "profile came from. Both have regression tests now.",
        "Fourth, the migration. Sessions gain a refreshed_at column, nullable, with no",
        "default, so the migration is instant on the production table. I added a down",
        "migration and ran both directions against a copy of the staging schema.",
        "Fifth, tests. I moved the auth tests to match the new layout: tests/auth/",
        "test_sessions.py, test_tokens.py and test_passwords.py. I added 23 tests: the",
        "background refresh loop with a fake clock, the inline fallback, the UTC cleanup",
        "fix, the login-only rehash, and the migration in both directions. The whole suite",
        "passes, 214 tests, in 41 seconds, down from 58 because of the rehash fix. Lint and",
        "type checks are clean.",
        "Things I didn't do: I left the OAuth callback handler where it was, because",
        "moving it would change a public URL. I didn't touch the rate limiter, although it",
        "reads the session twice per request and could share the lookup. And the refresh",
        "interval of 60 seconds is a constant in auth/tokens.py; if you want it in the",
        "config file, that's a small follow-up.",
        "What you should check: the refresh_failures table is new, so the dashboard won't",
        "show it until someone adds a panel. The background task starts from the app",
        "factory, so any script that builds the app without running it, such as the admin",
        "shell, will start it too; it's harmless but noisy in the logs. If that's a",
        "problem, I can gate it behind a flag.",
        "I also updated the README section on authentication, the architecture diagram in",
        "docs/auth.md, and the changelog under Unreleased. Nothing is committed yet; the",
        "changes are staged on the auth-split branch so you can review the diff first.",
        "Overall the change touches 27 files, adds 1,180 lines and removes 1,045. The",
        "largest single change is the move itself, which git shows as renames for most of",
        "the code, so the diff should be easier to read with rename detection on.",
    ]
)
LONG_PROMPT = (
    "A coding assistant finished a turn in the project accounts-api. Its last "
    f"message was: {LONG_MESSAGE} Say what was done."
)
LONG_RUNS = 15

Json = dict[str, Any]
_request_no = 0


def call(path: str, body: Json | None = None, timeout: float = 300) -> Json:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(ENDPOINT + path, data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def loaded(model: str) -> Json | None:
    for m in call("/api/ps")["models"]:
        if m["name"] == model:
            return m
    return None


def unload(model: str) -> None:
    if not loaded(model):
        return
    call("/api/generate", {"model": model, "keep_alive": 0})
    deadline = time.monotonic() + 30
    while loaded(model) and time.monotonic() < deadline:
        time.sleep(0.2)


def ask(
    model: str,
    thinks: bool,
    num_ctx: int | None = NUM_CTX,
    keep_alive: str | int | None = "10m",
    prompt: str = PROMPT,
) -> Json:
    """One fixed chat request. Returns wall time and Ollama's own timings, in ms.

    The user message starts with a request number, so Ollama can reuse the cached
    system prompt but must evaluate the rest, as it would for a real notification.
    """
    global _request_no
    _request_no += 1
    options: Json = {"temperature": 0, "seed": 1, "num_predict": 40}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    body: Json = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Request {_request_no}. {prompt}"},
        ],
        "options": options,
    }
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    if thinks:
        body["think"] = False
    start = time.perf_counter()
    resp = call("/api/chat", body)
    wall = (time.perf_counter() - start) * 1000
    return {
        "wall_ms": round(wall, 1),
        "load_ms": round(resp.get("load_duration", 0) / 1e6, 1),
        "prompt_ms": round(resp.get("prompt_eval_duration", 0) / 1e6, 1),
        "eval_ms": round(resp.get("eval_duration", 0) / 1e6, 1),
        "eval_count": resp.get("eval_count", 0),
        "prompt_count": resp.get("prompt_eval_count", 0),
        "text": resp["message"]["content"].strip(),
    }


def ps_snapshot(model: str) -> Json:
    m = loaded(model)
    if m is None:
        return {"loaded": False}
    return {
        "loaded": True,
        "size": m["size"],
        "size_vram": m.get("size_vram"),
        "context_length": m.get("context_length"),
        "expires_at": m.get("expires_at"),
    }


def expires_in_s(model: str) -> float | None:
    m = loaded(model)
    if m is None or not m.get("expires_at"):
        return None
    expires = datetime.fromisoformat(m["expires_at"])
    return round((expires - datetime.now(expires.tzinfo)).total_seconds(), 1)


def pct(samples: list[float], q: int) -> float:
    if len(samples) == 1:
        return samples[0]
    return round(statistics.quantiles(samples, n=100, method="inclusive")[q - 1], 1)


class Log:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, record: Json) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(json.dumps(record), file=sys.stderr)


def model_info(model: str, tags: dict[str, Json]) -> Json:
    show = call("/api/show", {"model": model})
    max_ctx = next(
        (v for k, v in show.get("model_info", {}).items() if k.endswith(".context_length")),
        None,
    )
    return {
        "model": model,
        "digest": tags[model]["digest"],
        "disk_bytes": tags[model]["size"],
        "parameter_size": show["details"].get("parameter_size"),
        "quantization": show["details"].get("quantization_level"),
        "max_context": max_ctx,
        "thinks": "thinking" in show.get("capabilities", []),
    }


def measure(info: Json, log: Log) -> Json:
    model, thinks = info["model"], info["thinks"]
    out: Json = {"info": info}

    cold: list[Json] = []
    for i in range(COLD_RUNS):
        unload(model)
        r = ask(model, thinks)
        log.write({"model": model, "phase": "cold", "run": i, **r})
        cold.append(r)
    out["cold"] = cold

    warm = [ask(model, thinks) for _ in range(WARM_RUNS)]
    for i, r in enumerate(warm):
        log.write({"model": model, "phase": "warm", "run": i, **r})
    out["warm"] = warm
    out["sample_text"] = warm[0]["text"]

    long = [ask(model, thinks, prompt=LONG_PROMPT) for _ in range(LONG_RUNS)]
    for i, r in enumerate(long):
        log.write({"model": model, "phase": "warm_long", "run": i, **r})
    out["warm_long"] = long

    sweep: list[Json] = []
    for ctx in CTX_SWEEP:
        switch = ask(model, thinks, num_ctx=ctx)
        runs = [ask(model, thinks, num_ctx=ctx) for _ in range(CTX_RUNS)]
        snap = ps_snapshot(model)
        rec = {
            "num_ctx": ctx,
            "switch": switch,
            "warm_p50": pct([r["wall_ms"] for r in runs], 50),
            **snap,
        }
        log.write({"model": model, "phase": "num_ctx", **rec})
        sweep.append(rec)
    out["num_ctx"] = sweep

    # Back to the configured context so eviction starts from the same state.
    ask(model, thinks)
    evicted: list[Json] = []
    for i in range(EVICT_RUNS):
        ask(model, thinks, keep_alive=f"{EVICT_KEEP_ALIVE_S}s")
        idle_start = time.monotonic()
        while loaded(model) and time.monotonic() - idle_start < 60:
            time.sleep(0.2)
        gone_after = round(time.monotonic() - idle_start, 1)
        r = ask(model, thinks)
        rec = {"run": i, "evicted_after_s": gone_after, **r}
        log.write({"model": model, "phase": "evicted", **rec})
        evicted.append(rec)
    out["evicted"] = evicted

    out["keep_warm"] = keep_warm_checks(model, thinks, log)
    unload(model)
    return out


def keep_warm_checks(model: str, thinks: bool, log: Log) -> Json:
    checks: Json = {}
    ask(model, thinks, keep_alive="1h")
    checks["after_1h_request_s"] = expires_in_s(model)

    time.sleep(3)
    call("/api/ps")
    call("/api/show", {"model": model})
    checks["after_ps_and_show_s"] = expires_in_s(model)

    ask(model, thinks, keep_alive=None)
    checks["after_request_without_keep_alive_s"] = expires_in_s(model)

    # A load-only ping (no prompt) like a keep-warm task might send.
    ask(model, thinks, keep_alive="1h")
    before = ps_snapshot(model)
    start = time.perf_counter()
    call("/api/generate", {"model": model, "keep_alive": "1h"})
    bare_ms = round((time.perf_counter() - start) * 1000, 1)
    after_bare = ps_snapshot(model)
    checks["bare_ping"] = {
        "ms": bare_ms,
        "context_before": before.get("context_length"),
        "context_after": after_bare.get("context_length"),
        "size_after": after_bare.get("size"),
        "expires_in_s": expires_in_s(model),
    }

    ask(model, thinks, keep_alive="1h")
    start = time.perf_counter()
    call(
        "/api/generate",
        {"model": model, "keep_alive": "1h", "options": {"num_ctx": NUM_CTX}},
    )
    checks["ping_with_num_ctx"] = {
        "ms": round((time.perf_counter() - start) * 1000, 1),
        "context_after": ps_snapshot(model).get("context_length"),
        "expires_in_s": expires_in_s(model),
    }

    # The cost of asking whether the model is loaded before each request.
    ps_ms: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        call("/api/ps")
        ps_ms.append((time.perf_counter() - start) * 1000)
    checks["ps_p50_ms"] = pct(ps_ms, 50)
    checks["ps_p95_ms"] = pct(ps_ms, 95)
    log.write({"model": model, "phase": "keep_warm", **checks})
    return checks


def gib(n: int | None) -> str:
    return "—" if n is None else f"{n / 2**30:.2f}"


def summarize(env: Json, results: list[Json]) -> str:
    lines = [
        "# Ollama latency and memory spike",
        "",
        f"- Date: {env['date']}",
        f"- Ollama: {env['ollama_version']}",
        f"- Machine: {env['cpu']}, {env['memory_gib']} GiB, macOS {env['macos']}",
        f"- Load average at start: {env['load_avg']}",
        f"- Prompt: fixed chat request, temperature 0, num_predict 40, num_ctx {NUM_CTX}",
        "",
        "## Models",
        "",
        "| Model | Digest | Params | Quant | Disk GiB | Max context |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        i = r["info"]
        lines.append(
            f"| `{i['model']}` | `{i['digest'][:12]}` | {i['parameter_size']} "
            f"| {i['quantization']} | {gib(i['disk_bytes'])} | {i['max_context']} |"
        )
    lines += [
        "",
        "## Latency (wall ms, client side)",
        "",
        f"Cold is each of {COLD_RUNS} requests after an explicit unload; the first is "
        "the first load in this run. Evicted is the request after Ollama unloaded the "
        f"model itself ({EVICT_KEEP_ALIVE_S} s `keep_alive`). Warm is {WARM_RUNS} "
        "back-to-back requests.",
        "",
        "| Model | Cold first | Cold median | Cold load ms | Evicted median | Warm p50 "
        "| Warm p95 | Tokens out |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        cold = [c["wall_ms"] for c in r["cold"]]
        loads = [c["load_ms"] for c in r["cold"]]
        warm = [w["wall_ms"] for w in r["warm"]]
        ev = [e["wall_ms"] for e in r["evicted"]]
        toks = statistics.median(w["eval_count"] for w in r["warm"])
        lines.append(
            f"| `{r['info']['model']}` | {cold[0]:.0f} | {statistics.median(cold):.0f} "
            f"| {statistics.median(loads):.0f} | {statistics.median(ev):.0f} "
            f"| {pct(warm, 50):.0f} | {pct(warm, 95):.0f} | {toks:.0f} |"
        )
    lines += [
        "",
        "## Warm latency by input length (wall ms)",
        "",
        f"Short is the warm phase above; long is {LONG_RUNS} back-to-back requests with a "
        f"final message of {len(LONG_MESSAGE)} characters, at num_ctx {NUM_CTX}. Tokens in is "
        "`prompt_eval_count`: what Ollama evaluated after reusing its prompt cache.",
        "",
        "| Model | Short p50 | Short p95 | Tokens in | Long p50 | Long p95 | Tokens in |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        warm = [w["wall_ms"] for w in r["warm"]]
        long = [w["wall_ms"] for w in r["warm_long"]]
        short_in = statistics.median(w["prompt_count"] for w in r["warm"])
        long_in = statistics.median(w["prompt_count"] for w in r["warm_long"])
        lines.append(
            f"| `{r['info']['model']}` | {pct(warm, 50):.0f} | {pct(warm, 95):.0f} "
            f"| {short_in:.0f} | {pct(long, 50):.0f} | {pct(long, 95):.0f} | {long_in:.0f} |"
        )
    lines += [
        "",
        "## num_ctx: resident memory and latency",
        "",
        "`size` and `context_length` from /api/ps. Switch is the request that changed "
        "the context, which makes Ollama reload the model.",
        "",
        "| Model | num_ctx sent | Context loaded | Resident GiB | In GPU GiB | Switch ms "
        "| Warm p50 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        for s in r["num_ctx"]:
            sent = "default" if s["num_ctx"] is None else s["num_ctx"]
            lines.append(
                f"| `{r['info']['model']}` | {sent} | {s.get('context_length')} "
                f"| {gib(s.get('size'))} | {gib(s.get('size_vram'))} "
                f"| {s['switch']['wall_ms']:.0f} | {s['warm_p50']:.0f} |"
            )
    lines += [
        "",
        "## What keeps a model warm",
        "",
        "Seconds until `expires_at` after each step.",
        "",
        "| Model | After 1h request | After /api/ps + /api/show | After request "
        "without keep_alive | Bare ping ms | Bare ping context | Ping with num_ctx ms "
        "| Its context | /api/ps p50 / p95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        k = r["keep_warm"]
        b, p = k["bare_ping"], k["ping_with_num_ctx"]
        lines.append(
            f"| `{r['info']['model']}` | {k['after_1h_request_s']} "
            f"| {k['after_ps_and_show_s']} | {k['after_request_without_keep_alive_s']} "
            f"| {b['ms']:.0f} | {b['context_before']} → {b['context_after']} "
            f"| {p['ms']:.0f} | {p['context_after']} "
            f"| {k['ps_p50_ms']:.1f} / {k['ps_p95_ms']:.1f} |"
        )
    lines += ["", "## Sample output (first warm request)", ""]
    for r in results:
        lines.append(f"- `{r['info']['model']}`: {r['sample_text']}")
    return "\n".join(lines) + "\n"


def environment() -> Json:
    def sysctl(name: str) -> str:
        return subprocess.run(
            ["sysctl", "-n", name], capture_output=True, text=True, check=True
        ).stdout.strip()

    return {
        "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ollama_version": call("/api/version")["version"],
        "cpu": sysctl("machdep.cpu.brand_string"),
        "memory_gib": int(sysctl("hw.memsize")) // 2**30,
        "macos": platform.mac_ver()[0],
        "load_avg": ", ".join(f"{x:.1f}" for x in os.getloadavg()),
    }


def stop(signum: int, frame: object) -> None:
    sys.exit("Stopped")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    # Ollama lists an untagged model as `<name>:latest`.
    models = [m if ":" in m else f"{m}:latest" for m in sys.argv[2:]] or DEFAULT_MODELS
    try:
        env = environment()
    except urllib.error.URLError as e:
        sys.exit(f"Ollama isn't reachable at {ENDPOINT}: {e}")
    tags = {m["name"]: m for m in call("/api/tags")["models"]}
    missing = [m for m in models if m not in tags]
    if missing:
        sys.exit(f"Pull these first: {' '.join(missing)}")

    # Turn SIGTERM into an exception so the unload in `finally` still runs.
    signal.signal(signal.SIGTERM, stop)
    log = Log(out_dir / "samples.jsonl")
    log.write({"phase": "env", **env})
    results: list[Json] = []
    try:
        for model in models:
            results.append(measure(model_info(model, tags), log))
    finally:
        for model in models:
            unload(model)
    (out_dir / "results.json").write_text(json.dumps({"env": env, "results": results}, indent=2))
    summary = summarize(env, results)
    (out_dir / "summary.md").write_text(summary)
    print(summary)


if __name__ == "__main__":
    main()
