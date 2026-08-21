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

from chat_turn_finalize import LIVE_FACT_TOOLS, is_tool_sourced

pytestmark = pytest.mark.unit


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
