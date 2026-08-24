"""Map a coarse route to a concrete next action."""

from __future__ import annotations

from typing import Any


def build_next_action(route: str, text: str, evidence: list[str] | None = None) -> dict[str, Any]:
    """Return a compact next-action object for the route decision."""
    evidence = evidence or []
    if route == "procedure":
        target = (text.split()[0] if text.split() else "fix").lower()
        return {
            "action": "execute_procedure",
            "target": target,
            "reason": "matched_procedure_signal",
            "evidence": evidence,
        }
    if route == "small_model":
        target = evidence[0] if evidence else "explain"
        return {
            "action": "answer_with_small_model",
            "target": target,
            "reason": "light_question",
            "evidence": evidence,
        }
    return {
        "action": "escalate_to_big_model",
        "target": "router",
        "reason": "uncertain",
        "evidence": evidence,
    }
