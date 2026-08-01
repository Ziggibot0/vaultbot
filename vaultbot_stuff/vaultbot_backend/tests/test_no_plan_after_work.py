"""Tests for the <done> turn-protocol.

The system prompt tells the model: "To finish, end your text with <done>.
To continue, call a tool." The framework uses this structured marker as the
ONLY deterministic signal for whether the turn is over. No heuristics, no
plan checks, no text-content inspection — just a marker.

This works for any model size: a 14b can follow "end with <done>" because
it's a single token pattern, not a judgment call about whether the work
"looks done".

This is a source-level test (AST scan) because the decision logic is
embedded in the large async handle_chat function which requires the full
Services stack to execute. The test verifies the contract is present and
correct in the source, not that it runs at runtime.

Offline: no LLM, no I/O, no Services. Pure AST inspection.

Leaf-module imports only — `import main` is hard-fenced by conftest.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from error_types import AgentSilentError

_BACKEND = Path(__file__).resolve().parent.parent
_CHAT_HANDLER = _BACKEND / "chat_handler.py"
_AGENT_TOOLS = _BACKEND / "agent_tools.py"


def _load_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _find_raise_nodes(tree: ast.Module) -> list[ast.Raise]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]


def _raise_matches_agent_silent(node: ast.Raise) -> bool:
    exc = node.exc
    if exc is None:
        return False
    if isinstance(exc, ast.Call):
        func = exc.func
        if isinstance(func, ast.Name) and func.id == "AgentSilentError":
            return True
    if isinstance(exc, ast.Name) and exc.id == "AgentSilentError":
        return True
    return False


# ---------------------------------------------------------------------------
# Contract: <done> marker in the system prompt
# ---------------------------------------------------------------------------

class TestDoneMarkerProtocol:
    """The <done> marker must be documented in the system prompt and
    enforced in the chat handler."""

    def test_done_marker_in_system_prompt(self):
        """Both build_system_prompt functions must tell the model to use
        the <done> marker to signal the end of a turn."""
        source = _AGENT_TOOLS.read_text(encoding="utf-8")
        # The protocol instruction must appear in both prompts
        assert source.count("TURN PROTOCOL") >= 2, (
            "The <done> turn protocol must be documented in BOTH "
            "build_system_prompt functions."
        )
        assert "<done>" in source, (
            "The system prompt must mention the <done> marker."
        )

    def test_done_marker_checked_in_chat_handler(self):
        """The chat handler must check for <done> in the model's text to
        decide if the turn is over."""
        source = _CHAT_HANDLER.read_text(encoding="utf-8")
        assert '"<done>" in round_text' in source or \
               "'<done>' in round_text" in source, (
            "The chat handler must check for '<done>' in round_text."
        )

    def test_done_marker_stripped_from_answer(self):
        """When <done> is present, it must be stripped from the final answer
        so the user never sees it."""
        source = _CHAT_HANDLER.read_text(encoding="utf-8")
        assert ".replace(\"<done>\", \"\")" in source or \
               ".replace('<done>', '')" in source, (
            "The <done> marker must be stripped from final_answer before "
            "delivery to the user."
        )

    def test_no_done_marker_nudge_exists(self):
        """When the model produces text without <done> and no tool call,
        the framework must nudge once."""
        source = _CHAT_HANDLER.read_text(encoding="utf-8")
        assert "no_done_marker_nudge" in source, (
            "The chat handler must log 'no_done_marker_nudge' when the model "
            "produces text without <done> and without a tool call."
        )
        assert "_protocol_nudge_used" in source, (
            "The chat handler must track the protocol nudge state via "
            "_protocol_nudge_used."
        )

    def test_no_done_marker_accepts_after_nudge(self):
        """After one nudge, if the model still omits <done>, the text must
        be accepted — the framework does not loop forever."""
        source = _CHAT_HANDLER.read_text(encoding="utf-8")
        assert "no_done_marker_accepted" in source, (
            "After one nudge, the chat handler must accept the text and log "
            "'no_done_marker_accepted'."
        )

    def test_empty_turn_still_fails_loud(self):
        """Empty turns (no text, no tool call) must still fail loud after
        one nudge — the <done> protocol doesn't change this."""
        source = _CHAT_HANDLER.read_text(encoding="utf-8")
        assert "agent_silent_fail_loud" in source, (
            "Empty turns must still fail loud after a nudge."
        )

    def test_no_plan_heuristics_remain(self):
        """The old plan-gated heuristics (Case 2, Case 2b) must be GONE.
        The <done> protocol replaces them entirely."""
        source = _CHAT_HANDLER.read_text(encoding="utf-8")
        # These were the old heuristic-based checks — they must not exist.
        assert "premature_stop_nudge" not in source, (
            "premature_stop_nudge was removed — the <done> protocol replaces it."
        )
        assert "agent_no_plan_after_work_fail_loud" not in source, (
            "agent_no_plan_after_work_fail_loud was removed — the <done> "
            "protocol replaces it."
        )
        assert "_text_nudge_used" not in source, (
            "_text_nudge_used was removed — replaced by _protocol_nudge_used."
        )

    def test_agent_silent_error_still_used(self):
        """AgentSilentError must still be raised for empty turns after a
        nudge — the <done> protocol preserves this fail-loud path."""
        tree = _load_ast(_CHAT_HANDLER)
        raises = _find_raise_nodes(tree)
        agent_silent_raises = [r for r in raises if _raise_matches_agent_silent(r)]
        assert len(agent_silent_raises) >= 1, (
            "AgentSilentError must still be raised for empty turns."
        )

    def test_agent_silent_error_is_real_class(self):
        """AgentSilentError must be a real exception class."""
        assert issubclass(AgentSilentError, Exception)
        with pytest.raises(AgentSilentError):
            raise AgentSilentError("test")

    def test_diagnostics_handles_agent_silent(self):
        """The diagnostics module must still classify AgentSilentError."""
        from diagnostics import classify_error
        try:
            raise AgentSilentError("empty turn after nudge")
        except AgentSilentError as exc:
            diag = classify_error(exc, {"stage": "chat"})
        assert diag.category.value == "agent_silent"
        assert diag.user_message