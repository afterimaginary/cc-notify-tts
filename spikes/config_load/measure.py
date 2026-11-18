"""Time `config.load` as it runs on every event.

Run from the repository root:

    uv run python spikes/config_load/measure.py

Prints p50, p95 and max per load for a missing file, the spec's example file
(every key) and a file with problems, plus the cost of importing the module in a
fresh process. Writes nothing outside a temporary directory.
"""

import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cc_notify_tts import config

RUNS = 5000
ROOT = Path(__file__).resolve().parents[2]


def spec_example() -> str:
    text = (ROOT / "docs" / "specs" / "config.md").read_text()
    return text.split("```toml\n", 1)[1].split("```", 1)[0]


def time_loads(path: Path) -> list[float]:
    config.load(path)  # Warm the file cache, as every load after the first is.
    samples: list[float] = []
    for _ in range(RUNS):
        start = time.perf_counter()
        config.load(path)
        samples.append((time.perf_counter() - start) * 1e6)
    return samples


def import_cost() -> list[float]:
    code = (
        "import time; s = time.perf_counter(); import cc_notify_tts.config; "
        "print((time.perf_counter() - s) * 1e3)"
    )
    return [
        float(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout)
        for _ in range(20)
    ]


def summary(samples: list[float], unit: str) -> str:
    q = statistics.quantiles(samples, n=100)
    return f"p50 {q[49]:.1f} {unit}, p95 {q[94]:.1f} {unit}, max {max(samples):.1f} {unit}"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        example = Path(tmp) / "example.toml"
        example.write_text(spec_example())
        problems = Path(tmp) / "problems.toml"
        problems.write_text(
            'volume = 3\n[presence]\nidle_seconds = -1\n[events.finished]\nspeak = "sometimes"\n'
        )
        cases = {
            "missing file": Path(tmp) / "missing.toml",
            "spec example (every key)": example,
            "three problems": problems,
        }
        print(f"{RUNS} loads each, microseconds per load:")
        for name, path in cases.items():
            print(f"  {name}: {summary(time_loads(path), 'µs')}")
    print(f"Import in a fresh process, 20 runs: {summary(import_cost(), 'ms')}")


if __name__ == "__main__":
    main()
