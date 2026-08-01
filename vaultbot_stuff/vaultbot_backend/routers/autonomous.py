"""Autonomous researcher + consolidation endpoints.

Migrated from main.py. Handlers read singletons via Depends(get_services).
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app_state import get_services
from services import Services

router = APIRouter()


@router.get("/autonomous/status")
async def autonomous_status(svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """Report autonomous researcher state and recent history."""
    return svc.autonomous_researcher.status()


@router.get("/autonomous/gaps")
async def autonomous_gaps(svc: Annotated[Services, Depends(get_services)]):
    """List the vault's current knowledge gaps via the knowledge curriculum.

    Uses the Voyager-style diversity-aware curriculum (not the simple
    reference-count ranking) so the gaps reflect what the vault should
    learn next for maximum coverage at achievable cost.
    """
    try:
        loop = asyncio.get_event_loop()
        gaps = await loop.run_in_executor(
            None, svc.knowledge_curriculum.propose_next_gaps, 20)
        return {"gaps": gaps, "count": len(gaps),
                "curriculum_state": svc.knowledge_curriculum.state_summary()}
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        svc.session_logger.log_exception(e, context="autonomous_gaps")
        return {"error": str(e)}, 500


@router.post("/autonomous/trigger")
async def autonomous_trigger(svc: Annotated[Services, Depends(get_services)]):
    """Run one autonomous research cycle immediately."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, svc.autonomous_researcher.trigger_now)
    return result


@router.get("/consolidation/gaps")
async def consolidation_gaps(svc: Annotated[Services, Depends(get_services)]):
    """Return patterns ripe for semantic consolidation.

    The pattern extractor scans chat logs for recurring topics, correction
    patterns, tool usage, and self-model drift. These gaps can be
    consolidated into semantic knowledge notes so future sessions start
    smarter. See [[Semantic-Consolidation-Architecture]].
    """
    try:
        loop = asyncio.get_event_loop()
        gaps = await loop.run_in_executor(
            None, svc.pattern_extractor.get_consolidation_gaps)
        return {"gaps": gaps, "count": len(gaps),
                "report": svc.pattern_extractor.consolidation_report()}
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        svc.session_logger.log_exception(e, context="consolidation_gaps")
        return {"error": str(e)}, 500


@router.post("/consolidation/extract")
async def consolidation_extract(svc: Annotated[Services, Depends(get_services)]):
    """Run pattern extraction and log the results without writing a note.

    This is the scan-only step of the consolidation pipeline. It extracts
    patterns deterministically (no LLM) and logs them. The LLM can then
    synthesize semantic notes from the pre-extracted findings.
    """
    try:
        loop = asyncio.get_event_loop()
        patterns = await loop.run_in_executor(None, svc.pattern_extractor.extract_all)
        svc.pattern_extractor.log_consolidation(patterns)
        return {
            "sessions_scanned": patterns["total_sessions"],
            "exchanges_scanned": patterns["total_exchanges"],
            "recurring_topics": len(patterns["recurring_topics"]),
            "sentiment": patterns["sentiment"]["distribution"],
            "negative_rate": patterns["sentiment"]["negative_rate"],
            "tool_frequency": dict(
                list(patterns["tool_patterns"]["tool_frequency"].items())[:10]),
            "over_reporting": patterns["over_reporting"]["count"],
            "self_model_drift": patterns["self_model_drift"],
        }
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        svc.session_logger.log_exception(e, context="consolidation_extract")
        return {"error": str(e)}, 500


@router.post("/autonomous/toggle")
async def autonomous_toggle(payload: dict | None = None,
                             svc: Annotated[Services, Depends(get_services)] = None):
    """Enable or disable the autonomous researcher."""
    if payload is None:
        payload = {}
    enable = payload.get("enabled", not svc.autonomous_researcher.enabled)
    svc.autonomous_researcher.enabled = bool(enable)
    if enable and not (svc.autonomous_researcher._thread
                       and svc.autonomous_researcher._thread.is_alive()):
        svc.autonomous_researcher.start()
    elif not enable:
        svc.autonomous_researcher.stop()
    return svc.autonomous_researcher.status()
