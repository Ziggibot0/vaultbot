"""Standalone CLI reader for VaultBot session JSONL logs.

Provides ``python -m session_log_reader read <uuid|latest|title-substring>``
to produce a human-readable conversation transcript with timestamps, tool
calls, and errors — without a running backend or LLM.

This is the external-facing replacement for the ``Analyze-Session-Log``
procedure, which could only run inside VaultBot via ``code_run`` and
truncated turns to 300 chars. The reader uses the canonical event types
(``chat_begin``, ``assistant_response``, ``tool_call``, ``tool_call_result``,
``exception``) rather than reverse-engineering raw websocket payloads.

See ``docs/SESSION-LOG-SCHEMA.md`` for the full event schema.

Filters (issue #86 Fix #6 — derived categories, not per-event tags):
  --filter conversation   Just user/assistant turns + timestamps
  --filter tools          Tool calls + results + durations
  --filter errors         Exceptions + console errors
  --filter all            Everything (default)
  --json                  Machine-readable JSON output
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Event category mapping (issue #86 Fix #6) ────────────────────────────
# Derived in the reader, not per-event tags in the writer. This keeps
# session_logger.py unchanged and centralizes the taxonomy in one place.
# When new events are added, append them here — the schema doc is the
# source of truth for the full list.
_EVENT_CATEGORIES: dict[str, str] = {
    # lifecycle
    "session_start": "lifecycle",
    "session_end": "lifecycle",
    "session_title": "lifecycle",
    "session_reset": "lifecycle",
    "session_token_total": "lifecycle",
    # conversation
    "chat_begin": "conversation",
    "assistant_response": "conversation",
    "websocket_message": "conversation",
    # tool
    "tool_call": "tool",
    "tool_call_requested": "tool",
    "tool_call_result": "tool",
    "tool_exec_enter": "tool",
    "tool_exec_exit": "tool",
    "code_read_auto_expand": "tool",
    "safe_mode_blocked": "tool",
    # retrieval
    "vault_search": "retrieval",
    "conversation_search": "retrieval",
    "context_resolution": "retrieval",
    "context_budget": "retrieval",
    "search_results_deduped": "retrieval",
    "go_find_out_triggered": "retrieval",
    "go_find_out_failed": "retrieval",
    "auto_research_no_note": "retrieval",
    "auto_research_no_sources": "retrieval",
    "auto_research_failed": "retrieval",
    # llm
    "llm_stream_start": "llm",
    "ollama_chat_call_enter": "llm",
    "model_changed": "llm",
    "prompt_built": "llm",
    "prompt_cache_structure": "llm",
    "token_usage": "llm",
    "token_usage_emit_failed": "llm",
    # error
    "exception": "error",
    "console_error": "error",
    "notify_console_failure": "error",
    "problem_notified": "error",
    # research
    "research_begin": "research",
    "research_error": "research",
    "research_progress_cb_failed": "research",
    "auto_research_note_md_failed": "research",
    "auto_research_index_failed": "research",
    "subagent_research_invoked": "research",
    # framework
    "plan_task_branch_enter": "framework",
    "plan_snapshot": "framework",
    "framework_plan_failed": "framework",
    "procedure_tracking_failed": "framework",
    "procedure_drift_feedback_failed": "framework",
    "procedure_hint_failed": "framework",
    "procedure_surface_failed": "framework",
    # background
    "stress_signal_failed": "background",
    "drift_feedback_failed": "background",
    "vault_changed_failed": "background",
    "lazy_condense_done": "background",
    "lazy_condense_bg_failed": "background",
    "card_refine_done": "background",
    "card_refine_failed": "background",
    "qa_idle_window_done": "background",
    "qa_idle_bg_failed": "background",
    "provenance_verify_skipped_idk": "background",
    "provenance_verify_bg_failed": "background",
    "provenance_verified_emit_failed": "background",
    "provenance_surface_failed": "background",
    "provenance_surface_skipped_idk": "background",
    "model_relevance_tags_failed": "background",
}


def _category(event_name: str) -> str:
    """Return the category for an event name, defaulting to 'misc'."""
    return _EVENT_CATEGORIES.get(event_name, "misc")


def _is_error_event(event_name: str, data: dict[str, Any]) -> bool:
    """Check if an event is an error (has 'error' field or error-category)."""
    if event_name in (
        "exception",
        "console_error",
        "notify_console_failure",
        "problem_notified",
    ):
        return True
    if data.get("error"):
        return True
    return "_failed" in event_name


def _format_timestamp(ts: float) -> str:
    """Convert epoch float to ISO 8601 string."""
    try:
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return f"<invalid ts:{ts}>"


def find_session_file(sessions_dir: Path, query: str) -> Path | None:
    """Find a session JSONL file by UUID, 'latest', or title substring.

    Args:
        sessions_dir: Path to the ``sessions/`` directory.
        query: A UUID, ``"latest"``, or a title substring.

    Returns:
        The matching ``.jsonl`` Path, or ``None`` if not found.
    """
    if not sessions_dir.exists():
        return None

    if query == "latest" or not query:
        files = sorted(
            sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True
        )
        return files[0] if files else None

    # UUID match (36 chars, 4 dashes)
    if len(query) == 36 and query.count("-") == 4:
        cand = sessions_dir / f"{query}.jsonl"
        return cand if cand.exists() else None

    # Title substring match — scan for session_title (or session_start
    # title) event in each file
    matches: list[tuple[float, Path, str]] = []
    for f in sessions_dir.glob("*.jsonl"):
        try:
            found_title = None
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("event") == "session_title":
                    found_title = evt.get("title", "")
                    break  # session_title is authoritative — stop here
                if evt.get("event") == "session_start" and found_title is None:
                    # Use session_start title as a fallback, but keep
                    # scanning in case a session_title event follows.
                    found_title = evt.get("title", "")
            if found_title is not None and query.lower() in found_title.lower():
                matches.append((f.stat().st_mtime, f, found_title))
        except OSError:
            continue
    if matches:
        matches.sort(key=lambda m: m[0], reverse=True)
        return matches[0][1]
    return None


def parse_session_log(file_path: Path) -> dict[str, Any]:
    """Parse a session JSONL file into a structured summary.

    Uses canonical event types (issue #86 Fix #3):
      - ``chat_begin`` for user messages (data.user_message)
      - ``assistant_response`` for assistant replies (data.content)
      - ``tool_call`` / ``tool_call_result`` for tool correlation (data.call_id)
      - ``exception`` / ``console_error`` for errors

    Falls back to websocket_message parsing for older sessions that don't
    have chat_begin/assistant_response events.

    Returns a dict with: title, session_id, started_at, events_count,
    turns, tool_calls, exceptions, console_errors, token_totals.
    """
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    title = "New Session"
    session_id = file_path.stem
    started_at = ""
    token_totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    turns: list[dict[str, Any]] = []
    tool_calls: dict[Any, dict[str, Any]] = {}
    exceptions: list[dict[str, Any]] = []
    console_errors: list[str] = []
    event_count = 0

    for line in lines:
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_count += 1
        ev = evt.get("event", "")
        data = evt.get("data") or {}
        ts = evt.get("timestamp", 0.0)

        if ev == "session_start":
            started_at = evt.get("started_at", "")
            session_id = evt.get("session_id", session_id)
            title = evt.get("title", title)
        elif ev == "session_title":
            title = evt.get("title", title)
        elif ev == "token_usage":
            token_totals["prompt_tokens"] += data.get("prompt_tokens", 0)
            token_totals["completion_tokens"] += data.get("completion_tokens", 0)
        elif ev == "session_token_total":
            # Final totals — override accumulated if present
            token_totals["prompt_tokens"] = data.get(
                "prompt_tokens", token_totals["prompt_tokens"]
            )
            token_totals["completion_tokens"] = data.get(
                "completion_tokens", token_totals["completion_tokens"]
            )
        # ── Conversation (canonical events, Fix #3) ──────────────
        elif ev == "chat_begin":
            turns.append(
                {
                    "role": "user",
                    "content": data.get("user_message", ""),
                    "timestamp": _format_timestamp(ts),
                }
            )
        elif ev == "assistant_response":
            # Use 'content' (authoritative) or fall back to 'text'
            content = data.get("content") or data.get("text", "")
            turns.append(
                {
                    "role": "assistant",
                    "content": content,
                    "timestamp": _format_timestamp(ts),
                }
            )
        # ── Tool calls (with call_id correlation, Fix #5) ─────────
        elif ev == "tool_call":
            call_id = data.get("call_id")
            name = data.get("tool") or data.get("name") or "?"
            args = data.get("inputs") or data.get("args") or {}
            tool_calls[call_id or len(tool_calls) + 1] = {
                "call_id": call_id,
                "tool": name,
                "args": json.dumps(args, default=str),
                "result": None,
                "error": None,
                "duration_ms": data.get("duration_ms"),
                "timestamp": _format_timestamp(ts),
            }
        elif ev == "tool_call_requested":
            call_id = data.get("call_id")
            name = data.get("tool") or "?"
            args = data.get("args") or {}
            key = call_id or f"req_{len(tool_calls) + 1}"
            tool_calls[key] = {
                "call_id": call_id,
                "tool": name,
                "args": json.dumps(args, default=str),
                "result": None,
                "error": None,
                "duration_ms": None,
                "timestamp": _format_timestamp(ts),
            }
        elif ev == "tool_call_result":
            call_id = data.get("call_id")
            # Match by call_id if present, else fall back to reversed walk
            if call_id and call_id in tool_calls:
                entry = tool_calls[call_id]
                entry["result"] = json.dumps(
                    data.get("result") or data.get("result_keys") or "",
                    default=str,
                )
                entry["error"] = data.get("error")
                entry["duration_ms"] = data.get("duration_ms", entry["duration_ms"])
            else:
                # Legacy: match by tool name, first unmatched
                tname = data.get("tool") or ""
                for tc in reversed(list(tool_calls.values())):
                    if tc["result"] is None and (not tname or tc["tool"] == tname):
                        tc["result"] = json.dumps(
                            data.get("result") or data.get("result_keys") or "",
                            default=str,
                        )
                        tc["error"] = data.get("error")
                        tc["duration_ms"] = data.get("duration_ms", tc["duration_ms"])
                        break
        # ── Errors ────────────────────────────────────────────────
        elif ev == "exception":
            msg = (
                data.get("error")
                or data.get("message")
                or json.dumps(data, default=str)
            )
            exceptions.append(
                {
                    "event": ev,
                    "context": data.get("context", ""),
                    "message": msg,
                    "timestamp": _format_timestamp(ts),
                }
            )
        elif ev in ("console_error", "notify_console_failure", "problem_notified"):
            msg = (
                data.get("message")
                or data.get("user_message")
                or json.dumps(data, default=str)
            )
            console_errors.append(f"[{_format_timestamp(ts)}] {msg}")
        elif "exception" in ev.lower() or "_failed" in ev:
            # Catch-all for *_failed events
            msg = data.get("error") or json.dumps(data, default=str)
            exceptions.append(
                {
                    "event": ev,
                    "context": "",
                    "message": msg,
                    "timestamp": _format_timestamp(ts),
                }
            )

    # If no canonical conversation events were found, fall back to
    # parsing websocket_message payloads (legacy sessions, Fix #3)
    if not turns:
        turns = _parse_ws_messages(lines)

    # Convert tool_calls dict to ordered list
    tool_call_list = list(tool_calls.values())

    return {
        "title": title,
        "session_id": session_id,
        "started_at": started_at,
        "events_count": event_count,
        "turns": turns,
        "tool_calls": tool_call_list,
        "exceptions": exceptions,
        "console_errors": console_errors,
        "token_totals": token_totals,
    }


def _parse_ws_messages(lines: list[str]) -> list[dict[str, Any]]:
    """Fallback: extract turns from raw websocket_message events.

    Handles the ``message`` (user, incoming) vs ``content`` (assistant,
    outgoing) field split documented in issue #86 Problem #2.
    """
    turns: list[dict[str, Any]] = []
    current_assistant = ""
    current_ts = ""

    for line in lines:
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("event") != "websocket_message":
            continue
        data = evt.get("data") or {}
        direction = data.get("direction")
        payload = data.get("payload") or {}
        ts = _format_timestamp(evt.get("timestamp", 0.0))

        if direction == "in":
            if current_assistant:
                turns.append(
                    {
                        "role": "assistant",
                        "content": current_assistant,
                        "timestamp": current_ts,
                    }
                )
                current_assistant = ""
            msg = payload.get("message") or payload.get("content") or ""
            if msg:
                turns.append({"role": "user", "content": msg, "timestamp": ts})
        elif direction == "out":
            ptype = payload.get("type")
            if ptype == "answer_chunk":
                current_assistant += payload.get("content") or ""
                current_ts = ts
            elif ptype == "answer_done":
                content = payload.get("content") or ""
                if content:
                    turns.append(
                        {"role": "assistant", "content": content, "timestamp": ts}
                    )
                elif current_assistant:
                    turns.append(
                        {
                            "role": "assistant",
                            "content": current_assistant,
                            "timestamp": ts,
                        }
                    )
                current_assistant = ""
            elif ptype == "thinking":
                pass  # thinking blocks are not conversation turns

    if current_assistant:
        turns.append(
            {"role": "assistant", "content": current_assistant, "timestamp": current_ts}
        )

    return turns


def format_transcript(summary: dict[str, Any], filter_type: str = "all") -> str:
    """Format a parsed session summary as a human-readable transcript.

    Args:
        summary: Output of ``parse_session_log()``.
        filter_type: ``conversation``, ``tools``, ``errors``, or ``all``.
    """
    out: list[str] = []
    tt = summary["token_totals"]

    out.append(f"SESSION: {summary['title']}")
    out.append(f"ID: {summary['session_id']}")
    out.append(f"Started: {summary['started_at']}")
    out.append(
        f"Events: {summary['events_count']}  "
        f"Turns: {len(summary['turns'])}  "
        f"Tool calls: {len(summary['tool_calls'])}  "
        f"Exceptions: {len(summary['exceptions'])}  "
        f"Console errors: {len(summary['console_errors'])}"
    )
    out.append(
        f"Tokens: prompt={tt['prompt_tokens']} completion={tt['completion_tokens']}"
    )

    if filter_type in ("conversation", "all"):
        out.append("")
        out.append("== TURNS ==")
        for i, t in enumerate(summary["turns"]):
            role = t["role"].upper()
            content = (t.get("content") or "").replace("\n", " ")
            ts = t.get("timestamp", "")
            # Don't truncate in the CLI reader — the procedure did 300 chars,
            # but the whole point of the CLI is full visibility (issue #86).
            out.append(f"[{i + 1}] {ts} {role}: {content}")

    if filter_type in ("tools", "all"):
        out.append("")
        out.append("== TOOL CALLS ==")
        for i, tc in enumerate(summary["tool_calls"]):
            tag = " [ERROR]" if tc.get("error") else ""
            dur = f" ({tc['duration_ms']:.0f}ms)" if tc.get("duration_ms") else ""
            call_id = tc.get("call_id", "?")
            result = (tc.get("result") or "")[:500]
            out.append(f"[{i + 1}] #{call_id} {tc['tool']}{tag}{dur}")
            out.append(f"    args: {tc['args']}")
            out.append(f"    -> {result}")

    if filter_type in ("errors", "all") and summary["exceptions"]:
        out.append("")
        out.append("== EXCEPTIONS ==")
        for e in summary["exceptions"]:
            out.append(f"- ({e['event']}) {e.get('context', '')}: {e['message']}")

    if filter_type in ("errors", "all") and summary["console_errors"]:
        out.append("")
        out.append("== CONSOLE ERRORS ==")
        for e in summary["console_errors"]:
            out.append(f"- {e}")

    return "\n".join(out)


def _default_sessions_dir() -> Path:
    """Return the default sessions directory (sibling of this module)."""
    return Path(__file__).parent / "sessions"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``python -m session_log_reader read <query>``."""
    parser = argparse.ArgumentParser(
        prog="session_log_reader",
        description="Read and summarize VaultBot session JSONL logs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    read_p = sub.add_parser("read", help="Read a session log")
    read_p.add_argument(
        "session",
        nargs="?",
        default="latest",
        help="Session UUID, 'latest', or a title substring (default: latest)",
    )
    read_p.add_argument(
        "--filter",
        dest="filter_type",
        choices=["conversation", "tools", "errors", "all"],
        default="all",
        help="Filter by category (default: all)",
    )
    read_p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output as JSON for machine consumption",
    )
    read_p.add_argument(
        "--sessions-dir",
        dest="sessions_dir",
        default=None,
        help="Path to sessions/ directory (default: sibling of this module)",
    )

    list_p = sub.add_parser("list", help="List recent sessions")
    list_p.add_argument(
        "-n",
        "--count",
        type=int,
        default=10,
        help="Number of recent sessions to show (default: 10)",
    )
    list_p.add_argument(
        "--sessions-dir",
        dest="sessions_dir",
        default=None,
        help="Path to sessions/ directory",
    )

    args = parser.parse_args(argv)

    sessions_dir = (
        Path(args.sessions_dir) if args.sessions_dir else _default_sessions_dir()
    )

    if args.command == "list":
        if not sessions_dir.exists():
            print(f"Sessions directory not found: {sessions_dir}", file=sys.stderr)
            return 1
        files = sorted(
            sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True
        )
        for f in files[: args.count]:
            # Read title from first session_title event
            t = "New Session"
            try:
                for line in f.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines():
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("event") == "session_title":
                        t = evt.get("title", t)
                        break
                    if evt.get("event") == "session_start":
                        t = evt.get("title", t)
            except OSError:
                pass
            size_kb = f.stat().st_size / 1024
            print(f"{f.stem}  {t}  ({size_kb:.0f}KB)")
        return 0

    if args.command == "read":
        target = find_session_file(sessions_dir, args.session)
        if target is None:
            print(
                f"ERROR: no session found for: {args.session!r} in {sessions_dir}",
                file=sys.stderr,
            )
            return 1

        summary = parse_session_log(target)

        if args.as_json:
            print(json.dumps(summary, indent=2, default=str))
        else:
            print(format_transcript(summary, filter_type=args.filter_type))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
