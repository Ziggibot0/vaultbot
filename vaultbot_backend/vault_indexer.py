import os
import json
import pickle
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import faiss
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from ollama_client import OllamaClient

IGNORED_DIRS = {
    "vaultbot_venv",
    "vaultbot_index",
    "sessions",
    "partials",
    ".git",
    ".obsidian",
}

def _is_ignored_path(path: Path) -> bool:
    for part in path.parts:
        if part in IGNORED_DIRS:
            return True
    return False

class VaultChangeHandler(FileSystemEventHandler):
    def __init__(self, indexer):
        self.indexer = indexer
        super().__init__()

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.md'):
            if _is_ignored_path(Path(event.src_path)):
                return
            self.indexer._update_file(event.src_path)

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.md'):
            if _is_ignored_path(Path(event.src_path)):
                return
            self.indexer._add_file(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and event.src_path.endswith('.md'):
            if _is_ignored_path(Path(event.src_path)):
                return
            self.indexer._remove_file(event.src_path)

class VaultIndexer:
    def __init__(self, vault_path: str, index_path: str | None = None, session_logger=None):
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
        self.ollama_client = OllamaClient(
            embed_model=os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            session_logger=session_logger,
        )
        self.dimension = None  # Will be set after first embedding
        self.index = None
        self.metadata = []  # List of dicts: {'file_path', 'last_modified', 'content_hash'}
        self.timestamps = {}  # file_path -> last_modified timestamp
        # Bounded content-preview cache stored alongside each metadata entry.
        # Populated at index time (where the file is already read for hashing +
        # embedding) so search() / search_by_vector() can return a snippet
        # WITHOUT re-reading the file from disk on every query. The whole point:
        # FAISS finds K nearest in O(log N), then we used to do K synchronous
        # disk reads for the content — this cache removes those reads. Set to
        # 0 via VAULTBOT_INDEX_PREVIEW_CHARS to disable (full content returned,
        # reads from disk as before). Default 2000 covers every known consumer
        # (abstract_context uses 500, build_graph_context 2000, _snippet 200,
        # graph_ops.search 240, build_context 1500). A-MEM's write-back path
        # re-reads from disk itself, so it is unaffected by this cap.
        self.preview_chars = int(os.getenv("VAULTBOT_INDEX_PREVIEW_CHARS", "2000"))

        self.observer = None
        self._load_index()

    def _log_tool(self, method: str, inputs: Optional[Dict[str, Any]] = None, outputs: Any = None, error: Optional[str] = None):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(tool="vault_indexer", method=method, inputs=inputs, outputs=outputs, error=error)
        
    def _load_index(self):
        """Load existing index and metadata from disk, or initialize new."""
        if self.index_file.exists() and self.metadata_file.exists() and self.timestamp_file.exists():
            try:
                self.index = faiss.read_index(str(self.index_file))
                with open(self.metadata_file, 'rb') as f:
                    self.metadata = pickle.load(f)
                with open(self.timestamp_file, 'r') as f:
                    self.timestamps = json.load(f)
                # Normalize any stale relative paths to absolute so a CWD change
                # between sessions doesn't produce doubled paths (e.g.
                # vaultbot_backend\vaultbot_backend\identity\...). This is a
                # one-time migration — once all paths are absolute, the
                # normalization is a no-op.
                for meta in self.metadata:
                    fp = Path(meta['file_path'])
                    if not fp.is_absolute():
                        resolved = (self.vault_path / fp).resolve()
                        meta['file_path'] = str(resolved)
                # Determine dimension from the index
                self.dimension = self.index.d
                print(f"Loaded existing index with {self.index.ntotal} vectors from {self.index_file}")
            except Exception as e:
                print(f"Error loading existing index: {e}. Creating new index.")
                self._init_new_index()
        else:
            print("No existing index found. Creating new index.")
            self._init_new_index()
    
    def _init_new_index(self):
        """Initialize a new empty index."""
        # We'll determine the dimension when we add the first vector
        self.index = None
        self.metadata = []
        self.timestamps = {}
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Compute a hash of the file content to detect changes."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""
    
    def _get_embedding(self, text: str) -> np.ndarray:
        """Get embedding for text using Ollama.

        For long text (> 4000 chars), uses chunked embedding: splits the
        text into ~3K-char overlapping chunks, embeds each in parallel, and
        averages into one vector.  This ensures a long note's FULL content
        contributes to its FAISS vector — without it, nomic-embed-text's
        ~4000-char input limit means only the first ~600 words are embedded
        and the rest of the note is invisible to vector search.
        """
        if len(text) > 4000:
            return self._get_chunked_embedding(text)
        embedding = self.ollama_client.embeddings(text)
        return np.array(embedding, dtype=np.float32)

    _CHUNK_SIZE = 3000
    _CHUNK_OVERLAP = 300

    def _get_chunked_embedding(self, text: str) -> np.ndarray:
        """Embed long text by chunking + averaging.

        Splits on paragraph boundaries (\n\n) into ~3K-char chunks with
        ~300-char overlap, embeds all chunks in parallel via
        batch_embeddings, and averages the vectors.  Returns a single
        float32 array of the same dimensionality as a single embedding.
        Falls back to a single truncated embedding if anything fails.
        """
        chunks = self._split_into_chunks(text, self._CHUNK_SIZE, self._CHUNK_OVERLAP)
        if not chunks:
            embedding = self.ollama_client.embeddings(text[:4000])
            return np.array(embedding, dtype=np.float32)
        if len(chunks) == 1:
            embedding = self.ollama_client.embeddings(chunks[0][:4000])
            return np.array(embedding, dtype=np.float32)
        # Parallel embed all chunks.
        embs = self.ollama_client.batch_embeddings(chunks)
        valid = [np.array(e, dtype=np.float32) for e in embs if e is not None and len(e) > 0]
        if not valid:
            # All chunks failed — fall back to first 4K.
            embedding = self.ollama_client.embeddings(text[:4000])
            return np.array(embedding, dtype=np.float32)
        # Average into one vector.
        stacked = np.stack(valid)
        return np.mean(stacked, axis=0).astype(np.float32)

    @staticmethod
    def _split_into_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
        """Split text into overlapping chunks on paragraph boundaries.

        Tries to break on \n\n (paragraph) or \n (line) boundaries near the
        target chunk size, so chunks don't split mid-sentence.  Each chunk
        overlaps the previous by `overlap` chars so context isn't lost at
        the seam.
        """
        if len(text) <= chunk_size:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end >= len(text):
                chunks.append(text[start:])
                break
            # Try to break at a paragraph boundary near `end`.
            boundary = text.rfind('\n\n', end - overlap, end)
            if boundary == -1 or boundary <= start:
                boundary = text.rfind('\n', end - overlap, end)
            if boundary == -1 or boundary <= start:
                boundary = end  # hard cut — no nice boundary nearby
            chunks.append(text[start:boundary])
            start = boundary + 1  # +1 to skip the newline
        # Filter out empty / tiny chunks.
        return [c for c in chunks if len(c.strip()) > 50]
    
    def _add_file_to_index(self, file_path: Path):
        """Read a file, compute embedding, and add to index."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except FileNotFoundError:
            return  # file was deleted between the watcher event and open — normal race
        except Exception as e:
            print(f"Error reading file {file_path}: {e}")
            return
        
        # Compute hash to detect changes
        content_hash = self._get_file_hash(file_path)
        stat = file_path.stat()
        last_modified = stat.st_mtime
        
        # Check if we already have this file and if it's unchanged
        for i, meta in enumerate(self.metadata):
            if meta['file_path'] == str(file_path):
                if meta.get('content_hash') == content_hash:
                    # No change
                    return
                else:
                    # Update existing
                    self._remove_file_internal(file_path)
                    break
        
        # Get embedding
        try:
            embedding = self._get_embedding(content)
        except Exception as e:
            print(f"Error getting embedding for {file_path}: {e}")
            return
        
        self._add_embedding_to_index(
            file_path, embedding, last_modified, content_hash,
            content_preview=content)

    def _add_embedding_to_index(self, file_path: Path, embedding: np.ndarray,
                                 last_modified: float, content_hash: str,
                                 content_preview: str = ""):
        """Add a pre-computed embedding to the index (shared by single and batch paths).

        ``content_preview`` is an optional bounded slice of the file content,
        cached so search results can return a snippet without re-reading the
        file from disk. Callers that already have the content (both add paths
        # below read the file for hashing/embedding) pass it in for free.
        """
        embed_dim = len(embedding)
        if embed_dim == 0:
            print(f"Skipping {file_path}: received empty embedding from Ollama.")
            self._log_tool("add_file", {"file_path": str(file_path), "last_modified": last_modified, "content_hash": content_hash}, error="empty embedding")
            return

        if self.index is None:
            self.dimension = embed_dim
            self.index = faiss.IndexFlatL2(self.dimension)
            print(f"Initialized new index with dimension {self.dimension}")
        elif embed_dim != self.index.d:
            print(f"Skipping {file_path}: embedding dimension {embed_dim} does not match index dimension {self.index.d}.")
            self._log_tool("add_file", {"file_path": str(file_path), "last_modified": last_modified, "content_hash": content_hash}, error=f"dimension mismatch: {embed_dim} vs {self.index.d}")
            return

        index = self.index
        assert index is not None
        index.add(embedding.reshape(1, -1))  # type: ignore
        meta_entry: Dict[str, Any] = {
            'file_path': str(file_path if file_path.is_absolute() else file_path.resolve()),
            'last_modified': last_modified,
            'content_hash': content_hash
        }
        # Cache the bounded preview so future searches skip the disk read.
        if self.preview_chars > 0 and content_preview:
            meta_entry['content_preview'] = content_preview[:self.preview_chars]
        self.metadata.append(meta_entry)
        self.timestamps[str(file_path)] = last_modified
        print(f"Added {file_path} to index. Total vectors: {self.index.ntotal}")
        self._log_tool("add_file", {"file_path": str(file_path), "last_modified": last_modified, "content_hash": content_hash})

    def batch_add_files(self, file_paths: list[str],
                        return_embeddings: bool = False):
        """Add multiple files to the index using parallel embedding calls.

        Reads all files, sends their content to Ollama in parallel batches,
        then adds all embeddings to the FAISS index in one pass.

        Returns the number of files successfully indexed.  When
        ``return_embeddings`` is True, returns a tuple
        ``(indexed, {abs_path_str: embedding_list})`` so callers that need
        the embeddings right away (e.g. A-MEM neighbor search during a
        textbook weave) can reuse them instead of re-embedding each note.
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
            except Exception:
                continue
            # Skip unchanged files
            content_hash = self._get_file_hash(fp)
            skip = False
            for meta in self.metadata:
                if meta['file_path'] == str(fp) and meta.get('content_hash') == content_hash:
                    skip = True
                    break
            if skip:
                continue
            contents.append(content)
            valid_paths.append(fp)
            hashes.append(content_hash)
            timestamps.append(fp.stat().st_mtime)

        if not contents:
            return (0, {}) if return_embeddings else 0

        # Get embeddings.  Short texts go through the parallel batch path;
        # long texts (\u003e 4000 chars) use chunked embedding (split + average)
        # so the note's FULL content contributes to its vector, not just the
        # first 4K chars.  We dispatch each file to the right path and still
        # parallelize across files via a thread pool.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _embed_one(text: str):
            if len(text) > 4000:
                return self._get_chunked_embedding(text)
            return np.array(self.ollama_client.embeddings(text), dtype=np.float32)

        embeddings: list[Optional[np.ndarray]] = [None] * len(contents)
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_embed_one, t): i for i, t in enumerate(contents)}
            for future in as_completed(futures):
                i = futures[future]
                try:
                    embeddings[i] = future.result()
                except Exception:
                    embeddings[i] = None

        # Add to index
        indexed = 0
        emb_by_path: Dict[str, list[float]] = {}
        for fp, emb, last_mod, ch, cont in zip(valid_paths, embeddings, timestamps, hashes, contents):
            if emb is None:
                continue
            # Remove old entry if it exists
            self._remove_file_internal(fp)
            self._add_embedding_to_index(
                fp, np.array(emb, dtype=np.float32), last_mod, ch,
                content_preview=cont)
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
        """Remove a file from the index (internal method)."""
        # FAISS doesn't support removal directly, so we rebuild the index without the file.
        # For simplicity, we'll mark it as removed and rebuild when needed.
        # For now, we'll just remove from metadata and timestamps, and note that we need to rebuild.
        # A better approach would be to use IVF or allow rebuilding periodically.
        # Given the likely size of a personal vault, we can rebuild on deletion.
        # However, to avoid frequent rebuilds, we'll just mark and rebuild when the number of deletions is significant.
        # For simplicity in this implementation, we'll remove and rebuild the entire index when a file is deleted.
        # This is acceptable for a personal vault that isn't huge and doesn't have frequent deletions.
        # We'll implement a simple removal by rebuilding without the file.
        
        # Find the index of the file in metadata
        index_to_remove = None
        for i, meta in enumerate(self.metadata):
            if meta['file_path'] == str(file_path):
                index_to_remove = i
                break
        
        if index_to_remove is not None:
            # Remove from metadata and timestamps
            del self.metadata[index_to_remove]
            del self.timestamps[str(file_path)]
            # Rebuild the index without this vector
            try:
                self._rebuild_index()
                print(f"Removed {file_path} from index. Total vectors: {self.index.ntotal if self.index else 0}")
                self._log_tool("remove_file", {"file_path": str(file_path)})
            except Exception as e:
                self._log_tool("remove_file", {"file_path": str(file_path)}, error=str(e))
                raise
    
    def _rebuild_index(self):
        """Rebuild the entire index from remaining metadata.

        Files that no longer exist on disk (or can't be read) are PRUNED
        from metadata/timestamps here, not just skipped.  Without pruning,
        dead entries accumulate (e.g. bulk-deleted textbook sections) and
        every subsequent rebuild re-attempts to read them, producing O(n^2)
        error spam across the many per-removal rebuilds triggered by
        index_missing_or_changed().
        """
        if not self.metadata:
            self.index = None
            self.dimension = None
            return

        # Get embeddings for all remaining files; prune any that are gone.
        embeddings = []
        dead: list[int] = []  # indices to drop from metadata
        for i, meta in enumerate(self.metadata):
            file_path = Path(meta['file_path'])
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                embedding = self._get_embedding(content)
                embeddings.append(embedding)
            except Exception as e:
                print(f"Pruning missing file from index: {file_path} ({e})")
                dead.append(i)
                continue

        # Drop dead entries (reverse order so indices stay valid).
        for i in reversed(dead):
            fp = self.metadata[i]['file_path']
            del self.metadata[i]
            self.timestamps.pop(fp, None)
        if not embeddings:
            self.index = None
            self.dimension = None
            return
        
        # Create new index
        self.dimension = len(embeddings[0])
        self.index = faiss.IndexFlatL2(self.dimension)
        # Add all embeddings
        embeddings_array = np.array(embeddings).astype('float32')
        index = self.index
        assert index is not None
        index.add(embeddings_array)  # type: ignore
        print(f"Rebuilt index with {len(embeddings)} vectors")
    
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
        print(f"Started watching vault at {self.vault_path}")
    
    def stop_watching(self):
        """Stop watching the vault."""
        if self.observer is not None:
            self.observer.stop()
            self.observer.join()
            self.observer = None
    
    def _collect_md_files(self) -> List[Path]:
        """Scan the vault for markdown files, skipping ignored directories."""
        return [p for p in self.vault_path.rglob("*.md") if not _is_ignored_path(p)]

    def load(self):
        """Load a persisted index quickly without making Ollama calls."""
        print("Loading vault index...")
        # _load_index already ran in __init__; just report the state.
        total = self.index.ntotal if self.index is not None else 0
        md_files = self._collect_md_files()
        print(f"Loaded index with {total} vectors. {len(md_files)} markdown files in vault.")

    def index_missing_or_changed(self):
        """Re-index only new or changed markdown files. This is safe to run in the background."""
        print("Starting background vault indexing...")
        md_files = self._collect_md_files()
        changed_or_missing = []

        # Build a quick lookup from metadata by file path.
        meta_by_path = {meta["file_path"]: meta for meta in self.metadata}

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

            # Only re-embed if the content hash changed.
            current_hash = self._get_file_hash(file_path)
            if meta.get("content_hash") != current_hash:
                changed_or_missing.append(file_path)

        # Also detect deleted files and remove them from the index.
        current_paths = {str(p) for p in md_files}
        removed_paths = [Path(p) for p in meta_by_path if p not in current_paths]

        print(f"Background indexing: {len(changed_or_missing)} changed/missing, {len(removed_paths)} removed out of {len(md_files)} total files.")

        for file_path in removed_paths:
            self._remove_file_internal(file_path)

        for file_path in changed_or_missing:
            self._add_file_to_index(file_path)

        self.persist()
        print(f"Background indexing complete. Index now has {self.index.ntotal if self.index else 0} vectors.")

    def initialize(self):
        """Perform initial indexing of the vault and start watching.

        Kept for backwards compatibility; prefer load() + index_missing_or_changed().
        """
        self.load()
        self.index_missing_or_changed()

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Search the vault for the k most relevant notes to `query`.

        Returns a list of dicts: {'file_path', 'content', 'score'} sorted by
        relevance (lower L2 distance = more similar).
        """
        if self.index is None or self.index.ntotal == 0 or not self.metadata:
            self._log_tool("search", {"query": query, "k": k}, outputs={"result_count": 0}, error="empty index")
            return []

        try:
            query_embedding = self._get_embedding(query)
        except Exception as e:
            self._log_tool("search", {"query": query, "k": k}, error=str(e))
            raise

        return self.search_by_vector(query_embedding, k)

    def reconstruct_embedding(self, file_path: str) -> Optional[np.ndarray]:
        """Pull a note's content embedding straight out of the FAISS index.

        This is the key to LLM-free drift re-ranking: instead of re-embedding
        a candidate note's content to apply drift (which would cost an
        Ollama call per candidate), we reconstruct the stored vector
        directly from the IndexFlatL2 index. Zero Ollama calls.

        Returns the float32 embedding, or None if the file isn't indexed.
        """
        if self.index is None or not self.metadata:
            return None
        try:
            for i, meta in enumerate(self.metadata):
                if meta.get("file_path") == file_path:
                    return self.index.reconstruct(i).astype(np.float32)
        except Exception as e:
            self._log_tool("reconstruct_embedding", {"file_path": file_path},
                           error=str(e))
        return None

    def search_by_vector(self, query_embedding: np.ndarray,
                         k: int = 5) -> List[Dict[str, Any]]:
        """Search using a pre-computed embedding vector.

        This is the same as ``search()`` but skips the Ollama embedding call,
        letting callers reuse an embedding they already computed (e.g. A-MEM
        during a textbook weave reuses the just-indexed note's embedding as
        the neighbor-search query instead of re-embedding the note text).
        """
        if self.index is None or self.index.ntotal == 0 or not self.metadata:
            self._log_tool("search_by_vector", {"k": k}, outputs={"result_count": 0}, error="empty index")
            return []

        # Guard against dimension mismatch (e.g. embed model changed).
        if len(query_embedding) != self.index.d:
            self._log_tool("search_by_vector", {"k": k},
                           error=f"dimension mismatch: query {len(query_embedding)} vs index {self.index.d}")
            return []

        # FAISS expects a 2-D float32 array.
        query_vec = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
        # Search up to k (but never more than what's indexed).
        k_eff = min(k, self.index.ntotal)
        distances, indices = self.index.search(query_vec, k_eff)  # type: ignore

        results: List[Dict[str, Any]] = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
            meta = self.metadata[idx]
            file_path = Path(meta['file_path'])
            # Prefer the cached preview (populated at index time) so the
            # search never re-reads the file from disk. Fall back to a disk
            # read only for legacy entries (pre-preview) or when the cache
            # is disabled (preview_chars == 0). Full-content callers (A-MEM
            # write-back) re-read from disk themselves and ignore this field.
            content = meta.get('content_preview')
            if content is None:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                except Exception:
                    content = "[Error reading file]"
            results.append({
                'file_path': str(file_path),
                'content': content,
                'score': float(distance)  # L2 distance, smaller is more similar
            })
        self._log_tool("search_by_vector", {"k": k}, outputs={"result_count": len(results)})
        return results
    
    def persist(self):
        """Save the index and metadata to disk."""
        if self.index is not None:
            faiss.write_index(self.index, str(self.index_file))
        with open(self.metadata_file, 'wb') as f:
            pickle.dump(self.metadata, f)
        with open(self.timestamp_file, 'w') as f:
            json.dump(self.timestamps, f)
        print(f"Index persisted to {self.index_path}")

# Example usage (for testing)
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    vault_path = os.getenv("VAULT_PATH", "C:\\Users\\skell\\Desktop\\Vault2")
    indexer = VaultIndexer(vault_path)
    indexer.initialize()
    
    # Try a search
    results = indexer.search("test query", k=3)
    print(f"Search results: {results}")
    
    # Keep the script running to allow watching
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        indexer.stop_watching()
        indexer.persist()