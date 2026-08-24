"""Lightweight pre-routing helper for chat turns.

This module classifies a user message into one of three coarse routes before
any expensive model work begins. The goal is to make cheap routing explicit,
cheap to run, and easy to test.
"""

from __future__ import annotations

import re
from typing import Any


def pre_route_message(
    text: str, rules: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    """Route a message to a cheap execution path.

    The implementation favors deterministic, evidence-based scoring over a
    large prompt. It is intentionally compact so it can run at the start of a
    turn before the main model loop.
    """
    if not text or not text.strip():
        return {
            "route": "small_model",
            "confidence": 0.0,
            "evidence": [],
            "reason": "empty_input",
        }

    if rules is None:
        rules = {
            "procedure": [
                "fix",
                "update",
                "change",
                "edit",
                "run",
                "check",
                "verify",
                "ci",
                "build",
                "test",
                "issue",
                "pr",
                "commit",
                "push",
                "review",
                "restore",
                "clean",
                "merge",
            ],
            "small_model": [
                "what",
                "why",
                "how",
                "explain",
                "summarize",
                "compare",
                "status",
                "help",
                "tell",
                "show",
                "list",
            ],
        }

    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    tokens = set(normalized.split())

    scores: list[tuple[str, int]] = []
    for route, keywords in rules.items():
        keyword_set = set(keywords)
        hit_count = len(tokens & keyword_set)
        if hit_count:
            scores.append((route, hit_count))

    if not scores:
        return {
            "route": "escalate",
            "confidence": 0.2,
            "evidence": [],
            "reason": "mixed_or_unsettled",
        }

    scores.sort(key=lambda item: item[1], reverse=True)
    best_route, best_score = scores[0]

    confidence = min(0.95, 0.35 + (best_score / 10.0))

    if best_route == "procedure" and best_score >= 3:
        return {
            "route": "procedure",
            "confidence": confidence,
            "evidence": sorted(tokens & set(rules["procedure"])),
            "reason": "action_signal",
        }

    if best_route == "small_model" and best_score < 2:
        return {
            "route": "escalate",
            "confidence": confidence,
            "evidence": sorted(tokens & set(rules["small_model"])),
            "reason": "mixed_or_unsettled",
        }

    if best_route == "small_model":
        return {
            "route": "small_model",
            "confidence": confidence,
            "evidence": sorted(tokens & set(rules["small_model"])),
            "reason": "clarification_or_explanation",
        }

    return {
        "route": "escalate",
        "confidence": confidence,
        "evidence": sorted(tokens & set(rules["procedure"])),
        "reason": "mixed_or_unsettled",
    }
