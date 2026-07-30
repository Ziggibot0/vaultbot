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

from app_state import get_services
from diagnostics import diagnose_from_message
from error_types import Diagnosis, ProblemCategory, Severity, make_diagnosis
from fastapi import APIRouter, Depends, Request
from services import Services
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
        "/onedrive/", "/dropbox/", "/icloud~", "/icloud drive/",
        "/google drive/", "/googledrive/",
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
    except Exception:
        # If we can't even list models, that's an ollama_down case the
        # other checks will catch; don't double-report.
        return None
    if model in available:
        return None
    # Heuristic: a non-empty model id that the backend recognizes the shape
    # of (contains a ':' tag separator, like "qwen3.6:latest") is probably
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
        except Exception:
            pass
        problems.append(diagnose_from_message(
            "connection refused",
            stage="starting",
            endpoint=backend,
        ))

    # 2) Configured model actually available?
    model_diag = _check_model_present(svc)
    if model_diag is not None:
        problems.append(model_diag)

    # 3) Vault not inside a sync folder?
    vault_path = ""
    try:
        # The vault root is the parent of vaultbot_backend/.
        vault_path = str(Path(__file__).resolve().parent.parent)
    except Exception:
        pass
    sync_diag = _check_synced_folder(vault_path)
    if sync_diag is not None:
        problems.append(sync_diag)

    # 4) Index healthy (FAISS loaded)? A missing/ABI-broken index surfaces
    #    as the faiss_abi category so the remedy points at repair, not
    #    "something went wrong."
    try:
        _ = svc.vault_indexer.index.ntotal
    except Exception as e:
        problems.append(diagnose_from_message(
            f"faiss error: {e}",
        ))

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
            problems.append(make_diagnosis(
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
            ))
    except Exception:
        pass

    return problems


# --- /sessions: conversation history list --------------------------------
# A lightweight listing of past chat sessions (one per .jsonl in sessions/)
# so the sidebar's History disclosure can show "what would I lose if I
# /new?" without the user having to find the sessions/ folder. Each entry
# carries the session id, a human-readable start time, and a one-line
# preview (the first user message) so the list is scannable. Read-only —
# this never modifies or deletes session files.

_SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


