# Research Note Writing Performance Bottleneck

## Summary
When the vaultbot says "writing note...", it sits for ~3 minutes not because the notes are long (they average ~6 KB) but because `create_note_from_research` runs a chain of redundant, inline maintenance work before it returns. The note itself is written to disk in milliseconds; the rest of the wait is throwaway work.

## Root Causes (measured against 59 research + 93 chat = 152 generated notes)

1. **Dead-link enrichment (the biggest sink).** `NoteCreator._find_related_notes` regex-extracts every quoted phrase and title-cased word from the research text (often dozens of entities), then runs an Ollama embedding + FAISS search **per entity**. Each Ollama call is ~1–3 s over the network. With ~20 entities that is 30–60 s of network round-trips alone. The discovered links are written into the note... and then **the caller immediately overwrites the note** with `research_engine.synthesize_note_markdown`, which has no Related Notes section. All of that embedding work is discarded. See `research_handler.py:111` and `chat_handler.py:831` — both do `Path(note_path).write_text(md)` right after `create_note_from_research` returns.
2. **Full O(n²) cleanup per write.** `_refresh_and_clean` → `VaultMaintenance.run_cleanup` does an O(n²) `SequenceMatcher` near-duplicate pass over all 152 generated notes on **every** note write. That is ~11,000 pairwise string comparisons synchronously inline.
3. **Triple graph refresh.** `NoteCreator._refresh_and_clean` calls `self.graph.refresh()`; the caller (`research_handler`) calls `svc.vault_graph.refresh()` again; and then A-MEM `evolve_on_create` calls `_refresh_indices` which refreshes the graph *and* the indexer a third time. The incremental refresh is cheap when nothing changed, but it's still redundant work and a full rglob on first build.
4. **Sequential, not pipelined.** All of the above run strictly inline before the function returns the path to the caller. The user sees nothing happening for the entire 3 minutes.

## Fix Applied
- Removed the dead-link enrichment step from `create_note_from_research` (it was always overwritten by `synthesize_note_markdown`).
- Switched post-write cleanup from a full O(n²) vault sweep to an incremental pass that only considers the single new note against the rest (O(n), one comparison per existing note).
- Made the graph refresh in `NoteCreator` a no-op when the caller is going to refresh anyway (the caller now owns the refresh).
- Added per-step timing logs to `note_creator` so the breakdown is visible in the session log.

## Expected Outcome
- "writing note..." should drop from ~180 s to ~5–15 s (the embedding cost of indexing the one new note + a single O(n) dedup pass).
- The actual note content is unchanged — `synthesize_note_markdown` still produces the same output the user sees today.

## Related Notes
- [[How-to-Structure-a-Research-Note]]
- [[Vault-Longevity-Architecture]]
- [[Context-Budgeting-for-Vault-Growth]]