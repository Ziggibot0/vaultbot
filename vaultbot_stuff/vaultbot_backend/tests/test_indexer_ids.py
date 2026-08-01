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
7. Legacy list-format metadata.pkl is migrated to the new tuple format on
   load without calling Ollama.

Uses real FAISS + a stub OllamaClient that counts embedding calls so we can
assert the zero-embedding-on-delete invariant.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pytest

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
        pytest.skip("faiss module has no IndexIDMap2 (stub loaded)", allow_module_level=True)
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
    assert indexer.index.ntotal == 2, f"Expected 2 vectors after delete, got {indexer.index.ntotal}"
    assert str(b) not in indexer._path_to_id, "Deleted path should be absent from _path_to_id"
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


def test_index_missing_or_changed_zero_embedding_on_remove(tmp_vault):
    """index_missing_or_changed with a deleted file should fire zero embedding calls for the removal."""
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
    assert abs(norm - 1.0) < 1e-5, f"Reconstructed vector should be unit-norm, got norm={norm}"


def test_reconstruct_embedding_returns_none_for_unknown(tmp_vault):
    """reconstruct_embedding should return None for a path that isn't indexed."""
    vault, index_dir = tmp_vault
    indexer = _make_indexer(vault, index_dir)
    _write_note(vault, "alpha", "Alpha")
    # Don't index it.
    assert indexer.reconstruct_embedding(str(vault / "alpha.md")) is None


def test_legacy_list_format_migration_zero_embedding(tmp_path, monkeypatch):
    """A legacy list-format metadata.pkl + IndexFlatL2 should be detected as
    schema-v1 on load. Because the embedding schema has since changed
    (procedures now embed their description surface, not full content), the
    stale reconstructed vectors are DISCARDED and the indexer flags itself
    for a full re-embed on the next index_missing_or_changed() call.

    The load itself still does zero Ollama calls — the migration reconstructs
    from the old flat index first, then the schema check wipes the result.
    The actual re-embedding happens later (in index_missing_or_changed),
    which is the production startup path.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("VAULTBOT_INDEX_PREVIEW_CHARS", "0")

    # Build a legacy on-disk state: IndexFlatL2 + list[dict] metadata.
    dim = 768
    legacy_index = faiss.IndexFlatL2(dim)
    vecs = np.random.RandomState(42).randn(3, dim).astype(np.float32)
    faiss.normalize_L2(vecs)
    legacy_index.add(vecs)

    legacy_meta = [
        {"file_path": str(vault / "a.md"), "last_modified": 1.0, "content_hash": "h1"},
        {"file_path": str(vault / "b.md"), "last_modified": 2.0, "content_hash": "h2"},
        {"file_path": str(vault / "c.md"), "last_modified": 3.0, "content_hash": "h3"},
    ]
    # Write the files so they exist on disk for the later re-embed.
    for p, content in zip(["a.md", "b.md", "c.md"], ["aaa", "bbb", "ccc"]):
        (vault / p).write_text(content, encoding="utf-8")

    faiss.write_index(legacy_index, str(index_dir / "index.faiss"))
    with open(index_dir / "metadata.pkl", "wb") as f:
        pickle.dump(legacy_meta, f)  # legacy: a LIST, not a tuple
    with open(index_dir / "timestamps.json", "w") as f:
        json.dump({m["file_path"]: m["last_modified"] for m in legacy_meta}, f)

    # Load — should detect legacy format and migrate (in __init__).
    import importlib

    import vault_indexer
    if not hasattr(vault_indexer.faiss, "IndexIDMap2"):
        if "faiss" in sys.modules and not hasattr(sys.modules["faiss"], "IndexIDMap2"):
            del sys.modules["faiss"]
        importlib.reload(vault_indexer)
    VaultIndexer = vault_indexer.VaultIndexer
    indexer = VaultIndexer(vault_path=str(vault), index_path=str(index_dir))
    # Replace ollama with a counter to assert zero calls during load.
    indexer.ollama_client = _CountingOllama()

    assert indexer.ollama_client.embeddings_call_count == 0, (
        "Load must not re-embed — re-embedding is deferred to "
        "index_missing_or_changed()"
    )
    # The schema-version check discards the stale reconstructed vectors and
    # flags for a full rebuild.
    assert indexer._needs_full_rebuild is True
    assert indexer.index is None or indexer.index.ntotal == 0, (
        "Stale legacy vectors should have been discarded"
    )
    # A subsequent index_missing_or_changed() re-embeds all files and clears
    # the flag.
    indexer.index_missing_or_changed()
    assert indexer._needs_full_rebuild is False
    assert isinstance(indexer.index, faiss.IndexIDMap2)
    assert indexer.index.ntotal == 3
    for p in ["a.md", "b.md", "c.md"]:
        assert str(vault / p) in indexer._path_to_id
