"""When each stage of one event happened, logged as one line once the event is done.

Every event the daemon accepts gets a `Timeline`. The hook shim's two marks come
in the `event` request (`fired_at` and `sent_at`); the daemon adds its own as the
event moves through the inbox, the gate, wording, the queue and speech, and logs
the whole timeline as one `timing {...}` line in `daemon.log` when the event
ends: with no notification, silenced, replaced, dropped, or played. `summarize`
reads those lines back and gives p50 and p95 per stage (ADR-0019).

All marks are wall-clock Unix seconds, `time.time()`, the same clock the shim
uses for `fired_at`, so marks from the two processes compare directly.
"""

import itertools
import json
import logging
import math
import statistics
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# The stages, in the order an event that is spoken goes through them.
STAGES = (
    "hook",  # the hook process started (the shim's `fired_at`)
    "sent",  # the shim sent the event to the socket
    "received",  # the daemon accepted it
    "dequeued",  # the event worker took it from the inbox, after the ordering hold
    "gated",  # classified and through the gate
    "worded",  # the sentence is written and queued
    "synthesizing",  # the speech worker took it from the queue
    "synthesized",  # the whole sentence is synthesized to a WAV file
    "playing",  # `afplay` started
    "ended",  # the sound has finished (`afplay` may still be exiting, ADR-0022)
)
LOG_MARKER = "timing "

_ids = itertools.count(1)


@dataclass
class Timeline:
    """One event's marks. Owned by one thread at a time: socket, event worker, then speech."""

    hook: str
    session_id: str
    marks: dict[str, float] = field(default_factory=dict[str, float])
    kind: str | None = None
    # How long the synthesized sentence plays, once it's known.
    audio_seconds: float | None = None
    id: int = field(default_factory=lambda: next(_ids))

    def mark(self, stage: str, at: float | None = None) -> None:
        self.marks[stage] = time.time() if at is None else at

    def finish(self, outcome: str) -> None:
        """Log the timeline. `outcome` says how the event ended, e.g. "played"."""
        record = {
            "event": self.id,
            "hook": self.hook,
            "session": self.session_id,
            "kind": self.kind,
            "outcome": outcome,
            "audio_seconds": None if self.audio_seconds is None else round(self.audio_seconds, 3),
            "marks": {stage: round(at, 4) for stage, at in self.marks.items()},
        }
        log.info("%s%s", LOG_MARKER, json.dumps(record))


def parse(lines: Iterable[str]) -> list[dict[str, Any]]:
    """The timing records in log lines; other lines, and damaged records, are skipped."""
    records: list[dict[str, Any]] = []
    for line in lines:
        _, marker, rest = line.partition(LOG_MARKER)
        if not marker or not rest.startswith("{"):
            continue
        try:
            record: Any = json.loads(rest)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        fields: dict[str, Any] = record  # pyright: ignore[reportUnknownVariableType]
        if isinstance(fields.get("marks"), dict):
            records.append(fields)
    return records


@dataclass(frozen=True)
class Span:
    name: str
    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float


def summarize(records: Iterable[Mapping[str, Any]]) -> list[Span]:
    """p50, p95 and max per stage, plus hook to playing and afplay's overhead.

    A stage's time runs from the stage before it, so the rows of an event that
    went through every stage add up to its total. Records without the earlier
    mark (a `fire`, which has no hook) leave that row out. afplay's overhead is
    `playing` to `ended` less the audio's length: the allowance for its start-up
    (ADR-0022), or more if it took longer than that to finish.
    """
    samples: dict[str, list[float]] = {}
    for record in records:
        marks: Mapping[str, Any] = record["marks"]
        for before, after in itertools.pairwise(STAGES):
            elapsed = _elapsed(marks, before, after)
            if elapsed is not None:
                samples.setdefault(f"{before} → {after}", []).append(elapsed)
        total = _elapsed(marks, "hook", "playing")
        if total is not None:
            samples.setdefault("hook → playing (total)", []).append(total)
        playback, audio = _elapsed(marks, "playing", "ended"), record.get("audio_seconds")
        if playback is not None and isinstance(audio, int | float):
            samples.setdefault("afplay overhead", []).append(playback - audio * 1000)
    return [
        Span(name, len(s), percentile(s, 50), percentile(s, 95), max(s))
        for name, s in samples.items()
    ]


def percentile(samples: list[float], pct: int) -> float:
    if len(samples) == 1:
        return samples[0]
    return statistics.quantiles(samples, n=100, method="inclusive")[pct - 1]


def _elapsed(marks: Mapping[str, Any], before: str, after: str) -> float | None:
    """Milliseconds from `before` to `after`, or None if either is missing."""
    start, end = marks.get(before), marks.get(after)
    if not isinstance(start, int | float) or not isinstance(end, int | float):
        return None
    if not math.isfinite(start) or not math.isfinite(end):
        return None
    return (end - start) * 1000
