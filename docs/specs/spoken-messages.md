# Spoken messages

What cc-notify-tts says for each notification event, when the sentence is written by code alone. The events are defined in the [event classification design](../designs/event-classification.md).

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
| `Skill` | "use the *name* skill" |
| `mcp__server__tool` | "use *tool* from *server*" |
| Anything else | "use" and the tool name as words ("use todo write") |

A missing argument gives a general phrase instead ("edit a file", "run a shell command", "use a tool"). The Bash description is preferred over the command because Claude usually writes one, and it reads as speech where a command often doesn't.

## Details from text

- **Last message:** code blocks are removed, and so are headings, bold labels ("**Summary**") and lead-in lines ending in a colon ("Here's what changed:") before the first paragraph. If a list comes first, its first item counts on its own; otherwise the opening paragraph does, up to a blank line or a list. Links and emphasis are reduced to their text. The first sentence is taken and capped at 20 words. A sentence ending in a colon gives no detail. A detail always ends in `.`, `!` or `?`.
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
