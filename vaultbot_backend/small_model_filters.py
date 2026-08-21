"""Small-model pre-filters — sit between retrieval and the big model.

Each function uses the small model cartridge (qwen3.5:4b when configured)
to filter, rerank, or compress data BEFORE the big model sees it. This cuts
the big model's input token cost on every turn.

The contract for every function: **fail-safe**. A broken or missing small
model degrades to exactly today's behavior — the big model sees the raw,
unfiltered data, never worse than now. Guards parse LLM output defensively;
on any failure (bad JSON, exception, garbage) the function returns the
input unchanged.

Two of the functions delegate to existing procedures via
``execute_procedure`` (the same dispatch path the big model uses):
  - ``rerank_results`` → calls ``Smart-Vault-Search``
  - ``filter_context`` → calls ``Filter-Context-For-Query``

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

from config import TUNABLES

# Caps shared with step_summarizer.py — keep the conversation bounded.
_MAX_SUMMARY_CHARS = 600
# Stop words for word-overlap guards (same set as _small_model_query).
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "to",
        "of",
        "in",
        "on",
        "at",
        "and",
        "or",
        "it",
        "this",
        "that",
        "for",
        "with",
        "as",
        "by",
        "its",
        "has",
        "have",
        "from",
        "which",
        "not",
        "but",
        "can",
        "will",
        "do",
        "does",
        "did",
        "you",
        "your",
        "we",
        "our",
        "they",
        "their",
        "he",
        "she",
    }
)

# ── Small-model circuit breaker ─────────────────────────────────────────
# When a small-model pre-filter times out or fails, retrying it every turn
# wastes 60s per turn and buys nothing (it'll keep failing for the same
# reason — usually the model is reasoning on a one-line task and hitting the
# timeout). The breaker remembers the failure for this many seconds and
# short-circuits subsequent calls to the fail-safe return path, so a broken
# helper costs zero latency after its first failure instead of 60s/turn.
#
# Per-helper keys: "expand", "rerank", "filter", "query",
# "digest", "relevance".  Value is the monotonic timestamp of the last
# failure; entries expire after the cooldown.
_BREAKER_COOLDOWN_SECONDS = float(
    os.environ.get("VAULTBOT_SMALL_BREAKER_COOLDOWN", "1800")
)  # 30 min
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
_SMALL_TIMEOUT = float(
    os.environ.get(
        "VAULTBOT_SMALL_TIMEOUT_SECONDS", str(TUNABLES.small_timeout_seconds)
    )
)


def _client_chat(
    client: Any,
    prompt: str,
    system: str = "",
    temperature: float = 0.2,
    max_predict: int = 512,
    breaker_key: str | None = None,
) -> str:
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
    resp = client.chat(
        msgs,
        temperature=temperature,
        stream=False,
        think=False,
        max_predict=max_predict,
    )
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


async def rerank_results(
    svc: Any, query: str, results: list[dict], k: int = 5, session_logger: Any = None
) -> list[dict]:
    """Deterministic reranking using embedding cosine similarity.

    Replaces the old small-model ``Smart-Vault-Search`` procedure call.
    The procedure routed through ``execute_procedure`` (compile + 3 code
    steps + 1 LLM call + JSON parse) to have a 0.8b model read 300-char
    previews and say "high/medium/low" — a keyword-matching task dressed up
    as relevance judgment.

    The deterministic replacement reconstructs each candidate's stored
    embedding from the FAISS index (zero Ollama calls — the vectors are
    already in the index), computes cosine similarity with the query
    embedding (one Ollama call, already made by the FUSED vector channel),
    and sorts. This is pure numpy and reuses signals that were already
    computed.

    Fail-safe: on any error, returns the original results truncated to k.
    """
    if not results or len(results) <= k:
        return results

    candidates = results[:15]
    try:
        import numpy as _np

        indexer = getattr(svc, "vault_indexer", None)
        if indexer is None or indexer.index is None:
            return results[:k]

        # Get the query embedding (one Ollama call — the same one the FUSED
        # vector channel already made, but we don't have a cache for it
        # here. This is still cheaper than the old procedure path which
        # made an LLM call PLUS went through the procedure execution
        # machinery.)
        try:
            query_vec = indexer._get_embedding(query)
        except Exception:  # noqa: BLE001 — best-effort, falls back to FUSED order
            # If the embedding service is down, fall back to FUSED order.
            return results[:k]

        query_vec = _np.asarray(query_vec, dtype=_np.float32).reshape(1, -1)
        # Manual L2 normalization (faiss.normalize_L2 is not available in
        # numpy and faiss may not be imported here).
        norm = _np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        scored: list[tuple[float, float, dict]] = []
        for r in candidates:
            fp = r.get("file_path", "")
            if not fp:
                # No file_path — can't reconstruct. Keep FUSED score.
                scored.append((r.get("score", 0.0), r.get("score", 0.0), r))
                continue
            stored = indexer.reconstruct_embedding(fp)
            if stored is None:
                # Not in the index — fall back to FUSED score.
                scored.append((r.get("score", 0.0), r.get("score", 0.0), r))
                continue
            # Cosine similarity = dot product of normalized vectors.
            cos_sim = float(_np.dot(query_vec[0], stored))
            # Blend: 70% embedding cosine, 30% FUSED score (which includes
            # graph + backlink signals the embedding alone doesn't capture).
            fused_score = r.get("score", 0.0)
            blended = 0.7 * cos_sim + 0.3 * fused_score
            scored.append((blended, fused_score, r))

        # Sort by blended score descending.
        scored.sort(key=lambda t: -t[0])
        reranked = [t[2] for t in scored[:k]]

        # Guard: always include the top FUSED result if it wasn't returned.
        if results[0] not in reranked:
            reranked.append(results[0])
            reranked = reranked[:k]

        if session_logger:
            session_logger.log(
                "deterministic_rerank",
                {
                    "candidates": len(candidates),
                    "kept": len(reranked),
                    "order_changed": [r.get("name", "") for r in reranked[:3]],
                },
            )
        return reranked
    except Exception as e:  # noqa: BLE001 — best-effort, falls back to FUSED order
        if session_logger:
            session_logger.log("deterministic_rerank_failed", {"error": str(e)})
        return results[:k]


# ---------------------------------------------------------------------------
# Phase 2: Query Expansion — direct small-model call
# ---------------------------------------------------------------------------


def expand_query(
    client: Any, user_message: str, session_logger: Any = None
) -> list[str]:
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
            "Queries:"
        )
        text = _client_chat(
            client, prompt, temperature=0.3, max_predict=256, breaker_key="expand"
        )
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
                    session_logger.log(
                        "small_model_expand_dropped",
                        {
                            "query": q[:80],
                        },
                    )
                continue
            if q.lower() not in (x.lower() for x in queries):
                queries.append(q)
            if len(queries) >= 3:
                break

        if session_logger:
            session_logger.log(
                "small_model_expand",
                {
                    "queries": queries,
                },
            )
        _breaker_reset("expand")
        return queries
    except Exception as e:  # noqa: BLE001 — best-effort, falls back to original query
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


async def filter_context(
    svc: Any, query: str, context: str, session_logger: Any = None
) -> str:
    """Deterministically drop irrelevant context sections by keyword overlap.

    Replaces the old small-model ``Filter-Context-For-Query`` procedure call.
    The procedure routed through ``execute_procedure`` (compile + 3 code
    steps + 1 LLM call + JSON parse) to have a 0.8b model read section titles
    + 200-char previews and say "keep/drop" — a keyword-matching task.

    The deterministic replacement splits the context by ``### [[name]]``
    headers (same as before), computes keyword overlap between the query's
    content words and each section's title + preview, and keeps sections
    with any overlap. Always keeps the first (L2 MOC) and last (L0
    drill-down) sections. Pure string matching — zero LLM calls.

    Fail-safe: on any error, returns the original context unchanged.
    """
    if len(context) < 3000:
        return context

    sections = _split_context_sections(context)
    if len(sections) < 3:
        return context  # not enough sections to filter

    try:
        query_words = _content_words(query)
        if not query_words:
            return context  # no content words to match against

        keep_set: set[int] = set()
        for i, s in enumerate(sections):
            # Score by keyword overlap between query and section title + preview.
            section_text = (s["title"] + " " + s["body"][:300]).lower()
            section_words = _content_words(section_text)
            # Keep if any content word from the query appears in the section.
            if query_words & section_words:
                keep_set.add(i)

        # Guard: always keep first (L2 MOC) and last (L0 drill-down) sections.
        keep_set.add(0)
        keep_set.add(len(sections) - 1)

        # Guard: if we'd keep fewer than 2, don't filter.
        if len(keep_set) < 2:
            return context

        # Guard: if we'd keep everything, don't bother reassembling.
        if len(keep_set) == len(sections):
            return context

        kept = [s for i, s in enumerate(sections) if i in keep_set]
        filtered = "\n\n".join(s["raw"] for s in kept)

        if session_logger:
            session_logger.log(
                "deterministic_filter",
                {
                    "total_sections": len(sections),
                    "kept_sections": len(kept),
                    "original_chars": len(context),
                    "filtered_chars": len(filtered),
                },
            )
        return filtered
    except Exception as e:  # noqa: BLE001 — best-effort, returns unfiltered context
        if session_logger:
            session_logger.log("deterministic_filter_failed", {"error": str(e)})
        return context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def rewrite_query_with_history(
    user_message: str,
    conversation_history: list[dict],
    session_logger: Any = None,
    on_failure: Any = None,
) -> str:
    """Rewrite a user query using conversation context for better retrieval.

    When the user asks a follow-up that references prior conversation
    ("what was that thing you found?", "tell me more about that"),
    the raw query is ambiguous for RAG — it doesn't contain the actual
    topic. This function uses the small model to produce a self-contained
    query that incorporates context from the recent conversation.

    Fail-safe: on any failure, returns the original user_message unchanged.
    The original message is ALWAYS included in the expanded queries list,
    so retrieval is never worse than baseline.

    ``on_failure`` (optional callable) is invoked with the exception when
    the small model is unreachable, so the caller can surface a fail-loud
    console warning (issue #129) instead of degrading silently.

    Returns a string suitable for FUSED retrieval (the rewritten query).
    """
    if not user_message.strip():
        return user_message

    # Build a compact recent-context string from the last few turns.
    # Only user + assistant messages (not tool results) — the model needs
    # the conversational gist, not the tool chatter.
    recent: list[str] = []
    for msg in (conversation_history or [])[-10:]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = str(msg.get("content", "") or "")
        if not content.strip():
            continue
        # Cap each turn to keep the prompt small.
        recent.append(f"[{role}] {content[:400]}")
    if not recent:
        return user_message  # no history to contextualize with

    context_str = "\n".join(recent[-6:])  # last ~3 exchanges

    try:
        from llm_client import get_small_client_or_big

        client = get_small_client_or_big(session_logger)
        if client is None:
            return user_message

        prompt = (
            "You are a query rewriter for a retrieval system. Given the user's "
            "new message and the recent conversation, produce a self-contained "
            "search query that captures what the user is ACTUALLY looking for. "
            "Resolve pronouns and references ('that', 'it', 'the thing you "
            "mentioned') to the actual topic from the conversation. "
            "Output ONLY the rewritten query, nothing else.\n\n"
            f"Recent conversation:\n{context_str}\n\n"
            f"User's new message: {user_message[:500]}\n\n"
            "Rewritten search query:"
        )
        resp = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            stream=False,
            think=False,
            max_predict=128,
        )
        text = ""
        if isinstance(resp, dict):
            msg = resp.get("message", {})
            if isinstance(msg, dict):
                text = msg.get("content", "") or ""
            if not text:
                text = resp.get("response", "") or resp.get("content", "")
        text = (text or "").strip().split("\n")[0].strip()
        # Sanity check: if the rewrite is empty or too long, fall back.
        if text and 3 < len(text) <= 500:
            # Overlap guard: if the rewrite shares ZERO content words with
            # the original, it's a hallucination — the small model went
            # off the rails and invented a completely different query.
            # Fall back to the original to avoid sending RAG down the
            # wrong path.  (See session 0e5239af — the rewriter turned
            # "read GOALS.md after restart" into a fabricated story about
            # "GOAL_SHELL.md" from a different user's file path.)
            _orig_words = _content_words(user_message)
            _rewrite_words = _content_words(text)
            if _orig_words and _rewrite_words and not (_orig_words & _rewrite_words):
                if session_logger:
                    session_logger.log(
                        "query_rewrite_rejected",
                        {
                            "original": user_message[:100],
                            "rewritten": text[:200],
                            "reason": "zero content-word overlap",
                        },
                    )
                return user_message
            if session_logger:
                session_logger.log(
                    "query_rewritten",
                    {
                        "original": user_message[:100],
                        "rewritten": text[:200],
                    },
                )
            return text
        return user_message
    except Exception as e:  # noqa: BLE001
        if session_logger:
            session_logger.log("query_rewrite_failed", {"error": str(e)})
        # Fail-loud (issue #129): the small model is down — surface it so
        # the operator knows retrieval is degrading, instead of silently
        # falling back every turn.
        if on_failure is not None:
            try:
                on_failure(e)
            except Exception:  # noqa: BLE001 — the callback must never break the fallback
                pass
        return user_message


def _content_words(text: str) -> set[str]:
    """Extract content words (non-stop-words) for overlap guards."""
    return {
        w.lower()
        for w in re.split(r"\s+", text)
        if w.lower() not in _STOP_WORDS and len(w) > 2
    }


def _parse_json_array(text: str) -> list | None:
    """Extract a JSON array from text. Returns None on failure."""
    if not text:
        return None
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return None
        return json.loads(text[start : end + 1])
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
        body = raw[len(m.group(0)) :].strip()
        sections.append({"title": title, "body": body, "raw": raw})

    # Anything before the first header is the preamble (L2 MOC).
    if matches[0].start() > 0:
        preamble = context[: matches[0].start()].strip()
        if preamble:
            sections.insert(
                0, {"title": "(preamble)", "body": preamble, "raw": preamble}
            )
    return sections
