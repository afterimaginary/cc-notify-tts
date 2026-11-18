"""Deterministic checks of one model answer, and the numbers a model run adds up to.

An answer passes when the daemon would speak it (it passes the guards in
`model_wording`), it keeps every required detail, says nothing forbidden, and
stays within the length the prompt asked for. The guards themselves allow a
little more length than the prompt asks, so "within length" measures how well a
model follows the prompt, not whether it gets spoken.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from cc_notify_tts.events import EventKind
from cc_notify_tts.model_wording import guard
from evals.cases import Case

# The length each prompt asks for, in words (the prompts in `model_wording`).
PROMPT_WORDS = {EventKind.FINISHED: 15, EventKind.QUESTION: 15, EventKind.PERMISSION: 10}
# A spoken detail that opens by introducing itself ("Here's what was done: …").
# Accurate, but wordy when spoken. A lead-in with nothing after it is rejected
# by the guards instead, and counted with the rejections.
_LEAD_IN = re.compile(
    r"^\W*(?:here(?:'s| is| are)|this is|these are|in summary|in short|to recap|summary|"
    r"overall|so|okay|ok|sure|done|all set|result|changes|update|what was done)\b"
    r"[^:]{0,40}:\s*\S",
    re.IGNORECASE,
)
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


@dataclass(frozen=True)
class Score:
    case_id: str
    kind: EventKind
    # The model's raw answer, and the detail the daemon would speak, if any.
    answer: str
    detail: str | None
    # The guards' reason, "ok" when the detail is spoken.
    reason: str
    # The first alternative of each required group the detail lacks.
    missing: tuple[str, ...] = ()
    # Forbidden phrases the detail contains.
    invented: tuple[str, ...] = ()
    # Words in the detail, without code's own suffix ("And one more question.").
    words: int = 0
    lead_in: bool = False

    @property
    def spoken(self) -> bool:
        return self.detail is not None

    @property
    def kept(self) -> bool:
        return self.spoken and not self.missing

    @property
    def within_length(self) -> bool:
        return self.spoken and self.words <= PROMPT_WORDS[self.kind]

    @property
    def passed(self) -> bool:
        return self.kept and not self.invented and self.within_length

    def failures(self) -> list[str]:
        """Why the answer fails, one entry per check; empty when it passes."""
        if not self.spoken:
            return [f"guard: {self.reason}"]
        found: list[str] = []
        if self.missing:
            found.append("missing " + ", ".join(repr(m) for m in self.missing))
        if self.invented:
            found.append("invented " + ", ".join(repr(i) for i in self.invented))
        if not self.within_length:
            found.append(f"{self.words} words, asked for {PROMPT_WORDS[self.kind]}")
        return found


def score(case: Case, answer: str) -> Score:
    checked = guard(answer, case.request, case.wording, case.project)
    if checked.detail is None:
        return Score(case.id, case.kind, answer, None, checked.reason)
    body = checked.detail
    if case.request.suffix:
        body = body.removesuffix(case.request.suffix).strip()
    return Score(
        case.id,
        case.kind,
        answer,
        checked.detail,
        checked.reason,
        missing=tuple(g[0] for g in case.required if not any(contains(body, a) for a in g)),
        invented=tuple(f for f in case.forbidden if contains(body, f)),
        words=len(body.split()),
        lead_in=_LEAD_IN.match(body.translate(_QUOTES)) is not None,
    )


def failed(case: Case, error: str) -> Score:
    """The score of a request Ollama didn't answer: the template is spoken."""
    return Score(case.id, case.kind, "", None, f"Ollama failed: {error}")


def contains(text: str, phrase: str) -> bool:
    """Whether `text` holds `phrase` as whole words, ignoring case, quotes and hyphens.

    A phrase ending in `*` matches any word starting with it ("retr*" finds "retries").
    """
    stem = phrase.endswith("*")
    pattern = re.escape(_normal(phrase.removesuffix("*")))
    pattern = rf"(?<!\w){pattern}" + (r"\w*" if stem else r"(?!\w)")
    return re.search(pattern, _normal(text)) is not None


def rejection(reason: str) -> str:
    """The guard a rejected answer failed, as a short category."""
    if reason.startswith("Ollama failed"):
        return "error"
    if "pronoun" in reason:
        return "pronoun"
    if "isn't in the input" in reason:
        return "not in input"
    if "words, not" in reason:
        return "length"
    if "lead-in" in reason:
        return "lead-in"
    if reason == "empty answer":
        return "empty"
    return "other"


def percentile(values: Sequence[float], p: float) -> float:
    """The nearest-rank percentile: the smallest value with `p`% of values at or below it."""
    if not values:
        return math.nan
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


@dataclass(frozen=True)
class Summary:
    cases: int
    passed: int
    spoken: int
    kept: int
    invented: int
    over_length: int
    # Spoken details that open with a lead-in, and answers rejected for being only one.
    lead_ins_spoken: int
    lead_ins_rejected: int
    p50_ms: float
    p95_ms: float
    # Requests slower than the daemon's wording timeout, which would speak the template.
    over_budget: int
    rejections: dict[str, int] = field(default_factory=dict[str, int])
    # Per event: [passed, cases].
    by_event: dict[str, list[int]] = field(default_factory=dict[str, list[int]])


def summarize(scores: Sequence[Score], latencies_ms: Sequence[float], budget_ms: float) -> Summary:
    rejected = Counter(rejection(s.reason) for s in scores if not s.spoken)
    by_event: dict[str, list[int]] = {}
    for s in scores:
        counts = by_event.setdefault(s.kind.value, [0, 0])
        counts[0] += s.passed
        counts[1] += 1
    return Summary(
        cases=len(scores),
        passed=sum(s.passed for s in scores),
        spoken=sum(s.spoken for s in scores),
        kept=sum(s.kept for s in scores),
        invented=sum(bool(s.invented) for s in scores),
        over_length=sum(s.spoken and not s.within_length for s in scores),
        lead_ins_spoken=sum(s.lead_in for s in scores),
        lead_ins_rejected=rejected["lead-in"],
        p50_ms=percentile(latencies_ms, 50),
        p95_ms=percentile(latencies_ms, 95),
        over_budget=sum(ms > budget_ms for ms in latencies_ms),
        rejections=dict(sorted(rejected.items())),
        by_event=by_event,
    )


def _normal(text: str) -> str:
    return text.translate(_QUOTES).lower().replace("-", " ")
