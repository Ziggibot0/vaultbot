"""FAISS vector index — embedding storage, similarity search, and file watching.

Indexes all .md files in the vault using nomic-embed-text embeddings. Watches
for file changes and updates the index incrementally. Supports chunked
embeddings for long notes and drift feedback for relevance tuning.

File-watching (``VaultChangeHandler``, ``_is_ignored_path``, ``IGNORED_DIRS``)
lives in ``vault_watcher.py``; embedding-text/chunking helpers live in
``embedding_utils.py``.  This module re-exports them for backwards
compatibility so existing imports (``from vault_indexer import
VaultChangeHandler``) keep working.
"""

import hashlib
import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from embedding_utils import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    embedding_text_for_note,
    get_chunked_embedding,
    split_into_chunks,
)
from ollama_client import OllamaClient
from vault_watcher import IGNORED_DIRS, VaultChangeHandler, _is_ignored_path
from watchdog.observers import Observer

_logger = logging.getLogger(__name__)

# Re-export so `from vault_indexer import VaultChangeHandler` etc. keep working.
__all__ = [
    "VaultIndexer",
    "VaultChangeHandler",
    "IGNORED_DIRS",
    "_is_ignored_path",
    "EMBEDDING_SCHEMA_VERSION",
]

# Embedding schema version. Bumped whenever the text we embed for a note
# changes meaningfully (e.g. procedures now embed their description surface
# instead of full content). On load, if the persisted version doesn't match,
# the index is rebuilt from scratch so every vector reflects the current
# embedding strategy. See embedding_text_for_note.
EMBEDDING_SCHEMA_VERSION = 4


