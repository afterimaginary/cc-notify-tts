---
description: The plugin listens on SessionStart, SessionEnd, UserPromptSubmit, Stop, StopFailure, PermissionRequest and PermissionDenied, all async, and reads background work from Stop's background_tasks.
status: accepted
---

# ADR-0001: Plugin hook events

## Context

The notifier needs to know when a turn ends (with or without background work still running), when background work finishes, when Claude asks for permission or asks a question, when an action is denied, and when a turn fails. It also needs the session id, the working directory, the last assistant message, the tool name and input, and any error. Hooks must not slow the session, so every handler should be able to run `async`.

Claude Code offers more hook events than the notifier needs, and several look like they fit (`Notification`, `SubagentStop`, `TaskCompleted`). Picking the wrong ones means duplicate or late announcements, or announcements that never come.

## Method

A throwaway plugin in `spikes/hook-logger/` registers every hook event twice, once synchronously and once with `async: true`, and appends each payload plus the host environment to a log outside the repository. A handler that logs in `async` mode proves `async` works for that event.

- **Claude Code version:** 2.1.327, macOS.
- **CLI:** an interactive session driven in tmux (`claude --plugin-dir spikes/hook-logger`), plus headless `claude -p` runs for a failed turn and a deny rule.
- **Desktop app:** untested (see below).

Scenarios run: session start and `/exit`; a plain turn; a turn that starts a background shell command and a background agent and ends at once; the follow-up turns when each finishes; `AskUserQuestion`; a Bash permission prompt in manual mode, answered "No"; two commands in auto mode meant to be denied (the classifier allowed both, because each was harmless); a Bash call blocked by a `--disallowedTools` deny rule; a turn against a model that doesn't exist.

## Findings

### Event coverage

| Scenario | Event that fires | CLI | Desktop app | `async` works | Needed fields present |
| - | - | - | - | - | - |
| Session start | `SessionStart` (`source: startup`) | Yes | Untested | Yes | session id, cwd; `model` (optional), `session_title` (optional) |
| Session end | `SessionEnd` (`reason: prompt_input_exit`, or `other` when the terminal is killed or `-p` exits) | Yes | Untested | Yes | session id, cwd |
| Turn starts | `UserPromptSubmit` | Yes | Untested | Yes | session id, cwd, `prompt` |
| Turn ends | `Stop` | Yes | Untested | Yes | session id, cwd, `last_assistant_message`, `background_tasks: []` |
| Turn ends with background work running | `Stop` with a non-empty `background_tasks` | Yes | Untested | Yes | each task has `id`, `type` (`subagent` or `shell`), `status`, `description`, plus `agent_type` or `command` |
| Background agent starts | `SubagentStart` | Yes | Untested | Yes | `agent_id`, `agent_type`; no description |
| Background agent finishes | `SubagentStop`, then a new turn: `UserPromptSubmit` (prompt is an `<agent-message>` or `<task-notification>`), then `Stop` | Yes | Untested | Yes | `SubagentStop` still lists the finishing agent as `running`; the later `Stop` has the updated list and a `last_assistant_message` describing the result |
| Background shell command finishes | No dedicated event. A new turn: `UserPromptSubmit` with a `<task-notification>` prompt, then `Stop` | Yes | Untested | Yes | as above |
| Task list (`TaskCreate`) | `TaskCreated`, `TaskCompleted` | Not observed: the task-list tools weren't available in the tested CLI | Untested | n/a | n/a. These track the task list, not background work |
| Permission request | `PermissionRequest`, then `Notification` (`permission_prompt`) about 6 s later | Yes | Untested | Yes | `tool_name`, `tool_input` (for Bash: `command`, `description`), `permission_suggestions`, `permission_mode` |
| Question to the user | `PermissionRequest` with `tool_name: AskUserQuestion`, then `Notification` (`permission_prompt`) about 6 s later. Fires in auto mode too | Yes | Untested | Yes | `tool_input.questions[]` with `question`, `header`, `options` |
| Plan waiting for approval (plan mode) | Expected: `PermissionRequest` with `tool_name: ExitPlanMode`, since the docs list it with `AskUserQuestion` as a tool that waits on the user | Untested | Untested | Untested | Expected: `tool_input` with the plan |
| Denied: user answers "No" | Nothing. The turn is interrupted, no `Stop` fires; `Notification` (`idle_prompt`) about 70 s later | Yes | Untested | n/a | n/a |
| Denied: deny rule | Nothing specific. `PreToolUse`, then the turn ends with `Stop` | Yes (headless) | Untested | n/a | n/a |
| Denied: auto-mode classifier | `PermissionDenied` (from the docs: `tool_name`, `tool_input`, `tool_use_id`, `reason`) | Untested: neither harmless probe was denied | Untested | Untested | per the docs, all needed fields |
| Turn fails | `StopFailure` instead of `Stop` | Yes | Untested | Yes interactively; in headless `-p` the async handler was lost when the process exited | `error` (e.g. `model_not_found`, `rate_limit`), `last_assistant_message` holds the error text |

Every payload carried `session_id`, `transcript_path` and `cwd`. Most also carry `prompt_id`, `permission_mode` and `effort`. `Notification`'s `message` is generic ("Claude needs your permission", "Claude is waiting for your input") and names neither the tool nor the question.

The hand-written fixtures in `tests/fixtures/hooks/` follow these shapes, and `tests/test_hook_fixtures.py` keeps them honest.

### Host app environment

A hook inherits Claude Code's environment, so the host app can only be read from what the launching app put there.

