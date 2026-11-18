"""Template wording: the sentence spoken for each notification, written by code alone.

`word` turns a notification into a frame (the fixed part, naming the project)
and an optional detail taken from the payload. A local model may replace the
detail for some events (`model_wording.py`); the frame always comes from here. Every template is
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
# Spoken when the sentence saying tests fail is too long to keep its failure (ADR-0021).
STILL_FAILING = "Some tests are still failing."
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
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
# A line that only labels what follows: a heading, a bold label or a lead-in ending in a colon.
_LABEL = re.compile(r"#+\s.*|(\*\*|__)[^*_]+\1:?|.*:")
# A lead-in at the start of a sentence, introducing what follows the colon: "Here's
# what was done:", "In short:", or a lone label word ("Summary:", "Done:"). A colon
# after anything else is content ("Fixed the parser: it dropped tokens", "Test run:
# 2 failed").
_LEAD_IN = re.compile(
    r"(?:here(?:['\u2019]s| is| are)\b[^:]{0,60}"
    r"|in summary|in short|to recap|tl;?dr|what (?:was done|changed|i did)"
    r"|summary|overall|done|all done|all set|results?|changes|update|note"
    r"):\s+",
    re.IGNORECASE,
)
# A mark that may end a sentence: one followed by a space or the end, so decimals
# and versions ("3.5", "v1.2.3") never do.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
# Words a period follows without ending the sentence, besides letters joined by
# periods ("e.g.", "i.e.", "U.S."). "etc." ends one unless lower case follows.
# Not "ms": in these messages it's milliseconds ("took 120 ms."), not a title.
_ABBREVIATIONS = frozenset(
    {"vs", "cf", "approx", "incl", "esp", "viz", "eg", "ie", "mr", "mrs", "dr"}
)
_INITIALISM = re.compile(r"(?:[a-z]\.)+[a-z]", re.IGNORECASE)
# A URL or a path, and any punctuation that ends the sentence after it.
_URL = re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s)>\]]*?)([.,;:!?]*)(?=[\s)>\]]|$)")
_PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2})?/(?:[\w.-]+/)*([\w.-]+?)([.,;:!?]*)(?=\s|$)")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# A sentence saying tests are failing as the turn ends (ADR-0021, ADR-0033): "2 of
# the 31 tests still fail", "the tests aren't passing yet", "3 failed, 40 passed".
_COUNT = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|some|several|a few)"
_SOME = r"(?:[1-9]\d*|one|two|three|four|five|six|seven|eight|nine|ten|some|several|a few)"
_TESTS = r"(?:tests?|specs?)"
# What a failure can be about: tests or the run, or a test by name ("test_refund").
_CHECK_WORDS = r"(?:tests?|specs?|suite|ci|build)"
_CHECKS = rf"(?:{_CHECK_WORDS}|test_\w+|\w+_test)"
# A test summary with failures: "2 failed, 41 passed" or "41 passed, 2 failed".
_SUMMARY = r"\b[1-9]\d*\s+failed\W+\d+\s+passed\b|\b\d+\s+passed\W+[1-9]\d*\s+failed\b"
# Words that start a clause about something else ("a test for when login fails").
_NOT_THE_TEST = r"(?:when|where|if|that|which|so|until|after|before|because)\b"
_TESTS_FAILING = re.compile(
    r"\bstill\s+fail\w*\b"
    # "Broken", "red" and "N failures" only about tests: "the suite is still red".
    rf"|\b{_CHECKS}\b[^,;]*?\b(?:still\s+(?:broken|red)|(?:is|are)\s+red|{_SOME}\s+failures)\b"
    rf"|\b{_TESTS}\s+(?:are|is|remain)\s+(?:still\s+)?failing\b"
    rf"|\b{_TESTS}\s+(?:do|does|are|is)\s*(?:not|n't)\s+(?:yet\s+)?pass"
    rf"|\b{_COUNT}\s+(?:of\s+(?:the\s+)?\d+\s+)?(?:\w+\s+)?{_TESTS}\s+(?:are\s+)?fail(?:s|ed|ing)?\b"
    # A test by name or description: "test_a is failing", "the test for leap years fails".
    r"|\b(?:test_\w+|\w+_test)\s+(?:is\s+)?(?:still\s+)?(?:failing|fails)\b"
    rf"|\b{_TESTS}\s+(?:for|in|on|of)\s+(?:(?!{_NOT_THE_TEST})[\w.'-]+\s+){{1,4}}?"
    r"(?:(?:is|are)\s+failing|fails?)\b"
    # "N failed" only about tests: "tests: 2 failed" or a summary, "2 failed, 41 passed".
    rf"|\b{_TESTS}\W+[1-9]\d*\s+failed\b|{_SUMMARY}"
    r"|\b(?:failing\s+tests?|test\s+failures?)\s+remains?\b",
    re.IGNORECASE,
)
# What a clause cut from a long sentence mustn't start with: "and the 2 billing
# tests still fail" is spoken as "The 2 billing tests still fail".
_LEADING_CONJUNCTION = re.compile(r"^(?:and|so|yet|or|plus|then)\s+", re.IGNORECASE)
# A clause that says what failed: names tests or the run, or gives a count of them.
# Not a test's own name, which may be one of a list ("two tests, test_a and test_b").
_NAMES_TESTS = re.compile(rf"\b{_CHECK_WORDS}\b|{_SUMMARY}", re.IGNORECASE)
# ...unless the clause it's in says that failure is over ("no longer fails", "now pass")
# or was fixed ("Fixed 2 tests failing on CI"). Checked clause by clause, so "31 now
# pass, but 2 still fail" still counts.
_FAILURE_OVER = re.compile(r"\bno\s+longer\b|\bnow\b.*\bpass", re.IGNORECASE)
_CLAUSES = re.compile(r"[;,]|\s(?:but|although|though|while|whereas)\s", re.IGNORECASE)
# A detail that admits tests are failing: a fail word near "tests" (either way round),
# "still fail", "not passing" or a test summary with failures. A fail word about
# anything else ("Fixed the broken import") doesn't count...
_ADMITS_FAILURE = re.compile(
    rf"\b{_CHECKS}\b(?:\W+\w+){{0,4}}?\W+(?:fail\w*|broken|red)\b"
    rf"|\bfail\w*\W+(?:\w+\W+){{0,2}}?{_CHECKS}\b"
    r"|\bstill\s+fail"
    r"|(?:\bnot|n't)\s+(?:yet\s+)?pass"
    rf"|{_SUMMARY}",
    re.IGNORECASE,
)
# ...other than a failure that was fixed ("Fixed the failing auth tests").
_FIXED_FAILURE = re.compile(
    r"\b(?:fix|resolv|repair)\w*\s+(?:[\w'-]+\s+){0,3}?fail\w*", re.IGNORECASE
)


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
        return f"{self.frame} {capitalize(chosen)}" if chosen else self.frame


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
                f"{capitalize(project)} needs your okay to {SLOT}.", describe_action(payload)
            )
        case EventKind.QUESTION:
            if _text(payload, "tool_name") == "ExitPlanMode":
                return Wording(f"{capitalize(project)} has a plan ready for you to review.")
            return Wording(
                f"{capitalize(project)} has a question for you.", _question_detail(payload)
            )
        case EventKind.DENIED:
            return Wording(
                f"{capitalize(project)} was refused permission to {SLOT}.",
                describe_action(payload),
            )
        case EventKind.FAILED:
            error = ERRORS.get(_text(payload, "error"))
            return Wording(f"The turn failed in {project}.", f"It hit {error}." if error else None)


def project_name(cwd: str) -> str:
    """The session folder's name as words: dashes, underscores and camel case become spaces."""
    # A worktree under .claude/worktrees/ belongs to the repository that holds it.
    root, worktree, _ = cwd.partition("/.claude/worktrees/")
    name = PurePosixPath(root if worktree else cwd).name if cwd else ""
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
                return f"use {_speakable(name)} from {_mcp_server(server)}"
            return f"use {_mcp_server(server)}"
        case "":
            return "use a tool"
        case _:
            return f"use {_speakable(tool)}"


