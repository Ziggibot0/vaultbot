"""Embedding drift — context-relevance feedback that nudges stored vectors
toward (or away from) queries they proved useful (or useless) for.

Why
---
Pure similarity retrieval ranks by "what is this note close to in embedding
space" — but a note can be semantically similar to a query yet *useless*
for answering it (a related-but-wrong chapter). The fix is relevance
feedback (Rocchio-style): when the LLM (or user) signals a retrieved note
was helpful, nudge its stored vector toward the query vector; when
unhelpful, nudge it away. Over time, notes drift toward the queries
they're good for, not just what they're similar to.

This is the "sm scooch the stored embeddings towards or away from the
given topic based on if the LLM says it's helpful or not" behavior.

Reset on rewrite
----------------
When a note's content changes (condense, refine, edit), the drift is
RESET — the old drift was earned against content that no longer exists,
so keeping it would mislead retrieval. The next embedding is the pure
content vector again, and drift rebuilds from zero as feedback arrives.

Storage
-------
A single JSON file `vaultbot_backend/embedding_drift.json` mapping
file_path -> {drift_vector: [floats], feedback_count: int, last_query: str}.
Drift vectors are small (768 floats) and the file is rewritten atomically.
Drift is applied at retrieval time by the fused retriever: the stored
embedding + drift is what gets compared to the query.

LLM cost
--------
Zero.  Feedback is a yes/no signal (helpful/unhelpful) supplied by the
chat loop after the answer is delivered — the LLM has already produced
its answer; this just records whether the context it used was good.  No
extra LLM call is needed to get the signal (the agent's own self-
assessment or a user thumbs-up/down drives it).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
from session_logger import SessionLoggerProtocol

logger = logging.getLogger(__name__)

# Drift magnitude per feedback step.  Small so a single thumbs-up doesn't
# swamp the content signal; cumulative over many signals.  Rocchio alpha.
DRIFT_STEP = 0.05
# Cap how far a vector can drift from its original content embedding, as a
# fraction of the original's norm.  Prevents runaway drift from endless
# feedback on a popular note.  0.3 = up to 30% of the original magnitude.
DRIFT_MAX_RATIO = 0.3
# Minimum feedback signals before drift is applied (avoids a single noisy
# thumbs-up moving a vector).  1 = apply immediately (simplest; the
# threshold can be raised if feedback is noisy).
MIN_FEEDBACK = 1


class EmbeddingDrift:
    """Per-note relevance-feedback drift, persisted to disk."""

    def __init__(
        self,
        state_path: str | Path,
        embedding_dim: int = 768,
        session_logger: SessionLoggerProtocol | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.embedding_dim = embedding_dim
        self.session_logger = session_logger
        # Load drift state — if the file is corrupt, log loudly and start
        # fresh rather than crashing the backend. This is a notified
        # fallback, not a silent one: the operator sees the log event.
        try:
            self.drift = self._load()
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            if self.session_logger:
                self.session_logger.log(
                    "drift_state_lost",
                    {
                        "error": str(e),
                        "category": "drift_lost",
                    },
                )
            logger.warning("drift state lost, starting fresh: %s", e)
            self.drift = {}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> dict[str, dict]:
        """Load drift state from disk.

        Returns empty dict when the file doesn't exist (first run — no
        drift is correct, not an error). Raises on corruption (JSON parse
        failure, IO error) so the caller knows drift state was lost.
        """
        if not self.state_path.exists():
            return {}
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(
                f"drift state file is not a dict ({type(data).__name__}): "
                f"{self.state_path}"
            )
        return data

    def _save(self) -> None:
        """Persist drift state to disk. Raises on failure.

        The caller (record_feedback) has a try/except that logs the error
        — so a save failure is visible, not silent.
        """
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.drift, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def record_feedback(
        self,
        file_path: str,
        query_embedding: Any,
        helpful: bool,
        magnitude: float = DRIFT_STEP,
    ) -> None:
        """Record one feedback signal for a note.

        `helpful=True` nudges the note's drift toward the query embedding
        (so future similar queries rank it higher).  `helpful=False`
        nudges it away.  The drift accumulates but is capped at
        DRIFT_MAX_RATIO of the original embedding's norm (which we
        approximate from the query embedding's norm — they're the same
        model/dim, so comparable).
        """
        try:
            q = np.asarray(query_embedding, dtype=np.float32)
            if q.ndim != 1 or q.size == 0:
                return
            key = str(Path(file_path).resolve())
            entry = self.drift.get(
                key,
                {
                    "drift_vector": None,
                    "feedback_count": 0,
                    "helpful_count": 0,
                    "unhelpful_count": 0,
                    "last_query_time": 0,
                },
            )
            # lazy-init the drift vector
            if entry.get("drift_vector") is None:
                entry["drift_vector"] = np.zeros_like(q).tolist()
            d = np.asarray(entry["drift_vector"], dtype=np.float32)
            # direction: toward query if helpful, away if not
            direction = 1.0 if helpful else -1.0
            step = (q / (np.linalg.norm(q) + 1e-9)) * magnitude * direction
            new_d = d + step
            # cap drift magnitude
            max_norm = np.linalg.norm(q) * DRIFT_MAX_RATIO
            dn = np.linalg.norm(new_d)
            if dn > max_norm and dn > 0:
                new_d = new_d * (max_norm / dn)
            entry["drift_vector"] = new_d.tolist()
            entry["feedback_count"] = entry.get("feedback_count", 0) + 1
            if helpful:
                entry["helpful_count"] = entry.get("helpful_count", 0) + 1
            else:
                entry["unhelpful_count"] = entry.get("unhelpful_count", 0) + 1
            entry["last_query_time"] = time.time()
            self.drift[key] = entry
            self._save()
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            # Log loudly — feedback was lost. The caller (chat_handler) also
            # has a try/except that logs this. No nested "swallowed" logging.
            if self.session_logger:
                self.session_logger.log(
                    "drift_record_failed", {"error": str(e), "file": file_path}
                )

    def apply_drift(self, file_path: str, content_embedding: Any) -> np.ndarray:
        """Return the drifted embedding for a note: content + drift vector.

        If no drift is recorded (or the note was reset), returns the content
        embedding unchanged.  Always returns a float32 ndarray.
        """
        base = np.asarray(content_embedding, dtype=np.float32)
        try:
            key = str(Path(file_path).resolve())
            entry = self.drift.get(key)
            if not entry or entry.get("drift_vector") is None:
                return base
            d = np.asarray(entry["drift_vector"], dtype=np.float32)
            if d.shape != base.shape:
                # dimension mismatch (model changed?) — reset this entry
                self.reset(file_path)
                return base
            if entry.get("feedback_count", 0) < MIN_FEEDBACK:
                return base
            return base + d
        except (ValueError, KeyError, TypeError):
            # Drift data is malformed — return the base embedding. This is
            # a bonus layer, not a critical channel. Narrow catch so real
            # bugs (AttributeError, etc.) propagate.
            return base

    def reset(self, file_path: str) -> None:
        """Clear drift for a note.  Called when its content changes (condense,
        refine, edit) — the old drift was earned against content that no
        longer exists, so it must not mislead retrieval."""
        key = str(Path(file_path).resolve())
        if key in self.drift:
            del self.drift[key]
            self._save()

    def status(self) -> dict[str, Any]:
        """Summary for /health or diagnostics."""
        total = len(self.drift)
        with_feedback = sum(
            1 for e in self.drift.values() if e.get("feedback_count", 0) > 0
        )
        return {
            "notes_with_drift": total,
            "notes_with_feedback": with_feedback,
            "state_path": str(self.state_path),
        }
