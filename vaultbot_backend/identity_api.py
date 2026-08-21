"""Identity API handlers extracted from main.py.

These are the /identity route handlers (GET /identity).
They operate on the ``identity`` singleton held in the ``Services``
registry; the @app decorators stay in main.py as thin shims that forward
to these extracted functions.

Each function receives the ``Services`` instance as its first parameter and
accesses the identity singleton via ``svc.identity`` instead of reading
main.py's module-level globals as free variables.
"""

from __future__ import annotations

from services import Services


async def get_identity(svc: Services):
    """Return the agent's current identity state so the UI can show
    who the agent is and what it's working on."""
    identity = svc.identity
    return {
        "identity": identity.get_identity(),
        "summary": identity.summary(),
    }
