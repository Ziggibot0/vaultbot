"""Framework-driven planning — the BabyAGI/LangGraph pattern for small models.

THE PROBLEM THIS SOLVES
-----------------------
A 30B local model (qwen3.6:27b) doesn't reliably CHOOSE to call plan_task
voluntarily. It'll answer "hi" directly, or start a 7-step task without
planning and lose the plot. Gate-and-nudge enforcement (blocking exec tools
until the model plans) was complex: two gates, nudge counters, edge cases
for exec vs read-only tools, and a "block then accept" fallback path.

The production-agent solution (AutoGPT, BabyAGI, CrewAI, LangGraph) is
simpler: the FRAMEWORK makes a dedicated planning call BEFORE the agentic
loop starts. The model never has to decide to plan — the framework gets
the plan, stores it in working memory, and starts the loop with the
checklist already in place.

HOW IT WORKS
------------
Before the agentic loop, ``framework_plan`` sends a planning-only prompt
to the chat model (or the small cartridge when configured) and asks for a
JSON plan: ``{"goal": str, "steps": [str, ...]}``. Even trivial messages
("hi") get a 1-step plan. The framework parses the JSON, calls
``wm.set_plan(goal, steps)``, and the agentic loop starts with the plan
already in working memory.

This is one extra LLM round per turn — a cheap, bounded-output call with no
tools, no context, no retrieval. It eliminates all gate machinery.

The model still calls ``update_task`` to mark steps in_progress → completed
during the loop; the framework doesn't drive step transitions. Only the
initial plan is framework-driven.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

_MAX_PLAN_STEPS = 20
_MAX_PLAN_CHARS = 4000  # cap the planning response so a runaway model can't flood

_PLAN_SYSTEM = (
    "You are a planning assistant. Given the user's message, output a JSON "
    "plan with exactly two fields:\n"
    '{"goal": "<one sentence describing what to accomplish>", '
    '"steps": ["<step 1>", "<step 2>", ...]}\n\n'
    "Rules:\n"
    "- Even a trivial message gets a 1-step plan (e.g. greeting → "
    '["respond to the user"]).\n'
    "- Each step is a short imperative (\"search the vault for X\", "
    "\"write a note about Y\").\n"
    "- 1-7 steps. More than 7 means the steps are too granular — merge them.\n"
    "- Output ONLY the JSON. No prose, no markdown fences, no explanation.\n"
)

_PLAN_USER_TMPL = "User message: {msg}\n\nOutput the JSON plan now."


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from the model's response.

    The small model may wrap JSON in markdown fences, prepend prose, or
    include trailing text. This finds the first ``{...}`` block and parses
    it. Returns None if no valid JSON is found.
    """
    # Try the whole text first (best case: model output pure JSON).
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict) and "steps" in obj:
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # Fall back to regex: find the first {...} block (handles markdown
    # fences, leading/trailing prose, etc.).
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        # Try a greedier match for nested-ish structures (the steps list
        # contains strings, not nested objects, so a simple brace match
        # usually suffices, but be permissive).
        match = re.search(r'\{.*?"steps".*?\}', text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        if isinstance(obj, dict) and "steps" in obj:
            return obj
    except (json.JSONDecodeError, ValueError):
        return None
    return None


def framework_plan(
    llm_client: Any,
    user_message: str,
    session_logger: Any = None,
) -> tuple[str, list[str]] | None:
    """Make a framework-driven planning call before the agentic loop.

    Returns ``(goal, steps)`` on success, or ``None`` on failure (the caller
    falls back to a 1-step plan so the loop always has *some* plan).

    Uses a non-streaming ``chat()`` call with no tools — a cheap, bounded
    round. The small model cartridge is preferred (cheap local model) when
    configured, else the big model.
    """
    messages = [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": _PLAN_USER_TMPL.format(msg=user_message[:500])},
    ]
    try:
        resp = llm_client.chat(
            messages=messages,
            tools=None,
            temperature=0.2,
            stream=False,
        )
    except Exception as e:  # noqa: BLE001 — degraded continuation
        if session_logger is not None:
            try:
                session_logger.log("framework_plan_failed", {"error": str(e)})
            except Exception:  # noqa: BLE001
                _log.warning("framework_plan log failed: %s", e)
        return None

    text = ""
    if isinstance(resp, dict):
        text = (resp.get("response") or "").strip()
    elif isinstance(resp, str):
        text = resp.strip()

    if not text:
        if session_logger is not None:
            try:
                session_logger.log("framework_plan_empty", {})
            except Exception:  # noqa: BLE001
                pass
        return None

    if len(text) > _MAX_PLAN_CHARS:
        text = text[:_MAX_PLAN_CHARS]

    plan = _extract_json(text)
    if plan is None:
        if session_logger is not None:
            try:
                session_logger.log("framework_plan_parse_failed", {
                    "response_preview": text[:200],
                })
            except Exception:  # noqa: BLE001
                pass
        return None

    goal = (plan.get("goal") or user_message[:100]).strip()
    steps_raw = plan.get("steps") or []
    if not isinstance(steps_raw, list) or not steps_raw:
        return None

    steps: list[str] = []
    for s in steps_raw[:_MAX_PLAN_STEPS]:
        if isinstance(s, str) and s.strip():
            steps.append(s.strip()[:300])
    if not steps:
        return None

    if session_logger is not None:
        try:
            session_logger.log("framework_plan_built", {
                "goal": goal[:100],
                "steps": len(steps),
            })
        except Exception:  # noqa: BLE001
            pass
    return goal, steps