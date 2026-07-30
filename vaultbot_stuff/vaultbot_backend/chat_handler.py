"""Agentic chat loop + tool dispatch — extracted from main.py.

This module owns the two biggest functions in the VaultBot backend:

- ``handle_chat`` — the "Jarvis loop": the LLM reasons over the vault,
  calls tools (research, search, gaps, status, self-improvement, textbook
  reads, custom agent-authored tools) when it hits a gap, and produces a
  grounded answer. Includes post-answer housekeeping (embedding-drift
  feedback, lazy de-fluff, history persistence, goal/self-model updates,
  pattern extraction).
- ``execute_agent_tool`` — single tool-call dispatcher for the chat LLM.

Both functions were lifted verbatim from ``main.py`` with two mechanical
transformations:

1. Each takes a ``svc: Services`` registry as its first parameter and
   reads the formerly-module-level singletons via attribute access
   (``svc.ollama_client``, ``svc.fused_retriever``, ...) instead of as
   free variables.
2. Calls back into ``main.py`` helper functions (``_send_progress``,
   ``_run_with_heartbeat``, ``_write_partial``, ``_tool_result_summary``,
   ``_weave_textbook_notes``, ``_existing_note_titles``, ``_link_outbound``,
   ``_cross_link_textbooks``) are deferred-imported inside the function
   body to break the ``main`` ↔ ``chat_handler`` import cycle
   (``main`` imports ``chat_handler`` at module init; if ``chat_handler``
   imported ``main`` at module init the cycle would explode).

The module-level imports here are leaf modules only (``services``,
``abstract_context``, ``agent_tools``, ``vault_graph``, ``procedure_tracker``,
``concept_card``, ``lazy_condenser``) — never ``main``.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from abstract_context import build_abstract_context
from agent_tools import (
    META_TOOL_DEFINITIONS, TOOL_DEFINITIONS, build_system_prompt,
    build_system_prompt_briefing,
)
from chat_checkpoint import snapshot_working_memory

# Leaf-module imports for helpers that were previously deferred-imported
# from main (circular). These are now direct leaf imports — no main dependency.
from chat_helpers import (
    run_with_heartbeat, send_progress, tool_result_summary, truncate_tool_result,
)
from conversation_state import save_history
from fastapi import WebSocket
from output_validator import corrective_message, validate_tool_call
from plan_gate import EXPLORE_TOOLS, is_multi_step, lifts_gate, plan_mode_directive
from procedure_surface import build_procedure_surface, status_allows_execution
from procedure_tracker import interpret_validation_result, parse_procedures_from_results
from services import Services
from task_api import write_partial
from vault_graph import build_graph_context
from weaving import (
    cross_link_textbooks,
    existing_note_titles,
    link_outbound,
    weave_textbook_notes,
)
from working_memory import TaskList


async def handle_chat(svc: Services, websocket: WebSocket,
                     user_message: str, session_logger) -> None:
    """Agentic chat: the LLM reasons over the vault, calls tools (research,
    search, gaps, status) when it hits a gap, and produces a grounded answer.

    This is the Jarvis loop — the LLM self-directs instead of shrugging.
    """
    # Module-level imports from chat_helpers, task_api, weaving — no longer
    # deferred from main (circular dependency eliminated).
    session_logger.log("chat_begin", {"user_message": user_message})

    # Working memory: per-session structured task list (the Copilot/Claude
    # Code TodoList pattern). The model writes a plan via the plan_task tool
    # and updates it via update_task; the harness re-injects the list into
    # the system prompt every round so the model never loses the plot to
    # compaction. One TaskList per websocket connection, reset on /new.
    if not hasattr(websocket, "working_memory") or websocket.working_memory is None:
        websocket.working_memory = TaskList()
    wm = websocket.working_memory

    # Chat-loop checkpoint/resume (multi-day sturdiness): if a prior turn was
    # interrupted mid-loop (crash/restart) and left a fresh checkpoint, resume
    # it — restore the working-memory plan and tell the model what it already
    # did so it doesn't re-run tools. Cleared on normal completion and /new.
    _cp = getattr(svc, "chat_checkpointer", None)
    _resumed_tool_history: list = []
    if _cp is not None:
        try:
            _prior = _cp.load()
            if _prior and _prior.get("user_message") == user_message:
                _resumed_tool_history = _prior.get("tool_history", []) or []
                _wm_snap = _prior.get("working_memory") or {}
                if _wm_snap and not wm.has_plan():
                    try:
                        wm.restore_snapshot(_wm_snap)
                    except Exception:
                        pass
                session_logger.log("chat_checkpoint_resumed", {
                    "round_idx": _prior.get("round_idx", 0),
                    "tools_already_run": len(_resumed_tool_history),
                })
        except Exception as e:
            session_logger.log("chat_checkpoint_resume_failed", {"error": str(e)})

    # Chat-priority: pause the autonomous researcher so it doesn't compete
    # with this interactive turn for the Ollama GPU. On a single-GPU laptop
    # the user's embedding + LLM calls would otherwise queue behind the
    # researcher's background synthesis, making the chat appear to hang.
    # Resumed in the finally block below so it always clears (even on
    # cancel/error). The researcher skips its cycle while this is set.
    svc.autonomous_researcher.pause_for_chat()
    try:

        # Calibration: detect if this message is a correction of the previous
        # answer. the operator's corrections are ground truth for calibrating automated
        # quality gates. See [[Calibration-via-Operator-Feedback]].
        try:
            _prev_history = getattr(websocket, "conversation_history", None)
            _prev_answer = None
            if _prev_history:
                for _msg in reversed(_prev_history):
                    if _msg.get("role") == "assistant" and _msg.get("content"):
                        _prev_answer = _msg["content"]
                        break
            if _prev_answer and svc.calibration_tracker.detect_correction(user_message, _prev_answer):
                _ftype = svc.calibration_tracker.classify_failure(user_message, _prev_answer)
                svc.calibration_tracker.log_correction(
                    user_message, _prev_answer, failure_type=_ftype)
                session_logger.log("correction_detected", {"failure_type": _ftype})
        except Exception as e:
            session_logger.log("correction_detection_failed", {"error": str(e)})
        await svc.manager.send_personal_message(json.dumps({"type": "status", "content": "Searching vault..."}), websocket, session_logger=session_logger)
        loop = asyncio.get_event_loop()
        chat_start_time = loop.time()  # for vault_changed file scan

        # Keep the in-memory vault graph current with disk before retrieval.
        # The intended design was an incremental diff (cost proportional to
        # changed files); VaultGraph.refresh() is now mtime-gated and only re-reads
        # files that changed since the last refresh, so the common no-edit case is
        # a cheap stat-only scan and notes created/edited earlier in the chat still
        # surface. This keeps the vault graph current with disk before retrieval.
        try:
            _t_graph = loop.time()
            await loop.run_in_executor(None, svc.vault_graph.refresh)
            session_logger.log("graph_refreshed", {
                "node_count": len(svc.vault_graph.nodes),
                "duration_ms": (loop.time() - _t_graph) * 1000,
            })
        except Exception as e:
            session_logger.log_exception(e, context="graph_refresh")

        t0 = loop.time()
        try:
            # Heartbeat-wrapped: the fused retriever embeds the query via Ollama,
            # which can stall when the autonomous researcher is also using Ollama.
            # Without a heartbeat the GUI freezes on "Searching vault..." with no
            # feedback. This pushes a "still alive / elapsed" pulse every 2s so
            # the operator always sees the backend is working, not hung.
            fused_result = await run_with_heartbeat(
                svc, websocket, "retrieving vault",
                svc.fused_retriever.retrieve, user_message, 5, 1)
            results = fused_result.get("results", []) if isinstance(fused_result, dict) else (fused_result or [])
        except Exception as e:
            session_logger.log_exception(e, context="fused_retriever.retrieve")
            # Degrade gracefully to flat vector search.
            try:
                results = await run_with_heartbeat(
                    svc, websocket, "retrieving vault (fallback)",
                    svc.vault_indexer.search, user_message, 5)
            except Exception:
                results = []
        session_logger.log("vault_search", {
            "query": user_message,
            "k": 5,
            "result_count": len(results),
            "duration_ms": (loop.time() - t0) * 1000,
            "retriever": "fused",
        })

        # RAG evaluation: log retrieval results for every query (cheap, always on).
        # Metrics are computed on-demand when ground truth is available.
        # See [[RAG-Evaluation-for-FUSED-Retrieval]].
        try:
            svc.rag_evaluator.log_retrieval(user_message, results, k=5)
        except Exception as e:
            session_logger.log("rag_eval_log_failed", {"error": str(e)})

        # Lazy-condenser touch tracking: record that each retrieved note was
        # queried.  Notes that cross the touch threshold (3+) AND are still long
        # get de-fluffed in the background after the answer is delivered — this
        # is the "de-fluff over time as pages are queried" behavior.  Never
        # raises; a failure here must not break the chat.
        retrieved_paths = []
        try:
            for r in results:
                fp = r.get("file_path") if isinstance(r, dict) else None
                if fp:
                    retrieved_paths.append(fp)
                    svc.lazy_condenser.note_touched(fp)
            # Persist the batched touch counts once per chat turn, not once per
            # retrieved note (each note_touched() only marks the dict dirty).
            svc.lazy_condenser.flush_touch_counts()
        except Exception as e:
            session_logger.log("lazy_condenser_touch_failed", {"error": str(e)})

        # Procedure context tracking: which procedural notes were in the vault
        # context for this turn? Used to log validation results against them.
        procedures_in_context = parse_procedures_from_results(results)
        if procedures_in_context:
            session_logger.log("procedures_in_context", {
                "procedures": procedures_in_context,
            })

        # Multi-resolution context: L2 MOC (bird's-eye) + L1 concept cards
        # (the thought highway — terse, hop-able) + L0 drill-down (full raw of
        # the single top seed only).  Replaces the old `build_graph_context`
        # content dump, which truncated every note to its first 2000 chars and
        # flooded the context with low-density detail.  Falls back to the legacy
        # builder if no L1 cards exist yet (pre-hierarchy vault regions).
        try:
            abs_ctx = await run_with_heartbeat(
                svc, websocket, "building context",
                build_abstract_context, svc.vault_graph, results,
                user_message, 5, 2, None)
            context = abs_ctx.get("context", "")
            session_logger.log("context_resolution", {
                "resolution": abs_ctx.get("resolution"),
                "l1_cards": abs_ctx.get("l1_cards", 0),
                "drill_down_used": abs_ctx.get("drill_down_used", False),
                "l0_drill": abs_ctx.get("l0_drill"),
                "context_length": len(context)})
        except Exception as e:
            session_logger.log_exception(e, context="build_abstract_context")
            context = build_graph_context(svc.vault_graph, results, user_message, k=5, depth=2)

        # Context budgeting: ensure the retrieved context fits within the
        # model's token budget. Truncates from the end (lowest-priority L0
        # drill-down detail) if the context would overflow the context window.
        # Pure deterministic -- no LLM calls. Graceful degradation: if the
        # budgeter fails, the original context is used unchanged.
        try:
            _budgeted = svc.context_budgeter.budget(
                context, getattr(websocket, "conversation_history", []))
            context = _budgeted["context"]
            if _budgeted["truncated"]:
                session_logger.log("context_budget", {
                    "original_tokens": _budgeted["original_tokens"],
                    "budgeted_tokens": _budgeted["budgeted_tokens"],
                    "budget": _budgeted["budget"],
                    "chars_dropped": _budgeted["chars_dropped"],
                })
        except Exception as e:
            session_logger.log("context_budget_failed", {"error": str(e)})

        # Inject the identity boot context so the agent wakes up coherent across
        # days regardless of which model is in the slot (IDENTITY + SELF_MODEL +
        # GOALS, delivered verbatim before the first turn — MIRROR/Letta pattern).
        identity_context = svc.identity.boot_context()

        # Gather live state so the system prompt is a real briefing, not static.
        autonomous_state = svc.autonomous_researcher.status()
        try:
            _t_gaps = loop.time()
            gaps = await run_with_heartbeat(
                svc, websocket, "finding gaps",
                svc.knowledge_curriculum.propose_next_gaps, 10)
            session_logger.log("gaps_proposed", {
                "gap_count": len(gaps),
                "duration_ms": (loop.time() - _t_gaps) * 1000,
            })
        except Exception:
            gaps = []
        gaps_summary = "\n".join(
            f"- [{g.get('kind')}] {g.get('topic')} (priority {g.get('priority', 0)})"
            for g in gaps[:10]) or "(none detected)"

        # Build the combined tool list: built-in vault tools + meta-tools (self-
        # improvement) + any agent-authored custom tools currently loaded.
        custom_schemas = svc.self_improver.custom_tool_schemas()
        custom_tool_names = [s["function"]["name"] for s in custom_schemas]
        all_tools = TOOL_DEFINITIONS + META_TOOL_DEFINITIONS + custom_schemas
        custom_tools_desc = "\n".join(
            f"- {s['function']['name']}: {s['function']['description'][:100]}"
            for s in custom_schemas) if custom_schemas else "(none yet)"

        # Build the DYNAMIC per-turn system prompt WITHOUT the vault context.
        # The briefing (identity + instructions + tool schemas + live state +
        # gaps) is rebuilt fresh every turn so newly-created tools and edits
        # appear immediately — the VaultBot is meant to change itself. The vault
        # context (retrieved subgraph for THIS query) is injected as a SEPARATE
        # message below so the compactor can trim it independently without
        # shredding recent conversation turns. This separation is the fix for
        # "losing the plot / redoing old prompts": bundling the context into
        # the system prompt made the sacred head 113K chars, so the compactor's
        # 80K cap shredded recent turns to 200-char fragments while leaving the
        # bloated head (with days-old goals) intact.
        system_prompt = (identity_context + "\n\n" +
                          build_system_prompt_briefing(
                              autonomous_state, gaps_summary,
                              custom_tools=custom_tools_desc,
                              custom_tool_names=custom_tool_names))
        # Inject the working-memory task list so the model sees its active plan
        # every round. This is the Copilot/Claude Code TodoList pattern: the list
        # lives outside the conversation, so compaction can't shred it and the
        # model always knows "what's done, what's next." render_for_prompt
        # returns "" when there's no active plan (simple Q&A is unaffected).
        wm_block = wm.render_for_prompt()
        if wm_block:
            system_prompt = system_prompt + "\n\n" + wm_block

        # Procedure Discovery Service: surface the COMPACT description lines for
        # any procedures that FUSED retrieval matched for THIS query — one line
        # each (description + when-to-use + status), NOT the full procedure body.
        # This is the "agent only knows about the procedures it needs" insight:
        # the model sees a one-line capability and calls execute_procedure(name),
        # instead of reading a 3KB body and deciding. Appended to the protected
        # briefing message [0] (rebuilt fresh each turn) so the compactor can't
        # shred it. Empty when no procedures matched (simple Q&A) — zero tokens.
        try:
            _proc_idx = getattr(svc.procedure_tracker, "_stem_index", None)
            _proc_surface = build_procedure_surface(results, _proc_idx)
            if _proc_surface:
                system_prompt = system_prompt + "\n\n" + _proc_surface
                session_logger.log("procedure_surface", {
                    "lines": _proc_surface.count("\n"),
                })
        except Exception as e:
            session_logger.log("procedure_surface_failed", {"error": str(e)})

        # If we're resuming an interrupted turn, tell the model what it already
        # did so it continues instead of re-running tools. Compact, one line per
        # completed tool round. Appended to the protected briefing.
        if _resumed_tool_history:
            _lines = ["# RESUMED TURN (you were interrupted mid-task and are "
                      "continuing — do NOT re-run these tools, build on them):"]
            for _h in _resumed_tool_history[-15:]:
                if isinstance(_h, dict):
                    _lines.append(
                        f"- round {_h.get('round', '?')}: {_h.get('tool', '?')}"
                        f" → {_h.get('result_summary', '')[:120]}")
            system_prompt = system_prompt + "\n\n" + "\n".join(_lines)

        session_logger.log("prompt_built", {
            "system_prompt_length": len(system_prompt),
            "vault_context_length": len(context),
            "context_length": len(context),
            "gaps_reported": len(gaps),
            "custom_tools": len(custom_schemas),
            "total_tools": len(all_tools),
        })

        # Build the conversation for /api/chat using PERSISTENT per-session history.
        # This is the amnesia fix: prior turns (user + assistant + tool exchanges)
        # carry over within the same websocket session, so corrections and
        # context survive. History lives on websocket.conversation_history.
        #
        # LAYOUT (the separation that keeps recent turns intact):
        #   [0] system   = identity + briefing (rebuilt fresh each turn; small,
        #                  stable, ~8-12K chars; protected by the compactor's
        #                  keep_head as sacred — this is the agent's identity)
        #   [1] system   = "# VAULT CONTEXT (retrieved for this query)\n..." —
        #                  the retrieved subgraph for THIS query. Compactable:
        #                  if it's large the compactor summarizes it along with
        #                  old conversation, NEVER shredding recent turns.
        #   [2..]        = prior history (user/assistant/tool) + this turn's user
        #   The compactor's keep_head=2 protects [0]+[1] as the head; the body
        #   ([2..]) is what gets compacted, so recent turns survive.
        conversation = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": (
                "# VAULT CONTEXT (retrieved for this query; compactable)\n"
                + context
            )},
        ]
        # Append the prior turns (no system prompt — that's rebuilt above each
        # turn so the agent always sees current vault state + gaps).
        conversation.extend(getattr(websocket, "conversation_history", []))
        # Add this turn's user message.
        conversation.append({"role": "user", "content": user_message})

        # Compact if the conversation is getting long (OpenHands Condenser pattern).
        # Prevents context overflow on long chats; keeps head + tail verbatim,
        # summarizes the middle. Now this actually has something to compact.
        if svc.compactor.should_compact(conversation):
            conversation = svc.compactor.compact(conversation)
            session_logger.log("context_compacted", {"messages": len(conversation)})

        # --- Token-usage meter: report how full the context window is ----------
        # Estimates the token cost of the full conversation (system prompts +
        # vault context + history + this turn) and emits a context_usage event
        # so the plugin can render a live meter that maxes out at the model's
        # context window. Uses the same ~4 chars/token heuristic as the budgeter.
        # Best-effort: never blocks the turn on a meter update.
        try:
            _total_chars = sum(len(str(m.get("content", "") or ""))
                               for m in conversation if isinstance(m, dict))
            _used_tokens = max(1, _total_chars // 4)
            _ctx_window = svc.ollama_client.context_window(svc.ollama_client.llm_model)
            await svc.manager.send_personal_message(json.dumps({
                "type": "context_usage",
                "model": svc.ollama_client.llm_model,
                "context_window": _ctx_window,
                "used_tokens": _used_tokens,
                "available_tokens": max(0, _ctx_window - _used_tokens),
                "messages": len(conversation),
            }), websocket, session_logger=session_logger)
        except Exception as _e:
            session_logger.log("context_usage_emit_failed", {"error": str(_e)})

        await svc.manager.send_personal_message(json.dumps({"type": "status", "content": "Thinking..."}), websocket, session_logger=session_logger)

        # --- Agentic loop: reason → tool call → execute → feed back → repeat --- #
        # No cap on rounds/tool calls: the agent loops until it produces a final
        # answer (a turn with no tool calls) or the loop crashes.
        final_answer = ""
        thinking_text = ""
        total_chunks = 0
        t0 = loop.time()
        # Per-turn record of tools run this turn (for the chat-loop checkpoint).
        # Seeded with any resumed history so a continued turn keeps its prior
        # rounds; appended after each tool result below.
        _turn_tool_history: list = list(_resumed_tool_history)

        # DETERMINISTIC LOOP DETECTOR (Copilot/Claude "verify each step" pattern):
        # track consecutive read-only rounds. If the model only explores
        _READ_ONLY_TOOLS = frozenset(EXPLORE_TOOLS) | {"vault_list"}
        _tool_rounds_executed = 0
        _empty_answer_retried = False
        _synthesize_requested = False  # empty-answer guard: only retry once per turn
        # No round cap, no loop detector, no pre-flight char cap — the operator asked for
        # all artificial limits removed. The model has a 1M-token context window;
        # let it work as long as it needs. The compactor is disabled too.
        _MAX_TOOL_ROUNDS_NO_PLAN = int(os.getenv("VAULTBOT_MAX_TOOL_ROUNDS_NO_PLAN", "5"))
        _FORCE_PLAN_ON_MULTI = os.getenv("VAULTBOT_FORCE_PLAN_ON_MULTI", "on").lower() != "off"

        _is_multi = bool(is_multi_step(user_message) and not _resumed_tool_history)

        # PLAN-MODE GATE (Copilot/Claude Code Explore→Plan→Implement): if this
        # turn looks multi-step and no plan exists yet, the model is restricted to
        # read-only/explore tools until it calls plan_task. Deterministic (no LLM)
        # via plan_gate.is_multi_step. The gate lifts the moment plan_task fires.
        _gate_active = bool(_is_multi and not wm.has_plan())
        if _gate_active:
            session_logger.log("plan_gate_active", {"user_message_head": user_message[:80]})

        # Partial-answer crash protection: write the streamed-so-far answer to a
        # temp file so a crash mid-stream doesn't lose it. On normal completion
        # the file is deleted; on crash, it survives and the next session can
        # surface it ("You were answering 'X' when I crashed — here's what I had:").
        #
        # The partial dir lives OUTSIDE the vault (in the OS temp dir) so that
        # Obsidian's file-recovery core plugin — which snapshots every .md file
        # inside the vault — doesn't race the backend's delete and spam the
        # console with ENOENT errors. The old in-vault location
        # (vaultbot_backend/partials/) is cleaned up at startup.
        import hashlib
        import tempfile
        import time as _time
        partial_dir = Path(tempfile.gettempdir()) / "vaultbot_partials"
        partial_dir.mkdir(parents=True, exist_ok=True)
        partial_id = hashlib.md5((user_message + str(_time.time())).encode()).hexdigest()[:12]
        partial_path = partial_dir / f"partial_{partial_id}.md"
        write_partial(partial_path, user_message, "", "")  # create the file immediately

        try:
         round_idx = 0
         # No round cap — the model works until it's done. the operator explicitly
         # asked for all caps removed. The model has a 1M-token context window.
         while True:
            session_logger.log("round_loop_top", {
                "round": round_idx, "t_ms": loop.time() * 1000,
                "conv_msgs": len(conversation),
            })
            # Refresh the working-memory block in the system prompt every round
            # so the model always sees the current task list (with the latest
            # completed/in_progress marks). The system prompt is conversation[0];
            # we rebuild it from the stable briefing + the live wm snapshot.
            # This is the Copilot/Claude Code pattern: the task list is re-injected
            # every turn so the model can't lose it to compaction.
            try:
                _wm_block = wm.render_for_prompt()
                if _wm_block:
                    conversation[0] = {"role": "system", "content": system_prompt + "\n\n" + _wm_block}
                else:
                    conversation[0] = {"role": "system", "content": system_prompt}
            except Exception:
                pass  # never let a wm render failure break the chat loop

            # Plan-mode gate: while active (multi-step, no plan yet), overlay the
            # plan-mode directive onto the working system message [0] so the model
            # explores + plans before executing. This edits only conversation[0]
            # (rebuilt every round), never appends to the shared history — so the
            # directive never duplicates and vanishes once the gate lifts.
            if _gate_active and not wm.has_plan():
                try:
                    conversation[0] = {
                        "role": "system",
                        "content": conversation[0].get("content", "")
                                   + "\n\n" + plan_mode_directive(),
                    }
                except Exception:
                    pass

            # Stream the LLM response for this round.
            round_text = ""
            round_thinking = ""
            round_tool_calls = []
            chunk_count = 0
            session_logger.log("llm_stream_start", {
                "round": round_idx, "conv_msgs": len(conversation),
                "conv_chars": sum(len(str(m.get("content","") or "")) for m in conversation),
                "t_ms": loop.time() * 1000,
            })
            try:
                def sync_stream():
                    session_logger.log("ollama_chat_call_enter", {
                        "round": round_idx, "t_ms": time.time() * 1000,
                    })
                    for chunk in svc.ollama_client.chat(conversation, tools=all_tools, stream=True):
                        yield chunk
                    session_logger.log("ollama_chat_call_exit", {
                        "round": round_idx, "t_ms": time.time() * 1000,
                    })
                gen = sync_stream()
                round_t0 = loop.time()
                last_chunk_at = loop.time()
                while True:
                    # Fetch the next chunk with a timeout so we can emit a
                    # heartbeat while the model is silent (e.g. still loading the
                    # model into memory, or in a long thinking pause before the
                    # first token). This is what kills the black-box feeling:
                    # even with zero output, the user sees "still thinking, 8s".
                    next_chunk_task = loop.run_in_executor(None, lambda: next(gen, {"done": True}))
                    chunk = None
                    while chunk is None:
                        try:
                            chunk = await asyncio.wait_for(
                                asyncio.shield(next_chunk_task), timeout=3.0)
                        except TimeoutError:
                            elapsed = int((loop.time() - round_t0) * 1000)
                            since = int((loop.time() - last_chunk_at) * 1000)
                            await svc.manager.send_personal_message(json.dumps({
                                "type": "heartbeat", "label": f"thinking (round {round_idx+1})",
                                "elapsed_ms": elapsed, "silent_ms": since,
                                "chunks": chunk_count,
                            }), websocket, session_logger=session_logger)
                            # Loop back: shield kept the task alive, retry the wait.
                        except asyncio.CancelledError:
                            # Interrupt (stop button / new message): close the
                            # Ollama generator so the backend thread stops pulling
                            # tokens, then re-raise so the outer handler exits.
                            gen.close()
                            raise
                    if chunk.get("done") and not chunk.get("response") and not chunk.get("tool_calls"):
                        break
                    chunk_count += 1
                    total_chunks += 1
                    last_chunk_at = loop.time()
                    thinking = chunk.get("thinking", "")
                    text = chunk.get("response", "")
                    tcs = chunk.get("tool_calls", [])
                    if thinking:
                        round_thinking += thinking
                        thinking_text += thinking
                        await svc.manager.send_personal_message(json.dumps({"type": "thinking", "content": thinking}), websocket, session_logger=session_logger)
                    if text:
                        round_text += text
                        await svc.manager.send_personal_message(json.dumps({"type": "answer_chunk", "content": text}), websocket, session_logger=session_logger)
                        # Update the partial-answer file so a crash mid-stream
                        # preserves whatever was streamed so far.
                        write_partial(partial_path, user_message, final_answer + round_text, thinking_text)
                    if tcs:
                        round_tool_calls.extend(tcs)
            except Exception as e:
                session_logger.log_exception(e, context="ollama_client.chat")
                # Don't drop the turn — salvage whatever was streamed so far so
                # the user's message + any tool work + partial answer is
                # persisted to history and the agent can recover on the next
                # turn. This is the tank-grade recovery: a transient cloud
                # timeout (Read timed out) shouldn't lose the whole turn.
                if round_text:
                    conversation.append({"role": "assistant", "content": round_text})
                final_answer = (final_answer + round_text).strip()
                # Translate the raw LLM error into a typed problem so the user
                # sees a remedy card (e.g. ollama_down with a Restart hint)
                # instead of "LLM error: <traceback>". The partial answer is
                # still preserved above + in the partial file.
                from diagnostics import classify_error
                diag = classify_error(e, {"stage": "thinking"})
                await svc.manager.send_personal_message(
                    json.dumps({"type": "problem", "diagnosis": diag.to_dict()}),
                    websocket, session_logger=session_logger)
                break

            session_logger.log("agent_round", {
                "round": round_idx,
                "chunk_count": chunk_count,
                "text_length": len(round_text),
                "tool_calls": len(round_tool_calls),
            })

            # Append the assistant's turn to the conversation so the next round
            # sees the full history (including thinking for Qwen-style models).
            assistant_msg = {"role": "assistant", "content": round_text}
            if round_thinking:
                assistant_msg["thinking"] = round_thinking
            if round_tool_calls:
                assistant_msg["tool_calls"] = round_tool_calls
            conversation.append(assistant_msg)

            # No tool calls → the LLM produced a final answer. We're done.
            if not round_tool_calls:
                # Empty-answer guard: if the model produced thinking but NO
                # user-facing text and no tool calls, it went silent — likely
                # because compaction ate its tool results and it has nothing to
                # say. Don't send an empty answer_done (the user sees nothing =
                # "bot stopped"). Instead, inject a system nudge telling the
                # model to respond to the user with whatever it knows, and give
                # it one more round. This only fires once per turn.
                if not round_text.strip() and round_thinking.strip():
                    if not _empty_answer_retried:
                        _empty_answer_retried = True
                        session_logger.log("empty_answer_retry", {
                            "round": round_idx,
                            "thinking_length": len(round_thinking),
                        })
                        conversation.append({
                            "role": "system",
                            "content": (
                                "You produced reasoning but no answer to the user. "
                                "Do NOT call any more tools. Based on everything you "
                                "know so far — including your reasoning above and any "
                                "tool results in your history — write a direct response "
                                "to the user now. If you don't have enough information, "
                                "say so plainly and explain what you need."),
                        })
                        round_idx += 1
                        continue
                if _synthesize_requested:
                    await svc.manager.send_personal_message(json.dumps({
                        "type": "status",
                        "content": "Synthesizing final answer.",
                    }), websocket, session_logger=session_logger)
                final_answer = round_text
                break

            _round_tool_names = []
            for _tc in round_tool_calls:
                _fn = _tc.get("function", {})
                _round_tool_names.append(_fn.get("name", ""))
            _tool_rounds_executed += 1

            # FRAMEWORK-DRIVEN PLAN (the Copilot/Claude enforcement the model can't
            # skip): on a multi-step task, if the model has used several tool
            # rounds WITHOUT writing a plan, the framework forces plan mode ON —
            # it doesn't wait for the model to volunteer. This is the gap the operator's
            # session fell into: the gate never fired on a signal-less multi-step
            # message, so no plan meant no all_done() stop and a 20-round spin.
            if (_FORCE_PLAN_ON_MULTI and _is_multi and not wm.has_plan()
                    and not _gate_active
                    and _tool_rounds_executed >= _MAX_TOOL_ROUNDS_NO_PLAN):
                _gate_active = True
                session_logger.log("plan_gate_forced", {
                    "round": round_idx, "reason": "no_plan_after_tool_rounds"})

            # Accumulate non-final round text into final_answer so the partial
            # file captures all streamed text across rounds, not just the last.
            final_answer += round_text

            # Execute each tool call and feed results back as tool-role messages.
            for tc in round_tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                tool_args_raw = fn.get("arguments", "{}")
                try:
                    tool_args = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
                except json.JSONDecodeError:
                    tool_args = {}
                tool_call_id = tc.get("id", tool_name)

                # Output validation (deterministic scaffolding): check the model's
                # tool args against the declared JSON schema BEFORE executing.
                # Small models emit malformed calls (missing required, wrong types,
                # hallucinated arg names); executing them crashes the tool or does
                # the wrong thing silently. A malformed call is rejected with a
                # precise corrective message so the model fixes + retries, and the
                # broken call NEVER runs. Skipped for the JSON-decode fallback {}
                # (already empty) — validation only fires on a parsed dict.
                if isinstance(tool_args, dict) and tool_args:
                    _problems = validate_tool_call(tool_name, tool_args, all_tools)
                    if _problems:
                        session_logger.log("tool_call_invalid", {
                            "tool": tool_name, "problems": _problems,
                            "round": round_idx})
                        conversation.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": json.dumps(
                                corrective_message(tool_name, _problems), default=str),
                        })
                        continue  # do NOT execute a malformed call

                # Plan-mode gate enforcement: while the gate is active, only
                # read-only/explore tools + plan_task may run. Execution tools are
                # blocked with a corrective message so the model redirects to
                # planning instead of mutating. plan_task LIFTS the gate.
                if _gate_active:
                    if lifts_gate(tool_name):
                        _gate_active = False
                        session_logger.log("plan_gate_lifted", {"via": tool_name})
                    elif tool_name not in EXPLORE_TOOLS:
                        session_logger.log("plan_gate_blocked", {
                            "tool": tool_name, "round": round_idx})
                        conversation.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": json.dumps({
                                "error": f"plan-mode: '{tool_name}' changes state and "
                                         "cannot run before a plan exists.",
                                "plan_mode": True,
                                "action_required": "call plan_task with a goal + "
                                                   "steps first, then re-run this tool.",
                            }, default=str),
                        })
                        continue  # skip execution; next tool call

                await svc.manager.send_personal_message(json.dumps({
                    "type": "tool_call", "tool": tool_name, "args": tool_args
                }), websocket, session_logger=session_logger)
                session_logger.log("tool_call_requested", {
                    "tool": tool_name, "args": tool_args, "round": round_idx,
                })

                t_tool0 = loop.time()
                session_logger.log("tool_exec_enter", {
                    "tool": tool_name, "round": round_idx,
                    "t_ms": t_tool0 * 1000,
                })
                try:
                    tool_result = await execute_agent_tool(
                        svc, tool_name, tool_args, session_logger, websocket,
                        user_message=user_message)
                except Exception as e:
                    session_logger.log_exception(e, context=f"tool_{tool_name}")
                    tool_result = {"error": str(e)}
                session_logger.log("tool_exec_exit", {
                    "tool": tool_name, "round": round_idx,
                    "duration_ms": (loop.time() - t_tool0) * 1000,
                })
                # If the agent just created a tool, refresh the tool list so the
                # new tool is callable in the very next round.
                if tool_name == "tool_create":
                    custom_schemas = svc.self_improver.custom_tool_schemas()
                    all_tools = TOOL_DEFINITIONS + META_TOOL_DEFINITIONS + custom_schemas
                tool_duration = (loop.time() - t_tool0) * 1000
                session_logger.log("tool_call_result", {
                    "tool": tool_name, "duration_ms": tool_duration,
                    "result_keys": list(tool_result.keys()) if isinstance(tool_result, dict) else None,
                })

                # Procedure tracking: log validation results against procedures
                # that were in context for this turn. This is the deterministic
                # feedback loop -- no LLM judgment, just structured logging.
                if tool_name in ("vault_lint", "safe_write", "code_run"):
                    try:
                        v_result, v_category, v_details = interpret_validation_result(
                            tool_name, tool_result)
                        proc_name = procedures_in_context[0] if procedures_in_context else "no_procedure"
                        # When no procedure is in context, use the user's message
                        # (truncated) as the task description so get_procedural_gaps()
                        # can group failures by what was actually being attempted,
                        # not by which tool happened to catch the failure.
                        _task_desc = user_message[:100] if proc_name == "no_procedure" else tool_name
                        svc.procedure_tracker.log_result(
                            procedure=proc_name,
                            task=_task_desc,
                            validation_result=v_result,
                            validation_tool=tool_name,
                            error_details=v_details,
                            category=v_category,
                        )
                    except Exception as e:
                        session_logger.log("procedure_tracking_failed", {"error": str(e)})
                await svc.manager.send_personal_message(json.dumps({
                    "type": "tool_result", "tool": tool_name,
                    "summary": tool_result_summary(tool_name, tool_result),
                }), websocket, session_logger=session_logger)

                # Cap the tool result before appending. Uncapped results
                # (vault_research syntheses, code_read of large files, graph
                # dumps) can be 50K+ chars; appended verbatim they balloon the
                # conversation past the compaction threshold every round. The
                # compactor then shreds recent user/assistant turns to 200-char
                # fragments while leaving the bloated result intact, and the
                # agent loses the thread — redoing old work because the recent
                # turns are the ones getting truncated. Bounding each result
                # keeps the agentic loop's context bounded and preserves the
                # recent turns the model needs to stay on track.
                capped_result = truncate_tool_result(tool_result)
                conversation.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(capped_result, default=str),
                })
                # Record for the chat-loop checkpoint (compact summary only).
                _turn_tool_history.append({
                    "round": round_idx,
                    "tool": tool_name,
                    "result_summary": (tool_result_summary(tool_name, tool_result) or "")[:200],
                })

            # Mid-loop compaction: the agentic loop adds 2+ messages per tool
            # round (assistant + tool result). Over many rounds the conversation
            # balloons to 100K+ chars even though it started compacted. A remote
            # cloud model (glm-5.2:cloud) takes >120s to process a 400K-char
            # payload before the first token, hitting the read timeout. Re-compact
            # here so the next LLM round sees a bounded conversation, not a
            # snowballing one. This is the fix for "read timed out" after a few
            # turns of tool use.
            if svc.compactor.should_compact(conversation):
                _compact_t0 = loop.time()
                session_logger.log("mid_loop_compact_enter", {
                    "round": round_idx,
                    "messages": len(conversation),
                    "tokens_est": svc.compactor.estimate_tokens(conversation),
                })
                conversation = svc.compactor.compact(conversation)
                session_logger.log("mid_loop_compacted", {
                    "messages": len(conversation),
                    "round": round_idx,
                    "duration_ms": (loop.time() - _compact_t0) * 1000,
                })
            else:
                session_logger.log("mid_loop_compact_skipped", {
                    "round": round_idx,
                    "messages": len(conversation),
                })

            # Deterministic stop signal (the Copilot/Claude Code pattern): if
            # the model has an active plan and EVERY task is marked completed,
            # inject a final system instruction telling the model to synthesize
            # its answer now and NOT emit more tool calls. This is the guard
            # against the "re-search the same thing forever" loop — the model
            # can't override a fully-checked list. The next round will produce
            # a no-tool-call message and the natural stop fires.
            #
            # IMPORTANT: Only fire when no WORK tools were called this round.
            # If the model called vault_search, code_read, etc. in the same
            # round it marked all tasks done, it is still actively working and
            # may need to add new tasks based on the results. Plan-management
            # tools (plan_task, update_task, add_task) don't count as work.
            _PLAN_MGMT_TOOLS = {"plan_task", "update_task", "add_task"}
            _called_work_tools = [t for t in _round_tool_names if t not in _PLAN_MGMT_TOOLS]
            if wm.has_plan() and wm.all_done() and not _called_work_tools:
                session_logger.log("working_memory_all_done", {
                    "round": round_idx,
                    "tasks": wm.snapshot().get("total", 0),
                })
                _synthesize_requested = True
                conversation.append({
                    "role": "system",
                    "content": (
                        "All tasks in your working memory are completed. "
                        "Synthesize your final answer for the user now. "
                        "Do NOT call any more tools — produce the answer."),
                })

            # Loop back: the LLM now sees the tool results and will produce
            # either another tool call or the final answer.
            round_idx += 1

            # Chat-loop checkpoint: snapshot the in-flight turn so a crash/restart
            # resumes mid-turn. Records which tools already ran (with a short
            # result summary) so the resumed turn builds on them instead of
            # re-running. Best-effort — a save failure never breaks the loop.
            if _cp is not None:
                try:
                    _cp.save({
                        "user_message": user_message,
                        "round_idx": round_idx,
                        "accumulated": final_answer,
                        "thinking": thinking_text,
                        "tool_history": _turn_tool_history,
                        "working_memory": snapshot_working_memory(wm),
                    })
                except Exception as e:
                    session_logger.log("chat_checkpoint_save_failed", {"error": str(e)})

        except Exception as e:
            # The whole agentic loop crashed — save whatever was streamed so far.
            session_logger.log_exception(e, context="handle_chat_agentic_loop")
            write_partial(partial_path, user_message, final_answer, thinking_text)
            session_logger.log("partial_answer_saved_on_crash", {
                "partial_path": str(partial_path),
                "answer_chars": len(final_answer),
            })
            raise
        finally:
            # If the answer completed normally, clean up the partial file.
            # If it didn't (crash, disconnect), the partial survives on disk.
            if final_answer and len(final_answer) > 50:
                try:
                    if partial_path.exists():
                        partial_path.unlink()
                except Exception:
                    pass

        session_logger.log("llm_generate", {
            "model": svc.ollama_client.llm_model,
            "stream": True,
            "total_chunks": total_chunks,
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
            "duration_ms": (loop.time() - t0) * 1000,
        })

        await svc.manager.send_personal_message(json.dumps({"type": "answer_done", "content": final_answer}), websocket, session_logger=session_logger)
        # Turn completed normally — clear the chat-loop checkpoint so a stale
        # in-flight snapshot isn't resumed next session.
        if _cp is not None:
            try:
                _cp.clear()
            except Exception:
                pass
        # Refresh the token meter after the full turn: tool rounds added
        # assistant + tool messages, so the window is now fuller than the
        # pre-loop estimate. Best-effort, never blocks.
        try:
            _total_chars = sum(len(str(m.get("content", "") or ""))
                               for m in conversation if isinstance(m, dict))
            _used_tokens = max(1, _total_chars // 4)
            _ctx_window = svc.ollama_client.context_window(svc.ollama_client.llm_model)
            await svc.manager.send_personal_message(json.dumps({
                "type": "context_usage",
                "model": svc.ollama_client.llm_model,
                "context_window": _ctx_window,
                "used_tokens": _used_tokens,
                "available_tokens": max(0, _ctx_window - _used_tokens),
                "messages": len(conversation),
            }), websocket, session_logger=session_logger)
        except Exception as _e:
            session_logger.log("context_usage_emit_failed", {"error": str(_e)})
        session_logger.log("chat_end", {
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
        })

        # --- Notify the Obsidian plugin that vault files may have changed -------
        # The backend writes files directly to disk, bypassing Obsidian's vault
        # API. Obsidian's file watcher may not detect these changes immediately
        # (especially on Windows), so the graph view stays stale until Obsidian
        # is restarted. This broadcast tells the plugin which files changed so
        # it can "touch" them through Obsidian's vault API, triggering the
        # metadata cache to re-process and the graph view to update in real-time.
        try:
            import time as _time
            changed_files = []
            vault_root = svc.vault_path
            for dirpath, dirnames, filenames in os.walk(vault_root):
                # Skip non-vault directories
                dirnames[:] = [d for d in dirnames if d not in (
                    '.obsidian', 'vaultbot_stuff/vaultbot_backend', 'node_modules', '.git',
                    'vaultbot_stuff/learningMaterial', 'custom_tools', '__pycache__',
                )]
                for fname in filenames:
                    if fname.endswith('.md'):
                        fpath = os.path.join(dirpath, fname)
                        try:
                            mtime = os.path.getmtime(fpath)
                            if mtime >= chat_start_time:
                                # Return path relative to vault root
                                rel = os.path.relpath(fpath, vault_root)
                                changed_files.append(rel.replace(os.sep, '/'))
                        except OSError:
                            pass
            if changed_files:
                await svc.manager.send_personal_message(
                    json.dumps({"type": "vault_changed", "files": changed_files}),
                    websocket, session_logger=session_logger)
                session_logger.log("vault_changed_broadcast", {
                    "file_count": len(changed_files),
                })
        except Exception as e:
            session_logger.log("vault_changed_failed", {"error": str(e)})

        # Embedding-drift feedback (relevance feedback, LLM-free): nudge the
        # stored embeddings of retrieved notes toward (or away from) this query
        # based on whether the context was useful.  Signal heuristic:
        #   - If the agent produced a substantive answer (len > 50) on the
        #     FIRST round WITHOUT calling vault_research, the vault context was
        #     helpful → nudge the top retrieved note's embedding TOWARD the
        #     query (it ranks higher for similar queries next time).
        #   - If the agent's first move was to call vault_research (the vault
        #     was insufficient), the retrieved context was UNhelpful for this
        #     query → nudge the top retrieved note AWAY from the query.
        #   - A short answer (< 50 chars) is ambiguous → no signal.
        # This is the "scooch embeddings toward/away based on if the LLM says
        # it's helpful" behavior.  Zero extra LLM calls — the signal is derived
        # from the agent's own behavior.  Drift is capped + reset on rewrite
        # (see embedding_drift.py).
        if retrieved_paths:
            try:
                # did the agent research on round 0? (vault context unhelpful)
                researched_first = False
                # round_idx 0 + a research tool call in the first round.
                # We approximate: if final_answer is short AND round_idx > 0,
                # the agent looped through tools (likely research).  A cleaner
                # signal would track whether vault_research was called, but
                # this is LLM-free and good enough for drift seeding.
                first_round_researched = (round_idx > 0 and len(final_answer) < 200)
                q_emb = await loop.run_in_executor(
                    None, svc.vault_indexer._get_embedding, user_message)
                top_fp = retrieved_paths[0]
                if first_round_researched:
                    svc.embedding_drift.record_feedback(top_fp, q_emb, helpful=False)
                elif len(final_answer) > 50:
                    svc.embedding_drift.record_feedback(top_fp, q_emb, helpful=True)
                session_logger.log("drift_feedback", {
                    "top_note": Path(top_fp).stem,
                    "helpful": (len(final_answer) > 50 and not first_round_researched),
                    "answer_len": len(final_answer),
                    "rounds": round_idx + 1})
            except Exception as e:
                session_logger.log("drift_feedback_failed", {"error": str(e)})

        # Lazy de-fluff: after the answer is delivered, condense any retrieved
        # notes that have crossed the touch threshold (3+ queries) and are still
        # long.  Fire-and-forget so the user is never blocked — the condense LLM
        # call happens in the background.  Notes that are never queried are never
        # touched (zero wasted LLM calls).  A note that condenses gets its touch
        # counter reset so it isn't immediately re-condensed.
        if retrieved_paths:
            async def _run_lazy_condense_bg():
                try:
                    summary = await loop.run_in_executor(
                        None, svc.lazy_condenser.condense_batch, retrieved_paths)
                    if not summary.get("condensed"):
                        return
                    session_logger.log("lazy_condense_done", summary)
                    # Re-index condensed notes AND get the new embeddings back so
                    # we can re-weave.  batch_add_files skips unchanged notes by
                    # hash, so only the condensed ones cost an embedding call.
                    # Detect which notes actually got condensed by checking for
                    # the marker (the summary's details list uses stem names,
                    # not full paths, so it's unreliable for path lookups).
                    from lazy_condenser import CONDENSE_MARKER
                    condensed_paths = []
                    for fp in retrieved_paths:
                        try:
                            if CONDENSE_MARKER in Path(fp).read_text(
                                    encoding="utf-8", errors="replace"):
                                condensed_paths.append(fp)
                        except Exception:
                            continue
                    if not condensed_paths:
                        return
                    _n, new_embs = await loop.run_in_executor(
                        None, svc.vault_indexer.batch_add_files,
                        condensed_paths, True)
                    # --- Post-condense re-weave --- #
                    # The condense LLM is told to keep all [[wikilinks]], but if
                    # it drops a scaffolding sentence that carried a link, the
                    # link goes with it.  Re-run the outbound linker on each
                    # condensed note to restore any links whose concept is still
                    # mentioned as plain text in the new body.  Idempotent (won't
                    # double-wrap).  Also re-run the cross-book linker with the
                    # NEW embeddings so the "## Related sections" block reflects
                    # the condensed content, not the original.
                    title_map = existing_note_titles(svc)
                    for fp in condensed_paths:
                        try:
                            await loop.run_in_executor(
                                None, link_outbound, fp, title_map)
                        except Exception:
                            pass
                    # Re-run cross-book linking on the condensed notes only.
                    # source_keys = the condensed set (so a condensed note doesn't
                    # cross-link to another condensed note from the same batch
                    # incorrectly — though same-book exclusion is less precise
                    # here since we don't have the full book path set; the
                    # distance threshold still prevents weak matches).
                    source_keys = {str(Path(fp).resolve()) for fp in condensed_paths}
                    try:
                        cross = await loop.run_in_executor(
                            None, cross_link_textbooks, svc,
                            condensed_paths, new_embs, source_keys)
                        session_logger.log("post_condense_relink", {
                            "condensed": len(condensed_paths),
                            "cross_links": cross.get("cross_links_added", 0),
                        })
                    except Exception as e:
                        session_logger.log("post_condense_crosslink_failed",
                                           {"error": str(e)})
                    # --- L1 concept-card lazy refine (rehearsal-gated) --- #
                    # Cards retrieved 3+ times get a one-shot LLM rewrite into a
                    # tight semantic summary (same rehearsal contract as the L0
                    # condenser).  When an L0 section is condensed, also refresh
                    # its card so the card reflects the new terse content.  Zero
                    # LLM calls for cards that haven't earned it.
                    try:
                        from concept_card import (
                            build_card_for,
                            card_path_for,
                            needs_refine,
                            refine_card,
                        )
                        # First: refresh cards for any condensed L0 sections so
                        # the card sketch reflects the new body (unless the card
                        # was already LLM-refined, which is sticky).  Also RESET
                        # the embedding drift for any note whose content changed
                        # (condense or refine) — the old drift was earned against
                        # content that no longer exists, so keeping it would
                        # mislead retrieval.
                        for fp in condensed_paths:
                            card = card_path_for(fp)
                            if card.exists():
                                # Re-build the extractive sketch only if not refined.
                                try:
                                    old = card.read_text(
                                        encoding="utf-8", errors="replace")
                                    from concept_card import REFINED_MARKER
                                    if REFINED_MARKER not in old:
                                        build_card_for(fp, vault_graph=svc.vault_graph)
                                except Exception:
                                    pass
                            # Drift reset: content changed, old drift is invalid.
                            try:
                                svc.embedding_drift.reset(fp)
                                if card.exists():
                                    svc.embedding_drift.reset(str(card))
                            except Exception:
                                pass
                        # Then: LLM-refine any retrieved cards that have crossed
                        # the rehearsal threshold.  Uses the touch counter the
                        # lazy_condenser already maintains.
                        refined = 0
                        for fp in retrieved_paths:
                            card = card_path_for(fp)
                            if not card.exists():
                                continue
                            try:
                                tc = svc.lazy_condenser.touch_counts.get(
                                    str(Path(card).resolve()), 0)
                            except Exception:
                                tc = 0
                            if needs_refine(card, tc):
                                r = await loop.run_in_executor(
                                    None, refine_card, card, svc.ollama_client, None)
                                if r.get("refined"):
                                    refined += 1
                                    # re-index the refined card
                                    await loop.run_in_executor(
                                        None, svc.vault_indexer.batch_add_files,
                                        [str(card)], False)
                                    # Drift reset: the card's content changed
                                    # (extractive → LLM summary), so the old
                                    # drift is invalid.
                                    try:
                                        svc.embedding_drift.reset(str(card))
                                    except Exception:
                                        pass
                        if refined:
                            session_logger.log("card_refine_done",
                                               {"refined": refined})
                    except Exception as e:
                        session_logger.log("card_refine_failed",
                                           {"error": str(e)})
                except Exception as e:
                    session_logger.log("lazy_condense_bg_failed", {"error": str(e)})
            asyncio.create_task(_run_lazy_condense_bg())

        # Persist this turn into the per-session history so the NEXT message has
        # context. We save the system-prompt-stripped conversation (the system
        # prompt is rebuilt fresh each turn) — i.e. everything after the initial
        # system message: the user turn, all assistant + tool rounds, and the
        # final assistant answer. This is what gets prepended next turn.
        try:
            history = getattr(websocket, "conversation_history", None)
            if history is not None:
                # The conversation list is [system, ...history, user, assistant,
                # tool, assistant, ...]. Strip the leading system message and
                # everything we just added (user msg onward) is the new history.
                # Strip the `thinking` field from persisted history. The
                # thinking field (Qwen/glm reasoning) can be thousands of chars
                # per round and is per-round scratch space — it doesn't need to
                # survive across turns. Keeping it bloats the restored history
                # and crowds out the actual user/assistant exchanges the model
                # needs to stay on track. Also cap any single message's content
                # so one huge tool result or answer can't dominate the restored
                # thread. This is the fix for "redoing a prompt from days ago":
                # the restored history was so full of stale thinking that the
                # recent turns were the first thing the compactor shredded.
                new_turns = []
                for m in conversation:
                    if m.get("role") == "system":
                        continue
                    m2 = dict(m)
                    m2.pop("thinking", None)
                    c = m2.get("content")
                    if isinstance(c, str) and len(c) > 4000:
                        m2["content"] = c[:4000] + "\n[...truncated in history...]"
                    new_turns.append(m2)
                # Only persist if we actually added turns this round (guard
                # against a no-op). Persist even when final_answer is empty —
                # an empty answer (model bailed / tool-only round) still
                # carried the user's message + any tool/thinking exchanges,
                # and dropping those from history breaks the thread both
                # in-session and across restarts.
                if len(new_turns) > len(history):
                    websocket.conversation_history = new_turns
                    session_logger.log("history_persisted", {
                        "turns": len(new_turns),
                        "history_chars": sum(len(str(m.get("content", ""))) for m in new_turns),
                        "final_answer_len": len(final_answer or ""),
                    })
                    # Persist to disk so a backend restart restores this exact
                    # thread on the next WebSocket connect. This is the "bring
                    # you back into the same session" fix — the live conversation
                    # survives restarts, not just the slow identity files.
                    # Best-effort, never blocks.
                    save_history(new_turns)
        except Exception as e:
            session_logger.log("history_persist_failed", {"error": str(e)})

        # Save a chat note if the answer is substantive
        if len(final_answer) > 100:
            try:
                note_path = await loop.run_in_executor(None, svc.note_creator.create_note_from_chat, user_message, final_answer, thinking_text)
                session_logger.log("chat_note_created", {"note_path": note_path})
            except Exception as e:
                session_logger.log_exception(e, context="note_creator.create_note_from_chat")
                print(f"Error creating chat note: {e}")

        # GOALS.md is now updated by the LLM itself via the set_goal tool — no
        # heuristic. The agent owns the decision: it calls set_goal when it
        # starts a task, decomposes steps, or completes one. If it has no goal to
        # update, it leaves GOALS.md alone. This removes the brittle char-count /
        # greeting-allowlist heuristic that wiped a live goal mid-task (the
        # "redoing a prompt from days ago" root cause). The goal persists until
        # the agent explicitly changes it, so multi-step tasks stay coherent
        # across turns without external guessing. See [[Generative-Agents]] plan-
        # persistence pattern: the goal lives in a file, the agent writes it.

        # Close the MIRROR loop: regenerate the bounded self-model from this
        # turn's activity so the agent consolidates its reasoning into a durable
        # first-person narrative that survives context compaction and model swaps.
        # This is the +9.3% vs +2.4% finding (MIRROR arXiv:2506.00430): the value
        # of thinking lies in maintaining its outputs across time, not the act of
        # thinking itself.
        try:
            # Build a rich activity summary so the self-model captures not just
            # the final answer but the reasoning + tool use that led there. An
            # empty answer (model bailed / tool-only round) still imprints the
            # thinking — otherwise a turn that ended in silence leaves the
            # self-model stale, which is what made the agent "forget what it was
            # doing" after a restart.
            activity_parts = [f"User asked: {user_message[:300]}"]
            if final_answer:
                activity_parts.append(f"Answer: {final_answer[:500]}")
            else:
                activity_parts.append("Answer: (empty — model produced no final text)")
            if thinking_text:
                # Include a slice of the reasoning so the self-model records
                # what the agent was actually working through.
                activity_parts.append(f"Reasoning: {thinking_text[:600]}")
            # Tool calls across all rounds, summarized.
            _tool_summary = []
            for m in conversation:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                        _tool_summary.append(fn.get("name", "?"))
            if _tool_summary:
                activity_parts.append(
                    "Tools used: " + ", ".join(_tool_summary[:10]))
            activity = "\n".join(activity_parts)
            await loop.run_in_executor(None, lambda: svc.identity.regenerate_self_model(activity))
        except Exception as e:
            session_logger.log("self_model_regenerate_failed", {"error": str(e)})

        # Pattern extraction: check for new consolidation gaps after each chat.
        # This is the episodic -> semantic consolidation trigger. The pattern
        # extractor scans chat logs for recurring topics, correction patterns,
        # and self-model drift. Gaps are logged so the autonomous researcher
        # can consolidate them into semantic knowledge notes.
        # Pure deterministic -- no LLM calls. See [[Semantic-Consolidation-Architecture]].
        try:
            _gaps = await loop.run_in_executor(
                None, svc.pattern_extractor.get_consolidation_gaps)
            if _gaps:
                session_logger.log("consolidation_gaps", {
                    "gap_count": len(_gaps),
                    "top_gaps": [
                        {"kind": g["kind"], "topic": g["topic"],
                         "priority": g.get("priority", 0)}
                        for g in _gaps[:5]
                    ],
                })
        except Exception as e:
            session_logger.log("pattern_extraction_failed", {"error": str(e)})
    finally:
        svc.autonomous_researcher.resume_after_chat()



async def execute_agent_tool(svc: Services, tool_name: str, args: dict[str, Any],
                             session_logger, websocket: WebSocket | None = None,
                             user_message: str = "") -> dict[str, Any]:
    """Execute one tool call from the chat LLM. Runs in the async context.

    `websocket` is passed so long-running tools (vault_research) can push
    live progress events to the UI instead of going silent for 30-60s.
    """
    # Module-level imports from chat_helpers, weaving — no longer deferred
    # from main (circular dependency eliminated).
    loop = asyncio.get_event_loop()
    session_logger.log("execute_agent_tool_body_start", {
        "tool": tool_name, "t_ms": loop.time() * 1000,
    })

    if tool_name == "vault_research":
        topic = (args.get("topic") or "").strip()
        depth = args.get("depth", "deep")
        if not topic:
            return {"error": "missing topic"}

        # SUBAGENT CONTEXT ISOLATION (Copilot runSubagent / Claude subagent
        # pattern): run the full dig + note creation in a SEPARATE PROCESS so
        # the verbose work (1900+ source-rejection events, scrapes, a 50K
        # synthesis) never enters the orchestrator's conversation. The
        # subprocess prints ONLY a compact JSON brief to stdout; the chat
        # loop gets one bounded tool result, not a flood. The full synthesis
        # stays on disk in the created note — re-readable via vault_search /
        # web_read_source. See subagent.py.
        #
        # Fallback: if VAULTBOT_SUBAGENT=off (or the import fails), the
        # in-process path runs below. It still distills the report to a
        # bounded brief before returning, so the conversation never balloons
        # either way. The subagent path is strictly safer (hard process
        # isolation); the in-process path is the safety net.
        try:
            from subagent import subagent_enabled, run_research_subagent
            _use_subagent = subagent_enabled()
        except Exception:
            _use_subagent = False

        if _use_subagent:
            session_logger.log("subagent_research_invoked", {"topic": topic[:80]})
            # Emit heartbeats while the subprocess runs — the child can't send
            # websocket events (it's a separate process), so the orchestrator
            # keeps the UI alive with the existing run_with_heartbeat pattern.
            t_sub = loop.time()
            try:
                brief = await run_with_heartbeat(
                    svc, websocket, f"research{topic[:40]}",
                    run_research_subagent, topic, depth, session_logger)
            except Exception as e:
                session_logger.log_exception(e, context="subagent_research")
                brief = {"status": "error",
                          "error": f"subagent research failed: {e}",
                          "subagent": True}
            # The subagent already created + indexed the note. Refresh the
            # orchestrator's in-memory graph so subsequent rounds see it
            # (the child's indexer is its own instance).
            try:
                await loop.run_in_executor(None, svc.vault_graph.refresh)
            except Exception as e:
                session_logger.log("post_subagent_graph_refresh_failed",
                                    {"error": str(e)})
            session_logger.log("subagent_research_complete", {
                "duration_ms": int((loop.time() - t_sub) * 1000),
                "status": brief.get("status"),
                "source_count": brief.get("source_count", 0),
                "note_path": brief.get("note_path"),
            })
            # Normalize: an empty-status brief becomes an error so the model
            # doesn't treat a no-sources dig as a real result.
            if brief.get("status") == "empty":
                brief["error"] = brief.get("error", "no web sources found")
            # Attach the goal hint so the agent can decide whether to record
            # this research against a larger task (same hint as the
            # in-process path — the subagent is stateless and can't call
            # set_goal itself).
            brief["goal_hint"] = (
                "If this research advances a multi-step task, consider "
                "calling set_goal to record the current goal + next step "
                "so you stay on track across turns. If this was a one-off, "
                "ignore this."
            )
            return brief

        # --- In-process fallback (VAULTBOT_SUBAGENT=off or import fail) ---
        # Kept as a safety net. Still distills the report to a bounded brief
        # so the conversation never balloons. The subagent path above is
        # preferred (hard isolation); this path shares the loop.
        if depth == "quick":
            svc.research_engine.max_rounds = 1
            svc.research_engine.max_follow_ups = 0

        prev_cb = svc.research_engine.progress_callback
        if websocket is not None:
            def _progress_cb(stage: str, detail: dict):
                try:
                    asyncio.run_coroutine_threadsafe(
                        send_progress(svc, websocket, stage, detail), loop)
                except Exception:
                    pass
            svc.research_engine.progress_callback = _progress_cb

        t_research = loop.time()
        try:
            report = await run_with_heartbeat(
                svc, websocket, f"research{topic[:40]}", svc.research_engine.research, topic)
        finally:
            svc.research_engine.max_rounds = int(os.getenv("VAULTBOT_RESEARCH_ROUNDS", "4"))
            svc.research_engine.max_follow_ups = int(os.getenv("VAULTBOT_RESEARCH_FOLLOWUPS", "3"))
            svc.research_engine.progress_callback = prev_cb
            session_logger.log("agent_research_done", {
                "duration_ms": (loop.time() - t_research) * 1000,
                "source_count": report.get("source_count", 0) if isinstance(report, dict) else 0,
            })
        # Persist a linked note so the research becomes vault knowledge.
        if report.get("source_count") and report.get("synthesis"):
            try:
                summary = (f"Research into '{topic}' ({report['source_count']} "
                           f"sources, {report['synthesis_facts']} facts).")
                await send_progress(svc, websocket, "writing_note", {"topic": topic})
                note_path = await run_with_heartbeat(
                    svc, websocket, "writing_note",
                    svc.note_creator.create_note_from_research,
                    topic, report["synthesis"], summary)
                if report.get("llm_synthesized"):
                    # LLM synthesis already produced a structured note with
                    # frontmatter, H2 prose sections, wikilinks, and Sources.
                    # Write it directly -- skip double-processing.
                    try:
                        Path(note_path).write_text(
                            report["synthesis"], encoding="utf-8")
                    except Exception:
                        pass
                else:
                    # Extractive fallback: wrap in markdown, then try LLM
                    # structuring (ONE call) for frontmatter + H2 sections.
                    md = svc.research_engine.synthesize_note_markdown(report, summary)
                    try:
                        Path(note_path).write_text(md, encoding="utf-8")
                    except Exception:
                        pass
                    try:
                        _titles = svc.research_engine._get_vault_note_titles(svc.vault_path)
                        _structured = svc.research_engine.synthesize_structured_note(
                            report, summary, ollama_client=svc.ollama_client,
                            vault_note_titles=_titles)
                        if _structured and len(_structured) >= svc.research_engine._STRUCTURED_MIN_CHARS:
                            Path(note_path).write_text(_structured, encoding="utf-8")
                            session_logger.log("research_note_structured",
                                               {"note_path": note_path,
                                                "chars": len(_structured)})
                    except Exception as _e:
                        session_logger.log("research_note_structure_failed",
                                           {"error": str(_e)})
                report["note_path"] = note_path
            except Exception as e:
                session_logger.log_exception(e, context="agent_research_note")
        # A-MEM: evolve neighboring notes' tags/links so the vault learns from
        # the new note (arXiv:2502.12110).
        if report.get("note_path"):
            try:
                await send_progress(svc, websocket, "amem_evolve", {
                    "note": Path(report["note_path"]).stem})
                await run_with_heartbeat(
                    svc, websocket, "amem_evolve",
                    lambda: svc.amem.evolve_on_create(
                        report.get("note_path", ""), report.get("synthesis", ""),
                        skip_refresh=True))
            except Exception as e:
                session_logger.log("amem_evolve_failed", {"error": str(e)})
        # Goal hint (same as the subagent path).
        if isinstance(report, dict):
            report["goal_hint"] = (
                "If this research advances a multi-step task, consider "
                "calling set_goal to record the current goal + next step "
                "so you stay on track across turns. If this was a one-off, "
                "ignore this."
            )
        # Distill the full report to a compact brief before returning so the
        # conversation never balloons (the in-process path doesn't have hard
        # isolation, so this is the bound). Same brief shape as the subagent
        # path so the chat loop + truncate_tool_result work unchanged.
        if isinstance(report, dict):
            try:
                _syn = str(report.get("synthesis", "") or "")
                _facts = report.get("synthesis_facts") or []
                if isinstance(_facts, list):
                    _facts_txt = "\n".join(f"- {str(f)[:300]}" for f in _facts[:8])
                else:
                    _facts_txt = str(_facts)[:1500]
                report = {
                    "topic": report.get("topic"),
                    "source_count": report.get("source_count", 0),
                    "note_path": report.get("note_path"),
                    "synthesis_brief": _syn[:1500] + (
                        "\n*[... full synthesis in the note at note_path ...]*"
                        if len(_syn) > 1500 else ""),
                    "key_facts": _facts_txt,
                    "subagent_note": (
                        "Verbose dig output kept OUT of context (subagent "
                        "isolation). Full synthesis is in the created note; "
                        "re-read it via vault_research/web_read_source if you "
                        "need a specific detail."),
                    "goal_hint": report.get("goal_hint", ""),
                }
                session_logger.log("subagent_result_distilled", {
                    "tool": "vault_research",
                    "orig_synthesis_chars": len(_syn),
                    "brief_chars": len(report.get("synthesis_brief", "")),
                })
            except Exception:
                pass  # distillation is best-effort; never break the tool
        return report

    if tool_name == "vault_search":
        query = args.get("query", "")
        k = int(args.get("k", 5))
        results = await loop.run_in_executor(None, svc.vault_indexer.search, query, k)
        return {"query": query, "results": [
            {"file_path": r.get("file_path"), "content": r.get("content", "")[:1200],
             "score": r.get("score")} for r in results
        ]}

    if tool_name == "vault_gaps":
        gaps = await loop.run_in_executor(None, svc.autonomous_researcher._identify_gaps)
        return {"gaps": gaps[:20], "count": len(gaps)}

    if tool_name == "vaultbot_status":
        return svc.autonomous_researcher.status()

    # --- Meta-tools (self-improvement) --- #
    if tool_name == "code_read":
        return await loop.run_in_executor(None, lambda: svc.self_improver.code_read(
            args.get("file_path", ""), int(args.get("start_line", 1)),
            int(args.get("end_line", 0))))

    if tool_name == "code_run":
        return await loop.run_in_executor(None, lambda: svc.self_improver.code_run(
            args.get("code", ""), int(args.get("timeout", 15))))

    if tool_name == "tool_create":
        result = await loop.run_in_executor(None, lambda: svc.self_improver.tool_create(
            args.get("tool_name", ""), args.get("description", ""),
            args.get("parameters", {}), args.get("code", "")))
        # Hot-reload so the new tool is callable immediately.
        svc.self_improver.load_custom_tools()
        return result

    if tool_name == "self_reflect":
        ctx = args.get("vault_context", "")
        return await loop.run_in_executor(None, lambda: svc.self_improver.self_reflect(
            args.get("topic", ""), ctx))

    if tool_name == "git_rollback":
        return await loop.run_in_executor(None, lambda: svc.self_improver.git_rollback(
            args.get("file_path", "")))

    if tool_name == "safe_write":
        return await loop.run_in_executor(None, lambda: svc.self_improver.safe_write(
            args.get("file_path", ""), args.get("content", ""),
            bool(args.get("dry_run", False))))

    if tool_name == "js_safe_write":
        return await loop.run_in_executor(None, lambda: svc.self_improver.js_safe_write(
            args.get("file_path", ""), args.get("content", ""),
            bool(args.get("dry_run", False))))

    if tool_name == "capability_audit":
        return await loop.run_in_executor(None, lambda: svc.self_improver.capability_audit(
            args.get("task", "")))

    # --- Procedure execution (step-gate runtime) --- #
    # The LLM calls this to execute a procedure written in a markdown note.
    # The procedure runs as a blocking subprocess: code steps execute
    # deterministically (zero LLM cost), LLM steps use minimal context via
    # get_llm_client(). Returns the procedure's step-by-step output.
    # See [[Procedure-Subprocess-Architecture]].
    if tool_name == "execute_procedure":
        from procedure_compiler import compile_procedure as _compile_proc
        from step_gate_runtime import execute_procedure as _run_proc

        proc_name = args.get("procedure_name", "")
        if not proc_name:
            return {"error": "missing procedure_name"}

        backend_dir = Path(__file__).parent.resolve()
        vault_root = backend_dir.parent

        # Resolve the procedure file via the tracker's stem index (O(1)
        # after first build) instead of rglob-walking the vault on every
        # call.  The index is cached on the tracker and rebuilt lazily if
        # the stem is missing (covers a note written seconds ago).
        proc_file = None
        try:
            idx = getattr(svc.procedure_tracker, "_stem_index", None)
            if idx is None:
                idx = svc.procedure_tracker.get_procedure_index(str(vault_root))
                svc.procedure_tracker._stem_index = idx
            entry = idx.get(proc_name)
            if entry:
                proc_file = Path(entry["path"])
        except Exception:
            pass

        if not proc_file:
            # Fallback: rglob for a just-written note the index hasn't seen.
            for candidate in vault_root.rglob("*.md"):
                if candidate.stem == proc_name:
                    proc_file = candidate
                    break

        if not proc_file:
            return {"error": f"procedure not found: {proc_name}"}

        # --- Execution gate (extra-safe): check the procedure's status BEFORE
        # running it. verified -> run clean; experimental -> run with a caution
        # note; flagged -> BLOCK and route to re-research. This is the
        # deterministic trust layer: a procedure that repeatedly failed
        # validation is never executed, no matter how confidently the model
        # asks for it. See procedure_surface.status_allows_execution.
        try:
            _idx = getattr(svc.procedure_tracker, "_stem_index", None) or {}
            _entry = _idx.get(proc_name) or {}
            _status = str((_entry.get("frontmatter") or {}).get("status", ""))
            _allowed, _gate_reason = status_allows_execution(_status)
            if not _allowed:
                session_logger.log("procedure_blocked", {
                    "procedure": proc_name, "status": _status})
                return {
                    "error": f"procedure blocked: {proc_name}",
                    "status": _status or "unknown",
                    "reason": _gate_reason,
                    "blocked": True,
                }
            _proc_caution = (_gate_reason == "experimental")
        except Exception:
            _proc_caution = False  # gate failure must not block execution

        proc = _compile_proc(str(proc_file))
        if not proc:
            return {"error": f"not a procedure note: {proc_name}"}

        result = await _run_proc(
            procedure=proc,
            context="",
            llm_client=svc.ollama_client,
            vault_path=str(vault_root),
            procedure_tracker=svc.procedure_tracker,
        )

        # --- Procedure-level drift feedback (Phase 3) ---
        # Nudge the procedure NOTE's embedding toward the query if it
        # passed, away if it failed.  Reuses the chat-loop query embedding
        # already computed for note drift.  No new drift code — just a
        # new caller.  See embedding_drift.py.
        if user_message:
            try:
                q_emb = await loop.run_in_executor(
                    None, svc.vault_indexer._get_embedding, user_message)
                helpful = result.overall_passed
                svc.embedding_drift.record_feedback(
                    str(proc_file), q_emb, helpful=helpful)
                session_logger.log("procedure_drift_feedback", {
                    "procedure": proc_name,
                    "helpful": helpful,
                    "failed_step": result.failed_step,
                })
            except Exception as e:
                session_logger.log("procedure_drift_feedback_failed",
                                    {"error": str(e)})

        return {
            "procedure": proc_name,
            "overall_passed": result.overall_passed,
            "failed_step": result.failed_step,
            "steps_executed": len(result.steps),
            "final_output": result.final_output[:4000],
            "child_procedures": result.child_procedures,
            # Surface the trust level so the model weighs the output
            # accordingly (an experimental procedure's result is provisional).
            "caution": ("experimental — unproven procedure; verify the "
                        "output before relying on it" if _proc_caution else ""),
            "step_details": [
                {"step": sr.step_number, "type": sr.step_type,
                 "passed": sr.passed,
                 "error": sr.error or sr.validation_error}
                for sr in result.steps
            ],
        }

    # --- Textbook page reader (index-only paradigm) --- #
    # The LLM calls this to read one page of an ingested textbook PDF. The
    # page is rendered to an image and sent to a vision-capable model so
    # equations/figures come through exactly as printed. Falls back to the
    # text layer (with a caveat) if the model can't see images. The result
    # carries provenance so the LLM can cite it in notes.
    #
    # Client selection: prefer the DEDICATED vision client (a separate
    # model the user configured just for page-reading, e.g. a vision model
    # on a different backend while their chat model stays text-only/fast).
    # Fall back to the synthesis client so a vision-capable chat model still
    # works without a separate vision config.
    if tool_name == "textbook_read_page":
        from custom_tools.textbook_read_page import run as _read_page
        page_client = svc.vision_client if svc.vision_client is not None else svc.ollama_client
        # Inject the active page-reading client so the tool can probe vision
        # support and call it for the page read.
        result = await loop.run_in_executor(
            None, lambda: _read_page(args, llm_client=page_client))
        return result

    # --- Web source re-reader (index-only paradigm for web research) --- #
    # The LLM calls this to re-read a source the research engine archived in
    # learningMaterial/web/. Returns the page's article text + provenance to
    # the saved file, so the LLM can verify/quote without re-scraping.
    if tool_name == "web_read_source":
        from custom_tools.web_read_source import run as _read_web
        result = await loop.run_in_executor(None, lambda: _read_web(args))
        return result

    if tool_name == "set_goal":
        # The LLM owns goal management. No heuristic — the agent decides when
        # to set, update, or clear its goal. This is the only path to
        # GOALS.md from the chat loop. See the set_goal tool schema for the
        # contract. Never raises; a failure returns an error dict, the chat
        # loop continues.
        goal = (args.get("goal") or "").strip()
        next_step = (args.get("next_step") or "(awaiting next request)").strip()
        steps = args.get("steps") or None
        context = args.get("context") or None
        if not goal or goal.lower() in ("clear", "none", ""):
            new_text = svc.identity.update_goals(
                goal="(no active goal)",
                steps=None,
                next_step=next_step or "(awaiting next request)")
            session_logger.log("goals_cleared_by_agent", {})
            return {"status": "cleared", "goals_md": new_text[:200]}
        new_text = svc.identity.update_goals(
            goal=goal[:500], steps=steps, next_step=next_step, context=context)
        session_logger.log("goals_set_by_agent", {"goal": goal[:100]})
        return {"status": "set", "goal": goal[:200],
                "goals_md_chars": len(new_text)}

    # --- Working memory (the Copilot/Claude Code TodoList pattern) ------ #
    # The model writes a structured task list via plan_task and updates it
    # via update_task. The harness re-injects the list into the system
    # prompt every round (see handle_chat). This is how the agent stays on
    # track instead of losing the plot to compaction.
    if tool_name == "plan_task":
        session_logger.log("plan_task_branch_enter", {"t_ms": loop.time() * 1000})
        wm = getattr(websocket, "working_memory", None)
        if wm is None:
            wm = TaskList()
            websocket.working_memory = wm
        goal = (args.get("goal") or "").strip()
        steps = args.get("steps") or []
        if not goal or not steps:
            return {"error": "plan_task requires 'goal' and 'steps'"}
        snap = wm.set_plan(goal=goal, items=[s for s in steps if s.strip()])
        session_logger.log("plan_task_set", {
            "goal": goal[:100], "steps": len(steps)})
        session_logger.log("plan_task_branch_exit", {"t_ms": loop.time() * 1000})
        return snap

    if tool_name == "update_task":
        wm = getattr(websocket, "working_memory", None)
        if wm is None:
            return {"error": "no active plan"}
        action = args.get("action", "update")
        if action == "add":
            content = (args.get("content") or "").strip()
            if not content:
                return {"error": "action='add' requires 'content'"}
            snap = wm.add_task(
                content=content,
                status=args.get("status", "pending"),
                notes=args.get("notes", ""))
            session_logger.log("plan_task_added", {"content": content[:80]})
            return snap
        task_id = args.get("task_id") or ""
        snap = wm.update_task(
            task_id=task_id,
            status=args.get("status", ""),
            notes=args.get("notes", ""))
        session_logger.log("plan_task_updated", {
            "task_id": task_id, "status": args.get("status", "")})
        return snap

    # --- Custom (agent-authored) tools --- #
    if svc.self_improver.has_tool(tool_name):
        result = await loop.run_in_executor(None, lambda: svc.self_improver.execute_custom_tool(
            tool_name, args))
        # Post-ingest weaving: tie newly-ingested textbook notes into the
        # existing vault so the content is actually usable (not inert islands).
        # Runs IN THE BACKGROUND so the tool returns immediately — the agent
        # (and the user) aren't blocked for minutes while 100+ notes get
        # indexed + linked + A-MEM evolved. Progress is pushed to the UI via
        # websocket so the user sees "linking 47/129…" instead of a freeze.
        # Only fires for textbook_ingest; cheap no-op otherwise.
        if tool_name == "textbook_ingest" and isinstance(result, dict):
            note_count = len(result.get("notes_created", []) +
                             result.get("notes_updated", []))
            if note_count > 0:
                result["weaving"] = {
                    "status": "background",
                    "notes_to_weave": note_count,
                    "message": (f"Weaving {note_count} notes into the vault "
                                f"in the background (indexing + linking + "
                                f"evolving neighbors)..."),
                }
                # Fire-and-forget: run the weaving in a background thread so
                # the agent gets the result now and can keep working/talking.
                # Progress events are sent to the websocket from the thread.
                async def _run_weave_bg():
                    try:
                        await weave_textbook_notes(svc,
                            result, websocket=websocket,
                            session_logger=session_logger)
                    except Exception as e:
                        session_logger.log("textbook_weave_bg_failed",
                                           {"error": str(e)})
                asyncio.create_task(_run_weave_bg())
        return result

    return {"error": f"unknown tool: {tool_name}"}
