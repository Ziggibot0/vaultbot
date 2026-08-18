"""Per-step consolidation — the memory-like summary that replaces raw tool noise.

THE PROBLEM THIS SOLVES
-----------------------
The agentic chat loop sent the full verbatim transcript of every tool call,
tool result, and thinking pass back to the model on every round. For a 7-step
task that's ~7 × (assistant text + thinking + tool_call + tool_result) of
noise re-processed every turn. The model loses the forest for the trees,
re-reads its own chatter, and hits the read-loop wall.

This is the consolidation layer the user asked for (2026-08-02): after each plan
step is marked completed, the raw tool/thinking noise for THAT step is
replaced in the conversation with a compact gist written by the small model
cartridge. The model's next round sees the shapes (summaries), not the
details — same principle as hippocampal memory consolidation: fast detailed
traces → consolidated gist that carries forward.

CONTRACT
--------
``summarize_step`` takes the goal, the step's content, and the raw material
(tool calls + results + thinking) accumulated during the step, and returns a
2-4 sentence summary containing:

  1. WHAT WAS ACCOMPLISHED — the outcome, not the process
  2. LESSONS LEARNED — what worked, what didn't, what to do differently
  3. KEY FACTS THE NEXT STEP NEEDS — numbers, names, paths, decisions that
     later steps depend on (so the model doesn't have to re-retrieve them)

No tool names, no raw output, no "I called X and it returned Y". The summary
is the gist a human would remember a day later.

The summary is written by the SMALL model cartridge (qwen3.5:0.8b when
configured, else the big model) via a non-streaming chat() call. This keeps
the cloud model focused on the actual reasoning work and delegates the
bounded-output summarization to the cheap local model — same pattern as the
procedure cartridge system.
"""

from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

# Hard caps so a runaway small-model summary can't flood the conversation
# the way the raw tool noise did.
_MAX_SUMMARY_CHARS = 600
_MAX_RAW_CHARS = 8000  # cap the raw material we send to the small model

_SUMMARY_SYSTEM = (
    "You are a memory consolidation engine. You are given the raw trace of one "
    "step of a larger plan: the tool calls, their results, and the agent's "
    "thinking during that step. Your job is to write the GIST a person would "
    "remember a day later — not a transcript.\n\n"
    "Write 2-4 sentences containing exactly three things:\n"
    "1. WHAT WAS ACCOMPLISHED — the outcome, not the process.\n"
    "2. LESSONS LEARNED — what worked, what didn't, what to do differently.\n"
    "3. KEY FACTS THE NEXT STEP NEEDS — specific numbers, names, file paths, "
    "or decisions that later steps depend on. Be concrete; the next step "
    "will NOT see the raw tool output, only your summary.\n\n"
    "Rules:\n"
    "- No tool names. No 'I called X'. No raw output. No process narration.\n"
    "- Do not restate the step title — the summary is shown next to it.\n"
    "- If a fact isn't needed by later steps, leave it out. Keep it tight.\n"
    "- Plain prose, no bullets, no headers. 2-4 sentences. One paragraph."
)

_SUMMARY_USER_TMPL = (
    "GOAL (the whole task): {goal}\n"
    "THIS STEP: {step}\n"
    "RAW TRACE (tool calls, results, thinking during this step):\n"
    "{raw}\n\n"
    "Write the consolidated summary of THIS STEP now. 2-4 sentences. "
    "What was accomplished, lessons learned, and key facts the next step needs."
)


def _build_raw_material(
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    thinking: str,
) -> str:
    """Render the step's raw trace into a compact string for the small model.

    Caps each piece so a single verbose tool result can't crowd out the rest.
    The small model only needs the shape, not the full payload.
    """
    parts: list[str] = []
    for i, (call, result) in enumerate(zip(tool_calls, tool_results)):
        name = (
            call.get("function", {}).get("name", "?") if isinstance(call, dict) else "?"
        )
        args = (
            call.get("function", {}).get("arguments", {})
            if isinstance(call, dict)
            else {}
        )
        try:
            args_str = json.dumps(args, default=str)[:400]
        except Exception:  # noqa: BLE001 — args may be non-serializable
            args_str = str(args)[:400]
        try:
            result_str = json.dumps(result, default=str)[:1200]
        except Exception:  # noqa: BLE001
            result_str = str(result)[:1200]
        parts.append(f"[{i + 1}] {name}({args_str}) -> {result_str}")
    if thinking.strip():
        parts.append("THINKING: " + thinking.strip()[:1500])
    raw = "\n".join(parts)
    return raw[:_MAX_RAW_CHARS]


def summarize_step(
    llm_client: Any,
    goal: str,
    step_content: str,
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    thinking: str = "",
    session_logger: Any = None,
) -> str:
    """Produce a consolidated summary of one plan step.

    Uses the provided LLM client (the small cartridge when configured) in a
    non-streaming chat() call. Returns a 2-4 sentence gist.

    On any failure, returns a deterministic one-line fallback ("Step completed;
    see session log for details.") so the loop never blocks on a summarizer
    outage. Failures are logged loudly — this is a *degraded continuation*
    (the chat must not freeze because the summarizer broke), not a silent
    fallback to a different mechanism. The raw trace is preserved in the
    session JSONL either way.
    """
    # Trivial step (no tools, no thinking) — nothing to consolidate.
    if not tool_calls and not thinking.strip():
        return f"Step completed: {step_content[:120]}"

    raw = _build_raw_material(tool_calls, tool_results, thinking)
    user_msg = _SUMMARY_USER_TMPL.format(
        goal=goal[:300],
        step=step_content[:300],
        raw=raw,
    )
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        resp = llm_client.chat(
            messages=messages,
            tools=None,
            temperature=0.3,
            stream=False,
        )
    except Exception as e:  # noqa: BLE001 — degraded continuation, not a mechanism swap
        if session_logger is not None:
            try:
                session_logger.log(
                    "step_summary_failed",
                    {
                        "step": step_content[:80],
                        "error": str(e),
                    },
                )
            except Exception:  # noqa: BLE001 — logging must not raise
                _log.warning("step_summary log failed: %s", e)
        return f"Step completed: {step_content[:120]}"

    # OllamaClient.chat(stream=False) returns a dict with "response".
    summary = ""
    if isinstance(resp, dict):
        summary = (resp.get("response") or "").strip()
    elif isinstance(resp, str):
        summary = resp.strip()

    if not summary:
        return f"Step completed: {step_content[:120]}"

    # Hard cap so a runaway summary can't re-flood the conversation.
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
    return summary
