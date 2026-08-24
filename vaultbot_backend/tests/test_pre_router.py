from __future__ import annotations

import pytest

from pre_router import pre_route_message

pytestmark = pytest.mark.unit


def test_pre_route_prefers_procedure_for_actionable_requests():
    result = pre_route_message("fix the failing CI check and open a PR")

    assert result["route"] == "procedure"
    assert result["confidence"] >= 0.6
    assert "fix" in result["evidence"]


def test_pre_route_uses_small_model_for_light_questions():
    result = pre_route_message("can you explain what this error means")

    assert result["route"] == "small_model"
    assert result["confidence"] >= 0.3


def test_pre_route_escalates_when_signals_are_unclear():
    result = pre_route_message("we should make the system more helpful")

    assert result["route"] == "escalate"
    assert result["reason"] == "mixed_or_unsettled"
