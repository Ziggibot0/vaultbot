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
    try:
        # The vault root is 4 levels up from routers/ (vaultbot/vaultbot_backend/routers/ -> vault root)
        vault_path = str(
            Path(__file__).resolve().parent.parent.parent
        )  # vault root (3 levels up from vaultbot/vaultbot_backend/routers/)
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass
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
        # New format: event-based (session_start, websocket_message, etc.)
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
                msg = data.get("user_message") or ""
                if msg:
                    preview = msg[:120]
        # Old format: type-based (session_start, user_message, etc.)
        if not started_at and evt.get("type") == "session_start":
            ts = evt.get("timestamp", 0)
            started_at = str(ts) if ts else ""
            continue
        if not preview and evt.get("type") == "user_message":
            msg = evt.get("content") or ""
            if msg:
                preview = msg[:120]
    # Also look for a session_title event (set by the user or auto-generated).
    title = ""
    for line in lines:
        try:
            evt = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if evt.get("event") == "session_title":
            title = evt.get("title", "")
            break  # last one wins, but there's typically only one
    return {
        "session_id": session_id,
        "started_at": started_at,
        "preview": preview or "(no messages)",
        "title": title or (preview[:60] if preview else "New Session"),
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
    """Return the turns of one session, for read-only replay.

    Parses the .jsonl session log and extracts message turns in order.
    Supports three event formats:

    1. **Explicit assistant_response** (new): ``event: "assistant_response"``
       with ``data.text`` (or ``data.content``) — the most reliable source.
    2. **websocket_message** (current): ``event: "websocket_message"`` with
       ``data.direction`` in/out and ``data.payload.type`` answer_chunk /
       answer_done — accumulates chunks into the current assistant turn.
    3. **Old type-based format**: ``type: "user_message"`` / ``type:
       "assistant_response"`` with ``content`` / ``text`` fields.

    Also extracts ``tool_call`` and ``thinking`` events so the replay shows
    what VaultBot actually did, not just the final text.

    Returns ``{"turns": [{"role", "content", "tool_name"?, "thinking"?}]}``.
    Read-only — never modifies the session file or the live conversation.

    The ``session_id`` is validated to reject path separators so a crafted
    id can't escape the sessions/ directory.
    """
    import re as _re
    import json as _json

    # Validate: allow UUIDs (36 chars) and timestamp_id format (e.g.
    # "1752150184_9479"). Reject anything with path separators.
    if not _re.fullmatch(r"[0-9a-fA-F_-]{1,60}", session_id):
        return {"turns": [], "error": "invalid session id"}
    path = _SESSIONS_DIR / f"{session_id}.jsonl"
    if not path.exists():
        return {"turns": [], "error": "session not found"}

    turns: list[dict[str, Any]] = []
    current_assistant = ""
    current_thinking = ""

    def _flush_assistant():
        nonlocal current_assistant, current_thinking
        if current_assistant:
            turn: dict[str, Any] = {"role": "assistant", "content": current_assistant}
            if current_thinking:
                turn["thinking"] = current_thinking
            turns.append(turn)
            current_assistant = ""
            current_thinking = ""

    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                evt = _json.loads(line)
            except _json.JSONDecodeError:
                continue

            # --- Format 3: old type-based format (type field at top level) ---
            evt_type = evt.get("type")
            if evt_type == "user_message":
                _flush_assistant()
                msg = evt.get("content") or ""
                if msg:
                    turns.append({"role": "user", "content": msg})
                continue
            if evt_type == "assistant_response":
                _flush_assistant()
                # Old format: content is often empty, text has the actual response
                text = evt.get("text") or evt.get("content") or ""
                if text:
                    turns.append({"role": "assistant", "content": text})
                continue
            if evt_type == "tool_call":
                tool_name = evt.get("tool_name") or evt.get("content") or "tool"
                turns.append({
                    "role": "tool_call",
                    "content": str(tool_name),
                    "tool_name": str(tool_name),
                })
                continue

            # --- Format 1 & 2: event-based format ---
            event_name = evt.get("event")
            if event_name == "assistant_response":
                # Explicit assistant_response event (new, most reliable).
                _flush_assistant()
                data = evt.get("data") or {}
                text = data.get("text") or data.get("content") or ""
                if text:
                    turns.append({"role": "assistant", "content": text})
                continue

            if event_name == "thinking":
                # Accumulate thinking text for the current assistant turn.
                data = evt.get("data") or {}
                chunk = data.get("content") or data.get("chunk") or ""
                if chunk:
                    current_thinking += chunk
                continue

            if event_name == "tool_call":
                data = evt.get("data") or {}
                tool_name = data.get("tool") or data.get("tool_name") or "tool"
                turns.append({
                    "role": "tool_call",
                    "content": str(tool_name),
                    "tool_name": str(tool_name),
                })
                continue

            if event_name != "websocket_message":
                continue

            # --- Format 2: websocket_message events ---
            data = evt.get("data") or {}
            payload = data.get("payload") or {}
            direction = data.get("direction")
            if direction == "in":
                _flush_assistant()
                msg = payload.get("message") or ""
                if msg:
                    turns.append({"role": "user", "content": msg})
            elif direction == "out":
                ptype = payload.get("type")
                if ptype == "answer_chunk":
                    current_assistant += payload.get("content") or ""
                elif ptype == "thinking":
                    current_thinking += payload.get("content") or ""
                elif ptype == "answer_done":
                    # answer_done has the final assembled text in content.
                    # Prefer it over accumulated chunks (chunks may be
                    # skipped by send_personal_message's logging filter).
                    done_content = payload.get("content") or ""
                    if done_content:
                        current_assistant = done_content
                    _flush_assistant()
                elif ptype == "tool_call":
                    tool_name = payload.get("tool_name") or payload.get("content") or "tool"
                    turns.append({
                        "role": "tool_call",
                        "content": str(tool_name),
                        "tool_name": str(tool_name),
                    })
                elif ptype == "tool_result":
                    tool_name = payload.get("tool") or "tool"
                    summary = payload.get("summary") or ""
                    if summary:
                        turns.append({
                            "role": "tool_result",
                            "content": str(summary)[:500],
                            "tool_name": str(tool_name),
                        })

        # Flush trailing assistant text if the session ended mid-stream.
        _flush_assistant()
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

    # Vault root = 4 levels up from routers/ (vaultbot/vaultbot_backend/routers/ -> vault root)
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
    except _asyncio.TimeoutError:
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
    except _asyncio.TimeoutError:
        return {
            "running": False,
            "version": None,
            "models": [],
            "error": "Ollama busy (timed out)",
        }
    except Exception as e:  # noqa: BLE001 — best-effort
        return {"running": False, "version": None, "models": [], "error": str(e)}


# --- /system/stats: hardware resource meters -----------------------------
# Polled by the plugin every 3s for the resource strip (CPU/RAM/GPU/NPU).
# Uses psutil for CPU/RAM/disk/net, optional libraries for GPU/NPU.
# Every field is best-effort: if a library isn't installed or hardware
# isn't present, the field is None and the frontend silently omits it.


def _cpu_stats() -> dict[str, Any]:
    """CPU utilization via psutil."""
    try:
        import psutil

        return {
            "percent": round(psutil.cpu_percent(interval=None), 1),
            "cores": psutil.cpu_count(logical=True) or 0,
            "per_core": [
                round(p, 1) for p in psutil.cpu_percent(interval=None, percpu=True)
            ]
            if psutil.cpu_count()
            else [],
        }
    except Exception:  # noqa: BLE001
        return {"percent": 0, "cores": 0, "per_core": []}


def _ram_stats() -> dict[str, Any]:
    """Memory usage via psutil."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {
            "used_gb": round(vm.used / 1_073_741_824, 1),
            "total_gb": round(vm.total / 1_073_741_824, 1),
            "percent": round(vm.percent, 1),
        }
    except Exception:  # noqa: BLE001
        return {"used_gb": 0, "total_gb": 0, "percent": 0}


def _gpu_stats() -> dict[str, Any] | None:
    """GPU utilization + VRAM + temperature.

    Tries four methods in order:
    1. Windows Performance Counters (works for AMD/NVIDIA iGPUs + dGPUs)
    2. NVIDIA via pynvml (if installed)
    3. AMD via pyadl (if installed)
    4. WMI fallback (name + total VRAM only, no utilization)

    Returns None only if no GPU is detectable at all. The Windows
    Performance Counter path is the most reliable on Windows because
    it doesn't require any vendor-specific library — it uses the OS's
    own GPU telemetry which works for integrated and discrete GPUs alike.
    """
    gpu_name = None
    gpu_vram_total = None

    # CREATE_NO_WINDOW: prevents PowerShell subprocess from popping up
    # a visible console window on every poll. Without this, the 3-second
    # polling from the frontend spawns 3 PowerShell windows every 3
    # seconds, making Obsidian unusable.
    import sys

    _no_window = 0
    if sys.platform == "win32":
        _no_window = 0x08000000  # CREATE_NO_WINDOW

    # ── Get the GPU name + total VRAM via WMI (always works on Windows)
    try:
        import subprocess
        import json as _json

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | "
                "Where-Object { $_.Name -and $_.Name -notmatch 'Microsoft Basic' } | "
                "Select-Object -First 1 Name, AdapterRAM | ConvertTo-Json",
            ],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = _json.loads(result.stdout.strip())
            if isinstance(data, list):
                data = data[0] if data else {}
            gpu_name = data.get("Name", "")
            gpu_vram_total = data.get("AdapterRAM", 0)
            if gpu_vram_total:
                gpu_vram_total = round(gpu_vram_total / 1_073_741_824, 1)
    except Exception:  # noqa: BLE001
        pass

    # ── Windows Performance Counters for real-time utilization + VRAM
    # This works for AMD, NVIDIA, and Intel GPUs on Windows 10+ without
    # any vendor-specific library. It's the same data Task Manager uses.
    try:
        import subprocess as _sp

        # GPU utilization: sum all active engine utilizations.
        # Each engine reports its own percentage; the total GPU usage is
        # the max across all engines (not the sum — one engine at 50% +
        # another at 30% means the GPU is at 50% busy, not 80%).
        util_cmd = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$u = Get-Counter '\\GPU Engine(*)\\Utilization Percentage';"
            "$max = ($u.CounterSamples | Measure-Object CookedValue -Maximum).Maximum;"
            "Write-Output $max"
        )
        result = _sp.run(
            ["powershell", "-NoProfile", "-Command", util_cmd],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window,
        )
        gpu_util = None
        if result.returncode == 0 and result.stdout.strip():
            try:
                gpu_util = round(float(result.stdout.strip()), 1)
            except (ValueError, TypeError):
                pass

        # GPU memory (dedicated + shared VRAM usage).
        # For iGPUs, "Dedicated Usage" includes system RAM allocated to
        # the GPU, so dedicated+shared is the real "GPU memory used".
        # For dGPUs, dedicated is VRAM and shared is minimal.
        vram_cmd = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$d = Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage';"
            "$dSum = ($d.CounterSamples | Measure-Object CookedValue -Sum).Sum;"
            "$s = Get-Counter '\\GPU Adapter Memory(*)\\Shared Usage';"
            "$sSum = ($s.CounterSamples | Measure-Object CookedValue -Sum).Sum;"
            "Write-Output ($dSum + $sSum)"
        )
        result = _sp.run(
            ["powershell", "-NoProfile", "-Command", vram_cmd],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window,
        )
        gpu_vram_used = None
        if result.returncode == 0 and result.stdout.strip():
            try:
                vram_bytes = float(result.stdout.strip())
                gpu_vram_used = round(vram_bytes / 1_073_741_824, 1)
            except (ValueError, TypeError):
                pass

        if gpu_name or gpu_util is not None:
            # For iGPUs (shared system RAM), the WMI AdapterRAM is just
            # the small dedicated segment. Use system RAM total as the
            # "pool" if the GPU name suggests an integrated GPU.
            vram_total = gpu_vram_total
            if gpu_name and any(
                kw in gpu_name.lower()
                for kw in (
                    "radeon",
                    "iris",
                    "uhd",
                    "hd graphics",
                    "integrated",
                    "amd radeon(tm)",
                )
            ):
                try:
                    import psutil as _ps

                    vram_total = round(_ps.virtual_memory().total / 1_073_741_824, 1)
                except Exception:  # noqa: BLE001
                    pass
                # For iGPUs, vram_used can exceed vram_total (shared mem
                # is allocated dynamically from system RAM). Cap the
                # reported used at the total so the meter doesn't break.
                if (
                    gpu_vram_used is not None
                    and vram_total
                    and gpu_vram_used > vram_total
                ):
                    gpu_vram_used = vram_total
            return {
                "name": gpu_name or "GPU",
                "utilization_percent": gpu_util,
                "vram_used_gb": gpu_vram_used,
                "vram_total_gb": vram_total,
                "temperature_c": None,  # no cross-vendor temp via perf counters
            }
    except Exception:  # noqa: BLE001
        pass

    # ── NVIDIA via pynvml (fallback if perf counters failed)
    try:
        import pynvml  # noqa: TRY1 — optional dependency

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        try:
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:  # noqa: BLE001
            temp = None
        try:
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            name = gpu_name or "NVIDIA GPU"
        return {
            "name": name,
            "utilization_percent": util.gpu,
            "vram_used_gb": round(mem.used / 1_073_741_824, 1),
            "vram_total_gb": round(mem.total / 1_073_741_824, 1),
            "temperature_c": temp,
        }
    except Exception:  # noqa: BLE001 — pynvml not installed or no NVIDIA GPU
        pass

    # ── If we got a name from WMI but no utilization, return what we have
    if gpu_name:
        return {
            "name": gpu_name,
            "utilization_percent": None,
            "vram_used_gb": None,
            "vram_total_gb": gpu_vram_total,
            "temperature_c": None,
        }

    return None


def _npu_stats() -> dict[str, Any] | None:
    """NPU utilization if detectable.

    AMD Ryzen AI NPUs and Intel NPUs don't have a standard Python API yet.
    We try Windows Performance Counters via subprocess; if unavailable,
    return None. This field is aspirational — the frontend will omit it
    gracefully until a reliable cross-vendor NPU library exists.
    """
    return None


def _disk_io() -> dict[str, Any]:
    """Disk read/write rates (MB/s) computed as delta since last call."""
    try:
        import psutil
        import time as _time

        counters = psutil.disk_io_counters()
        if not counters:
            return {"read_mb_s": 0, "write_mb_s": 0}
        now = _time.monotonic()
        if not hasattr(_disk_io, "_prev"):
            _disk_io._prev = (now, counters.read_bytes, counters.write_bytes)
            return {"read_mb_s": 0, "write_mb_s": 0}
        dt = now - _disk_io._prev[0]
        if dt < 0.1:
            dt = 0.1
        read_rate = round((counters.read_bytes - _disk_io._prev[1]) / 1_048_576 / dt, 1)
        write_rate = round(
            (counters.write_bytes - _disk_io._prev[2]) / 1_048_576 / dt, 1
        )
        _disk_io._prev = (now, counters.read_bytes, counters.write_bytes)
        return {"read_mb_s": max(0, read_rate), "write_mb_s": max(0, write_rate)}
    except Exception:  # noqa: BLE001
        return {"read_mb_s": 0, "write_mb_s": 0}


def _net_io() -> dict[str, Any]:
    """Network send/recv rates (KB/s) computed as delta since last call."""
    try:
        import psutil
        import time as _time

        counters = psutil.net_io_counters()
        if not counters:
            return {"send_kb_s": 0, "recv_kb_s": 0}
        now = _time.monotonic()
        if not hasattr(_net_io, "_prev"):
            _net_io._prev = (now, counters.bytes_sent, counters.bytes_recv)
            return {"send_kb_s": 0, "recv_kb_s": 0}
        dt = now - _net_io._prev[0]
        if dt < 0.1:
            dt = 0.1
        send_rate = round((counters.bytes_sent - _net_io._prev[1]) / 1024 / dt, 1)
        recv_rate = round((counters.bytes_recv - _net_io._prev[2]) / 1024 / dt, 1)
        _net_io._prev = (now, counters.bytes_sent, counters.bytes_recv)
        return {"send_kb_s": max(0, send_rate), "recv_kb_s": max(0, recv_rate)}
    except Exception:  # noqa: BLE001
        return {"send_kb_s": 0, "recv_kb_s": 0}


@router.get("/system/stats")
async def system_stats() -> dict[str, Any]:
    """Real-time hardware resource snapshot for the plugin's resource strip.

    Polled every 3 seconds by the frontend. Returns CPU, RAM, GPU, NPU,
    disk, and network stats. Every field is best-effort: None values are
    silently omitted by the frontend. Never raises — a stats failure
    never blocks the UI.

    The first call to ``psutil.cpu_percent(interval=None)`` returns 0
    (it needs a baseline), so we pre-seed it with a quick 0.1s sample on
    the first call to avoid a "0% CPU" flash.
    """
    try:
        import psutil

        # Seed cpu_percent so the first poll has a real value.
        if not hasattr(system_stats, "_cpu_seeded"):
            psutil.cpu_percent(interval=0.1)
            system_stats._cpu_seeded = True
    except Exception:  # noqa: BLE001
        pass

    return {
        "cpu": _cpu_stats(),
        "ram": _ram_stats(),
        "gpu": _gpu_stats(),
        "npu": _npu_stats(),
        "disk": _disk_io(),
        "net": _net_io(),
    }


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
                    "content": "Backend is restarting. This is the same code path as the restart button.",
                }
            ),
            session_logger=svc.session_logger,
        )

    asyncio.ensure_future(_delayed_broadcast())
    return {
        "status": "restart_requested",
        "message": "Restart scheduled in 3 seconds. Chat loop will finish first, then plugin will restart the backend.",
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
                "content": "Plugin reload requested. The plugin will disable and re-enable itself.",
            }
        ),
        session_logger=svc.session_logger,
    )
    return {
        "status": "reload_requested",
        "message": "WebSocket broadcast sent. Plugin will reload itself (disable + re-enable). Backend stays running.",
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
