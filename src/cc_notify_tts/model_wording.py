"""Model wording: a local model writes the detail of a notification, behind guards.

Only the three events with prose to compress use it: a finished turn, a question
and a permission request. The frame and the project name always come from the
template (`wording.py`); the model writes only the detail, and never sees the
frame. Its answer goes through the guards in `guard`, and any failure, from a
missing Ollama to a guard, means the template's own detail is spoken.

The wording eval reuses `request_for` and `guard`, so it measures exactly what
the daemon speaks. Behaviour: docs/specs/spoken-messages.md.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cc_notify_tts.events import EventKind, Notification
from cc_notify_tts.ollama import NUM_CTX, Client, KeepWarm, OllamaError
from cc_notify_tts.wording import (
    ACTION_WORDS,
    MESSAGE_WORDS,
    QUESTION_WORDS,
    SLOT,
    Wording,
    admits_failure,
    capitalize,
    end_sentence,
    failing_tests,
    first_sentence,
    lower_first,
    more_questions,
    plain,
    project_name,
    strip_end,
)

MODEL_EVENTS = frozenset({EventKind.FINISHED, EventKind.QUESTION, EventKind.PERMISSION})
# At most this many characters of input: the end of a final message, which is
# usually its summary, or the start of anything else (ADR-0003).
INPUT_CHARS = 4000
# Characters of any one tool argument shown to the model.
ARGUMENT_CHARS = 500
# Output tokens: room for a sentence of the longest allowed length.
MAX_TOKENS = 40
# Fewer words than this says nothing.
MIN_WORDS = 2

SYSTEM_PROMPT = (
    "You write one part of a short spoken notification about a coding assistant's work. "
    "Reply with that part only: plain words, no markdown, no quotes, no preamble. "
    "Use only names, file names and numbers that appear in the input."
)

_FINISHED = (
    "The assistant finished its turn. Its last message is between the lines.\n"
    "---\n{material}\n---\n"
    "In one sentence of at most 15 words, say what was done. "
    "Start with a past-tense verb and leave out the subject. "
    # Without this, a turn that only answers a question is worded as work done
    # ("Updated retry limit to 3") (ADR-0036).
    "If nothing was changed and the message only answers a question, start with Explained."
)
_QUESTION = (
    "The assistant is asking the user a question. It is between the lines.\n"
    "---\n{material}\n---\n"
    "Ask it again as one short spoken question of at most 15 words."
)
_PERMISSION = (
    "The assistant asks permission to use a tool. The tool call is between the lines.\n"
    "---\n{material}\n---\n"
    "Say what the tool call would do as a phrase of at most 10 words that starts with "
    "a verb in its base form, with no subject and no full stop."
)

# Subject pronouns a detail mustn't start with: the frame already says who.
_PRONOUNS = frozenset(
    {"i", "we", "it", "he", "she", "they"}
    | {
        f"{p}'{s}"
        for p in ("i", "we", "it", "he", "she", "they")
        for s in ("m", "s", "ve", "ll", "d", "re")
    }
)
_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL | re.IGNORECASE)
_STRAIGHT_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
_LABEL = re.compile(r"^(?:sentence|summary|answer|output|detail|question|action)\s*:\s*", re.I)
# A word as written, keeping inner dots, slashes, dashes and apostrophes ("auth.py").
_TOKEN = re.compile(r"[\w][\w.'/-]*\w|\w")
_NUMBER_WORDS = {
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
}
_DIGITS = {digit: spelled for spelled, digit in _NUMBER_WORDS.items()}


@dataclass(frozen=True)
class Request:
    """What the model is asked, and the input its answer is checked against."""

    kind: EventKind
    system: str
    user: str
    # The text given to the model: every name and number in the answer must be in it.
    source: str
    # Code's own words after the model's detail ("And one more question.").
    suffix: str = ""
    # Whether the whole message says tests are still failing, not just `source`.
    tests_failing: bool = False


@dataclass(frozen=True)
class Checked:
    """A guarded detail, or None and why it was rejected."""

    detail: str | None
    reason: str


@dataclass(frozen=True)
class Attempt:
    """The detail to speak in place of the template's, if any, and why."""

    detail: str | None
    reason: str


