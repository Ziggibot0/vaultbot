from __future__ import annotations

import pytest
from next_action_router import build_next_action

pytestmark = pytest.mark.unit


def test_build_next_action_sets_explicit_follow_up():
    result = build_next_action(
        "procedure",
        "fix the issue",
        evidence=["fix", "issue"],
    )

    assert result["action"] == "execute_procedure"
    assert result["target"] == "fix"
    assert result["reason"] == "matched_procedure_signal"


def test_build_next_action_uses_small_model_for_explanation():
    result = build_next_action("small_model", "explain this", evidence=["explain"])

    assert result["action"] == "answer_with_small_model"
    assert result["target"] == "explain"


def test_build_next_action_escalates_when_uncertain():
    result = build_next_action("escalate", "help me think through this", evidence=[])

    assert result["action"] == "escalate_to_big_model"
    assert result["target"] == "router"
