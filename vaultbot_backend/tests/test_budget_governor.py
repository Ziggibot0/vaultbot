"""Unit tests for the token-dollar budget governor (issue #364)."""

from __future__ import annotations

from contextlib import suppress

import pytest

pytestmark = pytest.mark.unit

from budget_governor import BudgetExceeded, BudgetState, make_budget_state


class TestLocalModelDetection:
    def test_ollama_is_local(self):
        s = BudgetState()
        assert s.is_local_model("ollama/llama3:30b")

    def test_local_in_name(self):
        s = BudgetState()
        assert s.is_local_model("local-mistral-7b")

    def test_cloud_model_not_local(self):
        s = BudgetState()
        assert not s.is_local_model("gpt-4o")
        assert not s.is_local_model("claude-3-opus")


class TestRecordUsage:
    def test_local_model_never_raises(self):
        s = BudgetState(usd_ceiling=0.0)
        # Should not raise even at $0 ceiling
        s.record_usage("ollama/llama3", 1000, 500, None)

    def test_unknown_cost_cloud_raises(self):
        s = BudgetState()
        with pytest.raises(BudgetExceeded, match="Cost data unavailable"):
            s.record_usage("gpt-4o", 1000, 500, None)

    def test_under_ceiling_ok(self):
        s = BudgetState(usd_ceiling=1.0)
        s.record_usage("gpt-4o", 100, 50, 0.30)
        assert s.total_usd_spent == pytest.approx(0.30)

    def test_exceeds_ceiling_raises(self):
        s = BudgetState(usd_ceiling=0.50)
        with pytest.raises(BudgetExceeded, match="Hard budget ceiling"):
            s.record_usage("gpt-4o", 10000, 5000, 0.60)

    def test_cumulative_spend_tracked(self):
        s = BudgetState(usd_ceiling=1.0)
        s.record_usage("gpt-4o", 100, 50, 0.20)
        s.record_usage("gpt-4o", 100, 50, 0.20)
        assert s.total_usd_spent == pytest.approx(0.40)


class TestCheckEscalation:
    def test_first_escalation_ok(self):
        s = BudgetState(max_escalations=3)
        s.check_escalation("gpt-4o")
        assert s.escalation_count == 1

    def test_at_limit_raises(self):
        s = BudgetState(max_escalations=2)
        s.check_escalation("gpt-4o")
        s.check_escalation("gpt-4o")
        with pytest.raises(BudgetExceeded, match="Escalation limit"):
            s.check_escalation("gpt-4o")

    def test_local_escalation_never_counted(self):
        s = BudgetState(max_escalations=0)
        # Should not raise even at max_escalations=0 for local models
        s.check_escalation("ollama/llama3")
        assert s.escalation_count == 0


class TestSummary:
    def test_summary_keys(self):
        s = BudgetState(usd_ceiling=1.0, max_escalations=3)
        summary = s.summary()
        assert "total_usd_spent" in summary
        assert "usd_ceiling" in summary
        assert "task_usd_ceiling" in summary
        assert "escalation_count" in summary
        assert "max_escalations" in summary
        assert "budget_remaining_usd" in summary


class TestProjection:
    def test_estimate_round_cost_uses_configured_rates(self):
        s = BudgetState(
            input_usd_per_million_tokens=1.0,
            output_usd_per_million_tokens=2.0,
            projected_completion_tokens=0,
        )
        # prompt=1000 => 0.001 * 1.0, completion=500 => 0.0005 * 2.0
        assert s.estimate_round_cost_usd(1000, 500) == pytest.approx(0.002)

    def test_estimate_round_cost_uses_projected_completion_default(self):
        s = BudgetState(
            input_usd_per_million_tokens=0.0,
            output_usd_per_million_tokens=1.0,
            projected_completion_tokens=250,
        )
        assert s.estimate_round_cost_usd(0) == pytest.approx(0.00025)

    def test_remaining_never_negative(self):
        s = BudgetState(usd_ceiling=0.10)
        with suppress(BudgetExceeded):
            s.record_usage("gpt-4o", 1000, 500, 0.50)
        assert s.summary()["budget_remaining_usd"] >= 0.0


class TestMakeBudgetState:
    def test_override_ceiling(self):
        s = make_budget_state(usd_ceiling=2.0)
        assert s.usd_ceiling == pytest.approx(2.0)

    def test_override_escalations(self):
        s = make_budget_state(max_escalations=10)
        assert s.max_escalations == 10

    def test_override_task_budget(self):
        s = make_budget_state(task_usd_ceiling=3.5)
        assert s.task_usd_ceiling == pytest.approx(3.5)