def _message_detail(payload: Mapping[str, Any]) -> str | None:
    """The first sentence of the turn's last message, cleaned up for speech.

    If the message says tests still fail and that sentence doesn't, the
    sentence that does is added, or spoken alone if both don't fit.
    """
    message = _text(payload, "last_assistant_message")
    text = plain(_opening(message))
    sentence = _without_lead_in(first_sentence(text)) if text else ""
    # A lead-in to a list ("Here's what changed:") says nothing on its own.
    detail = (
        end_sentence(_cap_words(sentence, MESSAGE_WORDS))
        if sentence and not sentence.rstrip().endswith(":")
        else None
    )
    failing = failing_tests(message)
    if failing is None or (detail is not None and admits_failure(detail)):
        return detail
    # Never repeat what the detail already says.
    if detail is not None and _without_lead_in(failing).lower() in detail.lower():
        return detail
    cut = len(failing.split()) > MESSAGE_WORDS
    failing = end_sentence(_cap_words(failing, MESSAGE_WORDS))
    # The cap can cut the failure off a long clause; say it plainly instead.
    if cut and not admits_failure(failing):
        failing = STILL_FAILING
    if detail is not None and len(detail.split()) + len(failing.split()) <= MESSAGE_WORDS:
        return f"{detail} {capitalize(failing)}"
    return capitalize(failing)


