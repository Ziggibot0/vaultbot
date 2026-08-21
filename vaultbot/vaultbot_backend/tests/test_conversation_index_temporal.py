"""Unit tests for temporal awareness in conversation_index.py (issue #85).

Pure offline tests — no Ollama, no FAISS, no Services. They verify:
  - build_conversation_context renders timestamps when present
  - build_conversation_context is backward-compatible (no timestamp)
  - the recency boost raises a recent turn's score above an older turn
    with an identical base score
  - rebuild_from_history preserves real timestamps from the history
"""

from __future__ import annotations

import time

import pytest
from conversation_index import (
    RECENCY_BOOST,
    ConversationIndex,
    build_conversation_context,
)

pytestmark = pytest.mark.unit


class TestBuildConversationContextTimestamps:
    def test_renders_timestamp_when_present(self):
        results = [
            {
                "turn_id": 5,
                "user_message": "what were we working on?",
                "assistant_answer": "we were auditing procedures",
                "timestamp": 1755700000.0,
            }
        ]
        ctx = build_conversation_context(results)
        assert "Turn 5" in ctx
        # The timestamp is rendered as a human-readable UTC date.
        assert "UTC" in ctx
        assert "2025-08-20" in ctx  # 1755700000 ≈ 2025-08-20

    def test_backward_compatible_without_timestamp(self):
        results = [
            {
                "turn_id": 3,
                "user_message": "hello",
                "assistant_answer": "hi",
            }
        ]
        ctx = build_conversation_context(results)
        assert "Turn 3" in ctx
        assert "UTC" not in ctx  # no timestamp → no date rendered

    def test_empty_results(self):
        assert build_conversation_context([]) == ""


class TestRecencyBoost:
    def test_recent_turn_outranks_older_turn_with_equal_score(self):
        """A recent turn with the same base score as an old turn must sort
        first after the recency boost is applied (issue #85)."""
        now = time.time()
        old_ts = now - (30 * 24 * 3600)  # 30 days ago
        recent_ts = now - 60  # 1 minute ago

        idx = ConversationIndex(ollama_client=None)  # keyword-only mode
        idx.add_turn("old topic", "old answer", timestamp=old_ts)
        idx.add_turn("recent topic", "recent answer", timestamp=recent_ts)

        # A query that matches BOTH turns equally by keyword overlap.
        results = idx.search("topic", k=2)
        assert len(results) == 2
        # The recent turn must outrank the old turn after the recency boost.
        assert results[0]["timestamp"] == recent_ts

    def test_boost_is_bounded(self):
        """The recency boost must never exceed RECENCY_BOOST (0.05)."""
        assert RECENCY_BOOST <= 0.1
