from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from energy_report import build_energy_report


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )


def _invocation(
    timestamp: float, role: str, model_id: str, prompt: int, completion: int, **extra
) -> dict:
    data = {
        "role": role,
        "model_id": model_id,
        "provider_id": model_id.split(":", 1)[0],
        "model": model_id.split(":", 1)[-1],
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "token_source": "reported",
        "outcome": "success",
        **extra,
    }
    return {"event": "llm_invocation", "timestamp": timestamp, "data": data}


def test_mixed_role_energy_and_all_big_counterfactual(tmp_path: Path) -> None:
    now = 2_000_000_000.0
    _write_events(
        tmp_path / "new.jsonl",
        [
            _invocation(now - 10, "big", "cloud:big", 1000, 1000),
            _invocation(now - 5, "small", "local:small", 2000, 1000),
        ],
    )
    _write_events(
        tmp_path / "legacy.jsonl", [{"event": "token_usage", "timestamp": now}]
    )
    profiles = {
        "cloud:big": {
            "wh_per_1k_input_tokens": 2.0,
            "wh_per_1k_output_tokens": 4.0,
        },
        "local:small": {
            "wh_per_1k_input_tokens": 0.2,
            "wh_per_1k_output_tokens": 0.4,
        },
    }

    report = build_energy_report(tmp_path, profiles, "cloud:big", now=now)

    assert report["totals"]["tracked_invocations"] == 2
    assert report["totals"]["tracked_tokens"] == 5000
    assert report["totals"]["actual_wh"] == 6.8
    assert report["totals"]["counterfactual_wh"] == 8.0
    assert report["totals"]["saved_wh"] == 7.2
    assert report["totals"]["savings_percent"] == 90.0
    assert report["coverage"]["legacy_session_files_excluded"] == 1


def test_missing_profile_and_vision_are_explicit(tmp_path: Path) -> None:
    now = 2_000_000_000.0
    _write_events(
        tmp_path / "session.jsonl",
        [
            _invocation(
                now, "small", "unknown:model", 50, 50, token_source="estimated"
            ),
            _invocation(now, "vision", "vision:model", 100, 100),
        ],
    )
    report = build_energy_report(
        tmp_path,
        {"big:model": {"wh_per_1k_input_tokens": 1, "wh_per_1k_output_tokens": 1}},
        "big:model",
        now=now,
    )
    coverage = report["coverage"]
    assert coverage["invocations_missing_profile"] == 2
    assert coverage["tokens_missing_profile"] == 300
    assert coverage["estimated_token_invocations"] == 1
    assert coverage["vision_invocations_excluded_from_savings"] == 1
    assert report["totals"]["savings_percent"] is None
