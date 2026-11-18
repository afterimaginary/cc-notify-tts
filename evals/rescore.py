"""Score recorded eval runs again with today's guards and case lists, without a model.

    uv run python -m evals.rescore evals/results/*.json
    uv run python -m evals.rescore --before "Scored with the guards on main before …" FILE

The guards (ADR-0015) and the cases' `required` and `forbidden` lists change after
a run is recorded, which leaves its report scoring answers the way the daemon no
longer would. This scores every recorded answer again and rewrites the run's
`.json` and `.md` in place; git keeps the earlier scoring. Answers, latency,
memory and digests stay as recorded, and the report says when it was re-scored.

A run that shows a change's "before" keeps the scoring it was recorded with:
`--before NOTE` marks it so, with a note on which guards scored it, and a re-score
skips it from then on. `make ready` checks that every other run matches the
current guards.

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

BEFORE = 'Kept as a "before" run:'


def is_before(results: Results) -> bool:
    """Whether the run is kept as scored, to show a change's "before"."""
    return results.scoring.startswith(BEFORE)


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
        f"Re-scored on {day} from the recorded answers, with the guards and case lists "
        "of that day. The answers, latency and memory are the run's own."
    )
    return replace(results, runs=runs, scoring=note)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.rescore",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("results", nargs="+", type=Path, help="results .json files")
    parser.add_argument(
        "--before",
        metavar="NOTE",
        help="keep these runs as scored, to show a change's before; NOTE says which guards",
    )
    args = parser.parse_args(argv)
    cases = load_cases()
    today = date.today().isoformat()
    before: str | None = args.before
    paths: list[Path] = args.results
    for path in paths:
        recorded = load_results(path)
        if before is not None:
            updated = replace(recorded, scoring=f"{BEFORE} {before}")
        elif is_before(recorded):
            print(f"{path}: skipped, {recorded.scoring}", file=sys.stderr)
            continue
        else:
            updated = rescore(recorded, cases, today)
        path.write_text(json.dumps(updated.to_json(), indent=2) + "\n")
        path.with_suffix(".md").write_text(render(updated))
        print(path.with_suffix(".md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
