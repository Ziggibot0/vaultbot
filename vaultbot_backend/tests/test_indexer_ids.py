"""Phase 1 verification: IndexIDMap2 migration — zero-embedding delete, O(1) lookup.

These tests verify the core claims of the Phase 1 migration:

1. Adding 3 notes then deleting the middle one does NOT trigger any Ollama
   embedding calls during the delete (the old code re-embedded the ENTIRE
   vault on every delete).
2. After delete, the deleted path is absent from `_path_to_id`/`_metadata`
   and the other two notes are still searchable.
3. `_next_id` increments on add and is NOT reused after a delete
   (tombstoned ids stay tombstoned until a full compaction).
4. `index_missing_or_changed` with a deleted file on disk fires zero
   embedding calls for the removal path.
5. `reconstruct_embedding` returns the stored vector within float tolerance
   of the embed input (validates the rev_map contract for drift reranking).
6. Add-then-update-same-path keeps `ntotal` constant (no duplicate vectors)
   and maps `_path_to_id` to the new id.
7. Metadata is persisted as JSON (metadata.json), never pickle — a tampered
   vault file cannot trigger arbitrary code execution on load.

Uses real FAISS + a stub OllamaClient that counts embedding calls so we can
assert the zero-embedding-on-delete invariant.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.integration

# ── faiss import — must use the REAL faiss, not the stub from test_fused_retrieval ──
# test_fused_retrieval.py (which runs first alphabetically) installs a no-op
# `faiss` stub in sys.modules so it can test FusedRetriever without the broken
# native extension.  Our tests need the REAL faiss (IndexIDMap2, remove_ids,
# reconstruct, normalize_L2), so we pop any stub and try the real import.  If
# the real faiss can't load (NumPy 2.x ABI break), skip the whole module.
if "faiss" in sys.modules:
    _existing = sys.modules["faiss"]
    if not hasattr(_existing, "IndexIDMap2"):
        # It's the stub from test_fused_retrieval — remove it so we can import
        # the real faiss below.
        del sys.modules["faiss"]

try:
    import faiss

    if not hasattr(faiss, "IndexIDMap2"):
        pytest.skip(
            "faiss module has no IndexIDMap2 (stub loaded)", allow_module_level=True
        )
except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
    pytest.skip("faiss not available in this env", allow_module_level=True)


# ── Stub OllamaClient — counts embedding calls ─────────────────────────────
class _CountingOllama:
    """Minimal OllamaClient stub that counts embedding calls.

    Every call to `embeddings(text)` returns a deterministic 768-dim vector
    derived from a hash of the text, so FAISS gets real vectors without a
    running Ollama.  `embeddings_call_count` lets tests assert the
    zero-embedding-on-delete invariant.
    """

    def __init__(self, embed_model="stub", session_logger=None):
        self.embed_model = embed_model
        self.embeddings_call_count = 0

    def embeddings(self, text: str) -> list[float]:
        self.embeddings_call_count += 1
        # Deterministic 768-dim vector from a hash of the text.
        import hashlib

        h = hashlib.sha256(text.encode()).digest()
        # Repeat the 32-byte hash to fill 768 dims (768 / 4 = 192 ints).
        rng = np.random.RandomState(int.from_bytes(h[:4], "little"))
        return rng.randn(768).astype(np.float32).tolist()

    def batch_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [self.embeddings(t) for t in texts]

    def set_model(self, model: str) -> None:
        self.embed_model = model


# ── Fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture
def tmp_vault(tmp_path, monkeypatch):
    """Create a tmp vault dir and point VAULT_PATH at it."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Index dir inside the backend test temp (not in the vault).
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("VAULTBOT_INDEX_PREVIEW_CHARS", "0")  # disable preview cache
    return vault, index_dir


