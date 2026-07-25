import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from ollama_client import OllamaClient
from vault_indexer import VaultIndexer
from vault_graph import VaultGraph
from vault_maintenance import VaultMaintenance


class NoteCreator:
    """
    Creates vault notes and immediately maintains the vault so it stays clean.

    - Research notes go to vaultbot/research/
    - Chat notes are merged by topic in vaultbot/chat/
    - After every write, orphan and near-duplicate generated notes are cleaned.

    Resilience: embedding/vector-search failures (e.g. Ollama returning 500)
    must NEVER prevent a note from being written to disk. The note file is
    created FIRST, then indexing/linking is attempted with graceful fallback.
    """

    def __init__(self, vault_path: str, indexer: VaultIndexer, session_logger=None):
        self.vault_path = Path(vault_path).resolve()
        self.indexer = indexer
        self.session_logger = session_logger
        self.ollama_client = OllamaClient(session_logger=session_logger)
        self.maintenance = VaultMaintenance(vault_path, session_logger=session_logger)
        self.graph = VaultGraph(vault_path, session_logger=session_logger)

    def _log_tool(self, method: str, inputs: Optional[Dict[str, Any]] = None,
                  outputs: Any = None, error: Optional[str] = None):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(
            tool="note_creator", method=method, inputs=inputs,
            outputs=outputs, error=error
        )

    def _extract_entities(self, text: str) -> List[str]:
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

    def _find_related_notes(self, entities: List[str], k: int = 5) -> List[Dict[str, Any]]:
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

    def _generate_links(self, entities: List[str], related_notes: List[Dict[str, Any]]) -> List[str]:
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

    def _refresh_and_clean(self):
        """Rebuild graph awareness and run self-maintenance.

        Wrapped in try/except so cleanup failures never block note creation.
        """
        try:
            self.graph.refresh()
            cleanup = self.maintenance.run_cleanup(self.graph)
            self._log_tool("maintenance_cleanup", cleanup)
        except Exception as e:
            self._log_tool("maintenance_cleanup", error=str(e))

    def create_note_from_research(self, topic: str, research_content: str,
                                  summary: Optional[str] = None) -> str:
        """Create a research note under vaultbot/research/ and clean up afterwards.

        The note file is written to disk FIRST, before any indexing or
        graph operations. This ensures the knowledge is persisted even if
        the embedding service (Ollama) is down or returning errors.
        """
        # --- Step 1: Write the note file immediately -------------------------
        # Use empty links initially; we'll try to enrich them but won't
        # block on vector search failures.
        note_path = self.maintenance.create_research_note(
            topic=topic,
            summary=summary or research_content[:500],
            research_content=research_content,
            links=[],  # placeholder; enriched below if possible
        )

        self._log_tool("create_note_from_research", {
            "topic": topic,
            "file_path": str(note_path),
        })

        # --- Step 2: Try to find related notes and enrich links -------------
        try:
            combined_text = f"{topic}\n{summary or ''}\n{research_content}"
            entities = self._extract_entities(combined_text)
            related_notes = self._find_related_notes(entities, k=5)
            links = self._generate_links(entities, related_notes)
            if links:
                # Re-write the note with enriched links.
                content_lines = [
                    f"# {topic}",
                    "",
                    "## Summary",
                    summary or research_content[:500],
                    "",
                    "## Research Notes",
                    research_content,
                    "",
                    "## Related Notes",
                ]
                if links:
                    content_lines.extend([f"- {link}" for link in links])
                else:
                    content_lines.append("*No related notes found.*")
                Path(note_path).write_text(
                    "\n".join(content_lines), encoding="utf-8")
        except Exception as e:
            self._log_tool("enrich_links_failed",
                           {"topic": topic}, error=str(e))

        # --- Step 3: Refresh graph and clean up (non-blocking) -------------
        self._refresh_and_clean()

        # --- Step 4: Try to index the note (non-blocking) -------------------
        try:
            self.indexer._add_file_to_index(note_path)
            self._log_tool("index_note", {"file_path": str(note_path)})
        except Exception as e:
            # Indexing failure (e.g. Ollama 500) must NOT prevent the note
            # from being returned. The file is already on disk.
            self._log_tool("index_note", {"file_path": str(note_path)},
                           error=str(e))

        return str(note_path)

    def create_note_from_chat(self, user_message: str, assistant_response: str,
                              thinking: Optional[str] = None) -> str:
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

        self._refresh_and_clean()

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

    vault_path = os.getenv("VAULT_PATH", "C:\\Users\\skell\\Desktop\\Vault2")
    indexer = VaultIndexer(vault_path)
    indexer.initialize()

    creator = NoteCreator(vault_path, indexer)

    test_topic = "Test Note from VaultBot"
    test_content = "This is a test note created by VaultBot to verify functionality."
    note_path = creator.create_note_from_research(test_topic, test_content)
    print(f"Created note at: {note_path}")