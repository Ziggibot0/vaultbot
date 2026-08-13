"""
Tournament API router — model benchmarking tournament endpoints.

Endpoints:
  GET  /tournament/staging      — list models in the tournament staging pot
  POST /tournament/staging      — add a model to the staging pot
  DELETE /tournament/staging/{id} — remove a model from staging
  GET  /tournament/providers    — list providers (for staging model picker)
  GET  /tournament/benchmarks   — list benchmarks for a role
  POST /tournament/run          — run a tournament (sync, returns full results)
  WS   /tournament/ws           — run a tournament with streaming progress
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app_state import get_services
from services import Services
from providers import ROLES, ProviderRegistry
from tournament_benchmarks import get_benchmarks
from tournament_runner import run_tournament, run_tournament_streaming
from tournament_staging import TournamentStaging

router = APIRouter()
logger = logging.getLogger(__name__)


def _registry(svc: Services) -> ProviderRegistry:
    """Return the live ProviderRegistry, constructing one if absent."""
    reg = getattr(svc, "registry", None)
    if reg is None:
        from providers import ProviderRegistry as PR
        reg = PR.migrate_from_env()
        svc.registry = reg
    return reg


def _staging() -> TournamentStaging:
    """Return the tournament staging pot (lazy singleton)."""
    return TournamentStaging()


# ═══════════════════════════════════════════════════════════════════════════
# Tournament staging pot — models to evaluate before adding to main pot
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/tournament/staging")
async def tournament_staging_list(
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """List all models in the tournament staging pot."""
    reg = _registry(svc)
    staging = _staging()
    entries = []
    for e in staging.list_entries():
        prov = reg.get_provider(e.provider)
        entries.append({
            "id": e.id,
            "model": e.model,
            "provider": e.provider,
            "provider_label": prov.label if prov else e.provider,
            "provider_type": prov.type if prov else "",
            "label": e.label or e.model,
        })
    return {"entries": entries, "count": len(entries)}


@router.post("/tournament/staging")
async def tournament_staging_add(
    payload: dict,
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """Add a model to the tournament staging pot.

    Body: {"model": "qwen3.6:27b", "provider": "ollama-local", "label": ""}
    The model does NOT need to be in the main registry — it just needs a
    provider that exists (so we know how to connect to it).
    """
    model = (payload.get("model") or "").strip()
    provider = (payload.get("provider") or "").strip()
    label = (payload.get("label") or "").strip()

    if not model:
        return {"status": "error", "detail": "model required"}, 400
    if not provider:
        return {"status": "error", "detail": "provider required"}, 400

    reg = _registry(svc)
    if reg.get_provider(provider) is None:
        return {"status": "error",
                "detail": f"Provider '{provider}' not found. Add it in AI Models & Providers first."}, 400

    staging = _staging()
    entry = staging.add_entry(model, provider, label)
    return {"status": "ok", "entry": entry.to_dict()}


@router.delete("/tournament/staging/{entry_id}")
async def tournament_staging_remove(entry_id: str) -> dict[str, Any]:
    """Remove a model from the tournament staging pot."""
    staging = _staging()
    ok = staging.remove_entry(entry_id)
    return {"status": "ok" if ok else "not_found"}


@router.post("/tournament/staging/clear")
async def tournament_staging_clear() -> dict[str, Any]:
    """Clear all models from the tournament staging pot."""
    staging = _staging()
    staging.clear()
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════
# GET /tournament/staging/sizes — probe model sizes (MB/GB)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/tournament/staging/sizes")
async def tournament_staging_sizes(
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """Return the on-disk size of each staging model.

    For Ollama providers, parses ``ollama list`` output (which includes a SIZE
    column). For cloud providers, returns null (we can't know the size).
    Results are keyed by staging entry id.
    """
    import subprocess
    from subprocess_utils import Popen as _popen

    reg = _registry(svc)
    staging = _staging()
    sizes: dict[str, Any] = {}

    # Collect all ollama models from staging
    ollama_models: list[tuple[str, str]] = []  # (entry_id, model_name)
    for e in staging.list_entries():
        prov = reg.get_provider(e.provider)
        if prov is None or prov.type != "ollama":
            sizes[e.id] = None
        else:
            ollama_models.append((e.id, e.model))

    if ollama_models:
        # Run ollama list once and parse the table
        try:
            proc = _popen(
                ["ollama", "list"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            out, _ = proc.communicate(timeout=15)
            if proc.returncode == 0:
                # Parse: NAME  ID  SIZE  MODIFIED
                # Lines look like:
                # qwen3.5:0.8b   f3817196d142   1.0 GB   5 weeks ago
                model_sizes: dict[str, str] = {}
                for line in out.splitlines():
                    line = line.strip()
                    if not line or line.startswith("NAME"):
                        continue
                    parts = line.split()
                    if len(parts) >= 3:
                        name = parts[0]
                        # Size is the first part that looks like "1.0" or "500"
                        # followed by "GB", "MB", "KB", or "B"
                        size_str = None
                        for i in range(1, len(parts) - 1):
                            if parts[i + 1].upper() in ("GB", "MB", "KB", "B"):
                                size_str = f"{parts[i]} {parts[i+1]}"
                                break
                        if size_str:
                            model_sizes[name] = size_str

                for eid, model_name in ollama_models:
                    # Try exact match first, then tag-only match
                    sz = model_sizes.get(model_name)
                    if sz is None:
                        # Try matching just the tag part (before :)
                        tag = model_name.split(":")[0] if ":" in model_name else model_name
                        for k, v in model_sizes.items():
                            if k.startswith(tag + ":"):
                                sz = v
                                break
                    sizes[eid] = sz
            else:
                for eid, _ in ollama_models:
                    sizes[eid] = None
        except Exception:
            for eid, _ in ollama_models:
                sizes[eid] = None

    return {"sizes": sizes}


# ═══════════════════════════════════════════════════════════════════════════
# GET /tournament/providers — list providers for the staging model picker
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/tournament/providers")
async def tournament_providers(
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """Return providers (secret-free) for the tournament staging model picker."""
    reg = _registry(svc)
    return {
        "providers": [p.to_public() for p in reg.list_providers()],
    }


# ═══════════════════════════════════════════════════════════════════════════
# GET /tournament/benchmarks — list benchmarks for a role
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/tournament/benchmarks")
async def tournament_benchmarks(role: str = "big") -> dict[str, Any]:
    """Return the benchmark suite for a cartridge role.

    Args:
        role: "big" or "small" (default "big")
    """
    if role not in ("big", "small"):
        return {"status": "error", "detail": "role must be 'big' or 'small'"}, 400
    benchmarks = get_benchmarks(role)
    return {
        "role": role,
        "count": len(benchmarks),
        "benchmarks": [
            {
                "id": b.id,
                "name": b.name,
                "description": b.description,
                "category": b.category,
            }
            for b in benchmarks
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# POST /tournament/run — run a tournament (sync)
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/tournament/run")
async def tournament_run(
    payload: dict,
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """Run a tournament: pit selected models against the role's benchmarks.

    Body:
      - contestants: list of {"model_id": str, "model_name": str, "provider_id": str}
        These can come from the tournament staging pot OR the main registry pot.
      - role: "big" or "small" (determines which benchmarks to run)

    Returns full tournament results with per-model and per-benchmark scores.
    This is a synchronous endpoint — it blocks until all models are tested.
    For streaming progress, use the WebSocket endpoint.
    """
    contestants: list[dict[str, str]] = payload.get("contestants", [])
    role: str = (payload.get("role") or "big").strip().lower()

    if not contestants:
        return {"status": "error", "detail": "contestants required (non-empty list)"}, 400
    if role not in ("big", "small"):
        return {"status": "error", "detail": "role must be 'big' or 'small'"}, 400

    reg = _registry(svc)

    # The judge is the current big model
    judge_client = svc.ollama_client
    if judge_client is None:
        return {"status": "error",
                "detail": "No big model assigned — a judge is required"}, 500

    try:
        results = await run_tournament(
            contestants=contestants,
            role=role,
            registry=reg,
            judge_client=judge_client,
        )
    except ValueError as e:
        return {"status": "error", "detail": str(e)}, 400
    except Exception as e:
        logger.error("Tournament failed: %s", e)
        return {"status": "error", "detail": str(e)}, 500

    return {
        "status": "ok",
        "role": results.role,
        "judge_model": results.judge_model,
        "duration_s": round(results.finished_at - results.started_at, 1),
        "benchmarks": results.benchmarks,
        "models": [
            {
                "model_id": m.model_id,
                "model_name": m.model_name,
                "provider_id": m.provider_id,
                "passed": m.passed,
                "failed": m.failed,
                "errors": m.errors,
                "total": m.total,
                "overall_score": round(m.overall_score, 3),
                "avg_latency_ms": round(m.avg_latency_ms, 0),
                "combined_score": round(m.combined_score, 3),
                "total_latency_ms": round(m.total_latency_ms, 0),
                "error": m.error,
                "benchmarks": [
                    {
                        "benchmark_id": b.benchmark_id,
                        "benchmark_name": b.benchmark_name,
                        "category": b.category,
                        "passed": b.passed,
                        "score": round(b.score, 2),
                        "latency_ms": round(b.latency_ms, 0),
                        "response": b.response,
                        "judge_reasoning": b.judge_reasoning,
                        "error": b.error,
                    }
                    for b in m.benchmarks
                ],
            }
            for m in results.models
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# WS /tournament/ws — streaming tournament
# ═══════════════════════════════════════════════════════════════════════════

@router.websocket("/tournament/ws")
async def tournament_ws(
    websocket: WebSocket,
    svc: Annotated[Services, Depends(get_services)],
):
    """Run a tournament with streaming progress over WebSocket.

    Client sends a JSON start message:
      {"type": "start", "contestants": [{"model_id","model_name","provider_id"},...], "role": "big"|"small"}

    Server streams progress events:
      {"type": "start", "role": "...", "model_count": N, "benchmark_count": N, ...}
      {"type": "model_start", "model_id": "...", "index": N, "total": N}
      {"type": "model_done", "model_id": "...", "passed": N, "failed": N, ...}
      {"type": "done", "role": "...", "models": [...]}
    """
    await websocket.accept()
    reg = _registry(svc)
    judge_client = svc.ollama_client

    try:
        raw = await websocket.receive_text()
        msg = json.loads(raw)
    except (WebSocketDisconnect, json.JSONDecodeError):
        return

    if msg.get("type") != "start":
        await websocket.send_json({"type": "error", "message": "Expected start message"})
        return

    contestants: list[dict[str, str]] = msg.get("contestants", [])
    role: str = (msg.get("role") or "big").strip().lower()

    if not contestants:
        await websocket.send_json({"type": "error", "message": "contestants required"})
        return
    if role not in ("big", "small"):
        await websocket.send_json({"type": "error", "message": "role must be 'big' or 'small'"})
        return
    if judge_client is None:
        await websocket.send_json({"type": "error", "message": "No big model assigned as judge"})
        return

    try:
        async for event in run_tournament_streaming(
            contestants=contestants,
            role=role,
            registry=reg,
            judge_client=judge_client,
        ):
            try:
                await websocket.send_json(event)
            except WebSocketDisconnect:
                return
    except Exception as e:
        logger.error("Tournament WS error: %s", e)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except WebSocketDisconnect:
            pass
        logger.error("Tournament WS error: %s", e)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except WebSocketDisconnect:
            pass
