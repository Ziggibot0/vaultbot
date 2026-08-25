"""Tests for issue #388 — interim reasoning stays out of the final answer.

The agentic loop streams narration during tool-calling rounds ("Let me
check X..."). That text used to be folded into ``final_answer``, so the
persisted chat history and chat notes read like a scratchpad. The fix
routes mid-loop text to ``TurnState.interim_text`` and only the final
synthesis round to ``final_answer``.

Source-level checks (no LLM, no I/O) plus a TurnState field default test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from chat_loop_state import TurnState

_BACKEND = Path(__file__).resolve().parent.parent
_LOOP_SRC = (_BACKEND / "chat_agentic_loop.py").read_text(encoding="utf-8")
_STREAM_SRC = (_BACKEND / "chat_loop_streaming.py").read_text(encoding="utf-8")


def test_turn_state_has_interim_text_default_empty():
    st = TurnState()
    assert st.interim_text == ""
    assert st.final_answer == ""


def test_mid_loop_text_goes_to_interim_not_final():
    # The accumulation right after tool execution must target interim_text.
    assert "st.interim_text += round_text" in _LOOP_SRC, (
        "mid-loop narration must accumulate into interim_text"
    )


def test_final_round_replaces_final_answer():
    # The no-tool-call round is the clean synthesis — it REPLACES
    # final_answer rather than appending to accumulated chatter.
    assert "st.final_answer = round_text" in _LOOP_SRC, (
        "final synthesis round must set final_answer directly"
    )


def test_crash_partial_still_captures_everything():
    # Crash protection must not lose interim narration.
    assert "st.interim_text + st.final_answer" in _LOOP_SRC, (
        "crash partial must include interim text"
    )


def test_streaming_partial_includes_interim():
    assert "st.interim_text + st.final_answer + round_text" in _STREAM_SRC, (
        "debounced partial write must include interim text"
    )
