"""The eval's cases: synthetic hook payloads, and what a good detail must and mustn't say.

Each file in `cases/` is named after the event its cases produce (`finished`,
`question`, `permission`) and holds a JSON list of cases. A case's payload goes
through the daemon's own classification and request building, so the model sees
exactly what it would see in use. Format: evals/README.md.
"""

import hashlib
import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cc_notify_tts.events import EventKind, classify
from cc_notify_tts.model_wording import MODEL_EVENTS, Request, request_for
from cc_notify_tts.sessions import SessionStore
from cc_notify_tts.wording import Wording, project_name, word

CASES_DIR = Path(__file__).parent / "cases"


class CaseError(ValueError):
    """A case file that can't be used as written."""


@dataclass(frozen=True)
class Case:
    id: str
    kind: EventKind
    # What makes the case hard: "numbers", "names", "long input", "lead-in"…
    traps: tuple[str, ...]
    # Each group lists alternatives; the detail must contain one from every group.
    required: tuple[tuple[str, ...], ...]
    # Words or phrases the detail must not contain: plausible but wrong.
    forbidden: tuple[str, ...]
    request: Request
    wording: Wording
    project: str


def load_cases(folder: Path = CASES_DIR) -> list[Case]:
    """Every case in `folder`, in file then list order. Raises `CaseError` on a bad case."""
    cases: list[Case] = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(Path(tmp))
        for path in sorted(folder.glob("*.json")):
            try:
                kind = EventKind(path.stem)
            except ValueError:
                raise CaseError(f"{path.name}: not named after an event") from None
            if kind not in MODEL_EVENTS:
                raise CaseError(f"{path.name}: {kind} keeps the template, so there's no model")
            entries: Any = json.loads(path.read_text())
            if not isinstance(entries, list):
                raise CaseError(f"{path.name}: expected a list of cases")
            for i, entry in enumerate(entries):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                case = _case(entry, kind, store, f"{path.name}[{i}]", {c.id for c in cases})
                cases.append(case)
    return cases


def cases_digest(folder: Path = CASES_DIR) -> str:
    """A short hash of the case files, so results say which case set they scored."""
    h = hashlib.sha256()
    for path in sorted(folder.glob("*.json")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _case(entry: Any, kind: EventKind, store: SessionStore, where: str, seen: set[str]) -> Case:
    if not isinstance(entry, Mapping):
        raise CaseError(f"{where}: expected an object")
    fields: Mapping[str, Any] = entry  # pyright: ignore[reportUnknownVariableType]
    case_id = fields.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise CaseError(f"{where}: missing id")
    if case_id in seen:
        raise CaseError(f"{where}: duplicate case id {case_id!r}")
    where = f"{where} ({case_id})"
    raw_payload = fields.get("payload")
    if not isinstance(raw_payload, Mapping):
        raise CaseError(f"{where}: missing payload")
    payload: dict[str, Any] = dict(raw_payload)  # pyright: ignore[reportUnknownArgumentType]
    # A long final message may be written as a list of lines.
    message = payload.get("last_assistant_message")
    if isinstance(message, list):
        lines = _strings(message, where, "message lines", allow_empty=True)
        payload["last_assistant_message"] = "\n".join(lines)
    payload.setdefault("session_id", f"eval-{case_id}")

    note = classify(store, payload, now=0.0)
    if note is None or note.kind is not kind:
        got = "nothing" if note is None else note.kind
        raise CaseError(f"{where}: the payload classifies as {got}, not {kind}")
    request = request_for(note)
    if request is None:
        raise CaseError(f"{where}: the payload keeps the template")

    required = fields.get("required")
    if not isinstance(required, list) or not required:
        raise CaseError(f"{where}: required must list at least one group")
    groups = tuple(
        tuple(_strings(group, where, "required"))
        for group in required  # pyright: ignore[reportUnknownVariableType]
    )
    if any(not group for group in groups):
        raise CaseError(f"{where}: a required group is empty")
    forbidden = tuple(_strings(fields.get("forbidden"), where, "forbidden"))
    if not forbidden:
        raise CaseError(f"{where}: forbidden must list at least one phrase")
    return Case(
        id=case_id,
        kind=kind,
        traps=tuple(_strings(fields.get("traps", []), where, "traps")),
        required=groups,
        forbidden=forbidden,
        request=request,
        wording=word(note),
        project=project_name(note.cwd),
    )


def _strings(value: Any, where: str, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(v, str) and (v or allow_empty)
        for v in value  # pyright: ignore[reportUnknownVariableType]
    ):
        raise CaseError(f"{where}: {name} must be a list of non-empty strings")
    return list(value)  # pyright: ignore[reportUnknownArgumentType]
