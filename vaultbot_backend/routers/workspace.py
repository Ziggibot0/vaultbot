"""Active development workspace endpoints."""

from __future__ import annotations

import re
import subprocess
from typing import Any

from custom_tools.gh_client import GhError, gh_api, gh_available
from fastapi import APIRouter, HTTPException
from paths import FRAMEWORK_ROOT
from workspace import WorkspaceError, workspace_registry

router = APIRouter(prefix="/workspace", tags=["workspace"])

_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MANAGED_ROOT = FRAMEWORK_ROOT / ".vaultbot-workspaces"
_SIGN_IN_REQUIRED = (
    "Sign in to GitHub in VaultBot settings before choosing a repository."
)
_MANAGED_CHECKOUT_EXISTS = (
    "A managed checkout already exists; select its local folder instead."
)


def _status_payload() -> dict[str, Any]:
    try:
        workspace = workspace_registry.get()
    except WorkspaceError as exc:
        return {"status": "invalid", "workspace": None, "error": str(exc)}
    return {
        "status": "selected" if workspace else "disconnected",
        "workspace": workspace.to_dict() if workspace else None,
    }


@router.get("")
async def workspace_status() -> dict[str, Any]:
    return _status_payload()


@router.post("/select")
async def select_local_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    local_root = str(payload.get("local_root") or "").strip()
    if not local_root:
        raise HTTPException(status_code=400, detail="local_root is required")
    try:
        selected = workspace_registry.select(local_root)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "selected", "workspace": selected.to_dict()}


@router.delete("")
async def disconnect_workspace() -> dict[str, Any]:
    try:
        workspace_registry.disconnect()
    except WorkspaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "disconnected", "workspace": None}


@router.get("/repositories")
async def list_github_repositories() -> dict[str, Any]:
    if not gh_available():
        raise HTTPException(status_code=409, detail=_SIGN_IN_REQUIRED)
    try:
        repositories = gh_api(
            "GET",
            "user/repos?affiliation=owner,collaborator,organization_member"
            "&sort=updated&direction=desc&per_page=100",
            timeout=30,
        )
    except GhError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "repositories": [
            {
                "full_name": item.get("full_name", ""),
                "private": bool(item.get("private")),
                "default_branch": item.get("default_branch") or "main",
                "clone_url": item.get("clone_url", ""),
            }
            for item in repositories
            if isinstance(item, dict) and item.get("full_name")
        ]
    }


@router.post("/clone")
async def clone_github_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    full_name = str(payload.get("full_name") or "").strip()
    if not _REPOSITORY_NAME.fullmatch(full_name):
        raise HTTPException(
            status_code=400, detail="full_name must be owner/repository"
        )
    if not gh_available():
        raise HTTPException(status_code=409, detail="GitHub sign-in is required")

    owner, repository = full_name.split("/", 1)
    destination = (_MANAGED_ROOT / owner / repository).resolve()
    try:
        destination.relative_to(_MANAGED_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid repository destination"
        ) from exc
    if destination.exists():
        raise HTTPException(status_code=409, detail=_MANAGED_CHECKOUT_EXISTS)

    destination.parent.mkdir(parents=True, exist_ok=True)
    clone_url = f"https://github.com/{owner}/{repository}.git"
    try:
        result = subprocess.run(
            ["git", "clone", "--", clone_url, str(destination)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=502, detail=f"Clone failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HTTPException(status_code=502, detail=f"Clone failed: {detail}")
    try:
        selected = workspace_registry.select(destination, managed_clone=True)
    except WorkspaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "selected", "workspace": selected.to_dict()}
