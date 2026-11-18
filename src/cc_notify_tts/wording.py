"""Template wording: the sentence spoken for each notification, written by code alone.

`word` turns a notification into a frame (the fixed part, naming the project)
and an optional detail taken from the payload. A local model may later replace
the detail for some events; the frame always comes from here. Every template is
listed in docs/specs/spoken-messages.md.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from cc_notify_tts.events import EventKind, Notification
from cc_notify_tts.sessions import BackgroundItem

# Where a frame takes its detail inline. Frames without it take the detail as a
# second sentence, or say nothing more.
SLOT = "{detail}"

FALLBACK_PROJECT = "your project"
# Word caps for details taken from free text.
MESSAGE_WORDS = 20
QUESTION_WORDS = 20
ACTION_WORDS = 12
COMMAND_WORDS = 6
# Background work is listed by description up to this many items, then counted.
LISTED_BACKGROUND = 2

# StopFailure `error` values, as spoken after "It hit ...".
ERRORS = {
    "authentication_failed": "an authentication error",
    "oauth_org_not_allowed": "an organization access error",
    "account_on_hold": "an account hold",
    "verification_required": "a verification check",
    "billing_error": "a billing error",
    "rate_limit": "a rate limit",
    "overloaded": "an overloaded server",
    "invalid_request": "an invalid request",
    "model_not_found": "a missing model",
    "server_error": "a server error",
    "max_output_tokens": "the output length limit",
    "cloud_credential_error": "a cloud credential error",
}

_NUMBERS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"`|\*\*|__|^#+\s*|^\s*[-*+]\s+|^\s*\d+\.\s+", re.MULTILINE)
_PARAGRAPH_END = re.compile(r"\n\s*\n|(?<=:)[ \t]*\n")
_SENTENCE = re.compile(r"(.+?[.!?])(?=\s|$)")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


@dataclass(frozen=True)
class Wording:
    """A frame naming the project, and the detail that goes with it, if any.

    A frame containing `SLOT` takes the detail inline and always has one; any
    other frame is a full sentence that the detail, if there is one, follows.
    """

    frame: str
    detail: str | None = None

    def text(self, detail: str | None = None) -> str:
        """The spoken sentence, with `detail` in place of the template's own if given."""
        chosen = detail or self.detail
        if SLOT in self.frame:
            return self.frame.replace(SLOT, chosen or "do something")
        return f"{self.frame} {_capitalize(chosen)}" if chosen else self.frame


def word(note: Notification) -> Wording:
    project = project_name(note.cwd)
    payload = note.payload
    match note.kind:
        case EventKind.FINISHED:
            return Wording(f"Finished up in {project}.", _message_detail(payload))
        case EventKind.STILL_RUNNING:
            return Wording(
                f"Done for now in {project}, but background work isn't finished.",
                _background_detail(note.background),
            )
        case EventKind.BACKGROUND_DONE:
            return Wording(f"Background work's all done in {project}.", _message_detail(payload))
        case EventKind.PERMISSION:
            return Wording(
                f"{_capitalize(project)} needs your okay to {SLOT}.", describe_action(payload)
            )
        case EventKind.QUESTION:
            if _text(payload, "tool_name") == "ExitPlanMode":
                return Wording(f"{_capitalize(project)} has a plan ready for you to review.")
            return Wording(
                f"{_capitalize(project)} has a question for you.", _question_detail(payload)
            )
        case EventKind.DENIED:
            return Wording(
                f"{_capitalize(project)} was refused permission to {SLOT}.",
                describe_action(payload),
            )
        case EventKind.FAILED:
            error = ERRORS.get(_text(payload, "error"))
            return Wording(f"The turn failed in {project}.", f"It hit {error}." if error else None)


def project_name(cwd: str) -> str:
    """The session folder's name as words: dashes, underscores and camel case become spaces."""
    name = PurePosixPath(cwd).name if cwd else ""
    words = _CAMEL.sub(" ", name.replace("-", " ").replace("_", " ")).split()
    # Lower-case ordinary words; leave acronyms like "API" as they are.
    spoken = " ".join(w if w.isupper() and len(w) > 1 else w.lower() for w in words)
    return spoken or FALLBACK_PROJECT


