"""Active development workspace endpoints."""

from __future__ import annotations

import hashlib
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
_WORKSPACE_UNAVAILABLE = (
    "The selected workspace is unavailable. Disconnect and reselect it."
)


def _status_payload() -> dict[str, Any]:
    try:
        workspace = workspace_registry.get()
    except WorkspaceError:
        return {
            "status": "invalid",
            "workspace": None,
            "error": _WORKSPACE_UNAVAILABLE,
        }
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
        raise HTTPException(
            status_code=400,
            detail="Could not select that Git repository.",
        ) from exc
    return {"status": "selected", "workspace": selected.to_dict()}


@router.delete("")
async def disconnect_workspace() -> dict[str, Any]:
    try:
        workspace_registry.disconnect()
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=500, detail="Could not disconnect the workspace."
        ) from exc
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
        raise HTTPException(
            status_code=502, detail="Could not list GitHub repositories."
        ) from exc
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
    identity_parts = full_name.split("/", 1)
    if (
        not _REPOSITORY_NAME.fullmatch(full_name)
        or len(identity_parts) != 2
        or any(part in {".", ".."} for part in identity_parts)
    ):
        raise HTTPException(
            status_code=400, detail="full_name must be owner/repository"
        )
    if not gh_available():
        raise HTTPException(status_code=409, detail="GitHub sign-in is required")

    owner, repository = identity_parts
    workspace_id = hashlib.sha256(full_name.casefold().encode()).hexdigest()[:24]
    destination = _MANAGED_ROOT / workspace_id
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
        raise HTTPException(status_code=502, detail="Repository clone failed.") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=502, detail="Repository clone failed.")
    try:
        selected = workspace_registry.select(destination, managed_clone=True)
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=500, detail="The cloned repository could not be selected."
        ) from exc
    return {"status": "selected", "workspace": selected.to_dict()}
