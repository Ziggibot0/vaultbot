"""VaultBot-specific routing helper built on the generic pre-router."""

from __future__ import annotations

import re
from typing import Any


def _pre_route_message(text: str) -> dict[str, Any]:
    """Classify a message into procedure, small_model, or escalate."""
    if not text or not text.strip():
        return {"route": "small_model", "confidence": 0.0, "evidence": []}

    procedure_terms = {
        "fix",
        "update",
        "run",
        "check",
        "verify",
        "ci",
        "build",
        "test",
        "issue",
        "issues",
        "github",
        "flywheel",
        "pr",
        "commit",
        "push",
        "review",
        "merge",
        "sync",
        "branch",
    }
    small_terms = {
        "what",
        "why",
        "how",
        "explain",
        "summarize",
        "compare",
        "status",
        "help",
        "show",
        "list",
    }

    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    tokens = set(normalized.split())
    procedure_hits = sorted(tokens & procedure_terms)
    small_hits = sorted(tokens & small_terms)

    if len(procedure_hits) >= 2:
        return {
            "route": "procedure",
            "confidence": min(0.95, 0.35 + (len(procedure_hits) / 10.0)),
            "evidence": procedure_hits,
        }
    if len(small_hits) >= 2:
        return {
            "route": "small_model",
            "confidence": min(0.95, 0.35 + (len(small_hits) / 10.0)),
            "evidence": small_hits,
        }
    return {
        "route": "escalate",
        "confidence": 0.4,
        "evidence": procedure_hits or small_hits,
    }


def _build_next_action(route: str, text: str, evidence: list[str]) -> dict[str, Any]:
    """Map route decisions to a concrete next action."""
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


def route_vaultbot_message(text: str) -> dict[str, Any]:
    """Route a VaultBot chat request to a cheap execution path.

    The helper uses lightweight keyword signals and a small set of VaultBot
    defaults to surface a procedure suggestion for repo work, keep simple
    status or explanatory turns on the small-model path, and escalate the rest.
    """
    routed = _pre_route_message(text)
    lower = text.lower()
    if routed["route"] == "procedure":
        procedure_hint = "Solve-GitHub-Issue"
        # "Get to work on issues" should kick off the flywheel queue runner.
        if (
            any(term in lower for term in ["flywheel", "issue queue", "next issue"])
            or (
                "issue" in lower
                and any(
                    term in lower
                    for term in [
                        "get to work",
                        "start",
                        "autopilot",
                        "in order",
                        "queue",
                        "sweep",
                    ]
                )
            )
        ):
            procedure_hint = "Flywheel-Issue-Autopilot"
        elif any(term in lower for term in ["merge", "pr", "pull request"]):
            procedure_hint = "Solve-GitHub-Issue"
        elif any(term in lower for term in ["sync", "upstream", "branch"]):
            procedure_hint = "Git-Sync-Upstream"
        return {
            "route": routed["route"],
            "confidence": routed["confidence"],
            "procedure_hint": procedure_hint,
            "next_action": _build_next_action(
                routed["route"], text, routed["evidence"]
            ),
        }
    if routed["route"] == "small_model":
        return {
            "route": routed["route"],
            "confidence": routed["confidence"],
            "procedure_hint": None,
            "next_action": _build_next_action(
                routed["route"], text, routed["evidence"]
            ),
        }
    return {
        "route": routed["route"],
        "confidence": routed["confidence"],
        "procedure_hint": None,
        "next_action": _build_next_action(routed["route"], text, routed["evidence"]),
    }
