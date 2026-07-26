"""Config endpoint: /config (GET + POST).

FreeSearch is keyless, so the config surface is informational only (which
engines are up / cooling down). Migrated from main.py.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app_state import get_services
from services import Services

router = APIRouter()


@router.get("/config")
async def get_config(svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """Return the current research-backend configuration + engine health."""
    engines = []
    for b in getattr(svc.search_client, "_backends", []):
        engines.append({
            "name": b.name,
            "in_cooldown": b._in_cooldown(),
            "cooldown_remaining_s": int(b._cooldown_remaining()),
        })
    return {
        "research_backend": "freesearch",
        "search_configured": svc.search_client.is_configured,
        "engines": engines,
    }


@router.post("/config")
async def set_config(payload: dict, svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """Update research-backend settings at runtime.

    FreeSearch is keyless, so tavily_api_key / research_backend are accepted
    for plugin backwards-compat but are no-ops. We always report freesearch.
    """
    return {
        "status": "ok",
        "research_backend": "freesearch",
        "search_configured": svc.search_client.is_configured,
    }