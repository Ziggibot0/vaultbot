"""Tests for the session_log_reader CLI and parsing logic.

Covers:
  - parse_session_log: canonical event extraction (chat_begin, assistant_response)
  - parse_session_log: fallback to websocket_message for legacy sessions
  - parse_session_log: tool call correlation by call_id
  - parse_session_log: tool call legacy fallback (reversed-walk)
  - find_session_file: UUID, latest, title-substring matching
  - format_transcript: filter output (conversation, tools, errors, all)
  - _category / _is_error_event: derived category mapping (Fix #6)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from session_log_reader import (
    _category,
    _is_error_event,
    find_session_file,
    format_transcript,
    parse_session_log,
)


def _write_session(
    path: Path,
    events: list[dict],
    session_id: str = "test-uuid-1234",
    title: str = "Test Session",
) -> None:
    """Write a list of event dicts as JSONL to a file."""
    lines = []
    for evt in events:
        lines.append(json.dumps(evt, default=str))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _evt(
    event: str,
    data: dict | None = None,
    ts: float = 1000.0,
    sid: str = "test-uuid-1234",
) -> dict:
    """Build a minimal event dict."""
    out = {"event": event, "session_id": sid, "timestamp": ts}
    if data is not None:
        out["data"] = data
    return out


# ── parse_session_log: canonical events ────────────────────────────────


def test_parse_canonical_conversation(tmp_path: Path):
    """Fix #3: chat_begin/assistant_response are canonical conversation events."""
    f = tmp_path / "test-uuid-1234.jsonl"
    # session_start writes title at the TOP LEVEL, not inside data
    _write_session(
        f,
        [
            {
                "event": "session_start",
                "session_id": "test-uuid-1234",
                "timestamp": 1000.0,
                "started_at": "2026-08-20T10:00:00+00:00",
                "title": "Test",
            },
            _evt("chat_begin", {"user_message": "Hello world"}, ts=1001.0),
            _evt("assistant_response", {"content": "Hi there!"}, ts=1002.0),
            _evt("session_end", {"closed_at": "2026-08-20T10:05:00+00:00"}, ts=1003.0),
        ],
    )
    summary = parse_session_log(f)
    assert summary["title"] == "Test"
    assert len(summary["turns"]) == 2
    assert summary["turns"][0]["role"] == "user"
    assert summary["turns"][0]["content"] == "Hello world"
    assert summary["turns"][1]["role"] == "assistant"
    assert summary["turns"][1]["content"] == "Hi there!"
    assert summary["events_count"] == 4


def test_parse_assistant_response_text_fallback(tmp_path: Path):
    """assistant_response may use 'text' instead of 'content' (chat_turn_prep)."""
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt("chat_begin", {"user_message": "hi"}),
            _evt("assistant_response", {"text": "trivial response"}),
        ],
    )
    summary = parse_session_log(f)
    assert summary["turns"][-1]["content"] == "trivial response"


def test_parse_token_usage(tmp_path: Path):
    """token_usage events are accumulated; session_token_total overrides."""
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt("token_usage", {"prompt_tokens": 100, "completion_tokens": 50}),
            _evt("token_usage", {"prompt_tokens": 200, "completion_tokens": 80}),
            _evt(
                "session_token_total",
                {"prompt_tokens": 300, "completion_tokens": 130, "total_tokens": 430},
            ),
        ],
    )
    summary = parse_session_log(f)
    assert summary["token_totals"]["prompt_tokens"] == 300
    assert summary["token_totals"]["completion_tokens"] == 130


# ── parse_session_log: websocket fallback ──────────────────────────────


def test_parse_websocket_fallback(tmp_path: Path):
    """Fix #3: legacy sessions without chat_begin fall back to websocket parsing."""
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "Legacy"},
            ),
            _evt(
                "websocket_message",
                {
                    "direction": "in",
                    "payload": {"type": "chat", "message": "old style question"},
                },
                ts=1000.0,
            ),
            _evt(
                "websocket_message",
                {
                    "direction": "out",
                    "payload": {"type": "answer_done", "content": "old style answer"},
                },
                ts=1001.0,
            ),
        ],
    )
    summary = parse_session_log(f)
    assert len(summary["turns"]) == 2
    assert summary["turns"][0]["role"] == "user"
    assert summary["turns"][0]["content"] == "old style question"
    assert summary["turns"][1]["role"] == "assistant"
    assert summary["turns"][1]["content"] == "old style answer"


