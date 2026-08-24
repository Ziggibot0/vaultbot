"""Unit tests for nudge_scheduler.py — urgency computation and throttle rules.

All I/O is redirected to tmp_path; no asyncio loop needed for the pure-
logic surface (compute_urgency, _was_nudged_recently, build_nudge_message).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _patch_vault_root(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "VAULT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("paths._resolve_vault_root", lambda: tmp_path)
    yield


from nudge_scheduler import (
    NudgeScheduler,
    _in_quiet_hours,
    _was_nudged_recently,
    build_nudge_message,
    compute_urgency,
)


# ── compute_urgency ───────────────────────────────────────────────────────────


class TestComputeUrgency:
    def test_overdue(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert compute_urgency(yesterday) == "overdue"

    def test_today(self):
        today = date.today().isoformat()
        assert compute_urgency(today) == "today"

    def test_tomorrow(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert compute_urgency(tomorrow) == "tomorrow"

    def test_48h(self):
        day_after = (date.today() + timedelta(days=2)).isoformat()
        assert compute_urgency(day_after) == "48h"

    def test_far_future_returns_none(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        assert compute_urgency(future) is None

    def test_empty_string_returns_none(self):
        assert compute_urgency("") is None

    def test_invalid_date_returns_none(self):
        assert compute_urgency("not-a-date") is None

    def test_explicit_today_param(self):
        today = date(2025, 1, 10)
        assert compute_urgency("2025-01-09", today) == "overdue"
        assert compute_urgency("2025-01-10", today) == "today"
        assert compute_urgency("2025-01-11", today) == "tomorrow"
        assert compute_urgency("2025-01-12", today) == "48h"
        assert compute_urgency("2025-01-20", today) is None


# ── _in_quiet_hours ───────────────────────────────────────────────────────────


class TestQuietHours:
    def test_midnight_is_quiet_wrapped_window(self):
        # 22–8 wraps midnight; hour 0 is inside quiet window
        assert _in_quiet_hours(22, 8, _hour=0) is True

    def test_evening_is_quiet(self):
        assert _in_quiet_hours(22, 8, _hour=23) is True

    def test_morning_before_end_is_quiet(self):
        assert _in_quiet_hours(22, 8, _hour=7) is True

    def test_midday_is_not_quiet(self):
        assert _in_quiet_hours(22, 8, _hour=12) is False

    def test_boundary_start_is_quiet(self):
        assert _in_quiet_hours(22, 8, _hour=22) is True

    def test_boundary_end_is_not_quiet(self):
        # quiet_end is exclusive
        assert _in_quiet_hours(22, 8, _hour=8) is False

    def test_non_wrapping_window(self):
        # quiet 12–14 covers hours 12 and 13
        assert _in_quiet_hours(12, 14, _hour=12) is True
        assert _in_quiet_hours(12, 14, _hour=13) is True
        assert _in_quiet_hours(12, 14, _hour=14) is False
        assert _in_quiet_hours(12, 14, _hour=11) is False


# ── build_nudge_message ───────────────────────────────────────────────────────


class TestBuildNudgeMessage:
    def test_overdue_message(self):
        msg = build_nudge_message({"title": "Lab report", "target_date": "2025-01-01"}, "overdue")
        assert "overdue" in msg.lower() or "⚠️" in msg
        assert "Lab report" in msg

    def test_today_message(self):
        msg = build_nudge_message({"title": "Submit essay", "target_date": "2025-01-10"}, "today")
        assert "today" in msg.lower() or "📅" in msg
        assert "Submit essay" in msg

    def test_tomorrow_message(self):
        msg = build_nudge_message({"title": "Study session"}, "tomorrow")
        assert "tomorrow" in msg.lower() or "🔔" in msg

    def test_48h_message(self):
        msg = build_nudge_message({"title": "Project draft"}, "48h")
        assert "2 days" in msg or "⏳" in msg


# ── _was_nudged_recently ──────────────────────────────────────────────────────


class TestWasNudgedRecently:
    def test_no_log_returns_false(self):
        assert _was_nudged_recently("goal-xyz", 3600) is False

    def test_recent_nudge_via_append_log(self, tmp_path):
        import nudge_scheduler as ns
        # Manually write a recent nudge record
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ns._append_nudge_log({"ts": now, "goal_id": "gA", "urgency": "today", "message": "test"})
        assert ns._was_nudged_recently("gA", 3600) is True

    def test_old_nudge_returns_false(self, tmp_path):
        import nudge_scheduler as ns
        old_ts = (datetime.now(UTC) - timedelta(seconds=7200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ns._append_nudge_log({"ts": old_ts, "goal_id": "gB", "urgency": "overdue", "message": "old"})
        assert ns._was_nudged_recently("gB", 3600) is False


# ── NudgeScheduler instantiation ─────────────────────────────────────────────


class TestNudgeSchedulerInit:
    def test_default_init(self):
        sched = NudgeScheduler()
        assert sched.scan_interval > 0
        assert sched.throttle_seconds > 0
        assert sched._running is False

    def test_update_user_activity(self):
        sched = NudgeScheduler()
        sched.update_user_activity("session-1")
        assert "session-1" in sched._last_user_message

    def test_is_user_idle_never_messaged(self):
        sched = NudgeScheduler(idle_seconds=60)
        assert sched._is_user_idle("new-session") is True

    def test_is_user_idle_recent_message(self):
        sched = NudgeScheduler(idle_seconds=3600)
        sched.update_user_activity("s1")
        assert sched._is_user_idle("s1") is False

    def test_is_user_idle_old_message(self):
        sched = NudgeScheduler(idle_seconds=1)
        import time
        sched.update_user_activity("s2")
        time.sleep(0.01)
        # idle_seconds=1 but user messaged just now — test that flag logic exists
        sched._last_user_message["s2"] = datetime.now(UTC) - timedelta(seconds=10)
        assert sched._is_user_idle("s2") is True
