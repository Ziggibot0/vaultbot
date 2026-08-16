"""Tests for the trigger/inhibitor gate in FusedRetriever.retrieve().

Verifies that the gate drops notes whose inhibitor phrases match the query
more strongly than their trigger phrases, and passes through notes with no
trigger/inhibitor entry.

Pure-Python stubs stand in for VaultGraph, VaultIndexer, and TriggerStore —
no real vault, no FAISS, no Ollama.  Follows the pattern in
test_fused_retrieval.py.
"""

from __future__ import annotations

import sys
import types

import numpy as np

# Install the no-op faiss stub so the leaf import of fused_retrieval
# (which imports vault_indexer → faiss) succeeds without the broken
# native extension.  Same pattern as test_fused_retrieval.py.
if "faiss" not in sys.modules:
    _faiss_stub = types.ModuleType("faiss")
    _faiss_stub.IndexFlatL2 = type("IndexFlatL2", (), {})
    _faiss_stub.read_index = lambda *a, **k: None
    _faiss_stub.write_index = lambda *a, **k: None
    sys.modules["faiss"] = _faiss_stub

from fused_retrieval import FusedRetriever


# ── Stubs ────────────────────────────────────────────────────────────────


class _NoEdgeGraph:
    """Graph stub with no edges — isolates the vector channel."""

    nodes = {}
    backlinks = {}

    def neighbors(self, name, direction="both"):
        return []


class _StubIndexer:
    """Indexer stub returning fixed vector hits + a fixed query embedding."""

    def __init__(self, hits, query_emb):
        self._hits = hits
        self._query_emb = query_emb

    def search(self, query, k=10):
        return [dict(h) for h in self._hits]

    def _get_embedding(self, text):
        return self._query_emb

    def reconstruct_embedding(self, file_path):
        return None


class _StubTriggerStore:
    """TriggerStore stub with a configurable check() that returns preset results.

    ``check_results`` maps file_path -> (should_drop, trigger_score, inhibitor_score).
    Any file_path not in the map returns (False, 0, 0) (passthrough).
    """

    def __init__(self, check_results: dict | None = None):
        self.check_results = check_results or {}
        self.check_calls: list[str] = []

    def check(self, query_emb, file_path, margin=0.05):
        self.check_calls.append(file_path)
        return self.check_results.get(file_path, (False, 0.0, 0.0))


# ── Tests ────────────────────────────────────────────────────────────────


def test_gate_drops_inhibitor_match(tmp_path):
    """A note whose inhibitor matches the query is dropped from results."""
    note_a = str(tmp_path / "a.md")
    note_b = str(tmp_path / "b.md")
    query_emb = np.array([1.0, 0.0], dtype=np.float32)
    hits = [
        {"file_path": note_a, "score": 0.9},
        {"file_path": note_b, "score": 0.8},
    ]
    indexer = _StubIndexer(hits, query_emb)
    # note_a: inhibitor dominates → drop.  note_b: no entry → passthrough.
    store = _StubTriggerStore({note_a: (True, 0.2, 0.9)})
    fused = FusedRetriever(
        vault_graph=_NoEdgeGraph(),
        vault_indexer=indexer,
        trigger_store=store,
    )
    out = fused.retrieve("query", k=5)
    paths = [r["file_path"] for r in out["results"]]
    assert note_a not in paths, "inhibitor-matching note should be dropped"
    assert note_b in paths, "passthrough note should be kept"


def test_gate_passes_through_no_store(tmp_path):
    """When trigger_store is None, no gating occurs (all notes kept)."""
    note_a = str(tmp_path / "a.md")
    query_emb = np.array([1.0, 0.0], dtype=np.float32)
    hits = [{"file_path": note_a, "score": 0.9}]
    indexer = _StubIndexer(hits, query_emb)
    fused = FusedRetriever(
        vault_graph=_NoEdgeGraph(),
        vault_indexer=indexer,
        trigger_store=None,  # no store → gate is a no-op
    )
    out = fused.retrieve("query", k=5)
    assert note_a in [r["file_path"] for r in out["results"]]


def test_gate_passes_through_no_entry(tmp_path):
    """A note with no trigger/inhibitor entry passes through (not dropped)."""
    note_a = str(tmp_path / "a.md")
    query_emb = np.array([1.0, 0.0], dtype=np.float32)
    hits = [{"file_path": note_a, "score": 0.9}]
    indexer = _StubIndexer(hits, query_emb)
    store = _StubTriggerStore()  # no entries → all passthrough
    fused = FusedRetriever(
        vault_graph=_NoEdgeGraph(),
        vault_indexer=indexer,
        trigger_store=store,
    )
    out = fused.retrieve("query", k=5)
    assert note_a in [r["file_path"] for r in out["results"]]


def test_gate_keeps_trigger_match(tmp_path):
    """A note whose trigger matches the query (trigger > inhibitor) is kept."""
    note_a = str(tmp_path / "a.md")
    query_emb = np.array([1.0, 0.0], dtype=np.float32)
    hits = [{"file_path": note_a, "score": 0.9}]
    indexer = _StubIndexer(hits, query_emb)
    # trigger_score > inhibitor_score → not dropped.
    store = _StubTriggerStore({note_a: (False, 0.9, 0.2)})
    fused = FusedRetriever(
        vault_graph=_NoEdgeGraph(),
        vault_indexer=indexer,
        trigger_store=store,
    )
    out = fused.retrieve("query", k=5)
    assert note_a in [r["file_path"] for r in out["results"]]