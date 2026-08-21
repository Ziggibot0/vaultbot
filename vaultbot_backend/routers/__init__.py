"""VaultBot backend routers package.

Each module in this package exposes an ``APIRouter`` instance (``router``)
that main.py includes via ``app.include_router(X.router)``.  Routers receive
the Services singleton via ``Depends(get_services)`` so they never import
``main`` (avoiding the circular import the extracted modules already follow).

Migration order (easiest first — see the Phase 3 plan in /memories/session/plan.md):
  system → llm → config → research → autonomous → custom_tools →
  task → identity → ws
"""
