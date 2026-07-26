"""Identity API handlers extracted from main.py.

These are the /identity route handlers (GET /identity, POST /identity/goals,
POST /identity/self_model). They operate on the ``identity`` singleton held
in the ``Services`` registry; the @app decorators stay in main.py as thin
shims that forward to these extracted functions.

Each function receives the ``Services`` instance as its first parameter and
accesses the identity singleton via ``svc.identity`` instead of reading
main.py's module-level globals as free variables.
"""
from __future__ import annotations

import asyncio

from services import Services


async def get_identity(svc: Services):
    """Return the agent's current identity state (IDENTITY + SELF_MODEL +
    GOALS) so the UI can show who the agent is and what it's working on."""
    identity = svc.identity
    return {
        "identity": identity.get_identity(),
        "self_model": identity.get_self_model(),
        "goals": identity.get_goals(),
        "summary": identity.summary(),
    }


async def set_goals(svc: Services, payload: dict):
    """Update the agent's active goal (full-replace GOALS.md)."""
    identity = svc.identity
    goal = payload.get("goal", "")
    steps = payload.get("steps", [])
    completed = payload.get("completed_step")
    next_step = payload.get("next_step")
    if not goal:
        return {"error": "missing goal"}, 400
    text = identity.update_goals(goal, steps, completed, next_step)
    return {"goals": text, "summary": identity.summary()}


async def regenerate_self_model(svc: Services, payload: dict):
    """Regenerate the MIRROR-style bounded self-model from recent activity.

    This is the bounded reconstructive synthesis (regenerate, don't append)
    that gave +5-20% across 7 architecturally diverse models (MIRROR,
    arXiv:2506.00430). The self-model is a ≤3000-token first-person narrative
    that makes the agent coherent across days regardless of model.
    """
    identity = svc.identity
    activity = payload.get("activity", "")
    loop = asyncio.get_event_loop()
    try:
        new_model = await loop.run_in_executor(
            None, lambda: identity.regenerate_self_model(activity))
    except Exception as e:
        return {"error": str(e)}, 500
    return {"self_model": new_model, "summary": identity.summary()}