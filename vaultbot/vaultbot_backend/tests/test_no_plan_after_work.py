"""Tests for the finish_reason turn-protocol with auto-continuation.

The framework uses Ollama's /v1/chat/completions endpoint, which gives us
finish_reason ("stop", "tool_calls", "length") — the structural signal for
turn termination. When the model stops with text (no tool call) after
doing work, the framework auto-nudges it to continue (up to
_MAX_CONTINUE_NUDGES times). The nudge counter resets on every tool call,
so the model can work for days without the user typing "continue".

No <done> marker, no text-content heuristics, no plan-gate enforcement.
The model speaks freely and the framework reads the structured signal.

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
# chat_handler.py was decomposed into leaf modules — tests must scan all of them.
_CHAT_MODULES = [
    _CHAT_HANDLER,
    _BACKEND / "chat_context.py",
    _BACKEND / "chat_preflight.py",
    _BACKEND / "chat_tool_dispatch.py",
    _BACKEND / "chat_turn_prep.py",
    _BACKEND / "chat_turn_finalize.py",
    _BACKEND / "chat_background.py",
    _BACKEND / "chat_agentic_loop.py",
    _BACKEND / "chat_loop_streaming.py",
    _BACKEND / "chat_loop_tools.py",
]


def _load_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _all_chat_source() -> str:
    """Concatenated source of all chat-related modules."""
    return "\n".join(p.read_text(encoding="utf-8") for p in _CHAT_MODULES)


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
# Contract: finish_reason-based turn protocol
# ---------------------------------------------------------------------------


class TestFinishReasonProtocol:
    """The finish_reason signal from /v1/chat/completions must be used as
    the primary turn-termination signal, with the continuation-intent
    engine as a secondary check."""

    def test_turn_protocol_in_system_prompt(self):
        """The build_system_prompt_briefing function must document the turn
        protocol (tool call to continue, text to finish)."""
        source = _AGENT_TOOLS.read_text(encoding="utf-8")
        assert source.count("TURN PROTOCOL") >= 1, (
            "The turn protocol must be documented in the system prompt."
        )
        # The <done> marker must NOT be in the system prompt anymore.
        assert "<done>" not in source, (
            "The <done> marker must be removed from the system prompt — "
            "finish_reason is the sole structural signal now."
        )

    def test_finish_reason_recorded_in_chat_handler(self):
        """The chat handler must still record finish_reason (for logging),
        even though the nudges that consumed it were removed."""
        source = _all_chat_source()
        assert "finish_reason" in source, (
            "The chat handler must read finish_reason from the /v1 endpoint."
        )
        assert "turn_done" in source, (
            "The chat handler must log 'turn_done' when accepting an answer."
        )

    def test_auto_continuation_removed(self):
        """The auto-continue nudge must be GONE — the framework no longer
        nudges the model to keep working after it stops with text. A turn
        with any text is the final answer (model-driven, identical for
        local 30B and cloud)."""
        source = _all_chat_source()
        assert "auto_continue_nudge" not in source, (
            "The auto-continue nudge must be removed."
        )
        assert "_continue_nudges" not in source, (
            "The continue-nudge counter must be removed."
        )
        assert "_MAX_CONTINUE_NUDGES" not in source, (
            "The _MAX_CONTINUE_NUDGES bound must be removed."
        )

    def test_continue_nudge_counter_removed(self):
        """No continue-nudge counter should exist anywhere — the reset on
        tool-call is gone with the counter itself."""
        source = _all_chat_source()
        assert "_continue_nudges = 0" not in source, (
            "The continue-nudge reset must be removed."
        )

    def test_truncation_nudge_removed(self):
        """The finish_reason='length' truncation nudge must be GONE — a
        30B local model that stops should not be re-prompted by a char/
        length heuristic."""
        source = _all_chat_source()
        assert "truncation_nudge" not in source, "The truncation nudge must be removed."
        assert 'finish_reason == "length"' not in source, (
            "No finish_reason='length' special-casing should remain."
        )

    def test_empty_turn_still_fails_loud(self):
        """The double-silent failsafe must remain: two consecutive turns
        with no text AND no thinking fail loud (never a blank screen)."""
        source = _all_chat_source()
        assert "_double_silent_once" in source, (
            "The double-silent retry flag must be present."
        )
        assert "agent_silent_fail_loud" in source, (
            "Two silent turns must still fail loud."
        )
        assert "silent_turn_retry" in source, (
            "A single silent turn gets ONE retry, then fails loud."
        )

    def test_no_plan_gate_in_chat_loop(self):
        """The OLD heuristic plan-gate must be GONE. The NEW one-rule plan
        enforcement (round-counter-based, not signal-word-based) is allowed.

        The old gate used _FORCE_PLAN_ON_MULTI, plan_gate_forced, and
        from plan_gate import. The new enforcement uses _rounds_without_plan
        (a simple counter) and _plan_gate_active (a single boolean mask).
        The new approach is NOT a heuristic — it's one rule: if the model
        works N rounds without a plan, execution tools are masked until it
        calls plan_task. No signal-word matching, no message-text analysis.
        """
        source = _all_chat_source()
        # OLD heuristics that must stay gone:
        assert "_FORCE_PLAN_ON_MULTI" not in source, (
            "The old force-plan-on-multi heuristic must be removed."
        )
        assert "plan_gate_forced" not in source, (
            "The old plan_gate_forced log must be removed."
        )
        # The old _EXEC_TOOLS gate set must stay gone:
        assert "_EXEC_TOOLS" not in source, "The _EXEC_TOOLS gate set must be removed."
        assert "plan_gate_blocked" not in source, (
            "The plan_gate_blocked log must be removed."
        )
        assert "no_plan_text_gate" not in source, (
            "The no_plan_text_gate must be removed."
        )

    def test_no_read_loop_detector(self):
        """The read-loop detector must be GONE — it was a heuristic that
        nudged the model to stop reading."""
        source = _all_chat_source()
        assert "_READ_LOOP_STREAK" not in source, (
            "The read-loop streak tracker must be removed."
        )
        assert "read_loop_nudge" not in source, (
            "The read_loop_nudge log must be removed."
        )

    def test_no_code_read_dedup_nudge(self):
        """The code_read dedup nudge must be GONE — it appended 'you
        already read this file' notices to tool results."""
        source = _all_chat_source()
        assert "code_read_duplicate" not in source, (
            "The code_read_duplicate log must be removed."
        )
        assert "_dedup_notice" not in source, "The dedup notice must be removed."

    def test_no_synthesize_forced(self):
        """The forced-synthesize nudge (all_done → 'synthesize now') must
        be GONE — the model decides when to synthesize."""
        source = _all_chat_source()
        assert "_synthesize_requested" not in source, (
            "The _synthesize_requested state must be removed."
        )
        assert "working_memory_all_done" not in source, (
            "The working_memory_all_done forced-synthesize log must be removed."
        )

    def test_no_remaining_nudges(self):
        """No other nudge mechanism should survive: no truncation nudge and
        no old empty-answer nudge remain after the simplification."""
        source = _all_chat_source()
        assert "truncation_nudge" not in source, "The truncation nudge must be removed."
        assert "empty_answer_nudge" not in source, (
            "The empty_answer_nudge must be removed."
        )
        assert "_empty_nudge_used" not in source, (
            "The _empty_nudge_used flag must be removed."
        )

    def test_agent_silent_error_still_used(self):
        """AgentSilentError must still be raised for the double-silent
        failsafe — the one contract preserved from the old protocol."""
        tree = ast.parse(_all_chat_source())
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
