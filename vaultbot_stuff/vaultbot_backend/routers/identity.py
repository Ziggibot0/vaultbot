"""Identity endpoints: the identity layer.

These were thin shims in main.py that deferred-imported identity_api.* and
injected svc. The router calls the extracted functions directly.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app_state import get_services
from services import Services
from identity_api import get_identity

router = APIRouter()


@router.get("/identity")
async def get_identity_endpoint(svc: Annotated[Services, Depends(get_services)]):
    """Return the agent's current identity state so the UI can show
    who the agent is and what it's working on.
    """
    return await get_identity(svc)