def request_for(note: Notification) -> Request | None:
    """The model request for a notification, or None if this one keeps the template."""
    payload = note.payload
    suffix = ""
    tests_failing = False
    match note.kind:
        case EventKind.FINISHED:
            message = _text(payload, "last_assistant_message").strip()
            material = message[-INPUT_CHARS:]
            template = _FINISHED
            tests_failing = failing_tests(message) is not None
        case EventKind.QUESTION:
            if _text(payload, "tool_name") == "ExitPlanMode":
                return None
            material = _questions(payload)[:INPUT_CHARS]
            suffix = more_questions(_question_count(payload))
            template = _QUESTION
        case EventKind.PERMISSION:
            material = _tool_call(payload)[:INPUT_CHARS]
            template = _PERMISSION
        case _:
            return None
    if not material:
        return None
    user = template.format(material=material)
    return Request(note.kind, SYSTEM_PROMPT, user, material, suffix, tests_failing)


def guard(raw: str, request: Request, wording: Wording, project: str) -> Checked:
    """Clean the model's answer into a detail, or reject it.

    In order: the first line, without reasoning or a label; frame echoes
    stripped; the first sentence; then the checks for a leading subject
    pronoun, length, names and numbers not in the input, and a finished turn
    worded as a success when its message says tests still fail.
    """
    text = _first_line(_THINK.sub(" ", raw)).translate(_STRAIGHT_QUOTES)
    text = plain(_LABEL.sub("", text).strip().strip("\"'"))
    text = _strip_echo(text, wording.frame, project)
    if not text:
        return Checked(None, "empty answer")
    text = first_sentence(text).strip()
    # A lead-in ("Here's what was done:") introduces a detail; it isn't one.
    if text.endswith(":"):
        return Checked(None, "a lead-in, not a detail")
    words = text.split()
    first = words[0].lower().strip(",;:") if words else ""
    if first in _PRONOUNS:
        return Checked(None, f"starts with the pronoun {words[0]!r}")
    limit = _word_limit(request.kind)
    if not MIN_WORDS <= len(words) <= limit:
        return Checked(None, f"{len(words)} words, not {MIN_WORDS} to {limit}")
    if invented := _not_in_source(text, request.source):
        return Checked(None, f"{invented!r} isn't in the input")
    # A turn that ends with tests failing is never reported as a success (ADR-0021),
    # even when the sentence that says so is before the part the model was shown.
    if request.tests_failing and not admits_failure(text):
        return Checked(None, "leaves out that tests still fail")
    finished = _finish(text, request.kind)
    return Checked(f"{finished} {request.suffix}" if request.suffix else finished, "ok")


def write_detail(
    note: Notification,
    wording: Wording,
    client: Client,
    model: str,
    timeout: float,
    keep_warm: KeepWarm | None = None,
) -> Attempt:
    """Ask the model for the detail; None and the reason whenever the template should stay.

    The model is asked only when it's already loaded at the expected context, so
    a cold load never delays a notification; `keep_warm` then loads it for next
    time (ADR-0003).
    """
    request = request_for(note)
    if request is None:
        return Attempt(None, "template event")
    try:
        context = client.loaded_context(model)
    except OllamaError as e:
        return Attempt(None, f"Ollama unavailable: {e}")
    if context != NUM_CTX:
        if keep_warm is not None:
            keep_warm.load_soon(model)
        return Attempt(None, f"{model} isn't loaded yet")
    try:
        raw = client.chat(
            model, request.system, request.user, timeout=timeout, max_tokens=MAX_TOKENS
        )
    except OllamaError as e:
        return Attempt(None, f"Ollama failed: {e}")
    checked = guard(raw, request, wording, project_name(note.cwd))
    if checked.detail is None:
        return Attempt(None, f"guard: {checked.reason}: {raw.strip()[:200]!r}")
    return Attempt(checked.detail, "model")


