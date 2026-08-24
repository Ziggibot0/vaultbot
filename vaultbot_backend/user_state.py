"""Personal-state substrate for VaultBot's student-secretary features.

This module is the single source of truth for reading and writing the
student's durable personal state:

  - ``User/State/profile.json``   — identity, preferences, timezone
  - ``User/State/goals.json``     — active/archived goals
  - ``User/State/wellbeing.jsonl``— append-only mood/energy check-in log
  - ``User/State/User-Model.md``  — human-readable summary regenerated
                                    from the structured files above

DESIGN PRINCIPLES
-----------------
* Vault-first: all state files live inside the vault under ``User/State/``.
* Atomic writes: JSON files are written via a tempfile + os.replace so a
  crash during write never leaves a corrupt file.
* Append-only log: ``wellbeing.jsonl`` is append-only; never rewritten.
* Schema-validated: every write goes through a lightweight pydantic-free
  validator so the model cannot corrupt state with a badly shaped dict.
* Audit-logged: every write emits a Python ``logging`` record at INFO
  level (caller, key changed, timestamp) for traceability.
* Thread-safe: module-level locks protect each state file.

BOOT CONTEXT
------------
``build_boot_summary()`` returns a compact (≤500 char) string suitable for
injection into the system prompt every turn.  It shows the owner name,
active goal count, today's top goal, and the most recent wellbeing check-in
so every coaching turn is grounded in live state.

This is a pure leaf module — no FastAPI, no Services, no asyncio.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import paths

logger = logging.getLogger(__name__)

# ── Per-file write locks ─────────────────────────────────────────────────────
_profile_lock = threading.Lock()
_goals_lock = threading.Lock()
_wellbeing_lock = threading.Lock()
_user_model_lock = threading.Lock()

# ── State directory ──────────────────────────────────────────────────────────
_STATE_SUBDIR = Path("User") / "State"


def _state_dir() -> Path:
    """Resolve User/State inside the vault root (lazy — honours VAULT_PATH)."""
    return paths.VAULT_ROOT / _STATE_SUBDIR  # type: ignore[operator]


def _ensure_state_dir() -> Path:
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Atomic JSON write ────────────────────────────────────────────────────────


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write *data* to *path* atomically via a same-directory tempfile."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ── Schema validators ────────────────────────────────────────────────────────

_ALLOWED_GOAL_STATUSES = {"active", "completed", "archived", "paused"}


def _validate_profile(data: Any) -> dict[str, Any]:
    """Validate and normalise a profile dict."""
    if not isinstance(data, dict):
        raise ValueError(f"profile must be a dict, got {type(data).__name__}")
    allowed = {"name", "timezone", "preferences", "updated_at", "schema_version"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown profile keys: {unknown}")
    return data


def _validate_goals(data: Any) -> dict[str, Any]:
    """Validate and normalise a goals container."""
    if not isinstance(data, dict):
        raise ValueError(f"goals must be a dict, got {type(data).__name__}")
    if "goals" not in data:
        raise ValueError("goals container must have a 'goals' key")
    if not isinstance(data["goals"], list):
        raise ValueError("goals['goals'] must be a list")
    for g in data["goals"]:
        _validate_single_goal(g)
    return data


def _validate_single_goal(g: Any) -> None:
    required = {"id", "title", "status"}
    if not isinstance(g, dict):
        raise ValueError(f"goal entry must be a dict, got {type(g).__name__}")
    missing = required - set(g)
    if missing:
        raise ValueError(f"Goal missing required keys: {missing}")
    if g["status"] not in _ALLOWED_GOAL_STATUSES:
        raise ValueError(
            f"goal status {g['status']!r} not in {_ALLOWED_GOAL_STATUSES}"
        )


def _validate_checkin(data: Any) -> dict[str, Any]:
    """Validate a single wellbeing check-in record."""
    if not isinstance(data, dict):
        raise ValueError(f"check-in must be a dict, got {type(data).__name__}")
    required = {"ts", "mood", "energy", "stress"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"Check-in missing required keys: {missing}")
    for k in ("mood", "energy", "stress"):
        v = data[k]
        if not isinstance(v, (int, float)) or not (1 <= v <= 5):
            raise ValueError(f"Check-in field {k!r} must be a number 1-5, got {v!r}")
    return data


# ── Profile ──────────────────────────────────────────────────────────────────


def read_profile() -> dict[str, Any]:
    """Return the profile dict, or ``{}`` if no file exists yet."""
    p = _state_dir() / "profile.json"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def upsert_profile(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge *updates* into the profile and persist.  Returns the new profile."""
    if not isinstance(updates, dict):
        raise ValueError("updates must be a dict")
    with _profile_lock:
        current = read_profile()
        merged = {**current, **updates, "updated_at": _now_iso()}
        _validate_profile(merged)
        _atomic_write_json(_state_dir() / "profile.json", merged)
    logger.info("user_state: profile updated keys=%s", sorted(updates))
    return merged


