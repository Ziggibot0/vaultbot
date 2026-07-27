# Chat: how do we make the system keep up with you so you 

## 2026-07-26 14:59 UTC

**User:** how do we make the system keep up with you so you don't need to do the bitch work of cleaning up this kind of thing in the future?

**Assistant:** Now I have the full picture. Here's the bottom line:

## The system already has 3 sync mechanisms — but they have a gap

| Mechanism | What it does | When it runs | Gap |
|---|---|---|---|
| **File watcher** (`watchdog` Observer) | Re-embeds .md files on modification | Real-time | Can miss events, no health check, no retry |
| **`index_missing_or_changed()`** | Hash-compares all .md files vs index, re-embeds changed ones | **Startup only** | Never runs again after startup |
| **`vault_graph.refresh()`** | Incremental mtime-gated wikilink graph rebuild | Before every chat | Works fine, but only triggers on chat |

The problem: if the file watcher misses an event (Windows filesystem timing, crash, race condition), there's **no safety net**. The startup audit catches up once, but if a note is rewritten mid-session and the watcher misses it, the stale embedding persists until the next restart.

That's exactly what happened with How-to-Evaluate-Source-Credibility — I rewrote it from garbage to exemplar, the watcher didn't catch it (or the re-embed failed silently), and the stale version persisted in the FAISS index.

## The fix: periodic index audit as a safety net

One background task, runs every N minutes, calls the existing `index_missing_or_changed()` + `vault_graph.refresh()`. Catches anything the file watcher misses. Zero new dependencies — just wires up existing functions on a timer.

Want me to build it? It's ~30 lines in `main.py`'s startup, all additive.

<details>
<summary>Thinking process</summary>

The vault doesn't have anything about automatic re-indexing or file watching. Let me look at the actual source code to understand how the embedding index works and what happens when notes are modified.Now let me look at the vault_indexer to understand how embeddings are indexed and when they get updated.Now I understand the full picture. Let me look at how the graph is refreshed too, since that's the other half of FUSED retrieval.Now let me check where the vault graph gets refreshed and where the indexer starts watching. Let me look at the startup sequence in main.py.Now I have the full picture. Let me look at where the vault_graph is instantiated and whether it gets refreshed:Now I have the full picture. Let me look at where vault_graph is instantiated:Now I have the full picture. Let me also check if VaultGraph has a refresh method:Now I have the full picture. Let me check where `vault_graph.refresh()` is called in the chat handler to see if the graph gets refreshed before retrieval:Now I have the full picture. Let me summarize what I found:

**The vault indexer (FAISS embeddings):**
- Has a `watchdog` Observer that watches for file changes in real-time (`start_watching()`)
- On file modification → `on_modified` → `_update_file` → `_add_file_to_index` → checks content hash, re-embeds if changed
- On startup → `load()` (loads persisted index) + `start_watching()` (starts file watcher) + background `index_missing_or_changed()` (catches up on anything that changed while backend was down)
- **This part works.** The file watcher should catch modifications and re-embed.

**The vault graph (wikilink graph):**
- Built once at startup (`_build_graph()` in `__init__`)
- Has `refresh()` and `refresh_if_changed()` — incremental, mtime-gated
- `handle_chat` calls `vault_graph.refresh()` before every retrieval
- **This part works too.** The graph is refreshed before each chat message.

So the system already has:
1. **File watcher on the indexer** — catches file modifications in real-time, re-embeds changed files
2. **Graph refresh before each chat** — catches wikilink changes incrementally

The problem Sean experienced was that the FUSED retrieval returned the **old** version of the How-to-Evaluate-Source-Credibility note (the garbage autonomous research version with 0 outgoing links) instead of the current exemplar version. This means the **embedding index had a stale version**.

Why would the file watcher miss the update? Possible reasons:
1. The note was rewritten by the backend itself (via `code_write` or `safe_write` or `note_creator`) — and the file watcher might not catch changes made by the same process. Actually, `watchdog` should catch any file system changes regardless of which process makes them.
2. The file watcher might have crashed or been stopped.
3. The note might have been rewritten while the backend was down, and `index_missing_or_changed()` didn't catch it because the content hash matched (unlikely — the content changed).
4. The file watcher might have a race condition or debounce issue.

