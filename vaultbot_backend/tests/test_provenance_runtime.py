"""Tests for synchronous provenance verification on the delivery path."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from provenance_runtime import parse_final_verification_summary, verify_answer_delivery

pytestmark = pytest.mark.unit


def test_final_verification_summary_uses_last_procedure_step():
    final_output = (
        '{"pairs":[{"claim":"A"}]}\n\n'
        '{"pairs":[{"claim":"A","source_text":"source"}]}\n\n'
        '{"total":1,"verdicts":[{"claim":"A","verdict":"supported"}]}'
    )

    summary = parse_final_verification_summary(final_output)

    assert summary["total"] == 1
    assert summary["verdicts"][0]["verdict"] == "supported"


def test_malformed_concatenated_output_fails_closed():
    assert parse_final_verification_summary('{"ok":true}\n\nnot-json') == {}


def test_supported_verifier_output_is_deliverable(monkeypatch):
    run = AsyncMock(
        return_value={
            "overall_passed": True,
            "final_output": '{"verdicts":[{"claim":"A","verdict":"supported"}]}',
        }
    )
    monkeypatch.setattr("chat_preflight.run_procedure_direct", run)

    decision, summary = asyncio.run(verify_answer_delivery(None, None, None, "q", "a"))

    assert decision.deliverable is True
    assert summary["verdicts"][0]["verdict"] == "supported"


def test_procedure_success_with_unsupported_claims_fails_closed(monkeypatch):
    run = AsyncMock(
        return_value={
            "overall_passed": True,
            "final_output": '{"supported":0,"unsupported":7,"verdicts":['
            + ",".join('{"verdict":"unsupported"}' for _ in range(7))
            + "]}",
        }
    )
    monkeypatch.setattr("chat_preflight.run_procedure_direct", run)

    decision, _ = asyncio.run(verify_answer_delivery(None, None, None, "q", "a"))

    assert decision.deliverable is False
    assert decision.unsupported_claims == 7


@pytest.mark.parametrize(
    "result",
    [
        {"overall_passed": False, "final_output": ""},
        {"overall_passed": True, "final_output": "not json"},
        {"error": "verifier unavailable"},
    ],
)
def test_verifier_failure_is_unverifiable(monkeypatch, result):
    monkeypatch.setattr(
        "chat_preflight.run_procedure_direct", AsyncMock(return_value=result)
    )

    decision, _ = asyncio.run(verify_answer_delivery(None, None, None, "q", "a"))

    assert decision.deliverable is False
    assert decision.disposition == "verification_unavailable"
