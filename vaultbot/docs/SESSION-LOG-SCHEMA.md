# Session Log Schema

> **Source of truth** for VaultBot session JSONL event types, their `data`
> fields, and how to read them. Generated 2026-08-20 from a grep of all
> `session_logger.log()` / `log_tool_call()` / `log_message()` /
> `log_stage()` / `log_exception()` call sites across
> `vaultbot_backend/*.py`.
>
> **When you add a new event type, add it here.** The CLI reader
> (`session_log_reader.py`) and the `Analyze-Session-Log` procedure both
> consume this schema. If an event is missing from the category table in
> the reader, it falls into the `misc` bucket.

## File Location & Format

Session logs live in `vaultbot/vaultbot_backend/sessions/*.jsonl` (one
file per WebSocket session, named by UUID, append-only, gitignored).

Each line is a JSON object with this top-level shape:

```json
{
  "event": "<event_name>",
  "session_id": "<uuid>",
  "timestamp": 1234567890.123,   // epoch float (seconds), NOT ISO string
  "data": { ... }                 // event-specific payload (may be absent)
}
```

- `timestamp` is a `time.time()` epoch float. Convert with
  `datetime.fromtimestamp(ts, tz=UTC).isoformat()`.
- `session_id` is a UUID string, never redacted (excluded by `_UUID_RE`).
- `data` may be absent for events with no payload.

## Secret Redaction

All records are passed through `_redact()` before writing to disk. See
`session_logger.py` for the implementation. Two signals:

1. **Key-suffix match:** any dict key ending in `api_key`, `key`,
   `secret`, `token`, `password`, `passphrase`, or `credential` is
   replaced with `[REDACTED]` regardless of value.
2. **Provider-key prefix match:** any string value matching
   `^(sk-|sk-or-|tvly-|xai-|sk-ant-)[A-Za-z0-9_\-]{8,}$` is replaced
   with `[REDACTED]`.

