"""Regression tests for chat_turn_finalize.py — the grounding gate's
tool-sourced answer detection (issue #132).

The grounding check only knows about vault notes, so a correct answer
derived from a live tool call (calendar, code_read, github_issues) would
always show 0% grounded and trigger a scary "may draw on model weights"
warning. The fix detects tool-sourced turns and suppresses that false alarm.

Pure function tests — no Services, no Ollama, no websocket.
"""

from __future__ import annotations

import pytest
from chat_turn_finalize import LIVE_FACT_TOOLS, is_tool_sourced, score_code_grounding

pytestmark = pytest.mark.unit


class TestScoreCodeGrounding:
    """issue #387 — read-only code_run is not a doc-gate bypass."""

    def test_empty_history(self):
        score = score_code_grounding(None)
        assert score == {
            "safe_writes": 0,
            "doc_proven": 0,
            "unproven": 0,
            "bypassed": False,
        }

    def test_readonly_code_run_is_not_bypass(self):
        # Default code_run calls run under the read-only guard — no files
        # can be written, so they must NOT trip the bypass flag.
        score = score_code_grounding([{"tool": "code_run"}])
        assert score["bypassed"] is False
        score = score_code_grounding(
            [{"tool": "code_run", "allow_write": False}]
        )
        assert score["bypassed"] is False

    def test_write_capable_code_run_is_bypass(self):
        score = score_code_grounding(
            [{"tool": "code_run", "allow_write": True}]
        )
        assert score["bypassed"] is True

    def test_code_write_is_bypass(self):
        score = score_code_grounding([{"tool": "code_write"}])
        assert score["bypassed"] is True

    def test_doc_proven_safe_write_counts(self):
        score = score_code_grounding(
            [
                {"tool": "code_run"},
                {"tool": "safe_write", "doc_source": True},
            ]
        )
        assert score["safe_writes"] == 1
        assert score["doc_proven"] == 1
        assert score["unproven"] == 0
        assert score["bypassed"] is False


class TestIsToolSourced:
    """issue #132 — grounding false-alarm on tool-sourced answers."""

    def test_empty_history(self):
        assert is_tool_sourced(None) is False
        assert is_tool_sourced([]) is False

    def test_calendar_tool_is_live(self):
        assert is_tool_sourced([{"tool": "google_workspace"}]) is True
        assert is_tool_sourced([{"tool": "calendar_list"}]) is True

    def test_code_read_is_live(self):
        assert is_tool_sourced([{"tool": "code_read"}]) is True

    def test_github_issues_is_live(self):
        assert is_tool_sourced([{"tool": "github_issues"}]) is True

    def test_vault_search_is_not_live(self):
        # vault_search is retrieval, not a live fact source — it must NOT
        # suppress the grounding gate.
        assert is_tool_sourced([{"tool": "vault_search"}]) is False

    def test_vault_read_note_is_not_live(self):
        assert is_tool_sourced([{"tool": "vault_read_note"}]) is False

    def test_planning_tools_are_not_live(self):
        assert is_tool_sourced([{"tool": "plan_task"}]) is False
        assert is_tool_sourced([{"tool": "update_task"}]) is False

    def test_mixed_history_detects_live(self):
        history = [
            {"tool": "vault_search"},
            {"tool": "google_workspace"},
            {"tool": "plan_task"},
        ]
        assert is_tool_sourced(history) is True

    def test_non_dict_entries_ignored(self):
        assert is_tool_sourced(["not-a-dict", None, 42]) is False

    def test_live_tools_set_is_frozen(self):
        # The set must be immutable so a stray mutation can't silently
        # change the gate's behavior.
        assert isinstance(LIVE_FACT_TOOLS, frozenset)
