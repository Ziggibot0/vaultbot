"""VaultBot-specific routing helper built on the generic pre-router."""

from __future__ import annotations

from typing import Any

from next_action_router import build_next_action
from pre_router import pre_route_message


def route_vaultbot_message(text: str) -> dict[str, Any]:
    """Route a VaultBot chat request to a cheap execution path.

    The helper uses lightweight keyword signals and a small set of VaultBot
    defaults to surface a procedure suggestion for repo work, keep simple
    status or explanatory turns on the small-model path, and escalate the rest.
    """
    routed = pre_route_message(text)
    if routed["route"] == "procedure":
        procedure_hint = "Solve-GitHub-Issue"
        if any(term in text.lower() for term in ["merge", "pr", "pull request"]):
            procedure_hint = "Solve-GitHub-Issue"
        elif any(term in text.lower() for term in ["sync", "upstream", "branch"]):
            procedure_hint = "Git-Sync-Upstream"
        return {
            "route": routed["route"],
            "confidence": routed["confidence"],
            "procedure_hint": procedure_hint,
            "next_action": build_next_action(routed["route"], text, routed["evidence"]),
        }
    if routed["route"] == "small_model":
        return {
            "route": routed["route"],
            "confidence": routed["confidence"],
            "procedure_hint": None,
            "next_action": build_next_action(routed["route"], text, routed["evidence"]),
        }
    return {
        "route": routed["route"],
        "confidence": routed["confidence"],
        "procedure_hint": None,
        "next_action": build_next_action(routed["route"], text, routed["evidence"]),
    }
