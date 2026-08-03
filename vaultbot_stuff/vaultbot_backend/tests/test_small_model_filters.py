"""Tests for small_model_filters.py — the pre-filter functions that sit
between retrieval and the big model.

All tests use stub doubles — no real Ollama, FAISS, or procedure execution.
Each test follows Arrange → Act → Assert; cleanup is automatic via tmp_path.

These tests verify the fail-safe contract: a broken small model degrades to
exactly today's behavior. The big model never sees worse data than it would
without the filters.
"""
import sys
import types

# Shim faiss so fused_retrieval imports without the NumPy 2.x ABI break
# (same pattern as test_fused_retrieval.py).
if "faiss" not in sys.modules:
    _faiss_stub = types.ModuleType("faiss")
    _faiss_stub.IndexFlatL2 = type("IndexFlatL2", (), {})
    _faiss_stub.read_index = lambda *a, **k: None
    _faiss_stub.write_index = lambda *a, **k: None
    sys.modules["faiss"] = _faiss_stub

import json

import pytest

from small_model_filters import (
    compress_window, dedup_results, expand_query, filter_context,
    _content_words, _parse_json_array, _split_context_sections,
)


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

class _FakeClient:
    """LLM client stub that returns canned text for chat()."""

    def __init__(self, response: str = "", raises: bool = False):
        self._response = response
        self._raises = raises
        self.call_count = 0

    def chat(self, messages, temperature=0.2, stream=False, **kwargs):
        self.call_count += 1
        if self._raises:
            raise RuntimeError("fake client error")
        return {"message": {"content": self._response}}


