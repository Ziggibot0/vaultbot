"""Plan-mode gate — deterministic routing to plan-before-execute.

THE PROBLEM THIS SOLVES
-----------------------
Copilot Chat and Claude Code separate a task into Explore → Plan → Implement.
VaultBot's chat loop plans *inline*: the model is told (in the briefing) to
"PLAN FIRST" on multi-step tasks, but nothing enforces it — a weak model can
skip planning and start firing execution tools, which is how it loses the plot
on long tasks. Prompt prose is a nudge, not a gate.

This module makes plan-mode DETERMINISTIC: a cheap, zero-LLM heuristic decides
whether a user turn is likely multi-step. On round 0 of such a turn — before
any plan exists — the chat loop injects a directive that restricts the model
to planning (plan_task) or read-only exploration first. Execution tools
(vault_research, safe_write, tool_create, textbook_ingest, ...) are deferred
until a plan exists. Once the model writes a plan, the gate lifts and the loop
proceeds normally.

WHY A HEURISTIC (not an LLM judge)
----------------------------------
The whole point of the sturdiness plan is "the LLM only calls tools and
synthesizes; the framework decides." A routing decision like "is this
multi-step?" must not itself cost an LLM call. The heuristic is transparent,
tunable, and wrong only in the safe direction: a false positive (a simple Q&A
judged multi-step) just means the model writes a one-step plan first, which is
harmless; a false negative (a multi-step task judged simple) just means no
forced gate — the old inline behavior, which the briefing still encourages.

Pure stdlib. No LLM calls. No I/O.
"""
from __future__ import annotations

import os

# Read-only tools the model may call during plan-mode (Explore phase). These
# gather information without changing anything, so they're safe before a plan
# exists. Everything NOT in this set is an "execution" tool that the gate
# defers until a plan exists.
EXPLORE_TOOLS = frozenset({
    "vault_search", "vault_list", "code_read", "capability_audit",
    "web_read_source", "textbook_read_page", "vault_gaps", "vaultbot_status",
    "plan_task",  # the planning tool itself — always allowed
})

# Signals that a turn is likely multi-step / mutation-heavy. Each is a word
# or phrase; a hit bumps the score. Tuned conservatively (favor false
# negatives over false positives).
_MULTistep_SIGNALS = (
    # explicit multi-step intent
    "step by step", "step-by-step", "plan", "roadmap", "phases", "phase ",
    "multi-step", "multistep", "end to end", "end-to-end",
    # mutation / build intent (these change the vault or code)
    "ingest", "create a tool", "build a tool", "write a procedure",
    "refactor", "restructure", "reorganize", "implement", "add a feature",
    "fix the", "rewrite", "migrate", "overhaul", "self-edit", "edit your",
    "update your", "improve yourself", "audit", "consolidate",
    # research-then-synthesize intent (research → note → answer = multi-step)
    "research and", "research this", "deep dive", "dig into", "investigate",
    "write a note", "write notes", "fill the gap", "fill gaps",
)

# Short, obviously-single-turn openers that should NOT be gated even if they
# contain a signal word (e.g. "what's the plan?" is a question, not a task).
_QUESTION_OPENERS = (
    "what is", "what's", "who is", "who's", "when did", "where is",
    "why does", "why do", "how do i", "how does", "explain", "define",
    "tell me about", "summarize", "is there", "are there", "do you know",
)


def is_multi_step(user_message: str) -> bool:
    """Deterministic heuristic: is this turn likely multi-step?

    Returns True when the message looks like a multi-step or mutation-heavy
    task (the gate should force a plan first). Returns False for simple Q&A,
    greetings, and single-fact lookups (the gate stays off, zero overhead).

    The threshold + signals are tunable via env so it can be tightened or
    loosened without a code change:
      VAULTBOT_PLAN_GATE=off           -> disable the gate entirely
      VAULTBOT_PLAN_GATE_MIN_HITS=N    -> require N signal hits (default 1)
    """
    if os.getenv("VAULTBOT_PLAN_GATE", "on").lower() == "off":
        return False
    msg = (user_message or "").strip().lower()
    if not msg:
        return False

    # A plan already existing is handled by the caller (gate lifts). Here we
    # only judge the raw message.

    # Short question openers are almost always single-turn Q&A. If the whole
    # message is a single short question, don't gate even on a signal hit.
    is_short_question = (
        len(msg) < 90 and msg.endswith("?")
        and any(msg.startswith(op) for op in _QUESTION_OPENERS)
    )

    min_hits = int(os.getenv("VAULTBOT_PLAN_GATE_MIN_HITS", "1"))
    hits = sum(1 for s in _MULTistep_SIGNALS if s in msg)

    if is_short_question and hits < min_hits + 1:
        return False
    return hits >= min_hits


def plan_mode_directive() -> str:
    """The system directive injected when the gate is active (round 0, no plan).

    Tells the model it's in plan/explore mode: it may use read-only tools and
    MUST produce a plan (plan_task) before any execution tool. Concise and
    imperative so a small model can follow it.
    """
    allowed = ", ".join(sorted(EXPLORE_TOOLS - {"plan_task"}))
    return (
        "# PLAN MODE (explore + plan BEFORE executing)\n"
        "This looks like a multi-step task. Before doing any work that "
        "changes the vault, runs research, or edits code, you MUST call "
        "plan_task with a goal + concrete ordered steps. You SHOULD first "
        "use read-only tools to explore and understand the problem "
        f"({allowed}) — reading files, searching the vault, and checking "
        "capabilities are all encouraged BEFORE planning. Take as many "
        "exploration rounds as you need, then call plan_task when you "
        "understand the problem well enough to write concrete steps. "
        "Do NOT call any execution tool (vault_research, safe_write, "
        "js_safe_write, tool_create, textbook_ingest, execute_procedure, "
        "git_rollback, or any custom tool that writes) until a plan "
        "exists. Once you call plan_task, this restriction lifts and "
        "you proceed step by step."
    )


def lifts_gate(tool_name: str) -> bool:
    """Should calling this tool lift the gate? Only plan_task does — once the
    model writes a plan, plan-mode is satisfied and execution may proceed."""
    return tool_name == "plan_task"