def _question_count(payload: Mapping[str, Any]) -> int:
    raw: Any = _mapping(payload.get("tool_input")).get("questions")
    return len(raw) if isinstance(raw, list) else 0  # pyright: ignore[reportUnknownArgumentType]


def _questions(payload: Mapping[str, Any]) -> str:
    tool_input = _mapping(payload.get("tool_input"))
    raw: Any = tool_input.get("questions")
    lines: list[str] = []
    for item in raw if isinstance(raw, list) else []:  # pyright: ignore[reportUnknownVariableType]
        question = _mapping(item)
        text = _text(question, "question") or _text(question, "header")
        if not text:
            continue
        lines.append(text.strip())
        options: Any = question.get("options")
        labels = [
            _text(_mapping(o), "label")
            for o in (options if isinstance(options, list) else [])  # pyright: ignore[reportUnknownVariableType]
        ]
        if labels := [label for label in labels if label]:
            lines.append("Options: " + "; ".join(labels))
    return "\n".join(lines)


def _tool_call(payload: Mapping[str, Any]) -> str:
    tool = _text(payload, "tool_name")
    if not tool:
        return ""
    lines = [f"Tool: {tool}"]
    for key, value in _mapping(payload.get("tool_input")).items():
        shown = value if isinstance(value, str) else json.dumps(value)
        lines.append(f"{key}: {shown[:ARGUMENT_CHARS]}")
    return "\n".join(lines)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _strip_echo(text: str, frame: str, project: str) -> str:
    """Remove the frame, its fixed phrases or the project name from the start of the answer."""
    echoes = [strip_end(frame.split(SLOT)[0]).strip()]
    for piece in re.split(re.escape(SLOT) + "|" + re.escape(project), frame, flags=re.I):
        piece = piece.strip(" .!?,")
        if len(piece.split()) >= 2:
            echoes.append(piece)
    echoes.append(project)
    stripped = True
    while stripped and text:
        stripped = False
        for echo in echoes:
            rest = text[len(echo) :]
            if echo and text.lower().startswith(echo.lower()) and not rest[:1].isalnum():
                text = rest.lstrip(" .,:;!?-—")
                stripped = True
    return text


def _word_limit(kind: EventKind) -> int:
    return {EventKind.PERMISSION: ACTION_WORDS, EventKind.QUESTION: QUESTION_WORDS}.get(
        kind, MESSAGE_WORDS
    )


def _not_in_source(text: str, source: str) -> str | None:
    """The first name or number in `text` that `source` doesn't contain, if any."""
    haystack = source.lower()

    def found(word: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", haystack) is not None

    for i, token in enumerate(_TOKEN.findall(text)):
        word = token.removesuffix("'s")
        lower = word.lower()
        # A count may be written in digits or words: "3" and "three" are the same.
        if spelled := _NUMBER_WORDS.get(lower) or _DIGITS.get(lower):
            if not found(lower) and not found(spelled):
                return word
            continue
        # The first word is capitalized anyway, so only its inner capitals mark a name.
        capitals = word[1:] if i == 0 else word
        is_name = (
            any(c.isupper() for c in capitals)
            or any(c.isdigit() for c in word)
            or any(c in "._/" for c in word)
        )
        if is_name and not found(lower):
            return word
    return None


def _finish(text: str, kind: EventKind) -> str:
    match kind:
        case EventKind.PERMISSION:
            # Inline after "needs your okay to", which adds its own full stop.
            text = re.sub(r"^to\s+", "", text, flags=re.IGNORECASE)
            return lower_first(strip_end(text))
        case EventKind.QUESTION:
            return end_sentence(capitalize(text), "?")
        case _:
            return end_sentence(capitalize(text))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}  # pyright: ignore[reportUnknownVariableType]


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""