def describe_action(payload: Mapping[str, Any]) -> str:
    """What a tool call is about to do, as a verb phrase ("run git status")."""
    tool = _text(payload, "tool_name")
    tool_input = _mapping(payload.get("tool_input"))
    description = _text(tool_input, "description")
    match tool:
        case "Bash":
            if description:
                return _phrase(description)
            command = _text(tool_input, "command").strip().splitlines()
            return f"run {_short_command(command[0])}" if command else "run a shell command"
        case "Edit" | "MultiEdit":
            return _file_action("edit", _text(tool_input, "file_path"))
        case "Write":
            return _file_action("write", _text(tool_input, "file_path"))
        case "Read":
            return _file_action("read", _text(tool_input, "file_path"))
        case "NotebookEdit":
            return _file_action("edit", _text(tool_input, "notebook_path"))
        case "WebFetch":
            host = _host(_text(tool_input, "url"))
            return f"fetch a page from {host}" if host else "fetch a web page"
        case "WebSearch":
            query = _text(tool_input, "query")
            return (
                f"search the web for {_cap_words(query, ACTION_WORDS)}"
                if query
                else "search the web"
            )
        case "Agent" | "Task":
            if description:
                return f"start an agent to {_phrase(description)}"
            return "start an agent"
        case "Skill":
            skill = _text(tool_input, "skill")
            return f"use the {_speakable(skill)} skill" if skill else "use a skill"
        case _ if tool.startswith("mcp__"):
            server, _, name = tool.removeprefix("mcp__").partition("__")
            if name:
                return f"use {_speakable(name)} from {_speakable(server)}"
            return f"use {_speakable(server)}"
        case "":
            return "use a tool"
        case _:
            return f"use {_speakable(tool)}"


def _message_detail(payload: Mapping[str, Any]) -> str | None:
    """The first sentence of the turn's last message, cleaned up for speech."""
    raw = _CODE_FENCE.sub(" ", _text(payload, "last_assistant_message")).strip()
    # Only the opening paragraph, up to any line that leads into a list.
    text = _plain(_PARAGRAPH_END.split(raw, maxsplit=1)[0])
    if not text:
        return None
    match = _SENTENCE.match(text)
    sentence = match.group(1) if match else text
    # A lead-in to a list ("Here's what changed:") says nothing on its own.
    if sentence.rstrip().endswith(":"):
        return None
    return _end(_cap_words(sentence, MESSAGE_WORDS))


def _background_detail(items: Sequence[BackgroundItem]) -> str | None:
    if not items:
        return None
    if len(items) <= LISTED_BACKGROUND and all(item.description for item in items):
        names = [_lower_first(_strip_end(item.description)) for item in items]
        return f"Still going: {' and '.join(names)}."
    agents = sum(1 for item in items if item.type == "subagent")
    commands = len(items) - agents
    parts = [_count(agents, "agent"), _count(commands, "command")]
    return f"Still going: {' and '.join(p for p in parts if p)}."


def _question_detail(payload: Mapping[str, Any]) -> str | None:
    tool_input = _mapping(payload.get("tool_input"))
    raw: Any = tool_input.get("questions")
    questions: list[Any] = raw if isinstance(raw, list) else []  # pyright: ignore[reportUnknownVariableType]
    if not questions:
        return None
    first = _mapping(questions[0])
    question = _plain(_text(first, "question") or _text(first, "header"))
    if not question:
        return None
    detail = _end(_cap_words(question, QUESTION_WORDS), "?")
    more = len(questions) - 1
    if more:
        detail += f" And {_count(more, 'more question')}."
    return detail


def _phrase(description: str) -> str:
    """A tool call's own description ("Run the tests.") as a verb phrase."""
    return _lower_first(_cap_words(_strip_end(description), ACTION_WORDS))


def _short_command(command: str) -> str:
    """A command's first words, with paths reduced to file names."""
    words = [(PurePosixPath(w).name or w) if "/" in w else w for w in command.split()]
    return " ".join(words[:COMMAND_WORDS])


def _file_action(verb: str, path: str) -> str:
    name = PurePosixPath(path).name if path else ""
    return f"{verb} {name}" if name else f"{verb} a file"


def _host(url: str) -> str:
    host = re.sub(r"^[a-z]+://", "", url).split("/")[0].split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def _plain(text: str) -> str:
    """Markdown reduced to plain prose on one line."""
    text = _CODE_FENCE.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_MARKS.sub("", text)
    return " ".join(text.split())


def _cap_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip(",;:")


def _count(n: int, noun: str) -> str:
    if n == 0:
        return ""
    number = _NUMBERS[n] if n < len(_NUMBERS) else str(n)
    return f"{number} {noun}" if n == 1 else f"{number} {noun}s"


def _speakable(name: str) -> str:
    return " ".join(_CAMEL.sub(" ", name.replace("_", " ").replace("-", " ")).lower().split())


def _strip_end(text: str) -> str:
    return text.strip().rstrip(".!?")


def _end(text: str, mark: str = ".") -> str:
    """Make sure a detail ends like a sentence, so the voice lands it."""
    text = text.rstrip(",;: ")
    return text if text.endswith((".", "!", "?")) else text + mark


def _lower_first(text: str) -> str:
    # Keep acronyms and names like "API" or "README" capitalized.
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}  # pyright: ignore[reportUnknownVariableType]


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""
