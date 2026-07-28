"""System endpoints: /, /health, /checkpoints/*, /supervision/nssm.

Migrated from main.py as the first Phase 3 router (simplest — no service
mutation, no websocket state). Handlers read singletons via
``svc: Services = Depends(get_services)`` instead of as free variables.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app_state import get_services
from services import Services
from session_logger import SessionLogger
from supervision import generate_nssm_install, generate_nssm_uninstall

router = APIRouter()


def _ping_ollama(svc: Services) -> bool:
    """Quick check that the configured LLM backend is responding.

    Uses the client's own is_running() method so it works with ANY backend
    (Ollama, OpenAI-compatible, etc.) — not just local Ollama.
    """
    try:
        return bool(svc.ollama_client.is_running())
    except Exception:
        return False


@router.get("/")
async def root(svc: Annotated[Services, Depends(get_services)]) -> dict[str, str]:
    # Lightweight marker that the backend is up. The plugin's startBackend
    # probe hits this.
    return {"status": "VaultBot Backend is running"}


@router.get("/health")
async def health(svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """Liveness check. Returns uptime, heartbeat age, current task, and
    dependency status so a watchdog (or the Obsidian plugin) can detect hangs
    and restart if needed. Keep this <50ms.
    """
    extra = {
        "ollama": _ping_ollama(svc),
        "autonomous_enabled": svc.autonomous_researcher.enabled,
        "autonomous_running": bool(svc.autonomous_researcher._thread and
                                   svc.autonomous_researcher._thread.is_alive()),
        "index_vectors": svc.vault_indexer.index.ntotal if svc.vault_indexer.index else 0,
        "graph_nodes": len(svc.vault_graph.nodes),
        "identity_self_model_chars": len(svc.identity.get_self_model()),
    }
    return svc.health_monitor.health(extra=extra)


@router.post("/restart")
async def restart_endpoint(svc: Annotated[Services, Depends(get_services)]):
    """Ask the Obsidian plugin to restart the backend via WebSocket.

    Broadcasts ``{"type": "restart"}`` to all connected WebSocket clients.
    The plugin's message handler calls ``restartBackend()`` — the exact same
    code path as the GUI restart button. The plugin then calls ``/shutdown``
    and spawns a fresh backend process.

    This is the clean way for the agent to self-restart: the plugin (a
    separate process that survives the backend dying) handles the actual
    shutdown + respawn, not a fragile batch script.
    """
    await svc.manager.broadcast(json.dumps({
        "type": "restart",
        "content": "Backend is restarting. This is the same code path as the restart button."
    }), session_logger=svc.session_logger)
    return {"status": "restart_requested", "message": "WebSocket broadcast sent. Plugin will restart the backend."}


# --- /checkpoints: crash-recovery status --------------------------------

@router.get("/checkpoints")
async def checkpoint_status(svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """Return the autonomous researcher's checkpoint state so the UI can
    show whether there's interrupted work to resume after a crash.
    """
    return svc.checkpointer.summary()


@router.post("/checkpoints/recover")
async def recover_checkpoints(svc: Annotated[Services, Depends(get_services)]):
    """Manually trigger recovery of any interrupted research work."""
    try:
        loop = asyncio.get_event_loop()
        recovery = await loop.run_in_executor(
            None, svc.checkpointer.recover, svc.autonomous_researcher)
        return recovery
    except Exception as e:
        return {"error": str(e)}, 500


@router.get("/supervision/nssm")
async def nssm_install_script():
    """Return the nssm install commands so the user can install VaultBot as a
    Windows service that starts on boot, restarts on crash, and rotates logs.
    Run the output in an admin terminal to install.
    """
    vaultbot_dir = str(Path(__file__).parent.parent.resolve())
    python_exe = str(Path(sys.executable).resolve())
    log_dir = str(Path(vaultbot_dir).parent / "logs")
    return {
        "install": generate_nssm_install(vaultbot_dir, python_exe, log_dir),
        "uninstall": generate_nssm_uninstall(),
        "instructions": (
            "1. Install nssm: https://nssm.cc/download\n"
            "2. Open an admin terminal\n"
            "3. Paste the install commands\n"
            "4. VaultBot will start on boot, restart on crash, and run for days.\n"
            "5. Logs rotate at 10MB in: " + log_dir
        ),
    }