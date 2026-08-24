"""Unit tests for user_state.py — personal-state substrate.

All file I/O is redirected to a tmp_path fixture so no test ever
reads or writes the real vault.  Runs entirely offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _patch_vault_root(tmp_path, monkeypatch):
    """Redirect paths.VAULT_ROOT to a throwaway tmp tree for every test."""
    import paths
    monkeypatch.setattr(paths, "VAULT_ROOT", tmp_path, raising=False)
    # Also replace the lazy __getattr__ result for the module.
    monkeypatch.setattr("paths._resolve_vault_root", lambda: tmp_path)
    import user_state
    # Clear caches / force re-resolution
    yield


import user_state


# ── Profile ──────────────────────────────────────────────────────────────────


class TestProfile:
    def test_read_profile_returns_empty_when_missing(self):
        assert user_state.read_profile() == {}

    def test_upsert_and_read_profile(self, tmp_path):
        result = user_state.upsert_profile({"name": "Alice", "timezone": "UTC"})
        assert result["name"] == "Alice"
        assert result["timezone"] == "UTC"
        assert "updated_at" in result
        loaded = user_state.read_profile()
        assert loaded["name"] == "Alice"

    def test_upsert_profile_merges(self, tmp_path):
        user_state.upsert_profile({"name": "Bob"})
        user_state.upsert_profile({"timezone": "US/Eastern"})
        loaded = user_state.read_profile()
        assert loaded["name"] == "Bob"
        assert loaded["timezone"] == "US/Eastern"

    def test_upsert_profile_rejects_non_dict(self):
        with pytest.raises(ValueError):
            user_state.upsert_profile("not a dict")  # type: ignore[arg-type]

    def test_upsert_profile_rejects_unknown_keys(self):
        with pytest.raises(ValueError, match="Unknown profile keys"):
            user_state.upsert_profile({"evil_field": "x"})


# ── Goals ────────────────────────────────────────────────────────────────────


class TestGoals:
    def test_read_goals_returns_empty_when_missing(self):
        result = user_state.read_goals()
        assert result["goals"] == []

    def test_upsert_and_list_goals(self):
        user_state.upsert_goal({"id": "g1", "title": "Pass biochem", "status": "active"})
        goals = user_state.list_goals()
        assert len(goals) == 1
        assert goals[0]["title"] == "Pass biochem"

    def test_upsert_goal_updates_existing(self):
        user_state.upsert_goal({"id": "g1", "title": "Original", "status": "active"})
        user_state.upsert_goal({"id": "g1", "title": "Updated", "status": "active"})
        goals = user_state.list_goals()
        assert len(goals) == 1
        assert goals[0]["title"] == "Updated"

    def test_list_goals_status_filter(self):
        user_state.upsert_goal({"id": "g1", "title": "Active goal", "status": "active"})
        user_state.upsert_goal({"id": "g2", "title": "Done goal", "status": "completed"})
        assert len(user_state.list_goals(status_filter="active")) == 1
        assert len(user_state.list_goals(status_filter="completed")) == 1

    def test_complete_goal(self):
        user_state.upsert_goal({"id": "g1", "title": "Finish thesis", "status": "active"})
        result = user_state.complete_goal("g1")
        assert result is not None
        assert result["status"] == "completed"

    def test_complete_goal_not_found_returns_none(self):
        assert user_state.complete_goal("nonexistent") is None

    def test_archive_goal(self):
        user_state.upsert_goal({"id": "g1", "title": "Old goal", "status": "active"})
        result = user_state.archive_goal("g1")
        assert result is not None
        assert result["status"] == "archived"

    def test_upsert_goal_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            user_state.upsert_goal({"id": "g1", "title": "X", "status": "INVALID"})

    def test_upsert_goal_missing_required_keys_raises(self):
        with pytest.raises(ValueError, match="missing"):
            user_state.upsert_goal({"id": "g1"})  # missing title and status

    def test_goals_persist_across_calls(self):
        user_state.upsert_goal({"id": "g1", "title": "Goal A", "status": "active"})
        user_state.upsert_goal({"id": "g2", "title": "Goal B", "status": "active"})
        all_goals = user_state.list_goals()
        assert len(all_goals) == 2


# ── Wellbeing ────────────────────────────────────────────────────────────────


class TestWellbeing:
    def test_read_checkins_empty_when_missing(self):
        assert user_state.read_checkins() == []

    def test_append_and_read_checkin(self):
        record = user_state.append_checkin({"mood": 4, "energy": 3, "stress": 2})
        assert record["mood"] == 4
        assert "ts" in record
        checkins = user_state.read_checkins()
        assert len(checkins) == 1
        assert checkins[0]["mood"] == 4

    def test_checkin_validates_range(self):
        with pytest.raises(ValueError, match="1-5"):
            user_state.append_checkin({"mood": 6, "energy": 3, "stress": 2})

    def test_checkin_validates_missing_field(self):
        with pytest.raises(ValueError, match="missing"):
            user_state.append_checkin({"mood": 3, "energy": 3})  # missing stress

    def test_append_multiple_checkins(self):
        for i in range(5):
            user_state.append_checkin({"mood": i + 1, "energy": 3, "stress": 2})
        checkins = user_state.read_checkins()
        assert len(checkins) == 5

    def test_read_checkins_last_n(self):
        for i in range(10):
            user_state.append_checkin({"mood": 3, "energy": 3, "stress": 2})
        assert len(user_state.read_checkins(last_n=3)) == 3

    def test_wellbeing_summary_empty(self):
        result = user_state.wellbeing_summary()
        assert result["count"] == 0

    def test_wellbeing_summary_averages(self):
        user_state.append_checkin({"mood": 4, "energy": 3, "stress": 2})
        user_state.append_checkin({"mood": 2, "energy": 5, "stress": 4})
        result = user_state.wellbeing_summary()
        assert result["count"] == 2
        assert result["avg_mood"] == 3.0
        assert result["avg_energy"] == 4.0
        assert result["avg_stress"] == 3.0

    def test_wellbeing_summary_burnout_risk_high(self):
        for _ in range(3):
            user_state.append_checkin({"mood": 2, "energy": 1, "stress": 5})
        result = user_state.wellbeing_summary()
        assert result["burnout_risk"] == "high"

    def test_wellbeing_summary_burnout_risk_low(self):
        user_state.append_checkin({"mood": 5, "energy": 5, "stress": 1})
        result = user_state.wellbeing_summary()
        assert result["burnout_risk"] == "low"


# ── User-Model.md regeneration ────────────────────────────────────────────────


class TestUserModel:
    def test_regenerate_creates_file(self, tmp_path):
        user_state.upsert_profile({"name": "Carol"})
        md = user_state.regenerate_user_model()
        assert "Carol" in md
        assert "User Model" in md
        p = tmp_path / "User" / "State" / "User-Model.md"
        assert p.exists()

    def test_regenerate_includes_goals(self, tmp_path):
        user_state.upsert_profile({"name": "Dave"})
        user_state.upsert_goal({"id": "g1", "title": "Graduate", "status": "active"})
        md = user_state.regenerate_user_model()
        assert "Graduate" in md

    def test_regenerate_no_goals_shows_none(self, tmp_path):
        user_state.upsert_profile({"name": "Eve"})
        md = user_state.regenerate_user_model()
        assert "*(none)*" in md


# ── Boot summary ──────────────────────────────────────────────────────────────


class TestBootSummary:
    def test_boot_summary_empty_when_no_profile(self):
        assert user_state.build_boot_summary() == ""

    def test_boot_summary_with_profile(self, tmp_path):
        user_state.upsert_profile({"name": "Frank"})
        summary = user_state.build_boot_summary()
        assert "Frank" in summary

    def test_boot_summary_includes_top_goal(self, tmp_path):
        user_state.upsert_profile({"name": "Grace"})
        user_state.upsert_goal({"id": "g1", "title": "Learn ML", "status": "active"})
        summary = user_state.build_boot_summary()
        assert "Learn ML" in summary
