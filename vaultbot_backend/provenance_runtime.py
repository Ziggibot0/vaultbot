"""Synchronous answer-verification adapter for the chat critical path."""

from __future__ import annotations

import json
from typing import Any

from provenance_policy import DeliveryDecision, decide_delivery, verdicts_from_summary


def parse_final_verification_summary(final_output: Any) -> dict[str, Any]:
    """Parse the final step JSON from a procedure's concatenated outputs."""
    if isinstance(final_output, dict):
        return final_output
    if not isinstance(final_output, str) or not final_output.strip():
        return {}

    decoder = json.JSONDecoder()
    position = 0
    parsed_values: list[Any] = []
    while position < len(final_output):
        while position < len(final_output) and final_output[position].isspace():
            position += 1
        if position >= len(final_output):
            break
        try:
            value, position = decoder.raw_decode(final_output, position)
        except json.JSONDecodeError:
            return {}
        parsed_values.append(value)

    final_value = parsed_values[-1] if parsed_values else None
    return final_value if isinstance(final_value, dict) else {}


async def verify_answer_delivery(
    svc: Any,
    websocket: Any,
    session_logger: Any,
    user_message: str,
    answer: str,
) -> tuple[DeliveryDecision, dict[str, Any]]:
    """Verify cited claims and return the authoritative delivery decision."""
    from chat_preflight import run_procedure_direct

    result = await run_procedure_direct(
        svc,
        "Verify-Answer-Entailment",
        proc_args={"answer": answer},
        session_logger=session_logger,
        user_message=user_message,
        websocket=websocket,
    )
    if result.get("error") or not result.get("overall_passed"):
        return decide_delivery(None), {}

    summary = parse_final_verification_summary(result.get("final_output", ""))

    verdicts = verdicts_from_summary(summary)
    return decide_delivery(verdicts), summary