| Variable | CLI in tmux | Headless `-p` | Session hosted by the desktop app |
| - | - | - | - |
| `__CFBundleIdentifier` | Absent: tmux's server process holds whatever the app that started it set, which may be nothing | Absent (inherited) | Absent (seen in a desktop-hosted background session; an ordinary desktop session is untested) |
| `TERM_PROGRAM` | `tmux` (tmux overrides the terminal's value) | inherited | Absent |
| `TERM_PROGRAM_VERSION` | tmux's version | inherited | Absent |
| `TMUX` | Set | inherited | Absent |
| `CLAUDE_CODE_ENTRYPOINT` | `cli` | `sdk-cli` | `cli` |
| `CLAUDE_CODE_DESKTOP_APP_VERSION` | Present only because this tmux server was started from a desktop-hosted session: it is inherited by every child process | inherited | Set |

Untested here: a CLI session started directly in Terminal, iTerm2, Ghostty or VS Code's terminal. Those terminals are expected to set `__CFBundleIdentifier` and `TERM_PROGRAM` to their own values, but this needs confirming (steps below).

Consequences for the presence design: `__CFBundleIdentifier` is the most direct answer when present; `TERM_PROGRAM` names a terminal but not always the app in front (tmux hides it); `CLAUDE_CODE_DESKTOP_APP_VERSION` marks a desktop-app session but leaks into anything started from one. When none of them identify the app, the `away` check can only use keyboard and mouse idle time, which matches the rule "if presence can't be read, speak".

## Decision

The hook list was approved as proposed. The desktop-app check is a manual check not yet run (steps below, #89).

Register these events in the plugin's `hooks/hooks.json`, every handler `async: true`:

| Event | Used for |
| - | - |
| `SessionStart` | Record the session and its host app from the environment; start the daemon early so the first announcement isn't a cold start |
| `SessionEnd` | Drop the session's state, so an ended session never counts as live |
| `UserPromptSubmit` | Turn start time. Prompts that are `<task-notification>` or `<agent-message>` wake-ups aren't new user turns |
| `Stop` | Turn finished. A non-empty `background_tasks` means "finished, still running in the background"; an empty list after a heard "still running" means "background work done" |
| `StopFailure` | Turn failed, with `error` |
| `PermissionRequest` | Permission prompts (naming the action from `tool_name` and `tool_input`) and questions. `AskUserQuestion` and `ExitPlanMode` (a plan waiting for approval) are waits on the user, not permission for an action, and must not be spoken as "needs your okay to ExitPlanMode". Listen-only: the handler must never print a decision |
| `PermissionDenied` | Actions the auto-mode classifier denied |

Not registered:

- **`Notification`:** it arrives about 6 s after `PermissionRequest` with a generic message, so it adds a duplicate with less detail. `idle_prompt` isn't one of the events the notifier speaks.
- **`SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`:** `Stop.background_tasks` already lists live agents and shell commands, and background shell commands have no start or stop event at all, so counting starts and stops would cover only half the work. `SubagentStop` even lists the finishing agent as still running.
- **`Elicitation`:** an MCP server asking the user for input. MCP prompts aren't among the events the notifier speaks about.
- **Tool events** (`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`) and **`MessageDisplay`:** they fire many times per turn and carry nothing the notifier needs.

## Consequences

- "Background work done" depends on Claude Code waking the main agent when background work finishes. It did for both a background agent and a background shell command. If a future version stops doing that, `SubagentStop` would be the fallback for agents, and shell commands would have no signal at all.
- A denial the user makes by answering "No", or that a deny rule makes, is silent. That's acceptable: the user was there to answer, or chose the rule.
- `PermissionDenied` and the whole desktop app are untested. The manual checks below confirm them (#89); if it shows a gap, the hook list is revisited in a new decision.

## Manual checks not yet run (#89)

The log holds real prompts and paths, so it stays on your machine; paste only the summary.

**Desktop app:**

1. `claude plugin marketplace add <repo>/spikes/hook-logger`
2. `claude plugin install cc-notify-tts-hook-logger@cc-notify-tts-spikes`
3. Restart the desktop app and open a new Code session in a scratch folder.
4. Run these prompts, one per turn:
   - "Reply with just the word hello."
   - "Start two things in the background, then end your turn immediately: a Bash command `sleep 25` with run_in_background, and a background Agent that runs `sleep 35` and replies done." Wait for both to finish.
   - "Use AskUserQuestion to ask me 'Pick a color' with options red and blue." Answer it.
   - In manual permission mode: "Run the Bash command: touch spike-file.txt". Approve it once, then repeat and answer No.
   - In plan mode: "Plan adding a README line saying hello." Leave the plan waiting for approval for a few seconds, then reject it.
   - Switch the model to one that doesn't exist (`/model claude-nonexistent`) and send any prompt.
   - Close the session.
5. `python3 <repo>/spikes/hook-logger/summarize.py` and paste the output on #89.
6. Clean up: `claude plugin uninstall cc-notify-tts-hook-logger@cc-notify-tts-spikes`, `claude plugin marketplace remove cc-notify-tts-spikes`, and delete `~/Library/Logs/cc-notify-tts-spike`.

**A terminal without tmux:** in Terminal or iTerm2, run `claude --plugin-dir <repo>/spikes/hook-logger`, send one prompt, `/exit`, then run `summarize.py` and paste its "Host environment" lines.

**`PermissionDenied`:** in auto mode, when the classifier next denies something in normal work with the logger installed, `summarize.py` will list a `PermissionDenied` row. Check that the async handler logged it too.
