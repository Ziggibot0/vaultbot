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
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app_state import get_services
from services import Services
from session_logger import SessionLogger
from chat_handler import handle_chat
from research_handler import handle_research
from conversation_state import load_history, clear_history

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket,
                             svc: Annotated[Services, Depends(get_services)]):
    """The chat/research dispatch loop.

    Per-connection conversation history lives on the websocket; the
    compactor trims it when it grows too long. The receive loop spawns
    fire-and-forget tasks for each chat/research turn so stop/new messages
    stay responsive.
    """
    session_logger = SessionLogger()
    client_host = websocket.client.host if websocket.client else "unknown"
    session_logger.log("websocket_connect", {"client_host": client_host})
    await svc.manager.connect(websocket)
    # Per-connection conversation history. This is THE fix for the "amnesia"
    # bug: without it, every message started a fresh 2-message conversation
    # (system + this message) with zero memory of prior turns.
    #
    # RESTORE-ON-RECONNECT: load the persisted history from disk so a backend
    # restart (Sean asked VaultBot to restart itself, a crash, a code reload)
    # brings the agent back into the SAME session — the live thread is
    # restored, not just the slow identity files. Sean: "change your restart
    # backend tool to bring you back into the same session and start you
    # back up." This is that change.
    restored = load_history()
    websocket.conversation_history = restored
    if restored:
        session_logger.log("conversation_history_restored", {
            "turns": len(restored),
            "history_chars": sum(len(str(m.get("content", ""))) for m in restored),
        })
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
                # Wipe the persisted copy too so a restart after /new doesn't
                # resurrect the cleared thread.
                clear_history()
                old_session_id = session_logger.session_id
                session_logger = SessionLogger()
                session_logger.log("session_reset", {
                    "trigger": "/new", "previous_session_id": old_session_id})
                session_logger.log("websocket_connect", {"client_host": client_host})
                await svc.manager.send_personal_message(json.dumps({
                    "type": "session_reset",
                    "content": "New session started. I've cleared our conversation history — what would you like to work on?"
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
                        await svc.manager.send_personal_message(
                            json.dumps({"type": "error", "content": f"Server error: {e}"}),
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