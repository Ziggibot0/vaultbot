"""Identity endpoints: the two-file identity layer.

These were thin shims in main.py that deferred-imported identity_api.* and
injected svc. The router calls the extracted functions directly.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app_state import get_services
from services import Services
from identity_api import get_identity, regenerate_self_model

router = APIRouter()


@router.get("/identity")
async def get_identity_endpoint(svc: Annotated[Services, Depends(get_services)]):
    """Return the agent's current identity state (IDENTITY + SELF_MODEL)
    so the UI can show who the agent is and what it's working on.
    """
    return await get_identity(svc)


@router.post("/identity/self_model")
async def regenerate_self_model_endpoint(payload: dict,
                                         svc: Annotated[Services, Depends(get_services)]):
    """Regenerate the MIRROR-style bounded self-model from recent activity.

    This is the bounded reconstructive synthesis (regenerate, don't append)
    that gave +5-20% across 7 architecturally diverse models (MIRROR,
    arXiv:2506.00430). The self-model is a ≤3000-token first-person narrative
    that makes the agent coherent across days regardless of model.
    """
    return await regenerate_self_model(svc, payload)
