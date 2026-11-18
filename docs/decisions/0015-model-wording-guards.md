---
description: A local Ollama model writes only the detail of finished, question and permission notifications, never sees the frame, is asked only when already loaded, and its answer is cut to its first line and sentence, stripped of frame echoes, then rejected (not repaired) on a leading subject pronoun, a length outside 2 to 12 or 20 words, or any name or number missing from its input, with the template spoken instead; the model is kept warm only while a session is open.
status: accepted
---

# ADR-0015: Model wording, its guards and when the model is kept warm

## Context

The brief lets a local Ollama model write the detail of the three notifications with prose to compress: a finished turn, a question and a permission request. A small local model is fast enough when it's warm (ADR-0003), but it drifts: it adds a subject ("I added…", "Claude has…"), repeats the notification's own words back, answers in several sentences, and states names and numbers it wasn't given. A spoken notification can't be corrected after the fact, so a wrong detail is worse than a plainer one, and the template always has a correct one ([spoken messages](../specs/spoken-messages.md)).

ADR-0003 settled the request options, the 1 s wait for a loaded model and the keep-warm ping. Still open: which events use the model, what it's shown, which checks its answer must pass and what a failed check does, and when keeping the model warm should stop.

## Decision

- **The model writes only the detail, and only for `finished`, `question` and `permission`.** The frame and the project name always come from the template. Events built from structured data (background work, denials, failures) keep the template: there's nothing to compress, and a model given an error code can only embellish it. A plan to review has no detail, so it keeps the template too.
- **The model never sees the frame or the project name.** It gets the material (the end of the final message, the questions and their options, or the tool call's arguments) and an instruction for the shape of the answer. Shown the frame, small models repeat it. There are no example answers in the prompt either, since a small model copies an example's content as well as its style.
- **Ask only a model already loaded at the expected context** (`/api/ps`), within `wording.timeout_seconds`. Otherwise the template is spoken at once and the model is loaded in the background for the next one, as ADR-0003 decided.
- **Guards, in order.** The first non-empty line, without a reasoning block, label, quotes or markdown; any leading repeat of the frame, its fixed phrases ("needs your okay to") or the project name, removed; the first sentence, with the template's own splitter. Then three checks:
  - **No leading subject pronoun** (I, we, it, he, she, they, and their contractions). The frame already says who; "Finished up in weather app. I added…" changes speaker mid-notification.
  - **Length:** 2 to 20 words, or 2 to 12 for a permission's action, the template's own caps. One word says nothing.
  - **Every name and number in the answer appears in the input.** A name is a word with a capital (other than a sentence's first letter), a digit, or a `.`, `_` or `/` inside it; numbers count as found in either digits or words ("3" for "three"). Words match whole, case-insensitively. This is the check that catches invention: a model that adds "Redis" or "12 tests" fails it.
- **A failed check speaks the template; nothing is repaired.** Dropping a leading pronoun or truncating a long sentence produces fragments that pass the checks but read badly, and a fragment the eval can't tell from a good answer hides how often the model fails. A rejection is always safe: the template detail is correct by construction. Every fallback is logged with its reason and the model's answer.
- **Temperature 0 and at most 40 output tokens.** The same input should give the same sentence, which also makes the eval reproducible.
- **Keep the model warm only while a session is open.** The daemon loads it after an event when wording is on and a session is open, pings every 45 minutes while that stays true, and unloads it once none is open (the last `SessionEnd`), when wording is turned off, or when the daemon exits. ADR-0003 unloaded only at exit, which with a 2-hour idle timeout kept the model resident for up to 2 hours after Claude Code was closed.
- **The HTTP client is the standard library's.** Four small JSON requests don't justify a dependency in the daemon.
- **The prompt and the guards live in one module** (`model_wording.py`), which the wording eval imports, so the eval scores exactly what the daemon would speak.

## Consequences

- A notification's detail comes from the model or from the template, never a mix, and either way it's spoken under the same frame. A user can't tell which wrote it except from the log.
- The name check can't see an invented name as a sentence's first word ("Postgres now handles…"), since every sentence starts with a capital. The length and pronoun checks don't depend on that word, and the eval reports what slips through.
- Strict checks fall back more often than lenient ones. The eval's guard pass rate measures how often, per model, and that rate is part of choosing the default model.
- The first notification of a new session, after the last one closed, is spoken with the template while the model loads.
- Unloading on the last `SessionEnd` also unloads the model for any other client using it, which then pays one load (as ADR-0003 already accepted for exit).
