"""System endpoints: /, /health, /checkpoints/*, /supervision/nssm.

Migrated from main.py as the first Phase 3 router (simplest — no service
mutation, no websocket state). Handlers read singletons via
``svc: Services = Depends(get_services)`` instead of as free variables.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Annotated, Any

from app_state import get_services
from diagnostics import diagnose_from_message
from error_types import Diagnosis, ProblemCategory, Severity, make_diagnosis
from fastapi import APIRouter, Depends, Request
from services import Services
from supervision import generate_nssm_install, generate_nssm_uninstall

router = APIRouter()

# Strong references to fire-and-forget tasks so they aren't garbage-collected
# mid-flight (RUF006).
_background_tasks: set[asyncio.Task] = set()

# Include sub-routers for extracted endpoints.
from routers.sessions import router as _sessions_router  # noqa: E402
from routers.system_stats import router as _stats_router  # noqa: E402

router.include_router(_sessions_router)
router.include_router(_stats_router)


def _ping_ollama(svc: Services) -> bool:
    """Quick check that the configured LLM backend is responding.

    Uses the client's own is_running() method so it works with ANY backend
    (Ollama, OpenAI-compatible, etc.) — not just local Ollama.

    is_running() now has a 5s timeout so a busy Ollama (loading a model
    during preload) can't hang this call indefinitely.  This is a SYNC
    helper used by _run_diagnose_checks (also sync); the async /health
    endpoint calls the executor directly to avoid blocking the loop.
    """
    try:
        return bool(svc.ollama_client.is_running())
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False


# ─────────────────────────────────────────────────────────────────────────
# Proactive diagnostics — /diagnose and /preflight
# ─────────────────────────────────────────────────────────────────────────
# These two endpoints are the proactive counterpart to ``classify_error``:
# where classify_error reacts to a raised exception, /diagnose and /preflight
# *probe* the environment and return a list of Diagnosis objects so the UI
# can show problems (with remedy hints) BEFORE a user action fails.
#
# - /diagnose  requires the backend to be running (checks live services).
# - /preflight runs without the backend (used by the plugin at first boot
#   to decide whether to show the Finish-setup wizard vs. just start).
# Both return ``{"problems": [diagnosis_dict, ...]}`` so the frontend has
# one render path (``renderProblem``) for both reactive and proactive cases.


def _check_synced_folder(vault_path: str) -> Diagnosis | None:
    """Return a Diagnosis if the vault lives inside a known sync folder.

    Sync services (OneDrive, Dropbox, iCloud, Google Drive) corrupt the
    SQLite + FAISS files VaultBot writes. This used to be a buried README
    footnote; promoting it to a proactive check means the user finds out
    *before* their first chat silently corrupts.
    """
    if not vault_path:
        return None
    p = vault_path.lower().replace("\\", "/")
    # Match on path segments to avoid false positives like "OneDriveBackup".
    sync_markers = (
        "/onedrive/",
        "/dropbox/",
        "/icloud~",
        "/icloud drive/",
        "/google drive/",
        "/googledrive/",
    )
    if any(marker in p for marker in sync_markers):
        return diagnose_from_message(
            "synced folder detected",
            path=vault_path,
        )
    return None


def _check_port_free(port: int) -> Diagnosis | None:
    """Return a Diagnosis if ``port`` is already bound by another process.

    Uses a non-blocking connect attempt — if *we* can connect, something
    else is already listening (and it isn't us, since this runs inside the
    backend, which would mean we're checking our own port; callers should
    pass a *different* port, e.g. the configured one minus us). Kept
    conservative: only flags a clear bind conflict.
    """
    import socket

    try:
        # Try to bind: if it fails, the port is taken.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.bind(("127.0.0.1", port))
        finally:
            s.close()
        return None  # bind succeeded → port is free
    except OSError:
        return diagnose_from_message(
            "address already in use",
            port=port,
        )


def _check_model_present(svc: Services) -> Diagnosis | None:
    """Return a Diagnosis if the configured LLM model isn't available locally.

    Distinguishes "not pulled" (model id is valid but not downloaded) from
    "missing" (model id is malformed / unknown). The remedy differs:
    pull vs. reconfigure. We can only tell the two apart by asking the
    backend for its model list — so this check is /diagnose-only, not
    /preflight (which has no backend).
    """
    model = getattr(svc.ollama_client, "llm_model", "") or ""
    if not model:
        return None  # nothing configured yet — not a failure to surface here
    try:
        available = svc.ollama_client.list_models()
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        # If we can't even list models, that's an ollama_down case the
        # other checks will catch; don't double-report.
        return None
    if model in available:
        return None
    # Heuristic: a non-empty model id that the backend recognizes the shape
    # of (contains a ':' tag separator, like "model-name:latest") is probably
    # just not pulled; a bare/garbage id is "missing".
    if ":" in model and " " not in model:
        return diagnose_from_message(
            f"model '{model}' not found",
            model=model,
        )
    return diagnose_from_message(
        f"model '{model}' does not exist",
        model=model,
    )


@router.get("/diagnose")
async def diagnose(
    svc: Annotated[Services, Depends(get_services)],
    request: Request,
) -> dict[str, Any]:
    """Run the proactive check battery and return user-facing problems.

    Returns ``{"problems": [diagnosis_dict, ...]}`` where each diagnosis
    is ready to render via the frontend's ``renderProblem``. An empty list
    means everything looks healthy. This is what the sidebar's Diagnose
    button calls, and what Restart auto-runs on failure.

    Checks are intentionally cheap (sub-100ms each) and side-effect free
    so it's safe to call on every startup or on a manual button press.
    """
    return {"problems": [d.to_dict() for d in _run_diagnose_checks(svc)]}


def _run_diagnose_checks(svc: Services) -> list[Diagnosis]:
    """Pure check battery — shared by /diagnose and the /diagnose command.

    Synchronous + side-effect free so it can be called from the WS
    command path without spinning up a fake Request. The async endpoint
    just wraps it for FastAPI.
    """
    problems: list[Diagnosis] = []

    # 1) Ollama / LLM backend reachable?
    if not _ping_ollama(svc):
        # Build the diagnosis directly so we control the endpoint name
        # shown to the user (the configured backend, not a hardcoded
        # "Ollama" string — works for cloud backends too).
        backend = "Ollama"
        try:
            host = getattr(svc.ollama_client, "base_url", "") or ""
            if host and "11434" not in host:
                backend = "the LLM backend"
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass
        problems.append(
            diagnose_from_message(
                "connection refused",
                stage="starting",
                endpoint=backend,
            )
        )

    # 2) Configured model actually available?
    model_diag = _check_model_present(svc)
    if model_diag is not None:
        problems.append(model_diag)

    # 3) Vault not inside a sync folder?
    vault_path = ""
    with contextlib.suppress(Exception):
        # The vault root is 4 levels up from routers/
        # (vaultbot/vaultbot_backend/routers/ -> vault root)
        vault_path = str(
            Path(__file__).resolve().parent.parent.parent
        )  # vault root (3 levels up from vaultbot/vaultbot_backend/routers/)
    sync_diag = _check_synced_folder(vault_path)
    if sync_diag is not None:
        problems.append(sync_diag)

    # 4) Index healthy (FAISS loaded)? A missing/ABI-broken index surfaces
    #    as the faiss_abi category so the remedy points at repair, not
    #    "something went wrong."
    try:
        _ = svc.vault_indexer.index.ntotal
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        problems.append(
            diagnose_from_message(
                f"faiss error: {e}",
            )
        )

    # 5) LLM backend misconfigured? If the user set LLM_BACKEND=openai but
    #    didn't provide an API key/model, the backend fell back to Ollama
    #    at startup (so it always starts). Surface this so the user knows
    #    their cloud model isn't being used and can fix .env. This is the
    #    "backend starts but chat uses the wrong model" silent failure.
    try:
        import os as _os

        _configured = (_os.getenv("LLM_BACKEND") or "").strip().lower()
        _api_key = (_os.getenv("LLM_API_KEY") or "").strip()
        _model = (_os.getenv("LLM_MODEL") or "").strip()
        _actual_url = getattr(svc.ollama_client, "base_url", "") or ""
        # If configured for openai but the client is actually Ollama
        # (base_url points at localhost:11434), the fallback fired.
        if _configured == "openai" and "11434" in _actual_url:
            problems.append(
                make_diagnosis(
                    ProblemCategory.CONFIG_CONFLICT,
                    user_message=(
                        "You set LLM_BACKEND=openai in .env but didn't provide "
                        "an API key or model. VaultBot fell back to local Ollama "
                        "so it could still start. Add your LLM_API_KEY and "
                        "LLM_MODEL to .env (or set LLM_BACKEND=ollama to stop "
                        "this message)."
                    ),
                    remedy_hint=(
                        "Edit .env: set LLM_API_KEY=sk-... and "
                        "LLM_MODEL=gpt-4o-mini (or your provider's model id). "
                        "Then click Restart."
                    ),
                    action="open_settings",
                    severity=Severity.INFO,
                )
            )
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass

    return problems


# --- /sessions: conversation history list --------------------------------
# A lightweight listing of past chat sessions (one per .jsonl in sessions/)
# so the sidebar's History disclosure can show "what would I lose if I
# /new?" without the user having to find the sessions/ folder. Each entry
# carries the session id, a human-readable start time, and a one-line
# preview (the first user message) so the list is scannable. Read-only —
# this never modifies or deletes session files.
#
# ENDPOINTS MOVED to routers/sessions.py (list_sessions, get_session,
# _extract_session_preview). Kept here as a comment for navigation.


@router.get("/preflight")
async def preflight(request: Request) -> dict[str, Any]:
    """No-backend-required environment check, used at first boot.

    Unlike /diagnose, this runs before the backend is up (the plugin calls
    it during onload to decide whether to show the Finish-setup wizard).
    It can only check things that don't need the running backend: Python
    presence, Ollama presence, sync folder, and port availability. Model
    availability is deferred to /diagnose.
    """
    problems: list[Diagnosis] = []

    # Vault root = 4 levels up from routers/
    # (vaultbot/vaultbot_backend/routers/ -> vault root)
    vault_path = str(
        Path(__file__).resolve().parent.parent.parent
    )  # vault root (3 levels up from vaultbot/vaultbot_backend/routers/)
    sync_diag = _check_synced_folder(vault_path)
    if sync_diag is not None:
        problems.append(sync_diag)

    # Python + Ollama presence: shell out to --version. Missing either is
    # a setup_incomplete diagnosis so the wizard offers download buttons.
    from subprocess_utils import run as _subprocess_run

    for tool, label in (("python", "Python"), ("ollama", "Ollama")):
        present = False
        try:
            result = _subprocess_run(
                [tool, "--version"],
                capture_output=True,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
            present = result.returncode == 0
        except (FileNotFoundError, OSError):
            present = False
        if not present:
            problems.append(
                diagnose_from_message(
                    "setup incomplete",
                    missing=label,
                )
            )

    # Port 8000 free? (Only flag if *something else* holds it — we can't
    # be holding it during preflight since the backend isn't up yet.)
    port_diag = _check_port_free(8000)
    if port_diag is not None:
        problems.append(port_diag)

    return {"problems": [d.to_dict() for d in problems]}


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

    The ollama ping runs in the executor with a 3s timeout so a busy
    Ollama (loading a model during preload) never freezes the event loop.
    """
    import asyncio as _asyncio

    loop = _asyncio.get_event_loop()
    try:
        ollama_ok = await _asyncio.wait_for(
            loop.run_in_executor(None, svc.ollama_client.is_running),
            timeout=3.0,
        )
    except TimeoutError:
        ollama_ok = False  # Ollama is busy — don't block the health check
    except Exception:  # noqa: BLE001
        ollama_ok = False
    extra = {
        "ollama": ollama_ok,
        "autonomous_enabled": svc.autonomous_researcher.enabled,
        "autonomous_running": bool(
            svc.autonomous_researcher._thread
            and svc.autonomous_researcher._thread.is_alive()
        ),
        "index_vectors": svc.vault_indexer.index.ntotal
        if svc.vault_indexer.index
        else 0,
        "graph_nodes": len(svc.vault_graph.nodes),
    }
    result = svc.health_monitor.health(extra=extra)
    # If the researcher thread is alive but the heartbeat is stale for
    # more than 3x the cycle interval, the researcher is likely stuck
    # in a long operation (web request, LLM call) or hung. Surface this
    # so the operator knows the researcher isn't actually making progress.
    if extra["autonomous_running"] and not result["ok"]:
        interval = svc.autonomous_researcher.interval_seconds
        if result.get("last_heartbeat_age_s", 0) > interval * 3:
            result["researcher_stuck"] = True
            result["researcher_stuck_reason"] = (
                f"heartbeat stale for {result['last_heartbeat_age_s']}s "
                f"(cycle interval: {interval}s)"
            )
    return result


