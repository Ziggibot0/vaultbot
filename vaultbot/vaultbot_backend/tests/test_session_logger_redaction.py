"""Test that SessionLogger redacts secret-shaped values before writing to disk.

Covers two signals:
  1. A dict KEY whose name ends with a secret suffix (api_key, token, ...).
  2. A string VALUE that matches a known provider key shape (sk-..., tvly-...).

Over-redacts rather than under-redacts. A false positive only replaces a log
field with [REDACTED], never breaks the app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


from session_logger import SessionLogger, _redact


def test_redact_dict_key_with_secret_suffix():
    assert _redact({"api_key": "sk-real-key-here-12345"}) == {"api_key": "[REDACTED]"}
    assert _redact({"LLM_API_KEY": "x"}) == {"LLM_API_KEY": "[REDACTED]"}
    assert _redact({"secret": "x"}) == {"secret": "[REDACTED]"}
    assert _redact({"token": "x"}) == {"token": "[REDACTED]"}
    assert _redact({"password": "x"}) == {"password": "[REDACTED]"}


def test_redact_preserves_non_secret_keys():
    assert _redact({"model": "qwen3.6:27b"}) == {"model": "qwen3.6:27b"}
    assert _redact({"round": 3}) == {"round": 3}
    assert _redact({"detail": "some text"}) == {"detail": "some text"}


def test_redact_provider_key_values():
    # The tightened regex (issue #86 Fix #4) only matches strings with
    # a known provider key prefix — not bare 24+ char alnum.
    assert _redact({"x": "sk-abcdef1234567890"}) == {"x": "[REDACTED]"}
    assert _redact({"x": "tvly-abcdef1234567890xyz"}) == {"x": "[REDACTED]"}
    assert _redact({"x": "sk-or-v1-abcdef1234567890xyz"}) == {"x": "[REDACTED]"}
    assert _redact({"x": "xai-abcdef1234567890"}) == {"x": "[REDACTED]"}
    assert _redact({"x": "sk-ant-abcdef1234567890"}) == {"x": "[REDACTED]"}


def test_redact_no_longer_matches_bare_alnum_24_plus():
    """Issue #86 Fix #4: bare 24+ char alnum strings are NOT redacted.

    The old regex ``^[A-Za-z0-9_\-]{24,}$`` caught legitimate
    diagnostic strings. The new regex only matches known provider key
    prefixes.
    """
    long_query = "recent-projects-completed-today"
    assert len(long_query) >= 24
    # Under a non-safe key, a bare alnum string is now preserved
    assert _redact({"unknown_key": long_query}) == {"unknown_key": long_query}
    # A bare 32-char alnum string without a prefix is preserved
    bare_32 = "aB3dE5gH7iJ9kL1mN3oP5qR7sT9uV1wX"
    assert _redact({"x": bare_32}) == {"x": bare_32}


def test_redact_preserves_uuids():
    """UUIDs are identifiers, not secrets — never redacted."""
    uuid_str = "121ea6f7-3733-4b33-9259-68db5398d8bc"
    assert _redact({"session_id": uuid_str}) == {"session_id": uuid_str}


def test_redact_safe_field_allowlist():
    """Issue #86 Fix #4: known-safe fields are never value-redacted."""
    # A 30-char search query under data.query is preserved
    long_query = "what-were-we-working-on-last-week"
    assert len(long_query) >= 24
    assert _redact({"data": {"query": long_query}}) == {"data": {"query": long_query}}
    # User message under data.payload.message is preserved
    long_msg = "this is a long user message that exceeds twenty four chars"
    assert _redact({"data": {"payload": {"message": long_msg}}}) == {
        "data": {"payload": {"message": long_msg}}
    }
    # Note title under data.title is preserved
    long_title = "a-very-long-note-title-that-exceeds"
    assert _redact({"data": {"title": long_title}}) == {"data": {"title": long_title}}


def test_redact_safe_field_still_redacts_secret_key_suffix():
    """Even safe-named fields are redacted if the KEY has a secret suffix."""
    # ``api_key`` is caught by _SECRET_KEY_RE regardless of allowlist
    assert _redact({"api_key": "sk-abcdef1234567890"}) == {"api_key": "[REDACTED]"}


def test_redact_preserves_normal_strings():
    assert _redact({"x": "hello world"}) == {"x": "hello world"}
    assert _redact({"x": "short"}) == {"x": "short"}


def test_redact_nested():
    obj = {"outer": {"api_key": "sk-secret-1234567890", "ok": "keep"}}
    assert _redact(obj) == {"outer": {"api_key": "[REDACTED]", "ok": "keep"}}


def test_redact_list():
    obj = [{"api_key": "sk-x-1234567890"}, {"model": "qwen"}]
    out = _redact(obj)
    assert out[0]["api_key"] == "[REDACTED]"
    assert out[1]["model"] == "qwen"


def test_logger_writes_redacted_to_disk(tmp_path: Path):
    s = SessionLogger(log_dir=str(tmp_path))
    s.log("test", {"api_key": "sk-secret-1234567890abcdef", "model": "qwen3.6:27b"})
    s.close()
    line = s._file_path.read_text(encoding="utf-8").splitlines()[
        1
    ]  # [0] is session_start
    record = json.loads(line)
    assert record["data"]["api_key"] == "[REDACTED]"
    assert record["data"]["model"] == "qwen3.6:27b"


def test_logger_tool_call_has_call_id(tmp_path: Path):
    """Issue #86 Fix #5: log_tool_call emits a unique call_id."""
    s = SessionLogger(log_dir=str(tmp_path))
    s.log_tool_call(tool="vault_search", method="search", inputs={"query": "test"})
    s.log_tool_call(tool="vault_read_note", method="read", inputs={"title": "X"})
    s.close()
    lines = s._file_path.read_text(encoding="utf-8").splitlines()
    tool_call_lines = [
        json.loads(l) for l in lines if json.loads(l).get("event") == "tool_call"
    ]
    assert len(tool_call_lines) == 2
    assert tool_call_lines[0]["data"]["call_id"] == 1
    assert tool_call_lines[1]["data"]["call_id"] == 2


def test_logger_log_tool_result_correlation(tmp_path: Path):
    """Issue #86 Fix #5: log_tool_result pairs with log_tool_call by call_id."""
    s = SessionLogger(log_dir=str(tmp_path))
    s.log_tool_call(tool="vault_search", method="search", inputs={"query": "test"})
    s.log_tool_result(call_id=1, tool="vault_search", result={"hits": 3})
    s.close()
    lines = s._file_path.read_text(encoding="utf-8").splitlines()
    tc = [json.loads(l) for l in lines if json.loads(l).get("event") == "tool_call"][0]
    tr = [json.loads(l) for l in lines if json.loads(l).get("event") == "tool_call_result"][0]
    assert tc["data"]["call_id"] == tr["data"]["call_id"] == 1


def test_logger_next_call_id_is_monotonic(tmp_path: Path):
    """Issue #86 Fix #5: next_call_id returns incrementing IDs."""
    s = SessionLogger(log_dir=str(tmp_path))
    assert s.next_call_id() == 1
    assert s.next_call_id() == 2
    assert s.next_call_id() == 3
    s.close()