class VaultIndexer:
    def __init__(
        self,
        vault_path: str,
        index_path: str | None = None,
        session_logger=None,
        trigger_store: Any = None,
    ):
        self.vault_path = Path(vault_path).resolve()
        if index_path is None:
            # Store index in the backend folder, not in the vault
            self.index_path = Path(__file__).parent / "vaultbot_index"
        else:
            self.index_path = Path(index_path)
        self.index_path.mkdir(exist_ok=True)

        self.index_file = self.index_path / "index.faiss"
        self.metadata_file = self.index_path / "metadata.pkl"
        self.timestamp_file = self.index_path / "timestamps.json"

        self.session_logger = session_logger
        # Trigger/inhibitor phrase-embedding store (optional, bonus layer — see trigger_store.py).
        self.trigger_store = trigger_store
        self.ollama_client = OllamaClient(
            embed_model=os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            session_logger=session_logger,
        )
        self.dimension = None  # set after first embedding
        self.index = None
        # Phase 1: id-keyed metadata + IndexIDMap2. _metadata: faiss id → meta dict;
        # _path_to_id: str(file_path) → faiss id (O(1) lookup). _next_id is monotonic;
        # tombstoned ids reused only after _rebuild_index compaction. The legacy
        # `self.metadata` list is kept as a back-compat @property below.
        self._metadata: dict[int, dict[str, Any]] = {}
        self._path_to_id: dict[str, int] = {}
        self._next_id: int = 0
        self.timestamps = {}  # file_path -> last_modified timestamp
        # Bounded content-preview cache (populated at index time) so search()
        # returns snippets WITHOUT re-reading files. 0 via VAULTBOT_INDEX_PREVIEW_CHARS
        # disables (full content, reads from disk). Default 2000 covers all consumers.
        self.preview_chars = int(os.getenv("VAULTBOT_INDEX_PREVIEW_CHARS", "2000"))
        self._needs_full_rebuild = False  # set by _load_index on schema mismatch

        self.observer = None
        self._load_index()

    # Back-compat: external callers iterate `indexer.metadata` as a list of dicts.
    @property
    def metadata(self) -> list[dict[str, Any]]:
        return list(self._metadata.values())

    @metadata.setter
    def metadata(self, value):
        # Legacy callers / migration may assign a list — re-key into id dict.
        self._metadata = {}
        self._path_to_id = {}
        for i, m in enumerate(value):
            self._metadata[i] = m
            self._path_to_id[m["file_path"]] = i
        self._next_id = len(value)

    def _log_tool(self, method: str, inputs: dict[str, Any] | None = None, outputs: Any = None, error: str | None = None):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(tool="vault_indexer", method=method, inputs=inputs, outputs=outputs, error=error)

    def _load_index(self):
        """Load existing index and metadata from disk, or initialize new.

        Handles on-disk formats: new (tuple of 3 or 4 with schema version +
        IndexIDMap2) and legacy (list[dict] + IndexFlatL2, migrated in-place
        via reconstruct — zero Ollama calls).
        """
        if (
            self.index_file.exists()
            and self.metadata_file.exists()
            and self.timestamp_file.exists()
        ):
            try:
                self.index = faiss.read_index(str(self.index_file))
                with open(self.metadata_file, "rb") as f:
                    loaded = pickle.load(f)
                with open(self.timestamp_file) as f:
                    self.timestamps = json.load(f)

                # Detect format: tuple(3)=v1, tuple(4)=v2+ (schema ver), list=legacy.
                _stored_schema_version = 1
                if isinstance(loaded, tuple) and len(loaded) == 4:
                    (self._metadata, self._path_to_id, self._next_id,
                     _stored_schema_version) = loaded
                elif isinstance(loaded, tuple) and len(loaded) == 3:
                    self._metadata, self._path_to_id, self._next_id = loaded
                    # Normalize stale relative paths to absolute.
                    for fid, meta in list(self._metadata.items()):
                        fp = Path(meta["file_path"])
                        if not fp.is_absolute():
                            resolved = (self.vault_path / fp).resolve()
                            old_key = meta["file_path"]
                            meta["file_path"] = str(resolved)
                            self._path_to_id.pop(old_key, None)
                            self._path_to_id[str(resolved)] = fid
                else:
                    # Legacy list format — migrate to id-keyed dict + IndexIDMap2.
                    _logger.info("[migration] Detected legacy list-format; converting to IndexIDMap2...")
                    legacy_list = loaded if isinstance(loaded, list) else []
                    self._metadata = {}
                    self._path_to_id = {}
                    old_index = self.index
                    dim = old_index.d if old_index is not None else None
                    self.index = None  # let _add_embedding_to_index create it
                    for i, meta in enumerate(legacy_list):
                        fp = Path(meta["file_path"])
                        if not fp.is_absolute():
                            fp = (self.vault_path / fp).resolve()
                            meta["file_path"] = str(fp)
                        try:
                            vec = old_index.reconstruct(i).astype(np.float32)  # type: ignore
                        except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                            _logger.info(f"[migration] Skipping unreconstructable legacy vector {i} ({meta['file_path']})")
                            continue
                        self._add_embedding_to_index(
                            fp, vec, meta.get("last_modified", 0.0),
                            meta.get("content_hash", ""),
                            content_preview=meta.get("content_preview", ""),
                        )
                    self.dimension = dim
                    _logger.info(f"[migration] Migrated {self._next_id} vectors to IndexIDMap2.")

                # If the embedding schema changed, stored vectors no longer
                # match the text we now embed — discard and force full rebuild.
                if _stored_schema_version != EMBEDDING_SCHEMA_VERSION:
                    _logger.info(
                        f"[migration] Embedding schema version changed "
                        f"({_stored_schema_version} -> {EMBEDDING_SCHEMA_VERSION}); "
                        f"discarding {self.index.ntotal if self.index else 0} "
                        f"stale vectors for a full re-embed."
                    )
                    self._init_new_index()
                    self._needs_full_rebuild = True
                else:
                    self._needs_full_rebuild = False

                if self.index is not None:
                    self.dimension = self.index.d
                    _logger.info(
                        f"Loaded existing index with {self.index.ntotal} vectors from {self.index_file}"
                    )
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                _logger.warning(
                    f"Error loading existing index: {e}. Creating new index."
                )
                self._init_new_index()
                self._needs_full_rebuild = True
        else:
            _logger.info("No existing index found. Creating new index.")
            self._init_new_index()

    def _init_new_index(self):
        """Initialize a new empty index."""
        self.index = None  # dimension set on first vector add
        self._metadata = {}
        self._path_to_id = {}
        self._next_id = 0
        self.timestamps = {}
        self._needs_full_rebuild = getattr(self, "_needs_full_rebuild", False)

    def _get_file_hash(self, file_path: Path) -> str:
        """Compute a hash of the file content to detect changes."""
        try:
            with open(file_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except OSError:  # file gone / permission error — treat as unchanged
            return ""

    def _get_embedding(self, text: str) -> np.ndarray:
        """Get embedding for text using Ollama (chunked for > 4000 chars)."""
        if len(text) > 4000:
            return self._get_chunked_embedding(text)
        embedding = self.ollama_client.embeddings(text)
        return np.array(embedding, dtype=np.float32)

    _CHUNK_SIZE = CHUNK_SIZE
    _CHUNK_OVERLAP = CHUNK_OVERLAP

    def _get_chunked_embedding(self, text: str) -> np.ndarray:
        """Embed long text by chunking + averaging (delegates to embedding_utils)."""
        return get_chunked_embedding(
            text, self.ollama_client, self._CHUNK_SIZE, self._CHUNK_OVERLAP
        )

    @staticmethod
    def _split_into_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
        """Split text into overlapping chunks on paragraph boundaries (delegates to embedding_utils)."""
        return split_into_chunks(text, chunk_size, overlap)

    @staticmethod
    def _embedding_text_for_note(file_path: Path, content: str) -> str:
        """Return the text that should be EMBEDDED for a note (delegates to embedding_utils).

        See ``embedding_utils.embedding_text_for_note`` for the full
        rationale on procedure discovery surfaces vs. full-content embedding.
        """
        return embedding_text_for_note(file_path, content)

    def _update_trigger_store(self, file_path: Path, content: str) -> None:
        """Parse trigger/inhibitor frontmatter and update the store.

        Uses ``note_schema.parse_frontmatter`` (handles list values).  A note
        with neither field has its entry removed so a stale gate doesn't
        persist.  Called from ``_add_file_to_index`` when wired.
        """
        from note_schema import parse_frontmatter

        fm = parse_frontmatter(content)
        triggers = fm.get("trigger") or []
        inhibitors = fm.get("inhibitor") or []
        if isinstance(triggers, str):
            triggers = [triggers]
        if isinstance(inhibitors, str):
            inhibitors = [inhibitors]
        self.trigger_store.update_note(str(file_path), triggers, inhibitors)

    def _add_file_to_index(self, file_path: Path):
        """Read a file, compute embedding, and add to index."""
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return  # file was deleted between the watcher event and open — normal race
        except OSError as e:  # permission error / IO error
            _logger.warning(f"Error reading file {file_path}: {e}")
            return

        content_hash = self._get_file_hash(file_path)
        last_modified = file_path.stat().st_mtime

        # Skip unchanged (O(1) lookup); update existing by removing old vector.
        key = str(file_path)
        existing_id = self._path_to_id.get(key)
        if existing_id is not None:
            meta = self._metadata.get(existing_id)
            if meta and meta.get("content_hash") == content_hash:
                return
            self._remove_file_internal(file_path)

        # Procedures embed description surface; other notes embed full content.
        embed_text = self._embedding_text_for_note(file_path, content)
        try:
            embedding = self._get_embedding(embed_text)
        except (RuntimeError, ConnectionError) as e:  # Ollama down / API error
            _logger.warning(f"Error getting embedding for {file_path}: {e}")
            return

        # Update trigger store from frontmatter (best-effort).
        if self.trigger_store is not None:
            try:
                self._update_trigger_store(file_path, content)
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                _logger.warning("trigger store update failed for %s: %s", file_path, e)

        self._add_embedding_to_index(
            file_path, embedding, last_modified, content_hash, content_preview=content
        )

    def _add_embedding_to_index(
        self,
        file_path: Path,
        embedding: np.ndarray,
        last_modified: float,
        content_hash: str,
        content_preview: str = "",
    ):
        """Add a pre-computed embedding to the index (shared by single and batch paths).

        ``content_preview`` is an optional bounded slice cached so search
        results can return a snippet without re-reading the file from disk.
        """
        embed_dim = len(embedding)
        if embed_dim == 0:
            _logger.warning(f"Skipping {file_path}: received empty embedding from Ollama.")
            self._log_tool("add_file", {"file_path": str(file_path), "last_modified": last_modified, "content_hash": content_hash}, error="empty embedding")
            return

        if self.index is None:
            self.dimension = embed_dim
            self.index = faiss.IndexIDMap2(faiss.IndexFlatL2(self.dimension))
            _logger.info(f"Initialized new IndexIDMap2 with dimension {self.dimension}")
        elif embed_dim != self.index.d:
            _logger.warning(f"Skipping {file_path}: dim {embed_dim} != index dim {self.index.d}.")
            self._log_tool("add_file", {"file_path": str(file_path), "last_modified": last_modified, "content_hash": content_hash}, error=f"dimension mismatch: {embed_dim} vs {self.index.d}")
            return

        # Normalize in-place so L2 distance ≡ cosine distance (unit vectors:
        # ||a−b||² = 2(1−cos(a,b)), so L2 ranking == cosine ranking).
        vec = embedding.reshape(1, -1).astype(np.float32)
        faiss.normalize_L2(vec)

        faiss_id = self._next_id
        self._next_id += 1
        index = self.index
        assert index is not None
        index.add_with_ids(vec, np.array([faiss_id], dtype=np.int64))  # type: ignore
        abs_path_str = str(
            file_path if file_path.is_absolute() else file_path.resolve()
        )
        meta_entry: dict[str, Any] = {
            "file_path": abs_path_str,
            "last_modified": last_modified,
            "content_hash": content_hash,
        }
        # Cache the bounded preview so future searches skip the disk read.
        if self.preview_chars > 0 and content_preview:
            meta_entry["content_preview"] = content_preview[: self.preview_chars]
        self._metadata[faiss_id] = meta_entry
        self._path_to_id[abs_path_str] = faiss_id
        self.timestamps[abs_path_str] = last_modified
        _logger.debug(f"Added {file_path} to index. Total vectors: {self.index.ntotal}")
        self._log_tool("add_file", {"file_path": abs_path_str, "last_modified": last_modified, "content_hash": content_hash})

    def batch_add_files(self, file_paths: list[str], return_embeddings: bool = False):
        """Add multiple files to the index using parallel embedding calls.

        Returns the number of files indexed, or ``(indexed, {path: emb})``
        when ``return_embeddings`` is True (e.g. A-MEM neighbor reuse).
        """
        if not file_paths:
            return (0, {}) if return_embeddings else 0

        # Read all files and collect content + metadata
        contents: list[str] = []
        valid_paths: list[Path] = []
        hashes: list[str] = []
        timestamps: list[float] = []

        for fp_str in file_paths:
            fp = Path(fp_str)
            if not fp.exists():
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:  # file gone between exists() and read — skip
                continue
            # Skip unchanged files (O(1) lookup)
            content_hash = self._get_file_hash(fp)
            key = str(fp)
            existing_id = self._path_to_id.get(key)
            if existing_id is not None:
                meta = self._metadata.get(existing_id)
                if meta and meta.get("content_hash") == content_hash:
                    continue  # unchanged
            contents.append(content)
            valid_paths.append(fp)
            hashes.append(content_hash)
            timestamps.append(fp.stat().st_mtime)

        if not contents:
            return (0, {}) if return_embeddings else 0

        # Short texts → parallel batch; long texts → chunked embedding.
        # Procedures embed description surface (see _embedding_text_for_note).
        from concurrent.futures import ThreadPoolExecutor, as_completed

        embed_texts = [self._embedding_text_for_note(fp, content) for fp, content in zip(valid_paths, contents)]

        def _embed_one(text: str):
            if len(text) > 4000:
                return self._get_chunked_embedding(text)
            return np.array(self.ollama_client.embeddings(text), dtype=np.float32)

        embeddings: list[np.ndarray | None] = [None] * len(embed_texts)
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_embed_one, t): i for i, t in enumerate(embed_texts)
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    embeddings[i] = future.result()
                except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                    embeddings[i] = None

        # Add to index
        indexed = 0
        emb_by_path: dict[str, list[float]] = {}
        for fp, emb, last_mod, ch, cont in zip(
            valid_paths, embeddings, timestamps, hashes, contents
        ):
            if emb is None:
                continue
            # Remove old entry if it exists
            self._remove_file_internal(fp)
            self._add_embedding_to_index(
                fp, np.array(emb, dtype=np.float32), last_mod, ch, content_preview=cont
            )
            indexed += 1
            if return_embeddings:
                # emb may be an np.ndarray (chunked) or a plain list.
                emb_by_path[str(fp)] = list(np.asarray(emb).tolist())

        if indexed:
            self.persist()
        if return_embeddings:
            return indexed, emb_by_path
        return indexed

    def _remove_file_internal(self, file_path: Path):
        """Remove a file from the index — O(1), zero re-embedding.

        IndexIDMap2.remove_ids tombstones the id in one pass — no Ollama
        calls.  Tombstoned ids are never reused until _rebuild_index compaction.
        """
        key = str(file_path)
        # Clean trigger store even if note wasn't in FAISS (best-effort).
        if self.trigger_store is not None:
            try:
                self.trigger_store.remove_note(key)
            except Exception as e:  # noqa: BLE001 — best-effort
                _logger.warning("trigger store remove failed for %s: %s", key, e)
        faiss_id = self._path_to_id.pop(key, None)
        if faiss_id is None:
            return  # not indexed — nothing to do
        self._metadata.pop(faiss_id, None)
        self.timestamps.pop(key, None)
        if self.index is not None:
            try:
                self.index.remove_ids(np.array([faiss_id], dtype=np.int64))  # type: ignore
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                self._log_tool("remove_file", {"file_path": key}, error=str(e))
                raise
        _logger.debug(f"Removed {file_path} from index. Total vectors: {self.index.ntotal if self.index else 0}")
        self._log_tool("remove_file", {"file_path": key})

    def _rebuild_index(self):
        """Compact the index by reconstructing live vectors — zero Ollama calls.

        Kept for corruption recovery and optional compaction (remove_ids
        leaves tombstones).  Rebuilds a fresh IndexIDMap2 from
        reconstruct(id) of all live ids — zero embedding calls.  Prunes
        files whose paths no longer exist on disk.
        """
        if not self._metadata:
            self.index = None
            self.dimension = None
            self._path_to_id = {}
            self._next_id = 0
            return

        # Reconstruct live vectors from FAISS (zero Ollama calls); prune
        # any whose path is gone on disk.
        live_ids = []
        live_vecs = []
        dead_keys = []
        for fid, meta in self._metadata.items():
            fp = Path(meta["file_path"])
            if not fp.exists():
                dead_keys.append((fid, meta["file_path"]))
                continue
            try:
                vec = self.index.reconstruct(fid).astype(np.float32).reshape(1, -1)  # type: ignore
                live_ids.append(fid)
                live_vecs.append(vec)
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                dead_keys.append((fid, meta["file_path"]))
                _logger.warning(f"Pruning unreconstructable id {fid} ({meta['file_path']}): {e}")
                continue

        for fid, fp_str in dead_keys:
            self._metadata.pop(fid, None)
            self._path_to_id.pop(fp_str, None)
            self.timestamps.pop(fp_str, None)

        if not live_vecs:
            self.index = None
            self.dimension = None
            self._path_to_id = {}
            self._next_id = 0
            return

        # Build fresh compact IndexIDMap2 with same ids + normalized vectors.
        stacked = np.vstack(live_vecs).astype(np.float32)
        faiss.normalize_L2(stacked)
        ids_arr = np.array(live_ids, dtype=np.int64)
        self.dimension = stacked.shape[1]
        self.index = faiss.IndexIDMap2(faiss.IndexFlatL2(self.dimension))
        self.index.add_with_ids(stacked, ids_arr)  # type: ignore
        _logger.info(f"Compacted index with {len(live_ids)} live vectors (pruned {len(dead_keys)} dead)")

    def _update_file(self, file_path_str: str):
        """Update a file in the index (called on modification)."""
        file_path = Path(file_path_str)
        if not file_path.exists():
            # File was deleted, handle in on_deleted
            return
        self._add_file_to_index(file_path)

    def _add_file(self, file_path_str: str):
        """Add a new file to the index (called on creation)."""
        file_path = Path(file_path_str)
        if not file_path.exists():
            return
        self._add_file_to_index(file_path)

    def _remove_file(self, file_path_str: str):
        """Remove a file from the index (called on deletion)."""
        file_path = Path(file_path_str)
        self._remove_file_internal(file_path)

    def start_watching(self):
        """Start watching the vault for changes."""
        if self.observer is not None:
            self.observer.stop()
        self.observer = Observer()
        event_handler = VaultChangeHandler(self)
        # Recursively watch the vault, but ignore the backend venv/index folders
        self.observer.schedule(event_handler, str(self.vault_path), recursive=True)
        # Filter later by path checks in event handler
        self.observer.start()
        _logger.info(f"Started watching vault at {self.vault_path}")

    def stop_watching(self):
        """Stop watching the vault."""
        if self.observer is not None:
            self.observer.stop()
            self.observer.join()
            self.observer = None

    def _collect_md_files(self) -> list[Path]:
        """Scan the vault for markdown files, skipping ignored directories."""
        return [p for p in self.vault_path.rglob("*.md") if not _is_ignored_path(p)]

    def load(self):
        """Load a persisted index quickly without making Ollama calls."""
        _logger.info("Loading vault index...")
        # _load_index already ran in __init__; just report the state.
        total = self.index.ntotal if self.index is not None else 0
        md_files = self._collect_md_files()
        _logger.info(
            f"Loaded index with {total} vectors. {len(md_files)} markdown files in vault."
        )

    def index_missing_or_changed(self):
        """Re-index only new or changed markdown files. This is safe to run in the background."""
        _logger.info("Starting background vault indexing...")
        md_files = self._collect_md_files()
        changed_or_missing = []

        # Build a quick lookup from metadata by file path.
        meta_by_path = {meta["file_path"]: meta for meta in self.metadata}

        # If the embedding schema changed, re-embed EVERY file — content
        # hashes haven't changed but the vectors are stale. Flag set by
        # _load_index, cleared after.
        force_full = getattr(self, "_needs_full_rebuild", False)
        if force_full:
            _logger.info(
                "[migration] Forcing full re-embed of all %d notes "
                "(embedding schema version changed).",
                len(md_files),
            )

        for file_path in md_files:
            key = str(file_path)
            meta = meta_by_path.get(key)
            if meta is None:
                # Brand new file.
                changed_or_missing.append(file_path)
                continue

            # If the file no longer exists at this exact path, skip it; the watcher will remove it.
            if not file_path.exists():
                continue

            if force_full:
                changed_or_missing.append(file_path)
                continue

            # Only re-embed if the content hash changed.
            current_hash = self._get_file_hash(file_path)
            if meta.get("content_hash") != current_hash:
                changed_or_missing.append(file_path)

        # Also detect deleted files and remove them from the index.
        current_paths = {str(p) for p in md_files}
        removed_paths = [Path(p) for p in meta_by_path if p not in current_paths]

        _logger.info(
            f"Background indexing: {len(changed_or_missing)} changed/missing, {len(removed_paths)} removed out of {len(md_files)} total files."
        )

        for file_path in removed_paths:
            self._remove_file_internal(file_path)

        for file_path in changed_or_missing:
            self._add_file_to_index(file_path)

        # Schema-version forced re-embed is done; clear the flag so we don't
        # re-embed everything on every subsequent call.
        if force_full:
            self._needs_full_rebuild = False

        self.persist()
        _logger.info(
            f"Background indexing complete. Index now has {self.index.ntotal if self.index else 0} vectors."
        )

    def initialize(self):
        """Perform initial indexing of the vault and start watching.

        Kept for backwards compatibility; prefer load() + index_missing_or_changed().
        """
        self.load()
        self.index_missing_or_changed()

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Search the vault for the k most relevant notes to `query`.

        Returns [{'file_path', 'content', 'score'}] sorted by relevance.
        """
        if self.index is None or self.index.ntotal == 0 or not self._metadata:
            self._log_tool("search", {"query": query, "k": k}, outputs={"result_count": 0}, error="empty index")
            return []

        try:
            query_embedding = self._get_embedding(query)
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            self._log_tool("search", {"query": query, "k": k}, error=str(e))
            raise

        return self.search_by_vector(query_embedding, k)

    def reconstruct_embedding(self, file_path: str) -> np.ndarray | None:
        """Pull a note's embedding from FAISS (zero Ollama calls) for drift re-ranking.

        Returns the float32 embedding (normalized), or None if not indexed.
        """
        if self.index is None or not self._metadata:
            return None
        faiss_id = self._path_to_id.get(file_path)
        if faiss_id is None:
            return None
        try:
            return self.index.reconstruct(faiss_id).astype(np.float32)  # type: ignore
        except RuntimeError as e:  # faiss raises on bad/tombstoned id
            self._log_tool("reconstruct_embedding", {"file_path": file_path}, error=str(e))
            return None

    def search_by_vector(self, query_embedding: np.ndarray, k: int = 5) -> list[dict[str, Any]]:
        """Search using a pre-computed embedding vector (skips Ollama call)."""
        if self.index is None or self.index.ntotal == 0 or not self._metadata:
            self._log_tool("search_by_vector", {"k": k}, outputs={"result_count": 0}, error="empty index")
            return []

        # Guard against dimension mismatch (e.g. embed model changed).
        if len(query_embedding) != self.index.d:
            self._log_tool(
                "search_by_vector",
                {"k": k},
                error=f"dimension mismatch: query {len(query_embedding)} vs index {self.index.d}",
            )
            return []

        # Normalize the query vector so L2 ≡ cosine (matches the normalized
        # stored vectors).
        query_vec = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
        faiss.normalize_L2(query_vec)
        # Search up to k (but never more than what's indexed).
        k_eff = min(k, self.index.ntotal)
        distances, indices = self.index.search(query_vec, k_eff)  # type: ignore

        results: list[dict[str, Any]] = []
        for faiss_id, distance in zip(indices[0], distances[0]):
            if faiss_id < 0:
                continue  # FAISS returns -1 for "no result"
            meta = self._metadata.get(int(faiss_id))
            if meta is None:
                continue  # tombstoned or unknown id
            file_path = Path(meta["file_path"])
            # Prefer cached preview so search never re-reads from disk; fall
            # back to disk read for legacy entries or when cache is disabled.
            content = meta.get("content_preview")
            if content is None:
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()
                except OSError:  # file gone / corrupt — return a placeholder
                    content = "[Error reading file]"
            results.append(
                {
                    "file_path": str(file_path),
                    "content": content,
                    "score": float(distance),  # L2 distance, smaller is more similar
                }
            )
        self._log_tool(
            "search_by_vector", {"k": k}, outputs={"result_count": len(results)}
        )
        return results

    def persist(self):
        """Save the index and metadata to disk.

        The metadata pickle stores a tuple ``(_metadata, _path_to_id,
        _next_id, EMBEDDING_SCHEMA_VERSION)`` so _load_index can detect the
        format and migrate accordingly.
        """
        if self.index is not None:
            faiss.write_index(self.index, str(self.index_file))
        with open(self.metadata_file, "wb") as f:
            pickle.dump(
                (
                    self._metadata,
                    self._path_to_id,
                    self._next_id,
                    EMBEDDING_SCHEMA_VERSION,
                ),
                f,
            )
        with open(self.timestamp_file, "w") as f:
            json.dump(self.timestamps, f)
        _logger.info(f"Index persisted to {self.index_path}")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    indexer = VaultIndexer(os.getenv("VAULT_PATH", "."))
    indexer.initialize()
    _logger.debug(f"Search results: {indexer.search('test query', k=3)}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        indexer.stop_watching()
        indexer.persist()
