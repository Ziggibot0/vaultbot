"""Task endpoints: plain-English → verified plan execution.

These were thin shims in main.py that deferred-imported task_api.* and
injected svc. The router calls the extracted functions directly.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app_state import get_services
from services import Services
from task_api import create_task, get_task, resume_task

router = APIRouter()


@router.post("/task")
async def create_task_endpoint(payload: dict,
                                svc: Annotated[Services, Depends(get_services)]):
    """Take a plain-English task, decompose it into a JSON plan of atomic
    idempotent graph-op subtasks (each with a deterministic verifier), and
    execute them against the curated graph-op vocabulary.
    """
    return await create_task(svc, payload)


@router.get("/task/{plan_id}")
async def get_task_endpoint(plan_id: str,
                            svc: Annotated[Services, Depends(get_services)]):
    """Retrieve a persisted plan's status."""
    return await get_task(svc, plan_id)


@router.post("/task/{plan_id}/resume")
async def resume_task_endpoint(plan_id: str,
                                svc: Annotated[Services, Depends(get_services)]):
    """Resume a partially-completed plan from disk."""
    return await resume_task(svc, plan_id)