def test_parse_websocket_answer_chunks_assembled(tmp_path: Path):
    """answer_chunk events are assembled into a single assistant turn."""
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt(
                "websocket_message",
                {"direction": "in", "payload": {"type": "chat", "message": "hi"}},
                ts=1000.0,
            ),
            _evt(
                "websocket_message",
                {
                    "direction": "out",
                    "payload": {"type": "answer_chunk", "content": "Hello "},
                },
                ts=1001.0,
            ),
            _evt(
                "websocket_message",
                {
                    "direction": "out",
                    "payload": {"type": "answer_chunk", "content": "world!"},
                },
                ts=1002.0,
            ),
            _evt(
                "websocket_message",
                {"direction": "out", "payload": {"type": "answer_done", "content": ""}},
                ts=1003.0,
            ),
        ],
    )
    summary = parse_session_log(f)
    assert len(summary["turns"]) == 2
    assert summary["turns"][1]["content"] == "Hello world!"


# ── parse_session_log: tool call correlation ───────────────────────────


def test_parse_tool_call_with_call_id(tmp_path: Path):
    """Fix #5: tool_call and tool_call_result are correlated by call_id."""
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt(
                "tool_call_requested",
                {
                    "call_id": 1,
                    "tool": "vault_search",
                    "args": {"query": "test"},
                    "round": 0,
                },
                ts=1000.0,
            ),
            _evt(
                "tool_call_result",
                {
                    "call_id": 1,
                    "tool": "vault_search",
                    "round": 0,
                    "duration_ms": 42.5,
                    "result_keys": ["results"],
                },
                ts=1001.0,
            ),
            _evt(
                "tool_call_requested",
                {
                    "call_id": 2,
                    "tool": "vault_read_note",
                    "args": {"title": "X"},
                    "round": 1,
                },
                ts=1002.0,
            ),
            _evt(
                "tool_call_result",
                {
                    "call_id": 2,
                    "tool": "vault_read_note",
                    "round": 1,
                    "duration_ms": 10.0,
                    "result_keys": ["content"],
                },
                ts=1003.0,
            ),
        ],
    )
    summary = parse_session_log(f)
    assert len(summary["tool_calls"]) == 2
    tc0 = summary["tool_calls"][0]
    tc1 = summary["tool_calls"][1]
    assert tc0["call_id"] == 1
    assert tc0["tool"] == "vault_search"
    assert "42.5" in str(tc0["duration_ms"])
    assert tc1["call_id"] == 2
    assert tc1["tool"] == "vault_read_note"


def test_parse_tool_call_legacy_fallback(tmp_path: Path):
    """Pre-fix sessions without call_id use the reversed-walk name-matching fallback."""
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt(
                "tool_call_requested",
                {"tool": "vault_search", "args": {"query": "test"}, "round": 0},
                ts=1000.0,
            ),
            _evt(
                "tool_call_result",
                {
                    "tool": "vault_search",
                    "round": 0,
                    "duration_ms": 15.0,
                    "result_keys": ["results"],
                },
                ts=1001.0,
            ),
        ],
    )
    summary = parse_session_log(f)
    assert len(summary["tool_calls"]) == 1
    tc = summary["tool_calls"][0]
    assert tc["tool"] == "vault_search"
    assert tc["result"] is not None


def test_parse_log_tool_call_combined_event(tmp_path: Path):
    """log_tool_call() emits a combined tool_call event with inputs+outputs."""
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt(
                "tool_call",
                {
                    "call_id": 1,
                    "tool": "free_search",
                    "method": "search",
                    "inputs": {"q": "test"},
                    "outputs": {"hits": 3},
                    "duration_ms": 100.0,
                },
                ts=1000.0,
            ),
        ],
    )
    summary = parse_session_log(f)
    assert len(summary["tool_calls"]) == 1
    tc = summary["tool_calls"][0]
    assert tc["tool"] == "free_search"
    assert "test" in tc["args"]


# ── parse_session_log: errors ──────────────────────────────────────────


def test_parse_exceptions(tmp_path: Path):
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt(
                "exception",
                {
                    "error": "ValueError: bad thing",
                    "context": "tool_vault_search",
                    "traceback": "...",
                },
            ),
            _evt("console_error", {"message": "something broke"}),
            _evt("query_rewrite_failed", {"error": "timeout"}),
        ],
    )
    summary = parse_session_log(f)
    assert len(summary["exceptions"]) == 2  # exception + query_rewrite_failed
    assert summary["exceptions"][0]["context"] == "tool_vault_search"
    assert len(summary["console_errors"]) == 1
    assert "something broke" in summary["console_errors"][0]


# ── find_session_file ──────────────────────────────────────────────────


