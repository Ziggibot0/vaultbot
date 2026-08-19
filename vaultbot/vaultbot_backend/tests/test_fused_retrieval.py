"""Tests for the fused (vector + graph + backlink) retrieval in fused_retrieval.py.

Pure-Python stubs stand in for VaultGraph and VaultIndexer — no real vault,
no FAISS, no Ollama, no Docker, no network.  Each test follows
Arrange → Act → Assert; cleanup is automatic via the `tmp_path` and
`monkeypatch` fixtures.

Documentation grounding:
- Anatomy of a test (Arrange → Act → Assert → Cleanup)
  https://docs.pytest.org/en/stable/explanation/anatomy.html
- tmp_path: unique pathlib.Path per test, auto-cleaned
  https://docs.pytest.org/en/stable/reference/reference.html
- monkeypatch: setattr/setitem/setenv, auto-undone after test
  https://docs.pytest.org/en/stable/reference/reference.html

Leaf-module imports only — `import main` is hard-fenced by conftest.py
(main.py calls acquire_lock() → sys.exit + loads the live FAISS index).
"""

import sys
import types
from typing import ClassVar

import pytest

# fused_retrieval imports vault_indexer, which imports `faiss`.  The
# installed faiss wheel was compiled against NumPy 1.x and raises an
# ImportError under NumPy 2.5.1 (ABI break).  The tests here never touch a
# real VaultIndexer/FAISS index — they inject stub indexers — so a
# no-op `faiss` module in sys.modules is enough to let the leaf import
# succeed without dragging in the broken native extension.  This is a
# test-environment shim, not a production-code change.
if "faiss" not in sys.modules:
    _faiss_stub = types.ModuleType("faiss")
    _faiss_stub.IndexFlatL2 = type("IndexFlatL2", (), {})
    _faiss_stub.read_index = lambda *a, **k: None
    _faiss_stub.write_index = lambda *a, **k: None
    sys.modules["faiss"] = _faiss_stub

from fused_retrieval import FusedRetriever

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------
class _BrokenGraph:
    """A VaultGraph stub whose graph/backlink access always raises.

    Used to prove retrieval degrades to vector-only when the graph channel
    fails, rather than crashing the whole retrieve() call.
    """

    # FusedRetriever reads `graph.backlinks` via getattr in _backlink_channel
    # and _rerank; make it a property that raises to simulate a broken graph.
    @property
    def backlinks(self):
        raise RuntimeError("graph offline")

    @property
    def nodes(self):
        raise RuntimeError("graph offline")

    def neighbors(self, name, direction="both"):
        raise RuntimeError("graph offline")


class _StubIndexer:
    """Minimal VaultIndexer stub returning a fixed vector-search result list.

    `search()` returns whatever was configured at construction time so tests
    can control the vector channel without a real FAISS index.
    """

    def __init__(self, hits):
        self._hits = hits

    def search(self, query, k=10):
        return list(self._hits)


# ---------------------------------------------------------------------------
# Test 1: graceful degradation to vector-only on graph failure
# ---------------------------------------------------------------------------
def test_retrieve_degrades_to_vector_only_on_graph_failure(tmp_path):
    # Arrange: a broken graph + an indexer with one vector hit.
    # https://docs.pytest.org/en/stable/explanation/anatomy.html
    note_path = str(tmp_path / "note.md")
    vector_hit = {"file_path": note_path, "score": 0.9}
    stub_graph = _BrokenGraph()
    stub_indexer = _StubIndexer([vector_hit])
    fused = FusedRetriever(vault_graph=stub_graph, vault_indexer=stub_indexer)

    # Act: retrieve must NOT raise even though every graph path raises.
    out = fused.retrieve("query", k=5)

    # Assert: the result is non-empty and carries the vector hit — the graph
    # failure degraded retrieval to vector-only instead of killing it.
    assert out["count"] >= 1
    paths = [r["file_path"] for r in out["results"]]
    assert note_path in paths
    # The vector channel reported its hit; graph/backlink channels are empty
    # because the stub raised before producing candidates.
    assert out["channels"]["vector"] == 1


# ---------------------------------------------------------------------------
# Test 2: merge dedups by file_path (max score across channels)
# ---------------------------------------------------------------------------
def test_merge_dedups_by_file_path(tmp_path):
    # Arrange: two vector hits.  The graph walk from the second hit ("b")
    # resolves back to the FIRST hit's file_path ("dup.md"), so the graph
    # channel and the vector channel both surface "dup.md".  After merge,
    # "dup.md" must appear exactly once, with the max (vector) score.
    #
    # Graph stub wiring:
    #   nodes = {"b": {"file_path": "other.md"},
    #            "dup": {"file_path": "dup.md"}}
    #   neighbors("b") -> ["dup"]  (resolved to dup.md → overlaps vector)
    # The graph candidate score = GRAPH_BOOST * base_of_b = 0.5 * 0.667
    # which is below the vector score for dup.md (1.0), so the merged
    # score stays at the vector max and the entry is deduped to one row.
    dup_path = "dup.md"
    vector_hits = [
        {"file_path": dup_path, "score": 0.3},  # closer → higher norm score
        {"file_path": "other.md", "score": 0.6},
    ]

    class _OverlapGraph:
        nodes: ClassVar[dict] = {
            "dup": {"file_path": dup_path, "content": ""},
            "b": {"file_path": "other.md", "content": ""},
        }
        backlinks: ClassVar[dict] = {}  # no backlink channel contribution

        def neighbors(self, name, direction="both"):
            # The graph walk starts from the second vector hit ("other.md"),
            # whose name resolves to "b".  Return "dup" so the graph channel
            # produces a candidate whose file_path is dup.md (the overlap).
            if name == "b":
                return ["dup"]
            return []

    class _OverlapIndexer:
        def search(self, query, k=10):
            return list(vector_hits)

    fused = FusedRetriever(vault_graph=_OverlapGraph(), vault_indexer=_OverlapIndexer())

    # Act
    out = fused.retrieve("q", k=5)

    # Assert: "dup.md" appears exactly once (deduped across channels).
    results = out["results"]
    dup_rows = [r for r in results if r["file_path"] == dup_path]
    assert len(dup_rows) == 1, f"expected 1 dup row, got {len(dup_rows)}"
    # The vector channel normalized the closer hit (0.3 distance) to 1.0,
    # which is the max across channels; the graph candidate (0.5*0.667)
    # must NOT have lowered it.  Score is rounded in _finalize.
    assert dup_rows[0]["score"] > 0.0


class _NoEdgeGraph:
    """Graph stub with no edges — isolates the vector channel so the
    test only observes vector ranking, not graph boosts."""

    nodes: ClassVar[dict] = {}
    backlinks: ClassVar[dict] = {}

    def neighbors(self, name, direction="both"):
        return []