def _without_lead_in(sentence: str) -> str:
    """Only what an inline lead-in introduces ("Here's what was done: renamed it.")."""
    lead_in = _LEAD_IN.match(sentence)
    return capitalize(sentence[lead_in.end() :]) if lead_in else sentence


def failing_tests(message: str) -> str | None:
    """The first sentence of a message that says tests are still failing, if any.

    A sentence over `MESSAGE_WORDS` gives only its clauses that hold the
    failure ("2 of the 31 tests still fail"), from the nearest one that names
    tests, so the word cap on what's spoken can't cut the failure off the end
    and what's left still says what failed.
    """
    # Line by line, so a list item or heading isn't run into the next one.
    for line in _CODE_FENCE.sub("\n", message).splitlines():
        for sentence in _sentences(plain(line)):
            # Matched on the sentence, since a summary ("2 failed, 41 passed") has a comma.
            for match in _TESTS_FAILING.finditer(sentence):
                start, end = _clause_at(sentence, match.start())
                clause = sentence[start:end]
                if _FAILURE_OVER.search(clause) or _FIXED_FAILURE.search(clause):
                    continue
                if len(sentence.split()) > MESSAGE_WORDS:
                    end = _clause_at(sentence, match.end() - 1)[1]
                    start = _naming_clause_start(sentence, start, end)
                    sentence = _LEADING_CONJUNCTION.sub("", sentence[start:end].strip())
                return strip_end(sentence.strip()).rstrip(",;: ")
    return None


def _sentences(text: str) -> list[str]:
    """Each sentence of a one-line text, split where `first_sentence` would split it."""
    sentences: list[str] = []
    while text := text.strip():
        sentence = first_sentence(text)
        sentences.append(sentence)
        text = text[len(sentence) :]
    return sentences


def _clause_starts(sentence: str) -> list[int]:
    return [0, *(split.end() for split in _CLAUSES.finditer(sentence))]


def _clause_at(sentence: str, index: int) -> tuple[int, int]:
    """Where the clause of `sentence` that holds the character at `index` starts and ends."""
    start = 0
    for split in _CLAUSES.finditer(sentence):
        if split.start() > index:
            return start, split.start()
        start = split.end()
    return start, len(sentence)


def _naming_clause_start(sentence: str, start: int, end: int) -> int:
    """Where to start a span ending at `end` so it names tests: `start`, or an earlier clause.

    "Two tests, test_a and test_b, still fail" and "31 of 33 tests now pass;
    the other 2 still fail" keep the clause that says which tests.
    """
    earlier = [s for s in _clause_starts(sentence) if s <= start]
    for candidate in reversed(earlier):
        if _NAMES_TESTS.search(sentence[candidate:end]):
            return candidate
    return start


def admits_failure(text: str) -> bool:
    """Whether a detail says something failed or isn't passing, not that a failure was fixed."""
    return _ADMITS_FAILURE.search(_FIXED_FAILURE.sub("", text)) is not None


