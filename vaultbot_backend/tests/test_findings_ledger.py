"""Tests for the findings-ledger outcome helper (issue #386).

The findings ledger must reflect the ACTUAL tool result payload, not the
write-failure heuristic: a failed read/search is "failed", a procedure
-suggestion nudge is "suggested", and a clean result is "ok". Pure function
tests — no LLM, no I/O.
"""

import pytest

pytestmark = pytest.mark.unit

from chat_context import round_tool_outcome


def test_clean_result_is_ok():
    assert round_tool_outcome("vault_search", {"results": []}) == "ok"


def test_failed_result_reports_error():
    out = round_tool_outcome("vault_search", {"error": "index not ready"})
    assert out.startswith("failed(")
    assert "index not ready" in out


def test_long_error_is_truncated():
    out = round_tool_outcome("code_run", {"error": "x" * 200})
    assert out == f"failed({'x' * 60})"


def test_procedure_suggestion_is_suggested():
    out = round_tool_outcome("code_run", {"procedure_suggestion": "Git-Sync-Upstream"})
    assert out == "suggested"


def test_proceed_keyword_is_suggested():
    out = round_tool_outcome("safe_write", {"proceed_keyword": "proceed"})
    assert out == "suggested"


def test_suggestion_beats_error_key():
    out = round_tool_outcome(
        "code_run",
        {"procedure_suggestion": "X", "error": "should not surface"},
    )
    assert out == "suggested"


def test_non_dict_result_is_ok():
    assert round_tool_outcome("thought", None) == "ok"
    assert round_tool_outcome("thought", "string result") == "ok"


def test_empty_dict_is_ok():
    assert round_tool_outcome("vault_search", {}) == "ok"
