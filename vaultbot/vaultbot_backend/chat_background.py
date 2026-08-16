"""Fire-and-forget post-turn background work.

Extracted from ``chat_handler.py`` — ``run_background_tasks`` runs AFTER
the answer is delivered to the user. It performs: stress-signal logging
(for Dream Pass), vault-changed broadcast, embedding-drift feedback, model
relevance tagging, lazy condensing (background task), QA idle worker
(background task), history persistence, conversation-index add, working-
memory save, chat-note creation, and pattern extraction.

All of this is best-effort: failures are logged but never break the chat
loop. The user has already received their answer.

This is a leaf module in the chat-handler family (see ``chat_context.py``,
``chat_preflight.py``, ``chat_helpers.py`` for the established pattern).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from chat_helpers import (
    notify_console_failure,
    notify_problem,
)
from conversation_state import save_history
from last_session import touch as touch_last_session
from services import Services
from weaving import (
    cross_link_textbooks,
    existing_note_titles,
    link_outbound,
)
from working_memory import TaskList


async def run_background_tasks(
    svc: Services,
    websocket,
    session_logger,
    loop,
    user_message: str,
    final_answer: str,
    thinking_text: str,
    round_idx: int,
    _turn_token_totals: dict,
    _turn_failed_write_count: int,
    conversation: list,
    retrieved_paths: list,
    chat_start_time: float,
    wm: TaskList,
    _turn_tool_history: list,
    _findings: list,
) -> None:
    """Fire-and-forget post-turn work: stress signal, vault-changed,
    drift feedback, lazy condense, QA worker, history persistence, chat
    notes, pattern extraction.
    """
    # --- Stress signal: log intent + work summary for Dream Pass ---
    # Every turn emits a stress_signal event. Dream Pass reads these
    # to find high-effort manual work and create procedures that
    # handle it next time. No LLM call here — just raw signals.
    # The small model in Dream Pass does the intent+work summarization.
    try:
        _stress_tools = list(
            dict.fromkeys(e.get("tool", "?") for e in _turn_tool_history)
        )
        _stress_procedures = any("execute_procedure" in t for t in _stress_tools)
        _stress_manual = (
            not _stress_procedures
            and len(_stress_tools) > 0
            and _turn_token_totals.get("total_tokens", 0) > 2000
        )
        session_logger.log(
            "stress_signal",
            {
                "user_message": (user_message or "")[:500],
                "tools_used": _stress_tools,
                "tool_count": len(_stress_tools),
                "rounds": round_idx + 1,
                "findings": _findings[:20],
                "prompt_tokens": _turn_token_totals.get("prompt_tokens", 0),
                "completion_tokens": _turn_token_totals.get("completion_tokens", 0),
                "total_tokens": _turn_token_totals.get("total_tokens", 0),
                "failed_writes": _turn_failed_write_count,
                "answer_length": len(final_answer),
                "had_procedure_calls": _stress_procedures,
                "had_manual_work": _stress_manual,
            },
        )
    except Exception as _e:  # noqa: BLE001 — best-effort
        session_logger.log("stress_signal_failed", {"error": str(_e)})

    # --- Notify the Obsidian plugin that vault files may have changed ---
    try:
        changed_files = []
        vault_root = svc.vault_path
        for dirpath, dirnames, filenames in os.walk(vault_root):
            dirnames[:] = [
                d
                for d in dirnames
                if d
                not in (
                    ".obsidian",
                    "vaultbot/vaultbot_backend",
                    "node_modules",
                    ".git",
                    "vaultbot/learningMaterial",
                    "custom_tools",
                    "__pycache__",
                )
            ]
            for fname in filenames:
                if fname.endswith(".md"):
                    fpath = os.path.join(dirpath, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                        if mtime >= chat_start_time:
                            rel = os.path.relpath(fpath, vault_root)
                            changed_files.append(rel.replace(os.sep, "/"))
                    except OSError:
                        pass
        if changed_files:
            await svc.manager.send_personal_message(
                json.dumps({"type": "vault_changed", "files": changed_files}),
                websocket,
                session_logger=session_logger,
            )
            session_logger.log(
                "vault_changed_broadcast",
                {
                    "file_count": len(changed_files),
                },
            )
    except Exception as e:  # noqa: BLE001
        session_logger.log("vault_changed_failed", {"error": str(e)})

    # Embedding-drift feedback: nudge the stored embeddings of retrieved
    # notes toward (or away from) this query based on whether the context
    # was useful.
    if retrieved_paths:
        try:
            first_round_researched = round_idx > 0 and len(final_answer) < 200
            q_emb = await loop.run_in_executor(
                None, svc.vault_indexer._get_embedding, user_message
            )
            top_fp = retrieved_paths[0]
            if first_round_researched:
                svc.embedding_drift.record_feedback(top_fp, q_emb, helpful=False)
            elif len(final_answer) > 50:
                svc.embedding_drift.record_feedback(top_fp, q_emb, helpful=True)
            session_logger.log(
                "drift_feedback",
                {
                    "top_note": Path(top_fp).stem,
                    "helpful": (len(final_answer) > 50 and not first_round_researched),
                    "answer_len": len(final_answer),
                    "rounds": round_idx + 1,
                },
            )
        except Exception as e:  # noqa: BLE001
            session_logger.log("drift_feedback_failed", {"error": str(e)})
            await notify_console_failure(
                svc,
                websocket,
                f"embedding drift feedback failed: {e}",
                context="drift_feedback",
            )

    # --- Model self-assessment: tag retrieved notes as useful/neutral ----
    # This is the per-turn half of the trigger/inhibitor feedback loop.  For
    # each retrieved note, we check whether the final answer CITES it via a
    # [[wikilink]].  Cited → "useful"; uncited → "neutral".  "Harmful" is
    # NOT detectable heuristically (an uncited note wasn't necessarily
    # harmful — the model might just not have needed it) and is deferred to
    # user sentiment (the Dream-Pass update step pairs this event with the
    # user's next-message sentiment).
    #
    # Zero LLM calls: cite detection is a regex match (same pattern as the
    # grounding check).  The event is read offline by Dream-Trigger-
    # Inhibitor-Update, which pairs it with the next websocket_message
    # (direction "in") and classifies sentiment.
    if retrieved_paths:
        try:
            import re as _re

            _answer_links = set(
                _re.findall(
                    r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]", final_answer or ""
                )
            )
            _answer_links_lower = {l.strip().lower() for l in _answer_links}
            _tags = []
            for fp in retrieved_paths:
                stem = Path(fp).stem
                tag = "useful" if stem.strip().lower() in _answer_links_lower else "neutral"
                _tags.append({"path": fp, "stem": stem, "tag": tag})
            session_logger.log(
                "model_relevance_tags",
                {
                    "query": (user_message or "")[:500],
                    "tags": _tags,
                    "answer_length": len(final_answer or ""),
                    "rounds": round_idx + 1,
                },
            )
        except Exception as e:  # noqa: BLE001 — best-effort
            session_logger.log("model_relevance_tags_failed", {"error": str(e)})

    # Lazy de-fluff: after the answer is delivered, condense any retrieved
    # notes that have crossed the touch threshold.
    if retrieved_paths:

        async def _run_lazy_condense_bg():
            try:
                summary = await loop.run_in_executor(
                    None, svc.lazy_condenser.condense_batch, retrieved_paths
                )
                if not summary.get("condensed"):
                    return
                session_logger.log("lazy_condense_done", summary)
                from lazy_condenser import CONDENSE_MARKER

                condensed_paths = []
                for fp in retrieved_paths:
                    try:
                        if CONDENSE_MARKER in Path(fp).read_text(
                            encoding="utf-8", errors="replace"
                        ):
                            condensed_paths.append(fp)
                    except Exception:  # noqa: BLE001
                        continue
                if not condensed_paths:
                    return
                _n, new_embs = await loop.run_in_executor(
                    None, svc.vault_indexer.batch_add_files, condensed_paths, True
                )
                title_map = existing_note_titles(svc)
                for fp in condensed_paths:
                    try:
                        await loop.run_in_executor(None, link_outbound, fp, title_map)
                    except Exception as e:  # noqa: BLE001
                        session_logger.log(
                            "post_condense_linkoutbound_failed",
                            {"path": fp, "error": str(e)},
                        )
                source_keys = {str(Path(fp).resolve()) for fp in condensed_paths}
                try:
                    cross = await loop.run_in_executor(
                        None,
                        cross_link_textbooks,
                        svc,
                        condensed_paths,
                        new_embs,
                        source_keys,
                    )
                    session_logger.log(
                        "post_condense_relink",
                        {
                            "condensed": len(condensed_paths),
                            "cross_links": cross.get("cross_links_added", 0),
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    session_logger.log(
                        "post_condense_crosslink_failed", {"error": str(e)}
                    )
                    await notify_console_failure(
                        svc,
                        websocket,
                        f"post-condense cross-linking failed: {e}",
                        context="post_condense",
                    )
                try:
                    from concept_card import (
                        build_card_for,
                        card_path_for,
                        needs_refine,
                        refine_card,
                    )

                    for fp in condensed_paths:
                        card = card_path_for(fp)
                        if card.exists():
                            try:
                                old = card.read_text(encoding="utf-8", errors="replace")
                                from concept_card import REFINED_MARKER

                                if REFINED_MARKER not in old:
                                    build_card_for(fp, vault_graph=svc.vault_graph)
                            except Exception as e:  # noqa: BLE001
                                session_logger.log(
                                    "card_rebuild_failed",
                                    {"path": fp, "error": str(e)},
                                )
                        try:
                            svc.embedding_drift.reset(fp)
                            if card.exists():
                                svc.embedding_drift.reset(str(card))
                        except Exception as e:  # noqa: BLE001
                            session_logger.log(
                                "drift_reset_failed", {"path": fp, "error": str(e)}
                            )
                    refined = 0
                    for fp in retrieved_paths:
                        card = card_path_for(fp)
                        if not card.exists():
                            continue
                        try:
                            tc = svc.lazy_condenser.touch_counts.get(
                                str(Path(card).resolve()), 0
                            )
                        except Exception:  # noqa: BLE001
                            tc = 0
                        if needs_refine(card, tc):
                            r = await loop.run_in_executor(
                                None, refine_card, card, svc.ollama_client, None
                            )
                            if r.get("refined"):
                                refined += 1
                                await loop.run_in_executor(
                                    None,
                                    svc.vault_indexer.batch_add_files,
                                    [str(card)],
                                    False,
                                )
                                try:
                                    svc.embedding_drift.reset(str(card))
                                except Exception as e:  # noqa: BLE001
                                    session_logger.log(
                                        "drift_reset_failed",
                                        {"card": str(card), "error": str(e)},
                                    )
                    if refined:
                        session_logger.log("card_refine_done", {"refined": refined})
                except Exception as e:  # noqa: BLE001
                    session_logger.log("card_refine_failed", {"error": str(e)})
                    await notify_console_failure(
                        svc,
                        websocket,
                        f"card refinement failed: {e}",
                        context="card_refine",
                    )
            except Exception as e:  # noqa: BLE001
                session_logger.log("lazy_condense_bg_failed", {"error": str(e)})
                await notify_problem(
                    svc,
                    websocket,
                    e,
                    context={"stage": "condensing notes in the background"},
                    user_message=(
                        "Something went wrong while condensing long "
                        "notes in the background. This won't affect "
                        "your chat — your notes are safe."
                    ),
                    remedy_hint="",
                )

        asyncio.create_task(_run_lazy_condense_bg())

    # --- QA idle worker: fix note frontmatter while the user reads ---
    # After the answer is delivered, the user spends time reading and
    # typing their next message.  This idle window is when the QA worker
    # pulls notes from a priority queue (most-used first) and fixes
    # weak frontmatter (missing fields, weak summaries, generic tags).
    # The worker is interrupted the moment the user sends a new message
    # — in-flight note is completed, unprocessed notes stay queued.
    async def _run_qa_idle_bg():
        try:
            from qa_worker import run_qa_idle_window

            _qa_ollama = getattr(svc, "ollama_client", None)
            # Use the small model if available (cheaper for metadata gen)
            try:
                from llm_client import get_small_client

                _qa_ollama = get_small_client() or _qa_ollama
            except Exception:  # noqa: BLE001
                pass
            _qa_summary = await run_qa_idle_window(
                vault_root=svc.vault_path,
                ollama_client=_qa_ollama,
                logger=lambda msg: session_logger.log("qa_worker", {"msg": msg}),
            )
            session_logger.log("qa_idle_window_done", _qa_summary)
        except Exception as e:  # noqa: BLE001
            session_logger.log("qa_idle_bg_failed", {"error": str(e)})
            await notify_console_failure(
                svc,
                websocket,
                f"background QA worker failed: {e}",
                context="qa_worker",
            )

    asyncio.create_task(_run_qa_idle_bg())

    # Persist this turn into the per-session history.
    try:
        _persist_cap = int(os.getenv("VAULTBOT_HISTORY_MSG_CAP", "40000"))
        history = getattr(websocket, "conversation_history", None)
        if history is not None:
            new_turns = []
            for m in conversation:
                if m.get("role") == "system":
                    continue
                m2 = dict(m)
                m2.pop("thinking", None)
                c = m2.get("content")
                if isinstance(c, str) and len(c) > _persist_cap:
                    m2["content"] = c[:_persist_cap] + "\n[...truncated in history...]"
                new_turns.append(m2)
            if len(new_turns) > len(history):
                websocket.conversation_history = new_turns
                session_logger.log(
                    "history_persisted",
                    {
                        "turns": len(new_turns),
                        "history_chars": sum(
                            len(str(m.get("content", ""))) for m in new_turns
                        ),
                        "final_answer_len": len(final_answer or ""),
                    },
                )
                save_history(
                    new_turns, session_id=getattr(websocket, "session_id", None)
                )
                # Refresh the last-active-session pointer so a reconnect
                # or restart finds THIS session, not a stale one.
                _sid = getattr(websocket, "session_id", None)
                if _sid:
                    touch_last_session(_sid, session_logger.title)
            # Index this turn in the conversation index so future queries
            # can retrieve it (conversation-aware retrieval).  Only
            # index when there's a real answer — a tool-only or empty
            # turn isn't useful for recall.
            if final_answer and len(final_answer) > 20:
                try:
                    _conv_idx_reg = getattr(svc, "conversation_index", None)
                    if _conv_idx_reg is not None:
                        _sid = getattr(websocket, "session_id", None)
                        _conv_idx = _conv_idx_reg.get(_sid)
                        _conv_idx.add_turn(user_message, final_answer)
                except Exception as _e:  # noqa: BLE001
                    session_logger.log(
                        "conversation_index_add_failed", {"error": str(_e)}
                    )
            # Persist working memory to disk so the plan survives
            # restarts.  Only save when there's an active plan.
            try:
                if wm.has_plan():
                    wm.save_to_disk(session_id=getattr(websocket, "session_id", None))
            except Exception as _e:  # noqa: BLE001
                session_logger.log("wm_save_disk_failed", {"error": str(_e)})
    except Exception as e:  # noqa: BLE001
        session_logger.log("history_persist_failed", {"error": str(e)})
        await notify_problem(
            svc,
            websocket,
            e,
            context={
                "category": "history_lost",
                "stage": "persisting chat history",
            },
            user_message=(
                "I couldn't save our conversation history. "
                "If I restart, I won't remember this chat."
            ),
            remedy_hint="Check disk space and file permissions.",
        )

    # Save a chat note if the answer is substantive.
    if len(final_answer) > 100:
        try:
            note_path = await loop.run_in_executor(
                None,
                svc.note_creator.create_note_from_chat,
                user_message,
                final_answer,
                thinking_text,
            )
            session_logger.log("chat_note_created", {"note_path": note_path})
        except Exception as e:  # noqa: BLE001
            session_logger.log_exception(
                e, context="note_creator.create_note_from_chat"
            )
            print(f"Error creating chat note: {e}")

    # Pattern extraction: check for new consolidation gaps after each chat.
    try:
        _gaps = await loop.run_in_executor(
            None, svc.pattern_extractor.get_consolidation_gaps
        )
        if _gaps:
            session_logger.log(
                "consolidation_gaps",
                {
                    "gap_count": len(_gaps),
                    "top_gaps": [
                        {
                            "kind": g["kind"],
                            "topic": g["topic"],
                            "priority": g.get("priority", 0),
                        }
                        for g in _gaps[:5]
                    ],
                },
            )
    except Exception as e:  # noqa: BLE001
        session_logger.log("pattern_extraction_failed", {"error": str(e)})