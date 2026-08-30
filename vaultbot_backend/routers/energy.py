"""Energy estimate profiles and historical report endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from app_state import get_services
from energy_profiles import EnergyProfileStore
from energy_report import build_energy_report
from fastapi import APIRouter, Depends, HTTPException, Query
from providers import ProviderRegistry
from services import Services

router = APIRouter()


def _registry(svc: Services) -> ProviderRegistry:
    registry = getattr(svc, "registry", None)
    if registry is None:
        registry = ProviderRegistry.migrate_from_env()
        svc.registry = registry
    return registry


def _public_profiles(
    store: EnergyProfileStore, registry: ProviderRegistry
) -> list[dict[str, Any]]:
    profiles = store.load()
    model_ids = set(profiles)
    model_ids.update(model.id for model in registry.list_models())
    result = []
    for model_id in sorted(model_ids):
        model = registry.get_model(model_id)
        provider = registry.get_provider(model.provider) if model else None
        result.append(
            {
                "model_id": model_id,
                "model": model.model if model else model_id,
                "label": (model.label or model.model) if model else model_id,
                "provider_id": model.provider if model else "",
                "provider_label": provider.label if provider else "",
                "roles": [
                    role
                    for role in ("big", "small", "vision")
                    if registry.get_role(role) == model_id
                ],
                "profile": profiles.get(model_id),
            }
        )
    return result


@router.get("/energy/profiles")
async def list_energy_profiles(
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    registry = _registry(svc)
    try:
        return {"profiles": _public_profiles(EnergyProfileStore(), registry)}
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/energy/profiles/{model_id:path}")
async def set_energy_profile(
    model_id: str,
    payload: dict[str, Any],
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    try:
        profile = EnergyProfileStore().set(model_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "model_id": model_id, "profile": profile}


@router.delete("/energy/profiles/{model_id:path}")
async def delete_energy_profile(
    model_id: str,
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    try:
        deleted = EnergyProfileStore().delete(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "ok" if deleted else "not_found", "model_id": model_id}


@router.get("/energy/report")
async def energy_report(
    svc: Annotated[Services, Depends(get_services)],
    days: Annotated[int, Query(ge=1, le=3650)] = 30,
) -> dict[str, Any]:
    registry = _registry(svc)
    try:
        profiles = EnergyProfileStore().load()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    sessions_dir = svc.session_logger.log_dir
    big_model_id = registry.get_role("big")
    return await asyncio.to_thread(
        build_energy_report,
        sessions_dir,
        profiles,
        big_model_id,
        days=days,
    )
