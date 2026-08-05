"""Conversation-aware retrieval — search the conversation history like RAG.

THE PROBLEM THIS SOLVES
-----------------------
The VaultBot's RAG pipeline searched ONLY vault notes (FAISS index + wikilink
graph + backlinks). When the user says "what was that thing you just found?"
the retriever returns vault notes about "things" — not the actual conversation
the user just had. The conversation history was available as raw messages in
the LLM context, but the sliding window dropped them after 40 messages,
and they were never searchable.

This module indexes conversation turns (user + assistant) into a lightweight
in-memory FAISS index that can be queried alongside the vault. When the user
asks a follow-up that references prior conversation, relevant prior turns
are retrieved and injected into the context — so the bot can "remember what
it just said."

DESIGN
------
- In-memory FAISS index (not persisted — conversation history is already
  persisted in conversation_state.json; this index is rebuilt from that on
  startup and updated incrementally as turns complete).
- Each turn (user message + assistant answer) is one document. The embedding
  is computed from the combined text (user question + assistant answer),
  capped at ~2000 chars to stay within nomic-embed-text's sweet spot.
- Thread-safe (the chat loop + any background thread may read/write).
- Best-effort: never raises. On any failure, retrieval returns empty (the
  vault RAG still works; this is additive).
- Degraded mode: if the embedding model isn't available, falls back to
  keyword matching (simple token overlap) so conversation recall works
  even without a model.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# How many conversation turns to keep in the index.  Conversations are
# bounded by conversation_state.json's MAX_TURNS (40), so this matches.
MAX_INDEXED_TURNS = 80  # generous — the index is tiny (80 × 768 floats)
# Max chars of a turn to embed.  nomic-embed-text works best under ~4000
# chars; we cap at 2000 to keep it fast (one short embedding call per turn).
MAX_TURN_CHARS = 2000
# How many conversation turns to retrieve per query.  3 is enough to
# surface the relevant prior exchange without flooding context.
DEFAULT_K = 3
# Minimum similarity score to include a turn (normalized [0,1]).
MIN_SCORE = 0.10


@dataclass
class _ConvTurn:
    """One indexed conversation turn."""
    turn_id: int           # sequential, monotonically increasing
    user_message: str
    assistant_answer: str
    timestamp: float
    embedding: np.ndarray | None = None  # None in keyword-only mode


class ConversationIndex:
    """Searchable index of recent conversation turns.

    Thread-safe. In-memory. Rebuilt from conversation_state.json on startup
    and updated incrementally as turns complete.

    Usage:
        idx = ConversationIndex(ollama_client)  # or None for keyword-only
        idx.add_turn(user_message, assistant_answer)
        results = idx.search("what was that thing about X?", k=3)
        # results: [{"turn_id", "user_message", "assistant_answer", "score"}]
    """

    def __init__(self, ollama_client: Any = None) -> None:
        self._ollama = ollama_client
        self._lock = threading.Lock()
        self._turns: list[_ConvTurn] = []
        self._next_id = 1
        self._faiss_index: Any = None  # faiss.IndexFlatL2, lazily built
        self._faiss_ids: list[int] = []  # turn_id for each row in faiss
        self._log = logger

    # ------------------------------------------------------------------ #
    # Building / rebuilding
    # ------------------------------------------------------------------ #

    def rebuild_from_history(self, history: list[dict[str, Any]]) -> None:
        """Rebuild the index from a persisted conversation history list.

        Called on startup (after loading conversation_state.json) and on
        /new (with an empty list). Best-effort: on any failure, the index
        is left empty (not partially built) so retrieval degrades cleanly.
        """
        with self._lock:
            self._turns = []
            self._next_id = 1
            self._faiss_index = None
            self._faiss_ids = []

        if not history:
            return

        # Pair user + assistant messages into turns. The history is a flat
        # list of role/content dicts; we scan for user→assistant pairs.
        turns_to_add: list[tuple[str, str, float]] = []
        i = 0
        while i < len(history):
            msg = history[i]
            if not isinstance(msg, dict):
                i += 1
                continue
            role = msg.get("role", "")
            if role == "user":
                user_text = str(msg.get("content", "") or "")
                # Find the next assistant message
                assistant_text = ""
                j = i + 1
                while j < len(history):
                    next_msg = history[j]
                    if isinstance(next_msg, dict) and next_msg.get("role") == "assistant":
                        assistant_text = str(next_msg.get("content", "") or "")
                        break
                    if isinstance(next_msg, dict) and next_msg.get("role") == "user":
                        break  # next user message without an answer — skip
                    j += 1
                if user_text.strip() or assistant_text.strip():
                    turns_to_add.append((user_text, assistant_text, time.time()))
                i = j + 1
            else:
                i += 1

        # Add turns outside the lock (embedding calls hit Ollama).
        for user_msg, assistant_msg, ts in turns_to_add[-MAX_INDEXED_TURNS:]:
            self.add_turn(user_msg, assistant_msg, timestamp=ts, _skip_lock=True)

    # ------------------------------------------------------------------ #
    # Adding turns
    # ------------------------------------------------------------------ #

    def add_turn(self, user_message: str, assistant_answer: str,
                 timestamp: float | None = None,
                 _skip_lock: bool = False) -> None:
        """Add a completed conversation turn to the index.

        Best-effort: embedding failures result in a turn with embedding=None
        (keyword-only mode). Never raises.
        """
        if not user_message and not assistant_answer:
            return

        ts = timestamp if timestamp is not None else time.time()
        combined = f"User: {user_message[:MAX_TURN_CHARS]}\nAssistant: {assistant_answer[:MAX_TURN_CHARS]}"

        # Try to embed (outside lock for the Ollama call).
        embedding = None
        if self._ollama is not None:
            try:
                emb = self._ollama.embeddings(combined[:4000])
                embedding = np.array(emb, dtype=np.float32)
                # Normalize for cosine similarity via L2.
                norm = np.linalg.norm(embedding)
                if norm > 0:
                    embedding = embedding / norm
            except Exception as e:  # noqa: BLE001
                self._log.debug("conversation_index embed failed: %s", e)
                embedding = None

        turn = _ConvTurn(
            turn_id=0,  # assigned under lock
            user_message=user_message[:500],
            assistant_answer=assistant_answer[:2000],
            timestamp=ts,
            embedding=embedding,
        )

        if not _skip_lock:
            self._lock.acquire()
        try:
            turn.turn_id = self._next_id
            self._next_id += 1
            self._turns.append(turn)
            # Bound the list.
            if len(self._turns) > MAX_INDEXED_TURNS:
                self._turns = self._turns[-MAX_INDEXED_TURNS:]
            # Add to FAISS index if we have an embedding.
            if embedding is not None:
                self._add_to_faiss(turn)
        finally:
            if not _skip_lock:
                self._lock.release()

    def _add_to_faiss(self, turn: _ConvTurn) -> None:
        """Add a turn's embedding to the FAISS index (caller holds lock)."""
        if turn.embedding is None:
            return
        try:
            import faiss
            if self._faiss_index is None:
                dim = len(turn.embedding)
                self._faiss_index = faiss.IndexFlatL2(dim)
                self._faiss_ids = []
            vec = turn.embedding.reshape(1, -1).astype(np.float32)
            self._faiss_index.add(vec)
            self._faiss_ids.append(turn.turn_id)
            # If we trimmed the turns list, trim the FAISS index too by
            # rebuilding (IndexFlatL2 doesn't support removal).  This is
            # rare (only when MAX_INDEXED_TURNS is exceeded) so the rebuild
            # cost is acceptable.
            if len(self._faiss_ids) > MAX_INDEXED_TURNS:
                self._rebuild_faiss()
        except Exception as e:  # noqa: BLE001
            self._log.debug("conversation_index faiss add failed: %s", e)

    def _rebuild_faiss(self) -> None:
        """Rebuild the FAISS index from the current turns (caller holds lock)."""
        try:
            import faiss
            embedded = [t for t in self._turns if t.embedding is not None]
            if not embedded:
                self._faiss_index = None
                self._faiss_ids = []
                return
            dim = len(embedded[0].embedding)
            self._faiss_index = faiss.IndexFlatL2(dim)
            self._faiss_ids = []
            for t in embedded:
                vec = t.embedding.reshape(1, -1).astype(np.float32)
                self._faiss_index.add(vec)
                self._faiss_ids.append(t.turn_id)
        except Exception as e:  # noqa: BLE001
            self._log.debug("conversation_index faiss rebuild failed: %s", e)
            self._faiss_index = None
            self._faiss_ids = []

    # ------------------------------------------------------------------ #
    # Searching
    # ------------------------------------------------------------------ #

    def search(self, query: str, k: int = DEFAULT_K) -> list[dict[str, Any]]:
        """Search the conversation index for turns relevant to the query.

        Returns a list of dicts: {"turn_id", "user_message",
        "assistant_answer", "score", "timestamp"}.

        Uses FAISS vector search when embeddings are available, with a
        keyword-overlap fallback for turns that don't have embeddings (or
        when the embedding model isn't configured).

        Best-effort: returns [] on any failure. Never raises.
        """
        if not query or not query.strip():
            return []
        with self._lock:
            turns_snapshot = list(self._turns)
            faiss_index = self._faiss_index
            faiss_ids = list(self._faiss_ids)

        if not turns_snapshot:
            return []

        results: list[dict[str, Any]] = []

        # --- Vector search path (when we have embeddings) ---
        if faiss_index is not None and self._ollama is not None:
            try:
                q_emb = self._ollama.embeddings(query[:4000])
                q_vec = np.array(q_emb, dtype=np.float32).reshape(1, -1)
                norm = np.linalg.norm(q_vec)
                if norm > 0:
                    q_vec = q_vec / norm
                import faiss
                k_eff = min(k * 2, faiss_index.ntotal)  # over-fetch for keyword merge
                distances, indices = faiss_index.search(q_vec, k_eff)
                # Build a turn_id → turn lookup
                turn_map = {t.turn_id: t for t in turns_snapshot}
                for tid, dist in zip(indices[0], distances[0]):
                    if tid < 0:
                        continue
                    turn_id = faiss_ids[tid] if tid < len(faiss_ids) else -1
                    turn = turn_map.get(turn_id)
                    if turn is None:
                        continue
                    # Convert L2 distance to similarity [0,1]
                    score = max(0.0, 1.0 - float(dist) / 2.0)  # L2 of normalized vectors ∈ [0, 2]
                    if score >= MIN_SCORE:
                        results.append({
                            "turn_id": turn.turn_id,
                            "user_message": turn.user_message,
                            "assistant_answer": turn.assistant_answer,
                            "score": round(score, 4),
                            "timestamp": turn.timestamp,
                        })
            except Exception as e:  # noqa: BLE001
                self._log.debug("conversation_index vector search failed: %s", e)
                results = []

        # --- Keyword fallback / supplement ---
        # Always also run keyword matching to catch turns without embeddings
        # and to supplement vector results.
        kw_results = self._keyword_search(query, turns_snapshot, k)
        # Merge: dedup by turn_id, keep the higher score.
        existing_ids = {r["turn_id"] for r in results}
        for kw_r in kw_results:
            if kw_r["turn_id"] not in existing_ids:
                results.append(kw_r)
            else:
                # Keep the higher score.
                for r in results:
                    if r["turn_id"] == kw_r["turn_id"]:
                        r["score"] = max(r["score"], kw_r["score"])
                        break

        # --- Entity-mention boosting (deterministic anti-amnesia) ---
        # When the user mentions a [[wikilink]] entity, boost prior turns
        # that contain the exact entity string — even if vector similarity
        # is low. This fixes the "looks empty" vs "note is complete" case:
        # the prior turn where the file path was established would be
        # missed by vector search (semantically distant) but is caught by
        # a literal [[entity]] string match. Zero-cost (no embeddings).
        entity_results = self._entity_search(query, turns_snapshot)
        for ent_r in entity_results:
            if ent_r["turn_id"] not in existing_ids:
                results.append(ent_r)
            else:
                for r in results:
                    if r["turn_id"] == ent_r["turn_id"]:
                        r["score"] = max(r["score"], ent_r["score"])
                        break

        # Sort by score descending, take top k.
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:k]

    def _entity_search(self, query: str,
                       turns: list[_ConvTurn]) -> list[dict[str, Any]]:
        """Boost turns containing exact [[wikilink]] entities from the query.

        Extracts [[entity]] targets from the query (handling | aliases) and
        matches them literally against each turn's user_message +
        assistant_answer. Matched turns get a high score (0.85) so they
        surface above the vector threshold even when semantic similarity
        is low. Returns [] when the query has no wikilink entities.
        """
        # Extract [[entity]] targets, strip aliases after |.
        entities = re.findall(r'\[\[([^\]]+)\]\]', query)
        if not entities:
            return []
        # Normalize: lowercase, strip whitespace, split on | for alias.
        entities = [e.strip().lower().split("|")[0].strip() for e in entities]
        entities = [e for e in entities if e]
        if not entities:
            return []
        ENTITY_BOOST_SCORE = 0.85
        scored: list[dict[str, Any]] = []
        for turn in turns:
            text = (turn.user_message + " " + turn.assistant_answer).lower()
            if any(ent in text for ent in entities):
                scored.append({
                    "turn_id": turn.turn_id,
                    "user_message": turn.user_message,
                    "assistant_answer": turn.assistant_answer,
                    "score": ENTITY_BOOST_SCORE,
                    "timestamp": turn.timestamp,
                })
        return scored

    def _keyword_search(self, query: str, turns: list[_ConvTurn],
                        k: int) -> list[dict[str, Any]]:
        """Simple keyword-overlap matching as a fallback / supplement."""
        query_words = set(re.findall(r'\b\w{3,}\b', query.lower()))
        if not query_words:
            return []
        scored: list[dict[str, Any]] = []
        for turn in turns:
            text = (turn.user_message + " " + turn.assistant_answer).lower()
            turn_words = set(re.findall(r'\b\w{3,}\b', text))
            if not turn_words:
                continue
            overlap = len(query_words & turn_words)
            if overlap == 0:
                continue
            score = overlap / len(query_words)  # fraction of query words found
            if score >= MIN_SCORE:
                scored.append({
                    "turn_id": turn.turn_id,
                    "user_message": turn.user_message,
                    "assistant_answer": turn.assistant_answer,
                    "score": round(score, 4),
                    "timestamp": turn.timestamp,
                })
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]

    # ------------------------------------------------------------------ #
    # Maintenance
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """Wipe the index (called on /new)."""
        with self._lock:
            self._turns = []
            self._next_id = 1
            self._faiss_index = None
            self._faiss_ids = []

    @property
    def size(self) -> int:
        """Number of indexed turns."""
        with self._lock:
            return len(self._turns)


def build_conversation_context(results: list[dict[str, Any]],
                                max_chars: int = 3000) -> str:
    """Format conversation search results into a context string for injection.

    Returns a ``# PRIOR CONVERSATION`` block suitable for appending to the
    system prompt or the vault context message. Returns empty string when
    there are no results.
    """
    if not results:
        return ""
    lines = [
        "# PRIOR CONVERSATION (recent turns relevant to your current question — "
        "this is what you and the user discussed before. Use this to stay on "
        "track and reference prior context.)",
    ]
    total = 0
    for r in results:
        user = r.get("user_message", "")[:300]
        answer = r.get("assistant_answer", "")[:800]
        block = f"## Turn {r['turn_id']}\nUser: {user}\nYou said: {answer}"
        if total + len(block) > max_chars:
            break
        lines.append(block)
        total += len(block)
    return "\n\n".join(lines)