@router.get("/ollama/stats")
async def ollama_stats(
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """Return Ollama runtime stats for the plugin's status bar.

    Combines /api/ps (loaded models, VRAM, context length, expiry) and
    /api/version into a single snapshot.  For cloud backends (OpenAI-
    compatible), returns a minimal stub since there's no local GPU to
    report.  Never raises — best-effort so a stats fetch failure never
    blocks the UI.

    Runs in the executor with a 5s timeout — get_ollama_stats() does
    blocking HTTP calls to Ollama (/api/ps, /api/version) and a busy
    Ollama (loading a model during preload) would freeze the event loop.
    """
    import asyncio as _asyncio

    try:
        loop = _asyncio.get_event_loop()
        return await _asyncio.wait_for(
            loop.run_in_executor(None, svc.ollama_client.get_ollama_stats),
            timeout=5.0,
        )
    except TimeoutError:
        return {
            "running": False,
            "version": None,
            "models": [],
            "error": "Ollama busy (timed out)",
        }
    except Exception as e:  # noqa: BLE001 — best-effort
        return {"running": False, "version": None, "models": [], "error": str(e)}


# --- /system/stats: hardware resource meters -----------------------------
# MOVED to routers/system_stats.py (_cpu_stats, _ram_stats, _gpu_stats,
# _npu_stats, _disk_io, _net_io, system_stats endpoint).


@router.post("/restart")
async def restart_endpoint(svc: Annotated[Services, Depends(get_services)]):
    """Ask the Obsidian plugin to restart the backend via WebSocket.

    Broadcasts ``{"type": "restart"}`` to all connected WebSocket clients
    after a short delay. The plugin's message handler calls
    ``restartBackend()`` — the exact same code path as the GUI restart
    button. The plugin then calls ``/shutdown`` and spawns a fresh backend
    process.

    DELAYED BROADCAST: When the agent calls this endpoint via the
    backend_restart tool, the HTTP response must return to the chat loop
    BEFORE the plugin kills the backend. If we broadcast immediately, the
    plugin calls stopBackend() while the chat handler is still mid-iteration
    — the tool result never reaches the LLM, the MCP client loses
    connection, and the session dies dead in the water. The 3-second delay
    gives the chat loop time to process the tool result, let the LLM
    generate a final message, and send it to the user before the backend
    gets killed.
    """

    async def _delayed_broadcast():
        await asyncio.sleep(3)
        await svc.manager.broadcast(
            json.dumps(
                {
                    "type": "restart",
                    "content": (
                        "Backend is restarting. This is the same code path "
                        "as the restart button."
                    ),
                }
            ),
            session_logger=svc.session_logger,
        )

    _restart_task = asyncio.create_task(_delayed_broadcast())
    _background_tasks.add(_restart_task)
    _restart_task.add_done_callback(_background_tasks.discard)
    return {
        "status": "restart_requested",
        "message": (
            "Restart scheduled in 3 seconds. Chat loop will finish first, "
            "then plugin will restart the backend."
        ),
    }


@router.post("/reload-plugin")
async def reload_plugin_endpoint(svc: Annotated[Services, Depends(get_services)]):
    """Ask the Obsidian plugin to reload itself via WebSocket.

    Broadcasts ``{"type": "reload_plugin"}`` to all connected WebSocket
    clients. The plugin's message handler calls ``reloadSelf()`` which
    disables and re-enables the plugin via Obsidian's plugin API
    (``app.plugins.disablePlugin`` + ``app.plugins.enablePlugin``).

    Unlike ``/restart``, the backend stays running during the reload —
    ``onunload()`` checks ``_isReloading`` and skips ``stopBackend()``.
    The new plugin instance reconnects to the existing backend.

    This lets the agent pick up changes to ``main.js`` / ``styles.css``
    without the operator having to manually toggle the plugin in Settings.
    """
    await svc.manager.broadcast(
        json.dumps(
            {
                "type": "reload_plugin",
                "content": (
                    "Plugin reload requested. The plugin will disable "
                    "and re-enable itself."
                ),
            }
        ),
        session_logger=svc.session_logger,
    )
    return {
        "status": "reload_requested",
        "message": (
            "WebSocket broadcast sent. Plugin will reload itself "
            "(disable + re-enable). Backend stays running."
        ),
    }


# --- /checkpoints: crash-recovery status --------------------------------


@router.get("/checkpoints")
async def checkpoint_status(
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
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
            None, svc.checkpointer.recover, svc.autonomous_researcher
        )
        return recovery
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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


# --- /broadcast_questionnaire: ask_user tool -> plugin bridge -----------


@router.post("/broadcast_questionnaire")
async def broadcast_questionnaire(
    request: Request,
    svc: Annotated[Services, Depends(get_services)],
):
    """Receive a questionnaire from the ask_user tool and send it over
    WebSocket to the owning tab.  The plugin renders interactive question
    cards; the user's answers come back via POST /user_response.

    When the ask_user tool stored a websocket reference in
    ``_pending_requests`` (multi-tab isolation), the questionnaire is sent
    to THAT websocket only.  Fallback: broadcast to all connected clients
    (legacy behavior when no websocket ref is available).
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    request_id = payload.get("request_id", "")
    if not request_id:
        return {"status": "error", "message": "Missing request_id"}

    # Look up the owning websocket from the ask_user registry.
    try:
        from custom_tools.ask_user import _pending_requests
    except ImportError:
        _pending_requests = {}

    entry = _pending_requests.get(request_id)
    ws_ref = entry[2] if entry and len(entry) >= 3 else None

    if ws_ref is not None:
        # Send to the owning tab only.
        await svc.manager.send_personal_message(
            json.dumps(payload), ws_ref, session_logger=svc.session_logger
        )
    else:
        # Fallback: broadcast to all tabs (legacy behavior).
        await svc.manager.broadcast(
            json.dumps(payload),
            session_logger=svc.session_logger,
        )
    return {"status": "ok", "request_id": request_id}


# --- /user_response: plugin -> ask_user tool bridge ---------------------


@router.post("/user_response")
async def user_response_endpoint(request: Request):
    """Receive the user's answers from the plugin and unblock the waiting
    ask_user tool. The plugin sends the request_id + answers dict; this
    endpoint finds the waiting thread and signals it.
    """
    import time as _time

    _debug_log = Path(__file__).resolve().parent / "ask_user_debug.log"

    def _dbg(msg):
        with open(_debug_log, "a", encoding="utf-8") as _f:
            _f.write(f"{_time.strftime('%H:%M:%S')} {msg}\n")

    _dbg("POST /user_response received")
    try:
        payload = await request.json()
    except Exception as e:
        _dbg(f"invalid JSON: {e}")
        return {"status": "error", "message": "Invalid JSON"}

    request_id = payload.get("request_id", "")
    _dbg(f"request_id={request_id}")
    if not request_id:
        _dbg(f"missing request_id in payload: {payload}")
        return {"status": "error", "message": "Missing request_id"}

    # Import the pending-requests registry from the ask_user tool.
    try:
        from custom_tools.ask_user import _pending_requests
    except ImportError:
        _dbg("ask_user tool not loaded - ImportError")
        return {"status": "error", "message": "ask_user tool not loaded"}

    _dbg(f"pending_keys={list(_pending_requests.keys())}")
    entry = _pending_requests.get(request_id)
    if entry is None:
        _dbg(f"request_id {request_id} NOT in pending_requests")
        return {
            "status": "error",
            "message": f"No pending request with id {request_id}",
        }

    event, response_holder = entry[0], entry[1]
    # Copy the user's answers into the response holder.
    answers = payload.get("answers", {})
    comments = payload.get("comments", "")
    response_holder.clear()
    response_holder.update(answers)
    if comments:
        response_holder["_comments"] = comments
    event.set()

    return {"status": "ok", "request_id": request_id}
