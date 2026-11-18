"""What an eval run produced, and its JSON form on disk."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cc_notify_tts.config import Wording as WordingConfig
from cc_notify_tts.events import EventKind
from evals.score import Score, Summary, summarize

# The daemon's wording timeout: a slower answer would have been the template.
BUDGET_MS = WordingConfig().timeout_seconds * 1000


@dataclass(frozen=True)
class ModelRun:
    model: str
    digest: str
    resident_bytes: int
    vram_bytes: int
    context_length: int
    warm_up_ms: float
    # The 1-minute load average when the run started: latency moves with it (ADR-0003).
    load_average: float
    latencies_ms: tuple[float, ...]
    scores: tuple[Score, ...]

    def summary(self) -> Summary:
        return summarize(self.scores, self.latencies_ms, BUDGET_MS)


@dataclass(frozen=True)
class Results:
    date: str
    ollama_version: str
    machine: str
    cases_digest: str
    case_counts: dict[str, int]
    settings: dict[str, Any]
    runs: tuple[ModelRun, ...]

    def to_json(self) -> dict[str, Any]:
        runs = [
            {
                **{k: v for k, v in asdict(r).items() if k not in ("scores", "latencies_ms")},
                "summary": asdict(r.summary()),
                "latencies_ms": list(r.latencies_ms),
                "scores": [{**asdict(s), "passed": s.passed} for s in r.scores],
            }
            for r in self.runs
        ]
        return {**asdict(self), "runs": runs}


def load_results(path: Path) -> Results:
    data: dict[str, Any] = json.loads(path.read_text())
    runs = tuple(
        ModelRun(
            model=r["model"],
            digest=r["digest"],
            resident_bytes=r["resident_bytes"],
            vram_bytes=r["vram_bytes"],
            context_length=r["context_length"],
            warm_up_ms=r["warm_up_ms"],
            load_average=r["load_average"],
            latencies_ms=tuple(r["latencies_ms"]),
            scores=tuple(
                Score(
                    case_id=s["case_id"],
                    kind=EventKind(s["kind"]),
                    answer=s["answer"],
                    detail=s["detail"],
                    reason=s["reason"],
                    missing=tuple(s["missing"]),
                    invented=tuple(s["invented"]),
                    words=s["words"],
                    lead_in=s["lead_in"],
                )
                for s in r["scores"]
            ),
        )
        for r in data["runs"]
    )
    return Results(
        date=data["date"],
        ollama_version=data["ollama_version"],
        machine=data["machine"],
        cases_digest=data["cases_digest"],
        case_counts=data["case_counts"],
        settings=data["settings"],
        runs=runs,
    )
