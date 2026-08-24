"""Wellbeing check-in tool for VaultBot's student-secretary features.

Captures mood / energy / stress check-ins, stores them in
``User/State/wellbeing.jsonl``, and returns rolling summaries.

Actions
-------
- ``checkin``  — record a new mood/energy/stress check-in
- ``summary``  — return rolling averages + burnout-risk signal
- ``history``  — return the last N raw check-in records
"""

from __future__ import annotations

from typing import Any

import user_state

SCHEMA: dict[str, Any] = {
    "name": "wellbeing_checkin",
    "description": (
        "Record and retrieve mood/energy/stress check-ins for the student. "
        "Use 'checkin' to capture how the student is feeling right now "
        "(mood, energy, stress each 1-5). "
        "Use 'summary' to get a rolling 7-day average and burnout-risk signal. "
        "Use 'history' to view recent raw records."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["checkin", "summary", "history"],
                "description": "The operation to perform.",
            },
            "mood": {
                "type": "number",
                "description": "Mood score 1-5 (required for checkin).",
            },
            "energy": {
                "type": "number",
                "description": "Energy score 1-5 (required for checkin).",
            },
            "stress": {
                "type": "number",
                "description": "Stress score 1-5 (required for checkin).",
            },
            "note": {
                "type": "string",
                "description": "Optional free-text note.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional context tags: sleep, workload, gym, social, illness, etc."
                ),
            },
            "days": {
                "type": "integer",
                "description": "Rolling window in days for 'summary' (default 7).",
            },
            "last_n": {
                "type": "integer",
                "description": "Number of records for history (default 10).",
            },
        },
        "required": ["action"],
    },
}


def run(args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch wellbeing_checkin actions."""
    action = args.get("action", "")

    if action == "checkin":
        return _checkin(args)
    if action == "summary":
        return _summary(args)
    if action == "history":
        return _history(args)
    return {"error": f"Unknown action: {action!r}"}


# ── Action handlers ──────────────────────────────────────────────────────────


def _checkin(args: dict[str, Any]) -> dict[str, Any]:
    for field in ("mood", "energy", "stress"):
        if args.get(field) is None:
            return {"error": f"'{field}' is required for checkin (number 1-5)"}
    record: dict[str, Any] = {
        "mood": args["mood"],
        "energy": args["energy"],
        "stress": args["stress"],
    }
    if args.get("note"):
        record["note"] = args["note"]
    if args.get("tags"):
        record["tags"] = args["tags"]
    try:
        stored = user_state.append_checkin(record)
    except ValueError as exc:
        return {"error": str(exc)}
    return {
        "status": "ok",
        "action": "checkin_recorded",
        "record": stored,
        "message": (
            f"Check-in recorded: mood {stored['mood']}/5, "
            f"energy {stored['energy']}/5, stress {stored['stress']}/5."
        ),
    }


def _summary(args: dict[str, Any]) -> dict[str, Any]:
    days = int(args.get("days") or 7)
    summary = user_state.wellbeing_summary(days=days)
    if summary.get("count", 0) == 0:
        return {
            "status": "ok",
            "message": f"No check-ins recorded in the last {days} days.",
            "summary": summary,
        }
    return {
        "status": "ok",
        "summary": summary,
        "message": (
            f"Last {days}d ({summary['count']} check-ins): "
            f"mood {summary['avg_mood']}/5, "
            f"energy {summary['avg_energy']}/5, "
            f"stress {summary['avg_stress']}/5. "
            f"Burnout risk: {summary['burnout_risk']}."
        ),
    }


def _history(args: dict[str, Any]) -> dict[str, Any]:
    last_n = int(args.get("last_n") or 10)
    records = user_state.read_checkins(last_n=last_n)
    return {
        "status": "ok",
        "count": len(records),
        "records": records,
    }