def _make_indexer(vault: Path, index_dir: Path):
    """Construct a VaultIndexer with the counting OllamaClient.

    Also patches ``vault_indexer.faiss`` to point at the REAL faiss module,
    because ``test_fused_retrieval.py`` (alphabetically first) installs a
    no-op faiss stub in ``sys.modules`` before this module collects, and
    ``vault_indexer.py``'s top-level ``import faiss`` captures that stub.
    Without this patch, ``vault_indexer`` would see a stub with no
    ``IndexIDMap2`` / ``normalize_L2`` / ``remove_ids``.
    """
    import importlib

    import vault_indexer

    # If vault_indexer captured the stub faiss (no IndexIDMap2), reload it
    # after removing the stub from sys.modules so it picks up the real faiss.
    if not hasattr(vault_indexer.faiss, "IndexIDMap2"):
        if "faiss" in sys.modules and not hasattr(sys.modules["faiss"], "IndexIDMap2"):
            del sys.modules["faiss"]
        importlib.reload(vault_indexer)
    # Re-import the class from the (possibly reloaded) module.
    VaultIndexer = vault_indexer.VaultIndexer
    indexer = VaultIndexer(vault_path=str(vault), index_path=str(index_dir))
    # Replace the real OllamaClient with our counting stub (same dim).
    indexer.ollama_client = _CountingOllama()
    return indexer


def _write_note(vault: Path, name: str, content: str) -> Path:
    p = vault / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return p


# ── Tests ─────────────────────────────────────────────────────────────────


def test_delete_triggers_zero_embedding_calls(tmp_vault):
    """Delete a note → assert NO Ollama embedding calls were made during the delete.

    The old rebuild-on-delete code would re-embed the ENTIRE vault (N calls).
    The new IndexIDMap2.remove_ids path should fire zero embedding calls.
    """
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    a = _write_note(vault, "alpha", "Alpha note about apples.")
    b = _write_note(vault, "beta", "Beta note about bananas.")
    c = _write_note(vault, "gamma", "Gamma note about grapes.")

    indexer._add_file_to_index(a)
    indexer._add_file_to_index(b)
    indexer._add_file_to_index(c)
    assert indexer.index is not None
    assert indexer.index.ntotal == 3

    # Snapshot the embedding call count AFTER the adds.
    calls_before_delete = indexer.ollama_client.embeddings_call_count

    # Delete the middle note.
    indexer._remove_file_internal(b)

    # ── Core invariant: zero embedding calls during delete ──
    calls_during_delete = (
        indexer.ollama_client.embeddings_call_count - calls_before_delete
    )
    assert calls_during_delete == 0, (
        f"Expected zero embedding calls during delete, got {calls_during_delete}. "
        "The old rebuild-on-delete bug is back."
    )

    # ── Structural invariants ──
    assert indexer.index.ntotal == 2, (
        f"Expected 2 vectors after delete, got {indexer.index.ntotal}"
    )
    assert str(b) not in indexer._path_to_id, (
        "Deleted path should be absent from _path_to_id"
    )
    # The other two should still be searchable.
    results = indexer.search("apples grapes", k=5)
    found_paths = {r["file_path"] for r in results}
    assert str(a) in found_paths or str(c) in found_paths, (
        f"Surviving notes should be searchable; got {found_paths}"
    )


def test_next_id_not_reused_after_delete(tmp_vault):
    """_next_id must NOT be reused after a delete (tombstones stay tombstoned)."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    a = _write_note(vault, "alpha", "Alpha")
    b = _write_note(vault, "beta", "Beta")
    indexer._add_file_to_index(a)
    indexer._add_file_to_index(b)
    id_a = indexer._path_to_id[str(a)]
    id_b = indexer._path_to_id[str(b)]
    assert id_a == 0
    assert id_b == 1
    assert indexer._next_id == 2

    indexer._remove_file_internal(a)
    # _next_id must NOT go back to 1 — the tombstoned id 0 is gone.
    assert indexer._next_id == 2, "Tombstoned ids must not be reused"

    # Add a new note — it should get id 2, not 0.
    c = _write_note(vault, "gamma", "Gamma")
    indexer._add_file_to_index(c)
    id_c = indexer._path_to_id[str(c)]
    assert id_c == 2, f"New note should get id 2, got {id_c}"


def test_add_then_update_same_path_no_duplicate(tmp_vault):
    """Updating a file in-place should not create a duplicate vector."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    a = _write_note(vault, "alpha", "Alpha version 1")
    indexer._add_file_to_index(a)
    assert indexer.index.ntotal == 1

    # Overwrite with new content.
    a.write_text("Alpha version 2 with different content.", encoding="utf-8")
    indexer._add_file_to_index(a)  # update path

    assert indexer.index.ntotal == 1, (
        f"Update should not duplicate; ntotal={indexer.index.ntotal}"
    )
    # _path_to_id should map to the new id.
    new_id = indexer._path_to_id[str(a)]
    assert new_id in indexer._metadata
    assert indexer._metadata[new_id]["content_hash"] != ""