def test_find_session_by_uuid(tmp_path: Path):
    f = tmp_path / "abc12345-1234-1234-1234-123456789abc.jsonl"
    _write_session(f, [_evt("session_start", {"started_at": "x", "title": "T"})])
    result = find_session_file(tmp_path, "abc12345-1234-1234-1234-123456789abc")
    assert result == f


def test_find_session_latest(tmp_path: Path):
    import time

    f1 = tmp_path / "aaa11111-1111-1111-1111-111111111111.jsonl"
    f2 = tmp_path / "bbb22222-2222-2222-2222-222222222222.jsonl"
    _write_session(f1, [_evt("session_start", {"started_at": "x", "title": "Old"})])
    time.sleep(0.05)  # ensure f2 is newer
    _write_session(f2, [_evt("session_start", {"started_at": "x", "title": "New"})])
    result = find_session_file(tmp_path, "latest")
    assert result == f2


def test_find_session_by_title_substring(tmp_path: Path):
    f = tmp_path / "ccc33333-3333-3333-3333-333333333333.jsonl"
    # session_title writes title at the TOP LEVEL
    _write_session(
        f,
        [
            {
                "event": "session_start",
                "session_id": "ccc33333-3333-3333-3333-333333333333",
                "timestamp": 1000.0,
                "started_at": "x",
                "title": "New Session",
            },
            {
                "event": "session_title",
                "session_id": "ccc33333-3333-3333-3333-333333333333",
                "timestamp": 1001.0,
                "title": "Temporal Awareness Debug",
            },
        ],
    )
    result = find_session_file(tmp_path, "temporal")
    assert result == f


def test_find_session_not_found(tmp_path: Path):
    result = find_session_file(tmp_path, "nonexistent-uuid-here-1234567890ab")
    assert result is None


def test_find_session_dir_missing(tmp_path: Path):
    missing = tmp_path / "no_such_dir"
    result = find_session_file(missing, "latest")
    assert result is None


# ── format_transcript filters ──────────────────────────────────────────


def test_format_transcript_conversation_filter(tmp_path: Path):
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt("chat_begin", {"user_message": "hello"}),
            _evt("assistant_response", {"content": "hi back"}),
            _evt(
                "tool_call",
                {
                    "call_id": 1,
                    "tool": "vault_search",
                    "method": "search",
                    "inputs": {},
                    "outputs": {},
                },
            ),
        ],
    )
    summary = parse_session_log(f)
    text = format_transcript(summary, filter_type="conversation")
    assert "== TURNS ==" in text
    assert "hello" in text
    assert "hi back" in text
    assert "== TOOL CALLS ==" not in text


def test_format_transcript_tools_filter(tmp_path: Path):
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt("chat_begin", {"user_message": "hello"}),
            _evt(
                "tool_call",
                {
                    "call_id": 1,
                    "tool": "vault_search",
                    "method": "search",
                    "inputs": {"q": "x"},
                    "outputs": {},
                    "duration_ms": 50.0,
                },
            ),
        ],
    )
    summary = parse_session_log(f)
    text = format_transcript(summary, filter_type="tools")
    assert "== TOOL CALLS ==" in text
    assert "vault_search" in text
    assert "== TURNS ==" not in text


def test_format_transcript_all_filter(tmp_path: Path):
    f = tmp_path / "test.jsonl"
    _write_session(
        f,
        [
            _evt(
                "session_start",
                {"started_at": "2026-01-01T00:00:00+00:00", "title": "T"},
            ),
            _evt("chat_begin", {"user_message": "hi"}),
            _evt("assistant_response", {"content": "hello"}),
            _evt(
                "tool_call",
                {"call_id": 1, "tool": "x", "method": "m", "inputs": {}, "outputs": {}},
            ),
            _evt("exception", {"error": "boom", "context": "test"}),
        ],
    )
    summary = parse_session_log(f)
    text = format_transcript(summary, filter_type="all")
    assert "== TURNS ==" in text
    assert "== TOOL CALLS ==" in text
    assert "== EXCEPTIONS ==" in text


# ── derived categories (Fix #6) ────────────────────────────────────────


def test_category_mapping():
    assert _category("chat_begin") == "conversation"
    assert _category("tool_call") == "tool"
    assert _category("session_start") == "lifecycle"
    assert _category("exception") == "error"
    assert _category("vault_search") == "retrieval"
    assert _category("unknown_event") == "misc"


def test_is_error_event():
    assert _is_error_event("exception", {}) is True
    assert _is_error_event("console_error", {}) is True
    assert _is_error_event("notify_console_failure", {}) is True
    assert _is_error_event("something_failed", {}) is True
    assert _is_error_event("tool_call", {"error": "oops"}) is True
    assert _is_error_event("tool_call", {}) is False
    assert _is_error_event("chat_begin", {}) is False
