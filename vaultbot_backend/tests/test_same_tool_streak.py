"""Unit tests for the same-tool streak guard (issue #362)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from chat_agentic_loop import _update_same_tool_streak
from chat_loop_state import TurnState


def _entry(round_idx: int, tool: str, sig: str) -> dict[str, object]:
    return {
        "round": round_idx,
        "tool": tool,
        "result_summary": f"{tool}:{sig}",
        "result_signature": sig,
    }


class TestSameToolStreak:
    def test_counts_repeated_single_tool_with_same_result(self):
        st = TurnState()
        st._turn_tool_history.extend(
            [
                _entry(0, "code_run", "same"),
                _entry(1, "code_run", "same"),
            ]
        )

        st.round_idx = 0
        _update_same_tool_streak(st)
        st.round_idx = 1
        out = _update_same_tool_streak(st)

        assert out["tool_name"] == "code_run"
        assert out["same_result"] is True
        assert st._consecutive_same_tool == 2

    def test_resets_when_result_changes(self):
        st = TurnState()
        st._turn_tool_history.extend(
            [
                _entry(0, "code_run", "before"),
                _entry(1, "code_run", "after"),
            ]
        )

        st.round_idx = 0
        _update_same_tool_streak(st)
        st.round_idx = 1
        out = _update_same_tool_streak(st)

        assert out["same_result"] is False
        assert st._consecutive_same_tool == 1

    def test_resets_when_round_has_multiple_tools(self):
        st = TurnState()
        st._turn_tool_history.extend(
            [
                _entry(0, "code_run", "same"),
                _entry(1, "code_run", "same"),
                _entry(1, "vault_search", "other"),
            ]
        )

        st.round_idx = 0
        _update_same_tool_streak(st)
        st.round_idx = 1
        out = _update_same_tool_streak(st)

        assert out["count"] == 0
        assert st._last_tool_name == ""