def _opening(message: str) -> str:
    """A message's opening paragraph, past any headings and labels, or its first list item."""
    paragraph: list[str] = []
    for line in _CODE_FENCE.sub("\n\n", message).splitlines():
        line = line.strip()
        if _LIST_ITEM.match(line):
            if paragraph:
                break
            return _LIST_ITEM.sub("", line)
        if not line or (not paragraph and _LABEL.fullmatch(line)):
            if paragraph:
                break
            continue
        paragraph.append(line)
    return " ".join(paragraph)


def _background_detail(items: Sequence[BackgroundItem]) -> str | None:
    if not items:
        return None
    if len(items) <= LISTED_BACKGROUND and all(item.description for item in items):
        names = [lower_first(strip_end(item.description)) for item in items]
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
    question = plain(_text(first, "question") or _text(first, "header"))
    if not question:
        return None
    detail = end_sentence(_cap_words(question, QUESTION_WORDS), "?")
    if more := more_questions(len(questions)):
        detail += f" {more}"
    return detail


def _phrase(description: str) -> str:
    """A tool call's own description ("Run the tests.") as a verb phrase."""
    return lower_first(_cap_words(strip_end(description), ACTION_WORDS))


def _short_command(command: str) -> str:
    """A command's first words, with paths reduced to file names and redirections left out."""
    words: list[str] = []
    for w in command.split():
        if "<" in w or ">" in w:
            continue
        if "://" in w:
            w = _host(w)
        elif "/" in w:
            w = PurePosixPath(w).name or w
        words.append(w)
    return " ".join(words[:COMMAND_WORDS])


def _file_action(verb: str, path: str) -> str:
    name = PurePosixPath(path).name if path else ""
    return f"{verb} {name}" if name else f"{verb} a file"


def _host(url: str) -> str:
    host = re.sub(r"^[a-z]+://", "", url).split("/")[0].split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def plain(text: str) -> str:
    """Markdown reduced to plain prose on one line, with URLs and paths shortened."""
    text = _CODE_FENCE.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_MARKS.sub("", text)
    text = _URL.sub(lambda m: _host(m.group(1)) + m.group(2), text)
    text = _PATH.sub(lambda m: m.group(1) + m.group(2), text)
    return " ".join(text.split())


def more_questions(total: int) -> str:
    """ "And two more questions." after the first of `total`, or "" for one."""
    more = total - 1
    return f"And {_count(more, 'more question')}." if more > 0 else ""


def first_sentence(text: str) -> str:
    """Text up to the first `.`, `!` or `?` that ends a sentence, or all of it.

    A mark ends one when a space or the end follows it, and a period after an
    abbreviation ("e.g.", "vs.") doesn't.
    """
    for end in _SENTENCE_END.finditer(text):
        if end.start() > 0 and not _after_abbreviation(text, end.start()):
            return text[: end.end()]
    return text


def _after_abbreviation(text: str, period: int) -> bool:
    """Whether the mark at `period` is a period that ends an abbreviation."""
    if text[period] != ".":
        return False
    word = re.sub(r"^\W+", "", (text[:period].split() or [""])[-1]).lower()
    if word == "etc":
        return text[period + 1 :].lstrip()[:1].islower()
    return word in _ABBREVIATIONS or _INITIALISM.fullmatch(word) is not None


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
    spaced = re.sub(r"[_\-:]", " ", name)
    return " ".join(_CAMEL.sub(" ", spaced).lower().split())


def _mcp_server(server: str) -> str:
    """An MCP server's name; a plugin's server ("plugin_docs_docs") is named after its plugin."""
    words = _speakable(server.removeprefix("plugin_")).split()
    half = len(words) // 2
    if len(words) % 2 == 0 and words[:half] == words[half:]:
        words = words[:half]
    return " ".join(words)


def strip_end(text: str) -> str:
    return text.strip().rstrip(".!?")


def end_sentence(text: str, mark: str = ".") -> str:
    """Make sure a detail ends like a sentence, so the voice lands it."""
    text = text.rstrip(",;: ")
    return text if text.endswith((".", "!", "?")) else text + mark


def lower_first(text: str) -> str:
    # Keep acronyms and names like "API" or "README" capitalized.
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}  # pyright: ignore[reportUnknownVariableType]


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""
