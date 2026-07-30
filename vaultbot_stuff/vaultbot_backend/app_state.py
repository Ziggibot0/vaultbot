"""App-state singletons for FastAPI dependency injection.

Provides ``get_services()`` (the FastAPI dependency) and ``set_services()``
(called once by main.py at startup).  Routers use::

    from fastapi import Depends
    from app_state import get_services, Services

    @router.get("/x")
    async def handler(svc: Services = Depends(get_services)):
        ...

This is the service-locator surface that lets the extracted routers avoid
importing ``main`` (which would create a circular import).  ``main.py``
constructs the singletons, builds the ``Services`` dataclass, and calls
``set_services(svc)`` before ``app.include_router(...)``.

``app.dependency_overrides[get_services] = fake_services`` is how tests
inject fakes (see test_endpoints.py).
"""
from __future__ import annotations

from typing import Optional

from services import Services

# Module-level singleton — set once by main.py at startup, read by every
# router handler via Depends(get_services).  Typed Optional so a missing
# set_services() call fails loudly at the first request instead of silently
# returning a half-built Services.
_services: Optional[Services] = None


def set_services(svc: Services) -> None:
    """Set the global Services singleton. Called once by main.py at startup."""
    global _services
    _services = svc


def get_services() -> Services:
    """FastAPI dependency: return the global Services singleton.

    Raises RuntimeError if set_services() hasn't been called — this is
    intentional so a misconfigured app fails loudly at the first request
    rather than returning a None to a handler that dereferences it.
    """
    if _services is None:
        raise RuntimeError(
            "Services not initialized — main.py must call set_services() "
            "before serving requests."
        )
    return _services