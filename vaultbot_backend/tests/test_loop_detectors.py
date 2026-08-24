"""Tests for the stripped-down agentic loop (2026-08-06).

Verifies (via source-level AST inspection — no LLM, no I/O) that:

1. The read-loop detector, identical-call detector, stale-plan detector,
   plan-enforcement gate, and convergence nudge are ALL GONE.
2. The ONLY safety nets remaining are: failed-write streak (3 consecutive
   failed writes) and MAX_ROUNDS (200).
3. The system prompt no longer threatens the model with framework
   enforcement.
4. code_read auto-expand on repeat reads is present.
5. The double-silent failsafe is present.

Offline: no LLM, no I/O, no Services. Pure AST inspection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


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


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_chat_source() -> str:
    """Concatenated source of all chat-related modules."""
    return "\n".join(_source(p) for p in _CHAT_MODULES)


class TestDetectorsRemoved:
    """All framework babysitting detectors must be GONE."""

    def test_read_loop_detector_gone(self):
        src = _all_chat_source()
        assert "_READ_TOOLS" not in src, "Read-loop detector must be removed"
        assert "_turn_read_count" not in src, "Read-loop tracking must be removed"
        assert "_turn_effective_read_count" not in src, (
            "Read-loop tracking must be removed"
        )
        assert "_read_loop_warned_soft" not in src, "Read-loop nudges must be removed"
        assert "_read_loop_warned_hard" not in src, "Read-loop nudges must be removed"
        assert "_read_loop_forced" not in src, "Read-loop force-stop must be removed"
        assert "read_loop_break" not in src, "Read-loop break must be removed"
        assert "read_streak_unplanned" not in src, "Read-streak break must be removed"

    def test_identical_call_detector_gone(self):
        src = _all_chat_source()
        assert "_IDENTICAL_SOFT" not in src, "Identical-call detector must be removed"
        assert "_IDENTICAL_HARD" not in src, "Identical-call detector must be removed"
        assert "_identical_streak" not in src, "Identical-call tracking must be removed"
        assert "_last_call_sig" not in src, "Identical-call tracking must be removed"
        assert "identical_call_loop" not in src, "Identical-call break must be removed"
        assert "identical_call_warn" not in src, "Identical-call nudge must be removed"
        assert "REPEATED CALL" not in src, "Identical-call nudge text must be removed"

    def test_stale_plan_detector_gone(self):
        src = _all_chat_source()
        assert "_STALE_PLAN_ROUNDS" not in src, "Stale-plan detector must be removed"
        assert "_plan_is_stale" not in src, "Stale-plan check must be removed"
        assert "stale_plan_read_loop" not in src, "Stale-plan break must be removed"

    def test_plan_enforcement_gate_gone(self):
        src = _all_chat_source()
        assert "_FORCE_PLAN_ROUNDS" not in src, "Plan-enforcement gate must be removed"
        assert "_plan_gate_active" not in src, "Plan gate must be removed"
        assert "_rounds_without_plan" not in src, "Plan gate tracking must be removed"
        assert "force_plan_triggered" not in src, "Plan gate trigger must be removed"

    def test_convergence_nudge_gone(self):
        src = _all_chat_source()
        assert "_CONVERGENCE_NUDGE_ROUND" not in src, (
            "Convergence nudge must be removed"
        )
        assert "convergence_nudge" not in src, "Convergence nudge log must be removed"
        assert "BUDGET NOTICE" not in src, "Convergence nudge text must be removed"

    def test_dangling_detector_gone(self):
        src = _all_chat_source()
        assert "_dangling_retries" not in src, "Dangling detector must be removed"
        assert "_looks_dangling" not in src, "Dangling detection must be removed"

    def test_plan_continuation_nudge_gone(self):
        src = _all_chat_source()
        assert "_plan_complete_nudge_used" not in src, (
            "Plan-continuation nudge must be removed"
        )
        assert "plan_continuation_nudge" not in src, (
            "Plan-continuation nudge log must be removed"
        )


class TestSafetyNetsRemain:
    """The ONLY safety nets that remain: failed-write streak and MAX_ROUNDS."""

    def test_failed_write_streak_remains(self):
        src = _all_chat_source()
        assert "_turn_failed_write_count" in src, "Failed-write tracking must remain"
        assert "_WRITE_TOOLS" in src, "Write-tool classification must remain"
        assert "_tool_actually_wrote" in src, "Write-success check must remain"

    def test_suggestion_dict_not_counted_as_failed_write(self):
        # A procedure-suggestion result (procedure_suggestion / proceed_keyword)
        # is a nudge, not a write attempt. The failed-write tracker must skip it
        # so a nudge never increments the anti-thrash counter (issue #340).
        src = _all_chat_source()
        assert '"procedure_suggestion" in _tr' in src, (
            "Suggestion-dict skip guard must remain in the failed-write tracker"
        )
        assert '"proceed_keyword" in _tr' in src, (
            "Suggestion-dict skip guard must remain in the failed-write tracker"
        )

    def test_max_rounds_remains(self):
        src = _all_chat_source()
        assert "_MAX_ROUNDS" in src, "MAX_ROUNDS safety net must remain"
        assert 'os.getenv("VAULTBOT_MAX_ROUNDS", "10000")' in src, (
            "VAULTBOT_MAX_ROUNDS default must be 10000"
        )

    def test_double_silent_failsafe_remains(self):
        src = _all_chat_source()
        assert "_double_silent_once" in src, "Double-silent failsafe must remain"
        assert "AgentSilentError" in src, "AgentSilentError must remain"


class TestSystemPromptNoThreats:
    """The system prompt must NOT threaten the model with framework enforcement."""

    def test_no_stale_plan_in_prompt(self):
        src = _source(_AGENT_TOOLS)
        assert "STALE PLAN" not in src, (
            "System prompt must NOT mention STALE PLAN — it's removed"
        )

    def test_no_force_plan_in_prompt(self):
        src = _source(_AGENT_TOOLS)
        assert "framework will force you" not in src, (
            "System prompt must NOT threaten forced planning"
        )
        assert "execution tools are masked" not in src, (
            "System prompt must NOT threaten tool masking"
        )

    def test_no_loop_stop_threat_in_prompt(self):
        src = _source(_AGENT_TOOLS)
        assert "framework treats your plan as stale" not in src, (
            "System prompt must NOT threaten stale-plan stops"
        )
        assert "framework sends you back" not in src, (
            "System prompt must NOT threaten sending back"
        )

    def test_permissive_tone_in_prompt(self):
        src = _source(_AGENT_TOOLS)
        # The prompt communicates framework-handled routing via the
        # PREFLIGHT ROUTING block and the "You are a router" directive.
        # The exact wording has evolved; check for the current phrasing.
        assert "PREFLIGHT ROUTING" in src, (
            "System prompt must tell model the framework handles routing"
        )


class TestCodeReadWholeFile:
    """code_read should auto-expand to whole-file on repeat reads."""

    def test_auto_expand_logic_exists(self):
        src = _all_chat_source()
        assert "code_read_auto_expand" in src, "Missing code_read_auto_expand log event"
        assert 'tool_args["end_line"] = 0' in src, (
            "Auto-expand must set end_line=0 (whole file)"
        )
        assert "_seen_content.get(_cr_fp)" in src, (
            "Auto-expand must check _seen_content for prior reads"
        )
