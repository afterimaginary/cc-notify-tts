# Spoken messages

What cc-notify-tts says for each notification event: the template, written by code alone, and when a local model may write the detail instead ([model wording](#model-wording)). The events are defined in the [event classification design](../designs/event-classification.md).

Code: `src/cc_notify_tts/wording.py`. Tests: `tests/test_wording.py`, which runs every hook fixture in `tests/fixtures/hooks/` through classification and wording and checks the exact sentence.

## Shape

Each message is a **frame** and an optional **detail**.

- The frame is fixed text with the project name in it. It always comes from code.
- The detail comes from the hook payload. A frame either takes it inline (permission, denied) or as a second sentence (the rest). An inline detail is never empty; a second-sentence detail is left out when the payload has nothing to say.
- `word(notification)` returns a `Wording(frame, detail)`. `Wording.text()` gives the sentence to speak; `Wording.text(other)` puts another detail in the template's place, which is how a model-written detail will be slotted in.

## Templates

`<project>` is the project name (below). The first letter of the sentence is capitalized.

| Event | Frame | Detail |
| - | - | - |
| `finished` | Finished up in `<project>`. | First sentence of the turn's last message |
| `still_running` | Done for now in `<project>`, but background work isn't finished. | Still going: `<background work>`. |
| `background_done` | Background work's all done in `<project>`. | First sentence of the wake-up turn's last message |
| `permission` | `<project>` needs your okay to `<action>`. | The action, inline |
| `question` | `<project>` has a question for you. | The first question, and "And *n* more questions." if there are more |
| `question` (`ExitPlanMode`) | `<project>` has a plan ready for you to review. | None |
| `denied` | `<project>` was refused permission to `<action>`. | The action, inline |
| `failed` | The turn failed in `<project>`. | It hit `<error>`. |

Examples, from the fixtures:

- Finished up in weather app. Added exponential backoff to the forecast fetcher and two tests for the retry path.
- Done for now in weather app, but background work isn't finished. Still going: audit the forecast cache and run the integration test suite.
- Weather app needs your okay to force-push the rebased branch to main.
- Weather app has a question for you. Should the retry give up after three attempts or five?
- The turn failed in weather app. It hit a rate limit.

## Project name

The last component of the folder the session started in, made speakable. A later `cd` doesn't change it, and a worktree under `.claude/worktrees/` is named after the repository that holds it.

- Dashes and underscores become spaces, and camel case is split: `weather-app`, `weather_app` and `WeatherApp` are all "weather app".
- Words are lower-cased, except all-capital words such as `API`, which are kept.
- Other characters are kept: `next.js` stays "next.js".
- With no folder name (no `cwd`, or `/`), the name is "your project".

## Actions

A permission request or denial names what the tool call would do, from `tool_name` and `tool_input`.

| Tool | Action |
| - | - |
| `Bash` with a `description` | The description as a verb phrase ("force-push the rebased branch to main"), up to 12 words |
| `Bash` without one | "run" and the command's first line, up to 6 words, with redirections such as `2>/dev/null` left out, URLs reduced to their host and any other word containing `/` reduced to its file name ("run python build.py --release") |
| `Edit`, `MultiEdit`, `NotebookEdit` | "edit" and the file name |
| `Write` | "write" and the file name |
| `Read` | "read" and the file name |
| `WebFetch` | "fetch a page from" and the host, without `www.` |
| `WebSearch` | "search the web for" and the query, up to 12 words |
| `Agent`, `Task` | "start an agent to" and its description |
| `Skill` | "use the *name* skill", with `-`, `_` and `:` spoken as spaces |
| `mcp__server__tool` | "use *tool* from *server*"; a plugin's server (`plugin_<plugin>_<server>`) drops `plugin_` and a repeated name ("use query docs from docs") |
| Anything else | "use" and the tool name as words ("use todo write") |

A missing argument gives a general phrase instead ("edit a file", "run a shell command", "use a tool"). The Bash description is preferred over the command because Claude usually writes one, and it reads as speech where a command often doesn't.

## Details from text

- **Last message:** code blocks are removed, and so are headings, bold labels ("**Summary**") and lead-in lines ending in a colon ("Here's what changed:") before the first paragraph. If a list comes first, its first item counts on its own; otherwise the opening paragraph does, up to a blank line or a list. Links and emphasis are reduced to their text, bare URLs to their host and paths (starting with `/`, `~/`, `./` or `../`) to their file name. The first sentence is taken and capped at 20 words. A sentence ends at a `.`, `!` or `?` followed by a space or the end of the text, so decimals and versions ("3.5", "v1.2.3") don't end one, and neither does the period of an abbreviation: letters joined by periods ("e.g.", "i.e.", "U.S."), "vs.", "cf.", "approx.", "incl.", "esp.", "viz.", "Mr.", "Mrs." and "Dr." (not "ms.", which is usually milliseconds), or "etc." when lower case follows. The model's answer is cut with the same splitter. In the template, if the sentence opens with an inline lead-in, only what follows its colon is spoken (and a failing-tests sentence that repeats it isn't added): "Here's" or "Here is/are" and up to 60 more characters ("Here's what was done: renamed it." gives "Renamed it."), "In summary", "In short", "To recap", "TL;DR", "What was done", "What changed", "What I did", or a lone label word ("Summary", "Overall", "Done", "All done", "All set", "Result(s)", "Changes", "Update", "Note"). A lead-in that names options, choices, alternatives, ways or approaches stays, so a one-line choice is still heard as one ("Here are two options: keep the cache or drop it." is spoken as written). A colon after anything else is content and stays ("Fixed the parser: it dropped the last token.", "Test run: 2 failed, 41 passed."). A sentence ending in a colon, inline lead-in or not, gives no detail. A detail always ends in `.`, `!` or `?`.
- **Tests still failing:** a turn whose last message says tests are still failing is never spoken as a success (ADR-0021). If the detail doesn't already say so, the first sentence anywhere in the message that does ("Three tests still fail because they expect UTC.") is added after it, or spoken on its own if the two together pass 20 words. If that sentence alone passes 20 words, only its clauses that hold the failure are used, starting from the nearest clause that names tests (if that keeps it within 20 words), specs, the suite, CI or the build or holds a test summary, so what's spoken says what failed ("Two tests, test_a and test_b, still fail.", "31 of 33 tests now pass, the other 2 still fail."), and without a leading "and", "so", "yet", "or", "plus" or "then". If the 20-word cap would still cut the failure off, "Some tests are still failing." is spoken in its place. Clauses are split at commas, semicolons and words like "but"; sentences are split as above. A clause counts when it says something still fails, tests are failing or aren't passing, a count of tests fail ("2 of the 31 tests fail"), a test by name or description fails or is failing ("test_a is failing", "the test for leap years fails", but not "a test for when login fails"), tests, specs, the suite, CI, the build or a named test are still broken, still red or red, or have a count of failures ("the test suite still has 2 failures"), within the same clause, or it holds a test summary ("tests: 2 failed", "2 failed, 41 passed"), unless it also says the failure is over ("no longer fails", "now pass") or was fixed ("Fixed 2 tests failing on CI"). So "31 now pass, but 2 still fail" counts, and "Retried the 3 failed uploads" and "The CSV import is still broken" don't (ADR-0033).
- **Background work:** up to two items are listed by description ("audit the forecast cache and run the integration test suite"). With more, or with an item that has no description, they're counted: "one agent and two commands".
- **Questions:** the first question's text (or its header), capped at 20 words and ending in `?` if it has no end mark.
- **Errors:** the `StopFailure` `error` value, spoken as a phrase:

| `error` | Spoken |
| - | - |
| `authentication_failed` | an authentication error |
| `oauth_org_not_allowed` | an organization access error |
| `account_on_hold` | an account hold |
| `verification_required` | a verification check |
| `billing_error` | a billing error |
| `rate_limit` | a rate limit |
| `overloaded` | an overloaded server |
| `invalid_request` | an invalid request |
| `model_not_found` | a missing model |
| `server_error` | a server error |
| `max_output_tokens` | the output length limit |
| `cloud_credential_error` | a cloud credential error |

`unknown`, or any other value, gives no detail.

Counts up to nine are spoken as words.

## Model wording

With `wording.ollama` on (the default, [config](config.md)), a local Ollama model may write the detail for three events. The frame, and so the project name, is always the template's. Code: `src/cc_notify_tts/model_wording.py` (prompt and guards) and `src/cc_notify_tts/ollama.py` (client and keep-warm). Decisions: ADR-0003, ADR-0015.

| Event | The model is shown | It writes |
| - | - | - |
| `finished` | The last 4000 characters of the turn's last message | What was done, one sentence |
| `question` | Each question and its option labels | The question, asked again shortly |
| `permission` | The tool name and each argument (up to 500 characters each) | What the call would do, as a verb phrase for the frame's slot |

Every other event, and a plan to review, keeps the template. The model never sees the frame or the project name.

The model is asked only when it's already loaded, and gets `wording.timeout_seconds` to answer. Its answer is cleaned, then checked:

1. Its first non-empty line, without a reasoning block, a label ("Summary:"), quotes or markdown.
2. A repeat of the frame at the start is removed: the whole frame, its fixed phrases ("needs your okay to", "has a question for you") or the project name.
3. Its first sentence. Rejected if that ends in a colon: a lead-in such as "Here's what was done:" says nothing.
4. Rejected if it starts with a subject pronoun: I, we, it, he, she, they, or a contraction of one.
5. Rejected unless it has 2 to 20 words (2 to 12 for `permission`).
6. Rejected if any name or number in it isn't in what the model was shown. A name is a word with a capital letter (other than the first word's first letter), a digit, or `.`, `_` or `/` inside it. A count matches in digits or words ("3" or "three").
7. For `finished`, rejected if the turn's whole last message, not only the part the model was shown, says tests are still failing (as in "Tests still failing" above) and the answer doesn't say tests are failing: a fail word, "broken" or "red" within a few words of tests, specs, the suite, CI, the build or a test by name, "still fails", "not passing", or a test summary with failures ("41 passed, 2 failed") (ADR-0021, ADR-0033). A fail word about anything else ("Fixed the broken import") doesn't count. A failure the answer says was fixed ("Fixed the failing auth tests") doesn't count. The same test decides whether the template's first sentence already mentions the failure. The template's detail is spoken instead: the sentence from the message that says tests are failing, or a short summary of it ("Ran it again: 41 passed, 2 failed.").

A detail that passes ends in `.` (`?` for a question) with its first letter capitalized. A permission's action loses a leading "to" and has no end mark, since the frame supplies both. A question with more than one keeps code's "And *n* more questions." after the model's question.

The template's detail is spoken, and the reason logged, whenever:

- Wording is off in the config.
- Ollama isn't running or isn't installed.
- The model isn't loaded, or is loaded at another context. It's then loaded in the background, so the next notification can use it; a model that isn't pulled is logged when that load fails.
- Ollama doesn't answer within the timeout, or answers with an error.
- The answer fails a check.

While wording is on and a session is open, the daemon keeps the model loaded, and it unloads it once no session is open, wording is turned off or the daemon exits (ADR-0015).
