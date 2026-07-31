"""WebSocket endpoint: the chat/research dispatch loop.

Migrated from main.py. This is the last router — it deletes the
handle_chat/handle_research shims by calling the extracted
chat_handler.handle_chat / research_handler.handle_research directly with
svc. Uses Depends(get_services) (FastAPI supports Depends in websocket
endpoints — verified against the docs).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app_state import get_services
from diagnostics import classify_error
from services import Services
from session_logger import SessionLogger
from chat_handler import handle_chat
from research_handler import handle_research
from conversation_state import load_history, clear_history, clear_trail_tracker

router = APIRouter()
logger = logging.getLogger(__name__)

# Path to RESTART_CONTEXT.md — written by the backend_restart tool before
# triggering a restart. If this file exists when a WebSocket connects, the
# agent proactively resumes work without waiting for the operator to send a message.
_RESTART_CONTEXT_PATH = (
    Path(__file__).resolve().parent.parent / "identity" / "RESTART_CONTEXT.md"
)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket,
                             svc: Annotated[Services, Depends(get_services)]):
    """The chat/research dispatch loop.

    Per-connection conversation history lives on the websocket; the
    compactor trims it when it grows too long. The receive loop spawns
    fire-and-forget tasks for each chat/research turn so stop/new messages
    stay responsive.

    AUTO-RESUME: If RESTART_CONTEXT.md exists (written by backend_restart
    before triggering a restart), the agent proactively sends a message to
    the operator and spawns handle_chat with a synthetic "continue" trigger. This
    means after a restart, the agent wakes up and continues working without
    the operator having to send a wake-up message. See [[Auto-Resume-Directive]].
    """
    session_logger = SessionLogger()
    client_host = websocket.client.host if websocket.client else "unknown"
    session_logger.log("websocket_connect", {"client_host": client_host})
    await svc.manager.connect(websocket)
    # Send session info (id + title) so the frontend can display it.
    await svc.manager.send_personal_message(json.dumps({
        "type": "session_info",
        "session_id": session_logger.session_id,
        "title": session_logger.title,
    }), websocket, session_logger=session_logger)
    # Per-connection conversation history. This is THE fix for the "amnesia"
    # bug: without it, every message started a fresh 2-message conversation
    # (system + this message) with zero memory of prior turns.
    #
    # RESTORE-ON-RECONNECT: load the persisted history from disk so a backend
    # restart (the operator asked VaultBot to restart itself, a crash, a code reload)
    # brings the agent back into the SAME session — the live thread is
    # restored, not just the slow identity files. the operator: "change your restart
    # backend tool to bring you back into the same session and start you
    # back up." This is that change.
    restored = load_history()
    websocket.conversation_history = restored
    # Fresh working memory per websocket connection (the Copilot/Claude Code
    # TodoList pattern). Cleared on /new and on reconnect.
    from working_memory import TaskList
    websocket.working_memory = TaskList()
    if restored:
        session_logger.log("conversation_history_restored", {
            "turns": len(restored),
            "history_chars": sum(len(str(m.get("content", ""))) for m in restored),
        })

    # ---- AUTO-RESUME ---------------------------------------------------
    # If RESTART_CONTEXT.md exists, the backend was restarted mid-session.
    # The restart context (recent chat history) will be injected into the
    # system prompt by Identity.boot_context() on the first handle_chat
    # call. Here we proactively trigger that call so the agent resumes
    # without the operator having to send a wake-up message.
    if _RESTART_CONTEXT_PATH.exists():
        session_logger.log("auto_resume_triggered", {
            "restart_context": str(_RESTART_CONTEXT_PATH),
        })
        # Send a proactive heads-up to the operator so he sees something happening.
        await svc.manager.send_personal_message(
            json.dumps({
                "type": "chat",
                "content": "Backend restarted. Picking up where I left off...",
            }),
            websocket, session_logger=session_logger)

        # Spawn handle_chat with a synthetic continue message. The LLM will
        # see the restart context in its system prompt (via boot_context)
        # and this message telling it to continue. This runs concurrently
        # with the receive loop below, so the operator can still interrupt with
        # stop/new messages while the agent is resuming.
        async def _auto_resume():
            try:
                await handle_chat(
                    svc, websocket,
                    "You were just restarted mid-session. Your restart context "
                    "(recent chat history) has been injected into your system "
                    "prompt. Read GOALS.md and continue where you left off. "
                    "Don't ask the operator to re-explain anything. Just do it.",
                    session_logger)
            except asyncio.CancelledError:
                session_logger.log("auto_resume_cancelled", {"reason": "interrupted"})
            except Exception as e:
                session_logger.log_exception(e, context="auto_resume")
                # Surface auto-resume failures as a typed problem so the
                # UI shows a remedy card, not a raw "Server error: …".
                diag = classify_error(e, {"stage": "resuming after restart"})
                await svc.manager.send_personal_message(
                    json.dumps({"type": "problem", "diagnosis": diag.to_dict()}),
                    websocket, session_logger=session_logger)
            finally:
                try:
                    svc.autonomous_researcher.resume_after_chat()
                except Exception as e:
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
                    json.dumps({"type": "error", "content": "Invalid JSON"}),
                    websocket)
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
                    task.cancel()
                await svc.manager.send_personal_message(
                    json.dumps({"type": "stopped", "content": "Interrupted"}),
                    websocket)
                continue

            # "/new" starts a FRESH session: clears history + rolls a new log.
            if msg_type == "chat" and user_message.strip().lower() == "/new":
                task = getattr(websocket, "_current_task", None)
                if task and not task.done():
                    task._stopped_by_user = True
                    task.cancel()
                websocket.conversation_history = []
                # Clear the working-memory task list too so /new wipes the plan.
                if hasattr(websocket, "working_memory"):
                    websocket.working_memory.clear()
                # Wipe the persisted copy too so a restart after /new doesn't
                # resurrect the cleared thread.
                clear_history()
                clear_trail_tracker()
                old_session_id = session_logger.session_id
                session_logger = SessionLogger()
                session_logger.log("session_reset", {
                    "trigger": "/new", "previous_session_id": old_session_id})
                session_logger.log("websocket_connect", {"client_host": client_host})
                await svc.manager.send_personal_message(json.dumps({
                    "type": "session_reset",
                    "content": "New session started. I've cleared our conversation history — what would you like to work on?"
                }), websocket, session_logger=session_logger)
                # Send updated session info for the new session.
                await svc.manager.send_personal_message(json.dumps({
                    "type": "session_info",
                    "session_id": session_logger.session_id,
                    "title": session_logger.title,
                }), websocket, session_logger=session_logger)
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
                    await svc.manager.send_personal_message(json.dumps({
                        "type": "system_info",
                        "content": (
                            "Commands you can type here:\n"
                            "  /new     — start a fresh conversation\n"
                            "  /clear   — clear the chat window (keeps history)\n"
                            "  /stop    — stop what I'm doing (same as the Stop button)\n"
                            "  /diagnose — run a health check and show any problems\n"
                            "  /help    — show this list"
                        ),
                    }), websocket, session_logger=session_logger)
                    continue
                if cmd == "/clear":
                    # Clear the on-screen chat only (history persists). The
                    # frontend handles the visual wipe; this ack keeps the
                    # channel in sync.
                    await svc.manager.send_personal_message(json.dumps({
                        "type": "session_reset",
                        "content": "Chat cleared. Your history is saved — I still remember our conversation.",
                    }), websocket, session_logger=session_logger)
                    continue
                if cmd == "/stop":
                    task = getattr(websocket, "_current_task", None)
                    if task and not task.done():
                        task._stopped_by_user = True
                        task.cancel()
                    await svc.manager.send_personal_message(
                        json.dumps({"type": "stopped", "content": "Interrupted"}),
                        websocket)
                    continue
                if cmd == "/diagnose":
                    # Run the proactive battery + stream results as problem
                    # cards. Reuses the same check battery as the /diagnose
                    # endpoint so there's one path for button + command.
                    try:
                        from routers.system import _run_diagnose_checks
                        problems = [d.to_dict()
                                    for d in _run_diagnose_checks(svc)]
                        if not problems:
                            await svc.manager.send_personal_message(
                                json.dumps({"type": "system_info",
                                            "content": "Everything looks healthy. No problems found."}),
                                websocket, session_logger=session_logger)
                        else:
                            for p in problems:
                                await svc.manager.send_personal_message(
                                    json.dumps({"type": "problem", "diagnosis": p}),
                                    websocket, session_logger=session_logger)
                    except Exception as diag_err:
                        session_logger.log_exception(
                            diag_err, context="/diagnose command")
                        await svc.manager.send_personal_message(
                            json.dumps({"type": "problem",
                                        "diagnosis": classify_error(
                                            diag_err, {"stage": "diagnose"}
                                        ).to_dict()}),
                            websocket, session_logger=session_logger)
                    continue
                if cmd.startswith("/") and cmd not in ("/new",):
                    # Unknown slash command: nudge, don't hallucinate.
                    await svc.manager.send_personal_message(json.dumps({
                        "type": "system_info",
                        "content": (
                            f"Unknown command \"{cmd}\". Type /help to see "
                            "what's available."
                        ),
                    }), websocket, session_logger=session_logger)
                    continue

            # Allow the frontend to update the session title inline.
            if msg_type == "set_title":
                new_title = payload.get("title", "").strip()
                if new_title:
                    session_logger.set_title(new_title)
                    await svc.manager.send_personal_message(json.dumps({
                        "type": "session_info",
                        "session_id": session_logger.session_id,
                        "title": session_logger.title,
                    }), websocket, session_logger=session_logger)
                continue

            if not user_message:
                session_logger.log("empty_message", {"payload": payload})
                continue

            # Optional per-message model override
            requested_model = payload.get("model")
            if requested_model and requested_model != svc.ollama_client.llm_model:
                svc.ollama_client.set_model(requested_model)
                session_logger.log("model_override", {"model": requested_model})

            # Interrupt-on-send: cancel any in-flight turn.
            task = getattr(websocket, "_current_task", None)
            if task and not task.done():
                task.cancel()
                session_logger.log("chat_interrupted", {"reason": "new_message"})

            # Auto-generate session title from the first user message if
            # the title is still the default "New Session".
            if session_logger.title == "New Session" and user_message.strip():
                _auto_title = user_message.strip()[:80]
                session_logger.set_title(_auto_title)
                await svc.manager.send_personal_message(json.dumps({
                    "type": "session_info",
                    "session_id": session_logger.session_id,
                    "title": session_logger.title,
                }), websocket, session_logger=session_logger)

            # Spawn the handler fire-and-forget so the receive loop stays
            # responsive to stop/new messages.
            def _spawn_handler():
                async def _run():
                    try:
                        if msg_type == "research":
                            await handle_research(svc, websocket, user_message, session_logger)
                        else:
                            await handle_chat(svc, websocket, user_message, session_logger)
                    except asyncio.CancelledError:
                        session_logger.log("chat_cancelled", {"reason": "interrupted"})
                        if not getattr(asyncio.current_task(), "_stopped_by_user", False):
                            await svc.manager.send_personal_message(
                                json.dumps({"type": "stopped", "content": "Interrupted"}),
                                websocket)
                    except Exception as e:
                        session_logger.log_exception(e, context=f"handle_{msg_type}")
                        # Translate the raw exception into a typed, user-
                        # facing Diagnosis. The frontend renders a remedy
                        # card; the raw repr stays only in backend.log via
                        # log_exception above. This is the "classify at the
                        # edge" rule: no stack trace reaches the chat UI.
                        diag = classify_error(e, {"stage": msg_type})
                        await svc.manager.send_personal_message(
                            json.dumps({"type": "problem",
                                        "diagnosis": diag.to_dict()}),
                            websocket)
                    finally:
                        # Chat-priority: always release the researcher pause.
                        try:
                            svc.autonomous_researcher.resume_after_chat()
                        except Exception as e:
                            logger.debug("swallowed: %s", e)
                return asyncio.create_task(_run())

            websocket._current_task = _spawn_handler()
    except WebSocketDisconnect:
        session_logger.log("websocket_disconnect", {"reason": "client_disconnected"})
    except Exception as e:
        session_logger.log_exception(e, context="websocket_endpoint")
    finally:
        svc.manager.disconnect(websocket)
        session_logger.close()