def _extract_session_preview(path: Path) -> dict[str, Any] | None:
    """Read the first + last lines of a session .jsonl for a list entry.

    Returns ``None`` if the file can't be parsed (corrupt/empty). Keeps the
    endpoint resilient: one bad session file never breaks the whole list.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    import json as _json
    session_id = path.stem
    started_at = ""
    preview = ""
    # Scan the whole file for the session_start + first user message.
    # The user message ("in" event) can be hundreds of lines in (after
    # boot-time tool calls + init), so we can't just read the first 20.
    # We break early once we have both started_at + preview to stay fast.
    for line in lines:
        if started_at and preview:
            break
        try:
            evt = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if not started_at and evt.get("event") == "session_start":
            started_at = evt.get("started_at", "")
            continue
        if not preview:
            data = evt.get("data") or {}
            if evt.get("event") == "websocket_message":
                d = data or {}
                if d.get("direction") == "in":
                    payload = d.get("payload") or {}
                    msg = payload.get("message") or ""
                    if msg:
                        preview = msg[:120]
            elif evt.get("event") == "chat_begin":
                msg = (data.get("user_message") or "")
                if msg:
                    preview = msg[:120]
    return {
        "session_id": session_id,
        "started_at": started_at,
        "preview": preview or "(no messages)",
    }


@router.get("/sessions")
async def list_sessions() -> dict[str, Any]:
    """List recent chat sessions for the sidebar History disclosure.

    Returns ``{"sessions": [{"session_id", "started_at", "preview"}, ...]}``
    sorted newest-first. Reads the sessions/ directory; each .jsonl is one
    session. Only the first ~20 lines of each file are read (for the start
    time + first user message) so this stays fast even with hundreds of
    sessions. Corrupt files are silently skipped.
    """
    if not _SESSIONS_DIR.exists():
        return {"sessions": []}
    entries = []
    for f in _SESSIONS_DIR.glob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0.0
        preview = _extract_session_preview(f)
        if preview is None:
            continue
        preview["mtime"] = mtime
        entries.append(preview)
    # Sort newest-first by file mtime (more reliable than started_at string
    # parse, which can be empty for old sessions).
    entries.sort(key=lambda e: e.get("mtime", 0.0), reverse=True)
    # Drop the mtime from the response payload — it was just for sorting.
    for e in entries:
        e.pop("mtime", None)
    # Cap at 50 so the list stays scannable; the user almost never needs
    # older sessions in the sidebar.
    return {"sessions": entries[:50]}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """Return the user/assistant turns of one session, for read-only replay.

    Parses the .jsonl session log and extracts the message turns in order:
    user messages (event "in" with a payload message, or "chat_begin" with
    user_message) and assistant final answers (event "chat_end" or any
    event carrying a finalized assistant content). Returns ``{"turns":
    [{"role": "user"|"assistant", "content": "..."}]}``. Read-only — this
    never modifies the session file or the live conversation.

    The ``session_id`` is validated as a bare UUID stem (no path separators)
    to prevent path traversal outside sessions/.
    """
    # Validate: only allow UUID-like characters so a crafted id can't
    # escape the sessions/ directory (e.g. "../../etc/passwd").
    import re as _re
    if not _re.fullmatch(r"[0-9a-fA-F-]{36}", session_id):
        return {"turns": [], "error": "invalid session id"}
    path = _SESSIONS_DIR / f"{session_id}.jsonl"
    if not path.exists():
        return {"turns": [], "error": "session not found"}
    import json as _json
    turns: list[dict[str, str]] = []
    # Accumulate outgoing answer_chunk events into the current assistant
    # turn; a new incoming message starts a new user turn. This mirrors the
    # WS streaming protocol: user → answer_chunks → user → answer_chunks.
    current_assistant = ""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                evt = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if evt.get("event") != "websocket_message":
                continue
            data = evt.get("data") or {}
            payload = data.get("payload") or {}
            direction = data.get("direction")
            if direction == "in":
                # Flush any accumulated assistant text before the new user turn.
                if current_assistant:
                    turns.append({"role": "assistant", "content": current_assistant})
                    current_assistant = ""
                msg = payload.get("message") or ""
                if msg:
                    turns.append({"role": "user", "content": msg})
            elif direction == "out":
                ptype = payload.get("type")
                if ptype == "answer_chunk":
                    current_assistant += payload.get("content") or ""
                elif ptype == "answer_done":
                    if current_assistant:
                        turns.append({"role": "assistant", "content": current_assistant})
                        current_assistant = ""
        # Flush trailing assistant text if the session ended mid-stream.
        if current_assistant:
            turns.append({"role": "assistant", "content": current_assistant})
    except OSError:
        return {"turns": [], "error": "could not read session"}
    return {"turns": turns}


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

    # Vault root = parent of vaultbot_backend/ (where this file lives).
    vault_path = str(Path(__file__).resolve().parent.parent)
    sync_diag = _check_synced_folder(vault_path)
    if sync_diag is not None:
        problems.append(sync_diag)

    # Python + Ollama presence: shell out to --version. Missing either is
    # a setup_incomplete diagnosis so the wizard offers download buttons.
    import subprocess
    for tool, label in (("python", "Python"), ("ollama", "Ollama")):
        present = False
        try:
            result = subprocess.run(
                [tool, "--version"],
                capture_output=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            present = result.returncode == 0
        except (FileNotFoundError, OSError):
            present = False
        if not present:
            problems.append(diagnose_from_message(
                "setup incomplete",
                missing=label,
            ))

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
    without Sean having to manually toggle the plugin in Settings.
    """
    await svc.manager.broadcast(json.dumps({
        "type": "reload_plugin",
        "content": "Plugin reload requested. The plugin will disable and re-enable itself."
    }), session_logger=svc.session_logger)
    return {
        "status": "reload_requested",
        "message": "WebSocket broadcast sent. Plugin will reload itself (disable + re-enable). Backend stays running."
    }


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