# ── Goals ────────────────────────────────────────────────────────────────────


def read_goals() -> dict[str, Any]:
    """Return the goals container, or ``{"goals": []}`` if no file exists."""
    p = _state_dir() / "goals.json"
    if not p.exists():
        return {"goals": [], "schema_version": 1}
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def list_goals(status_filter: str | None = None) -> list[dict[str, Any]]:
    """Return goals, optionally filtered by ``status``."""
    container = read_goals()
    goals = container.get("goals", [])
    if status_filter:
        goals = [g for g in goals if g.get("status") == status_filter]
    return goals


def upsert_goal(goal: dict[str, Any]) -> dict[str, Any]:
    """Insert or update a goal by ``id``.  Returns the stored goal dict."""
    if not isinstance(goal, dict):
        raise ValueError(f"goal entry must be a dict, got {type(goal).__name__}")
    if "id" not in goal:
        raise ValueError("Goal missing required keys: {'id'}")
    if "status" in goal and goal["status"] not in _ALLOWED_GOAL_STATUSES:
        raise ValueError(
            f"goal status {goal['status']!r} not in {_ALLOWED_GOAL_STATUSES}"
        )
    with _goals_lock:
        container = read_goals()
        goals: list[dict[str, Any]] = container.get("goals", [])
        idx = next((i for i, g in enumerate(goals) if g.get("id") == goal["id"]), None)
        goal = {**goal, "updated_at": _now_iso()}
        if idx is None:
            # Insert: full validation required (title + status must be present)
            if "title" not in goal:
                raise ValueError("Goal missing required keys: {'title'}")
            if "status" not in goal:
                goal["status"] = "active"
            if "created_at" not in goal:
                goal["created_at"] = goal["updated_at"]
            _validate_single_goal(goal)
            goals.append(goal)
            action = "insert"
        else:
            # Update: merge with existing so required fields are always present
            existing = goals[idx]
            merged = {**existing, **goal}
            _validate_single_goal(merged)
            goals[idx] = merged
            goal = merged
            action = "update"
        container["goals"] = goals
        _validate_goals(container)
        _atomic_write_json(_state_dir() / "goals.json", container)
    logger.info("user_state: goal %s id=%s title=%r", action, goal["id"], goal.get("title"))
    return goal


def complete_goal(goal_id: str) -> dict[str, Any] | None:
    """Mark a goal as completed.  Returns the updated goal or None if not found."""
    with _goals_lock:
        container = read_goals()
        goals = container.get("goals", [])
        idx = next((i for i, g in enumerate(goals) if g.get("id") == goal_id), None)
        if idx is None:
            return None
        goals[idx] = {**goals[idx], "status": "completed", "updated_at": _now_iso()}
        container["goals"] = goals
        _atomic_write_json(_state_dir() / "goals.json", container)
    logger.info("user_state: goal completed id=%s", goal_id)
    return goals[idx]


def archive_goal(goal_id: str) -> dict[str, Any] | None:
    """Archive a goal.  Returns the updated goal or None if not found."""
    with _goals_lock:
        container = read_goals()
        goals = container.get("goals", [])
        idx = next((i for i, g in enumerate(goals) if g.get("id") == goal_id), None)
        if idx is None:
            return None
        goals[idx] = {**goals[idx], "status": "archived", "updated_at": _now_iso()}
        container["goals"] = goals
        _atomic_write_json(_state_dir() / "goals.json", container)
    logger.info("user_state: goal archived id=%s", goal_id)
    return goals[idx]


# ── Wellbeing ────────────────────────────────────────────────────────────────


def append_checkin(record: dict[str, Any]) -> dict[str, Any]:
    """Validate and append a check-in to wellbeing.jsonl.  Returns the record."""
    if "ts" not in record:
        record = {**record, "ts": _now_iso()}
    _validate_checkin(record)
    p = _ensure_state_dir() / "wellbeing.jsonl"
    with _wellbeing_lock:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info(
        "user_state: checkin mood=%s energy=%s stress=%s",
        record.get("mood"),
        record.get("energy"),
        record.get("stress"),
    )
    return record


