"""Proactive deadline/nudge scheduler for VaultBot's student-secretary features.

Scans goals and tasks for upcoming deadlines and delivers throttled
nudge messages to connected WebSocket sessions.

DESIGN
------
* ``NudgeScheduler`` is a background asyncio loop started by ``main.py``
  at boot (single instance, per-process).
* Every ``scan_interval`` seconds it checks all active goals for
  upcoming target_dates.
* It computes urgency windows (overdue, today, tomorrow, 48h).
* It only sends a nudge when:
  - A WebSocket session is connected and the user has been idle for at
    least ``idle_seconds`` (no message in the last N seconds).
  - The same goal hasn't been nudged within the ``throttle_seconds``
    window (to avoid spam).
  - The current time is outside the configured quiet hours.
* Nudge history is persisted to ``User/State/nudge_log.jsonl`` so the
  throttle window survives restarts.

This is NOT on the hot path — it runs in a separate asyncio task and
failures are logged but never surface to the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import paths
import user_state

logger = logging.getLogger(__name__)

# ── Nudge log ────────────────────────────────────────────────────────────────
_nudge_log_lock = threading.Lock()

_STATE_SUBDIR = Path("User") / "State"


def _nudge_log_path() -> Path:
    return paths.VAULT_ROOT / _STATE_SUBDIR / "nudge_log.jsonl"  # type: ignore[operator]


def _append_nudge_log(record: dict[str, Any]) -> None:
    p = _nudge_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _nudge_log_lock:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_nudge_log(last_n: int = 500) -> list[dict[str, Any]]:
    p = _nudge_log_path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines[-last_n:]:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


# ── Urgency computation ──────────────────────────────────────────────────────


def compute_urgency(target_date_str: str, today: date | None = None) -> str | None:
    """Return urgency label for a target_date string (YYYY-MM-DD).

    Returns one of: ``'overdue'``, ``'today'``, ``'tomorrow'``, ``'48h'``,
    or ``None`` if the deadline is further out or unparseable.
    """
    if not target_date_str:
        return None
    try:
        target = date.fromisoformat(target_date_str)
    except ValueError:
        return None
    today = today or datetime.now(UTC).date()
    delta = (target - today).days
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta <= 2:
        return "48h"
    return None


def _was_nudged_recently(goal_id: str, throttle_seconds: int) -> bool:
    """True if a nudge for *goal_id* was sent within *throttle_seconds*."""
    cutoff = datetime.now(UTC) - timedelta(seconds=throttle_seconds)
    for record in reversed(_read_nudge_log()):
        if record.get("goal_id") == goal_id:
            ts = _parse_iso(record.get("ts", ""))
            if ts >= cutoff:
                return True
            break  # records are appended newest-last; first old hit = done
    return False


def _in_quiet_hours(quiet_start: int = 22, quiet_end: int = 8, *, _hour: int | None = None) -> bool:
    """True if the current local hour is in the quiet window [quiet_start, quiet_end)."""
    hour = _hour if _hour is not None else datetime.now().hour  # local time
    if quiet_start > quiet_end:  # wraps midnight
        return hour >= quiet_start or hour < quiet_end
    return quiet_start <= hour < quiet_end


def _parse_iso(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=UTC)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Nudge message builder ────────────────────────────────────────────────────


def build_nudge_message(goal: dict[str, Any], urgency: str) -> str:
    """Return a concise, actionable nudge message for the given goal."""
    title = goal.get("title", "a goal")
    target = goal.get("target_date", "")
    label_map = {
        "overdue": f"⚠️ **{title}** is overdue (was due {target}). Want to reschedule or mark it done?",
        "today": f"📅 **{title}** is due today! What's your next action?",
        "tomorrow": f"🔔 **{title}** is due tomorrow ({target}). Time to push on this.",
        "48h": f"⏳ **{title}** is due in 2 days ({target}). Any blockers to clear?",
    }
    return label_map.get(urgency, f"Reminder: **{title}** ({target})")


# ── NudgeScheduler ───────────────────────────────────────────────────────────


class NudgeScheduler:
    """Background asyncio task that scans goals and delivers throttled nudges."""

    def __init__(
        self,
        *,
        scan_interval: int = 300,       # seconds between scans
        throttle_seconds: int = 3600,   # min time between nudges for same goal
        idle_seconds: int = 60,         # user must be idle this long before nudge
        quiet_start: int = 22,          # quiet hours start (local hour)
        quiet_end: int = 8,             # quiet hours end (local hour)
    ) -> None:
        self.scan_interval = int(os.environ.get("VAULTBOT_NUDGE_INTERVAL", scan_interval))
        self.throttle_seconds = throttle_seconds
        self.idle_seconds = idle_seconds
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end
        self._running = False
        self._task: asyncio.Task | None = None
        # Injected at start() — the Services manager for WS delivery
        self._manager: Any = None
        # Track last user message time per session (set by chat_handler)
        self._last_user_message: dict[str, datetime] = {}

    def update_user_activity(self, session_id: str) -> None:
        """Called by chat_handler every time the user sends a message."""
        self._last_user_message[session_id] = datetime.now(UTC)

    def _is_user_idle(self, session_id: str) -> bool:
        last = self._last_user_message.get(session_id)
        if last is None:
            return True  # never messaged = idle
        return (datetime.now(UTC) - last).total_seconds() >= self.idle_seconds

    async def start(self, manager: Any) -> None:
        """Start the background scan loop.  ``manager`` is the WS ConnectionManager."""
        if self._running:
            return
        self._manager = manager
        self._running = True
        self._task = asyncio.create_task(self._scan_loop())
        logger.info("nudge_scheduler: started (interval=%ds)", self.scan_interval)

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _scan_loop(self) -> None:
        while self._running:
            try:
                await self._scan_once()
            except Exception:  # noqa: BLE001 — best-effort background scan
                logger.warning("nudge_scheduler: scan failed", exc_info=True)
            await asyncio.sleep(self.scan_interval)

    async def _scan_once(self) -> None:
        if _in_quiet_hours(self.quiet_start, self.quiet_end):
            logger.debug("nudge_scheduler: quiet hours, skipping")
            return

        goals = user_state.list_goals(status_filter="active")
        today = datetime.now(UTC).date()

        for goal in goals:
            urgency = compute_urgency(goal.get("target_date", ""), today)
            if urgency is None:
                continue
            goal_id = goal.get("id", "")
            if _was_nudged_recently(goal_id, self.throttle_seconds):
                logger.debug("nudge_scheduler: throttled goal_id=%s", goal_id)
                continue

            await self._deliver_nudge(goal, urgency)

    async def _deliver_nudge(self, goal: dict[str, Any], urgency: str) -> None:
        """Send a nudge to connected, idle sessions."""
        if self._manager is None:
            return

        message = build_nudge_message(goal, urgency)
        goal_id = goal.get("id", "")

        delivered = False
        try:
            # Try to broadcast to all active sessions
            sessions = getattr(self._manager, "active_connections", {})
            for session_id, ws in list(sessions.items()):
                if not self._is_user_idle(session_id):
                    continue
                try:
                    await self._manager.send_personal_message(
                        {"type": "nudge", "message": message, "urgency": urgency,
                         "goal_id": goal_id},
                        ws,
                    )
                    delivered = True
                    logger.info(
                        "nudge_scheduler: nudge delivered goal_id=%s urgency=%s session=%s",
                        goal_id, urgency, session_id,
                    )
                except Exception:  # noqa: BLE001 — best-effort delivery
                    logger.debug("nudge_scheduler: delivery failed session=%s", session_id)
        except Exception:  # noqa: BLE001 — best-effort background task
            logger.warning("nudge_scheduler: _deliver_nudge failed", exc_info=True)
            return

        if delivered:
            _append_nudge_log({
                "ts": _now_iso(),
                "goal_id": goal_id,
                "urgency": urgency,
                "message": message,
            })


# ── Module-level singleton ────────────────────────────────────────────────────
# Accessed by main.py and chat_handler.py.
scheduler = NudgeScheduler()
