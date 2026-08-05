"""Tests for the plan-mode gate (plan_gate.py).

Offline: no LLM, no I/O. Tests the routing heuristic, the explore allowlist,
and the gate-lift condition.

Leaf-module imports only — `import main` is hard-fenced by conftest.py.
"""
from __future__ import annotations

from plan_gate import (
    EXPLORE_TOOLS,
    is_multi_step,
    lifts_gate,
    plan_mode_directive,
)


# ---------------------------------------------------------------------------
# is_multi_step — now a no-op (the gate is round-counter-based, not heuristic)
# ---------------------------------------------------------------------------
def test_simple_question_not_gated():
    assert is_multi_step("what is the vault longevity architecture?") is False


def test_greeting_not_gated():
    assert is_multi_step("hey, how are you doing?") is False


def test_empty_message_not_gated():
    assert is_multi_step("") is False


def test_multistep_research_task_not_heuristic_gated():
    # is_multi_step is now a no-op (always False). The plan gate is enforced
    # by the round counter in chat_handler, not by signal-word matching.
    assert is_multi_step("research this topic and write a note about it") is False


def test_mutation_task_not_heuristic_gated():
    assert is_multi_step("ingest the new physics textbook") is False


def test_build_tool_task_not_heuristic_gated():
    assert is_multi_step("create a tool that lists all MOCs") is False


def test_refactor_task_not_heuristic_gated():
    assert is_multi_step("refactor the retrieval module and add a feature") is False


def test_plan_gate_env_off():
    # is_multi_step always returns False regardless of env — the env knob
    # is no longer used (the gate is round-counter-based in chat_handler).
    assert is_multi_step("research this and write a note") is False


def test_short_question_with_signal_word_not_gated():
    assert is_multi_step("what is the plan?") is False


# ---------------------------------------------------------------------------
# EXPLORE_TOOLS allowlist + lifts_gate
# ---------------------------------------------------------------------------
def test_explore_allowlist_contains_readonly_and_plan():
    assert "vault_search" in EXPLORE_TOOLS
    assert "code_read" in EXPLORE_TOOLS
    assert "plan_task" in EXPLORE_TOOLS
    # Execution tools must NOT be in the explore allowlist.
    assert "vault_research" not in EXPLORE_TOOLS
    assert "safe_write" not in EXPLORE_TOOLS
    assert "tool_create" not in EXPLORE_TOOLS
    assert "textbook_ingest" not in EXPLORE_TOOLS


def test_only_plan_task_lifts_gate():
    assert lifts_gate("plan_task") is True
    assert lifts_gate("vault_search") is False
    assert lifts_gate("safe_write") is False


# ---------------------------------------------------------------------------
# plan_mode_directive — the injected instruction
# ---------------------------------------------------------------------------
def test_directive_mentions_plan_first_and_restriction():
    d = plan_mode_directive()
    assert "PLAN MODE" in d
    assert "plan_task" in d
    # Must forbid execution until a plan exists.
    assert "until a plan exists" in d or "before a plan exists" in d