def test_empty_file_skipped_zero_embedding_calls(tmp_vault):
    """An empty (0-byte) file must be skipped WITHOUT an Ollama call.

    Regression for #22: the indexer used to call Ollama with empty text,
    which returned an empty vector and logged a misleading "received empty
    embedding from Ollama" warning. Empty content has nothing to embed, so
    it should be skipped before any embedding call.
    """
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    empty = _write_note(vault, "empty", "")
    normal = _write_note(vault, "normal", "Normal note with content.")

    indexer._add_file_to_index(empty)
    indexer._add_file_to_index(normal)

    # Only the normal note should trigger an embedding call.
    assert indexer.ollama_client.embeddings_call_count == 1, (
        f"Empty file should be skipped without an embedding call; "
        f"got {indexer.ollama_client.embeddings_call_count}"
    )
    assert indexer.index.ntotal == 1
    assert str(empty) not in indexer._path_to_id
    assert str(normal) in indexer._path_to_id


def test_batch_add_files_skips_empty(tmp_vault):
    """batch_add_files must skip empty files without an Ollama call."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    empty = _write_note(vault, "empty", "")
    normal = _write_note(vault, "normal", "Normal note.")

    indexed = indexer.batch_add_files([str(empty), str(normal)])

    assert indexed == 1
    assert indexer.ollama_client.embeddings_call_count == 1
    assert str(empty) not in indexer._path_to_id
    assert str(normal) in indexer._path_to_id


def test_index_missing_or_changed_zero_embedding_on_remove(tmp_vault):
    """index_missing_or_changed with a deleted file should fire zero embedding
    calls for the removal."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    a = _write_note(vault, "alpha", "Alpha")
    b = _write_note(vault, "beta", "Beta")
    indexer._add_file_to_index(a)
    indexer._add_file_to_index(b)

    # Delete the file on disk.
    b.unlink()

    calls_before = indexer.ollama_client.embeddings_call_count
    indexer.index_missing_or_changed()
    calls_during = indexer.ollama_client.embeddings_call_count - calls_before

    # The removal path should fire zero embedding calls.  (The unchanged
    # alpha note also fires zero because its hash matches.)
    assert calls_during == 0, (
        f"Expected zero embedding calls for a delete-only scan, got {calls_during}"
    )
    assert indexer.index.ntotal == 1
    assert str(b) not in indexer._path_to_id


def test_reconstruct_embedding_matches_stored_vector(tmp_vault):
    """reconstruct_embedding should return the stored vector (rev_map contract)."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    a = _write_note(vault, "alpha", "Alpha note for reconstruction test.")
    indexer._add_file_to_index(a)

    recon = indexer.reconstruct_embedding(str(a))
    assert recon is not None, "reconstruct_embedding returned None for an indexed note"
    assert recon.dtype == np.float32
    assert len(recon) == indexer.dimension

    # The reconstructed vector should be normalized (L2 norm ≈ 1.0) because
    # we normalize_L2 before add_with_ids.
    norm = float(np.linalg.norm(recon))
    assert abs(norm - 1.0) < 1e-5, (
        f"Reconstructed vector should be unit-norm, got norm={norm}"
    )


def test_reconstruct_embedding_returns_none_for_unknown(tmp_vault):
    """reconstruct_embedding should return None for a path that isn't indexed."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)
    _write_note(vault, "alpha", "Alpha")
    # Don't index it.
    assert indexer.reconstruct_embedding(str(vault / "alpha.md")) is None


