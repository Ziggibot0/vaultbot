import os
import re
from pathlib import Path
from typing import Any

from vault_graph import VaultGraph
from vault_indexer import VaultIndexer
from vault_maintenance import VaultMaintenance


class NoteCreator:
    """
    Creates vault notes and immediately maintains the vault so it stays clean.

    - Research notes go to Knowledge/Research/
    - Chat notes are merged by topic in Memory/Chat/
    - After every write, orphan and near-duplicate generated notes are cleaned.

    Resilience: embedding/vector-search failures (e.g. Ollama returning 500)
    must NEVER prevent a note from being written to disk. The note file is
    created FIRST, then indexing/linking is attempted with graceful fallback.
    """

    def __init__(self, vault_path: str, indexer: VaultIndexer, session_logger=None):
        self.vault_path = Path(vault_path).resolve()
        self.indexer = indexer
        self.session_logger = session_logger
        self.maintenance = VaultMaintenance(vault_path, session_logger=session_logger)
        self.graph = VaultGraph(vault_path, session_logger=session_logger)

    def _log_tool(self, method: str, inputs: dict[str, Any] | None = None,
                  outputs: Any = None, error: str | None = None):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(
            tool="note_creator", method=method, inputs=inputs,
            outputs=outputs, error=error
        )

    def _extract_entities(self, text: str) -> list[str]:
        """Pull out quoted phrases and title-cased proper nouns as link seeds."""
        patterns = [
            r'\"([^\"]+)\"',
            r'\'\'([^\']+)\'\'',
            r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'
        ]
        entities = set()
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                entities.add(match.strip())
        return list(entities)

    def _find_related_notes(self, entities: list[str], k: int = 5) -> list[dict[str, Any]]:
        """Vector-search the vault for each entity and deduplicate results.

        Gracefully returns an empty list if vector search fails (e.g. Ollama
        embedding endpoint returning 500). This must never block note creation.
        """
        related = []
        seen_files = set()
        for entity in entities:
            try:
                results = self.indexer.search(entity, k=k)
            except Exception as e:
                self._log_tool("find_related_notes_search_failed",
                               {"entity": entity}, error=str(e))
                continue
            for res in results:
                file_path = res['file_path']
                if file_path not in seen_files:
                    seen_files.add(file_path)
                    related.append(res)
        return related

    def _generate_links(self, entities: list[str], related_notes: list[dict[str, Any]]) -> list[str]:
        """Turn discovered entities and related notes into Obsidian wikilinks."""
        links = set()
        note_stems = {Path(meta['file_path']).stem: meta['file_path']
                      for meta in self.indexer.metadata}
        for entity in entities:
            for stem, file_path in note_stems.items():
                if stem.lower() == entity.lower():
                    links.add(f"[[{stem}]]")
                    break
        for note in related_notes:
            stem = Path(note['file_path']).stem
            links.add(f"[[{stem}]]")
        return sorted(links)

    def _refresh_and_clean(self, target_path: Path | None = None,
                           skip_graph_refresh: bool = False):
        """Rebuild graph awareness and run self-maintenance.

        Wrapped in try/except so cleanup failures never block note creation.

        When ``target_path`` is supplied, the cleanup is incremental: only
        the single new note is checked against the existing generated notes
        (O(n) dedup) instead of the full O(n^2) pairwise sweep that
        ``run_cleanup`` performs over every generated note. This is the
        hot path called after every research/chat note write, where the
        full sweep was the dominant cost of the "writing note..." stage.

        When ``skip_graph_refresh`` is True, the graph refresh is skipped
        entirely — used when the caller is about to refresh the graph
        itself (research_handler / chat_handler both refresh after this
        returns), so a second refresh here is wasted work.
        """
        try:
            if not skip_graph_refresh:
                self.graph.refresh()
            if target_path is not None:
                cleanup = self.maintenance.run_cleanup_for_new(
                    self.graph, target_path)
            else:
                cleanup = self.maintenance.run_cleanup(self.graph)
            self._log_tool("maintenance_cleanup", cleanup)
        except Exception as e:
            self._log_tool("maintenance_cleanup", error=str(e))

    def create_note_from_research(self, topic: str, research_content: str,
                                  summary: str | None = None) -> str:
        """Create a research note under Knowledge/Research/ and clean up afterwards.

        The note file is written to disk FIRST, before any indexing or
        graph operations. This ensures the knowledge is persisted even if
        the embedding service (Ollama) is down or returning errors.

        Performance note: this function intentionally does NOT do
        embedding-based link enrichment. The callers
        (``research_handler``, ``chat_handler``, ``autonomous_researcher``)
        overwrite the note file with ``synthesize_note_markdown`` immediately
        after this returns, which has no "Related Notes" section — so any
        enrichment here would be discarded. A-MEM (``evolve_on_create``)
        runs afterward and adds backlinks to *neighbors*, which is the
        durable form of link enrichment. Skipping the per-entity vector
        search here removes ~30-60s of Ollama round-trips from the
        "writing note..." stage.
        """
        import time
        t_start = time.monotonic()

        # --- Step 1: Write the note file immediately -------------------------
        note_path = self.maintenance.create_research_note(
            topic=topic,
            summary=summary or research_content[:500],
            research_content=research_content,
            links=[],
        )
        t_write = time.monotonic()

        self._log_tool("create_note_from_research", {
            "topic": topic,
            "file_path": str(note_path),
            "write_ms": round((t_write - t_start) * 1000, 1),
        })

        # --- Step 2: Incremental cleanup for the new note (non-blocking) ----
        # Skip the graph refresh — every caller refreshes the graph itself
        # right after this returns, so a refresh here is wasted work.
        self._refresh_and_clean(
            target_path=Path(note_path), skip_graph_refresh=True)
        t_clean = time.monotonic()

        # --- Step 3: Try to index the note (non-blocking) -------------------
        try:
            self.indexer._add_file_to_index(note_path)
            self._log_tool("index_note", {
                "file_path": str(note_path),
                "cleanup_ms": round((t_clean - t_write) * 1000, 1),
                "index_ms": round((time.monotonic() - t_clean) * 1000, 1),
            })
        except Exception as e:
            # Indexing failure (e.g. Ollama 500) must NOT prevent the note
            # from being returned. The file is already on disk.
            self._log_tool("index_note", {"file_path": str(note_path)},
                           error=str(e))

        return str(note_path)

    def create_note_from_chat(self, user_message: str, assistant_response: str,
                              thinking: str | None = None) -> str:
        """Append chat exchanges to a running chat note and clean up afterwards."""
        topic = f"Chat: {user_message[:50]}"
        entry = f"**User:** {user_message}\n\n**Assistant:** {assistant_response}"
        if thinking:
            entry += f"\n\n<details>\n<summary>Thinking process</summary>\n\n{thinking}\n\n</details>"

        note_path = self.maintenance.merge_chat_note(topic, entry)

        self._log_tool("create_note_from_chat", {
            "topic": topic,
            "file_path": str(note_path),
        })

        # Incremental cleanup for the new note; skip graph refresh — the
        # caller's handle_chat refreshes the graph, so a second refresh here
        # is wasted work.
        self._refresh_and_clean(
            target_path=Path(note_path), skip_graph_refresh=True)

        try:
            self.indexer._add_file_to_index(note_path)
            self._log_tool("index_note", {"file_path": str(note_path)})
        except Exception as e:
            self._log_tool("index_note", {"file_path": str(note_path)},
                           error=str(e))

        return str(note_path)

# Example usage (for testing)
if __name__ == "__main__":
    import os

    from dotenv import load_dotenv
    from vault_indexer import VaultIndexer

    load_dotenv()

    vault_path = os.getenv("VAULT_PATH", ".")
    indexer = VaultIndexer(vault_path)
    indexer.initialize()

    creator = NoteCreator(vault_path, indexer)

    test_topic = "Test Note from VaultBot"
    test_content = "This is a test note created by VaultBot to verify functionality."
    note_path = creator.create_note_from_research(test_topic, test_content)
    print(f"Created note at: {note_path}")
