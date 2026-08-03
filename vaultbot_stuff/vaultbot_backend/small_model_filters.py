"""Small-model pre-filters — sit between retrieval and the big model.

Each function uses the small model cartridge (qwen3.5:0.8b when configured)
to filter, rerank, or compress data BEFORE the big model sees it. This cuts
the big model's input token cost on every turn.

The contract for every function: **fail-safe**. A broken or missing small
model degrades to exactly today's behavior — the big model sees the raw,
unfiltered data, never worse than now. Guards parse LLM output defensively;
on any failure (bad JSON, exception, garbage) the function returns the
input unchanged.

Three of the five functions delegate to existing procedures via
``execute_procedure`` (the same dispatch path the big model uses):
  - ``rerank_results`` → calls ``Smart-Vault-Search``
  - ``filter_context`` → calls ``Filter-Context-For-Query``
  - ``compress_window`` → calls ``Summarize-Conversation``

This means the vaultbot can tune the prompts by editing the procedure notes
— no code change or backend restart needed (procedures are recompiled on
each ``execute_procedure`` call).

See [[Cloud-Model-Obsolescence-Architecture]] and
[[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]].
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

# Caps shared with step_summarizer.py — keep the conversation bounded.
_MAX_SUMMARY_CHARS = 600
# Stop words for word-overlap guards (same set as _small_model_query).
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in",
    "on", "at", "and", "or", "it", "this", "that", "for", "with", "as", "by",
    "its", "has", "have", "from", "which", "not", "but", "can", "will", "do",
    "does", "did", "you", "your", "we", "our", "they", "their", "he", "she",
})

# ── Small-model circuit breaker ─────────────────────────────────────────
# When a small-model pre-filter times out or fails, retrying it every turn
# wastes 60s per turn and buys nothing (it'll keep failing for the same
# reason — usually the model is reasoning on a one-line task and hitting the
# timeout). The breaker remembers the failure for this many seconds and
# short-circuits subsequent calls to the fail-safe return path, so a broken
# helper costs zero latency after its first failure instead of 60s/turn.
#
# Per-helper keys: ("expand", 0), ("rerank", 0), ("filter", 0),
# ("compress", 0), ("query", 0), ("digest", 0).  Value is the monotonic
# timestamp of the last failure; entries expire after the cooldown.
_BREAKER_COOLDOWN_SECONDS = float(
    os.environ.get("VAULTBOT_SMALL_BREAKER_COOLDOWN", "1800"))  # 30 min
_breaker: dict[str, float] = {}


def _breaker_tripped(key: str) -> bool:
    """True if helper `key` failed recently and should be skipped this turn."""
    t = _breaker.get(key)
    if t is None:
        return False
    if (time.monotonic() - t) > _BREAKER_COOLDOWN_SECONDS:
        _breaker.pop(key, None)
        return False
    return True


def _breaker_trip(key: str) -> None:
    """Record a failure for `key`. Subsequent calls skip until the cooldown."""
    _breaker[key] = time.monotonic()


def _breaker_reset(key: str) -> None:
    """Clear a breaker after a success so the helper runs next turn."""
    _breaker.pop(key, None)


# ── Bounded-task call helper ────────────────────────────────────────────
# All small-model pre-filter calls go through this so they share:
#  - think=False  → no chain-of-thought on a one-line classification
#  - max_predict  → cap output so the model can't ramble for 60s
#  - short timeout → fail fast instead of blocking the whole turn
#  - circuit breaker → don't retry a helper that's failing every turn
#
# The old path let qwen3.5:0.8b reason for 60s on "rewrite this as a search
# query" and then time out — the single biggest contributor to the 3-minute
# retrieve. With think=False + a 512-token cap the same call returns in
# well under a second.
_SMALL_TIMEOUT = float(os.environ.get("VAULTBOT_SMALL_TIMEOUT_SECONDS", "12"))


def _client_chat(client: Any, prompt: str, system: str = "",
                 temperature: float = 0.2,
                 max_predict: int = 512,
                 breaker_key: str | None = None) -> str:
    """Call client.chat non-streaming with think=False + bounded output.

    Raises on failure. Callers wrap in try/except and fall back to the
    deterministic path. ``breaker_key`` short-circuits (returns "") if that
    helper failed recently — callers treat "" as "skip".
    """
    if breaker_key and _breaker_tripped(breaker_key):
        return ""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    # think=False is the whole game: a 0.8b model asked to classify or
    # rewrite doesn't need chain-of-thought, and reasoning was the 60s
    # bottleneck. max_predict caps the tail so a confused model can't
    # burn the full read timeout.
    resp = client.chat(msgs, temperature=temperature, stream=False,
                       think=False, max_predict=max_predict)
    text = ""
    if isinstance(resp, dict):
        msg = resp.get("message", {})
        if isinstance(msg, dict):
            text = msg.get("content", "") or ""
        if not text:
            text = resp.get("response", "") or resp.get("content", "")
    return (text or "").strip()


# ---------------------------------------------------------------------------
# Phase 1: RAG Reranking — delegates to Smart-Vault-Search procedure
# ---------------------------------------------------------------------------

async def rerank_results(svc: Any, query: str, results: list[dict],
                          k: int = 5, session_logger: Any = None) -> list[dict]:
    """Use the small model to rerank FUSED retrieval results by true relevance.

    Delegates to the ``Smart-Vault-Search`` procedure via
    ``execute_procedure``. The procedure reads each result's preview and
    judges relevance (high/medium/low) — not just keyword overlap.

    Fail-safe: on any error, returns the original results truncated to k.
    """
    if not results or len(results) <= k:
        return results

    # Circuit breaker: if rerank failed recently, skip it entirely.
    # The Smart-Vault-Search procedure's llm_generate step was timing out
    # (small model reasoning on relevance judgment) and failing every turn.
    # Skipping after the first failure saves 60s/turn and the deterministic
    # FUSED order is already a reasonable baseline.
    if _breaker_tripped("rerank"):
        return results[:k]

    # Cap at 15 candidates so the small model isn't reading the whole vault.
    candidates = results[:15]
    try:
        from chat_handler import execute_agent_tool
        # Build the hits arg in the shape Smart-Vault-Search expects.
        hits_arg = [
            {"file_path": r.get("file_path", ""),
             "name": r.get("name", ""),
             "keyword_score": r.get("score", 0),
             "preview": (r.get("snippet") or "")[:300]}
            for r in candidates
        ]
        proc_result = await execute_agent_tool(
            svc, "execute_procedure",
            {"procedure_name": "Smart-Vault-Search",
             "query": query, "hits": hits_arg},
            session_logger, None, user_message=query)

        if not isinstance(proc_result, dict) or not proc_result.get("overall_passed"):
            if session_logger:
                session_logger.log("small_model_rerank_skip", {
                    "reason": "procedure_failed",
                    "overall_passed": proc_result.get("overall_passed") if isinstance(proc_result, dict) else None,
                })
            # Trip the breaker: a procedure that fails (timeout, bad output)
            # will keep failing every turn for the same reason. Skipping it
            # after the first failure saves the full procedure latency on
            # every subsequent turn.
            _breaker_trip("rerank")
            return results[:k]

        final = proc_result.get("final_output", "")
        parsed = _parse_json_array(final)
        if parsed is None:
            if session_logger:
                session_logger.log("small_model_rerank_skip", {
                    "reason": "json_parse_failed",
                })
            # Same rationale: a small model that emits unparseable JSON once
            # will keep doing it. Trip the breaker so we stop paying its
            # latency every turn.
            _breaker_trip("rerank")
            return results[:k]

        # Map file_path → relevance rank (high=0, medium=1, low=2).
        rel_order = {"high": 0, "medium": 1, "low": 2}
        scored: list[tuple[int, float, dict]] = []
        for item in parsed:
            fp = item.get("file_path", "")
            rel = item.get("relevance", "medium").lower()
            rank = rel_order.get(rel, 1)
            # Find the original result dict for this file_path.
            orig = next((r for r in candidates if r.get("file_path") == fp), None)
            if orig:
                scored.append((rank, orig.get("score", 0.0), orig))

        if not scored:
            return results[:k]

        # Sort by relevance rank first, then by original FUSED score (tie-break).
        scored.sort(key=lambda t: (t[0], -t[1]))
        reranked = [t[2] for t in scored[:k]]

        # Guard: always include the top FUSED result if it wasn't returned.
        if results[0] not in reranked:
            reranked.append(results[0])
            reranked = reranked[:k]

        if session_logger:
            session_logger.log("small_model_rerank", {
                "candidates": len(candidates),
                "kept": len(reranked),
                "order_changed": [r.get("name", "") for r in reranked[:3]],
            })
        _breaker_reset("rerank")
        return reranked
    except Exception as e:
        if session_logger:
            session_logger.log("small_model_rerank_failed", {"error": str(e)})
        _breaker_trip("rerank")
        return results[:k]


# ---------------------------------------------------------------------------
# Phase 2: Query Expansion — direct small-model call
# ---------------------------------------------------------------------------

def expand_query(client: Any, user_message: str,
                 session_logger: Any = None) -> list[str]:
    """Use the small model to generate 2 alternative search queries.

    Always returns a list with the original user_message first (fail-safe).
    Expanded queries must share at least one content word with the original;
    otherwise they're dropped (same guard as _small_model_query).
    """
    queries = [user_message]
    if client is None:
        return queries
    # Circuit breaker: if expand failed recently, skip it entirely. A
    # reasoning-prone 0.8b model that times out once will time out every
    # turn for the same reason — retrying burns 60s/turn for nothing.
    if _breaker_tripped("expand"):
        return queries
    try:
        prompt = (
            "Rewrite this question as 2 alternative search queries that "
            "might find different relevant notes in a knowledge vault.\n\n"
            f"Question: {user_message[:300]}\n\n"
            "Output one query per line. No numbering, no preamble, no quotes.\n"
            "Queries:")
        text = _client_chat(client, prompt, temperature=0.3,
                            max_predict=256, breaker_key="expand")
        if not text:
            # Empty can mean the breaker tripped (skip silently) OR the
            # model returned nothing (treat as skip either way).
            return queries

        orig_words = _content_words(user_message)
        for line in text.split("\n"):
            q = line.strip().strip('"').strip("'").strip()
            if not q or len(q) < 3:
                continue
            # Guard: must share at least one content word with the original.
            if orig_words and not (_content_words(q) & orig_words):
                if session_logger:
                    session_logger.log("small_model_expand_dropped", {
                        "query": q[:80],
                    })
                continue
            if q.lower() not in (x.lower() for x in queries):
                queries.append(q)
            if len(queries) >= 3:
                break

        if session_logger:
            session_logger.log("small_model_expand", {
                "queries": queries,
            })
        _breaker_reset("expand")
        return queries
    except Exception as e:
        if session_logger:
            session_logger.log("small_model_expand_failed", {"error": str(e)})
        _breaker_trip("expand")
        return [user_message]


def dedup_results(results: list[dict]) -> list[dict]:
    """Merge results from multiple queries by file_path, keep max score."""
    merged: dict[str, dict] = {}
    for r in results:
        fp = r.get("file_path", "")
        if not fp:
            continue
        existing = merged.get(fp)
        if existing is None:
            merged[fp] = dict(r)
        else:
            if r.get("score", 0) > existing.get("score", 0):
                existing["score"] = r["score"]
            ch = existing.get("channels", set())
            if isinstance(ch, set):
                ch |= r.get("channels", set())
                existing["channels"] = ch
    return list(merged.values())


# ---------------------------------------------------------------------------
# Phase 4: Context Filtering — delegates to Filter-Context-For-Query procedure
# ---------------------------------------------------------------------------

# Regex to split context into sections by the L1 card header format.
_SECTION_RE = re.compile(r"^### \[\[([^\]]+)\]\]", re.MULTILINE)


async def filter_context(svc: Any, query: str, context: str,
                          session_logger: Any = None) -> str:
    """Use the small model to drop irrelevant context sections.

    Splits the context by ``### [[name]]`` headers (the L1 card format from
    abstract_context.py), calls the ``Filter-Context-For-Query`` procedure to
    pick which sections to keep, and reassembles.

    Fail-safe: on any error, returns the original context unchanged.
    """
    if len(context) < 3000:
        return context

    sections = _split_context_sections(context)
    if len(sections) < 3:
        return context  # not enough sections to filter

    # Circuit breaker: if context-filter failed recently, skip it.
    if _breaker_tripped("filter"):
        return context

    try:
        from chat_handler import execute_agent_tool
        sections_arg = [
            {"id": i, "title": s["title"], "preview": s["body"][:200]}
            for i, s in enumerate(sections)
        ]
        proc_result = await execute_agent_tool(
            svc, "execute_procedure",
            {"procedure_name": "Filter-Context-For-Query",
             "query": query, "sections": sections_arg},
            session_logger, None, user_message=query)

        if not isinstance(proc_result, dict) or not proc_result.get("overall_passed"):
            if session_logger:
                session_logger.log("small_model_filter_skip", {
                    "reason": "procedure_failed",
                })
            return context

        keep_ids = _parse_json_array(proc_result.get("final_output", ""))
        if keep_ids is None:
            return context

        # Keep_ids might be a list of ints or a list of {"id": int}.
        keep_set: set[int] = set()
        for item in keep_ids:
            if isinstance(item, int):
                keep_set.add(item)
            elif isinstance(item, dict) and "id" in item:
                keep_set.add(int(item["id"]))

        # Guard: always keep first (L2 MOC) and last (L0 drill-down) sections.
        keep_set.add(0)
        keep_set.add(len(sections) - 1)

        # Guard: if we'd keep fewer than 2, don't filter.
        if len(keep_set) < 2:
            return context

        kept = [s for i, s in enumerate(sections) if i in keep_set]
        filtered = "\n\n".join(s["raw"] for s in kept)

        if session_logger:
            session_logger.log("small_model_filter", {
                "total_sections": len(sections),
                "kept_sections": len(kept),
                "original_chars": len(context),
                "filtered_chars": len(filtered),
            })
        _breaker_reset("filter")
        return filtered
    except Exception as e:
        if session_logger:
            session_logger.log("small_model_filter_failed", {"error": str(e)})
        _breaker_trip("filter")
        return context


# ---------------------------------------------------------------------------
# Phase 5: Conversation Compression — delegates to Summarize-Conversation
# ---------------------------------------------------------------------------

def compress_window(messages: list[dict],
                    session_logger: Any = None) -> str | None:
    """Summarize dropped conversation messages via the Summarize-Conversation
    procedure so the big model retains context without re-reading raw noise.

    Returns a summary string, or None on any failure (caller drops messages
    as before — today's behavior).
    """
    if len(messages) <= 3:
        return None
    # Circuit breaker: skip summarization if it failed recently.
    if _breaker_tripped("compress"):
        return None

    try:
        from llm_client import get_small_client
        client = get_small_client(session_logger)
        if client is None:
            return None

        # Format the dropped messages as a transcript.
        lines = []
        for msg in messages:
            role = msg.get("role", "?")
            content = str(msg.get("content", "") or "")
            if msg.get("tool_calls"):
                tcs = msg["tool_calls"]
                names = [tc.get("function", {}).get("name", "?") for tc in tcs]
                content += f" [tool_calls: {', '.join(names)}]"
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"[{role}] {content}")
        transcript = "\n".join(lines)

        prompt = (
            "Summarize the following conversation history concisely. "
            "Preserve: the user's original goal, key decisions made, "
            "important tool results/facts learned, and any open questions. "
            "Be specific and brief (max 500 tokens). Return only the summary.\n\n"
            f"Transcript:\n{transcript}")
        summary = _client_chat(client, prompt, temperature=0.2,
                               max_predict=256, breaker_key="compress")
        if not summary or len(summary) < 20:
            return None

        summary = summary[:_MAX_SUMMARY_CHARS]
        if session_logger:
            session_logger.log("small_model_compress", {
                "messages_in": len(messages),
                "summary_chars": len(summary),
            })
        _breaker_reset("compress")
        return summary
    except Exception as e:
        if session_logger:
            session_logger.log("small_model_compress_failed", {"error": str(e)})
        _breaker_trip("compress")
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_words(text: str) -> set[str]:
    """Extract content words (non-stop-words) for overlap guards."""
    return {w.lower() for w in re.split(r"\s+", text)
            if w.lower() not in _STOP_WORDS and len(w) > 2}


def _parse_json_array(text: str) -> list | None:
    """Extract a JSON array from text. Returns None on failure."""
    if not text:
        return None
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return None
        return json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _split_context_sections(context: str) -> list[dict]:
    """Split context by ``### [[name]]`` headers into sections.

    Returns a list of {"title": str, "body": str, "raw": str} where raw
    includes the header line.
    """
    matches = list(_SECTION_RE.finditer(context))
    if not matches:
        return [{"title": "", "body": context, "raw": context}]

    sections = []
    for i, m in enumerate(matches):
        title = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(context)
        raw = context[start:end].strip()
        body = raw[len(m.group(0)):].strip()
        sections.append({"title": title, "body": body, "raw": raw})

    # Anything before the first header is the preamble (L2 MOC).
    if matches[0].start() > 0:
        preamble = context[:matches[0].start()].strip()
        if preamble:
            sections.insert(0, {"title": "(preamble)", "body": preamble, "raw": preamble})
    return sections