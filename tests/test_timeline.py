"""Per-event stage marks: the logged line, reading it back and the per-stage summary."""

import logging
from collections.abc import Mapping

import pytest

from cc_notify_tts.timeline import STAGES, Timeline, parse, percentile, summarize


def record(
    offsets_ms: Mapping[str, float], audio_seconds: float | None = None
) -> dict[str, object]:
    start = 1_763_000_000.0
    marks = {stage: start + ms / 1000 for stage, ms in offsets_ms.items()}
    return {"event": 1, "marks": marks, "audio_seconds": audio_seconds}


def test_finished_timeline_logs_one_line_that_reads_back(caplog: pytest.LogCaptureFixture) -> None:
    timeline = Timeline("Stop", "s1")
    timeline.mark("hook", 100.0)
    timeline.mark("received", 100.25)
    timeline.kind = "finished"
    timeline.audio_seconds = 1.5
    with caplog.at_level(logging.INFO):
        timeline.finish("played")

    lines = [f"2026-11-18 10:00:00,000 INFO cc_notify_tts.timeline: {m}" for m in caplog.messages]
    [read] = parse(["daemon 0.0.0 listening on /x", *lines])
    assert read == {
        "event": timeline.id,
        "hook": "Stop",
        "session": "s1",
        "kind": "finished",
        "outcome": "played",
        "audio_seconds": 1.5,
        "marks": {"hook": 100.0, "received": 100.25},
    }


def test_each_timeline_gets_its_own_id() -> None:
    assert Timeline("Stop", "s1").id != Timeline("Stop", "s1").id


def test_damaged_records_are_skipped() -> None:
    lines = ["x timing {not json", "x timing [1]", 'x timing {"marks": 3}', "x timing soon"]
    assert parse(lines) == []


def test_summary_times_each_stage_from_the_one_before() -> None:
    offsets = [0.0, 20, 22, 125, 130, 140, 141, 400, 401, 2401]
    full: dict[str, float] = dict(zip(STAGES, offsets, strict=True))
    spans = {s.name: s for s in summarize([record(full, audio_seconds=1.95)])}

    assert spans["hook → sent"].p50_ms == pytest.approx(20)
    assert spans["received → dequeued"].p50_ms == pytest.approx(103)
    assert spans["synthesizing → synthesized"].p50_ms == pytest.approx(259)
    assert spans["hook → playing (total)"].p50_ms == pytest.approx(401)
    # 2000 ms from playing to ended, of which 1950 ms is audio.
    assert spans["afplay overhead"].p50_ms == pytest.approx(50)
    stage_rows = [s for s in spans.values() if "→" in s.name and "total" not in s.name]
    assert sum(s.p50_ms for s in stage_rows) == pytest.approx(2401)


def test_summary_leaves_out_stages_an_event_never_reached() -> None:
    silenced = record({"hook": 0, "sent": 20, "received": 22, "dequeued": 125, "gated": 130})
    fired = record({"received": 0, "gated": 5, "worded": 9})
    spans = {s.name: s for s in summarize([silenced, fired])}

    assert spans["hook → sent"].count == 1
    assert spans["gated → worded"].count == 1
    assert "dequeued → gated" in spans
    assert "received → gated" not in spans
    assert "hook → playing (total)" not in spans


def test_percentiles() -> None:
    samples = [float(n) for n in range(1, 101)]
    assert percentile(samples, 50) == pytest.approx(50.5)
    assert percentile(samples, 95) == pytest.approx(95.05)
    assert percentile([7.0], 95) == 7.0
