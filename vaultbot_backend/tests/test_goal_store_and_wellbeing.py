"""Unit tests for goal_store and wellbeing_checkin custom tools.

All vault I/O is redirected to tmp_path via patching paths.VAULT_ROOT.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _patch_vault_root(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "VAULT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("paths._resolve_vault_root", lambda: tmp_path)
    yield


# ── goal_store ────────────────────────────────────────────────────────────────


class TestGoalStore:
    def test_goal_list_empty(self):
        from custom_tools import goal_store as gs

        result = gs.run({"action": "goal_list"})
        assert result["status"] == "ok"
        assert result["goals"] == []

    def test_goal_upsert_new(self):
        from custom_tools import goal_store as gs

        result = gs.run({"action": "goal_upsert", "title": "Pass calculus"})
        assert result["status"] == "ok"
        assert result["goal"]["title"] == "Pass calculus"
        assert result["goal"]["status"] == "active"
        assert "id" in result["goal"]

    def test_goal_upsert_update_without_title(self):
        """Update existing goal without title by merging stored title."""
        from custom_tools import goal_store as gs

        gs.run({"action": "goal_upsert", "id": "gu1", "title": "Original title"})
        result = gs.run(
            {"action": "goal_upsert", "id": "gu1", "target_date": "2030-01-01"}
        )
        assert result["status"] == "ok"
        assert result["goal"]["id"] == "gu1"
        assert result["goal"]["target_date"] == "2030-01-01"

    def test_goal_upsert_missing_title_new_goal_returns_error(self):
        from custom_tools import goal_store as gs

        result = gs.run({"action": "goal_upsert"})
        assert "error" in result

    def test_goal_upsert_persists(self):
        from custom_tools import goal_store as gs

        gs.run({"action": "goal_upsert", "title": "Apply for internship"})
        result = gs.run({"action": "goal_list"})
        assert result["count"] == 1
        assert result["goals"][0]["title"] == "Apply for internship"

    def test_goal_upsert_update_existing(self):
        from custom_tools import goal_store as gs

        first = gs.run({"action": "goal_upsert", "title": "Old title", "id": "g1"})
        gid = first["goal"]["id"]
        gs.run({"action": "goal_upsert", "id": gid, "title": "New title"})
        goals = gs.run({"action": "goal_list"})["goals"]
        assert len(goals) == 1
        assert goals[0]["title"] == "New title"

    def test_goal_complete(self):
        from custom_tools import goal_store as gs

        gs.run({"action": "goal_upsert", "id": "g99", "title": "Lab report"})
        result = gs.run({"action": "goal_complete", "id": "g99"})
        assert result["status"] == "ok"
        assert result["goal"]["status"] == "completed"

    def test_goal_complete_not_found(self):
        from custom_tools import goal_store as gs

        result = gs.run({"action": "goal_complete", "id": "missing"})
        assert "error" in result

    def test_goal_complete_missing_id(self):
        from custom_tools import goal_store as gs

        result = gs.run({"action": "goal_complete"})
        assert "error" in result

    def test_goal_archive(self):
        from custom_tools import goal_store as gs

        gs.run({"action": "goal_upsert", "id": "ga1", "title": "Old project"})
        result = gs.run({"action": "goal_archive", "id": "ga1"})
        assert result["status"] == "ok"
        assert result["goal"]["status"] == "archived"

    def test_goal_list_filter(self):
        from custom_tools import goal_store as gs

        gs.run({"action": "goal_upsert", "id": "ga", "title": "Active"})
        gs.run({"action": "goal_complete", "id": "ga"})
        gs.run({"action": "goal_upsert", "id": "gb", "title": "Another active"})
        active = gs.run({"action": "goal_list", "status_filter": "active"})
        assert active["count"] == 1
        assert active["goals"][0]["title"] == "Another active"

    def test_unknown_action_returns_error(self):
        from custom_tools import goal_store as gs

        result = gs.run({"action": "goal_explode"})
        assert "error" in result


# ── wellbeing_checkin ─────────────────────────────────────────────────────────


class TestWellbeingCheckin:
    def test_checkin_stores_record(self):
        from custom_tools import wellbeing_checkin as wc

        result = wc.run({"action": "checkin", "mood": 4, "energy": 3, "stress": 2})
        assert result["status"] == "ok"
        assert "record" in result
        assert result["record"]["mood"] == 4

    def test_checkin_missing_field(self):
        from custom_tools import wellbeing_checkin as wc

        result = wc.run({"action": "checkin", "mood": 3, "energy": 3})
        assert "error" in result

    def test_checkin_out_of_range(self):
        from custom_tools import wellbeing_checkin as wc

        result = wc.run({"action": "checkin", "mood": 10, "energy": 3, "stress": 2})
        assert "error" in result

    def test_checkin_optional_fields(self):
        from custom_tools import wellbeing_checkin as wc

        result = wc.run(
            {
                "action": "checkin",
                "mood": 3,
                "energy": 4,
                "stress": 2,
                "note": "Exam week",
                "tags": ["workload"],
            }
        )
        assert result["status"] == "ok"
        assert result["record"]["note"] == "Exam week"
        assert result["record"]["tags"] == ["workload"]

    def test_summary_no_checkins(self):
        from custom_tools import wellbeing_checkin as wc

        result = wc.run({"action": "summary"})
        assert result["status"] == "ok"
        assert result["summary"]["count"] == 0

    def test_summary_with_checkins(self):
        from custom_tools import wellbeing_checkin as wc

        wc.run({"action": "checkin", "mood": 5, "energy": 5, "stress": 1})
        wc.run({"action": "checkin", "mood": 3, "energy": 3, "stress": 3})
        result = wc.run({"action": "summary"})
        assert result["summary"]["count"] == 2
        assert result["summary"]["avg_mood"] == 4.0

    def test_history_action(self):
        from custom_tools import wellbeing_checkin as wc

        wc.run({"action": "checkin", "mood": 4, "energy": 4, "stress": 2})
        result = wc.run({"action": "history", "last_n": 5})
        assert result["status"] == "ok"
        assert result["count"] >= 1

    def test_unknown_action_returns_error(self):
        from custom_tools import wellbeing_checkin as wc

        result = wc.run({"action": "fly"})
        assert "error" in result
