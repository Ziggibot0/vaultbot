"""Config endpoint: /config (GET + POST), /update/rollback.

FreeSearch is keyless, so the config surface is informational only (which
engines are up / cooling down). Migrated from main.py.

The /update/rollback endpoint restores backend code files from the latest
timestamp directory in .vaultbot-update-backup/, reversing a failed or
unwanted self-update. The backup is created by the plugin's copyCodeTree
during performSelfUpdate — this endpoint just reverses it.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any

from app_state import get_services
from fastapi import APIRouter, Depends
from services import Services

router = APIRouter()

# The backup directory lives inside vaultbot_backend/ (created by the
# plugin's copyCodeTree during performSelfUpdate). Each timestamp subdir
# contains files backed up before they were overwritten by the update.
_BACKUP_DIR = Path(__file__).resolve().parent.parent / ".vaultbot-update-backup"
_BACKEND_DIR = Path(__file__).resolve().parent.parent


@router.get("/config")
async def get_config(svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """Return the current research-backend configuration + engine health."""
    engines = []
    for b in getattr(svc.search_client, "_backends", []):
        in_cd = b._in_cooldown() if hasattr(b, "_in_cooldown") else False
        rem = b._cooldown_remaining() if hasattr(b, "_cooldown_remaining") else 0.0
        engines.append({
            "name": b.name,
            "in_cooldown": in_cd,
            "cooldown_remaining_s": int(rem),
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


def _find_latest_backup() -> Path | None:
    """Return the newest timestamp directory in .vaultbot-update-backup/.

    Returns None if no backups exist. Each backup is a directory named
    with an ISO timestamp (e.g. ``2026-07-29T18-30-00.000Z``). We sort by
    name (which sorts chronologically since the timestamps are ISO).
    """
    if not _BACKUP_DIR.exists():
        return None
    subdirs = [d for d in _BACKUP_DIR.iterdir() if d.is_dir()]
    if not subdirs:
        return None
    # Sort by directory name (ISO timestamps sort chronologically).
    subdirs.sort(key=lambda d: d.name, reverse=True)
    return subdirs[0]


def _list_backups() -> list[dict[str, str]]:
    """Return a list of all backups (timestamp + file count) for the UI."""
    if not _BACKUP_DIR.exists():
        return []
    result = []
    for d in sorted(_BACKUP_DIR.iterdir(), key=lambda d: d.name, reverse=True):
        if not d.is_dir():
            continue
        # Count files in the backup (recursively).
        count = sum(1 for _ in d.rglob("*") if _.is_file())
        result.append({"timestamp": d.name, "file_count": count})
    return result


@router.get("/update/backups")
async def list_update_backups() -> dict[str, Any]:
    """List available update backups for the rollback UI.

    Returns ``{"backups": [{"timestamp": ..., "file_count": ...}, ...]}``
    sorted newest-first. An empty list means no backups exist (either the
    user has never updated, or the update never changed any files).
    """
    return {"backups": _list_backups()}


@router.post("/update/rollback")
async def rollback_update(
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """Restore backend code files from the latest update backup.

    The plugin's ``copyCodeTree`` backs up any file it's about to overwrite
    to ``.vaultbot-update-backup/<timestamp>/`` before applying an update.
    This endpoint reverses that: it finds the newest backup directory and
    copies its files back into ``vaultbot_backend/``, restoring the
    pre-update code.

    The backup filenames use ``__`` as a path separator (e.g.
    ``routers__system.py``), which ``copyCodeTree`` uses when saving. We
    reverse the ``__`` → ``/`` mapping to reconstruct the relative path.

    After restoring, the backend should be restarted so the old code takes
    effect. The frontend's "Restore last version" button calls this
    endpoint, then triggers a backend restart via the sidebar's Restart
    button (or the WS ``restart`` mechanism).

    Returns:
        ``{"status": "ok", "restored": N, "backup": "timestamp"}`` on success.
        ``{"status": "no_backup"}`` if no backups exist.
        ``{"status": "error", "error": "..."}`` on failure.
    """
    latest = _find_latest_backup()
    if latest is None:
        return {"status": "no_backup", "message": "No backups found."}

    restored = 0
    errors = []
    try:
        for backup_file in latest.rglob("*"):
            if not backup_file.is_file():
                continue
            # Reconstruct the relative path: copyCodeTree replaces / with __
            # in the relPath when naming backup files. Reverse it.
            rel_name = backup_file.name
            rel_path = rel_name.replace("__", "/")
            dest = _BACKEND_DIR / rel_path

            # Safety: only restore files INSIDE vaultbot_backend/. The __ → /
            # reconstruction could theoretically produce a path that escapes
            # (e.g. via .. in the original filename), so we resolve + check.
            resolved_dest = dest.resolve()
            if not str(resolved_dest).startswith(str(_BACKEND_DIR.resolve())):
                errors.append(f"skipped (outside backend): {rel_path}")
                continue

            # Create parent directories if needed.
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, dest)
            restored += 1
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        return {"status": "error", "error": str(e),
                "restored": restored, "backup": latest.name}

    return {
        "status": "ok",
        "restored": restored,
        "backup": latest.name,
        "errors": errors if errors else None,
    }
