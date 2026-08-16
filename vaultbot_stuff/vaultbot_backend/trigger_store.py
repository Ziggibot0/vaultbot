"""Trigger/inhibitor store — per-note phrase embeddings for retrieval gating.

THE PROBLEM THIS SOLVES
-----------------------
``when_to_use`` is static prose: written once (by the LLM or by hand), it never
improves from real outcomes.  Retrieval surfaces a procedure whenever the
query semantically matches its description, even when the user's *situation*
is one where the procedure has repeatedly failed or been unhelpful.  There is
no "do not show me this here" signal.

The operator's insight (the one this module implements): split the single
``when_to_use`` field into two feedback-tuned fields — ``trigger`` and
``inhibitor`` — each a list of natural-language phrases.  A phrase is *earned*:
the Dream Pass writes it after observing that the note was useful (trigger) or
harmful/ignored (inhibitor) for a given kind of query, confirmed by the user's
sentiment in their next message.

At retrieval time, each note's trigger and inhibitor phrases are embedded.  If
the current query matches an *inhibitor* phrase more strongly than any
*trigger* phrase (by a margin), the note is **dropped** — it is not shown to the
model at all.  This is a gate, not a downrank: the model sees less and less
noise over time as inhibitors accumulate.

WHAT THIS MODULE DOES
--------------------
1. ``update_note(file_path, trigger_phrases, inhibitor_phrases)`` — embed each
   phrase (via an injected embedding getter) and store the vectors + phrase
   texts, persisted to ``trigger_embeddings.json``.
2. ``check(query_emb, file_path, margin) -> (should_drop, trigger_score,
   inhibitor_score)`` — compute max cosine similarity of the query embedding
   against each trigger phrase emb and each inhibitor phrase emb.  Return
   ``should_drop = inhibitor_score > trigger_score + margin``.
3. ``remove_note(file_path)`` — delete the entry (called on file deletion or
   when a note loses its trigger/inhibitor fields).

Pure stdlib + numpy.  No FAISS, no LLM calls — embeddings are passed in by the
indexer (``vault_indexer._get_embedding``).  Persistence mirrors
``embedding_drift.py``: atomic temp-file rename, JSON on disk.

The store is a *bonus layer*: when no entry exists for a note (the entire
non-procedure vault today, and all notes before migration), ``check`` returns
``(False, 0, 0)`` and the gate is a no-op.  This makes the feature safe to ship
before any note has trigger/inhibitor fields.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

# Minimum phrase length — skip empty / whitespace-only phrases so a malformed
# frontmatter list doesn't embed a zero-signal vector that ties with every
# query (cosine of a near-zero norm vector is undefined / noisy).
_MIN_PHRASE_LEN = 3


class TriggerStore:
    """Per-note trigger/inhibitor phrase embeddings, persisted to disk.

    The embedding getter is injected (``embedding_getter: (text) -> ndarray``)
    so this module stays decoupled from ``OllamaClient`` / ``VaultIndexer`` —
    tests pass a fixed-vector fake; production wires
    ``vault_indexer._get_embedding``.
    """

    def __init__(
        self,
        state_path: str | Path,
        embedding_getter: Callable[[str], np.ndarray] | None = None,
        session_logger: Any = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.embedding_getter = embedding_getter
        self.session_logger = session_logger
        try:
            self.store = self._load()
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            if self.session_logger:
                self.session_logger.log(
                    "trigger_store_lost",
                    {"error": str(e), "category": "trigger_store_lost"},
                )
            logger.warning("trigger store state lost, starting fresh: %s", e)
            self.store = {}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> dict[str, dict]:
        """Load the store from disk.

        Returns ``{}`` when the file doesn't exist (first run — no triggers
        is correct, not an error).  Raises on corruption so the caller knows
        the state was lost (mirrors ``embedding_drift._load``).
        """
        if not self.state_path.exists():
            return {}
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(
                f"trigger store file is not a dict ({type(data).__name__}): "
                f"{self.state_path}"
            )
        return data

    def _save(self) -> None:
        """Persist the store to disk atomically (temp-file rename)."""
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.store, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def update_note(
        self,
        file_path: str,
        trigger_phrases: list[str],
        inhibitor_phrases: list[str],
    ) -> None:
        """Embed and store trigger/inhibitor phrases for a note.

        Called by the indexer when a file is added/updated.  If both phrase
        lists are empty (the note has neither field), the entry is removed —
        a note that lost its trigger/inhibitor fields must not keep a stale
        gate.
        """
        triggers = [p.strip() for p in trigger_phrases if p and len(p.strip()) >= _MIN_PHRASE_LEN]
        inhibitors = [p.strip() for p in inhibitor_phrases if p and len(p.strip()) >= _MIN_PHRASE_LEN]
        if not triggers and not inhibitors:
            self.remove_note(file_path)
            return
        if self.embedding_getter is None:
            # No embedding source wired — keep the phrase texts so a later
            # wiring can backfill embeddings, but the gate is inert.
            key = str(Path(file_path).resolve())
            self.store[key] = {
                "trigger_phrases": triggers,
                "inhibitor_phrases": inhibitors,
                "trigger_embs": [],
                "inhibitor_embs": [],
            }
            self._save()
            return
        try:
            trigger_embs = [self._embed(p) for p in triggers]
            inhibitor_embs = [self._embed(p) for p in inhibitors]
            key = str(Path(file_path).resolve())
            self.store[key] = {
                "trigger_phrases": triggers,
                "inhibitor_phrases": inhibitors,
                "trigger_embs": [e.tolist() for e in trigger_embs if e is not None],
                "inhibitor_embs": [e.tolist() for e in inhibitor_embs if e is not None],
            }
            self._save()
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            if self.session_logger:
                self.session_logger.log(
                    "trigger_store_update_failed",
                    {"error": str(e), "file": file_path},
                )
            logger.warning("trigger store update failed for %s: %s", file_path, e)

    def _embed(self, phrase: str) -> np.ndarray | None:
        """Embed a single phrase via the injected getter, normalized."""
        if self.embedding_getter is None:
            return None
        try:
            emb = np.asarray(self.embedding_getter(phrase), dtype=np.float32)
            if emb.ndim != 1 or emb.size == 0:
                return None
            norm = np.linalg.norm(emb)
            if norm < 1e-9:
                return None
            return emb / norm
        except Exception:  # noqa: BLE001 — embedding a phrase must not crash the indexer
            return None

    def check(
        self,
        query_emb: np.ndarray,
        file_path: str,
        margin: float = 0.05,
    ) -> tuple[bool, float, float]:
        """Gate check: should this note be dropped from retrieval results?

        Returns ``(should_drop, trigger_score, inhibitor_score)`` where the
        scores are the max cosine similarity of ``query_emb`` against any
        trigger / inhibitor phrase embedding for the note.

        ``should_drop = inhibitor_score > trigger_score + margin``.

        A note with no entry (no trigger/inhibitor fields) returns
        ``(False, 0.0, 0.0)`` — passthrough.  This is what makes the gate a
        no-op until notes acquire the fields.
        """
        key = str(Path(file_path).resolve())
        entry = self.store.get(key)
        if not entry:
            return (False, 0.0, 0.0)
        # If embeddings were never computed (getter was None at update time),
        # the gate is inert — passthrough so a misconfigured store doesn't
        # drop everything.
        trigger_embs = entry.get("trigger_embs") or []
        inhibitor_embs = entry.get("inhibitor_embs") or []
        if not trigger_embs and not inhibitor_embs:
            return (False, 0.0, 0.0)

        q = np.asarray(query_emb, dtype=np.float32)
        qn = np.linalg.norm(q)
        if qn < 1e-9:
            return (False, 0.0, 0.0)
        qn = q / qn

        trigger_score = self._max_sim(qn, trigger_embs)
        inhibitor_score = self._max_sim(qn, inhibitor_embs)
        should_drop = inhibitor_score > trigger_score + margin
        return (should_drop, trigger_score, inhibitor_score)

    @staticmethod
    def _max_sim(q_norm: np.ndarray, embs: list[list[float]]) -> float:
        """Max cosine similarity of a unit query vs a list of stored vectors.

        Stored vectors were normalized at update time; cosine = dot product.
        Returns ``0.0`` for an empty list (no phrases → no signal → no drop).
        """
        if not embs:
            return 0.0
        best = 0.0
        for raw in embs:
            try:
                v = np.asarray(raw, dtype=np.float32)
                vn = np.linalg.norm(v)
                if vn < 1e-9:
                    continue
                sim = float(np.dot(q_norm, v / vn))
                if sim > best:
                    best = sim
            except (ValueError, TypeError):
                continue
        return best

    def remove_note(self, file_path: str) -> None:
        """Delete the entry for a note.

        Called on file deletion and when a note loses its trigger/inhibitor
        fields (both lists empty in ``update_note``).
        """
        key = str(Path(file_path).resolve())
        if key in self.store:
            del self.store[key]
            self._save()

    def status(self) -> dict[str, Any]:
        """Summary for /health or diagnostics."""
        return {
            "notes_with_triggers": len(self.store),
            "state_path": str(self.state_path),
        }