"""Agentic chat loop — Copilot-style, simple.

The model drives. It can use plan_task / update_task to track its own state
(the harness re-injects the working-memory block every round so the model
always sees its todo list). The framework enforces ONE rule: if the model
works 3+ tool rounds without a plan, execution tools are masked until it
calls plan_task. No phase state machine, no auto-advance, no forced
convergence, no consolidation, no step summaries — just the one-rule plan
gate plus the read-loop detector that observes actual tool results.

What we keep:
- _sanitize_tool_history: convert tool-call rounds to model-safe format
- double-silent failsafe: if the model emits nothing twice, fail loud
- checkpointing: save progress so a crash resumes mid-turn
- answer streaming: answer_chunk / answer_done / thinking events
- per-step RAG: retrieve notes relevant to the current in-progress step
- tool dispatch: execute_agent_tool unchanged (plan_task / update_task /
  custom tools / code tools / etc.)

2026-08-13: removed Hermes-style preflight compression — the small-model
summarization latency cost outweighed the token savings. The hard token cap
(_enforce_token_cap) and proactive tool-result aging (_age_old_tool_results)
remain as the primary context-bounding mechanisms.
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
    build_system_prompt_briefing,
    build_tool_list,
)
from chat_checkpoint import snapshot_working_memory

# Leaf-module imports for helpers that were previously deferred-imported
# from main (circular). These are now direct leaf imports — no main dependency.
from chat_helpers import (
    notify_console_failure,
    notify_info,
    notify_problem,
    run_with_heartbeat,
    send_progress,
    tool_result_summary,
    truncate_tool_result,
)
from conversation_state import save_history
from last_session import touch as touch_last_session
from error_types import AgentSilentError
from fastapi import WebSocket
from config import TUNABLES
from procedure_surface import build_procedure_surface
from procedure_tracker import interpret_validation_result, parse_procedures_from_results
from services import Services
from conversation_index import build_conversation_context
from small_model_filters import (
    dedup_results,
    expand_query,
    filter_context,
    rerank_results,
    rewrite_query_with_history,
)
from task_api import write_partial
from weaving import (
    cross_link_textbooks,
    existing_note_titles,
    link_outbound,
)
from working_memory import TaskList


# ---------------------------------------------------------------------------
# Extracted leaf modules — imported with underscore aliases so all existing
# call sites (e.g. _check_cancelled, _enforce_token_cap, _sanitize_tool_history)
# work unchanged without a mass rename across the 2,000-line handle_chat body.
# ---------------------------------------------------------------------------
from chat_context import (
    age_old_tool_results as _age_old_tool_results,
    dedup_seen_results as _dedup_seen_results,
    enforce_token_cap as _enforce_token_cap,
    estimate_conv_tokens as _estimate_conv_tokens,
    sanitize_tool_history as _sanitize_tool_history,
    tool_actually_wrote as _tool_actually_wrote,
)
from chat_preflight import (
    check_cancelled as _check_cancelled,
    classify_trivial as _classify_trivial,
    deterministic_procedure_hint as _deterministic_procedure_hint,
    run_procedure_direct as _run_procedure_direct,
)
from chat_tool_dispatch import execute_agent_tool  # noqa: F401 — re-exported


async def _prepare_turn(
    svc: Services,
    websocket: WebSocket,
    user_message: str,
    session_logger,
    wm: TaskList,
    _cp,
    _resumed_tool_history: list,
) -> tuple | None:
    """Setup, RAG, preflight routing, trivial-turn shortcut.

    Returns (conversation, results, system_prompt, all_tools, custom_schemas,
    procedures_in_context, retrieved_paths, chat_start_time, loop) or None
    if the turn was trivial and handled directly.
    """
    # Calibration: detect if this message is a correction of the previous
    # answer. Corrections are ground truth for calibrating quality gates.
    try:
        _prev_history = getattr(websocket, "conversation_history", None)
        _prev_answer = None
        if _prev_history:
            for _msg in reversed(_prev_history):
                if _msg.get("role") == "assistant" and _msg.get("content"):
                    _prev_answer = _msg["content"]
                    break
        if _prev_answer and svc.calibration_tracker.detect_correction(
            user_message, _prev_answer
        ):
            _ftype = svc.calibration_tracker.classify_failure(
                user_message, _prev_answer
            )
            svc.calibration_tracker.log_correction(
                user_message, _prev_answer, failure_type=_ftype
            )
            session_logger.log("correction_detected", {"failure_type": _ftype})
    except Exception as e:  # noqa: BLE001 — best-effort
        session_logger.log("correction_detection_failed", {"error": str(e)})

    await svc.manager.send_personal_message(
        json.dumps({"type": "status", "content": "Searching vault..."}),
        websocket,
        session_logger=session_logger,
    )
    loop = asyncio.get_event_loop()
    chat_start_time = loop.time()  # for vault_changed file scan

    # Keep the in-memory vault graph current with disk before retrieval.
    try:
        _t_graph = loop.time()
        await send_progress(svc, websocket, "refreshing vault graph", {})
        await loop.run_in_executor(None, svc.vault_graph.refresh)
        await send_progress(
            svc,
            websocket,
            "graph_refreshed",
            {
                "node_count": len(svc.vault_graph.nodes),
                "duration_ms": (loop.time() - _t_graph) * 1000,
            },
        )
        session_logger.log(
            "graph_refreshed",
            {
                "node_count": len(svc.vault_graph.nodes),
                "duration_ms": (loop.time() - _t_graph) * 1000,
            },
        )
    except Exception as e:  # noqa: BLE001
        session_logger.log_exception(e, context="graph_refresh")

    _check_cancelled(websocket)

    # RAG: retrieve vault context relevant to the user's message.
    # Phase 3: conversation-aware query rewriting — rewrite the user's
    #   query using conversation context so follow-ups like "what was
    #   that thing?" resolve to the actual topic. Fail-safe: returns
    #   the original message on any failure.
    # Phase 2: small-model query expansion (fail-safe — always includes
    #   the raw user message, so retrieval is never worse than today).
    # Phase 1: small-model reranking (over-fetch k=15, rerank down to 5
    #   via the Smart-Vault-Search procedure; fail-safe — falls back to
    #   FUSED order on any error).
    t0 = loop.time()
    _rewritten_query = user_message  # default: no rewrite
    try:
        # Conversation-aware query rewriting: resolve references to prior
        # conversation so retrieval finds the right notes.
        _history = getattr(websocket, "conversation_history", [])
        await send_progress(svc, websocket, "rewriting query", {})
        _rewritten_query = await loop.run_in_executor(
            None, rewrite_query_with_history, user_message, _history, session_logger
        )
        queries = [_rewritten_query]
        if svc.small_client:
            await send_progress(svc, websocket, "expanding query", {})
            _expanded = expand_query(svc.small_client, _rewritten_query, session_logger)
            # Always include the original user message so retrieval is
            # never worse than baseline.
            queries = list(
                dict.fromkeys([_rewritten_query] + _expanded + [user_message])
            )
        # Run all query retrievals concurrently. Each retrieve() is a
        # blocking call scheduled on the default executor; gathering them
        # turns N sequential round-trips into one parallel wave, cutting
        # retrieval latency ~N× (3 queries → ~3×). A single heartbeat
        # label covers the whole wave so the UI stays responsive.
        all_results: list[dict] = []
        if len(queries) <= 1:
            fused_result = await run_with_heartbeat(
                svc,
                websocket,
                "retrieving vault",
                svc.fused_retriever.retrieve,
                queries[0],
                15,
                1,
            )
            _r = (
                fused_result.get("results", [])
                if isinstance(fused_result, dict)
                else (fused_result or [])
            )
            all_results.extend(_r)
        else:
            _qlabel = "retrieving vault"
            await send_progress(svc, websocket, _qlabel, {})
            try:
                gathered = await asyncio.gather(
                    *[
                        loop.run_in_executor(
                            None, svc.fused_retriever.retrieve, q, 15, 1
                        )
                        for q in queries[:3]
                    ]
                )
                for _fr in gathered:
                    _r = (
                        _fr.get("results", []) if isinstance(_fr, dict) else (_fr or [])
                    )
                    all_results.extend(_r)
            finally:
                await send_progress(svc, websocket, _qlabel + "_done", {})
        if len(queries) > 1:
            results = dedup_results(all_results)
        else:
            results = all_results
        # Phase 1: deterministic reranking (embedding cosine similarity).
        # No longer gated on svc.small_client — the reranker uses FAISS
        # vector reconstruction, not an LLM call.
        if len(results) > 5:
            await send_progress(
                svc, websocket, "reranking results", {"count": len(results)}
            )
            results = await rerank_results(
                svc, user_message, results, k=5, session_logger=session_logger
            )
            await send_progress(
                svc, websocket, "reranking_done", {"kept": len(results)}
            )
        else:
            results = results[:5]
    except Exception as e:  # noqa: BLE001
        session_logger.log_exception(e, context="fused_retriever.retrieve")
        await notify_problem(
            svc,
            websocket,
            e,
            context={
                "category": "retrieval_broken",
                "stage": "searching the vault",
            },
            user_message=(
                "I couldn't search your vault for this question. "
                "I'll answer from what I know, but it may not be "
                "grounded in your notes."
            ),
            remedy_hint="Try restarting VaultBot.",
        )
        results = []
    session_logger.log(
        "vault_search",
        {
            "query": user_message,
            "k": 5,
            "result_count": len(results),
            "duration_ms": (loop.time() - t0) * 1000,
            "retriever": "fused",
            "rewritten_query": _rewritten_query[:200]
            if _rewritten_query != user_message
            else "",
        },
    )

    # Conversation-aware retrieval: search the conversation index for
    # prior turns relevant to this query. This is what lets the bot
    # "remember what it just said" — when the user asks a follow-up,
    # the relevant prior turns are retrieved and injected into context
    # alongside the vault notes. Best-effort: never breaks the chat loop.
    _conv_results: list[dict] = []
    try:
        _conv_idx_reg = getattr(svc, "conversation_index", None)
        _sid = getattr(websocket, "session_id", None)
        if _conv_idx_reg is not None:
            _conv_idx = _conv_idx_reg.get(_sid)
            if _conv_idx.size > 0:
                _conv_results = await loop.run_in_executor(
                    None, _conv_idx.search, _rewritten_query, 3
                )
            if _conv_results:
                session_logger.log(
                    "conversation_search",
                    {
                        "query": _rewritten_query[:100],
                        "result_count": len(_conv_results),
                        "top_score": _conv_results[0].get("score", 0)
                        if _conv_results
                        else 0,
                    },
                )
    except Exception as e:  # noqa: BLE001
        session_logger.log("conversation_search_failed", {"error": str(e)})

    # RAG evaluation: log retrieval results for every query.
    try:
        svc.rag_evaluator.log_retrieval(user_message, results, k=5)
    except Exception as e:  # noqa: BLE001
        session_logger.log("rag_eval_log_failed", {"error": str(e)})

    # Lazy-condenser touch tracking: record that each retrieved note was
    # queried so the condenser can de-fluff it later.
    retrieved_paths = []
    try:
        for r in results:
            fp = r.get("file_path") if isinstance(r, dict) else None
            if fp:
                retrieved_paths.append(fp)
                svc.lazy_condenser.note_touched(fp)
        svc.lazy_condenser.flush_touch_counts()
    except Exception as e:  # noqa: BLE001
        session_logger.log("lazy_condenser_touch_failed", {"error": str(e)})

    # Procedure context tracking: which procedural notes were in the vault
    # context for this turn? Used to log validation results against them.
    procedures_in_context = parse_procedures_from_results(results)
    if procedures_in_context:
        session_logger.log(
            "procedures_in_context",
            {
                "procedures": procedures_in_context,
            },
        )

    # Multi-resolution context: L2 MOC + L1 concept cards + L0 drill-down.
    abs_ctx = await run_with_heartbeat(
        svc,
        websocket,
        "building context",
        build_abstract_context,
        svc.vault_graph,
        results,
        user_message,
        5,
        2,
        None,
    )
    context = abs_ctx.get("context", "")
    session_logger.log(
        "context_resolution",
        {
            "resolution": abs_ctx.get("resolution"),
            "l1_cards": abs_ctx.get("l1_cards", 0),
            "drill_down_used": abs_ctx.get("drill_down_used", False),
            "l0_drill": abs_ctx.get("l0_drill"),
            "context_length": len(context),
        },
    )

    # Context budgeting: ensure the retrieved context fits within the
    # model's token budget.
    try:
        await send_progress(svc, websocket, "budgeting context", {})
        _budgeted = svc.context_budgeter.budget(
            context, getattr(websocket, "conversation_history", [])
        )
        context = _budgeted["context"]
        if _budgeted["truncated"]:
            await send_progress(
                svc,
                websocket,
                "context_budgeted",
                {
                    "original_tokens": _budgeted["original_tokens"],
                    "budgeted_tokens": _budgeted["budgeted_tokens"],
                },
            )
            session_logger.log(
                "context_budget",
                {
                    "original_tokens": _budgeted["original_tokens"],
                    "budgeted_tokens": _budgeted["budgeted_tokens"],
                    "budget": _budgeted["budget"],
                    "chars_dropped": _budgeted["chars_dropped"],
                },
            )
    except Exception as e:  # noqa: BLE001
        session_logger.log("context_budget_failed", {"error": str(e)})
        await notify_console_failure(
            svc,
            websocket,
            f"context budgeting failed: {e}",
            context="context_budget",
        )

    # Phase 4: deterministic context filtering — drop irrelevant L1 card
    # sections so the big model sees only what's relevant to this query.
    # No longer gated on svc.small_client — the filter uses keyword
    # overlap, not an LLM call. Fail-safe: on any error, the full
    # context passes through unchanged.
    if len(context) > 3000:
        try:
            await send_progress(
                svc, websocket, "filtering context", {"chars": len(context)}
            )
            context = await filter_context(svc, user_message, context, session_logger)
            await send_progress(
                svc, websocket, "context_filtered", {"chars": len(context)}
            )
        except Exception as e:  # noqa: BLE001
            session_logger.log("context_filter_failed", {"error": str(e)})
            await notify_console_failure(
                svc,
                websocket,
                f"context filtering failed: {e}",
                context="context_filter",
            )

    # Inject the identity boot context so the agent wakes up coherent.
    identity_context = svc.identity.boot_context()

    # Gather live state so the system prompt is a real briefing, not static.
    autonomous_state = svc.autonomous_researcher.status()
    try:
        _t_gaps = loop.time()
        gaps = await run_with_heartbeat(
            svc,
            websocket,
            "finding gaps",
            svc.knowledge_curriculum.propose_next_gaps,
            10,
        )
        session_logger.log(
            "gaps_proposed",
            {
                "gap_count": len(gaps),
                "duration_ms": (loop.time() - _t_gaps) * 1000,
            },
        )
    except Exception as e:  # noqa: BLE001
        session_logger.log("gaps_propose_failed", {"error": str(e)})
        await notify_info(
            svc,
            websocket,
            "I couldn't scan for knowledge gaps right now. "
            "This doesn't affect your answer.",
        )
        gaps = []
    gaps_summary = (
        "\n".join(
            f"- [{g.get('kind')}] {g.get('topic')} (priority {g.get('priority', 0)})"
            for g in gaps[:10]
        )
        or "(none detected)"
    )

    # Build the combined tool list.
    custom_schemas = svc.self_improver.custom_tool_schemas()
    custom_tool_names = [s["function"]["name"] for s in custom_schemas]
    all_tools = build_tool_list(
        user_message, wm.render_for_prompt() if wm else "", custom_schemas
    )
    custom_tools_desc = (
        "\n".join(
            f"- {s['function']['name']}: {s['function']['description'][:100]}"
            for s in custom_schemas
        )
        if custom_schemas
        else "(none yet)"
    )

    # Build the DYNAMIC per-turn system prompt WITHOUT the vault context.
    # The briefing is rebuilt fresh every turn so newly-created tools and
    # edits appear immediately.
    _briefing = build_system_prompt_briefing(
        autonomous_state,
        gaps_summary,
        custom_tools=custom_tools_desc,
        custom_tool_names=custom_tool_names,
    )
    # Build the system prompt from identity + briefing verbatim.
    # The dynamic parts (wm block, procedure surface, conversation
    # recall) are added below.
    system_prompt = identity_context + "\n\n" + _briefing
    # NOTE: the working-memory task list is injected by the in-loop
    # composer (see the "System prompt is FROZEN after preflight build"
    # block below), NOT here. The list can change mid-turn as the model
    # calls plan_task/update_task, and having a single source of truth
    # for the composed conversation[0] prevents the double-append bug
    # (2026-08-06 refactor).

    # Procedure Discovery Service: surface one-line capability lines for
    # any procedures that FUSED retrieval matched for THIS query.
    # The procedure hint (suggested action) is intentionally NOT injected
    # into the system prompt here. It is appended as the FINAL system
    # message, AFTER the user message, so it is the last thing the model
    # reads before acting — giving it an immediate starting point.
    _suggested_action = ""
    try:
        _proc_idx = getattr(svc.procedure_tracker, "_stem_index", None)
        _proc_surface = build_procedure_surface(results, _proc_idx)
        if _proc_surface:
            system_prompt = system_prompt + "\n\n" + _proc_surface
            session_logger.log(
                "procedure_surface",
                {
                    "lines": _proc_surface.count("\n"),
                },
            )
            # --- Deterministic procedure routing hint ----------------
            # The small-model hint used to make a round-trip to Ollama to
            # pick which procedure matches the query. But FUSED retrieval
            # already ranked these same procedures by embedding+graph
            # similarity to the query — the best-matching one is simply
            # the highest-scored surfaced procedure. Reusing that score
            # is zero-LLM, zero-new-embedding, and never worse than the
            # small model's pick (it was choosing from the same surface).
            # Skipped for greetings/trivial messages (no procedure is the
            # right answer there) and for flagged procedures (can't run).
            try:
                _hint = _deterministic_procedure_hint(results, _proc_idx, user_message)
                if _hint:
                    _suggested_action = (
                        f"# SUGGESTED ACTION (pre-classification — "
                        f"verify before executing): consider "
                        f'execute_procedure("{_hint}") if it matches '
                        f"the task above."
                    )
                    session_logger.log(
                        "procedure_hint",
                        {
                            "hint": _hint,
                            "source": "fused_score",
                        },
                    )
            except Exception as e:  # noqa: BLE001
                session_logger.log("procedure_hint_failed", {"error": str(e)})
    except Exception as e:  # noqa: BLE001
        session_logger.log("procedure_surface_failed", {"error": str(e)})
        await notify_console_failure(
            svc,
            websocket,
            f"procedure surface build failed: {e}",
            context="procedure_surface",
        )

    # --- Route-Task preflight (intent classifier) ---------------------
    # Route-Task classifies intent and returns a procedure chain. It's
    # cheap (1 LLM call) and doesn't need a timeout. Think (the BS
    # detector / premise gate) is NOT run here — it's an opt-in tool
    # the big model can call via execute_procedure("Think") if it
    # decides a question needs structured premise checking. Running it
    # on every turn added 60-180s of latency for no benefit.
    #
    # Skipped for: trivial messages (uses _classify_trivial patterns),
    # resumed turns (model is mid-task).
    _preflight_chain: list[str] = []
    _preflight_results: list[dict[str, Any]] = []
    _preflight_category = ""
    _is_trivial = _classify_trivial(
        user_message, getattr(websocket, "conversation_history", []), wm
    )
    if not _is_trivial and not _resumed_tool_history:

        async def _run_route() -> dict[str, Any]:
            """Run Route-Task. Returns {"error": ...} on failure."""
            try:
                await send_progress(svc, websocket, "routing", {})
                _result = await _run_procedure_direct(
                    svc,
                    "Route-Task",
                    proc_args={"intent": user_message},
                    session_logger=session_logger,
                    user_message=user_message,
                    websocket=websocket,
                )
                await send_progress(svc, websocket, "routing_done", {})
                return _result
            except Exception as e:  # noqa: BLE001
                session_logger.log("preflight_route_failed", {"error": str(e)})
                await notify_console_failure(
                    svc,
                    websocket,
                    f"Route-Task procedure failed: {e}",
                    context="preflight_route",
                )
                return {"error": str(e)}

        # Run Route-Task (the sole preflight router).
        _route_result = await _run_route()

        # --- Process Route-Task result ---
        if not _route_result.get("error"):
            _route_output = _route_result.get("final_output", "")
            if _route_output:
                try:
                    _parsed = json.loads(_route_output)
                except (json.JSONDecodeError, TypeError):
                    _parsed = {}
                _preflight_category = _parsed.get("category", "")
                _preflight_chain = _parsed.get("procedure_chain", [])
                if isinstance(_preflight_chain, list) and _preflight_chain:
                    session_logger.log(
                        "preflight_route",
                        {
                            "category": _preflight_category,
                            "chain": _preflight_chain,
                        },
                    )
                    # Auto-execute small-cartridge chain steps.
                    # Big-cartridge steps are left for the big model.
                    for _chain_proc in _preflight_chain:
                        # Check cartridge before running.
                        _chain_cartridge = "big"
                        try:
                            _idx = (
                                getattr(svc.procedure_tracker, "_stem_index", None)
                                or {}
                            )
                            _entry = _idx.get(_chain_proc) or {}
                            _fm = _entry.get("frontmatter") or {}
                            _chain_cartridge = (
                                str(_fm.get("model_cartridge", "big")).strip().lower()
                                or "big"
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        if _chain_cartridge == "small":
                            await send_progress(
                                svc, websocket, f"running {_chain_proc}", {}
                            )
                            _chain_result = await _run_procedure_direct(
                                svc,
                                _chain_proc,
                                proc_args={"intent": user_message},
                                session_logger=session_logger,
                                user_message=user_message,
                                websocket=websocket,
                            )
                            _preflight_results.append(_chain_result)
                            session_logger.log(
                                "preflight_chain_step",
                                {
                                    "procedure": _chain_proc,
                                    "cartridge": _chain_cartridge,
                                    "passed": _chain_result.get("overall_passed"),
                                },
                            )
                        else:
                            # Big-cartridge: stop here, let the
                            # big model handle the rest.
                            _preflight_results.append(
                                {
                                    "procedure": _chain_proc,
                                    "cartridge": _chain_cartridge,
                                    "pending": True,
                                }
                            )
                            break

    # If we're resuming an interrupted turn, tell the model what it already
    # did so it continues instead of re-running tools.
    if _resumed_tool_history:
        _lines = [
            "# RESUMED TURN (you were interrupted mid-task and are "
            "continuing — do NOT re-run these tools, build on them):"
        ]
        for _h in _resumed_tool_history[-15:]:
            if isinstance(_h, dict):
                _lines.append(
                    f"- round {_h.get('round', '?')}: {_h.get('tool', '?')}"
                    f" →’ {_h.get('result_summary', '')[:120]}"
                )
        system_prompt = system_prompt + "\n\n" + "\n".join(_lines)

    # Inject conversation recall: prior turns relevant to this query.
    # This is what lets the bot "remember what it just said" — the
    # conversation index retrieved turns that match the user's question,
    # and we inject them here so the model sees its own recent history
    # alongside the vault context. Skipped when there are no results.
    _conv_ctx_str = ""
    if _conv_results:
        try:
            _conv_ctx_str = build_conversation_context(_conv_results)
            if _conv_ctx_str:
                system_prompt = system_prompt + "\n\n" + _conv_ctx_str
        except Exception as e:  # noqa: BLE001
            session_logger.log("conversation_context_inject_failed", {"error": str(e)})

    session_logger.log(
        "prompt_built",
        {
            "system_prompt_length": len(system_prompt),
            "vault_context_length": len(context),
            "context_length": len(context),
            "gaps_reported": len(gaps),
            "custom_tools": len(custom_schemas),
            "total_tools": len(all_tools),
            "conversation_turns_recalled": len(_conv_results),
        },
    )

    # Build the conversation for /api/chat using PERSISTENT per-session history.
    #
    # PROMPT-CACHING STRUCTURE (2026-08-15):
    # The conversation starts with up to THREE separate system messages at
    # the beginning (stable prompt, vault context, wm block). This split is
    # INTERNAL bookkeeping only — it lets the in-loop composer update the wm
    # block in place (conversation[2]) without rebuilding the stable prefix.
    # Ollama's /v1/chat/completions REJECTS multiple leading system messages
    # with a 500, so OllamaClient.chat() collapses them into ONE system
    # message right before sending (see merge_leading_system_messages).
    # Token-prefix caching is unaffected by the merge — the token sequence is
    # identical either way, so the stable prompt prefix still caches:
    #
    #   conversation[0] = STABLE system prompt (identity + briefing +
    #     procedure surface). This NEVER changes between rounds within
    #     a turn → the entire prefix is a cache hit every round after
    #     the first.
    #   conversation[1] = PER-QUERY vault context (retrieved notes). This
    #     changes between turns (different query → different notes) but
    #     is stable across rounds WITHIN a turn. It's the cache-break
    #     boundary: everything from here down is re-billed each round,
    #     but the stable prefix above it is cached.
    #   conversation[2] = Working-memory block (task list). Updated by
    #     the in-loop composer only when the task list changes, so it
    #     also benefits from prefix caching between rounds where the
    #     task list is unchanged. Stored as a system message so it
    #     stays at the beginning (Ollama rejects system messages after
    #     user messages).
    #
    # The wm block is added by the in-loop composer on the first round
    # (see the "System prompt is FROZEN after preflight build" block).
    # We pre-allocate slot [2] here so the composer can update it in
    # place without shifting indices.
    conversation = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "system",
            "content": (
                "# VAULT CONTEXT (retrieved for this query; compactable)\n"
                + context
            ),
        },
        {
            "role": "system",
            "content": "",  # wm block placeholder — filled by in-loop composer
        },
    ]
    conversation.extend(getattr(websocket, "conversation_history", []))
    conversation.append({"role": "user", "content": user_message})

    # --- Preflight chain results injection ----------------------------
    # If the framework already ran Route-Task and auto-executed small-
    # cartridge chain steps, inject the results as a system message
    # AFTER the user message. The big model sees what was already done
    # and what remains. This replaces the old "SUGGESTED ACTION" hint
    # with concrete pre-computed results.
    if _preflight_chain:
        _pf_lines = [
            "# PREFLIGHT ROUTING (framework already ran Route-Task)",
            f"Category: {_preflight_category}",
            f"Full chain: {' →’ '.join(_preflight_chain)}",
            "",
        ]
        _pending_chain: list[str] = []
        for _pr in _preflight_results:
            _pn = _pr.get("procedure", "?")
            if _pr.get("pending"):
                _pending_chain.append(_pn)
            else:
                _pf_lines.append(
                    f"✓ {_pn} — ALREADY EXECUTED (passed: "
                    f"{_pr.get('overall_passed', '?')})"
                )
                _fo = _pr.get("final_output", "")
                if _fo:
                    _pf_lines.append(f"  Result: {str(_fo)[:500]}")
        if _pending_chain:
            _pf_lines.append("")
            _pf_lines.append(
                f"# YOUR JOB: run the remaining chain steps: "
                f"{' →’ '.join(_pending_chain)}"
            )
            _pf_lines.append(
                "Call execute_procedure for each in order. Do NOT "
                "re-run the already-executed steps above. After the "
                "chain completes, synthesize a final answer for the "
                "user."
            )
        else:
            _pf_lines.append("")
            _pf_lines.append(
                "# YOUR JOB: all chain steps are done. Synthesize a "
                "final answer for the user from the results above."
            )
        _pf_block = "\n".join(_pf_lines)
        # Use 'user' role (not 'system') because Ollama's /v1/chat/completions
        # rejects system messages that appear after a user message
        # ("system message must be at the beginning"). This block is
        # context for the user's question, so 'user' role is correct.
        conversation.append({"role": "user", "content": _pf_block})
        session_logger.log(
            "preflight_chain_injected",
            {
                "category": _preflight_category,
                "chain": _preflight_chain,
                "executed": sum(1 for p in _preflight_results if not p.get("pending")),
                "pending": len(_pending_chain),
            },
        )
    elif _suggested_action:
        # Fallback: no preflight chain, use the old deterministic hint.
        # 'user' role, not 'system' — see preflight_chain_injected comment.
        conversation.append({"role": "user", "content": _suggested_action})
        session_logger.log(
            "suggested_action_injected",
            {
                "position": "post_user",
            },
        )

    # --- Confirmation context injection -------------------------------
    # When the user replies with a short confirmation ("yeah do that",
    # "do it", "go ahead", "proceed", "sounds good", "ok do it", "yes
    # do that", "go for it", etc.), the anaphor ("that"/"it") refers to
    # whatever the bot just proposed in its last assistant turn. The
    # conversation_index recall often returns 0 for these terse
    # messages (the rewrite doesn't match the prior turn), so the model
    # has no idea what it's agreeing to and asks "which thing?" or
    # re-searches the vault (sessions 1ebcb22d, 0b753aa7, dc11514c).
    # Fix: surface the last assistant message verbatim as a system
    # message labeled "the user is agreeing to THIS" so the model can
    # resolve the reference deterministically. Only fires when the last
    # assistant turn exists and isn't already trivially short.
    _CONFIRM_PATTERNS = (
        "yeah",
        "yes",
        "yep",
        "yup",
        "do it",
        "do that",
        "do this",
        "go ahead",
        "go for it",
        "proceed",
        "sounds good",
        "sounds great",
        "looks good",
        "looks great",
        "ok do",
        "okay do",
        "ok go",
        "okay go",
        "please do",
        "go on",
        "continue",
        "that works",
        "that's good",
        "that'd be great",
        "do whatever",
        "do your thing",
        "make it so",
        "make sure",
        "please go",
        "let's do",
        "lets do",
        "i'm down",
        "im down",
        "sure thing",
        "affirmative",
        "roger",
    )
    _msg_lower = user_message.strip().lower()
    _is_confirmation = len(_msg_lower) <= 60 and any(
        _msg_lower == p
        or _msg_lower.startswith(p + " ")
        or _msg_lower.startswith(p + "!")
        or _msg_lower.startswith(p + ".")
        or _msg_lower.startswith(p + ",")
        for p in _CONFIRM_PATTERNS
    )
    if _is_confirmation:
        _last_assistant = ""
        _hist = getattr(websocket, "conversation_history", [])
        for _m in reversed(_hist):
            if isinstance(_m, dict) and _m.get("role") == "assistant":
                _c = _m.get("content", "")
                if isinstance(_c, str) and len(_c.strip()) > 40:
                    _last_assistant = _c.strip()
                    break
        if _last_assistant:
            _confirm_ctx = (
                f"# THE USER IS AGREEING TO YOUR LAST PROPOSAL\n"
                f'The user just said: "{user_message}"\n'
                f"This is a confirmation/agreement. They are saying "
                f'"yes, do what you just proposed." Here is what you '
                f"proposed in your previous turn (this is what they are "
                f"agreeing to):\n\n"
                f"--- YOUR LAST TURN ---\n"
                f"{_last_assistant[:4000]}\n"
                f"--- END YOUR LAST TURN ---\n\n"
                f"Proceed with what you proposed. Do NOT ask the user "
                f"what they mean or re-search for context — they "
                f"agreed to the above. Call plan_task to structure the "
                f"work, then execute it."
            )
            # 'user' role, not 'system' — see preflight_chain_injected
            # comment (Ollama rejects post-user system messages).
            conversation.append({"role": "user", "content": _confirm_ctx})
            session_logger.log(
                "confirmation_context_injected",
                {
                    "user_message": user_message[:80],
                    "last_assistant_chars": len(_last_assistant),
                },
            )

    # Token-usage meter: report how full the context window is.
    try:
        _total_chars = sum(
            len(str(m.get("content", "") or ""))
            for m in conversation
            if isinstance(m, dict)
        )
        _used_tokens = max(1, _total_chars // 4)
        _ctx_window = svc.ollama_client.context_window(svc.ollama_client.llm_model)
        await svc.manager.send_personal_message(
            json.dumps(
                {
                    "type": "context_usage",
                    "model": svc.ollama_client.llm_model,
                    "context_window": _ctx_window,
                    "used_tokens": _used_tokens,
                    "available_tokens": max(0, _ctx_window - _used_tokens),
                    "messages": len(conversation),
                }
            ),
            websocket,
            session_logger=session_logger,
        )
    except Exception as _e:  # noqa: BLE001
        session_logger.log("context_usage_emit_failed", {"error": str(_e)})

    await svc.manager.send_personal_message(
        json.dumps({"type": "status", "content": "Thinking..."}),
        websocket,
        session_logger=session_logger,
    )

    # --- Trivial-turn shortcut (Phase 7: skip big model) ---------------
    # Simple greetings, thanks, and meta-questions are routed to the
    # small model directly, saving cloud tokens. Conservative: falls
    # through to the big-model agentic loop on any failure.
    if _classify_trivial(
        user_message, getattr(websocket, "conversation_history", []), wm
    ):
        try:
            from llm_client import get_small_client

            _trivial_client = get_small_client(session_logger)
            if _trivial_client is not None:
                _trivial_identity = svc.identity.boot_context()
                _trivial_prompt = (
                    f"{_trivial_identity}\n\n"
                    f'The user said: "{user_message}"\n'
                    f"Answer briefly and naturally. Be warm and concise. "
                    f"Do not call any tools."
                )
                _trivial_resp = _trivial_client.chat(
                    [{"role": "user", "content": _trivial_prompt}],
                    temperature=0.5,
                    stream=False,
                    think=False,
                    max_predict=512,
                )
                _trivial_text = ""
                if isinstance(_trivial_resp, dict):
                    _msg = _trivial_resp.get("message", {})
                    if isinstance(_msg, dict):
                        _trivial_text = _msg.get("content", "") or ""
                    if not _trivial_text:
                        _trivial_text = _trivial_resp.get(
                            "response", ""
                        ) or _trivial_resp.get("content", "")
                _trivial_text = (_trivial_text or "").strip()
                # Only accept the shortcut if the small model produced a
                # real response. Otherwise fall through to the big model.
                if len(_trivial_text) >= 10:
                    session_logger.log(
                        "trivial_turn_shortcut",
                        {
                            "user_message": user_message[:80],
                            "response_chars": len(_trivial_text),
                        },
                    )
                    await svc.manager.send_personal_message(
                        json.dumps({"type": "answer_chunk", "content": _trivial_text}),
                        websocket,
                        session_logger=session_logger,
                    )
                    await svc.manager.send_personal_message(
                        json.dumps({"type": "answer_done", "content": _trivial_text}),
                        websocket,
                        session_logger=session_logger,
                    )
                    # Explicitly log the assistant response so /sessions/{id}
                    # replay can find it even if the websocket_message log
                    # path was skipped or the session crashed mid-stream.
                    session_logger.log("assistant_response", {"text": _trivial_text})
                    # Save to conversation history.
                    _new_turns = [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": _trivial_text},
                    ]
                    websocket.conversation_history = (
                        getattr(websocket, "conversation_history", []) + _new_turns
                    )
                    save_history(
                        websocket.conversation_history,
                        session_id=getattr(websocket, "session_id", None),
                    )
                    session_logger.log(
                        "chat_end",
                        {
                            "answer_length": len(_trivial_text),
                            "thinking_length": 0,
                            "tool_rounds": 0,
                        },
                    )
                    return None  # trivial turn handled — caller skips the loop
                # Fall through — small model gave nothing useful.
                session_logger.log(
                    "trivial_turn_fallback_empty",
                    {
                        "user_message": user_message[:80],
                    },
                )
        except Exception as e:  # noqa: BLE001 — best-effort: fall through to big model
            session_logger.log(
                "trivial_turn_fallback_error",
                {
                    "error": str(e),
                    "user_message": user_message[:80],
                },
            )

    return (
        conversation,
        results,
        system_prompt,
        all_tools,
        custom_schemas,
        procedures_in_context,
        retrieved_paths,
        chat_start_time,
        loop,
    )


async def _finalize_turn(
    svc: Services,
    websocket: WebSocket,
    session_logger,
    loop,
    final_answer: str,
    thinking_text: str,
    total_chunks: int,
    round_idx: int,
    t0: float,
    _turn_token_totals: dict,
    _model_conversation: list,
    conversation: list,
    partial_path: Path,
    _cp,
) -> str:
    """Post-loop cleanup, grounding enforcement, answer delivery.

    Returns the (possibly modified) final_answer with grounding caution
    appended.
    """
    session_logger.log(
        "llm_generate",
        {
            "model": svc.ollama_client.llm_model,
            "stream": True,
            "total_chunks": total_chunks,
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
            "duration_ms": (loop.time() - t0) * 1000,
        },
    )

    # --- Grounding enforcement: verify the answer is grounded in vault ---
    # This is the code-level fix for Problem #1 from
    # [[Why-Vault-Knowledge-Loses-to-Model-Weights]]: no enforcement
    # mechanism. We check that the answer cites vault notes (wikilinks)
    # and that those wikilinks actually exist. An answer with zero
    # wikilinks is flagged as ungrounded. This is logged for calibration
    # and surfaced to the user as a caution when the grounding score
    # is below threshold.
    _grounding_score = 1.0
    _grounding_caution = ""
    if final_answer and len(final_answer) > 50:
        try:
            import re as _re

            # Extract all [[wikilinks]] from the answer
            _wikilinks = _re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]", final_answer)
            _unique_links = list(dict.fromkeys(_wikilinks))  # dedup, preserve order
            # Verify each wikilink exists in the vault graph
            _found = 0
            _missing = []
            for _wl in _unique_links[:20]:  # check up to 20
                _node = svc.vault_graph.get_note(_wl)
                if _node and _node.get("file_path"):
                    _found += 1
                else:
                    _missing.append(_wl)
            _total = len(_unique_links)
            if _total == 0:
                # Zero wikilinks — answer is completely ungrounded
                _grounding_score = 0.0
                _grounding_caution = (
                    "\n\n> ⚠️ **Grounding check**: This answer cites no vault notes. "
                    "It may be from model weights rather than your vault. "
                    "Consider asking me to verify or research this topic."
                )
            else:
                _grounding_score = _found / _total
                if _grounding_score < 0.5:
                    _grounding_caution = (
                        f"\n\n> ⚠️ **Grounding check**: Only {_found}/{_total} "
                        f"cited notes were found in the vault. "
                        f"Missing: {', '.join(_missing[:5])}. "
                        f"This answer may be partially ungrounded."
                    )
            session_logger.log(
                "grounding_check",
                {
                    "total_wikilinks": _total,
                    "found": _found,
                    "missing": _missing[:10],
                    "grounding_score": round(_grounding_score, 2),
                    "caution": bool(_grounding_caution),
                },
            )
            # Append the caution to the answer so the user sees it
            if _grounding_caution:
                final_answer += _grounding_caution
        except Exception as _e:  # noqa: BLE001 — best-effort
            session_logger.log("grounding_check_failed", {"error": str(_e)})

    # --- Token cost tracking: log and emit cumulative per-turn totals ---
    _turn_token_totals["rounds"] = round_idx + 1
    # Fallback estimate: if the cloud backend didn't return eval_stats
    # (Ollama Cloud proxy doesn't), estimate tokens from the final
    # conversation + the answer/thinking text using chars/4 heuristic.
    if _turn_token_totals["prompt_tokens"] == 0:
        _est_prompt = (
            sum(
                len(str(m.get("content", "") or ""))
                for m in _model_conversation
                if isinstance(m, dict)
            )
            // 4
        )
        _turn_token_totals["prompt_tokens"] = _est_prompt
    if _turn_token_totals["completion_tokens"] == 0:
        _est_completion = (len(final_answer) + len(thinking_text)) // 4
        _turn_token_totals["completion_tokens"] = max(1, _est_completion)
    _turn_token_totals["total_tokens"] = (
        _turn_token_totals["prompt_tokens"] + _turn_token_totals["completion_tokens"]
    )
    _turn_token_totals["estimated"] = True  # flag that these are estimates
    session_logger.log("token_usage", _turn_token_totals)
    # Persist to the session-level accumulator for cross-turn totals.
    try:
        session_logger.add_token_usage(
            _turn_token_totals["prompt_tokens"],
            _turn_token_totals["completion_tokens"],
        )
    except Exception:  # noqa: BLE001 — best-effort
        pass
    try:
        await svc.manager.send_personal_message(
            json.dumps(
                {
                    "type": "token_usage",
                    "prompt_tokens": _turn_token_totals["prompt_tokens"],
                    "completion_tokens": _turn_token_totals["completion_tokens"],
                    "total_tokens": _turn_token_totals["total_tokens"],
                    "rounds": _turn_token_totals["rounds"],
                }
            ),
            websocket,
            session_logger=session_logger,
        )
    except Exception as _e:  # noqa: BLE001
        session_logger.log("token_usage_emit_failed", {"error": str(_e)})

    await svc.manager.send_personal_message(
        json.dumps({"type": "answer_done", "content": final_answer}),
        websocket,
        session_logger=session_logger,
    )
    # Explicitly log the assistant's final response so /sessions/{id}
    # replay can find it even if the websocket_message log was missed
    # (e.g. session closed mid-stream, or the answer_done was sent but
    # the log_message call failed). This is the authoritative record.
    session_logger.log("assistant_response", {"content": final_answer})
    # Turn completed normally — clear the chat-loop checkpoint.
    if _cp is not None:
        try:
            _cp.clear()
        except Exception as e:  # noqa: BLE001
            session_logger.log("checkpoint_clear_failed", {"error": str(e)})
    # Refresh the token meter after the full turn.
    try:
        _total_chars = sum(
            len(str(m.get("content", "") or ""))
            for m in conversation
            if isinstance(m, dict)
        )
        _used_tokens = max(1, _total_chars // 4)
        _ctx_window = svc.ollama_client.context_window(svc.ollama_client.llm_model)
        await svc.manager.send_personal_message(
            json.dumps(
                {
                    "type": "context_usage",
                    "model": svc.ollama_client.llm_model,
                    "context_window": _ctx_window,
                    "used_tokens": _used_tokens,
                    "available_tokens": max(0, _ctx_window - _used_tokens),
                    "messages": len(conversation),
                }
            ),
            websocket,
            session_logger=session_logger,
        )
    except Exception as _e:  # noqa: BLE001
        session_logger.log("context_usage_emit_failed", {"error": str(_e)})
    session_logger.log(
        "chat_end",
        {
            "answer_length": len(final_answer),
            "thinking_length": len(thinking_text),
            "tool_rounds": round_idx + 1,
        },
    )

    return final_answer


async def _run_background_tasks(
    svc: Services,
    websocket: WebSocket,
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
                    "vaultbot_stuff/vaultbot_backend",
                    "node_modules",
                    ".git",
                    "vaultbot_stuff/learningMaterial",
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


async def handle_chat(
    svc: Services, websocket: WebSocket, user_message: str, session_logger
) -> None:
    """Agentic chat: the LLM reasons over the vault, calls tools when it hits
    a gap, and produces a grounded answer. The model drives — no framework
    enforcement, no phases, no auto-planning.
    """
    session_logger.log("chat_begin", {"user_message": user_message})

    # Clear the cancel flag at the start of a new turn so a stale flag
    # from a previous stop doesn't kill the new turn.
    websocket._cancelled = False

    # Working memory: per-session structured task list. The model writes a
    # plan via plan_task and updates it via update_task; the harness re-injects
    # the list into the system prompt every round so the model always sees
    # "what's done, what's next." One TaskList per websocket connection,
    # reset on /new. THE MODEL OWNS THIS — the framework never auto-advances
    # or force-completes anything.
    if not hasattr(websocket, "working_memory") or websocket.working_memory is None:
        websocket.working_memory = TaskList()
    wm = websocket.working_memory
    # A new user message is a NEW turn. We do NOT clear the working-memory
    # plan automatically — the plan persists across turns so the model can
    # handle interruptions and follow-up questions without losing its
    # place. The plan is cleared when:
    #   - the model explicitly calls plan_task with a new plan (set_plan
    #     replaces the old list), or
    #   - the model completes all tasks and we detect all_done(), or
    #   - the user types /new (ws.py clears it).
    # If the prior plan is fully done, clear it so the model starts fresh.
    if wm.has_plan() and wm.all_done():
        session_logger.log(
            "wm_plan_cleared_completed",
            {
                "previous_goal": wm.goal[:100],
                "completed_steps": sum(1 for t in wm.tasks if t.status == "completed"),
                "total_steps": len(wm.tasks),
            },
        )
        wm.clear()

    # Chat-loop checkpoint/resume: if a prior turn was interrupted mid-loop
    # and left a fresh checkpoint, resume it — restore the working-memory plan
    # and tell the model what it already did so it doesn't re-run tools.
    # Cleared on normal completion and /new.  Per-session: each tab gets its
    # own checkpoint file so concurrent sessions don't interfere.
    _session_id = getattr(websocket, "session_id", None)
    if _session_id:
        from chat_checkpoint import ChatLoopCheckpointer as _CLC

        _cp = _CLC.for_session(_session_id, session_logger)
    else:
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
                    except Exception as e:  # noqa: BLE001 — best-effort
                        session_logger.log("wm_restore_failed", {"error": str(e)})
                session_logger.log(
                    "chat_checkpoint_resumed",
                    {
                        "round_idx": _prior.get("round_idx", 0),
                        "tools_already_run": len(_resumed_tool_history),
                    },
                )
        except Exception as e:  # noqa: BLE001 — best-effort
            session_logger.log("chat_checkpoint_resume_failed", {"error": str(e)})

    # Chat-priority: pause the autonomous researcher so it doesn't compete
    # with this interactive turn for the Ollama GPU. Resumed in the finally
    # block so it always clears.
    svc.autonomous_researcher.pause_for_chat()
    try:
        _prep = await _prepare_turn(
            svc, websocket, user_message, session_logger, wm, _cp, _resumed_tool_history
        )
        if _prep is None:
            return  # trivial turn handled
        (
            conversation,
            results,
            system_prompt,
            all_tools,
            custom_schemas,
            procedures_in_context,
            retrieved_paths,
            chat_start_time,
            loop,
        ) = _prep

        # --- Agentic loop: model speaks →’ tool calls (if any) →’ repeat →’ final ---
        # The model decides to call tools, when, and when to stop. The framework
        # NEVER blocks, rejects, or auto-marks anything.
        final_answer = ""
        thinking_text = ""
        total_chunks = 0
        t0 = loop.time()
        _turn_tool_history: list = list(_resumed_tool_history)
        _tool_rounds_executed = 0
        _double_silent_once = False

        # Working-memory signature cache. conversation[0] is rebuilt only
        # when this changes across rounds, so provider prompt caches see a
        # stable prefix on rounds where the plan didn't move. Sentinel
        # object (never equals a real hash) so the first-round refresh
        # always fires and installs the wm block if present.
        _last_step_rag_key: Any = object()
        # Token cost tracking: accumulate per-round ollama_stats token counts
        # so we can log and emit a cumulative total per turn. This is the lever
        # for measuring cost-reduction changes — without it, we're tuning blind.
        _turn_token_totals = {"prompt_tokens": 0, "completion_tokens": 0, "rounds": 0}
        # Seen-content tracker: per-turn set of {file_path: {"source": str,
        # "lines": (start, end)|None, "round": int}}. Populated by vault_search
        # and code_read. Used to dedup vault_search results so the model doesn't
        # re-search for files it already has, breaking the search loop.
        _seen_content: dict[str, dict[str, Any]] = {}
        # Seed with the initial FUSED retrieval results — those files are
        # already in the vault context (conversation[1]) so the model has
        # already "seen" them. This prevents the first vault_search from
        # returning the same files that are already in context.
        for _r in results:
            _fp = _r.get("file_path", "") if isinstance(_r, dict) else ""
            if _fp:
                _seen_content[_fp] = {
                    "source": "initial_context",
                    "lines": None,
                    "round": -1,
                }
        # --- Findings ledger (anti-amnesia) ---
        # A per-turn list of 1-line entries: "R{n}: {tool} →’ {summary}".
        # Injected into the system prompt every round as "# FINDINGS SO FAR".
        # This survives history budget truncation because it lives in the
        # system prompt, not the conversation history. The model always sees
        # what it already did without re-reading dropped messages. Zero LLM
        # cost (deterministic).
        _findings: list[str] = []
        # Go-find-out escalation: counts consecutive vault_search calls
        # where ALL results were already seen. When this hits the threshold,
        # the harness auto-runs vault_research on the user's question to go
        # find the missing information on the web instead of looping.
        _consecutive_all_seen = 0
        _go_find_out_fired = False
        # Track the last vault_search query so go-find-out uses it as the
        # research topic instead of the raw user message. The user message
        # is a conversational instruction ("dude fix the researcher") — not
        # a web search query. The model's own vault_search query is a
        # focused research topic that the search engines can actually use.
        _last_search_query: str = ""
        # When go-find-out fires, the research summary is stored here so it
        # can be injected as a system message after the tool results are
        # appended. A system message is more authoritative than a tool result
        # — the model treats it as framework-level instruction, not optional data.
        _go_find_out_msg: str = ""

        # Partial-answer crash protection: write the streamed-so-far answer to a
        # temp file so a crash mid-stream doesn't lose it.
        # DEBOUNCED: writing on every chunk (50+ disk writes/sec) creates
        # consumer backpressure that throttles the LLM's streaming throughput.
        # We write at most once per second; the final answer is always written
        # at loop exit.
        import hashlib
        import tempfile
        import time as _time

        partial_dir = Path(tempfile.gettempdir()) / "vaultbot_partials"
        partial_dir.mkdir(parents=True, exist_ok=True)
        partial_id = hashlib.blake2b(
            (user_message + str(_time.time())).encode(), digest_size=12
        ).hexdigest()[:12]
        partial_path = partial_dir / f"partial_{partial_id}.md"
        write_partial(partial_path, user_message, "", "")
        _last_partial_write_s = 0.0  # debounce: write at most once per second

        try:
            # --- Core loop: the model drives, the harness supports ---
            # The model calls plan_task / update_task if it wants to stay on
            # track; the harness re-injects the wm block every round. The loop
            # ends when the model produces a turn with no tool calls.
            # NO read-loop detector, NO identical-call detector, NO stale-plan
            # detector, NO plan-enforcement gate, NO convergence nudge. The
            # model decides when it's done — same as Copilot's harness.
            round_idx = 0
            _MAX_ROUNDS = int(os.getenv("VAULTBOT_MAX_ROUNDS", "10000"))
            # Only two safety nets: failed-write streak (genuine thrash) and
            # the MAX_ROUNDS cap (runaway loop). Everything else is the model's
            # decision.
            # 10000 rounds allows multi-day autonomous work sessions. At ~12s
            # per round (typical for cloud models), 10K rounds ≈ 33 hours of
            # continuous work. The failed-write streak detector (3 consecutive
            # failed writes) is the primary anti-thrash guard — MAX_ROUNDS is
            # just a last-resort cap to prevent a truly infinite loop.
            _WRITE_TOOLS = frozenset(
                {
                    "safe_write",
                    "code_run",
                    "vault_safe_write",
                    "vault_append",
                    "vault_delete",
                    "tool_create",
                    "js_safe_write",
                    "execute_procedure",
                    "vault_research",
                }
            )
            _turn_failed_write_count = 0
            # --- Thought-loop detector (2026-08-15) ---
            # Counts consecutive rounds where the ONLY tool called is "thought".
            # The thought tool is a no-op scratchpad — it changes nothing in the
            # world. If the model calls it N times in a row without any other
            # tool, it's stuck in a thinking loop ("I need to stop thinking and
            # ACT" — but it never acts). This was observed in session 15e346b7
            # where the model called thought 20 consecutive times (R30-R47)
            # saying "I'll write the file next time" but never calling a write
            # tool, until the user manually hit Stop.
            #
            # Threshold: 5 consecutive thought-only rounds. At ~4s per round,
            # that's ~20s of zero progress — enough to be confident it's stuck,
            # not enough to waste the user's money on a long spiral.
            _THOUGHT_LOOP_THRESHOLD = int(
                os.getenv("VAULTBOT_THOUGHT_LOOP_LIMIT", "5")
            )
            _consecutive_thought_rounds = 0
            while round_idx < _MAX_ROUNDS:
                _check_cancelled(websocket)
                # --- Break condition 1: 3+ consecutive failed writes ---
                # A model hammering a broken tool is genuine thrash. Everything
                # else (reading, searching, planning, thinking) is the model's
                # business — the framework does not second-guess it.
                if _turn_failed_write_count >= 3:
                    session_logger.log(
                        "loop_exit",
                        {
                            "reason": "failed_write_streak",
                            "round": round_idx,
                            "total_tools": _tool_rounds_executed,
                            "failed_write_count": _turn_failed_write_count,
                        },
                    )
                    final_answer += (
                        "\n\n*The loop was stopped after 3 consecutive failed "
                        "writes. The findings above are what I was able to "
                        "determine, but I was unable to complete the task.*"
                    )
                    break

                # --- Break condition 2: consecutive thought-only rounds ---
                # If the model has called ONLY the thought tool for N
                # consecutive rounds, it's stuck in a thinking loop. Inject a
                # system message that forces it to act, and if it still loops
                # after the nudge, break out.
                if _consecutive_thought_rounds >= _THOUGHT_LOOP_THRESHOLD:
                    session_logger.log(
                        "thought_loop_detected",
                        {
                            "round": round_idx,
                            "consecutive_thought_rounds": _consecutive_thought_rounds,
                        },
                    )
                    # Inject a firm nudge. 'user' role, NOT 'system' — Ollama's
                    # /v1/chat/completions rejects system messages that appear
                    # after user/assistant messages ("system message must be at
                    # the beginning"), returning a 500. Same rule as the
                    # preflight_chain_injected / go_find_out injections below.
                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                "FRAMEWORK DIRECTIVE: You have called the "
                                "thought tool "
                                f"{_consecutive_thought_rounds} consecutive "
                                "times without taking any action. Stop "
                                "thinking and DO the thing you keep saying "
                                "you'll do. Call the actual tool (code_run, "
                                "safe_write, md_safe_replace, vault_safe_write, "
                                "etc.) RIGHT NOW. If you are stuck because a "
                                "tool keeps failing, explain the failure to "
                                "the user in a text response and end the turn. "
                                "Do NOT call the thought tool again."
                            ),
                        }
                    )
                    # Give it one more chance after the nudge. If it calls
                    # thought again, the counter will still be above threshold
                    # on the next iteration and we break.
                    if _consecutive_thought_rounds >= _THOUGHT_LOOP_THRESHOLD + 2:
                        session_logger.log(
                            "loop_exit",
                            {
                                "reason": "thought_loop",
                                "round": round_idx,
                                "consecutive_thought_rounds": _consecutive_thought_rounds,
                            },
                        )
                        final_answer += (
                            "\n\n*The loop was stopped after "
                            f"{_consecutive_thought_rounds} consecutive "
                            "thought-only rounds. I was stuck in a thinking "
                            "loop and unable to break out of it. The findings "
                            "above are what I was able to determine.*"
                        )
                        break

                # All tools available every round — no masking, no gate.
                _round_tools = all_tools

                session_logger.log(
                    "round_loop_top",
                    {
                        "round": round_idx,
                        "t_ms": loop.time() * 1000,
                        "conv_msgs": len(conversation),
                    },
                )

                # Track round index for tool execution context.
                websocket._chat_round_idx = round_idx

                # System prompt is FROZEN after preflight build (Priority A,
                # 2026-08-06). We only refresh the working-memory block when
                # the task list changed \u2014 the model owns that list
                # via plan_task/update_task and needs to see the current state.
                # Everything else that used to be re-injected here (per-step
                # RAG, findings ledger, procedure surface) has been removed
                # from the hot path: it churned the system prompt every round,
                # defeated provider prompt-caching (Anthropic / OpenAI /
                # OpenRouter all cache on prefix), and duplicated content the
                # model already had in conversation history. If the model
                # wants procedure info, it can call vault_search itself.
                #
                # PROMPT-CACHING STRUCTURE (2026-08-15):
                # conversation[0] = stable system prompt (NEVER touched here)
                # conversation[1] = per-query vault context (NEVER touched here)
                # conversation[2] = wm block (updated ONLY when the task list
                #   changes — when it's unchanged, the entire 3-message prefix
                #   is a cache hit, costing zero input tokens on the cached
                #   portion). This is the key: by NOT rebuilding conversation[0]
                #   every round, the stable prefix stays byte-identical and
                #   the provider's prefix cache fires.
                try:
                    _wm_block = wm.render_for_prompt() if wm else ""
                    _wm_sig = hash(_wm_block)
                    if _wm_sig != _last_step_rag_key:  # reused as wm-signature cache
                        conversation[2] = {
                            "role": "system",
                            "content": _wm_block,
                        }
                        _last_step_rag_key = _wm_sig
                    # When unchanged: conversation[2] stays as-is from the
                    # previous round. The prefix is byte-identical → cache hit.
                except Exception as e:  # noqa: BLE001 \u2014 wm render is best-effort; base system prompt passes through on failure
                    session_logger.log("wm_render_failed", {"error": str(e)})
                    conversation[2] = {"role": "system", "content": ""}

                # Stream the LLM response for this round.
                round_text = ""
                round_thinking = ""
                round_tool_calls = []
                round_finish_reason: str | None = None
                chunk_count = 0

                # —€—€ Proactive tool-result aging —€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€
                # Runs EVERY round, before the token cap. Stubs tool results
                # older than N rounds back to a 1-line summary so they don't
                # bloat the prompt and distract the model from the current
                # task. Unlike the token cap (which only fires when total
                # tokens exceed 60K), this is age-based and fires regardless
                # of total size — the model already processed those results
                # in prior rounds and doesn't need the full payload again.
                # Never breaks tool_call/tool_result pairing (stubs content
                # only); never touches the most recent N rounds.
                _pre_age_msgs = len(conversation)
                _pre_age_tokens = _estimate_conv_tokens(conversation)
                conversation = _age_old_tool_results(
                    conversation, session_logger=session_logger, round_idx=round_idx
                )
                _post_age_tokens = _estimate_conv_tokens(conversation)

                # —€—€ Hard token cap: GUARANTEED ceiling on prompt size —€—€—€—€—€—€
                # This runs EVERY round, right before the LLM call. Unlike
                # the context_budgeter (which only budgets vault context) and
                # preflight compression (which only fires once per turn at
                # 50% of context window), this is the enforcement layer that
                # guarantees the TOTAL conversation never exceeds the cap.
                # It prunes old tool-result content (never breaking pairs)
                # and, as a last resort, drops old middle messages.
                # The cap is set to 800K tokens by default — large enough for
                # cloud models with 1M context to work through long multi-round
                # tasks without losing context. For local models with smaller
                # context windows, the cap still applies as a hard ceiling.
                _pre_cap_msgs = len(conversation)
                _pre_cap_tokens = _estimate_conv_tokens(conversation)
                conversation = _enforce_token_cap(
                    conversation, session_logger=session_logger, round_idx=round_idx
                )
                _post_cap_tokens = _estimate_conv_tokens(conversation)

                # —€—€ Wait for model to finish loading —€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€
                # The startup-preload thread warms the model configured AT
                # BOOT. If the user switched models via the GUI since then
                # (or the model was evicted after keep_alive expired), the
                # current model is cold and NOTHING is loading it — polling
                # is_model_loaded() alone would spin the full timeout doing
                # nothing. So we ACTIVELY preload (a 1-token generate that
                # forces Ollama to load the model now), then poll with a
                # heartbeat until it's resident. preload_model() is a no-op
                # (returns True) for cloud backends, so this only loads
                # local Ollama models.
                _model_wait_t0 = loop.time()
                _model_wait_max = float(
                    os.environ.get("VAULTBOT_MODEL_LOAD_WAIT_S", "300")
                )
                # Kick off an ACTIVE preload in the executor. It returns
                # immediately if the model is already resident; otherwise it
                # blocks (up to 600s) while Ollama loads the model from disk.
                # We poll is_model_loaded() below with a heartbeat so the
                # user sees progress instead of a silent stall.
                _preload_task = loop.run_in_executor(
                    None, svc.ollama_client.preload_model
                )
                while True:
                    _loaded = await loop.run_in_executor(
                        None, svc.ollama_client.is_model_loaded
                    )
                    if _loaded:
                        break
                    _waited = loop.time() - _model_wait_t0
                    if _waited >= _model_wait_max:
                        session_logger.log(
                            "model_load_wait_timeout",
                            {"waited_s": _waited},
                        )
                        break
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "heartbeat",
                                "label": "loading model…",
                                "elapsed_ms": int(_waited * 1000),
                                "silent_ms": 0,
                                "chunks": 0,
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                    await asyncio.sleep(2)

                session_logger.log(
                    "llm_stream_start",
                    {
                        "round": round_idx,
                        "conv_msgs": len(conversation),
                        "conv_chars": sum(
                            len(str(m.get("content", "") or "")) for m in conversation
                        ),
                        "est_tokens": _post_cap_tokens,
                        "age_applied": _pre_age_tokens > _post_age_tokens,
                        "cap_applied": _pre_cap_tokens > _post_cap_tokens,
                        "t_ms": loop.time() * 1000,
                    },
                )
                # Log the prompt-cache structure: sizes of the stable
                # prefix (system prompt, vault context, wm block) so we
                # can see how much is cacheable vs. how much is re-billed
                # each round. The stable system prompt (conversation[0])
                # is the cacheable prefix; vault context + wm block are
                # cacheable WITHIN a turn (they don't change between
                # rounds unless the task list changes).
                _sys_msgs = [
                    m
                    for m in conversation
                    if isinstance(m, dict) and m.get("role") == "system"
                ]
                if _sys_msgs:
                    session_logger.log(
                        "prompt_cache_structure",
                        {
                            "round": round_idx,
                            "system_msg_count": len(_sys_msgs),
                            "stable_prompt_chars": len(
                                str(_sys_msgs[0].get("content", "") or "")
                            ),
                            "vault_context_chars": (
                                len(str(_sys_msgs[1].get("content", "") or ""))
                                if len(_sys_msgs) > 1
                                else 0
                            ),
                            "wm_block_chars": (
                                len(str(_sys_msgs[2].get("content", "") or ""))
                                if len(_sys_msgs) > 2
                                else 0
                            ),
                            "cacheable_prefix_chars": sum(
                                len(str(m.get("content", "") or ""))
                                for m in _sys_msgs
                            ),
                        },
                    )
                # Tool-history sanitization is a NARROW workaround for the
                # glm-5.2:cloud-via-Ollama bug (the model returns empty when it
                # sees ANY prior tool_calls / tool-role messages). Every other
                # provider (OpenAI-compatible, Anthropic, direct GLM cloud, real
                # Ollama models like qwen/llama/nemotron) gets NATIVE tool
                # protocol — the sanitizer corrupts it into flat system messages
                # and destroys tool_call IDs the model expects to reference.
                # See /memories/glm-ollama-tool-calls-broken.md.
                _model_name = (svc.ollama_client.llm_model or "").lower()
                _client_cls = svc.ollama_client.__class__.__name__.lower()
                _needs_sanitize = os.getenv(
                    "VAULTBOT_FORCE_SANITIZE_TOOL_HISTORY", "0"
                ) == "1" or ("ollama" in _client_cls and "glm" in _model_name)
                _model_conversation = (
                    _sanitize_tool_history(conversation)
                    if _needs_sanitize
                    else conversation
                )

                try:

                    def sync_stream():
                        session_logger.log(
                            "ollama_chat_call_enter",
                            {
                                "round": round_idx,
                                "t_ms": time.time() * 1000,
                            },
                        )
                        for chunk in svc.ollama_client.chat(
                            _model_conversation, tools=_round_tools, stream=True
                        ):
                            yield chunk
                        session_logger.log(
                            "ollama_chat_call_exit",
                            {
                                "round": round_idx,
                                "t_ms": time.time() * 1000,
                            },
                        )

                    gen = sync_stream()
                    round_t0 = loop.time()
                    last_chunk_at = loop.time()
                    while True:
                        next_chunk_task = loop.run_in_executor(
                            None, lambda: next(gen, {"done": True})
                        )
                        chunk = None
                        while chunk is None:
                            try:
                                chunk = await asyncio.wait_for(
                                    asyncio.shield(next_chunk_task), timeout=3.0
                                )
                            except TimeoutError:
                                elapsed = int((loop.time() - round_t0) * 1000)
                                since = int((loop.time() - last_chunk_at) * 1000)
                                await svc.manager.send_personal_message(
                                    json.dumps(
                                        {
                                            "type": "heartbeat",
                                            "label": f"thinking (round {round_idx + 1})",
                                            "elapsed_ms": elapsed,
                                            "silent_ms": since,
                                            "chunks": chunk_count,
                                        }
                                    ),
                                    websocket,
                                    session_logger=session_logger,
                                )
                            except asyncio.CancelledError:
                                # Don't call gen.close() here — the generator is
                                # running in an executor thread and close() from
                                # the main thread is unsafe (can raise RuntimeError).
                                # The HTTP response was already closed by
                                # cancel_active_stream() in ws.py, so the generator
                                # will exit on its own when iter_lines() raises.
                                raise
                        if (
                            chunk.get("done")
                            and not chunk.get("response")
                            and not chunk.get("tool_calls")
                        ):
                            if chunk.get("finish_reason"):
                                round_finish_reason = chunk["finish_reason"]
                            break
                        if chunk.get("eval_stats"):
                            _es = chunk["eval_stats"]
                            _prompt_tps = 0.0
                            _gen_tps = 0.0
                            if _es.get("prompt_eval_duration", 0) > 0:
                                _prompt_tps = _es["prompt_eval_count"] / (
                                    _es["prompt_eval_duration"] / 1e9
                                )
                            if _es.get("eval_duration", 0) > 0:
                                _gen_tps = _es["eval_count"] / (
                                    _es["eval_duration"] / 1e9
                                )
                            await svc.manager.send_personal_message(
                                json.dumps(
                                    {
                                        "type": "ollama_stats",
                                        "load_duration_ms": _es.get("load_duration", 0)
                                        / 1e6,
                                        "prompt_eval_count": _es.get(
                                            "prompt_eval_count", 0
                                        ),
                                        "prompt_eval_duration_ms": _es.get(
                                            "prompt_eval_duration", 0
                                        )
                                        / 1e6,
                                        "prompt_tokens_per_s": round(_prompt_tps, 1),
                                        "eval_count": _es.get("eval_count", 0),
                                        "eval_duration_ms": _es.get("eval_duration", 0)
                                        / 1e6,
                                        "gen_tokens_per_s": round(_gen_tps, 1),
                                        "total_duration_ms": _es.get(
                                            "total_duration", 0
                                        )
                                        / 1e6,
                                    }
                                ),
                                websocket,
                                session_logger=session_logger,
                            )
                            # Accumulate token counts for per-turn cost tracking.
                            _turn_token_totals["prompt_tokens"] += _es.get(
                                "prompt_eval_count", 0
                            )
                            _turn_token_totals["completion_tokens"] += _es.get(
                                "eval_count", 0
                            )
                            continue
                        chunk_count += 1
                        total_chunks += 1
                        last_chunk_at = loop.time()
                        thinking = chunk.get("thinking", "")
                        text = chunk.get("response", "")
                        tcs = chunk.get("tool_calls", [])
                        if thinking:
                            round_thinking += thinking
                            thinking_text += thinking
                            await svc.manager.send_personal_message(
                                json.dumps({"type": "thinking", "content": thinking}),
                                websocket,
                                session_logger=session_logger,
                            )
                        if text:
                            round_text += text
                            await svc.manager.send_personal_message(
                                json.dumps({"type": "answer_chunk", "content": text}),
                                websocket,
                                session_logger=session_logger,
                            )
                            # Debounced partial write: at most once per second.
                            # Per-chunk writes create disk I/O backpressure that
                            # throttles the LLM's streaming throughput.
                            _now_s = _time.time()
                            if _now_s - _last_partial_write_s >= 1.0:
                                write_partial(
                                    partial_path,
                                    user_message,
                                    final_answer + round_text,
                                    thinking_text,
                                )
                                _last_partial_write_s = _now_s
                        if tcs:
                            round_tool_calls.extend(tcs)
                except Exception as e:
                    session_logger.log_exception(e, context="ollama_client.chat")
                    if round_text:
                        write_partial(
                            partial_path,
                            user_message,
                            final_answer + round_text,
                            thinking_text,
                        )
                    from diagnostics import classify_error

                    diag = classify_error(e, {"stage": "thinking"})
                    await svc.manager.send_personal_message(
                        json.dumps({"type": "problem", "diagnosis": diag.to_dict()}),
                        websocket,
                        session_logger=session_logger,
                    )
                    raise

                session_logger.log(
                    "agent_round",
                    {
                        "round": round_idx,
                        "chunk_count": chunk_count,
                        "text_length": len(round_text),
                        "tool_calls": len(round_tool_calls),
                    },
                )

                # Append the assistant's turn to the conversation.
                assistant_msg = {"role": "assistant", "content": round_text}
                if round_thinking:
                    assistant_msg["thinking"] = round_thinking
                if round_tool_calls:
                    assistant_msg["tool_calls"] = round_tool_calls
                conversation.append(assistant_msg)

                # —€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€
                # Model produced text (no tool calls) →’ accept as final answer.
                # No dangling detection, no plan-continuation nudge, no text
                # inspection. The model decides when it's done.
                # —€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€—€
                if not round_tool_calls:
                    if round_text.strip():
                        # —€—€ Plan-continuation guard —€—€
                        # The model produced text without tool calls. Under the
                        # "model drives" architecture, the framework does NOT
                        # intervene when the model stops with unfinished tasks.
                        # The model is responsible for deciding when it's done.
                        # (test_no_framework_intervention_on_unfinished_plan
                        #  enforces this — no plan-completion checks here.)
                        final_answer += round_text
                        session_logger.log(
                            "turn_done",
                            {
                                "round": round_idx,
                                "answer_length": len(final_answer),
                                "tool_rounds": _tool_rounds_executed,
                                "finish_reason": round_finish_reason or "stop",
                            },
                        )
                        session_logger.log(
                            "loop_exit",
                            {
                                "reason": "natural_done",
                                "round": round_idx,
                                "total_tools": _tool_rounds_executed,
                                "total_text_chars": len(final_answer),
                                "findings_count": len(_findings),
                                "plan_had_tasks": wm.has_plan(),
                            },
                        )
                        break

                    # Double-silent failsafe: model returned nothing twice.
                    else:
                        if not _double_silent_once:
                            _double_silent_once = True
                            session_logger.log(
                                "silent_turn_retry", {"round": round_idx}
                            )
                            conversation.append(
                                {
                                    "role": "user",
                                    "content": "(no response received — please reply)",
                                }
                            )
                            round_idx += 1
                            continue
                        session_logger.log(
                            "agent_silent_fail_loud",
                            {
                                "round": round_idx,
                                "tool_rounds": _tool_rounds_executed,
                            },
                        )
                        raise AgentSilentError(
                            "Model returned nothing on two consecutive turns. "
                            "Please retry."
                        )

                # Model called tools →’ execute them and feed results back.
                _tool_rounds_executed += 1
                _double_silent_once = False

                # Accumulate non-final round text so partial file captures all streamed text.
                if round_text.strip() and round_text.strip() != ".":
                    final_answer += round_text

                # Execute each tool call and feed results back as tool-role messages.
                for tc in round_tool_calls:
                    _check_cancelled(websocket)
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    tool_args_raw = fn.get("arguments", "{}")
                    try:
                        tool_args = (
                            json.loads(tool_args_raw)
                            if isinstance(tool_args_raw, str)
                            else tool_args_raw
                        )
                    except json.JSONDecodeError:
                        tool_args = {}
                    tool_call_id = tc.get("id", tool_name)

                    # Track the last vault_search query for go-find-out: when the
                    # vault has no answer and the harness auto-triggers web
                    # research, the search query is a focused research topic (not
                    # the raw user message, which is conversational and produces
                    # zero search hits). See [[How-to-Fix-Research-Engine-Returning-Garbage]].
                    if tool_name == "vault_search":
                        _last_search_query = tool_args.get("query", "")

                    await svc.manager.send_personal_message(
                        json.dumps(
                            {"type": "tool_call", "tool": tool_name, "args": tool_args}
                        ),
                        websocket,
                        session_logger=session_logger,
                    )
                    session_logger.log(
                        "tool_call_requested",
                        {
                            "tool": tool_name,
                            "args": tool_args,
                            "round": round_idx,
                        },
                    )

                    # --- code_read whole-file auto-expand ---
                    # If the model calls code_read on a file it ALREADY read this
                    # turn (tracked in _seen_content), expand to the whole file.
                    # This collapses the 5-10 chunked 80-line reads into 1 call,
                    # which is how Copilot reads (whole file, large range). The
                    # model self-chunks because it sees total_lines and gets
                    # anxious; auto-expand removes the anxiety by giving it the
                    # full file on the repeat. First read stays as-is (the model
                    # chose that range for a reason).
                    if tool_name == "code_read":
                        _cr_fp = tool_args.get("file_path", "")
                        _cr_seen = _seen_content.get(_cr_fp)
                        if _cr_seen and _cr_fp:
                            # Already saw this file →’ give the whole file.
                            tool_args["start_line"] = 1
                            tool_args["end_line"] = 0  # 0 = whole file
                            session_logger.log(
                                "code_read_auto_expand",
                                {
                                    "round": round_idx,
                                    "file_path": _cr_fp,
                                    "prev_source": _cr_seen.get("source", "?"),
                                },
                            )

                    t_tool0 = loop.time()
                    session_logger.log(
                        "tool_exec_enter",
                        {
                            "tool": tool_name,
                            "round": round_idx,
                            "t_ms": t_tool0 * 1000,
                        },
                    )
                    try:
                        tool_result = await execute_agent_tool(
                            svc,
                            tool_name,
                            tool_args,
                            session_logger,
                            websocket,
                            user_message=user_message,
                        )
                    except Exception as e:  # noqa: BLE001
                        session_logger.log_exception(e, context=f"tool_{tool_name}")
                        tool_result = {"error": str(e)}
                        # Immediately report the tool crash to the console so the
                        # user sees it in red, not buried in a tool_result summary
                        # that the plugin renders with a green ✓. This is the
                        # "any failure of any kind is immediately reported" rule.
                        await notify_console_failure(
                            svc,
                            websocket,
                            f"tool {tool_name} crashed: {e}",
                            context="tool_exec",
                        )

                    _check_cancelled(websocket)

                    # --- Seen-content tracking for vault_search & code_read ----
                    # Track which files the model has seen this turn so we can
                    # dedup future vault_search results and break search loops.
                    if tool_name == "vault_search" and isinstance(tool_result, dict):
                        for _r in tool_result.get("results", []):
                            _fp = _r.get("file_path", "")
                            if _fp:
                                _seen_content[_fp] = {
                                    "source": "vault_search",
                                    "lines": None,
                                    "round": round_idx,
                                }
                    elif (
                        tool_name in ("code_read", "vault_read_note")
                        and isinstance(tool_result, dict)
                        and not tool_result.get("error")
                    ):
                        _fp = tool_result.get("file_path", "")
                        _sl = tool_result.get("start_line", 1)
                        _el = tool_result.get("end_line", 0)
                        _tl = tool_result.get("total_lines", 0)
                        if _fp:
                            # If the read covered the whole file, mark it fully seen.
                            # Otherwise track the specific line range.
                            _full = _sl <= 1 and (_el <= 0 or _el >= _tl)
                            _seen_content[_fp] = {
                                "source": tool_name,
                                "lines": None if _full else (_sl, _el),
                                "round": round_idx,
                            }

                    # --- Annotate vault_search results against seen content ------
                    # If the model calls vault_search and gets back files it
                    # already saw (via a previous vault_search or code_read),
                    # annotate them with "already_in_context: true" so the model
                    # can see that its searches are returning things it already
                    # has. When ALL results are already seen, inject a strong
                    # "stop searching" message. This breaks the "search anxiety"
                    # loop where the model keeps rephrasing the same query.
                    if tool_name == "vault_search" and isinstance(tool_result, dict):
                        _raw_results = tool_result.get("results", [])
                        _annotated, _already_seen = _dedup_seen_results(
                            _raw_results, _seen_content
                        )
                        if _already_seen:
                            _seen_names = ", ".join(
                                Path(o["file_path"]).stem for o in _already_seen[:10]
                            )
                            session_logger.log(
                                "search_results_deduped",
                                {
                                    "round": round_idx,
                                    "raw_count": len(_raw_results),
                                    "already_seen": len(_already_seen),
                                    "new_results": len(_annotated) - len(_already_seen),
                                    "seen_files": [
                                        Path(o["file_path"]).stem
                                        for o in _already_seen[:10]
                                    ],
                                },
                            )
                            tool_result["results"] = _annotated
                            _new_count = len(_annotated) - len(_already_seen)
                            if _new_count == 0:
                                # ALL results were already seen — increment the
                                # go-find-out escalation counter.
                                _consecutive_all_seen += 1
                                _go_find_out_threshold = int(
                                    os.getenv("VAULTBOT_GO_FIND_OUT_THRESHOLD", "3")
                                )
                                if (
                                    _consecutive_all_seen >= _go_find_out_threshold
                                    and not _go_find_out_fired
                                ):
                                    # GO FIND OUT: the vault doesn't have what the
                                    # model needs. Auto-trigger web research on the
                                    # user's original question and inject the result
                                    # as a tool result so the model gets new info.
                                    _go_find_out_fired = True
                                    # Use the last vault_search query as the
                                    # research topic, NOT the raw user message.
                                    # The user message is conversational ("dude
                                    # stop relying on model weights...") — search
                                    # engines return nothing for that and the
                                    # relevance gate filters out what little
                                    # comes back, producing zero-source research.
                                    # The model's own search query is a proper
                                    # research topic that the engines can handle.
                                    _research_topic = (
                                        _last_search_query or user_message[:200]
                                    )
                                    session_logger.log(
                                        "go_find_out_triggered",
                                        {
                                            "round": round_idx,
                                            "consecutive_all_seen": _consecutive_all_seen,
                                            "query": _research_topic[:100],
                                            "source": "last_search_query"
                                            if _last_search_query
                                            else "user_message",
                                        },
                                    )
                                    await svc.manager.send_personal_message(
                                        json.dumps(
                                            {
                                                "type": "status",
                                                "content": "Vault doesn't have enough — researching on the web...",
                                            }
                                        ),
                                        websocket,
                                        session_logger=session_logger,
                                    )
                                    try:
                                        _research_result = await execute_agent_tool(
                                            svc,
                                            "vault_research",
                                            {
                                                "topic": _research_topic,
                                                "depth": "quick",
                                            },
                                            session_logger,
                                            websocket,
                                            user_message=user_message,
                                        )
                                        # Build a compact summary of the research
                                        # result for the system message.
                                        _research_brief = ""
                                        if isinstance(_research_result, dict):
                                            _rb = _research_result.get(
                                                "synthesis_brief", ""
                                            )
                                            _kf = _research_result.get("key_facts", "")
                                            _np = _research_result.get("note_path", "")
                                            _parts = []
                                            if _rb:
                                                _parts.append(_rb[:2000])
                                            if _kf:
                                                _parts.append(f"Key facts:\n{_kf}")
                                            if _np:
                                                _parts.append(
                                                    f"A permanent note was "
                                                    f"created at {_np}."
                                                )
                                            _research_brief = "\n\n".join(_parts)
                                        # Store the system message for injection
                                        # after the tool results are appended.
                                        _go_find_out_msg = (
                                            f"# GO-FIND-OUT: Web research "
                                            f"completed automatically\n"
                                            f"The vault did not contain enough "
                                            f"information for this question after "
                                            f"{_consecutive_all_seen} searches. "
                                            f"I automatically researched it on the "
                                            f"web. Here are the results:\n\n"
                                            f"{_research_brief or '(no summary available)'}\n\n"
                                            f"Use these research results to answer "
                                            f"the user's question NOW. Do NOT call "
                                            f"vault_search again. Do NOT look for "
                                            f"procedures. You have the information "
                                            f"— write your answer."
                                        )
                                        # Also keep the tool result for the model
                                        # to see in the tool response.
                                        tool_result = {
                                            "go_find_out": True,
                                            "message": (
                                                "Web research completed "
                                                "automatically. See the system "
                                                "message for results. Use them "
                                                "to answer now — do NOT search "
                                                "again."
                                            ),
                                            "research": _research_result,
                                        }
                                    except Exception as e:  # noqa: BLE001
                                        session_logger.log(
                                            "go_find_out_failed", {"error": str(e)}
                                        )
                                        tool_result["message"] = (
                                            f"All search results are files you "
                                            f"already have, and auto-research "
                                            f"failed ({e}). Answer from what you "
                                            f"already have — do NOT search again."
                                        )
                                else:
                                    # Below threshold or already fired — tell the
                                    # model to stop searching and answer.
                                    tool_result["message"] = (
                                        f"All {len(_already_seen)} search results "
                                        f"are files you ALREADY retrieved this turn: "
                                        f"{_seen_names}. You have all the information "
                                        f"the vault contains on this topic. "
                                        f"STOP SEARCHING. Write your answer now "
                                        f"using the notes you already have. "
                                        f"Do NOT call vault_search again."
                                    )
                            else:
                                # Some new results — reset the counter.
                                _consecutive_all_seen = 0
                    session_logger.log(
                        "tool_exec_exit",
                        {
                            "tool": tool_name,
                            "round": round_idx,
                            "duration_ms": (loop.time() - t_tool0) * 1000,
                        },
                    )
                    # If the agent just created a tool, refresh the tool list.
                    if tool_name == "tool_create":
                        custom_schemas = svc.self_improver.custom_tool_schemas()
                        all_tools = build_tool_list(
                            user_message,
                            wm.render_for_prompt() if wm else "",
                            custom_schemas,
                        )
                    tool_duration = (loop.time() - t_tool0) * 1000
                    session_logger.log(
                        "tool_call_result",
                        {
                            "tool": tool_name,
                            "duration_ms": tool_duration,
                            "result_keys": list(tool_result.keys())
                            if isinstance(tool_result, dict)
                            else None,
                        },
                    )

                    # Procedure tracking: log validation results.
                    if tool_name in ("vault_lint", "safe_write", "code_run"):
                        try:
                            v_result, v_category, v_details = (
                                interpret_validation_result(tool_name, tool_result)
                            )
                            proc_name = (
                                procedures_in_context[0]
                                if procedures_in_context
                                else "no_procedure"
                            )
                            _task_desc = tool_name
                            svc.procedure_tracker.log_result(
                                procedure=proc_name,
                                task=_task_desc,
                                validation_result=v_result,
                                validation_tool=tool_name,
                                error_details=v_details,
                                category=v_category,
                            )
                        except Exception as e:  # noqa: BLE001
                            session_logger.log(
                                "procedure_tracking_failed", {"error": str(e)}
                            )
                            await notify_console_failure(
                                svc,
                                websocket,
                                f"procedure tracking failed: {e}",
                                context="procedure_tracker",
                            )
                    await svc.manager.send_personal_message(
                        json.dumps(
                            {
                                "type": "tool_result",
                                "tool": tool_name,
                                "summary": tool_result_summary(tool_name, tool_result),
                            }
                        ),
                        websocket,
                        session_logger=session_logger,
                    )

                    # Cap the tool result before appending.
                    # ALL tool results are bounded. code_read / vault_read_note
                    # get a very generous cap (read_result_cap, default 120K chars
                    # ≈ 30K tokens) so the model sees the WHOLE file in virtually
                    # all cases — only truly enormous files (500K+ chars) that
                    # would actually hurt the model are truncated. Other tool
                    # results get the standard cap (10K chars). The hard token
                    # cap (_enforce_token_cap) is the final guarantee, and it
                    # also exempts read tools from stubbing.
                    _READ_CAP = int(
                        os.getenv(
                            "VAULTBOT_READ_RESULT_CAP", str(TUNABLES.read_result_cap)
                        )
                    )
                    if tool_name in ("code_read", "vault_read_note"):
                        capped_result = truncate_tool_result(
                            tool_result, max_chars=_READ_CAP
                        )
                    else:
                        capped_result = truncate_tool_result(tool_result)
                    # All models get the SAME treatment: raw tool results,
                    # bounded only by truncate_tool_result for context-window
                    # safety. No per-model heuristics (no thinking-model
                    # digest, no name sniffing) — every model sees the same
                    # content. code_read / vault_read_note are never digested
                    # or structurally summarized; the raw content lands in the
                    # conversation as-is.
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "content": json.dumps(capped_result, default=str),
                        }
                    )
                    # Record for the chat-loop checkpoint.
                    _turn_tool_history.append(
                        {
                            "round": round_idx,
                            "tool": tool_name,
                            "result_summary": (
                                tool_result_summary(tool_name, tool_result) or ""
                            )[:200],
                        }
                    )

                # --- Failed-write tracking (the ONLY safety net) ---
                # Count failed writes. 3 consecutive failed writes = genuine
                # thrash (model hammering a broken tool). This is the only
                # framework-level break condition besides MAX_ROUNDS.
                _round_failed_write = False
                for tc in round_tool_calls:
                    _tn = (tc.get("function", {}) or {}).get("name", "")
                    if _tn in _WRITE_TOOLS:
                        _tr = None
                        for _m in reversed(conversation):
                            if _m.get("role") == "tool" and _m.get("tool_name") == _tn:
                                try:
                                    _tr = json.loads(_m.get("content", "{}"))
                                except (json.JSONDecodeError, TypeError):
                                    _tr = {}
                                break
                        if not _tool_actually_wrote(_tn, _tr):
                            _turn_failed_write_count += 1
                            _round_failed_write = True
                            session_logger.log(
                                "failed_write_detected",
                                {
                                    "round": round_idx,
                                    "tool": _tn,
                                    "result_keys": list(_tr.keys())
                                    if isinstance(_tr, dict)
                                    else None,
                                },
                            )
                        else:
                            # A successful write resets the failed-write counter.
                            _turn_failed_write_count = 0

                # --- Findings ledger: append a 1-line entry for this round ---
                _round_tools_summary = (
                    ", ".join(
                        (tc.get("function", {}) or {}).get("name", "?")
                        for tc in round_tool_calls
                    )
                    or "(no tools)"
                )
                _round_outcome = "write_failed" if _round_failed_write else "ok"
                _finding_entry = (
                    f"R{round_idx}: {_round_tools_summary} →’ {_round_outcome}"
                )
                if round_text.strip():
                    _finding_entry += f" | text: {round_text.strip()[:80]}"
                _findings.append(_finding_entry)
                session_logger.log(
                    "findings_ledger_updated",
                    {
                        "round": round_idx,
                        "entry": _finding_entry,
                        "total_findings": len(_findings),
                    },
                )

                # --- Go-find-out system message injection ------------------------
                # If go-find-out fired this round, inject the research results as a
                # user message AFTER the tool results are appended.
                # NOTE: 'user' role, not 'system' — Ollama's /v1/chat/completions
                # rejects system messages that appear after user/assistant/tool
                # messages ("system message must be at the beginning"). Using
                # 'user' role still conveys the instruction effectively.
                if _go_find_out_msg:
                    conversation.append(
                        {
                            "role": "user",
                            "content": _go_find_out_msg,
                        }
                    )
                    session_logger.log(
                        "go_find_out_system_msg_injected",
                        {
                            "round": round_idx,
                            "msg_chars": len(_go_find_out_msg),
                        },
                    )
                    # Clear so it only fires once.
                    _go_find_out_msg = ""

                # NO mid-loop truncation. Compression/pruning is a preflight
                # event (once per turn, before the first LLM call) — never
                # inside the tool-call loop. Mid-loop truncation destroys the
                # tool results the model JUST received and drops
                # tool_call/tool_result pairs the provider expects to be
                # paired. This is the Hermes Agent shape: within a turn, the
                # conversation only grows; between turns is when we compact.
                # See /memories/repo/hermes-agent-lessons.md.

                # --- Thought-loop tracking (2026-08-15) ---
                # Count consecutive rounds where the ONLY tool called is
                # "thought". Reset to 0 if any non-thought tool was called
                # (the model took action) or if no tools were called (the
                # model produced a text answer — the turn is ending).
                _round_tool_names = [
                    tc.get("function", {}).get("name", "?")
                    for tc in round_tool_calls
                ]
                if _round_tool_names and all(
                    t == "thought" for t in _round_tool_names
                ):
                    _consecutive_thought_rounds += 1
                else:
                    _consecutive_thought_rounds = 0

                # Loop back.
                round_idx += 1
                _check_cancelled(websocket)

                # Chat-loop checkpoint: snapshot the in-flight turn.
                # Includes the findings ledger so a restart mid-turn restores
                # the model's progress awareness, not just the partial answer.
                if _cp is not None:
                    try:
                        _cp.save(
                            {
                                "user_message": user_message,
                                "round_idx": round_idx,
                                "accumulated": final_answer,
                                "thinking": thinking_text,
                                "tool_history": _turn_tool_history,
                                "working_memory": snapshot_working_memory(wm),
                                "findings_ledger": list(_findings),
                            }
                        )
                        session_logger.log(
                            "checkpoint_saved",
                            {
                                "round": round_idx,
                                "findings_count": len(_findings),
                                "plan_has_tasks": wm.has_plan(),
                            },
                        )
                    except Exception as e:  # noqa: BLE001 — checkpoint is best-effort; the chat loop must not crash on save failure
                        session_logger.log(
                            "chat_checkpoint_save_failed", {"error": str(e)}
                        )

                # Stream history persistence: save the conversation-so-far to
                # disk after each tool round so a crash mid-turn doesn't lose
                # the entire turn's context. Best-effort: never breaks the loop.
                try:
                    # Per-message content cap for on-disk history. Was 4000 —
                    # too aggressive: a vault_read_note or code_read result gets
                    # chopped in the persisted copy, so the model on the NEXT
                    # round (or after a restart) sees a stub where its own read
                    # was. Default raised to 40000 chars, configurable via env.
                    _persist_cap = int(os.getenv("VAULTBOT_HISTORY_MSG_CAP", "40000"))
                    _hist_so_far = []
                    for _m in conversation:
                        if _m.get("role") == "system":
                            continue
                        _m2 = dict(_m)
                        _m2.pop("thinking", None)
                        _c = _m2.get("content")
                        if isinstance(_c, str) and len(_c) > _persist_cap:
                            _m2["content"] = (
                                _c[:_persist_cap] + "\n[...truncated in history...]"
                            )
                        _hist_so_far.append(_m2)
                    if len(_hist_so_far) > 0:
                        save_history(
                            _hist_so_far,
                            session_id=getattr(websocket, "session_id", None),
                        )
                except Exception as _e:  # noqa: BLE001 — stream history is best-effort; the loop must not crash on save failure
                    session_logger.log("stream_history_save_failed", {"error": str(_e)})

                # --- Round summary (diagnostic logging) ---
                # One event per round with everything needed to understand it
                # without reading any other event. Grep "round_summary" to
                # reconstruct a stalled session from the session log alone.
                session_logger.log(
                    "round_summary",
                    {
                        "round": round_idx,
                        "tools_called": [
                            tc.get("function", {}).get("name", "?")
                            for tc in round_tool_calls
                        ],
                        "tool_count": len(round_tool_calls),
                        "text_chars": len(round_text),
                        "thinking_chars": len(round_thinking),
                        "failed_write_count": _turn_failed_write_count,
                        "consecutive_thought_rounds": _consecutive_thought_rounds,
                        "has_plan": wm.has_plan(),
                        "findings_count": len(_findings),
                        "conv_chars": sum(
                            len(str(m.get("content", "") or "")) for m in conversation
                        ),
                        "conv_msgs": len(conversation),
                    },
                )

            # Round-cap safety log: if we exited the loop by hitting _MAX_ROUNDS
            # (not via a natural break), record it so it's visible in session logs.
            if round_idx >= _MAX_ROUNDS:
                session_logger.log(
                    "round_cap_hit",
                    {
                        "round": round_idx,
                        "answer_length": len(final_answer),
                        "tool_rounds": _tool_rounds_executed,
                    },
                )
                session_logger.log(
                    "loop_exit",
                    {
                        "reason": "max_rounds",
                        "round": round_idx,
                        "total_tools": _tool_rounds_executed,
                        "total_text_chars": len(final_answer),
                        "findings_count": len(_findings),
                        "failed_write_count": _turn_failed_write_count,
                    },
                )

        except Exception as e:  # noqa: BLE001
            session_logger.log_exception(e, context="handle_chat_agentic_loop")
            write_partial(partial_path, user_message, final_answer, thinking_text)
            session_logger.log(
                "partial_answer_saved_on_crash",
                {
                    "partial_path": str(partial_path),
                    "answer_chars": len(final_answer),
                },
            )
            raise
        finally:
            # If the answer completed normally, clean up the partial file.
            if final_answer and len(final_answer) > 50:
                try:
                    if partial_path.exists():
                        partial_path.unlink()
                except Exception as e:  # noqa: BLE001
                    session_logger.log("partial_cleanup_failed", {"error": str(e)})

        final_answer = await _finalize_turn(
            svc,
            websocket,
            session_logger,
            loop,
            final_answer,
            thinking_text,
            total_chunks,
            round_idx,
            t0,
            _turn_token_totals,
            _model_conversation,
            conversation,
            partial_path,
            _cp,
        )

        await _run_background_tasks(
            svc,
            websocket,
            session_logger,
            loop,
            user_message,
            final_answer,
            thinking_text,
            round_idx,
            _turn_token_totals,
            _turn_failed_write_count,
            conversation,
            retrieved_paths,
            chat_start_time,
            wm,
            _turn_tool_history,
            _findings,
        )

    finally:
        svc.autonomous_researcher.resume_after_chat()