Actually, looking more carefully at the vault context that was provided to me this session — it showed the OLD garbage version of the note. This means either:
- The embedding index still has the old embedding (file watcher didn't catch the update)
- OR the FUSED retrieval returned cached content from the metadata's `content_preview` field, which wasn't updated

Looking at `_add_file_to_index()`:
```python
def _add_file_to_index(self, file_path: Path):
    # ...
    content_hash = self._get_file_hash(file_path)
    # Check if we already have this file and if it's unchanged
    for i, meta in enumerate(self.metadata):
        if meta['file_path'] == str(file_path):
            if meta.get('content_hash') == content_hash:
                return  # No change
            else:
                self._update_file(event.src_path)  # Update existing
                break
    # Get embedding
    embedding = self._get_embedding(content)
    self._add_embedding_to_index(file_path, embedding, last_modified, content_hash, content_preview=content)
```

And `_add_embedding_to_index` stores `content_preview` in the metadata. So when the file is re-indexed, the content preview should be updated.

But wait — `_update_file` calls `_add_file_to_index`, which checks if the file already exists in metadata. If it does and the hash is different, it calls `_remove_file_internal` (which rebuilds the entire index!) and then re-adds. That's expensive but should work.

The issue might be that the file watcher's `on_modified` event doesn't fire for files modified by the backend itself. On Windows, `watchdog` should detect changes made by any process, but there can be issues with:
- File system caching
- The observer not running (maybe it crashed)
- The event being filtered out

Actually, I think the real issue is simpler. Looking at the code flow:

1. The note was originally created by the autonomous researcher as a garbage note
2. In a later session, I rewrote it as an exemplar (using `code_write` or `safe_write`)
3. The file watcher should have caught the modification and re-embedded
4. But the FUSED retrieval context I got this session showed the old version

This suggests the file watcher either:
- Wasn't running (backend was restarted and `start_watching()` wasn't called)
- Or the re-embedding failed (Ollama was down)
- Or there's a bug in the update path

Actually, the most likely issue is: **the file watcher's `on_modified` handler calls `_update_file` which calls `_add_file_to_index`**, but `_add_file_to_index` finds the existing metadata entry and if the hash is different, calls `_remove_file_internal` which **rebuilds the entire index**. This is O(n) and if it fails or times out, the old entry persists.

But the bigger question Sean is asking: **how do we make the system keep up automatically so I don't have to manually clean up stale indices?**

The answer is: the system ALREADY has the machinery (file watcher + incremental graph refresh), but there are gaps:

1. **No verification that the file watcher is actually working** — if it crashes or misses events, nobody knows
2. **No periodic sync/audit** — there's no periodic check that the index matches disk state
3. **The content_preview in metadata can be stale** — if the file watcher misses an event, the preview stays old
4. **No graph watcher** — the graph only refreshes when `handle_chat` calls `refresh()`, not on file events

The fix is straightforward: add a **periodic index audit** that runs on a timer (like the autonomous researcher) and:
1. Scans all .md files
2. Compares content hashes against index metadata
3. Re-embeds any that don't match
4. Refreshes the graph
5. Logs discrepancies

This is essentially `index_missing_or_changed()` but run on a timer instead of just at startup.

Let me think about what to build:

**Option 1: Periodic index audit** — a background task that runs every N minutes, calls `vault_indexer.index_missing_or_changed()` and `vault_graph.refresh()`. Simple, covers the gap.

**Option 2: Fix the file watcher** — make it more robust, add error handling, add a health check.

**Option 3: Both** — fix the watcher AND add a periodic audit as a safety net.

I think the right answer is: the system already has the right machinery, but it's missing a **periodic safety net**. The file watcher is real-time but can miss events. The startup `index_missing_or_changed()` catches up on restart but doesn't run again. Adding a periodic audit (every 5-10 minutes) would be the safety net that catches anything the file watcher missed.

Let me also check if there's a way to know if the file watcher is actually running:

Actually, let me just give Sean the bottom line and then build the fix. The fix is:

1. **Add a periodic index audit task** to `startup_event()` — runs `index_missing_or_changed()` + `vault_graph.refresh()` every N minutes as a safety net
2. **Add a health check** for the file watcher — log if the observer is not running
3. **That's it.** The file watcher already exists, the graph refresh already exists, the incremental indexer already exists. We just need a safety net to catch anything they miss.

Let me build this.

</details>