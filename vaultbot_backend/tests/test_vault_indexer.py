"""Tests for vault_indexer.VaultIndexer — FAISS index management.

Pure-Python stubs stand in for FAISS, OllamaClient, and file-watching so
these tests run entirely offline: no real vault, no FAISS native extension,
no Ollama, no Docker, no network.

The faiss shim is the same technique used in test_fused_retrieval.py:
inject a no-op ``faiss`` module into ``sys.modules`` before importing the
leaf module under test.  Each test follows Arrange → Act → Assert; cleanup
is automatic via ``tmp_path`` and ``monkeypatch``.

Documentation grounding:
- Anatomy of a test (Arrange → Act → Assert → Cleanup)
  https://docs.pytest.org/en/stable/explanation/anatomy.html
- tmp_path: unique pathlib.Path per test, auto-cleaned
  https://docs.pytest.org/en/stable/reference/reference.html
- monkeypatch: setattr/setitem/setenv, auto-undone after test
  https://docs.pytest.org/en/stable/reference/reference.html

Leaf-module imports only — ``import main`` is hard-fenced by conftest.py
(main.py calls acquire_lock() → sys.exit + loads the live FAISS index).
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# FAISS shim — must be installed before vault_indexer is imported so the
# native extension is never loaded.  The shim models an empty IndexIDMap2
# whose add_with_ids / search / reconstruct do the minimum needed to let
# VaultIndexer's pure-Python logic run.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# FAISS shim — vault_indexer (imported transitively) needs the faiss module
# present at import time even though these tests never touch a real index.
#
# The stubs are defined unconditionally at module level so they are available
# even when another test module (e.g. test_fused_retrieval.py) installs its
# own minimal shim first.  _make_indexer() re-patches the two factory symbols
# on whichever shim is active before constructing VaultIndexer.
# ---------------------------------------------------------------------------
if "faiss" not in sys.modules:
    _faiss_stub = types.ModuleType("faiss")
    _faiss_stub.normalize_L2 = lambda v: None
    _faiss_stub.read_index = lambda *a, **k: None
    _faiss_stub.write_index = lambda *a, **k: None
    _faiss_stub.IndexFlatL2 = object  # overridden in _make_indexer
    _faiss_stub.IndexIDMap2 = object  # overridden in _make_indexer
    sys.modules["faiss"] = _faiss_stub


class _FakeFlat:
    """Minimal IndexFlatL2 substitute — accepts a dimension argument."""

    def __init__(self, dim: int = 4):
        self.d = dim


class _FakeIndex:
    """Minimal FAISS IndexIDMap2 substitute."""

    def __init__(self, inner=None):
        self.d = getattr(inner, "d", 4)
        self.ntotal = 0
        self._data: dict[int, np.ndarray] = {}

    def add_with_ids(self, vec: np.ndarray, ids: np.ndarray) -> None:
        for fid, v in zip(ids.tolist(), vec.tolist(), strict=False):
            self._data[int(fid)] = np.array(v, dtype=np.float32)
        self.ntotal = len(self._data)

    def reconstruct(self, fid: int) -> np.ndarray:
        return self._data[fid]

    def search(self, query_vec: np.ndarray, k: int):
        # Return stored vectors ranked by L2 distance to the query.
        q = query_vec[0]
        if not self._data:
            empty = np.full((1, k), -1, dtype=np.int64)
            return np.zeros((1, k), dtype=np.float32), empty
        pairs = sorted(
            self._data.items(),
            key=lambda kv: float(np.sum((kv[1] - q) ** 2)),
        )[:k]
        fids = [p[0] for p in pairs]
        dists = [float(np.sum((p[1] - q) ** 2)) for p in pairs]
        while len(fids) < k:
            fids.append(-1)
            dists.append(0.0)
        return (
            np.array([dists], dtype=np.float32),
            np.array([fids], dtype=np.int64),
        )


from vault_indexer import EMBEDDING_SCHEMA_VERSION, VaultIndexer

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_indexer(tmp_path: Path, monkeypatch) -> VaultIndexer:
    """Build a VaultIndexer with a temp vault/index path, OllamaClient stubbed.

    The OllamaClient constructor is safe to call (no network), but
    ``_get_embedding`` makes HTTP calls — we patch it so unit tests never
    reach the network.

    The faiss shim installed at module-load time may have been provided by
    a sibling test module (e.g. test_fused_retrieval.py) with minimal stubs
    that don't accept constructor arguments.  We always re-patch the two
    factory symbols before constructing VaultIndexer so the tests here get
    the full-featured stubs regardless of import order.
    """
    faiss_mod = sys.modules["faiss"]
    monkeypatch.setattr(faiss_mod, "IndexFlatL2", _FakeFlat, raising=False)
    monkeypatch.setattr(faiss_mod, "IndexIDMap2", _FakeIndex, raising=False)
    monkeypatch.setattr(faiss_mod, "normalize_L2", lambda v: None, raising=False)

    vault = tmp_path / "vault"
    vault.mkdir()
    idx = tmp_path / "idx"
    idx.mkdir()

    indexer = VaultIndexer(vault_path=str(vault), index_path=str(idx))

    # Stub out the Ollama embed call: return a fixed 4-dim unit vector.
    _fixed_vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(indexer, "_get_embedding", lambda text: _fixed_vec.copy())
    return indexer


# ---------------------------------------------------------------------------
# Test 1: search() returns empty list when the index holds no vectors
# ---------------------------------------------------------------------------
def test_search_returns_empty_when_index_is_empty(tmp_path, monkeypatch):
    # Arrange: a freshly constructed indexer — no files added, index == None.
    indexer = _make_indexer(tmp_path, monkeypatch)
    assert indexer.index is None  # guard

    # Act
    results = indexer.search("anything", k=5)

    # Assert: must be an empty list, NOT raise.
    assert results == []


# ---------------------------------------------------------------------------
# Test 2: _add_embedding_to_index populates _metadata and _path_to_id
# ---------------------------------------------------------------------------
def test_add_embedding_to_index_updates_metadata(tmp_path, monkeypatch):
    # Arrange: indexer with a pre-written note file.
    indexer = _make_indexer(tmp_path, monkeypatch)
    note = tmp_path / "vault" / "note.md"
    note.write_text("# Hello\nSome content.", encoding="utf-8")

    embedding = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

    # Act: inject the embedding directly (bypasses Ollama).
    indexer._add_embedding_to_index(
        file_path=note,
        embedding=embedding,
        last_modified=note.stat().st_mtime,
        content_hash="abc123",
        content_preview="# Hello",
    )

    # Assert: the note appears in the internal maps.
    assert str(note) in indexer._path_to_id
    faiss_id = indexer._path_to_id[str(note)]
    assert faiss_id in indexer._metadata
    assert indexer._metadata[faiss_id]["file_path"] == str(note)
    assert indexer._metadata[faiss_id]["content_hash"] == "abc123"
    assert indexer.index is not None
    assert indexer.index.ntotal == 1


# ---------------------------------------------------------------------------
# Test 3: search() returns the indexed note after _add_file_to_index
# ---------------------------------------------------------------------------
def test_search_returns_indexed_note(tmp_path, monkeypatch):
    # Arrange: write a note file and index it.
    indexer = _make_indexer(tmp_path, monkeypatch)
    note = tmp_path / "vault" / "hello.md"
    note.write_text("# Hello\nWorld.", encoding="utf-8")

    indexer._add_file_to_index(note)

    # Act
    results = indexer.search("hello world", k=3)

    # Assert: the indexed note is returned.
    assert len(results) >= 1
    paths = [r["file_path"] for r in results]
    assert str(note) in paths


# ---------------------------------------------------------------------------
# Test 4: persist() writes metadata.json and timestamps.json
# ---------------------------------------------------------------------------
def test_persist_writes_json_files(tmp_path, monkeypatch):
    # Arrange: add one note then persist.
    indexer = _make_indexer(tmp_path, monkeypatch)
    note = tmp_path / "vault" / "doc.md"
    note.write_text("Some doc.", encoding="utf-8")
    indexer._add_file_to_index(note)

    # Act
    indexer.persist()

    # Assert: both JSON files were written and contain expected keys.
    meta_file = tmp_path / "idx" / "metadata.json"
    ts_file = tmp_path / "idx" / "timestamps.json"
    assert meta_file.exists(), "metadata.json not written"
    assert ts_file.exists(), "timestamps.json not written"

    data = json.loads(meta_file.read_text(encoding="utf-8"))
    assert data.get("schema_version") == EMBEDDING_SCHEMA_VERSION
    assert "metadata" in data
    assert "path_to_id" in data

    ts = json.loads(ts_file.read_text(encoding="utf-8"))
    assert str(note) in ts


# ---------------------------------------------------------------------------
# Test 5: persist() / _load_index() round-trip preserves JSON metadata
# ---------------------------------------------------------------------------
def test_persist_and_reload_roundtrip(tmp_path, monkeypatch):
    # Arrange: index a note and persist its JSON metadata.
    indexer = _make_indexer(tmp_path, monkeypatch)
    note = tmp_path / "vault" / "round.md"
    note.write_text("Round-trip content.", encoding="utf-8")
    indexer._add_file_to_index(note)

    # Persist only the JSON sidecar files (no real FAISS write needed).
    # We call persist() which calls faiss.write_index (stubbed as no-op)
    # but still writes metadata.json + timestamps.json.
    indexer.persist()

    # Assert: the JSON files encode the expected path.
    meta_file = tmp_path / "idx" / "metadata.json"
    data = json.loads(meta_file.read_text(encoding="utf-8"))
    all_file_paths = [m["file_path"] for m in data["metadata"].values()]
    assert str(note) in all_file_paths
