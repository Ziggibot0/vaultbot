"""Custom tool endpoints (for the MCP server + external clients).

Migrated from main.py. Handlers read singletons via Depends(get_services).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app_state import get_services
from services import Services

router = APIRouter()


@router.get("/custom_tools")
async def list_custom_tools(
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """Return schemas for all agent-authored custom tools."""
    return {"tools": svc.self_improver.custom_tool_schemas()}


@router.post("/custom_tools/call")
async def call_custom_tool(
    payload: dict, svc: Annotated[Services, Depends(get_services)]
):
    """Execute an agent-authored custom tool by name."""
    name = payload.get("name", "")
    args = payload.get("args", {})
    if not svc.self_improver.has_tool(name):
        return {"error": f"custom tool not found: {name}"}, 404
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: svc.self_improver.execute_custom_tool(name, args)
    )
    return result
