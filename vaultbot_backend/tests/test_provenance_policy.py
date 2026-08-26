"""Unit tests for the fail-closed provenance delivery policy."""

import pytest
from provenance_policy import (
    build_truth_gap,
    decide_delivery,
    is_pure_acknowledgement,
    verdicts_from_summary,
)

pytestmark = pytest.mark.unit


def test_all_supported_claims_are_deliverable():
    decision = decide_delivery(
        [
            {"claim": "Alpha", "verdict": "supported"},
            {"claim": "Beta", "verdict": "supported"},
        ]
    )

    assert decision.deliverable is True
    assert decision.disposition == "deliver"
    assert decision.supported_claims == 2


def test_all_unsupported_session_regression_fails_closed():
    decision = decide_delivery(
        [{"claim": f"claim {index}", "verdict": "unsupported"} for index in range(7)]
    )

    assert decision.deliverable is False
    assert decision.disposition == "insufficient_evidence"
    assert decision.unsupported_claims == 7
    assert decision.as_dict()["deliverable"] is False


def test_contradiction_takes_precedence_over_missing_support():
    decision = decide_delivery(
        [
            {"claim": "Alpha", "verdict": "unsupported"},
            {"claim": "Beta", "verdict": "contradicted"},
        ]
    )

    assert decision.deliverable is False
    assert decision.disposition == "conflicting_evidence"


@pytest.mark.parametrize("verdicts", [None, [], [{}], [{"verdict": "mystery"}]])
def test_missing_or_unknown_verdicts_are_unverifiable(verdicts):
    decision = decide_delivery(verdicts)

    assert decision.deliverable is False
    assert decision.disposition == "verification_unavailable"


def test_pure_acknowledgement_is_exempt():
    decision = decide_delivery(None, substantive=False)

    assert decision.deliverable is True
    assert decision.disposition == "acknowledgement"


@pytest.mark.parametrize("answer", ["ok", "Got it.", "Thank you", "I'm ready!"])
def test_strict_acknowledgements_are_non_substantive(answer):
    assert is_pure_acknowledgement(answer) is True


@pytest.mark.parametrize(
    "answer",
    [
        "Issue 335 is easiest.",
        "Use the citation gate.",
        "The fix worked.",
        "First claim. Second claim.",
    ],
)
def test_short_claims_are_not_acknowledgements(answer):
    assert is_pure_acknowledgement(answer) is False


def test_malformed_summary_produces_no_verdicts():
    assert verdicts_from_summary({"supported": 7}) == []
    assert verdicts_from_summary("not json") == []


def test_truth_gap_refuses_model_memory():
    message = build_truth_gap(decide_delivery([{"verdict": "unsupported"}]))

    assert "do not support" in message
    assert "won't fill the gap from model memory" in message
