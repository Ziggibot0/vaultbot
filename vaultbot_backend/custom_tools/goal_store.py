"""Durable goal store tool for VaultBot's student-secretary features.

Provides CRUD operations on the student's persistent goals, stored in
``User/State/goals.json`` inside the vault.  Goals survive backend
restarts and /new session boundaries.

Actions
-------
- ``goal_list``     — list goals (optionally filtered by status)
- ``goal_upsert``   — insert or update a goal
- ``goal_complete`` — mark a goal completed
- ``goal_archive``  — move a goal to archived
"""

from __future__ import annotations

import uuid
from typing import Any

import user_state

SCHEMA: dict[str, Any] = {
    "name": "goal_store",
    "description": (
        "Manage the student's durable goal list.  "
        "Goals persist across restarts and /new sessions.  "
        "Actions: goal_list (list active/all goals), "
        "goal_upsert (add or update a goal), "
        "goal_complete (mark done), "
        "goal_archive (archive an old goal)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["goal_list", "goal_upsert", "goal_complete", "goal_archive"],
                "description": "The operation to perform.",
            },
            "status_filter": {
                "type": "string",
                "enum": ["active", "completed", "archived", "paused"],
                "description": "Status filter for goal_list; omit to list all goals.",
            },
            "id": {
                "type": "string",
                "description": (
                    "Goal ID (goal_upsert / goal_complete / goal_archive). "
                    "For goal_upsert, omit to auto-generate a UUID."
                ),
            },
            "title": {
                "type": "string",
                "description": "Goal title (goal_upsert, required for new goals).",
            },
            "priority": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Goal priority (goal_upsert).",
            },
            "status": {
                "type": "string",
                "enum": ["active", "completed", "archived", "paused"],
                "description": "Goal status (goal_upsert). Defaults to 'active'.",
            },
            "target_date": {
                "type": "string",
                "description": "Target completion date YYYY-MM-DD (goal_upsert).",
            },
            "cadence": {
                "type": "string",
                "description": "Recurrence description, e.g. '3x/week' (goal_upsert).",
            },
            "notes": {
                "type": "string",
                "description": "Free-text notes for the goal (goal_upsert).",
            },
        },
        "required": ["action"],
    },
}


def run(args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch goal_store actions."""
    action = args.get("action", "")

    if action == "goal_list":
        return _goal_list(args)
    if action == "goal_upsert":
        return _goal_upsert(args)
    if action == "goal_complete":
        return _goal_complete(args)
    if action == "goal_archive":
        return _goal_archive(args)
    return {"error": f"Unknown action: {action!r}"}


# ── Action handlers ──────────────────────────────────────────────────────────


def _goal_list(args: dict[str, Any]) -> dict[str, Any]:
    status_filter = args.get("status_filter")
    goals = user_state.list_goals(status_filter=status_filter)
    return {
        "status": "ok",
        "count": len(goals),
        "goals": goals,
        "filter": status_filter or "all",
    }


def _goal_upsert(args: dict[str, Any]) -> dict[str, Any]:
    goal_id = args.get("id") or str(uuid.uuid4())
    title = args.get("title")
    # title is required only for new goals (no existing id in the store)
    if not title:
        existing_goals = user_state.list_goals()
        existing = next((g for g in existing_goals if g.get("id") == goal_id), None)
        if existing is None:
            return {"error": "title is required when creating a new goal"}
    goal: dict[str, Any] = {
        "id": goal_id,
        "status": args.get("status", "active"),
    }
    if title:
        goal["title"] = title
    for opt in ("priority", "target_date", "cadence", "notes"):
        if args.get(opt) is not None:
            goal[opt] = args[opt]
    try:
        stored = user_state.upsert_goal(goal)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"status": "ok", "action": "upserted", "goal": stored}


def _goal_complete(args: dict[str, Any]) -> dict[str, Any]:
    goal_id = args.get("id")
    if not goal_id:
        return {"error": "id is required for goal_complete"}
    result = user_state.complete_goal(goal_id)
    if result is None:
        return {"error": f"Goal not found: {goal_id!r}"}
    return {"status": "ok", "action": "completed", "goal": result}


def _goal_archive(args: dict[str, Any]) -> dict[str, Any]:
    goal_id = args.get("id")
    if not goal_id:
        return {"error": "id is required for goal_archive"}
    result = user_state.archive_goal(goal_id)
    if result is None:
        return {"error": f"Goal not found: {goal_id!r}"}
    return {"status": "ok", "action": "archived", "goal": result}
