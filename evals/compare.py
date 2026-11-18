"""Compare two eval runs model by model, to see run-to-run noise.

    uv run python -m evals.compare evals/results/A.json evals/results/B.json

Prints Markdown: each model both runs share, its numbers side by side, and the
cases whose answers differ.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.results import ModelRun, Results, load_results


def compare(a: Results, b: Results) -> str:
    lines = [f"# Eval runs compared: {a.date} (A) and {b.date} (B)", ""]
    if a.cases_digest != b.cases_digest:
        lines += [f"Different case sets: `{a.cases_digest}` and `{b.cases_digest}`.", ""]
    if a.ollama_version != b.ollama_version or a.machine != b.machine:
        lines += [
            f"A: Ollama {a.ollama_version} on {a.machine}. "
            f"B: Ollama {b.ollama_version} on {b.machine}.",
            "",
        ]
    runs_b = {run.model: run for run in b.runs}
    shared = [(run, runs_b[run.model]) for run in a.runs if run.model in runs_b]
    if not shared:
        return "\n".join([*lines, "No model is in both runs."]) + "\n"
    for run_a, run_b in shared:
        lines += _model(run_a, run_b)
    return "\n".join(lines) + "\n"


def _model(a: ModelRun, b: ModelRun) -> list[str]:
    sa, sb = a.summary(), b.summary()
    lines = [f"## `{a.model}`", ""]
    if a.digest != b.digest:
        lines += [f"Different digests: `{a.digest}` and `{b.digest}`.", ""]
    lines += ["| | A | B | B - A |", "| --- | --- | --- | --- |"]
    for name, x, y in (
        ("Pass", sa.passed, sb.passed),
        ("Spoken", sa.spoken, sb.spoken),
        ("Detail kept", sa.kept, sb.kept),
        ("Invented", sa.invented, sb.invented),
        ("Over length", sa.over_length, sb.over_length),
        ("Lead-ins spoken", sa.lead_ins_spoken, sb.lead_ins_spoken),
        ("Lead-ins rejected", sa.lead_ins_rejected, sb.lead_ins_rejected),
    ):
        lines.append(f"| {name} | {x} | {y} | {y - x:+d} |")
    for name, x, y in (
        ("p50 ms", sa.p50_ms, sb.p50_ms),
        ("p95 ms", sa.p95_ms, sb.p95_ms),
        ("Warm-up ms", a.warm_up_ms, b.warm_up_ms),
    ):
        lines.append(f"| {name} | {x:.0f} | {y:.0f} | {_relative(x, y)} |")
    x, y = a.load_average, b.load_average
    lines.append(f"| Load average | {x:.1f} | {y:.1f} | {y - x:+.1f} |")
    gib_a, gib_b = a.resident_bytes / 2**30, b.resident_bytes / 2**30
    lines.append(f"| Resident GiB | {gib_a:.2f} | {gib_b:.2f} | {gib_b - gib_a:+.2f} |")

    answers_b = {s.case_id: s for s in b.scores}
    changed = [
        (s, answers_b[s.case_id])
        for s in a.scores
        if s.case_id in answers_b and s.answer.strip() != answers_b[s.case_id].answer.strip()
    ]
    lines += ["", f"Answers that differ: {len(changed)} of {len(a.scores)}."]
    for x, y in changed:
        lines.append(f"- `{x.case_id}`: {_quote(x.answer)} → {_quote(y.answer)}")
    lines.append("")
    return lines


def _relative(x: float, y: float) -> str:
    return f"{(y - x) / x:+.0%}" if x else "n/a"


def _quote(answer: str) -> str:
    return "“" + " ".join(answer.split()) + "”"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.compare", description=__doc__)
    parser.add_argument("a", type=Path, help="the earlier results .json")
    parser.add_argument("b", type=Path, help="the later results .json")
    args = parser.parse_args(argv)
    print(compare(load_results(args.a), load_results(args.b)), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