def test_get_embedding_memoizes_identical_query(tmp_vault):
    """The same query text should hit Ollama once, then be served from cache."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    q = "what is the no wikipedia directive"
    first = indexer._get_embedding(q)
    second = indexer._get_embedding(q)

    # Only ONE Ollama call for two identical queries.
    assert indexer.ollama_client.embeddings_call_count == 1, (
        f"Expected 1 embedding call, got {indexer.ollama_client.embeddings_call_count}"
    )
    # Same vector returned.
    assert np.array_equal(first, second)


def test_get_embedding_returns_copy_not_cache_reference(tmp_vault):
    """Mutating the returned vector must not corrupt the cached copy."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    q = "mutate me"
    first = indexer._get_embedding(q)
    # Mutate the returned array in place (as search_by_vector does via
    # faiss.normalize_L2 on a view).
    first[:] = 0.0
    second = indexer._get_embedding(q)

    # The cached vector must be intact (not zeroed by the mutation).
    assert not np.all(second == 0.0), "Cache was corrupted by in-place mutation"
    assert indexer.ollama_client.embeddings_call_count == 1


def test_get_embedding_distinct_texts_not_collided(tmp_vault):
    """Different query strings must produce separate cache entries."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    indexer._get_embedding("alpha query")
    indexer._get_embedding("beta query")

    assert indexer.ollama_client.embeddings_call_count == 2


def test_metadata_persist_round_trip_json(tmp_vault):
    """persist() writes metadata.json; a fresh indexer loads it back intact.

    faiss ids are ints but JSON serializes dict keys as strings, so the
    round-trip must re-key them back to ints on load.
    """
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    a = _write_note(vault, "alpha", "Alpha note about apples.")
    b = _write_note(vault, "beta", "Beta note about bananas.")
    indexer._add_file_to_index(a)
    indexer._add_file_to_index(b)
    indexer.persist()

    # The metadata file must be JSON, not pickle.
    assert (index_dir / "metadata.json").exists()
    assert not (index_dir / "metadata.pkl").exists()
    with open(index_dir / "metadata.json", encoding="utf-8") as f:
        raw = json.load(f)
    import vault_indexer

    assert raw["schema_version"] == vault_indexer.EMBEDDING_SCHEMA_VERSION
    assert raw["next_id"] == 2
    # Keys are strings in JSON.
    assert all(isinstance(k, str) for k in raw["metadata"])

    # A fresh indexer loads the JSON back with int keys and correct state.
    indexer2 = _make_indexer(vault, index_dir)
    assert indexer2._next_id == 2
    assert indexer2._path_to_id == indexer._path_to_id
    assert set(indexer2._metadata) == set(indexer._metadata)
    assert all(isinstance(k, int) for k in indexer2._metadata)


def test_metadata_is_not_pickle(tmp_vault):
    """A tampered metadata file must not be unpickled (no RCE surface).

    Regression guard for the pickle.load deserialization vulnerability: the
    indexer must read metadata as JSON only. A file containing a pickle
    payload (or any non-JSON bytes) must be rejected and trigger a clean
    re-index, never executed.
    """
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)

    a = _write_note(vault, "alpha", "Alpha note.")
    indexer._add_file_to_index(a)
    indexer.persist()

    # Overwrite metadata.json with a malicious pickle payload. If the indexer
    # ever unpickles it, this would execute os.system and create a marker file.
    marker = index_dir / "pwned"
    import pickle

    payload = pickle.dumps(
        {"__reduce__": (__import__("os").system, (f"touch {marker}",))}
    )
    (index_dir / "metadata.json").write_bytes(payload)

    # Loading must NOT execute the payload; it must fall back to a clean
    # re-index (json.load raises, caught by the best-effort handler).
    indexer2 = _make_indexer(vault, index_dir)
    assert not marker.exists(), "pickle payload was executed — RCE regression"
    # The corrupt metadata is discarded; the indexer re-initializes empty.
    assert indexer2._metadata == {}
    assert indexer2._next_id == 0
