"""WebSocket endpoint: the chat/research dispatch loop.

Migrated from main.py. This is the last router — it deletes the
handle_chat/handle_research shims by calling the extracted
chat_handler.handle_chat / research_handler.handle_research directly with
svc. Uses Depends(get_services) (FastAPI supports Depends in websocket
endpoints — verified against the docs).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Annotated

from app_state import get_services, get_startup_reindex_failed
from chat_handler import handle_chat
from conversation_state import clear_history, clear_trail_tracker, load_history
from diagnostics import classify_error
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from last_session import clear as clear_last_session
from last_session import read as read_last_session
from last_session import touch as touch_last_session
from research_handler import handle_research
from services import Services
from session_logger import SessionLogger
from working_memory import TaskList

router = APIRouter()
logger = logging.getLogger(__name__)

# Path to RESTART_CONTEXT.md — written by the backend_restart tool before
# triggering a restart. If this file exists when a WebSocket connects, the
# agent proactively resumes work without waiting for the operator to send a message.
_RESTART_CONTEXT_PATH = (
    Path(__file__).resolve().parent.parent / "identity" / "RESTART_CONTEXT.md"
)


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    svc: Annotated[Services, Depends(get_services)],
):
    """The chat/research dispatch loop.

    Per-connection conversation history lives on the websocket; the
    sliding window bounds it when it grows too long. The receive loop
    spawns fire-and-forget tasks for each chat/research turn so stop/new
    messages stay responsive.

    SESSION RESUME (2026-08-15): The frontend sends the last-known
    ``session_id`` as a query parameter (``?sid=<uuid>``) so a dropped-
    and-reconnected socket resumes the SAME session instead of minting a
    fresh UUID with zero history. When no ``sid`` is provided (first-ever
    connect, or an older plugin build), the backend falls back to the
    last-active-session pointer written on every turn. Only when that is
    absent too does it mint a new UUID. This fixes the "cuts out and
    resumes the wrong session" bug.

    AUTO-RESUME: If RESTART_CONTEXT.md exists (written by backend_restart
    before triggering a restart), the agent proactively sends a message to
    the operator and spawns handle_chat with a synthetic "continue" trigger. This
    means after a restart, the agent wakes up and continues working without
    the operator having to send a wake-up message. See [[Auto-Resume-Directive]].
    """
    # ── Session resume: reuse the same session_id across reconnects ──
    # Priority: explicit frontend sid > last-active pointer > new UUID.
    _resume_sid = None
    with contextlib.suppress(Exception):
        _resume_sid = websocket.query_params.get("sid")
    if not _resume_sid:
        _resume_sid = read_last_session()
    # Validate a frontend-provided sid actually has state on disk; if not,
    # ignore it (stale plugin-side value after a /new on a different tab)
    # and fall through to a new UUID so we don't silently adopt a dead id.
    if _resume_sid:
        _ss_dir = Path(__file__).resolve().parent.parent / "session_state"
        _has_state = (_ss_dir / f"conversation_state_{_resume_sid}.json").exists() or (
            _ss_dir / f"working_memory_state_{_resume_sid}.json"
        ).exists()
        if not _has_state:
            _resume_sid = None
    session_logger = SessionLogger(session_id=_resume_sid)
    client_host = websocket.client.host if websocket.client else "unknown"
    session_logger.log(
        "websocket_connect",
        {
            "client_host": client_host,
            "session_id": session_logger.session_id,
            "resumed": _resume_sid is not None,
        },
    )
    # Store the session_id on the websocket so chat_handler and tools can
    # read it for per-session persistence (conversation_state, working_memory,
    # checkpoint).  Updated on /new when a new SessionLogger is rolled.
    websocket.session_id = session_logger.session_id
    # Record this as the most recently active session so a reconnect
    # without an explicit sid still finds it.
    touch_last_session(session_logger.session_id, session_logger.title)
    await svc.manager.connect(websocket)
    # Send session info (id + title) so the frontend can display it.
    await svc.manager.send_personal_message(
        json.dumps(
            {
                "type": "session_info",
                "session_id": session_logger.session_id,
                "title": session_logger.title,
            }
        ),
        websocket,
        session_logger=session_logger,
    )

    # ── Model preload on connect ────────────────────────────────────
    # When the user opens a new chat tab (or reconnects after the model was
    # evicted from Ollama's memory), fire a background preload so the model
    # is loaded BEFORE the user's first message arrives.  This eliminates
    # the "first chat of a new session takes 5 minutes" cold-load latency.
    # The preload runs in a thread (Ollama load is blocking) and is a no-op
    # if the model is already resident (is_model_loaded short-circuits) or
    # if the backend is cloud (OpenAICompatibleClient.preload_model is a
    # no-op).  Skip if disabled via VAULTBOT_PRELOAD_ON_CONNECT=0.
    if os.environ.get("VAULTBOT_PRELOAD_ON_CONNECT", "1") != "0":

        def _preload_on_connect():
            import time as _time

            _max_wait = int(os.environ.get("VAULTBOT_PRELOAD_MAX_WAIT_S", "300"))
            _elapsed = 0
            while _elapsed < _max_wait:
                try:
                    if svc.ollama_client.is_model_loaded():
                        return
                    if svc.ollama_client.preload_model():
                        session_logger.log(
                            "model_preloaded_on_connect",
                            {"model": svc.ollama_client.llm_model},
                        )
                        return
                except Exception as e:  # noqa: BLE001
                    session_logger.log(
                        "model_preload_on_connect_retry",
                        {"error": str(e), "elapsed_s": _elapsed},
                    )
                _time.sleep(10)
                _elapsed += 10
            session_logger.log(
                "model_preload_on_connect_timeout",
                {"model": svc.ollama_client.llm_model, "waited_s": _elapsed},
            )

        # Run in a DEDICATED thread (not the default ThreadPoolExecutor)
        # so a long preload (up to 300s for a cold large model) can't
        # starve the executor pool that endpoints rely on.  Each WS
        # reconnect used to grab an executor thread for up to 300s;
        # a few reconnects could exhaust the pool and freeze every
        # run_in_executor-based endpoint (including /models and
        # /llm/providers/.../live_models that the settings tab needs).
        import threading as _threading

        _preload_thread = _threading.Thread(
            target=_preload_on_connect, name="ws-preload", daemon=True
        )
        _preload_thread.start()

    # ── Startup reindex failure check ────────────────────────────────
    # If the background reindex crashed on startup (before any WS was
    # connected), the flag is set. Surface it now so the user knows their
    # vault may not be fully searchable. Cleared after surfacing.
    #
    # The flag lives in app_state (NOT on main.py) so this router never
    # needs to `import main` — a bare `import main` re-executes main.py's
    # top-level code (including acquire_lock() → sys.exit) and crashes the
    # WebSocket handler. See app_state.py docstring.
    try:
        _reindex_err = get_startup_reindex_failed()  # reads + clears (one-shot)
        if _reindex_err:
            from chat_helpers import notify_problem

            _diag = classify_error(
                RuntimeError(_reindex_err), {"stage": "indexing the vault on startup"}
            )
            # Override with a more specific user message.
            _diag.user_message = (
                "VaultBot couldn't finish indexing your vault on startup. "
                "Some notes might not appear in search until you restart."
            )
            _diag.remedy_hint = "Click Restart to re-index your vault."
            await notify_problem(svc, websocket, _diag)
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass  # never block the WS connect on a notification failure
    # Per-connection conversation history. This is THE fix for the "amnesia"
    # bug: without it, every message started a fresh 2-message conversation
    # (system + this message) with zero memory of prior turns.
    #
    # ALWAYS RESTORE on reconnect (Priority A, 2026-08-06): the persisted
    # history is loaded on EVERY connection, not only when RESTART_CONTEXT.md
    # exists. The old RESTART_CONTEXT.md gate meant a crash, uvicorn reload,
    # plugin reload, or WS drop silently WIPED the persisted history via
    # clear_history() — the user's complaint "if the backend restarts, the
    # vaultbot should get back its context from the current session." History
    # only clears on explicit /new. This is the Hermes Agent shape: session
    # persists across restarts, user controls when to reset.
    _is_restart_resume = (
        _RESTART_CONTEXT_PATH.exists()
    )  # only used for auto-resume nudge
    # ── Restart-resume: adopt the pointer session's state ───────────
    # When the backend restarts AND the session_id didn't carry through
    # (no frontend sid, pointer missing/stale), the per-session state
    # files are keyed by the OLD session_id.  We use the last-active
    # pointer (deterministic) instead of the old "most-recent file by
    # mtime" heuristic, which with 60+ accumulated files frequently
    # picked the WRONG session. The pointer is written on every turn,
    # so it reflects the session the operator was actually using.
    _adopted_old_session_id: str | None = None
    if _is_restart_resume:
        try:
            _pointer_sid = read_last_session()
            if _pointer_sid and _pointer_sid != session_logger.session_id:
                _old_sid = _pointer_sid
                _adopted_old_session_id = _old_sid
                # Adopt the working memory under the new session_id.
                _old_wm = TaskList.load_from_disk(session_id=_old_sid)
                if _old_wm is not None and _old_wm.has_plan():
                    _old_wm.save_to_disk(session_id=session_logger.session_id)
                    session_logger.log(
                        "wm_adopted_from_old_session",
                        {
                            "old_session_id": _old_sid,
                            "new_session_id": session_logger.session_id,
                            "goal": _old_wm.goal[:100],
                            "tasks": len(_old_wm.tasks),
                            "source": "last_active_pointer",
                        },
                    )
                # Adopt the conversation history under the new session_id.
                _old_conv = load_history(session_id=_old_sid)
                if _old_conv:
                    from conversation_state import save_history

                    save_history(_old_conv, session_id=session_logger.session_id)
                    session_logger.log(
                        "conv_adopted_from_old_session",
                        {
                            "old_session_id": _old_sid,
                            "new_session_id": session_logger.session_id,
                            "turns": len(_old_conv),
                            "source": "last_active_pointer",
                        },
                    )
                # Update the pointer to the new session_id.
                touch_last_session(session_logger.session_id, session_logger.title)
        except Exception as _e:  # noqa: BLE001 — best-effort
            session_logger.log("restart_adopt_failed", {"error": str(_e)})
    try:
        restored = load_history(session_id=session_logger.session_id)
        websocket.conversation_history = restored
        # Rebuild the conversation index from the restored history so
        # the bot can recall what was said before the restart.
        if restored:
            try:
                _conv_idx = getattr(svc, "conversation_index", None)
                if _conv_idx is not None:
                    _conv_idx.rebuild_from_history(
                        restored, session_id=session_logger.session_id
                    )
            except Exception:  # noqa: BLE001 — best-effort, index rebuild is optional
                pass
    except ValueError as _hist_err:
        # History file is corrupt. load_history already backed it up.
        # Start fresh + notify the user so they know their conversation
        # was lost, rather than silently amnesia-ing.
        websocket.conversation_history = []
        _hist_diag = classify_error(
            _hist_err, {"category": "history_lost", "stage": "reconnecting"}
        )
        await notify_problem(svc, websocket, _hist_diag)
        session_logger.log(
            "conversation_history_corrupt",
            {
                "error": str(_hist_err),
            },
        )
    # Working memory (the Copilot/Claude Code TodoList pattern). Restored on
    # EVERY reconnect if a persisted plan exists — same rationale as
    # conversation history: a crash/reload shouldn't wipe the plan. Cleared
    # only on explicit /new.
    _saved_wm = TaskList.load_from_disk(session_id=session_logger.session_id)
    if _saved_wm is not None and _saved_wm.has_plan():
        websocket.working_memory = _saved_wm
        session_logger.log(
            "working_memory_restored",
            {
                "goal": _saved_wm.goal[:100],
                "tasks": len(_saved_wm.tasks),
            },
        )
    else:
        websocket.working_memory = TaskList()
    _restored = websocket.conversation_history
    if _restored:
        session_logger.log(
            "conversation_history_restored",
            {
                "turns": len(_restored),
                "history_chars": sum(len(str(m.get("content", ""))) for m in _restored),
            },
        )

    # ---- AUTO-RESUME ---------------------------------------------------
    # If RESTART_CONTEXT.md exists, the backend was restarted mid-session.
    # The restart context (recent chat history) will be injected into the
    # system prompt by Identity.boot_context() on the first handle_chat
    # call. Here we proactively trigger that call so the agent resumes
    # without the operator having to send a wake-up message.
    if _RESTART_CONTEXT_PATH.exists():
        session_logger.log(
            "auto_resume_triggered",
            {
                "restart_context": str(_RESTART_CONTEXT_PATH),
            },
        )
        # Send a proactive heads-up to the operator so he sees something happening.
        await svc.manager.send_personal_message(
            json.dumps(
                {
                    "type": "chat",
                    "content": "Backend restarted. Picking up where I left off...",
                }
            ),
            websocket,
            session_logger=session_logger,
        )

        # Spawn handle_chat with a synthetic continue message. The LLM will
        # see the restart context in its system prompt (via boot_context)
        # and this message telling it to continue. This runs concurrently
        # with the receive loop below, so the operator can still interrupt with
        # stop/new messages while the agent is resuming.
        async def _auto_resume():
            try:
                await handle_chat(
                    svc,
                    websocket,
                    "You were just restarted mid-session. Your restart context "
                    "(recent chat history) has been injected into your system "
                    "prompt. Check your working memory (the plan_task task list) "
                    "and continue where you left off. Don't ask the operator "
                    "to re-explain anything. Just do it.",
                    session_logger,
                )
            except asyncio.CancelledError:
                session_logger.log("auto_resume_cancelled", {"reason": "interrupted"})
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                session_logger.log_exception(e, context="auto_resume")
                # Surface auto-resume failures as a typed problem so the
                # UI shows a remedy card, not a raw "Server error: …".
                diag = classify_error(e, {"stage": "resuming after restart"})
                await svc.manager.send_personal_message(
                    json.dumps({"type": "problem", "diagnosis": diag.to_dict()}),
                    websocket,
                    session_logger=session_logger,
                )
            finally:
                try:
                    svc.autonomous_researcher.resume_after_chat()
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    logger.debug("swallowed: %s", e)

        websocket._current_task = asyncio.create_task(_auto_resume())

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as e:
                session_logger.log_exception(e, context="websocket_receive_json")
                await svc.manager.send_personal_message(
                    json.dumps({"type": "error", "content": "Invalid JSON"}), websocket
                )
                continue

            session_logger.log_message("in", payload)

            msg_type = payload.get("type", "chat")
            user_message = payload.get("message", "")

            # "stop" lets the UI interrupt a running chat/research without
            # sending a new message. Cancels the current task if any.
            if msg_type == "stop":
                task = getattr(websocket, "_current_task", None)
                if task and not task.done():
                    task._stopped_by_user = True
                    # Set the cancel flag FIRST so _check_cancelled() raises
                    # CancelledError at every phase boundary in the agentic
                    # loop — not just at await points.
                    websocket._cancelled = True
                    # Close the active HTTP streaming response so the
                    # executor thread (blocked in response.iter_lines()) is
                    # unblocked immediately.
                    with contextlib.suppress(Exception):
                        svc.ollama_client.cancel_active_stream()
                    task.cancel()
                await svc.manager.send_personal_message(
                    json.dumps({"type": "stopped", "content": "Interrupted"}), websocket
                )
                continue

            # "/new" starts a FRESH session: clears history + rolls a new log.
            if msg_type == "chat" and user_message.strip().lower() == "/new":
                task = getattr(websocket, "_current_task", None)
                if task and not task.done():
                    task._stopped_by_user = True
                    websocket._cancelled = True
                    with contextlib.suppress(Exception):
                        svc.ollama_client.cancel_active_stream()
                    task.cancel()
                websocket.conversation_history = []
                # Capture the old session_id before rolling a new one so we
                # can clear ONLY this session's persisted state — other
                # tabs' sessions are untouched.
                _old_sid = getattr(websocket, "session_id", None)
                # Clear the working-memory task list too so /new wipes the plan.
                if hasattr(websocket, "working_memory"):
                    websocket.working_memory.clear()
                    # Also wipe the persisted working-memory state for the
                    # old session only.
                    try:
                        from working_memory import TaskList as _TL

                        _TL.clear_disk(session_id=_old_sid)
                    except Exception:  # noqa: BLE001
                        pass
                # Wipe the persisted copy too so a restart after /new doesn't
                # resurrect the cleared thread.  Clear the OLD session's files
                # only — other tabs' sessions are untouched.
                clear_history(session_id=_old_sid)
                clear_trail_tracker()
                # Clear the last-active pointer so a reconnect after /new
                # doesn't resurrect the just-discarded session.
                clear_last_session()
                # Clear the conversation index so recall starts fresh.
                try:
                    _conv_idx = getattr(svc, "conversation_index", None)
                    if _conv_idx is not None:
                        _conv_idx.clear(session_id=_old_sid)
                except Exception:  # noqa: BLE001
                    pass
                old_session_id = session_logger.session_id
                session_logger = SessionLogger()
                # Update the websocket's session_id to the new session so
                # subsequent save/load calls target the new session's files.
                websocket.session_id = session_logger.session_id
                # Point the last-active pointer at the new session.
                touch_last_session(session_logger.session_id, session_logger.title)
                session_logger.log(
                    "session_reset",
                    {"trigger": "/new", "previous_session_id": old_session_id},
                )
                session_logger.log("websocket_connect", {"client_host": client_host})
                await svc.manager.send_personal_message(
                    json.dumps(
                        {
                            "type": "session_reset",
                            "content": (
                                "New session started. I've cleared our "
                                "conversation history — what would you like "
                                "to work on?"
                            ),
                        }
                    ),
                    websocket,
                    session_logger=session_logger,
                )
                # Send updated session info for the new session.
                await svc.manager.send_personal_message(
                    json.dumps(
                        {
                            "type": "session_info",
                            "session_id": session_logger.session_id,
                            "title": session_logger.title,
                        }
                    ),
                    websocket,
                    session_logger=session_logger,
                )
                continue

            # ── Slash-command surface ──────────────────────────────────
            # Discoverable in-product via the frontend's "/" dropdown; the
            # backend is the single source of truth for what commands do.
            # Unknown "/foo" is intercepted here and answered with the
            # help text — it NEVER reaches the LLM as a normal message
            # (a non-tech user typing "/cleer" shouldn't get a hallucinated
            # reply). Only known commands are recognized; anything else
            # starting with "/" gets the friendly "try /help" nudge.
            if msg_type == "chat":
                cmd = user_message.strip().lower()
                if cmd == "/help":
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "system_info",
                                "content": (
                                    "Commands you can type here:\n"
                                    "  /new      — start a fresh conversation\n"
                                    "  /clear    — clear the chat window "
                                    "(keeps history)\n"
                                    "  /stop     — stop what I'm doing "
                                    "(same as the Stop button)\n"
                                    "  /ingest   — index any new textbooks "
                                    "from learningMaterial/\n"
                                    "  /diagnose — run a health check and "
                                    "show any problems\n"
                                    "  /restart  — restart the backend\n"
                                    "  /help     — show this list"
                                ),
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                    continue
                if cmd == "/clear":
                    # Clear the on-screen chat only (history persists). The
                    # frontend handles the visual wipe; this ack keeps the
                    # channel in sync.
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "session_reset",
                                "content": (
                                    "Chat cleared. Your history is saved — "
                                    "I still remember our conversation."
                                ),
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                    continue
                if cmd == "/stop":
                    task = getattr(websocket, "_current_task", None)
                    if task and not task.done():
                        task._stopped_by_user = True
                        websocket._cancelled = True
                        with contextlib.suppress(Exception):
                            svc.ollama_client.cancel_active_stream()
                        task.cancel()
                    await svc.manager.send_personal_message(
                        json.dumps({"type": "stopped", "content": "Interrupted"}),
                        websocket,
                    )
                    continue
                if cmd == "/diagnose":
                    # Run the proactive battery + stream results as problem
                    # cards. Reuses the same check battery as the /diagnose
                    # endpoint so there's one path for button + command.
                    try:
                        from routers.system import _run_diagnose_checks

                        problems = [d.to_dict() for d in _run_diagnose_checks(svc)]
                        if not problems:
                            await svc.manager.send_personal_message(
                                json.dumps(
                                    {
                                        "type": "system_info",
                                        "content": (
                                            "Everything looks healthy. "
                                            "No problems found."
                                        ),
                                    }
                                ),
                                websocket,
                                session_logger=session_logger,
                            )
                        else:
                            for p in problems:
                                await svc.manager.send_personal_message(
                                    json.dumps({"type": "problem", "diagnosis": p}),
                                    websocket,
                                    session_logger=session_logger,
                                )
                    except Exception as diag_err:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        session_logger.log_exception(
                            diag_err, context="/diagnose command"
                        )
                        await svc.manager.send_personal_message(
                            json.dumps(
                                {
                                    "type": "problem",
                                    "diagnosis": classify_error(
                                        diag_err, {"stage": "diagnose"}
                                    ).to_dict(),
                                }
                            ),
                            websocket,
                            session_logger=session_logger,
                        )
                    continue
                if cmd == "/ingest":
                    # Index any new PDFs from learningMaterial/ as
                    # pointer-only TOCs. Replaces the old Ingest GUI button
                    # — same logic as the /ingest_learning_material HTTP
                    # endpoint, but streamed back through the chat so the
                    # user sees progress + results in the conversation.
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "status",
                                "content": (
                                    "Scanning learningMaterial/ for new textbooks..."
                                ),
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                    try:
                        from textbook_index import index_learning_material

                        loop = asyncio.get_event_loop()
                        from paths import FRAMEWORK_ROOT

                        learning_dir = FRAMEWORK_ROOT / "learningMaterial"
                        result = await loop.run_in_executor(
                            None,
                            lambda learning_dir=learning_dir: index_learning_material(
                                str(learning_dir)
                            ),
                        )
                        msg = result.get(
                            "message",
                            f"Indexed {result.get('indexed', 0)} new, "
                            f"skipped {result.get('skipped', 0)}, "
                            f"errors {result.get('errors', 0)}.",
                        )
                        await svc.manager.send_personal_message(
                            json.dumps({"type": "system_info", "content": msg}),
                            websocket,
                            session_logger=session_logger,
                        )
                        # Stream per-file details so the user sees what
                        # happened to each PDF.
                        for d in result.get("details", []):
                            if d.get("error"):
                                await svc.manager.send_personal_message(
                                    json.dumps(
                                        {
                                            "type": "system_info",
                                            "content": (
                                                f"  ✗ {d.get('file', '?')}: "
                                                f"{d['error']}"
                                            ),
                                        }
                                    ),
                                    websocket,
                                    session_logger=session_logger,
                                )
                            else:
                                notes = d.get("notes_created", "?")
                                await svc.manager.send_personal_message(
                                    json.dumps(
                                        {
                                            "type": "system_info",
                                            "content": (
                                                f"  ✓ {d.get('file', '?')}: "
                                                f"{notes} notes"
                                            ),
                                        }
                                    ),
                                    websocket,
                                    session_logger=session_logger,
                                )
                    except Exception as ingest_err:  # noqa: BLE001 — best-effort, surfaces error to user — see CONTRIBUTING.md no-silent-fallbacks
                        session_logger.log_exception(
                            ingest_err, context="/ingest command"
                        )
                        await svc.manager.send_personal_message(
                            json.dumps(
                                {
                                    "type": "problem",
                                    "diagnosis": classify_error(
                                        ingest_err, {"stage": "ingest"}
                                    ).to_dict(),
                                }
                            ),
                            websocket,
                            session_logger=session_logger,
                        )
                    continue
                if cmd == "/restart":
                    # Restart the backend via the same /restart broadcast
                    # path the GUI button used. We send a heads-up, then
                    # broadcast the restart event so the plugin's
                    # restartBackend() fires. No HTTP round-trip needed.
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "system_info",
                                "content": "Restarting the backend...",
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                    try:
                        if svc.manager:
                            await svc.manager.broadcast(
                                json.dumps(
                                    {
                                        "type": "restart",
                                        "content": (
                                            "Restart requested via /restart command."
                                        ),
                                    }
                                )
                            )
                    except Exception as restart_err:  # noqa: BLE001
                        session_logger.log_exception(
                            restart_err, context="/restart command"
                        )
                    continue
                if cmd.startswith("/") and cmd not in ("/new",):
                    # Unknown slash command: nudge, don't hallucinate.
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "system_info",
                                "content": (
                                    f'Unknown command "{cmd}". Type /help to see '
                                    "what's available."
                                ),
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                    continue

            # Allow the frontend to submit questionnaire answers via the
            # same WebSocket channel as normal chat messages. The ask_user
            # tool blocks on a threading.Event; this unblocks it.
            if msg_type == "user_response":
                request_id = payload.get("request_id", "")
                if not request_id:
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "error",
                                "content": "Missing request_id in user_response",
                            }
                        ),
                        websocket,
                    )
                    continue
                try:
                    from custom_tools.ask_user import _pending_requests
                except ImportError:
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {"type": "error", "content": "ask_user tool not loaded"}
                        ),
                        websocket,
                    )
                    continue
                entry = _pending_requests.get(request_id)
                if entry is None:
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "error",
                                "content": f"No pending request with id {request_id}",
                            }
                        ),
                        websocket,
                    )
                    continue
                event, response_holder = entry[0], entry[1]
                answers = payload.get("answers", {})
                comments = payload.get("comments", "")
                response_holder.clear()
                response_holder.update(answers)
                if comments:
                    response_holder["_comments"] = comments
                event.set()
                session_logger.log(
                    "user_response_received",
                    {
                        "request_id": request_id,
                        "answer_count": len(answers),
                    },
                )
                continue

            # Allow the frontend to update the session title inline.
            if msg_type == "set_title":
                new_title = payload.get("title", "").strip()
                if new_title:
                    session_logger.set_title(new_title)
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "session_info",
                                "session_id": session_logger.session_id,
                                "title": session_logger.title,
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                continue

            if not user_message:
                session_logger.log("empty_message", {"payload": payload})
                continue

            # Interrupt-on-send: cancel any in-flight turn.
            task = getattr(websocket, "_current_task", None)
            if task and not task.done():
                websocket._cancelled = True
                with contextlib.suppress(Exception):
                    svc.ollama_client.cancel_active_stream()
                task.cancel()
                session_logger.log("chat_interrupted", {"reason": "new_message"})

            # Interrupt the QA idle worker — it should stop after the
            # current note so the user's message gets full hardware.
            try:
                from qa_worker import get_qa_interrupt

                get_qa_interrupt().trigger()
            except Exception:  # noqa: BLE001
                pass

            # Auto-generate session title from the first user message if
            # the title is still the default "New Session".
            if session_logger.title == "New Session" and user_message.strip():
                _auto_title = user_message.strip()[:80]
                session_logger.set_title(_auto_title)
                await svc.manager.send_personal_message(
                    json.dumps(
                        {
                            "type": "session_info",
                            "session_id": session_logger.session_id,
                            "title": session_logger.title,
                        }
                    ),
                    websocket,
                    session_logger=session_logger,
                )

            # Spawn the handler fire-and-forget so the receive loop stays
            # responsive to stop/new messages.
            def _spawn_handler(
                msg_type=msg_type,
                user_message=user_message,
                session_logger=session_logger,
            ):
                async def _run():
                    try:
                        if msg_type == "research":
                            await handle_research(
                                svc, websocket, user_message, session_logger
                            )
                        else:
                            await handle_chat(
                                svc, websocket, user_message, session_logger
                            )
                    except asyncio.CancelledError:
                        session_logger.log("chat_cancelled", {"reason": "interrupted"})
                        if not getattr(
                            asyncio.current_task(), "_stopped_by_user", False
                        ):
                            await svc.manager.send_personal_message(
                                json.dumps(
                                    {"type": "stopped", "content": "Interrupted"}
                                ),
                                websocket,
                            )
                    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        session_logger.log_exception(e, context=f"handle_{msg_type}")
                        # Translate the raw exception into a typed, user-
                        # facing Diagnosis. The frontend renders a remedy
                        # card; the raw repr stays only in backend.log via
                        # log_exception above. This is the "classify at the
                        # edge" rule: no stack trace reaches the chat UI.
                        diag = classify_error(e, {"stage": msg_type})
                        await svc.manager.send_personal_message(
                            json.dumps(
                                {"type": "problem", "diagnosis": diag.to_dict()}
                            ),
                            websocket,
                        )
                    finally:
                        # Chat-priority: always release the researcher pause.
                        try:
                            svc.autonomous_researcher.resume_after_chat()
                        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                            logger.debug("swallowed: %s", e)

                return asyncio.create_task(_run())

            websocket._current_task = _spawn_handler()
    except WebSocketDisconnect:
        session_logger.log("websocket_disconnect", {"reason": "client_disconnected"})
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        session_logger.log_exception(e, context="websocket_endpoint")
    finally:
        svc.manager.disconnect(websocket)
        session_logger.close()
