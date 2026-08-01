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


from services import Services

# Module-level singleton — set once by main.py at startup, read by every
# router handler via Depends(get_services).  Typed Optional so a missing
# set_services() call fails loudly at the first request instead of silently
# returning a half-built Services.
_services: Services | None = None

# Module-level flag set by the background_index() task in main.py if the
# startup reindex fails. Read on the first WS connect (routers/ws.py) so the
# user sees the problem the moment they open the chat. Cleared after
# surfacing so it doesn't repeat. Lives HERE (not on main.py) so ws.py can
# read it via `from app_state import get_startup_reindex_failed` instead of
# `import main` — a bare `import main` re-executes main.py's top-level code
# (including acquire_lock() → sys.exit) and crashes every WebSocket.
startup_reindex_failed: str | None = None


def set_startup_reindex_failed(value: str | None) -> None:
    """Set the startup-reindex-failure flag. Called by main.py's background_index."""
    global startup_reindex_failed
    startup_reindex_failed = value


def get_startup_reindex_failed() -> str | None:
    """Read + CLEAR the startup-reindex-failure flag (one-shot notification)."""
    global startup_reindex_failed
    value = startup_reindex_failed
    startup_reindex_failed = None
    return value


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