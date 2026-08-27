---
type: procedure
status: active
baseline: true
created: 2026-08-05
description: "Read and summarize any VaultBot chat session from its JSONL log. Finds a session by UUID, title (substring match), or 'latest', then extracts: title, start time, token totals, every user/assistant turn, every tool call with its result, every exception/console_error, and thinking-block count. Single entry point for looking at what happened in any past session — for both VaultBot self-diagnosis and operator troubleshooting. Use when Sean asks 'show me the last chat', 'what were you doing earlier', 'why did you crash', 'what happened in the last session', 'the session about X', or when resuming context after a restart. Read-only — never modifies a session file."
when_to_use: when asked 'what happened in the last session', 'show me the last chat', 'what were you doing earlier', 'why did you crash', 'the session about X', 'what did you do earlier', 'what were we doing last time', when resuming context after a restart, when investigating a reported failure, when asked about any specific past session by title or UUID
falsifiable_if: the session it finds does not match the requested title/id, or it reports an event that isn't actually in the JSONL file (verifiable by reading the raw file)
applies_to:
  - session-history
  - troubleshooting
  - self-diagnosis
  - log-analysis
  - context-recovery
allowed_tools:
  - code_run
summary: Analyze-Session-Log|read-only, tool calls + results, exceptions, console errors | jsonL log processing
tags:
  - procedure
  - procedures
  - troubleshooting
  - logs
  - sessions
---

# Analyze-Session-Log

Reads a VaultBot session's JSONL log and returns a structured summary of
everything that happened: title, turns, tool calls + results, exceptions,
console errors, and thinking. Sessions live in
`vaultbot-stuff/vaultbot_backend/sessions/*.jsonl` — one file per chat
session, named by UUID, append-only.

Read-only — never modifies a session file.

This procedure now delegates to the standalone CLI reader
(`vaultbot-stuff/vaultbot_backend/session_log_reader.py`), which uses the
canonical event types (`chat_begin`, `assistant_response`, `tool_call`,
`tool_call_result`) instead of reverse-engineering raw websocket
payloads. See `docs/SESSION-LOG-SCHEMA.md` for the full event schema.

## When to Run This

- "What happened in the last session" / "the session about X"
- Recover context after a restart
- Diagnose a failure: see the exact tool calls + errors
- Composing a larger diagnosis (e.g. [[Find-Recent-Errors]] calls this)

## Inputs

- `session` (optional): a UUID, a title substring, or `"latest"`. Default `"latest"`.
- `filter` (optional): `conversation`, `tools`, `errors`, or `all`. Default `all`.

## Steps

### Step 1: Run the CLI reader to get the session transcript

```python
import subprocess
from pathlib import Path

backend_dir = Path(FRAMEWORK_ROOT) / "vaultbot_backend"
session_arg = (args.get("session") or "latest").strip()
filter_arg = (args.get("filter") or "all").strip()

# The CLI reader works without a running backend — it reads JSONL
# files directly from disk. It uses canonical event types (Fix #3)
# and call_id correlation (Fix #5) instead of the old heuristic.
# See docs/SESSION-LOG-SCHEMA.md for the event schema.
result = subprocess.run(
    [
        str(Path(backend_dir) / ".." / ".." / ".venv" / "Scripts" / "python.exe"),
        "-m", "session_log_reader", "read",
        session_arg,
        "--filter", filter_arg,
    ],
    capture_output=True, text=True,
    cwd=str(backend_dir),
    timeout=30,
)

if result.returncode != 0:
    result = f"ERROR: session_log_reader failed: {result.stderr.strip()}"
else:
    result = result.stdout
```

## Why This Exists

After a restart or crash, the context of what happened in a past session
is lost. This procedure is the single read-only entry point for
reconstructing any session — turns, tool calls, exceptions, and thinking —
from its JSONL log. The key tradeoff is that it is strictly read-only, so
it can never corrupt the append-only session files it inspects.

## Related

- [[Find-Recent-Errors]] — composes this to diagnose failures
- [[Analyze-Failure-Log]] — aggregates failure patterns across procedures
- [[Session-Effort-Analysis]] — analyzes token consumption across sessions