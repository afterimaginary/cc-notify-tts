"""The Markdown results table for an eval run."""

from evals.results import BUDGET_MS, ModelRun, Results
from evals.score import PROMPT_WORDS, Score, Summary

_EVENTS = ("finished", "question", "permission")
_ANSWER_CHARS = 140


def render(results: Results) -> str:
    counts = results.case_counts
    total = sum(counts.values())
    by_kind = ", ".join(f"{counts.get(e, 0)} {e}" for e in _EVENTS)
    settings = results.settings
    lines = [
        f"# Wording eval, {results.date}",
        "",
        f"Ollama {results.ollama_version} on {results.machine}. "
        f"{total} cases ({by_kind}), case set `{results.cases_digest}`. "
        f"Every request: temperature {settings.get('temperature')}, "
        f"`num_ctx` {settings.get('num_ctx')}, at most {settings.get('max_tokens')} "
        "output tokens, thinking off. Latency is the client's wall time per request, "
        "after loading the model and one warm-up request.",
        "",
        "## Results",
        "",
        "| Model | Pass | Spoken | Detail kept | Invented | Over length | Lead-ins spoken "
        "| Lead-ins rejected "
        f"| p50 ms | p95 ms | Over {BUDGET_MS / 1000:g} s | Resident |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    summaries = [(run, run.summary()) for run in results.runs]
    for run, s in summaries:
        lines.append(
            f"| `{run.model}` | {_share(s.passed, s.cases)} | {_share(s.spoken, s.cases)} "
            f"| {_share(s.kept, s.cases)} | {s.invented} | {s.over_length} "
            f"| {s.lead_ins_spoken} | {s.lead_ins_rejected} "
            f"| {s.p50_ms:.0f} | {s.p95_ms:.0f} | {s.over_budget} "
            f"| {_gib(run.resident_bytes)} |"
        )
    words = ", ".join(f"{k} {v}" for k, v in PROMPT_WORDS.items())
    lines += [
        "",
        "- **Pass:** spoken, with every required detail, nothing forbidden, and within "
        f"the length the prompt asks for ({words} words).",
        "- **Spoken:** passed the guards, so the daemon would speak the model's detail "
        "instead of the template's.",
        "- **Detail kept:** spoken, with every required detail.",
        "- **Invented:** spoken, with something the case forbids (a plausible but wrong "
        "claim, name or number).",
        "- **Over length:** spoken, but longer than the prompt asks.",
        "- **Lead-ins spoken:** spoken details that open by introducing themselves "
        '("Here\'s what was done: …"), which is accurate but wordy.',
        "- **Lead-ins rejected:** answers that open with a lead-in and that the guards "
        "rejected, for any reason (nothing after it, too long, a pronoun).",
        f"- **Over {BUDGET_MS / 1000:g} s:** requests slower than the daemon's wording "
        "timeout, which would have spoken the template.",
        "",
        "## By event",
        "",
        "Passed of cases, per event.",
        "",
        "| Model | " + " | ".join(_EVENTS) + " |",
        "| --- |" + " --- |" * len(_EVENTS),
    ]
    for run, s in summaries:
        cells = [_share(*s.by_event.get(e, [0, 0])) for e in _EVENTS]
        lines.append(f"| `{run.model}` | " + " | ".join(cells) + " |")

    lines += _rejections(summaries)
    lines += [
        "",
        "## Models",
        "",
        "| Model | Digest | Context | Resident | In GPU memory | Warm-up ms | Load average |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run, _ in summaries:
        lines.append(
            f"| `{run.model}` | `{run.digest}` | {run.context_length} "
            f"| {_gib(run.resident_bytes)} | {_gib(run.vram_bytes)} | {run.warm_up_ms:.0f} "
            f"| {run.load_average:.1f} |"
        )
    lines += ["", "## Answers that didn't pass"]
    for run, _ in summaries:
        lines += ["", f"### `{run.model}`", ""]
        failing = [s for s in run.scores if not s.passed]
        if not failing:
            lines.append("None.")
            continue
        lines += ["| Case | Why | Answer |", "| --- | --- | --- |"]
        lines += [_failure_row(s) for s in failing]
    return "\n".join(lines) + "\n"


def _rejections(summaries: list[tuple[ModelRun, Summary]]) -> list[str]:
    categories = sorted({c for _, s in summaries for c in s.rejections})
    if not categories:
        return []
    lines = [
        "",
        "## Guard rejections",
        "",
        "Answers the guards rejected, by the guard that rejected them. The template is "
        "spoken instead.",
        "",
        "| Model | " + " | ".join(categories) + " |",
        "| --- |" + " --- |" * len(categories),
    ]
    for run, s in summaries:
        cells = [str(s.rejections.get(c, 0)) for c in categories]
        lines.append(f"| `{run.model}` | " + " | ".join(cells) + " |")
    return lines


def _failure_row(s: Score) -> str:
    why = "; ".join(s.failures())
    answer = " ".join(s.answer.split())
    if len(answer) > _ANSWER_CHARS:
        answer = answer[:_ANSWER_CHARS] + "…"
    return f"| `{s.case_id}` | {_cell(why)} | {_cell(answer) or '(none)'} |"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _share(n: int, total: int) -> str:
    return f"{n}/{total} ({n / total:.0%})" if total else "0/0"


def _gib(n: int) -> str:
    return f"{n / 2**30:.2f} GiB" if n else "unknown"
