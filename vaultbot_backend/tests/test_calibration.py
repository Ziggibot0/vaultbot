from __future__ import annotations

import pytest
from calibration import CalibrationTracker

pytestmark = pytest.mark.unit


def test_grounding_estimate_returns_calibrated_payload(tmp_path):
    tracker = CalibrationTracker(log_path=str(tmp_path / "calibration.json"))

    confidence = tracker.estimate_answer_confidence(
        grounding_score={
            "total_wikilinks": 2,
            "allowed_cited": 2,
            "sentences": 2,
            "ungrounded_ratio": 0.0,
            "grounding_score": 1.0,
            "failed": False,
        }
    )

    assert confidence["stage"] == "grounding"
    assert confidence["band"] == "high"
    assert confidence["raw_confidence"] == confidence["calibrated_confidence"]
    assert confidence["sample_size"] == 0


def test_operator_correction_calibrates_similar_future_answers_down(tmp_path):
    tracker = CalibrationTracker(log_path=str(tmp_path / "calibration.json"))
    answer = (
        "Alpha is first [[Alpha]].\n\n"
        "> ✓ **High confidence** — 100% calibrated confidence"
    )
    first = tracker.estimate_answer_confidence(
        grounding_score={
            "total_wikilinks": 1,
            "allowed_cited": 1,
            "sentences": 1,
            "ungrounded_ratio": 0.0,
            "grounding_score": 1.0,
            "failed": False,
        }
    )
    tracker.log_answer_confidence(answer, first)
    tracker.log_correction("that's wrong", answer, failure_type="verification")

    second = tracker.estimate_answer_confidence(
        grounding_score={
            "total_wikilinks": 1,
            "allowed_cited": 1,
            "sentences": 1,
            "ungrounded_ratio": 0.0,
            "grounding_score": 1.0,
            "failed": False,
        }
    )

    assert second["sample_size"] >= 1
    assert second["calibration_scope"] in {"bucket", "nearby", "global"}
    assert second["calibrated_confidence"] < second["raw_confidence"]


def test_verification_summary_rolls_up_to_answer_confidence(tmp_path):
    tracker = CalibrationTracker(log_path=str(tmp_path / "calibration.json"))

    confidence = tracker.estimate_answer_confidence(
        verification_summary={
            "total": 4,
            "supported": 2,
            "unsupported": 1,
            "contradicted": 1,
            "verdicts": [],
        }
    )

    assert confidence["stage"] == "verified"
    assert confidence["total_claims"] == 4
    assert confidence["supported_claims"] == 2
    assert confidence["unsupported_claims"] == 1
    assert confidence["contradicted_claims"] == 1
    assert confidence["raw_confidence"] == 0.4
    assert confidence["observed_quality"] == 0.4
    assert confidence["band"] == "moderate"