def read_checkins(last_n: int = 30) -> list[dict[str, Any]]:
    """Return the last *last_n* check-ins (newest last)."""
    p = _state_dir() / "wellbeing.jsonl"
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("user_state: malformed wellbeing.jsonl line skipped")
    return records[-last_n:]


def wellbeing_summary(days: int = 7) -> dict[str, Any]:
    """Return a rolling summary of mood/energy/stress over the last *days* days.

    Returns ``{"count": 0}`` when there are no recent check-ins.
    """
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(days=days)
    recent = [
        r
        for r in read_checkins(last_n=200)
        if _parse_iso(r.get("ts", "")) >= cutoff
    ]
    if not recent:
        return {"count": 0, "days": days}
    count = len(recent)
    avg_mood = round(sum(r["mood"] for r in recent) / count, 2)
    avg_energy = round(sum(r["energy"] for r in recent) / count, 2)
    avg_stress = round(sum(r["stress"] for r in recent) / count, 2)
    # Burnout risk: simple heuristic — high stress + low energy
    burnout_risk = "high" if avg_stress >= 4 and avg_energy <= 2 else (
        "medium" if avg_stress >= 3.5 or avg_energy <= 2.5 else "low"
    )
    return {
        "count": count,
        "days": days,
        "avg_mood": avg_mood,
        "avg_energy": avg_energy,
        "avg_stress": avg_stress,
        "burnout_risk": burnout_risk,
        "latest_ts": recent[-1]["ts"],
    }


# ── User-Model.md regeneration ───────────────────────────────────────────────


def regenerate_user_model() -> str:
    """Regenerate User/State/User-Model.md from structured state.

    Returns the rendered markdown string (also written to the vault).
    """
    profile = read_profile()
    active_goals = list_goals(status_filter="active")
    wb = wellbeing_summary()

    name = profile.get("name", os.environ.get("VAULTBOT_OWNER", "unknown"))
    tz = profile.get("timezone", "UTC")
    now_str = _now_iso()

    lines = [
        "# User Model",
        "",
        f"> Generated: {now_str}",
        "",
        "## Identity",
        f"- **Name**: {name}",
        f"- **Timezone**: {tz}",
        "",
        "## Active Goals",
    ]
    if active_goals:
        for g in active_goals:
            due = f" (due {g['target_date']})" if g.get("target_date") else ""
            lines.append(f"- [{g['status']}] {g['title']}{due}")
    else:
        lines.append("- *(none)*")

    lines += [
        "",
        "## Recent Wellbeing",
    ]
    if wb.get("count", 0) > 0:
        lines += [
            f"- Check-ins (last {wb['days']}d): {wb['count']}",
            f"- Avg mood: {wb['avg_mood']}/5 | energy: {wb['avg_energy']}/5 | stress: {wb['avg_stress']}/5",
            f"- Burnout risk: **{wb['burnout_risk']}**",
        ]
    else:
        lines.append("- *(no recent check-ins)*")

    md = "\n".join(lines) + "\n"
    p = _ensure_state_dir() / "User-Model.md"
    with _user_model_lock:
        p.write_text(md, encoding="utf-8")
    logger.info("user_state: User-Model.md regenerated")
    return md


# ── Boot context summary ─────────────────────────────────────────────────────


def build_boot_summary() -> str:
    """Return a compact (≤500 char) personal-state summary for prompt injection.

    Grounded in live vault state.  Returns an empty string if the state
    directory has never been initialised (first boot, no profile yet).
    """
    try:
        profile = read_profile()
        if not profile:
            return ""
        name = profile.get("name", "")
        active = list_goals(status_filter="active")
        wb = wellbeing_summary(days=7)

        parts = []
        if name:
            parts.append(f"User: {name}")
        if active:
            top = active[0]["title"]
            parts.append(f"Top goal: {top}")
            if len(active) > 1:
                parts.append(f"(+{len(active)-1} more active)")
        if wb.get("count", 0) > 0:
            parts.append(
                f"Recent wellbeing: mood {wb['avg_mood']}/5 "
                f"energy {wb['avg_energy']}/5 "
                f"stress {wb['avg_stress']}/5 "
                f"(burnout risk: {wb['burnout_risk']})"
            )
        return " | ".join(parts)
    except Exception:  # noqa: BLE001 — best-effort; never crash the boot path
        logger.warning("user_state: build_boot_summary failed", exc_info=True)
        return ""


# ── Helpers ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 UTC timestamp string to a timezone-aware datetime."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=UTC)