**Safe-field allowlist** (issue #86 Fix #4): the following leaf key names
are never value-redacted, even if the string is long:

| Key name | Example path | Why safe |
|---|---|---|
| `message` | `data.payload.message` | User input text |
| `content` | `data.payload.content` | Assistant response text |
| `query` | `data.query` | Search queries (plain text) |
| `topic` | `data.topic` | Research topics |
| `tool` | `data.tool` | Tool names |
| `method` | `data.method` | Tool method names |
| `model` | `data.model` | Model identifiers |
| `title` | `data.title` | Session/note titles |
| `detail` | `data.detail` | Stage detail strings |
| `stage` | `data.stage` | Stage names |
| `context` | `data.context` | Exception context |
| `error` | `data.error` | Error message strings |
| `user_message` | `data.user_message` | `chat_begin` user text |
| `source` | `data.source` | Provenance source labels |
| `name` | `data.name` | Tool call names |
| `msg` | `data.msg` | `qa_worker` messages |

> **Adding a safe field:** add the key name to `_SAFE_FIELD_NAMES` in
> `session_logger.py`. Only add fields that are known-safe by
> construction — never fields that could contain a credential.

## Canonical Conversation Events (issue #86 Fix #3)

The **authoritative** user and assistant messages are logged as
dedicated events, NOT as raw websocket payloads:

| Event | `data` field | When emitted | Emitted by |
|---|---|---|---|
| `chat_begin` | `user_message` (string) | Start of every chat turn | `chat_handler.py:69` |
| `assistant_response` | `content` (string) | Final answer, after streaming completes | `chat_turn_finalize.py:239` |

The `Analyze-Session-Log` procedure and the CLI reader should read
**these events** for conversation transcripts, not the raw
`websocket_message` events. The websocket events are plumbing — they
store user text in `payload.message` and assistant text in
`payload.content` (the field split documented below), but the canonical
events above are the reliable source.

**Fallback:** for older sessions that predate `chat_begin` /
`assistant_response`, the reader falls back to parsing
`websocket_message` events (see "Websocket Message Field Split" below).

### Websocket Message Field Split (legacy)

Raw `websocket_message` events store text differently depending on
direction:

| Direction | Payload type | Text field | Example |
|---|---|---|---|
| `in` (user → backend) | `chat` | `payload.message` | `{"message": "what were we doing last?"}` |
| `out` (backend → user) | `answer_chunk` | `payload.content` | `{"content": "Based on..."}` |
| `out` (backend → user) | `answer_done` | `payload.content` | `{"content": "Full answer..."}` |
| `out` (backend → user) | `thinking` | (no text field) | Thinking block marker |
| `out` (backend → user) | `tool_call` | `payload.tool`, `payload.args` | Tool dispatch to frontend |
| `out` (backend → user) | `tool_result` | `payload.tool`, `payload.summary` | Tool result to frontend |
| `out` (backend → user) | `status` | `payload.content` | Status messages |

## Tool Call Correlation (issue #86 Fix #5)

Tool calls and results are correlated by a `call_id` field — a
monotonically incrementing integer scoped to the session.

### `log_tool_call()` events (framework-level)

Emitted by `duckduckgo_client.py`, `free_search.py`, `vault_graph.py`,
`llm_client.py`, `ollama_client.py`, `vault_indexer.py`, etc. These are
**combined** call+result in a single event:

| Event | `data` fields |
|---|---|
| `tool_call` | `call_id` (int), `tool` (str), `method` (str), `inputs` (dict), `outputs` (any), `duration_ms` (float), `error` (str?) |

### Websocket-facing tool events (chat-loop level)

Emitted by `chat_loop_tools.py` during the agentic loop. These are
**split** across two events with a shared `call_id`:

| Event | `data` fields | When |
|---|---|---|
| `tool_call_requested` | `call_id` (int), `tool` (str), `args` (dict), `round` (int) | Before tool execution |
| `tool_exec_enter` | `tool` (str), `round` (int), `t_ms` (float) | Tool execution starts |
| `tool_exec_exit` | `tool` (str), `round` (int), `duration_ms` (float) | Tool execution ends |
| `tool_call_result` | `call_id` (int), `tool` (str), `round` (int), `duration_ms` (float), `result_keys` (list?) | After tool execution |

**Matching logic:** pair `tool_call_requested` and `tool_call_result` by
`call_id`. If `call_id` is absent (pre-fix sessions), fall back to
matching by tool name with the first unmatched result (the old
`reversed(tool_calls)` heuristic).

## Event Categories (issue #86 Fix #6)

Categories are **derived in the reader** (`session_log_reader.py`'s
`_EVENT_CATEGORIES` dict), not stored as per-event tags in the log.
This keeps the writer (`session_logger.py`) unchanged and centralizes
the taxonomy in one place.

| Category | Description | Example events |
|---|---|---|
| `lifecycle` | Session start/end, title changes | `session_start`, `session_end`, `session_title`, `session_token_total` |
| `conversation` | User/assistant messages | `chat_begin`, `assistant_response`, `websocket_message` |
| `tool` | Tool dispatch and execution | `tool_call`, `tool_call_requested`, `tool_call_result`, `tool_exec_enter`, `tool_exec_exit`, `safe_mode_blocked` |
| `retrieval` | Vault search, RAG, auto-research | `vault_search`, `conversation_search`, `context_budget`, `search_results_deduped`, `go_find_out_triggered`, `auto_research_no_note` |
| `llm` | LLM calls and token tracking | `llm_stream_start`, `ollama_chat_call_enter`, `model_changed`, `prompt_built`, `token_usage` |
| `error` | Exceptions and failures | `exception`, `console_error`, `notify_console_failure`, `problem_notified` |
| `research` | Research handler | `research_begin`, `research_error`, `auto_research_note_md_failed` |
| `framework` | Task planning, procedures | `plan_task_branch_enter`, `plan_snapshot`, `framework_plan_failed`, `procedure_tracking_failed` |
| `background` | Background tasks (QA, drift, condense) | `stress_signal_failed`, `drift_feedback_failed`, `lazy_condense_done`, `qa_idle_window_done`, `provenance_verify_skipped_idk` |
| `misc` | Uncategorized (default) | Any event not in the table above |

**Severity** is also derived: any event with `_failed` in its name or an
`error` field in its `data` is treated as an error-severity event.

## Full Event Reference

### Lifecycle

| Event | `data` fields | Emitted by |
|---|---|---|
| `session_start` | `started_at` (ISO str), `title` (str) | `session_logger.py` `__init__` |
| `session_title` | `title` (str) | `session_logger.py` `set_title()` |
| `session_end` | `closed_at` (ISO str) | `session_logger.py` `close()` |
| `session_token_total` | `prompt_tokens` (int), `completion_tokens` (int), `total_tokens` (int) | `session_logger.py` `close()` |
| `session_reset` | (varies) | `main.py` |

### Conversation

| Event | `data` fields | Emitted by |
|---|---|---|
| `chat_begin` | `user_message` (str) | `chat_handler.py:69` |
| `assistant_response` | `content` (str) | `chat_turn_finalize.py:239` |
| `websocket_message` | `direction` (`in`/`out`), `payload` (dict) | `main.py` via `log_message()` |
| `correction_detected` | `failure_type` (str) | `chat_turn_prep.py:90` |
| `silent_turn_retry` | `round` (int) | `chat_agentic_loop.py` |

### Tool

| Event | `data` fields | Emitted by |
|---|---|---|
| `tool_call` | `call_id`, `tool`, `method`, `inputs`, `outputs`, `duration_ms`, `error` | `session_logger.py` `log_tool_call()` |
| `tool_call_requested` | `call_id`, `tool`, `args`, `round` | `chat_loop_tools.py` |
| `tool_call_result` | `call_id`, `tool`, `round`, `duration_ms`, `result_keys` | `chat_loop_tools.py` |
| `tool_exec_enter` | `tool`, `round`, `t_ms` | `chat_loop_tools.py` |
| `tool_exec_exit` | `tool`, `round`, `duration_ms` | `chat_loop_tools.py` |
| `code_read_auto_expand` | `round`, `file_path`, `prev_source` | `chat_loop_tools.py` |
| `safe_mode_blocked` | `tool` | `chat_tool_dispatch.py:71` |
| `subagent_research_invoked` | `topic` | `chat_tool_dispatch.py:116` |
| `amem_evolve_failed` | `error` | `chat_tool_dispatch.py:307` |

### Retrieval

| Event | `data` fields | Emitted by |
|---|---|---|
| `context_budget` | (budgeted dict) | `context_budgeter.py:26` |
| `search_results_deduped` | `round`, `raw_count`, `already_seen`, `new_results`, `seen_files` | `chat_loop_tools.py` |
| `go_find_out_triggered` | `round`, `consecutive_all_seen`, `query`, `source` | `chat_loop_tools.py` |
| `go_find_out_failed` | `error` | `chat_loop_tools.py:357` |
| `auto_research_failed` | `error` | `chat_turn_prep.py:555` |
| `auto_research_no_note` | (varies) | `research_handler.py` |
| `auto_research_no_sources` | `topic` | `research_handler.py:108` |
| `conversation_search_failed` | `error` | `chat_turn_prep.py:595` |
| `gaps_propose_failed` | `error` | `chat_turn_prep.py:735` |
| `procedure_hint_failed` | `error` | `chat_turn_prep.py:831` |
| `procedure_surface_failed` | `error` | `chat_turn_prep.py:833` |

### LLM

| Event | `data` fields | Emitted by |
|---|---|---|
| `model_changed` | `model` | `llm_client.py:263`, `ollama_client.py:117` |
| `token_usage` | `prompt_tokens`, `completion_tokens` | `chat_turn_finalize.py:204` |
| `token_usage_emit_failed` | `error` | `chat_turn_finalize.py:228` |

### Error

| Event | `data` fields | Emitted by |
|---|---|---|
| `exception` | `traceback` (str), `error` (str), `context` (str) | `session_logger.py` `log_exception()` |
| `problem_notified` | `category`, `user_message`, `source` | `main.py` |
| `console_error` / `notify_console_failure` | `message` | `main.py` |

### Background

| Event | `data` fields | Emitted by |
|---|---|---|
| `stress_signal_failed` | `error` | `chat_background.py:94` |
| `drift_feedback_failed` | `error` | `chat_background.py:164` |
| `lazy_condense_done` | (summary dict) | `chat_background.py:223` |
| `lazy_condense_bg_failed` | `error` | `chat_background.py:347` |
| `card_refine_done` | `refined` | `chat_background.py:337` |
| `card_refine_failed` | `error` | `chat_background.py:339` |
| `qa_idle_window_done` | (summary dict) | `chat_background.py:387` |
| `qa_idle_bg_failed` | `error` | `chat_background.py:389` |
| `provenance_verify_skipped_idk` | (empty) | `chat_background.py:425` |
| `provenance_verify_bg_failed` | `error` | `chat_background.py:477` |
| `provenance_verified_emit_failed` | `error` | `chat_background.py:460` |
| `provenance_surface_failed` | `error` | `chat_turn_finalize.py:178` |
| `provenance_surface_skipped_idk` | (empty) | `chat_turn_finalize.py:180` |
| `model_relevance_tags_failed` | `error` | `chat_background.py:210` |
| `vault_changed_failed` | `error` | `chat_background.py:138` |

### Framework

| Event | `data` fields | Emitted by |
|---|---|---|
| `plan_task_branch_enter` | `t_ms` | `chat_tool_dispatch.py:734` |
| `plan_snapshot` | (snapshot dict) | `chat_tool_dispatch.py:756` |
| `framework_plan_failed` | `error` | `framework_planner.py:125` |
| `procedure_drift_feedback_failed` | `error` | `chat_tool_dispatch.py:644` |
| `procedure_tracking_failed` | `error` | `chat_loop_tools.py:428` |

### Research

| Event | `data` fields | Emitted by |
|---|---|---|
| `research_begin` | `user_message` | `research_handler.py:172` |
| `research_error` | `stage`, `error` | `research_handler.py:238` |
| `auto_research_note_md_failed` | `error` | `research_handler.py:139` |
| `auto_research_index_failed` | `error` | `research_handler.py:155` |
| `research_progress_cb_failed` | `error` | `research_handler.py:70,190` |

### Chat Loop Internals

These are high-volume plumbing events that the CLI reader filters out
by default (they're in the `misc` or `llm` categories):

| Event | `data` fields | Emitted by |
|---|---|---|
| `round_loop_top` | `round` | `chat_agentic_loop.py` |
| `agent_round` | `round` | `chat_agentic_loop.py` |
| `turn_done` | (varies) | `chat_agentic_loop.py` |
| `loop_exit` | `reason` | `chat_agentic_loop.py` |
| `wm_render_failed` | `error` | `chat_agentic_loop.py:233` |
| `wm_restore_failed` | `error` | `chat_handler.py:127` |
| `stream_history_save_failed` | `error` | `chat_agentic_loop.py:684` |
| `chat_checkpoint_save_failed` | `error` | `chat_agentic_loop.py:654` |
| `checkpoint_clear_failed` | `error` | `chat_turn_finalize.py` |
| `preflight_route_failed` | `error` | `chat_turn_prep.py:288` |
| `context_budget_failed` | `error` | `chat_turn_prep.py:682` |
| `context_filter_failed` | `error` | `chat_turn_prep.py:705` |
| `rag_eval_log_failed` | `error` | `chat_turn_prep.py:601` |
| `lazy_condenser_touch_failed` | `error` | `chat_turn_prep.py:614` |
| `correction_detection_failed` | `error` | `chat_turn_prep.py:92` |
| `websocket_send_failed` | `error` | `main.py:951` |
| `websocket_broadcast_failed` | `error` | `main.py:973` |
| `conversation_index_restore_failed` | `error` | `main.py:1016` |

## CLI Reader

```bash
# Read the latest session (human-readable transcript)
python -m session_log_reader read

# Read a specific session by UUID
python -m session_log_reader read 121ea6f7-3733-4b33-9259-68db5398d8bc

# Find a session by title substring
python -m session_log_reader read "temporal awareness"

# Show only the conversation
python -m session_log_reader read --filter conversation

# Show only tool calls and results
python -m session_log_reader read --filter tools

# Show only errors
python -m session_log_reader read --filter errors

# Machine-readable JSON output
python -m session_log_reader read --json

# List recent sessions
python -m session_log_reader list -n 20
```

The CLI reader works **without a running backend** — it reads the JSONL
files directly from disk. This replaces the `Analyze-Session-Log`
procedure's 300-char truncation and backend dependency (issue #86).