"""Score recorded eval runs again with today's guards and case lists, without a model.

    uv run python -m evals.rescore evals/results/2026-11-18-3.json

The guards (ADR-0015) and the cases' `required` and `forbidden` lists change after
a run is recorded, which leaves its report scoring answers the way the daemon no
longer would. This scores every recorded answer again and rewrites the run's
`.json` and `.md` in place; git keeps the earlier scoring. Answers, latency,
memory and digests stay as recorded, and the report says when it was re-scored.
A run whose report carries a hand-written note on how it was scored, such as
ADR-0021's "before" run, is skipped.

It assumes each case's payload is unchanged since the run, so the recorded answer
is still an answer to the prompt the case builds. Cases are added, not edited.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path

from evals.cases import Case, CaseError, load_cases
from evals.report import render
from evals.results import Results, load_results
from evals.score import Score, rejection, score

_RESCORED = "Re-scored on"


def rescore(results: Results, cases: Sequence[Case], day: str) -> Results:
    """`results` with every answer scored by the current guards and `cases`."""
    by_id = {c.id: c for c in cases}

    def again(s: Score) -> Score:
        if not s.spoken and rejection(s.reason) == "error":
            return s  # Ollama didn't answer, so there's nothing to score.
        case = by_id.get(s.case_id)
        if case is None:
            raise CaseError(f"{s.case_id}: not a case any more, so its answer can't be scored")
        return score(case, s.answer)

    runs = tuple(replace(run, scores=tuple(again(s) for s in run.scores)) for run in results.runs)
    note = (
        f"{_RESCORED} {day} from the recorded answers, with the guards and case lists "
        "of that day. The answers, latency and memory are the run's own."
    )
    return replace(results, runs=runs, scoring=note)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.rescore", description=__doc__)
    parser.add_argument("results", nargs="+", type=Path, help="results .json files")
    args = parser.parse_args(argv)
    cases = load_cases()
    today = date.today().isoformat()
    paths: list[Path] = args.results
    for path in paths:
        recorded = load_results(path)
        if recorded.scoring and not recorded.scoring.startswith(_RESCORED):
            # A hand-written note keeps a run as scored, such as ADR-0021's "before" run.
            print(f"{path}: skipped, kept as scored: {recorded.scoring}", file=sys.stderr)
            continue
        rescored = rescore(recorded, cases, today)
        path.write_text(json.dumps(rescored.to_json(), indent=2) + "\n")
        path.with_suffix(".md").write_text(render(rescored))
        print(path.with_suffix(".md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
