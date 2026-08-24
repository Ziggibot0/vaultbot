"""Turn preparation: calibration, RAG, context building, preflight routing.

Extracted from ``chat_handler.py`` -- ``prepare_turn`` runs BEFORE the
agentic loop starts. It does vault graph refresh, fused retrieval (query
rewrite + expand + parallel retrieve + rerank), conversation-aware
retrieval, context budgeting, procedure surface build, Route-Task
preflight routing, confirmation-context injection, and the trivial-turn
shortcut. Returns the fully-built conversation list plus all the
per-turn state the agentic loop needs, or ``None`` if the turn was
trivial and handled directly.

This is a leaf module in the chat-handler family (see ``chat_context.py``,
``chat_preflight.py``, ``chat_helpers.py`` for the established pattern).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from abstract_context import build_abstract_context
from agent_tools import (
    build_system_prompt_briefing,
    build_tool_list,
)
from chat_helpers import (
    notify_console_failure,
    run_with_heartbeat,
    send_progress,
)
from chat_preflight import (
    check_cancelled as _check_cancelled,
)
from chat_preflight import (
    deterministic_procedure_hint as _deterministic_procedure_hint,
)
from chat_preflight import (
    run_procedure_direct as _run_procedure_direct,
)
from citation_gate import build_allowed_citations
from conversation_index import build_conversation_context
from procedure_surface import build_procedure_surface
from services import Services
from small_model_filters import (
    dedup_results,
    expand_query,
    filter_context,
    rerank_results,
    rewrite_query_with_history,
)
from working_memory import TaskList


async def prepare_turn(
    svc: Services,
    websocket,
    user_message: str,
    session_logger,
    wm: TaskList,
    _cp,
    _resumed_tool_history: list,
) -> tuple | None:
    """Setup, RAG, preflight routing, trivial-turn shortcut.

    Returns (conversation, results, system_prompt, all_tools, custom_schemas,
    procedures_in_context, retrieved_paths, chat_start_time, loop,
    allowed_citations) or None if the turn was trivial and handled directly.
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
    except Exception as e:  # noqa: BLE001 -- best-effort
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
    # Phase 3: conversation-aware query rewriting -- rewrite the user's
    #   query using conversation context so follow-ups like "what was
    #   that thing?" resolve to the actual topic. Fail-safe: returns
    #   the original message on any failure.
    # Phase 2: small-model query expansion (fail-safe -- always includes
    #   the raw user message, so retrieval is never worse than today).
    # Phase 1: small-model reranking (over-fetch k=15, rerank down to 5
    #   via the Smart-Vault-Search procedure; fail-safe -- falls back to
    #   FUSED order on any error).
    t0 = loop.time()
    _rewritten_query = user_message  # default: no rewrite
    try:
        # Conversation-aware query rewriting: resolve references to prior
        # conversation so retrieval finds the right notes.
        _history = getattr(websocket, "conversation_history", [])
        await send_progress(svc, websocket, "rewriting query", {})

        # Fail-loud hook (issue #129): when the small model is unreachable,
        # surface a console warning so the operator knows retrieval is
        # degrading — instead of silently falling back every turn.
        def _on_rewrite_failure(_e):
            asyncio.run_coroutine_threadsafe(
                notify_console_failure(
                    svc,
                    websocket,
                    f"small model unreachable — query rewriting degraded: {_e}",
                    context="query_rewrite",
                ),
                loop,
            )

        _rewritten_query = await loop.run_in_executor(
            None,
            rewrite_query_with_history,
            user_message,
            _history,
            session_logger,
            _on_rewrite_failure,
        )
        queries = [_rewritten_query]
        if svc.small_client:
            await send_progress(svc, websocket, "expanding query", {})
            _expanded = expand_query(svc.small_client, _rewritten_query, session_logger)
            # Always include the original user message so retrieval is
            # never worse than baseline.
            queries = list(dict.fromkeys([_rewritten_query, *_expanded, user_message]))
        # Run all query retrievals concurrently. Each retrieve() is a
        # blocking call scheduled on the default executor; gathering them
        # turns N sequential round-trips into one parallel wave, cutting
        # retrieval latency ~Nx (3 queries → ~3x). A single heartbeat
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
        results = dedup_results(all_results) if len(queries) > 1 else all_results
        # Phase 1: deterministic reranking (embedding cosine similarity).
        # No longer gated on svc.small_client -- the reranker uses FAISS
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
        from chat_helpers import notify_problem

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

    # --- Route-Task preflight (intent classifier) ---------------------
    # Route-Task classifies intent and returns a procedure chain. It's
    # cheap (1 small-model LLM call). Think (the BS detector / premise
    # gate) is NOT run here -- it's an opt-in tool the big model can call
    # via execute_procedure("Think") if it decides a question needs
    # structured premise checking.
    #
    # Skipped for: resumed turns (model is mid-task).
    _preflight_chain: list[str] = []
    _preflight_results: list[dict[str, Any]] = []
    _preflight_category = ""
    if not _resumed_tool_history:

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

    # Conversation-aware retrieval: search the conversation index for
    # prior turns relevant to this query. This is what lets the bot
    # "remember what it just said" -- when the user asks a follow-up,
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
    from procedure_tracker import parse_procedures_from_results

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

    # Phase 4: deterministic context filtering -- drop irrelevant L1 card
    # sections so the big model sees only what's relevant to this query.
    # No longer gated on svc.small_client -- the filter uses keyword
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
    # reads before acting -- giving it an immediate starting point.
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
            # similarity to the query -- the best-matching one is simply
            # the highest-scored surfaced procedure. Reusing that score
            # is zero-LLM, zero-new-embedding, and never worse than the
            # small model's pick (it was choosing from the same surface).
            # Skipped for greetings/trivial messages (no procedure is the
            # right answer there) and for flagged procedures (can't run).
            try:
                _hint = _deterministic_procedure_hint(results, _proc_idx, user_message)
                if _hint:
                    _suggested_action = (
                        f"# SUGGESTED ACTION (pre-classification -- "
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

    # If we're resuming an interrupted turn, tell the model what it already
    # did so it continues instead of re-running tools.
    if _resumed_tool_history:
        _lines = [
            "# RESUMED TURN (you were interrupted mid-task and are "
            "continuing -- do NOT re-run these tools, build on them):"
        ]
        for _h in _resumed_tool_history[-15:]:
            if isinstance(_h, dict):
                _lines.append(
                    f"- round {_h.get('round', '?')}: {_h.get('tool', '?')}"
                    f" → {_h.get('result_summary', '')[:120]}"
                )
        system_prompt = system_prompt + "\n\n" + "\n".join(_lines)

    # Inject conversation recall: prior turns relevant to this query.
    # This is what lets the bot "remember what it just said" -- the
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
    # INTERNAL bookkeeping only -- it lets the in-loop composer update the wm
    # block in place (conversation[2]) without rebuilding the stable prefix.
    # Ollama's /v1/chat/completions REJECTS multiple leading system messages
    # with a 500, so OllamaClient.chat() collapses them into ONE system
    # message right before sending (see merge_leading_system_messages).
    # Token-prefix caching is unaffected by the merge -- the token sequence is
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
    # Build the closed-set citation directive (vault-centric provenance).
    # The model may ONLY cite notes that appear in the retrieved vault
    # context. We build the allowed-citations set from the rendered
    # context's `### [[Name]]` headers (backed by the raw search_results)
    # and append a directive to the VAULT CONTEXT message telling the
    # model exactly which notes it's allowed to cite. This is the
    # closed-set constraint that makes the big LLM a pure synthesizer.
    allowed_citations = build_allowed_citations(context, results)
    if allowed_citations:
        _allowed_stems = list(allowed_citations.keys())[:25]
        _allowed_block = (
            "\n\n--- CLOSED-SET CITATION RULE ---\n"
            "You are a SYNTHESIS ROUTER. Your world knowledge is DISABLED "
            "in this vault. You may ONLY make claims supported by the notes "
            "above, cited inline as [[Note-Name]] next to each claim.\n"
            "Allowed citation targets (cite ONLY from these):\n"
            + ", ".join(f"[[{s}]]" for s in _allowed_stems)
            + "\n\nIf you cannot support a claim from these notes, say "
            '"I don\'t know -- nothing in the vault covers this" and offer '
            "to call vault_research. Do NOT write from your own knowledge. "
            "A factual sentence with no [[wikilink]] from the allowed set is "
            "an UNCITED claim and is FORBIDDEN."
        )
        context = context + _allowed_block
        session_logger.log(
            "allowed_citations_built",
            {"count": len(allowed_citations), "stems": _allowed_stems[:10]},
        )
    else:
        # No notes retrieved -- the model should refuse + offer research.
        # The auto-research preflight gate (above) usually prevents this,
        # but keep the directive so the model knows not to hallucinate.
        _allowed_block = (
            "\n\n--- CLOSED-SET CITATION RULE ---\n"
            "NO vault notes were retrieved for this query. You have NOTHING "
            "to cite. Say \"I don't know -- nothing in the vault covers this. "
            'Want me to research it?" and offer to call vault_research. '
            "Do NOT answer from your own knowledge."
        )
        context = context + _allowed_block
        session_logger.log("allowed_citations_empty", {"result_count": len(results)})

    conversation = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "system",
            "content": (
                "# VAULT CONTEXT (retrieved for this query; compactable)\n" + context
            ),
        },
        {
            "role": "system",
            "content": "",  # wm block placeholder -- filled by in-loop composer
        },
    ]
    conversation.extend(getattr(websocket, "conversation_history", []))
    conversation.append(
        {"role": "user", "content": user_message, "timestamp": loop.time()}
    )

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
            f"Full chain: {' → '.join(_preflight_chain)}",
            "",
        ]
        _pending_chain: list[str] = []
        for _pr in _preflight_results:
            _pn = _pr.get("procedure", "?")
            if _pr.get("pending"):
                _pending_chain.append(_pn)
            else:
                _pf_lines.append(
                    f"✓ {_pn} -- ALREADY EXECUTED (passed: "
                    f"{_pr.get('overall_passed', '?')})"
                )
                _fo = _pr.get("final_output", "")
                if _fo:
                    _pf_lines.append(f"  Result: {str(_fo)[:500]}")
        if _pending_chain:
            _pf_lines.append("")
            _pf_lines.append(
                f"# YOUR JOB: run the remaining chain steps: "
                f"{' → '.join(_pending_chain)}"
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
        # 'user' role, not 'system' -- see preflight_chain_injected comment.
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
                f"what they mean or re-search for context -- they "
                f"agreed to the above. Call plan_task to structure the "
                f"work, then execute it."
            )
            # 'user' role, not 'system' -- see preflight_chain_injected
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

    # --- Trivial-turn shortcut removed (issue #166) ---------------------
    # The lexical trivial-turn classifier (exact/prefix string matching)
    # was removed: it misrouted real questions that merely started with a
    # greeting word (e.g. "yo can you access my google calendar?") to the
    # small model with no tool schemas. Every turn now goes through the
    # agentic loop, where the model sees the full tool list and decides.

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
        allowed_citations,
    )
