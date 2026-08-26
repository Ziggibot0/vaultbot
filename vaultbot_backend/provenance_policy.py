"""Fail-closed delivery policy for evidence-bound VaultBot answers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Verdict = Literal["supported", "unsupported", "contradicted", "unverifiable"]
Disposition = Literal[
    "deliver",
    "acknowledgement",
    "insufficient_evidence",
    "conflicting_evidence",
    "verification_unavailable",
]

_KNOWN_VERDICTS: frozenset[str] = frozenset(
    {"supported", "unsupported", "contradicted", "unverifiable"}
)

_ACKNOWLEDGEMENT_RE = re.compile(
    r"^(?:ok(?:ay)?|got it|understood|thanks|thank you|yes|no|sure|"
    r"sounds good|i(?:'m| am) ready|ready)(?:[.!\s]+)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DeliveryDecision:
    """Authoritative decision about whether an answer may reach the user."""

    deliverable: bool
    disposition: Disposition
    total_claims: int
    supported_claims: int
    unsupported_claims: int
    contradicted_claims: int
    unverifiable_claims: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for logs and APIs."""
        return {
            "deliverable": self.deliverable,
            "disposition": self.disposition,
            "total_claims": self.total_claims,
            "supported_claims": self.supported_claims,
            "unsupported_claims": self.unsupported_claims,
            "contradicted_claims": self.contradicted_claims,
            "unverifiable_claims": self.unverifiable_claims,
            "reason": self.reason,
        }


def is_pure_acknowledgement(answer: str) -> bool:
    """Return whether ``answer`` is a content-free acknowledgement.

    This deliberately uses a narrow allowlist. Brevity alone is not an
    exemption: a short diagnosis, recommendation, or factual claim remains
    substantive and must be verified.
    """
    return bool(_ACKNOWLEDGEMENT_RE.fullmatch((answer or "").strip()))


def verdicts_from_summary(summary: Any) -> list[dict[str, Any]]:
    """Extract verifier verdicts without inventing success on malformed data."""
    if isinstance(summary, dict):
        verdicts = summary.get("verdicts")
        return (
            [item for item in verdicts if isinstance(item, dict)]
            if isinstance(verdicts, list)
            else []
        )
    if isinstance(summary, list):
        return [item for item in summary if isinstance(item, dict)]
    return []


def build_truth_gap(decision: DeliveryDecision) -> str:
    """Render a transparent non-answer for a blocked substantive response."""
    if decision.disposition == "conflicting_evidence":
        gap = (
            "The available evidence conflicts, so I can't give you a reliable "
            "answer yet."
        )
    elif decision.disposition == "insufficient_evidence":
        gap = "The sources I checked do not support every claim in the drafted answer."
    else:
        gap = "I couldn't verify the drafted answer against its sources."
    return (
        f"{gap}\n\n"
        "I won't fill the gap from model memory. I need to retrieve or inspect "
        "better evidence before answering."
    )


def decide_delivery(
    verdicts: list[dict[str, Any]] | None,
    *,
    substantive: bool = True,
) -> DeliveryDecision:
    """Decide whether claim-verification results permit answer delivery.

    Substantive answers fail closed: every claim must have the explicit
    ``supported`` verdict. Missing, malformed, or unknown verdicts are treated
    as unverifiable. Pure acknowledgements bypass claim verification because
    they assert no external fact.
    """
    if not substantive:
        return DeliveryDecision(
            deliverable=True,
            disposition="acknowledgement",
            total_claims=0,
            supported_claims=0,
            unsupported_claims=0,
            contradicted_claims=0,
            unverifiable_claims=0,
            reason="output contains no substantive claim",
        )

    counts = {verdict: 0 for verdict in _KNOWN_VERDICTS}
    items = verdicts or []
    for item in items:
        raw_verdict = item.get("verdict") if isinstance(item, dict) else None
        verdict = str(raw_verdict or "").strip().lower()
        counts[verdict if verdict in _KNOWN_VERDICTS else "unverifiable"] += 1

    total = sum(counts.values())
    if counts["contradicted"]:
        disposition: Disposition = "conflicting_evidence"
        reason = "one or more claims contradict the available evidence"
    elif counts["unsupported"]:
        disposition = "insufficient_evidence"
        reason = "one or more claims are not supported by their cited evidence"
    elif counts["unverifiable"] or total == 0:
        disposition = "verification_unavailable"
        reason = "claim support could not be verified"
    else:
        disposition = "deliver"
        reason = "every substantive claim is supported"

    return DeliveryDecision(
        deliverable=disposition == "deliver",
        disposition=disposition,
        total_claims=total,
        supported_claims=counts["supported"],
        unsupported_claims=counts["unsupported"],
        contradicted_claims=counts["contradicted"],
        unverifiable_claims=counts["unverifiable"],
        reason=reason,
    )
