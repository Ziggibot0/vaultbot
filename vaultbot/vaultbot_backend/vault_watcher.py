"""Watchdog file-system event handling for the vault index.

Extracted from ``vault_indexer.py`` to keep the indexer focused on embedding
storage and similarity search.  This module owns:

* ``IGNORED_DIRS`` — directories that must never be indexed or watched.
* ``_is_ignored_path(path)`` — fast path-part check against ``IGNORED_DIRS``.
* ``VaultChangeHandler`` — a ``FileSystemEventHandler`` with debounce that
  batches rapid file events (writes during a research dig) and forwards the
  final set to the indexer once.

The handler calls ``indexer._remove_file`` / ``indexer._add_file`` /
``indexer._update_file`` — the indexer remains the owner of the FAISS index.
"""

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler

_logger = logging.getLogger(__name__)

IGNORED_DIRS = {
    ".venv",
    "vaultbot_venv",  # legacy name; superseded by .venv (Obsidian-hidden)
    "vaultbot_index",
    "sessions",
    "partials",
    ".git",
    ".obsidian",
    ".trash",  # Obsidian's recycle bin — deleted files must not pollute search
    "trash",  # backend's own backup dir — deleted notes must not pollute search
}


def _is_ignored_path(path: Path) -> bool:
    for part in path.parts:
        if part in IGNORED_DIRS:
            return True
    return False


class VaultChangeHandler(FileSystemEventHandler):
    """File-system event handler with debounce.

    When VaultBot writes multiple notes in rapid succession (e.g. during a
    research dig), the watchdog fires on_modified/on_created for every file.
    Without debouncing, each event triggers an immediate embedding compute
    via Ollama, which is CPU-intensive and starves the event loop.  We batch
    events for DEBOUNCE_SECONDS, then process the final set once.
    """

    DEBOUNCE_SECONDS = 2.0

    def __init__(self, indexer):
        self.indexer = indexer
        self._pending: dict[
            str, str
        ] = {}  # path -> event type ('modified'/'created'/'deleted')
        self._moved_pairs: dict[str, str] = {}  # src_path -> dest_path (for on_moved)
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        super().__init__()

    def _schedule_flush(self):
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.DEBOUNCE_SECONDS, self._flush)
        self._timer.daemon = True
        self._timer.start()

    def _flush(self):
        with self._lock:
            pending = dict(self._pending)
            moved_pairs = dict(self._moved_pairs)
            self._pending.clear()
            self._moved_pairs.clear()
            self._timer = None

        # Process moves first — remove old path, add new path
        for src_path, dest_path in moved_pairs.items():
            try:
                self.indexer._remove_file(src_path)
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                _logger.warning("Error removing moved src %s: %s", src_path, e)
            try:
                self.indexer._add_file(dest_path)
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                _logger.warning("Error adding moved dest %s: %s", dest_path, e)

        for path, evt_type in pending.items():
            try:
                if evt_type == "deleted":
                    self.indexer._remove_file(path)
                elif evt_type == "created":
                    self.indexer._add_file(path)
                else:
                    self.indexer._update_file(path)
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                _logger.warning("Error processing %s: %s", path, e)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            if _is_ignored_path(Path(event.src_path)):
                return
            with self._lock:
                # 'created' takes priority over 'modified' — a file that was
                # just created and then modified should be added, not updated.
                if (
                    event.src_path not in self._pending
                    or self._pending[event.src_path] != "created"
                ):
                    self._pending[event.src_path] = "modified"
            self._schedule_flush()

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            if _is_ignored_path(Path(event.src_path)):
                return
            with self._lock:
                self._pending[event.src_path] = "created"
            self._schedule_flush()

    def on_deleted(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            if _is_ignored_path(Path(event.src_path)):
                return
            with self._lock:
                self._pending[event.src_path] = "deleted"
            self._schedule_flush()

    def on_moved(self, event):
        """Handle file moves/renames — remove old path, add new path.

        Without this handler, moved files leave stale entries in the index
        (the old path becomes a ghost). Watchdog fires on_moved for both
        renames and directory moves.
        """
        if not event.is_directory and event.src_path.endswith(".md"):
            dest_path = getattr(event, "dest_path", "")
            if not dest_path:
                return
            src_ignored = _is_ignored_path(Path(event.src_path))
            dest_ignored = _is_ignored_path(Path(dest_path))
            with self._lock:
                if not src_ignored and dest_ignored:
                    # File moved OUT of vault — just delete src
                    self._pending[event.src_path] = "deleted"
                elif not src_ignored and not dest_ignored:
                    # File moved within vault — remove old, add new
                    self._pending.pop(event.src_path, None)
                    self._pending.pop(dest_path, None)
                    self._moved_pairs[event.src_path] = dest_path
                elif src_ignored and not dest_ignored:
                    # File moved INTO vault — just add dest
                    self._pending[dest_path] = "created"
            self._schedule_flush()