class _FakeSessionLogger:
    """SessionLogger stub that collects log events."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, data: dict | None = None):
        self.events.append((event, data or {}))


# ---------------------------------------------------------------------------
# Phase 1: rerank_results tests
# ---------------------------------------------------------------------------

# rerank_results delegates to execute_procedure, which is async and lives in
# chat_handler.py. We test it by monkeypatching execute_agent_tool.

def _make_results(n: int) -> list[dict]:
    """Generate n fake FUSED retrieval results."""
    return [
        {"file_path": f"note_{i}.md", "name": f"Note-{i}",
         "score": 1.0 - i * 0.1, "channels": {"vector"}, "snippet": f"content {i}"}
        for i in range(n)
    ]


def test_rerank_reorders_by_relevance(monkeypatch):
    """High-relevance note should be promoted to front."""
    from small_model_filters import rerank_results

    results = _make_results(10)
    # Make note_5 the most relevant.
    fake_proc_output = json.dumps([
        {"file_path": "note_5.md", "relevance": "high", "reason": "direct match"},
        {"file_path": "note_0.md", "relevance": "medium", "reason": "related"},
    ] + [{"file_path": f"note_{i}.md", "relevance": "low", "reason": "no"}
         for i in range(1, 10) if i != 5])

    async def fake_execute(svc, tool_name, args, logger, ws, **kw):
        return {"overall_passed": True, "final_output": fake_proc_output}

    monkeypatch.setattr("chat_handler.execute_agent_tool", fake_execute)

    import asyncio
    out = asyncio.run(rerank_results(
        svc=None, query="test", results=results, k=5,
        session_logger=_FakeSessionLogger()))
    # note_5 should be first (high relevance).
    assert out[0]["file_path"] == "note_5.md"
    assert len(out) == 5


def test_rerank_noop_when_few_results():
    """When results <= k, reranking is skipped (no procedure call)."""
    from small_model_filters import rerank_results
    import asyncio

    results = _make_results(3)
    # rerank_results is async — run it.
    out = asyncio.run(rerank_results(
        svc=None, query="test", results=results, k=5,
        session_logger=_FakeSessionLogger()))
    # Should return the original 3 results unchanged.
    assert len(out) == 3
    assert out == results


def test_rerank_fallback_on_procedure_failure(monkeypatch):
    """When procedure fails, original order is preserved."""
    from small_model_filters import rerank_results

    results = _make_results(10)

    async def fake_execute(svc, tool_name, args, logger, ws, **kw):
        return {"overall_passed": False, "final_output": ""}

    monkeypatch.setattr("chat_handler.execute_agent_tool", fake_execute)

    import asyncio
    out = asyncio.run(rerank_results(
        svc=None, query="test", results=results, k=5,
        session_logger=_FakeSessionLogger()))
    assert len(out) == 5
    # Original order preserved (note_0 first, note_1 second, ...).
    assert out[0]["file_path"] == "note_0.md"


def test_rerank_fallback_on_garbage_json(monkeypatch):
    """When procedure returns non-JSON, original order is preserved."""
    from small_model_filters import rerank_results

    results = _make_results(10)

    async def fake_execute(svc, tool_name, args, logger, ws, **kw):
        return {"overall_passed": True, "final_output": "this is not json"}

    monkeypatch.setattr("chat_handler.execute_agent_tool", fake_execute)

    import asyncio
    out = asyncio.run(rerank_results(
        svc=None, query="test", results=results, k=5,
        session_logger=_FakeSessionLogger()))
    assert len(out) == 5
    assert out[0]["file_path"] == "note_0.md"


# ---------------------------------------------------------------------------
# Phase 2: expand_query tests
# ---------------------------------------------------------------------------

def test_expand_returns_original_plus_alternatives():
    """Valid expansion produces original + at least 1 alternative."""
    client = _FakeClient("small models for vault\nsmall language model efficiency")
    out = expand_query(client, "tell me about small models for vault",
                       session_logger=_FakeSessionLogger())
    assert len(out) >= 2
    assert out[0] == "tell me about small models for vault"
    # At least one expanded query should mention "small".
    assert any("small" in q.lower() for q in out[1:])


def test_expand_fallback_on_failure():
    """When client raises, only the original message is returned."""
    client = _FakeClient(raises=True)
    out = expand_query(client, "what is embedding drift",
                       session_logger=_FakeSessionLogger())
    assert out == ["what is embedding drift"]


def test_expand_drops_unrelated_query():
    """Expanded queries with no word overlap are dropped."""
    client = _FakeClient("quantum physics formulas\nmacaroni recipe ingredients")
    out = expand_query(client, "tell me about embedding drift",
                       session_logger=_FakeSessionLogger())
    # Both expanded queries have no overlap with "embedding drift" — only
    # the original should survive.
    assert out == ["tell me about embedding drift"]


def test_expand_none_client_returns_original():
    """When client is None, only the original message is returned."""
    out = expand_query(None, "search for procedures",
                       session_logger=_FakeSessionLogger())
    assert out == ["search for procedures"]


# ---------------------------------------------------------------------------
# dedup_results tests
# ---------------------------------------------------------------------------

def test_dedup_merges_by_file_path():
    """Two results for the same path merge into one with max score."""
    results = [
        {"file_path": "note.md", "name": "Note", "score": 0.5, "channels": {"vector"}},
        {"file_path": "note.md", "name": "Note", "score": 0.8, "channels": {"backlink"}},
    ]
    out = dedup_results(results)
    assert len(out) == 1
    assert out[0]["score"] == 0.8


def test_dedup_keeps_different_paths():
    """Different file_paths stay separate."""
    results = [
        {"file_path": "a.md", "name": "A", "score": 0.5, "channels": set()},
        {"file_path": "b.md", "name": "B", "score": 0.8, "channels": set()},
    ]
    out = dedup_results(results)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Phase 3: digest condition (no new function — just condition change in
# chat_handler.py; tested via integration, not unit test)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 4: filter_context tests
# ---------------------------------------------------------------------------

def _make_context(n_sections: int) -> str:
    """Build a context string with n L1 card sections."""
    parts = ["# Vault Context\n"]
    for i in range(n_sections):
        parts.append(f"### [[Section-{i}]]\nThis is section {i}. " + "x" * 500)
    return "\n\n".join(parts)


def test_filter_noop_on_short_context():
    """Context under 3000 chars is not filtered."""
    short_ctx = "short context " * 10
    import asyncio
    out = asyncio.run(filter_context(
        svc=None, query="test", context=short_ctx,
        session_logger=_FakeSessionLogger()))
    assert out == short_ctx


def test_filter_fallback_on_procedure_failure(monkeypatch):
    """When procedure fails, original context is returned."""
    ctx = _make_context(6)

    async def fake_execute(svc, tool_name, args, logger, ws, **kw):
        return {"overall_passed": False, "final_output": ""}

    monkeypatch.setattr("chat_handler.execute_agent_tool", fake_execute)
    import asyncio
    out = asyncio.run(filter_context(
        svc=None, query="test", context=ctx,
        session_logger=_FakeSessionLogger()))
    assert out == ctx


def test_split_context_sections_basic():
    """_split_context_sections finds headers and splits correctly."""
    ctx = "# Header\n\n### [[Card-A]]\nBody A\n\n### [[Card-B]]\nBody B"
    sections = _split_context_sections(ctx)
    assert len(sections) == 3  # preamble + 2 cards
    assert "Card-A" in sections[1]["title"]
    assert "Card-B" in sections[2]["title"]


def test_split_context_sections_no_headers():
    """Context with no headers returns a single section."""
    ctx = "just plain text, no headers"
    sections = _split_context_sections(ctx)
    assert len(sections) == 1


# ---------------------------------------------------------------------------
# Phase 5: compress_window tests
# ---------------------------------------------------------------------------

def test_compress_trivial():
    """Fewer than 4 messages → no compression (returns None)."""
    msgs = [{"role": "user", "content": "hello"}]
    out = compress_window(msgs, session_logger=_FakeSessionLogger())
    assert out is None


def test_compress_fallback_on_failure(monkeypatch):
    """When the small model is unavailable, returns None (drop messages)."""
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "do something"},
        {"role": "assistant", "content": "done"},
    ]

    def fake_get_small_client(logger=None):
        return None

    monkeypatch.setattr("llm_client.get_small_client", fake_get_small_client)
    out = compress_window(msgs, session_logger=_FakeSessionLogger())
    assert out is None


def test_compress_returns_summary(monkeypatch):
    """Valid small-model output returns a summary string."""
    msgs = [
        {"role": "user", "content": "search for notes about biology"},
        {"role": "assistant", "content": "found 3 notes"},
        {"role": "user", "content": "summarize them"},
        {"role": "assistant", "content": "here is the summary of biology notes"},
    ]

    fake_client = _FakeClient("User asked about biology. Found 3 notes and summarized them.")
    monkeypatch.setattr("llm_client.get_small_client", lambda logger=None: fake_client)
    out = compress_window(msgs, session_logger=_FakeSessionLogger())
    assert out is not None
    assert "biology" in out.lower()


def test_compress_caps_at_600_chars(monkeypatch):
    """Summary is truncated to _MAX_SUMMARY_CHARS."""
    msgs = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
    long_summary = "x" * 2000
    fake_client = _FakeClient(long_summary)
    monkeypatch.setattr("llm_client.get_small_client", lambda logger=None: fake_client)
    out = compress_window(msgs, session_logger=_FakeSessionLogger())
    assert out is not None
    assert len(out) <= 600


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

def test_parse_json_array_valid():
    """Valid JSON array is parsed."""
    text = 'some preamble [{"a": 1}, {"b": 2}] trailing'
    out = _parse_json_array(text)
    assert out == [{"a": 1}, {"b": 2}]


def test_parse_json_array_invalid():
    """Non-JSON text returns None."""
    assert _parse_json_array("not json at all") is None
    assert _parse_json_array("") is None


def test_content_words_filters_stop_words():
    """Stop words are excluded from the content word set."""
    words = _content_words("the quick brown fox jumps over the lazy dog")
    assert "the" not in words
    assert "quick" in words
    assert "fox" in words