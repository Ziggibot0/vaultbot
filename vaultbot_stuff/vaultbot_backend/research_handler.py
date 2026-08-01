"""Research websocket handler, extracted from main.py.

Contains:
- handle_research: deep-research the web via the LLM-light engine, create a
  linked note, then answer from the note + vault.
- derive_topic: derive a concise note title from the user's research request.
- build_context: legacy graph-context builder (kept for backward
  compatibility; graph context is now built by build_graph_context).

These functions were extracted verbatim from main.py and adapted to take a
`Services` registry as their first parameter so they no longer read
main.py's module-level globals as free variables. main.py-side helpers
(_send_progress, _run_with_heartbeat, handle_chat) are imported lazily
inside the function body to avoid an import cycle with main.py.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from chat_handler import handle_chat

# Leaf-module imports for helpers that were previously deferred-imported
# from main (circular). These are now direct leaf imports — no main dependency.
from chat_helpers import notify_problem, run_with_heartbeat, send_progress
from fastapi import WebSocket
from services import Services
from session_logger import SessionLogger


async def handle_research(
    websocket: WebSocket,
    user_message: str,
    session_logger: SessionLogger,
    svc: Services,
):
    """Deep-research the web via the LLM-light engine, create a linked note,
    then answer from the note + vault.

    The dig itself uses NO LLM — only extractive synthesis over corroborated
    sources. The LLM only sees the finished, sourced summary at the end.
    """
    # Module-level imports from chat_helpers, chat_handler — no longer
    # deferred from main (circular dependency eliminated).
    session_logger.log("research_begin", {"user_message": user_message})
    await svc.manager.send_personal_message(
        json.dumps({"type": "status", "content": "Researching the web (deep dig)..."}),
        websocket, session_logger=session_logger)
    loop = asyncio.get_event_loop()

    t0 = loop.time()
    # Wire a thread-safe progress callback so the UI shows each search round.
    prev_cb = svc.research_engine.progress_callback

    def _progress_cb(stage: str, detail: dict):
        try:
            asyncio.run_coroutine_threadsafe(
                send_progress(svc, websocket, stage, detail), loop)
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            session_logger.log("research_progress_cb_failed", {"error": str(e)})

    svc.research_engine.progress_callback = _progress_cb
    try:
        report = await run_with_heartbeat(
            svc, websocket, "research", svc.research_engine.research, user_message)
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        svc.research_engine.progress_callback = prev_cb
        session_logger.log_exception(e, context="research_engine.research")
        await notify_problem(svc, websocket, e,
            context={"stage": "researching the web"},
            user_message=(
                "Something went wrong while researching the web for "
                "this topic. Your notes are safe."),
            remedy_hint="Try again in a minute, or rephrase your question.")
        return
    finally:
        svc.research_engine.progress_callback = prev_cb
    session_logger.log("deep_research", {
        "query": user_message,
        "source_count": report.get("source_count", 0),
        "facts": report.get("synthesis_facts", 0),
        "rounds": len(report.get("rounds", [])),
        "duration_ms": (loop.time() - t0) * 1000,
    })

    if not report.get("source_count"):
        from error_types import make_diagnosis, ProblemCategory, Severity
        _diag = make_diagnosis(
            ProblemCategory.GENERIC,
            user_message=(
                "I couldn't find any web sources for this topic. "
                "This might be a temporary issue with the search service."),
            remedy_hint="Try again in a minute, or rephrase your question.",
            severity=Severity.INFO)
        await notify_problem(svc, websocket, _diag)
        session_logger.log("research_error", {"stage": "search", "error": "no_sources"})
        return

    research_text = report.get("synthesis", "")
    if not research_text:
        research_text = " ".join(s.get("snippet", "") for s in report.get("sources", [])[:3])

    await svc.manager.send_personal_message(
        json.dumps({"type": "status", "content": "Creating linked note..."}),
        websocket, session_logger=session_logger)
    await send_progress(svc, websocket, "writing_note", {"topic": derive_topic(user_message)})

    try:
        topic = report.get("topic") or derive_topic(user_message)
        summary = (f"Deep research into '{topic}' "
                   f"({report.get('source_count', 0)} sources, "
                   f"{report.get('synthesis_facts', 0)} facts).")
        if len(summary) > 800:
            summary = summary[:797] + "..."
        note_path = await run_with_heartbeat(
            svc, websocket, "writing_note",
            svc.note_creator.create_note_from_research, topic, research_text, summary)
        # Overwrite with the richer markdown so sources + follow-ups persist.
        try:
            md = svc.research_engine.synthesize_note_markdown(report, summary)
            Path(note_path).write_text(md, encoding="utf-8")
        except Exception as e:
            session_logger.log("research_note_md_failed", {"error": str(e)})
            raise
        # LLM-assisted note structuring (ONE call): overwrite the extractive
        # markdown with a structured note (frontmatter, H2 sections,
        # wikilinks). Raises on failure — the extractive markdown is already
        # saved, but the user needs to know structuring failed.
        _titles = list(svc.vault_graph.nodes.keys())
        _structured = svc.research_engine.synthesize_structured_note(
            report, summary, ollama_client=svc.ollama_client,
            vault_note_titles=_titles)
        if _structured and len(_structured) >= svc.research_engine._STRUCTURED_MIN_CHARS:
            Path(note_path).write_text(_structured, encoding="utf-8")
            session_logger.log("research_note_structured",
                               {"note_path": note_path,
                                "chars": len(_structured)})
        session_logger.log("research_note_created", {"note_path": note_path, "topic": topic})
    except Exception as e:
        session_logger.log_exception(e, context="note_creator.create_note_from_research")
        await notify_problem(svc, websocket, e,
            context={"stage": "saving the research note"},
            user_message=(
                "I found web sources but couldn't save the research note "
                "to your vault. The research is in my memory for this "
                "chat, but it won't be saved permanently."),
            remedy_hint="Try restarting VaultBot and running the research again.")
        return

    await svc.manager.send_personal_message(
        json.dumps({"type": "status", "content": f"Created note: {Path(note_path).name}"}),
        websocket, session_logger=session_logger)
    session_logger.log("research_end", {"note_path": note_path})

    # Refresh graph after writing so subsequent chats see the updated vault state
    svc.vault_graph.refresh()
    await handle_chat(svc, websocket, user_message, session_logger)


def derive_topic(user_message: str) -> str:
    """Derive a concise note title from the user's research request."""
    cleaned = user_message.strip().rstrip("?").lower()
    for word in ["what is", "what are", "research", "tell me about", "explain", "define"]:
        cleaned = cleaned.replace(word, "")
    cleaned = cleaned.strip().title()
    return cleaned if cleaned else "Research Note"


# Kept for backward compatibility; graph context is now built by build_graph_context.
def build_context(results: list) -> str:
    if not results:
        return "VAULT CONTEXT: (no relevant notes found)"
    lines = ["VAULT CONTEXT:"]
    for i, res in enumerate(results, 1):
        file_path = res.get("file_path", "")
        note_name = Path(file_path).stem if file_path else "Unknown"
        lines.append(f"\n--- Note {i}: [[{note_name}]] ---")
        lines.append(res.get("content", "")[:1500])
    return "\n".join(lines)
