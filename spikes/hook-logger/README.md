# hook-logger (spike, not shipped)

A throwaway Claude Code plugin that logs every hook event, once from a synchronous handler and once from an `async` one, with its payload and the host app's environment. Results and how to rerun it are in ADR-0001 (`docs/decisions/`).

```sh
claude --plugin-dir spikes/hook-logger      # use it for one session
python3 spikes/hook-logger/summarize.py     # table of what fired
```

The log is `~/Library/Logs/cc-notify-tts-spike/hooks.jsonl` (or `$CC_HOOK_SPIKE_LOG`). It holds real prompts and paths: keep it out of the repository and share only `summarize.py`'s output.
