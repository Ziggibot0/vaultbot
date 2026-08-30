"""Regression tests for per-turn orchestration attribution events.

Verifies that SessionLogger emits ``route_decision``, ``turn_cost``, and
``turn_efficiency`` events with the correct schema, and that
``orchestration_report.session_orchestration_report`` correctly parses
them into a structured summary.

Issue #365 — [observability] Add per-turn orchestration attribution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from orchestration_report import session_orchestration_report
from session_logger import SessionLogger

# ── SessionLogger event-emission tests ───────────────────────────────────


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_log_route_decision_emits_correct_schema(tmp_path: Path) -> None:
    s = SessionLogger(log_dir=str(tmp_path))
    s.log_route_decision(
        route="procedure",
        confidence=0.85,
        reason="action_signal",
        turn_index=1,
    )
    s.close()

    events = _read_events(s._file_path)
    rd = next(e for e in events if e.get("event") == "route_decision")
    assert rd["data"]["route"] == "procedure"
    assert rd["data"]["confidence"] == 0.85
    assert rd["data"]["reason"] == "action_signal"
    assert rd["data"]["turn_index"] == 1


def test_log_route_decision_without_turn_index(tmp_path: Path) -> None:
    s = SessionLogger(log_dir=str(tmp_path))
    s.log_route_decision(
        route="small_model",
        confidence=0.6,
        reason="clarification_or_explanation",
    )
    s.close()

    events = _read_events(s._file_path)
    rd = next(e for e in events if e.get("event") == "route_decision")
    assert rd["data"]["route"] == "small_model"
    assert "turn_index" not in rd["data"]


def test_log_turn_cost_emits_correct_schema(tmp_path: Path) -> None:
    s = SessionLogger(log_dir=str(tmp_path))
    s.log_turn_cost(
        prompt_tokens=150,
        completion_tokens=80,
        tool_latency_ms=320.5,
        cost_usd=0.00042,
        model="gpt-4o-mini",
        turn_index=2,
    )
    s.close()

    events = _read_events(s._file_path)
    tc = next(e for e in events if e.get("event") == "turn_cost")
    d = tc["data"]
    assert d["prompt_tokens"] == 150
    assert d["completion_tokens"] == 80
    assert d["total_tokens"] == 230
    assert d["tool_latency_ms"] == 320.5
    assert d["cost_usd"] == 0.00042
    assert d["model"] == "gpt-4o-mini"
    assert d["turn_index"] == 2


def test_log_turn_cost_omits_cost_when_none(tmp_path: Path) -> None:
    s = SessionLogger(log_dir=str(tmp_path))
    s.log_turn_cost(prompt_tokens=50, completion_tokens=20)
    s.close()

    events = _read_events(s._file_path)
    tc = next(e for e in events if e.get("event") == "turn_cost")
    assert "cost_usd" not in tc["data"]
    assert "model" not in tc["data"]


def test_log_llm_invocation_emits_immutable_execution_facts(tmp_path: Path) -> None:
    s = SessionLogger(log_dir=str(tmp_path))
    invocation_id = s.log_llm_invocation(
        role="small",
        model_id="ollama-local:qwen3:4b",
        provider_id="ollama-local",
        provider_type="ollama",
        model="qwen3:4b",
        prompt_tokens=120,
        completion_tokens=30,
        token_source="reported",
        stream=True,
        duration_ms=42.126,
        context={"turn_index": 2, "round": 1, "purpose": "agentic_chat"},
    )
    s.close()

    events = _read_events(s._file_path)
    event = next(e for e in events if e.get("event") == "llm_invocation")
    data = event["data"]
    assert data["invocation_id"] == invocation_id
    assert data["role"] == "small"
    assert data["model_id"] == "ollama-local:qwen3:4b"
    assert data["provider_id"] == "ollama-local"
    assert data["provider_type"] == "ollama"
    assert data["model"] == "qwen3:4b"
    assert data["prompt_tokens"] == 120
    assert data["completion_tokens"] == 30
    assert data["total_tokens"] == 150
    assert data["token_source"] == "reported"
    assert data["stream"] is True
    assert data["duration_ms"] == 42.13
    assert data["outcome"] == "success"
    assert data["context"] == {
        "turn_index": 2,
        "round": 1,
        "purpose": "agentic_chat",
    }
    assert "energy_wh" not in data
    assert "energy_saved_wh" not in data


def test_log_turn_efficiency_emits_correct_schema(tmp_path: Path) -> None:
    s = SessionLogger(log_dir=str(tmp_path))
    s.log_turn_efficiency(
        tool_rounds=3,
        completion_outcome="success",
        repeated_tool_calls=["vault_search"],
        turn_index=1,
    )
    s.close()

    events = _read_events(s._file_path)
    te = next(e for e in events if e.get("event") == "turn_efficiency")
    d = te["data"]
    assert d["tool_rounds"] == 3
    assert d["completion_outcome"] == "success"
    assert d["repeated_tool_calls"] == ["vault_search"]
    assert d["turn_index"] == 1


def test_log_turn_efficiency_empty_repeated_calls(tmp_path: Path) -> None:
    s = SessionLogger(log_dir=str(tmp_path))
    s.log_turn_efficiency(tool_rounds=1, completion_outcome="success")
    s.close()

    events = _read_events(s._file_path)
    te = next(e for e in events if e.get("event") == "turn_efficiency")
    assert te["data"]["repeated_tool_calls"] == []


# ── orchestration_report integration tests ───────────────────────────────


def _write_session_with_two_turns(tmp_path: Path) -> Path:
    """Write a synthetic session log with two complete turns."""
    s = SessionLogger(log_dir=str(tmp_path))

    # Turn 1: procedure route
    s.log("chat_begin", {"user_message": "fix the CI"})
    s.log_route_decision(
        route="procedure",
        confidence=0.9,
        reason="action_signal",
        turn_index=1,
    )
    s.log_turn_cost(
        prompt_tokens=200,
        completion_tokens=100,
        tool_latency_ms=500.0,
        cost_usd=0.0006,
        turn_index=1,
    )
    s.log_turn_efficiency(
        tool_rounds=2,
        completion_outcome="success",
        repeated_tool_calls=[],
        turn_index=1,
    )

    # Turn 2: small_model route with a repeated tool call
    s.log("chat_begin", {"user_message": "explain the error"})
    s.log_route_decision(
        route="small_model",
        confidence=0.7,
        reason="clarification_or_explanation",
        turn_index=2,
    )
    s.log_turn_cost(
        prompt_tokens=50,
        completion_tokens=40,
        tool_latency_ms=120.0,
        turn_index=2,
    )
    s.log_turn_efficiency(
        tool_rounds=1,
        completion_outcome="success",
        repeated_tool_calls=["vault_search"],
        turn_index=2,
    )

    s.close()
    return s._file_path


def test_report_turn_count(tmp_path: Path) -> None:
    log = _write_session_with_two_turns(tmp_path)
    report = session_orchestration_report(log)
    assert report["summary"]["turn_count"] == 2


def test_report_route_distribution(tmp_path: Path) -> None:
    log = _write_session_with_two_turns(tmp_path)
    report = session_orchestration_report(log)
    dist = report["summary"]["route_distribution"]
    assert dist.get("procedure") == 1
    assert dist.get("small_model") == 1


def test_report_token_totals(tmp_path: Path) -> None:
    log = _write_session_with_two_turns(tmp_path)
    report = session_orchestration_report(log)
    s = report["summary"]
    assert s["total_prompt_tokens"] == 250
    assert s["total_completion_tokens"] == 140
    assert s["total_tokens"] == 390


def test_report_cost_aggregation(tmp_path: Path) -> None:
    log = _write_session_with_two_turns(tmp_path)
    report = session_orchestration_report(log)
    s = report["summary"]
    # Only turn 1 has cost_usd; turn 2 does not.
    assert "total_cost_usd" in s
    assert abs(s["total_cost_usd"] - 0.0006) < 1e-9


def test_report_tool_latency(tmp_path: Path) -> None:
    log = _write_session_with_two_turns(tmp_path)
    report = session_orchestration_report(log)
    s = report["summary"]
    assert abs(s["total_tool_latency_ms"] - 620.0) < 0.01
    assert abs(s["avg_tool_latency_ms_per_turn"] - 310.0) < 0.01


def test_report_repeated_tool_flags(tmp_path: Path) -> None:
    log = _write_session_with_two_turns(tmp_path)
    report = session_orchestration_report(log)
    assert report["summary"]["turns_with_repeated_tool_calls"] == 1


def test_report_per_turn_detail(tmp_path: Path) -> None:
    log = _write_session_with_two_turns(tmp_path)
    report = session_orchestration_report(log)
    turns = report["turns"]
    assert len(turns) == 2
    t1 = next(t for t in turns if t["turn_index"] == 1)
    assert t1["route"] == "procedure"
    assert t1["tool_rounds"] == 2
    assert t1["completion_outcome"] == "success"


def test_report_baseline_comparison(tmp_path: Path) -> None:
    log = _write_session_with_two_turns(tmp_path)
    report = session_orchestration_report(
        log,
        baselines={"tool_latency_ms_per_turn": 200.0, "tool_rounds_per_turn": 2.0},
    )
    cmp = report["comparisons"]
    assert "tool_latency_ms_per_turn_delta" in cmp
    assert abs(cmp["tool_latency_ms_per_turn_delta"] - 110.0) < 0.01
    assert "tool_rounds_per_turn_delta" in cmp
    assert abs(cmp["tool_rounds_per_turn_delta"] - (-0.5)) < 0.001


def test_report_missing_file_returns_error() -> None:
    report = session_orchestration_report(Path("/tmp/nonexistent-session.jsonl"))
    assert "error" in report


def test_report_empty_log_returns_zero_turns(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    report = session_orchestration_report(empty)
    assert report["summary"]["turn_count"] == 0
    assert report["turns"] == []


def test_report_attaches_multiple_invocations_to_one_turn(tmp_path: Path) -> None:
    s = SessionLogger(log_dir=str(tmp_path))
    s.log("chat_begin", {"user_message": "run a mixed-model procedure"})
    for role, model_id, tokens in (
        ("small", "local:small", (100, 20)),
        ("big", "cloud:big", (200, 40)),
    ):
        s.log_llm_invocation(
            role=role,
            model_id=model_id,
            provider_id=model_id.split(":", 1)[0],
            provider_type="ollama" if role == "small" else "openai",
            model=model_id.split(":", 1)[1],
            prompt_tokens=tokens[0],
            completion_tokens=tokens[1],
            token_source="reported",
            stream=False,
            duration_ms=10,
            context={"turn_index": 1, "purpose": "procedure_step"},
        )
    s.close()

    report = session_orchestration_report(s._file_path)

    assert len(report["turns"][0]["llm_invocations"]) == 2
    assert report["summary"]["llm_invocation_count"] == 2
    assert report["summary"]["llm_invocation_tokens"] == 360
