"""Turn retrieval: graph refresh + fused retrieval + rerank + telemetry.

Extracted from ``chat_turn_prep.py`` (issue #451) so ``prepare_turn`` stays
the orchestrator for routing, context, prompt, tools, and conversation
assembly while the retrieval phase lives in one focused, testable module.

This module owns the retrieval phase of a turn:

1. Keep the in-memory vault graph current with disk (``vault_graph.refresh``).
2. Conversation-aware query rewriting (resolve follow-up references).
3. Small-model query expansion (fail-safe: always includes the raw message).
4. Parallel fused retrieval across the (up to three) queries.
5. Deterministic reranking (FAISS vector reconstruction, no LLM call).
6. Fail-loud user notification when retrieval breaks (never silent).
7. ``vault_search`` telemetry.

It returns a typed ``TurnRetrievalResult`` consumed by ``prepare_turn``.
Behavior is preserved exactly from the inline code it replaces — including
cancellation points, progress-stage closure, the three-query cap, parallel
execution, dedup/rerank, the top-five contract, and fail-loud notification.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from chat_helpers import (
    notify_console_failure,
    notify_problem,
    run_with_heartbeat,
    send_progress,
)
from chat_preflight import check_cancelled as _check_cancelled
from services import Services
from small_model_filters import (
    dedup_results,
    expand_query,
    rerank_results,
    rewrite_query_with_history,
)


@dataclass
class TurnRetrievalResult:
    """Typed result of the turn-retrieval phase.

    ``results`` is the deduped, reranked top-five list of vault notes (or
    ``[]`` if retrieval failed — the fail-loud path already notified the
    user). ``rewritten_query`` is the conversation-aware rewrite (falls back
    to the raw user message). ``queries`` is the full query set actually
    retrieved against. ``duration_ms`` is the wall-clock time of the phase.
    """

    results: list[dict[str, Any]] = field(default_factory=list)
    rewritten_query: str = ""
    queries: list[str] = field(default_factory=list)
    duration_ms: int = 0


async def retrieve_turn_context(
    svc: Services,
    websocket,
    session_logger,
    user_message: str,
) -> TurnRetrievalResult:
    """Run the full retrieval phase for a turn and return its typed result.

    Preserves the exact behavior of the inline retrieval block it replaces
    in ``chat_turn_prep.prepare_turn``: graph refresh, cancellation check,
    query rewrite + expansion, parallel fused retrieval (three-query cap),
    dedup/rerank to the top five, fail-loud notification on any retrieval
    error, and ``vault_search`` telemetry.
    """
    loop = asyncio.get_event_loop()

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

    return TurnRetrievalResult(
        results=results,
        rewritten_query=_rewritten_query,
        queries=queries,
        duration_ms=int((loop.time() - t0) * 1000),
    )
