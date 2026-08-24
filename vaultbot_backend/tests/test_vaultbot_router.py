from __future__ import annotations

from vaultbot_router import route_vaultbot_message


def test_route_vaultbot_message_prefers_procedure_for_repo_work():
    result = route_vaultbot_message("please fix the failing PR and merge it")

    assert result["route"] == "procedure"
    assert result["procedure_hint"] == "Solve-GitHub-Issue"


def test_route_vaultbot_message_uses_small_model_for_simple_questions():
    result = route_vaultbot_message("what is the status of the repo")

    assert result["route"] == "small_model"
    assert result["procedure_hint"] is None


def test_route_vaultbot_message_escalates_for_ambiguous_research():
    result = route_vaultbot_message("help me think through a complex design tradeoff")

    assert result["route"] == "escalate"
    assert result["procedure_hint"] is None
